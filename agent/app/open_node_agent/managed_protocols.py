"""Declarative, private Mihomo listener management.

The control plane describes only the five listener profiles supported by Open
Node.  It never supplies Mihomo YAML.  This module validates that description,
compiles the complete configuration, validates it with the pinned Mihomo
binary, and atomically activates it.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from ipaddress import ip_address
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from open_node_agent.runtime import (
    MAX_CONFIG_BYTES,
    RuntimeFailure,
    atomic_write,
    guarded_atomic_write,
    run_command,
)

ENDPOINT = "/api/child/managed-protocols"
STATE_FILE = "managed-protocols.json"
Profile = Literal[
    "vless_reality_vision",
    "vless_xhttp_reality_xmux",
    "anytls_shadowtls",
    "mieru",
    "socks5",
]
_VLESS_PROFILES = {"vless_reality_vision", "vless_xhttp_reality_xmux"}
_LOOPBACK_PROFILES = {*_VLESS_PROFILES, "anytls_shadowtls"}
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_HOST = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\."
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_BASE64URL_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_SHORT_ID = re.compile(r"(?:[0-9a-fA-F]{2}){1,8}")


def _safe_text(value: str, *, name: str, maximum: int = 255) -> str:
    if (
        not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _bindings_overlap(left: tuple[str, int], right: tuple[str, int]) -> bool:
    if left[1] != right[1]:
        return False
    try:
        left_ip, right_ip = ip_address(left[0]), ip_address(right[0])
    except ValueError:
        # Unknown Xray listen syntax cannot safely prove that the bind is disjoint.
        return True
    if left_ip == right_ip:
        return True
    if left_ip.is_unspecified or right_ip.is_unspecified:
        return (
            left_ip.version == right_ip.version
            or left_ip.version == 6
            or right_ip.version == 6
        )
    return False


class ClientConfig(BaseModel):
    """Non-secret client addressing metadata; never copied into Mihomo YAML."""

    model_config = ConfigDict(extra="forbid", strict=True)
    server: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("server")
    @classmethod
    def safe_server(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _safe_text(value, name="client server", maximum=253)
        try:
            return str(ip_address(value))
        except ValueError:
            if not _HOST.fullmatch(value):
                raise ValueError("client server must be a literal IP or DNS hostname") from None
            return value.lower()


class VlessServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sni: str = Field(min_length=1, max_length=253)
    reality_private_key: str = Field(min_length=43, max_length=43)
    reality_short_id: str = Field(min_length=2, max_length=16)

    @field_validator("sni")
    @classmethod
    def hostname(cls, value: str) -> str:
        value = _safe_text(value, name="SNI", maximum=253).lower()
        if not _HOST.fullmatch(value):
            raise ValueError("SNI must be a canonical DNS hostname")
        return value

    @field_validator("reality_private_key")
    @classmethod
    def private_key(cls, value: str) -> str:
        if not _BASE64URL_KEY.fullmatch(value):
            raise ValueError("Reality private key must be canonical base64url")
        return value

    @field_validator("reality_short_id")
    @classmethod
    def short_id(cls, value: str) -> str:
        if not _SHORT_ID.fullmatch(value):
            raise ValueError("Reality short ID must contain 2-16 hexadecimal characters")
        return value.lower()


class VlessXhttpServerConfig(VlessServerConfig):
    xhttp_path: str = Field(min_length=1, max_length=256)
    xhttp_host: str | None = Field(default=None, min_length=1, max_length=253)

    @field_validator("xhttp_path")
    @classmethod
    def path(cls, value: str) -> str:
        value = _safe_text(value, name="XHTTP path", maximum=256)
        if (
            not value.startswith("/")
            or "\\" in value
            or "?" in value
            or "#" in value
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError("XHTTP path must be an absolute path without traversal or query")
        return value

    @field_validator("xhttp_host")
    @classmethod
    def host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _safe_text(value, name="XHTTP host", maximum=253).lower()
        if not _HOST.fullmatch(value):
            raise ValueError("XHTTP host must be a canonical DNS hostname")
        return value


class AnyTLSServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sni: str = Field(min_length=1, max_length=253)

    @field_validator("sni")
    @classmethod
    def hostname(cls, value: str) -> str:
        return VlessServerConfig.hostname(value)


class MieruServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transport: Literal["TCP", "UDP"] = "TCP"


class SocksServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    udp: bool = True


_SERVER_CONFIG = {
    "vless_reality_vision": VlessServerConfig,
    "vless_xhttp_reality_xmux": VlessXhttpServerConfig,
    "anytls_shadowtls": AnyTLSServerConfig,
    "mieru": MieruServerConfig,
    "socks5": SocksServerConfig,
}


class ManagedUser(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=255)
    uuid: str | None = Field(default=None, min_length=36, max_length=36)
    password: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("name", "password")
    @classmethod
    def safe_value(cls, value: str | None, info) -> str | None:
        if value is None:
            return value
        return _safe_text(value, name=info.field_name, maximum=255)

    @field_validator("uuid")
    @classmethod
    def canonical_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = UUID(value)
        except ValueError:
            raise ValueError("VLESS UUID is invalid") from None
        if str(parsed) != value.lower():
            raise ValueError("VLESS UUID must be canonical")
        return str(parsed)


class ManagedListener(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tag: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=36, max_length=36)
    profile: Profile
    listen: str = Field(min_length=1, max_length=45)
    port: int = Field(ge=1024, le=65535)
    enabled: bool
    client_config: ClientConfig = Field(default_factory=ClientConfig)
    server_config: object
    users: list[ManagedUser] = Field(max_length=10_000)

    @field_validator("tag")
    @classmethod
    def safe_tag(cls, value: str) -> str:
        if not _TAG.fullmatch(value):
            raise ValueError("Listener tag contains unsupported characters")
        return value

    @field_validator("listen")
    @classmethod
    def literal_listen(cls, value: str) -> str:
        try:
            return str(ip_address(value))
        except ValueError:
            raise ValueError("Listener address must be a literal IP address") from None

    @field_validator("node_id")
    @classmethod
    def canonical_node_id(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError:
            raise ValueError("Managed node ID is invalid") from None
        if str(parsed) != value.lower():
            raise ValueError("Managed node ID must be canonical")
        return str(parsed)

    @model_validator(mode="after")
    def profile_fields(self):
        try:
            self.server_config = _SERVER_CONFIG[self.profile].model_validate(self.server_config)
        except ValidationError as exc:
            raise ValueError(f"Invalid {self.profile} server_config") from exc
        if self.profile in _LOOPBACK_PROFILES and self.listen != "127.0.0.1":
            raise ValueError("VLESS and AnyTLS managed listeners require 127.0.0.1")
        requires_uuid = self.profile in _VLESS_PROFILES
        for user in self.users:
            if requires_uuid and (user.uuid is None or user.password is not None):
                raise ValueError("VLESS users require uuid and prohibit password")
            if not requires_uuid and (user.password is None or user.uuid is not None):
                raise ValueError("AnyTLS, Mieru, and SOCKS5 users require password only")
        names = [user.name for user in self.users]
        identities = [user.uuid or user.password for user in self.users]
        if len(names) != len(set(names)) or len(identities) != len(set(identities)):
            raise ValueError("Listener users must have unique names and credentials")
        return self


class ManagedProtocolsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    listeners: list[ManagedListener] = Field(max_length=512)

    @model_validator(mode="after")
    def unique_bindings(self):
        tags = [listener.tag for listener in self.listeners]
        nodes = [listener.node_id for listener in self.listeners]
        bindings = [(listener.listen, listener.port) for listener in self.listeners]
        snis = [
            listener.server_config.sni
            for listener in self.listeners
            if isinstance(
                listener.server_config,
                (VlessServerConfig, VlessXhttpServerConfig, AnyTLSServerConfig),
            )
        ]
        for values, message in (
            (tags, "Managed listener tags must be unique"),
            (nodes, "Managed node IDs must be unique"),
            (bindings, "Managed listener address and port pairs must be unique"),
            (snis, "Managed listener SNI values must be unique"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(message)
        if any(
            _bindings_overlap(left, right)
            for index, left in enumerate(bindings)
            for right in bindings[index + 1 :]
        ):
            raise ValueError("Managed listener bindings overlap through a wildcard address")
        return self


def request_dump(request: ManagedProtocolsRequest) -> dict:
    return request.model_dump(mode="json")


def request_digest(request: ManagedProtocolsRequest) -> str:
    encoded = json.dumps(
        request_dump(request), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _listener(listener: ManagedListener) -> dict | None:
    if not listener.enabled or not listener.users:
        # Never turn an empty account list into an unauthenticated public proxy.
        return None
    common = {
        "name": listener.tag,
        "port": listener.port,
        "listen": listener.listen,
    }
    server = listener.server_config
    if listener.profile in _VLESS_PROFILES:
        value = {
            **common,
            "type": "vless",
            "users": [
                {
                    "username": user.name,
                    "uuid": user.uuid,
                    **(
                        {"flow": "xtls-rprx-vision"}
                        if listener.profile == "vless_reality_vision"
                        else {}
                    ),
                }
                for user in listener.users
            ],
            "reality-config": {
                "dest": f"{server.sni}:443",
                "private-key": server.reality_private_key,
                "short-id": [server.reality_short_id],
                "server-names": [server.sni],
            },
        }
        if listener.profile == "vless_xhttp_reality_xmux":
            value["xhttp-config"] = {
                "path": server.xhttp_path,
                "host": server.xhttp_host or server.sni,
            }
        return value
    if listener.profile == "anytls_shadowtls":
        return {
            **common,
            "type": "anytls",
            "users": {user.name: user.password for user in listener.users},
            "shadow-tls": {
                "enable": True,
                "version": 3,
                "users": [
                    {"name": user.name, "password": user.password}
                    for user in listener.users
                ],
                "handshake": {"dest": f"{server.sni}:443"},
            },
        }
    if listener.profile == "mieru":
        return {
            **common,
            "type": "mieru",
            "transport": server.transport,
            "users": {user.name: user.password for user in listener.users},
        }
    return {
        **common,
        "type": "socks",
        "udp": server.udp,
        "users": [
            {"username": user.name, "password": user.password} for user in listener.users
        ],
    }


def compile_config(request: ManagedProtocolsRequest) -> dict:
    listeners = [value for item in request.listeners if (value := _listener(item)) is not None]
    return {
        "mode": "rule",
        "log-level": "warning",
        "allow-lan": False,
        "listeners": listeners,
        "rules": ["MATCH,DIRECT"],
    }


def encode_config(config: dict) -> bytes:
    encoded = yaml.safe_dump(config, sort_keys=False, allow_unicode=False).encode()
    if len(encoded) > MAX_CONFIG_BYTES:
        raise RuntimeFailure("Mihomo configuration exceeds 2 MiB")
    return encoded


def _read_regular(path: Path, *, maximum: int = MAX_CONFIG_BYTES) -> bytes:
    if path.is_symlink():
        raise RuntimeFailure("Refusing managed protocol state symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeFailure("Managed protocol state must be one regular file")
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise RuntimeFailure("Managed protocol state exceeds its size limit")
    return raw


class MihomoRuntime:
    """A Mihomo child owned only by this non-root Agent installation."""

    def __init__(self, config):
        self.config = config
        self.binary = config.mihomo_binary
        self.path = config.mihomo_config
        self.process: asyncio.subprocess.Process | None = None
        self.log_task: asyncio.Task | None = None
        self.loaded_sha256: str | None = None
        self.log = logging.Logger("open-node-mihomo")
        config.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.log_handler = RotatingFileHandler(
            config.state_dir / "mihomo.log", maxBytes=5_000_000, backupCount=2
        )
        os.chmod(config.state_dir / "mihomo.log", 0o600)
        self.log.addHandler(self.log_handler)

    def read_raw(self) -> bytes:
        return _read_regular(self.path)

    async def validate(self, content: bytes | dict) -> tuple[bool, str]:
        encoded = encode_config(content) if isinstance(content, dict) else content
        if len(encoded) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Mihomo configuration exceeds 2 MiB")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".open-node-mihomo-test-", suffix=".yaml", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
            code, output = await run_command(
                str(self.binary), "-t", "-f", temporary, timeout=20
            )
            return code == 0, output[-8192:]
        finally:
            os.unlink(temporary)

    async def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if await self.running():
            return
        if os.name == "posix" and os.geteuid() == 0:
            raise RuntimeFailure("Managed Mihomo must run under the dedicated non-root Agent user")
        content = self.read_raw()
        ok, _output = await self.validate(content)
        if not ok:
            raise RuntimeFailure("Mihomo rejected its generated configuration")
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "-f",
                str(self.path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.log_task = asyncio.create_task(self._capture_logs(self.process))
            await asyncio.sleep(0.25)
            if not await self.running():
                raise RuntimeFailure(
                    "Mihomo exited during startup; inspect its private runtime log"
                )
        except BaseException:
            # Cancellation/timeout after spawn must never leave an unjournaled
            # candidate serving credentials behind the rolled-back config file.
            await asyncio.shield(self.stop())
            raise
        self.loaded_sha256 = hashlib.sha256(content).hexdigest()

    async def _capture_logs(self, process) -> None:
        while block := await process.stdout.read(4096):
            self.log.info("%s", block.decode(errors="replace").rstrip())

    async def stop(self) -> None:
        process = self.process
        log_task = self.log_task
        interrupted = None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await asyncio.shield(process.wait())
            except BaseException as exc:
                interrupted = exc
                if process.returncode is None:
                    process.kill()
                    await asyncio.shield(process.wait())
        if log_task:
            await asyncio.shield(log_task)
        self.process = None
        self.log_task = None
        self.loaded_sha256 = None
        if interrupted is not None:
            raise interrupted

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def write(
        self,
        content: bytes | dict,
        *,
        activate: bool,
        expected: bytes | None,
    ) -> dict:
        encoded = encode_config(content) if isinstance(content, dict) else content
        ok, _output = await self.validate(encoded)
        if not ok:
            raise RuntimeFailure("Mihomo rejected the generated configuration before apply")
        try:
            current = self.read_raw()
        except FileNotFoundError:
            current = None
        if current != expected:
            raise RuntimeFailure("Mihomo configuration changed during the guarded update")
        was_running = await self.running()
        transaction = (
            guarded_atomic_write(self.path, encoded, hashlib.sha256(current).hexdigest())
            if current is not None
            else None
        )
        if transaction is None:
            atomic_write(self.path, encoded)
        try:
            if activate:
                await (self.restart() if was_running else self.start())
            elif was_running:
                await self.stop()
        except BaseException:
            restored = transaction.rollback() if transaction is not None else False
            if transaction is None and self.path.exists() and self.read_raw() == encoded:
                self.path.unlink()
                restored = True
            if restored and was_running:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.start())
            raise
        if transaction is not None:
            transaction.commit()
        return {"running": await self.running()}

    async def version(self) -> str | None:
        try:
            code, output = await run_command(str(self.binary), "-v", timeout=5)
        except (OSError, ValueError, TimeoutError):
            return None
        return output.splitlines()[0][:120] if code == 0 and output else None

    async def close(self) -> None:
        await self.stop()
        self.log_handler.close()


class ManagedProtocols:
    def __init__(self, config, xray_runtime=None):
        self.config = config
        self.xray_runtime = xray_runtime
        self.runtime = MihomoRuntime(config)
        self.state_path = config.state_dir / STATE_FILE
        self.xray_reserved_inbounds = None

    def load(self) -> ManagedProtocolsRequest | None:
        try:
            raw = _read_regular(self.state_path, maximum=MAX_CONFIG_BYTES * 2)
        except FileNotFoundError:
            return None
        try:
            return ManagedProtocolsRequest.model_validate_json(raw)
        except ValidationError as exc:
            raise RuntimeFailure("Managed protocol state is invalid") from exc

    def listeners(self) -> list[ManagedListener]:
        state = self.load()
        return [] if state is None else state.listeners

    def tags(self) -> set[str]:
        return {listener.tag for listener in self.listeners()}

    def active(self, request: ManagedProtocolsRequest | None = None) -> bool:
        request = request or self.load()
        return bool(request and compile_config(request)["listeners"])

    @staticmethod
    def _assert_xray_compatible(
        listeners: list[ManagedListener], config: object, reserved: object = ()
    ) -> None:
        if not isinstance(config, dict):
            raise RuntimeFailure("Cannot safely inspect the Xray configuration")
        inbounds = config.get("inbounds", [])
        if (
            not isinstance(inbounds, list)
            or not isinstance(reserved, (list, tuple))
            or any(not isinstance(item, dict) for item in [*inbounds, *reserved])
        ):
            raise RuntimeFailure("Cannot safely inspect Xray inbound ownership")
        inbounds = [*inbounds, *reserved]
        if any(
            ("tag" in item and not isinstance(item["tag"], str))
            or ("listen" in item and not isinstance(item["listen"], str))
            or ("port" in item and type(item["port"]) is not int)
            for item in inbounds
        ):
            raise RuntimeFailure("Cannot safely inspect Xray inbound tag or port ownership")
        xray_tags = {item.get("tag") for item in inbounds}
        xray_bindings = [
            (item.get("listen", "0.0.0.0"), item["port"])
            for item in inbounds
            if "port" in item
        ]
        if xray_tags & {listener.tag for listener in listeners}:
            raise RuntimeFailure("Managed protocol tag conflicts with an Xray inbound")
        if any(
            _bindings_overlap((listener.listen, listener.port), xray_binding)
            for listener in listeners
            for xray_binding in xray_bindings
        ):
            raise RuntimeFailure("Managed protocol listener conflicts with an Xray inbound")

    def assert_xray_compatible(self, config: object) -> None:
        state = self.load()
        if state is not None:
            reserved = (
                self.xray_reserved_inbounds()
                if self.xray_reserved_inbounds is not None
                else ()
            )
            self._assert_xray_compatible(state.listeners, config, reserved)

    async def ensure_started(self) -> None:
        state = self.load()
        if state is not None and self.xray_runtime is not None:
            reserved = (
                self.xray_reserved_inbounds()
                if self.xray_reserved_inbounds is not None
                else ()
            )
            try:
                self._assert_xray_compatible(
                    state.listeners, self.xray_runtime.read(), reserved
                )
            except BaseException:
                if await self.runtime.running():
                    await self.runtime.stop()
                raise
        desired = compile_config(state) if state is not None else {
            "mode": "rule",
            "log-level": "warning",
            "allow-lan": False,
            "listeners": [],
            "rules": ["MATCH,DIRECT"],
        }
        encoded = encode_config(desired)
        try:
            current = self.runtime.read_raw()
        except FileNotFoundError:
            current = None
        active = bool(desired["listeners"])
        desired_sha256 = hashlib.sha256(encoded).hexdigest()
        if current != encoded:
            # A host power loss can occur after the guarded config swap but before
            # the declaration journal is durable.  The journal is authoritative;
            # converge to it before opening any listener after restart.
            await self.runtime.write(encoded, activate=active, expected=current)
        elif active:
            if not await self.runtime.running():
                await self.runtime.start()
            elif self.runtime.loaded_sha256 != desired_sha256:
                await self.runtime.restart()
        elif await self.runtime.running():
            await self.runtime.stop()

    async def apply(self, body: dict) -> dict:
        try:
            request = ManagedProtocolsRequest.model_validate(body)
        except ValidationError as exc:
            raise RuntimeFailure("Invalid managed protocol payload") from exc
        previous = self.load()
        if previous is not None and previous.revision == request.revision:
            if self.xray_runtime is not None:
                reserved = (
                    self.xray_reserved_inbounds()
                    if self.xray_reserved_inbounds is not None
                    else ()
                )
                # The persisted declaration, rather than a retry body, owns the
                # live Mihomo listeners for this revision.  Check that state so
                # an empty/mismatched idempotent retry cannot hide Xray drift.
                self._assert_xray_compatible(
                    previous.listeners, self.xray_runtime.read(), reserved
                )
            return {
                "revision": request.revision,
                "changed": False,
                "listener_count": len(previous.listeners),
            }
        if self.xray_runtime is not None:
            reserved = (
                self.xray_reserved_inbounds()
                if self.xray_reserved_inbounds is not None
                else ()
            )
            self._assert_xray_compatible(
                request.listeners, self.xray_runtime.read(), reserved
            )
        config = compile_config(request)
        encoded = encode_config(config)
        ok, _output = await self.runtime.validate(encoded)
        if not ok:
            raise RuntimeFailure("Mihomo rejected the generated configuration before apply")
        try:
            old_config = self.runtime.read_raw()
        except FileNotFoundError:
            old_config = None
        old_state = None if previous is None else json.dumps(
            request_dump(previous), sort_keys=True, indent=2
        ).encode() + b"\n"
        await self.runtime.write(
            encoded, activate=bool(config["listeners"]), expected=old_config
        )
        try:
            state_bytes = (
                json.dumps(request_dump(request), sort_keys=True, indent=2).encode() + b"\n"
            )
            atomic_write(self.state_path, state_bytes)
        except BaseException:
            if previous is not None:
                restore_config = old_config or encode_config(compile_config(previous))
            else:
                restore_config = encode_config(
                    {
                        "mode": "rule",
                        "log-level": "warning",
                        "allow-lan": False,
                        "listeners": [],
                        "rules": ["MATCH,DIRECT"],
                    }
                )
            try:
                await asyncio.shield(
                    self.runtime.write(
                        restore_config,
                        activate=self.active(previous),
                        expected=encoded,
                    )
                )
            except Exception as rollback_error:
                raise RuntimeFailure(
                    "Managed protocol state write failed and runtime rollback needs review"
                ) from rollback_error
            if old_state is not None:
                atomic_write(self.state_path, old_state)
            else:
                self.state_path.unlink(missing_ok=True)
            raise
        return {
            "revision": request.revision,
            "changed": True,
            "listener_count": len(request.listeners),
        }

    def access_candidate(self, entries) -> ManagedProtocolsRequest | None:
        current = self.load()
        if current is None:
            return None
        tags = {item.tag for item in current.listeners}
        selected = [entry for entry in entries if entry.tag in tags]
        if not selected:
            return None
        candidate = copy.deepcopy(current)
        by_tag = {listener.tag: listener for listener in candidate.listeners}
        for entry in selected:
            listener = by_tag[entry.tag]
            expected_protocol = {
                "vless_reality_vision": "vless",
                "vless_xhttp_reality_xmux": "vless",
                "anytls_shadowtls": "anytls",
                "mieru": "mieru",
                "socks5": "socks",
            }[listener.profile]
            protocols = {expected_protocol}
            if expected_protocol == "socks":
                protocols.add("socks5")
            if entry.protocol not in protocols:
                raise RuntimeFailure("Access target protocol changed")
            if entry.routing_user_additions or entry.limiter:
                raise RuntimeFailure(
                    "Mihomo access entries do not support Xray routing or limiter data"
                )
            client = entry.client
            name = client.get("username") or client.get("user") or client.get("email")
            if not isinstance(name, str) or not name:
                raise RuntimeFailure("Mihomo access credential requires a name")
            uuid = client.get("id") if listener.profile in _VLESS_PROFILES else None
            password = (
                None
                if listener.profile in _VLESS_PROFILES
                else client.get("password") or client.get("pass")
            )
            try:
                desired = ManagedUser(name=name, uuid=uuid, password=password)
            except ValidationError as exc:
                raise RuntimeFailure("Invalid Mihomo access credential") from exc
            named = [user for user in listener.users if user.name == desired.name]
            matches = [
                user for user in listener.users
                if (uuid is not None and user.uuid == uuid)
                or (password is not None and user.password == password)
            ]
            if named and named != matches:
                raise RuntimeFailure("Mihomo access credential identity changed")
            if len(matches) > 1:
                raise RuntimeFailure("Mihomo access credential is ambiguous")
            if matches and matches[0].name != desired.name:
                raise RuntimeFailure("Mihomo access credential identity changed")
            if entry.enabled and not matches:
                if any(user.name == desired.name for user in listener.users):
                    raise RuntimeFailure("Mihomo access user name is already assigned")
                listener.users.append(desired)
            elif not entry.enabled and matches:
                listener.users.remove(matches[0])
        try:
            # Pydantic does not re-run list length or cross-user validators for
            # in-place append/remove operations.  Never persist a candidate that
            # the declarative PUT endpoint could not load again after restart.
            return ManagedProtocolsRequest.model_validate(request_dump(candidate))
        except ValidationError as exc:
            raise RuntimeFailure(
                "Mihomo access changes violate managed listener constraints"
            ) from exc

    async def validate_request(self, request: ManagedProtocolsRequest) -> bytes:
        encoded = encode_config(compile_config(request))
        ok, _output = await self.runtime.validate(encoded)
        if not ok:
            raise RuntimeFailure("Mihomo rejected generated access changes")
        return encoded

    async def commit_request(
        self, request: ManagedProtocolsRequest, *, expected: ManagedProtocolsRequest
    ) -> None:
        current = self.load()
        if current is None or request_dump(current) != request_dump(expected):
            raise RuntimeFailure("Managed protocol state changed during the guarded update")
        old_config = self.runtime.read_raw()
        encoded = encode_config(compile_config(request))
        await self.runtime.write(encoded, activate=self.active(request), expected=old_config)
        try:
            atomic_write(
                self.state_path,
                json.dumps(request_dump(request), sort_keys=True, indent=2).encode() + b"\n",
            )
        except BaseException:
            try:
                await asyncio.shield(
                    self.runtime.write(
                        old_config, activate=self.active(expected), expected=encoded
                    )
                )
            except Exception as rollback_error:
                raise RuntimeFailure(
                    "Mihomo access state write failed and runtime rollback needs review"
                ) from rollback_error
            raise

    async def rollback_request(
        self, previous: ManagedProtocolsRequest, *, expected: ManagedProtocolsRequest
    ) -> None:
        current = self.load()
        if current is None or request_dump(current) != request_dump(expected):
            raise RuntimeFailure("Managed protocol state changed before rollback")
        encoded_current = self.runtime.read_raw()
        encoded_previous = encode_config(compile_config(previous))
        await self.runtime.write(
            encoded_previous, activate=self.active(previous), expected=encoded_current
        )
        try:
            atomic_write(
                self.state_path,
                json.dumps(request_dump(previous), sort_keys=True, indent=2).encode() + b"\n",
            )
        except BaseException:
            try:
                await asyncio.shield(
                    self.runtime.write(
                        encoded_current,
                        activate=self.active(expected),
                        expected=encoded_previous,
                    )
                )
            except Exception as rollback_error:
                raise RuntimeFailure(
                    "Mihomo rollback state write failed and runtime recovery needs review"
                ) from rollback_error
            raise

    async def scan(self) -> dict:
        state = self.load()
        listeners = []
        if state is not None:
            listeners = [
                {
                    "tag": listener.tag,
                    "profile": listener.profile,
                    "listen": listener.listen,
                    "port": listener.port,
                    "enabled": listener.enabled,
                    "active": listener.enabled and bool(listener.users),
                }
                for listener in state.listeners
            ]
        return {
            "mihomo_running": await self.runtime.running(),
            "mihomo_version": await self.runtime.version(),
            "managed_protocols_revision": None if state is None else state.revision,
            "managed_protocol_listeners": listeners,
        }

    async def close(self) -> None:
        await self.runtime.close()
