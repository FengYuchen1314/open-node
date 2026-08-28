"""Control the free runtime's private limiter, without a network-facing listener."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, run_command

ENDPOINT = "/api/child/limiter"
MAX_RATE = 1 << 50


class LimitModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LimitUser(LimitModel):
    uid: int = Field(default=0, ge=0)
    email: str = Field(min_length=1, max_length=255)
    speed_limit: int = Field(default=0, ge=0, le=MAX_RATE)
    device_limit: int = Field(default=0, ge=0, le=1_000_000)
    conn_group: str = Field(default="", max_length=255)
    auto_speed_rules: list["SpeedRule"] = Field(default_factory=list, max_length=100)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if not self.auto_speed_rules:
            value.pop("auto_speed_rules", None)
        return value

    @field_validator("email", "conn_group")
    @classmethod
    def safe_text(cls, value):
        if (
            len(value.encode("utf-8")) > 255
            or value != value.strip()
            or any(char in value for char in "\x00\r\n")
        ):
            raise ValueError(
                "Limiter identifiers cannot contain surrounding whitespace or controls"
            )
        return value


class SpeedRule(LimitModel):
    type: Literal["sustained", "burst"]
    threshold_mbps: float = Field(gt=0, le=MAX_RATE / 125000, allow_inf_nan=False)
    sustained_seconds: int = Field(ge=1, le=86400)
    window_seconds: int = Field(default=0, ge=0, le=86400)
    burst_count: int = Field(default=0, ge=0, le=10000)
    limit_mbps: float = Field(gt=0, le=MAX_RATE / 125000, allow_inf_nan=False)
    limit_duration: int = Field(ge=1, le=86400)

    @model_validator(mode="after")
    def valid_rule(self):
        if min(self.threshold_mbps, self.limit_mbps) * 125000 < 1:
            raise ValueError("Automatic rates must be at least one byte per second")
        if self.type == "burst" and (
            self.window_seconds < self.sustained_seconds or self.burst_count < 1
        ):
            raise ValueError("Burst rules require a valid window and occurrence count")
        return self


class LimitPolicy(LimitModel):
    inbound_tag: str = Field(min_length=1, max_length=255)
    node_limit: int = Field(default=0, ge=0, le=MAX_RATE)
    users: list[LimitUser] = Field(default_factory=list, max_length=1000)
    auto_speed_rules: list[SpeedRule] = Field(default_factory=list, max_length=100)

    @field_validator("users", "auto_speed_rules", mode="before")
    @classmethod
    def empty_native_list(cls, value):
        return [] if value is None else value

    @field_validator("inbound_tag")
    @classmethod
    def safe_tag(cls, value):
        return LimitUser.safe_text(value)

    @model_validator(mode="after")
    def distinct_users(self):
        if len({user.email for user in self.users}) != len(self.users):
            raise ValueError("Limiter users must have distinct emails within an inbound")
        return self


class LimitDocument(LimitModel):
    version: Literal[1]
    inbounds: list[LimitPolicy] = Field(max_length=1000)

    @field_validator("inbounds", mode="before")
    @classmethod
    def empty_native_list(cls, value):
        return [] if value is None else value

    @model_validator(mode="after")
    def bounded_users(self):
        if len({policy.inbound_tag for policy in self.inbounds}) != len(self.inbounds):
            raise ValueError("Duplicate limiter inbound")
        if sum(len(policy.users) for policy in self.inbounds) > 20000:
            raise ValueError("Too many limiter users")
        return self


class LimitBinding(LimitModel):
    inbound_tag: str = Field(min_length=1, max_length=255)
    user: LimitUser


def validate_credentials(config, policy, *, existing=False):
    matches = [item for item in config.get("inbounds", []) if item.get("tag") == policy.inbound_tag]
    if existing and not matches:
        return
    if len(matches) != 1:
        raise RuntimeFailure("Limiter target must be an existing bound inbound")
    inbound = matches[0]
    protocol = inbound.get("protocol")
    if protocol not in {
        "vless",
        "vmess",
        "trojan",
        "shadowsocks",
        "hysteria",
        "anytls",
        "snell",
        "mieru",
    }:
        raise RuntimeFailure("Native limits require an authenticated proxy inbound")
    settings = inbound.get("settings", {})
    if not isinstance(settings, dict):
        raise RuntimeFailure("Limiter target has invalid authentication settings")
    container = "users" if protocol in {"anytls", "snell", "mieru"} else "clients"
    clients = settings.get(container, [])
    if not isinstance(clients, list) or any(not isinstance(item, dict) for item in clients):
        raise RuntimeFailure("Limiter target has an invalid credential list")
    if protocol == "shadowsocks" and not clients and settings.get("password"):
        clients = [*clients, settings]
    emails = {item.get("email") for item in clients if isinstance(item.get("email"), str)}
    if (policy.node_limit or policy.auto_speed_rules) and any(
        not item.get("email") for item in clients
    ):
        raise RuntimeFailure(
            "Inbound-wide limits require an email on every authentication credential"
        )
    if not existing and any(user.email not in emails for user in policy.users):
        raise RuntimeFailure("Limiter user email must match an authentication credential")


class NativeLimiter:
    def __init__(self, runtime):
        self.runtime = runtime
        self.directory = runtime.config.state_dir / "limits"
        self.path = self.directory / "policy.json"
        self.socket = self.directory / "control.sock"
        self._capability = None

    def environment(self):
        return {**os.environ, "OPEN_NODE_LIMITER_DIR": str(self.directory)}

    def document(self):
        if not self.path.exists() and not self.path.is_symlink():
            return {"version": 1, "inbounds": []}
        for path in (self.directory, self.path):
            info = path.lstat()
            directory = path == self.directory
            if (
                info.st_uid != os.geteuid()
                or info.st_mode & 0o777 != (0o700 if directory else 0o600)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)
            ):
                raise RuntimeFailure("Native limiter policy must remain private and service-owned")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            actual = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(actual.st_mode)
                or actual.st_nlink != 1
                or actual.st_uid != os.geteuid()
                or actual.st_mode & 0o777 != 0o600
                or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino)
            ):
                raise RuntimeFailure("Native limiter policy changed during inspection")
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Native limiter policy exceeds 2 MiB")
        return LimitDocument.model_validate_json(raw).model_dump()

    async def supported(self, binary: Path | None = None):
        binary = binary or self.runtime.binary
        try:
            info = binary.stat()
            identity = (str(binary), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            if self._capability and self._capability[0] == identity:
                return self._capability[1]
            code, output = await run_command(
                str(binary), "open-node-capabilities", timeout=3, env=self.environment()
            )
            data = json.loads(output) if code == 0 else {}
            value = (
                isinstance(data, dict) and type(data.get("limiter")) is int and data["limiter"] == 1
            )
            self._capability = (
                identity,
                value,
                value
                and type(data.get("user_auto_speed_rules")) is int
                and data["user_auto_speed_rules"] == 1,
            )
            return value
        except (OSError, ValueError, TimeoutError):
            return False

    async def require_binary(self, binary: Path | None = None):
        document = self.document()
        if document["inbounds"] and not await self.supported(binary):
            raise RuntimeFailure(
                "Stored limiter policies require the free Open Node limiter runtime; "
                "the selected binary cannot enforce them"
            )
        if any(
            user.get("auto_speed_rules")
            for policy in document["inbounds"]
            for user in policy["users"]
        ):
            await self.require_user_rules(binary)

    async def require_user_rules(self, binary: Path | None = None):
        if (
            not await self.supported(binary)
            or not self._capability
            or len(self._capability) < 3
            or not self._capability[2]
        ):
            raise RuntimeFailure(
                "Upgrade the free Open Node runtime for per-user automatic speed rules"
            )

    def require_config(self, config):
        for policy in LimitDocument.model_validate(self.document()).inbounds:
            validate_credentials(config, policy, existing=True)

    async def binding(self):
        binding = await self.runtime.binding()
        if not await self.runtime.running():
            raise RuntimeFailure("Start the bound Xray service before changing limiter policies")
        if binding:
            if binding.environment.get("OPEN_NODE_LIMITER_DIR") != str(self.directory):
                raise RuntimeFailure(
                    "The host-owned service must explicitly set OPEN_NODE_LIMITER_DIR "
                    "to this Agent's private limits directory"
                )
            pid = binding.pid
        else:
            pid = self.runtime.process.pid
        if not self.socket.exists():
            raise NotImplementedError(
                "Native limiter control is unavailable; use the free Open Node runtime"
            )
        if not await self.supported():
            raise NotImplementedError(
                "Install the free Open Node runtime with native limiter support"
            )
        for path, mode, check in (
            (self.directory, 0o700, stat.S_ISDIR),
            (self.socket, 0o600, stat.S_ISSOCK),
        ):
            info = path.lstat()
            if (
                info.st_uid != os.geteuid()
                or info.st_mode & 0o777 != mode
                or not check(info.st_mode)
            ):
                raise RuntimeFailure("Native limiter control is not private and service-owned")
        return pid

    async def request(self, method="GET", *, body=None, suffix="", expected=None, tag=None):
        pid = await self.binding()
        params = {}
        if expected is not None:
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise RuntimeFailure("Invalid limiter revision")
            params["expected_revision"] = expected
        if tag is not None:
            params["inbound_tag"] = tag
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(self.socket)),
            timeout=5,
            trust_env=False,
        ) as client:
            async with client.stream(
                method, "http://localhost/v1/limiter" + suffix, json=body, params=params
            ) as response:
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > MAX_CONFIG_BYTES * 4:
                        raise RuntimeFailure("Native limiter response exceeded its size limit")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise RuntimeFailure("Native limiter returned an invalid response")
                if response.status_code != 200:
                    raise RuntimeFailure(str(value.get("error") or "Native limiter request failed"))
        if (
            not isinstance(value, dict)
            or value.get("success") is not True
            or type(value.get("protocol_version")) is not int
            or value["protocol_version"] != 1
            or type(value.get("pid")) is not int
            or value["pid"] != pid
        ):
            raise RuntimeFailure("Native limiter response does not match the bound Xray process")
        if not isinstance(value.get("revision"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", value["revision"]
        ):
            raise RuntimeFailure("Native limiter returned an invalid revision")
        LimitDocument.model_validate({"version": 1, "inbounds": value.get("inbounds")})
        return {**value, "available": True}

    async def status(self):
        try:
            return await self.request()
        except (OSError, ValueError, TimeoutError, httpx.HTTPError, NotImplementedError) as exc:
            return {"success": True, "available": False, "message": str(exc)[:2048]}

    async def apply(self, body):
        if not isinstance(body, dict):
            raise RuntimeFailure("Limiter command must be an object")
        value = dict(body)
        expected = value.pop("expected_revision", None)
        action = value.pop("action", "sync")
        if action == "remove":
            if set(value) != {"inbound_tag"} or not isinstance(value["inbound_tag"], str):
                raise RuntimeFailure("Limiter removal requires one inbound tag")
            LimitPolicy(inbound_tag=value["inbound_tag"])
            return await self.request("DELETE", tag=value["inbound_tag"], expected=expected)
        if action != "sync":
            raise RuntimeFailure("Unsupported limiter action")
        policy = LimitPolicy.model_validate(value)
        if any(user.auto_speed_rules for user in policy.users):
            await self.require_user_rules()
        await self.runtime.binding()
        validate_credentials(self.runtime.read(), policy)
        return await self.request("POST", body=policy.model_dump(), expected=expected)

    async def provision(self, entries, config):
        if not isinstance(entries, list) or len(entries) > 1000:
            raise RuntimeFailure("Invalid batch limiter bindings")
        bindings = [LimitBinding.model_validate(item) for item in entries]
        if any(item.user.auto_speed_rules for item in bindings):
            await self.require_user_rules()
        keys = [(item.inbound_tag, item.user.email) for item in bindings]
        if len(set(keys)) != len(keys):
            raise RuntimeFailure("Duplicate batch limiter user")
        tags = {item.get("tag") for item in config.get("inbounds", [])}
        if any(item.inbound_tag not in tags for item in bindings):
            raise RuntimeFailure("Batch limiter target is missing from the candidate configuration")
        for item in bindings:
            validate_credentials(
                config, LimitPolicy(inbound_tag=item.inbound_tag, users=[item.user])
            )
        if not bindings or (
            not self.document()["inbounds"]
            and not any(
                item.user.speed_limit or item.user.device_limit or item.user.auto_speed_rules
                for item in bindings
            )
        ):
            return None
        ok, output = await self.runtime.validate(config)
        if not ok:
            raise RuntimeFailure(f"Xray validation failed before limiter changes: {output}")
        current = await self.request()
        policies = {
            item["inbound_tag"]: LimitPolicy.model_validate(item) for item in current["inbounds"]
        }
        changed = {}
        for item in bindings:
            policy = changed.setdefault(
                item.inbound_tag,
                policies.get(item.inbound_tag, LimitPolicy(inbound_tag=item.inbound_tag)),
            )
            users = {user.email: user for user in policy.users}
            users[item.user.email] = item.user
            policy.users = list(users.values())
        # Persist caps before credentials can become usable. A failed config write
        # retains the requested caps and reports failure, never an unlimited new user.
        return await self.request(
            "POST",
            suffix="/batch",
            body={"policies": [item.model_dump() for item in changed.values()], "removals": []},
            expected=current["revision"],
        )
