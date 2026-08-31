import asyncio
import contextlib
import ctypes
import hashlib
import json
import logging
import os
import stat
import tempfile
from ipaddress import ip_address
from logging.handlers import RotatingFileHandler
from pathlib import Path

from open_node_agent.config import AgentConfig
from open_node_agent.online import collect_online

MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


class RuntimeFailure(ValueError):
    pass


def loopback_endpoint(value: object) -> tuple[str, int] | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or value[closing + 1 : closing + 2] != ":":
            return None
        host, port = value[1:closing], value[closing + 2 :]
        if "]" in port:
            return None
    else:
        if value.count(":") != 1:
            return None
        host, port = value.split(":", 1)
    try:
        address = ip_address(host)
        number = int(port)
    except ValueError:
        return None
    if not address.is_loopback or not 1 <= number <= 65535:
        return None
    return str(address), number


def format_endpoint(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def routed_api_rule(rule: object, tag: str) -> bool:
    return (
        isinstance(rule, dict)
        and set(rule) <= {"type", "inboundTag", "outboundTag"}
        and rule.get("type") in {None, "field"}
        and rule.get("outboundTag") == tag
        and rule.get("inboundTag") == [tag]
    )


def xray_api_binding(config: dict) -> dict:
    """Return one safely representable direct or routed loopback API binding."""
    api = config.get("api")
    if api is None:
        return {
            "mode": "absent",
            "host": "127.0.0.1",
            "port": 46_736,
            "inbound_index": None,
        }
    if not isinstance(api, dict):
        raise RuntimeFailure("Xray API must be an object")
    services = api.get("services", [])
    if not isinstance(services, list) or any(not isinstance(item, str) for item in services):
        raise RuntimeFailure("Xray API services must be a list of strings")

    tag = api.get("tag", "api")
    if not isinstance(tag, str) or not tag:
        raise RuntimeFailure("Xray API tag must be a non-empty string")
    inbounds = config.get("inbounds", [])
    outbounds = config.get("outbounds", [])
    routing = config.get("routing", {})
    rules = routing.get("rules", []) if isinstance(routing, dict) else []
    if (
        not isinstance(inbounds, list)
        or not isinstance(outbounds, list)
        or not isinstance(rules, list)
    ):
        raise RuntimeFailure("Xray API inbounds, outbounds and routing rules must be arrays")
    outbound_tag_conflict = any(
        isinstance(outbound, dict) and outbound.get("tag") == tag
        for outbound in outbounds
    )
    if outbound_tag_conflict:
        raise RuntimeFailure("Xray API tag conflicts with an explicit outbound")

    api_related_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and (
            rule.get("outboundTag") == tag
            or (
                isinstance(rule.get("inboundTag"), list)
                and tag in rule["inboundTag"]
            )
        )
    ]

    if "listen" in api:
        endpoint = loopback_endpoint(api["listen"])
        if endpoint is None:
            raise RuntimeFailure("Existing Xray API must use a literal loopback listener")
        routed_inbound_exists = any(
            isinstance(inbound, dict) and inbound.get("tag") == tag
            for inbound in inbounds
        )
        if routed_inbound_exists or api_related_rules:
            raise RuntimeFailure(
                "Mixed direct and routed Xray API configuration is read-only in this form"
            )
        return {
            "mode": "direct",
            "host": endpoint[0],
            "port": endpoint[1],
            "inbound_index": None,
        }

    matches = [
        (index, inbound)
        for index, inbound in enumerate(inbounds)
        if isinstance(inbound, dict) and inbound.get("tag") == tag
    ]
    matching_rules = [rule for rule in rules if routed_api_rule(rule, tag)]
    if len(matches) != 1 or len(matching_rules) != 1 or len(api_related_rules) != 1:
        raise RuntimeFailure(
            "Traditional routed Xray API requires one dedicated inbound and routing rule"
        )
    index, inbound = matches[0]
    if inbound.get("protocol") not in {"tunnel", "dokodemo-door"}:
        raise RuntimeFailure(
            "Traditional routed Xray API must use the tunnel or dokodemo-door protocol"
        )
    try:
        address = ip_address(inbound.get("listen"))
    except (TypeError, ValueError) as exc:
        raise RuntimeFailure(
            "Traditional routed Xray API must use a literal loopback listener"
        ) from exc
    port = inbound.get("port")
    if not address.is_loopback or type(port) is not int or not 1 <= port <= 65_535:
        raise RuntimeFailure(
            "Traditional routed Xray API must use a literal loopback listener and port"
        )
    return {
        "mode": "routed",
        "host": str(address),
        "port": port,
        "inbound_index": index,
    }


async def run_command(
    *args: str, timeout: float = 20, env: dict | None = None, cwd: str | None = None
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=cwd,
    )
    output = bytearray()
    try:
        async with asyncio.timeout(timeout):
            while block := await process.stdout.read(4096):
                output.extend(block)
                if len(output) > MAX_OUTPUT_BYTES:
                    raise RuntimeFailure("Runtime command exceeded the output limit")
            code = await process.wait()
            return code, output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise RuntimeFailure("Refusing to replace a symlink")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    original = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=".open-node-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if original:
                os.fchmod(stream.fileno(), original.st_mode & 0o777)
                os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


_RENAME_EXCHANGE = 2


def _rename_exchange(directory_fd: int, left: str, right: str) -> None:
    """Atomically exchange two entries in one already-open Linux directory."""

    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise RuntimeFailure(
            "Guarded Xray writes require Linux renameat2 exchange support"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        directory_fd,
        os.fsencode(left),
        directory_fd,
        os.fsencode(right),
        _RENAME_EXCHANGE,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), right)


def _entry_fingerprint(directory_fd: int, name: str) -> tuple:
    """Identify an entry after an exchange without following links or blocking on FIFOs."""

    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    except OSError:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            None,
        )
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        digest = None
        if stat.S_ISREG(info.st_mode):
            checksum = hashlib.sha256()
            while block := stream.read(64 * 1024):
                checksum.update(block)
            digest = checksum.hexdigest()
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        digest,
    )


def _read_regular_entry(directory_fd: int, name: str) -> bytes:
    fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=directory_fd,
    )
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeFailure("Xray config must be one regular non-hard-linked file")
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise RuntimeFailure("Xray configuration exceeds 2 MiB")
    return raw


def _exchange_until_stable(
    directory_fd: int,
    target: str,
    swap: str,
    *,
    expected_target: tuple,
    swap_fingerprint: tuple,
) -> bool:
    """Put swap at target while preserving any writer that wins a concurrent race."""

    first_exchange = True
    while True:
        _rename_exchange(directory_fd, swap, target)
        displaced = _entry_fingerprint(directory_fd, swap)
        if displaced == expected_target:
            os.fsync(directory_fd)
            return first_exchange
        # A newer external entry reached the target before the exchange.  It is now
        # held by ``swap``; exchange once more so that the newest observed writer wins.
        expected_target, swap_fingerprint = swap_fingerprint, displaced
        first_exchange = False


class GuardedAtomicWrite:
    """Keep the displaced config until restart success or a compare-and-swap rollback."""

    def __init__(
        self,
        directory_fd: int,
        target: str,
        backup: str,
        previous: bytes,
        candidate_fingerprint: tuple,
        backup_fingerprint: tuple,
    ) -> None:
        self.directory_fd = directory_fd
        self.target = target
        self.backup = backup
        self.previous = previous
        self.candidate_fingerprint = candidate_fingerprint
        self.backup_fingerprint = backup_fingerprint
        self.closed = False

    def _finish(self) -> None:
        if self.closed:
            return
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.backup, dir_fd=self.directory_fd)
            os.fsync(self.directory_fd)
        finally:
            os.close(self.directory_fd)
            self.closed = True

    def commit(self) -> None:
        self._finish()

    def rollback(self) -> bool:
        if self.closed:
            raise RuntimeFailure("Guarded Xray write transaction is already closed")
        restored_previous = _exchange_until_stable(
            self.directory_fd,
            self.target,
            self.backup,
            expected_target=self.candidate_fingerprint,
            swap_fingerprint=self.backup_fingerprint,
        )
        self._finish()
        return restored_previous


def guarded_atomic_write(path: Path, content: bytes, expected_sha256: str) -> GuardedAtomicWrite:
    """Replace path only if the entry displaced at the atomic swap has the expected SHA."""

    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    temporary = None
    temporary_may_hold_external_config = False
    try:
        source_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        try:
            original = os.fstat(source_fd)
            if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
                raise RuntimeFailure("Xray config must be one regular non-hard-linked file")
        finally:
            os.close(source_fd)

        fd, temporary_path = tempfile.mkstemp(prefix=".open-node-guarded-", dir=path.parent)
        temporary = Path(temporary_path).name
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), original.st_mode & 0o777)
            os.fchown(stream.fileno(), original.st_uid, original.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        candidate_fingerprint = _entry_fingerprint(directory_fd, temporary)
        _rename_exchange(directory_fd, temporary, path.name)
        temporary_may_hold_external_config = True
        backup_fingerprint = None
        try:
            backup_fingerprint = _entry_fingerprint(directory_fd, temporary)
            previous = _read_regular_entry(directory_fd, temporary)
            if hashlib.sha256(previous).hexdigest() != expected_sha256:
                raise RuntimeFailure("Xray configuration changed during the guarded update")
            os.fsync(directory_fd)
        except BaseException as exc:
            try:
                backup_fingerprint = backup_fingerprint or _entry_fingerprint(
                    directory_fd, temporary
                )
                _exchange_until_stable(
                    directory_fd,
                    path.name,
                    temporary,
                    expected_target=candidate_fingerprint,
                    swap_fingerprint=backup_fingerprint,
                )
                temporary_may_hold_external_config = False
            except BaseException as restore_exc:
                raise RuntimeFailure(
                    "Guarded Xray swap recovery failed; the displaced config remains at "
                    + str(path.parent / temporary)
                ) from restore_exc
            raise exc
        transaction = GuardedAtomicWrite(
            directory_fd,
            path.name,
            temporary,
            previous,
            candidate_fingerprint,
            backup_fingerprint,
        )
        directory_fd = -1
        temporary = None
        temporary_may_hold_external_config = False
        return transaction
    finally:
        if temporary is not None and not temporary_may_hold_external_config:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def decode_config(raw: str | dict) -> dict:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise RuntimeFailure("Xray configuration must be a JSON object")
    if len(json.dumps(value).encode()) > MAX_CONFIG_BYTES:
        raise RuntimeFailure("Xray configuration exceeds 2 MiB")
    return value


class XrayRuntime:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.binary = config.xray_binary
        self.enabled = True
        self.process: asyncio.subprocess.Process | None = None
        self.log_task: asyncio.Task | None = None
        self.log = logging.Logger("open-node-xray")
        config.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.log_handler = RotatingFileHandler(
            config.state_dir / "xray.log", maxBytes=5_000_000, backupCount=2
        )
        os.chmod(config.state_dir / "xray.log", 0o600)
        self.log.addHandler(self.log_handler)
        self.lock = asyncio.Lock()
        self.binding_error: str | None = None
        self.systemd = None
        from open_node_agent.limiter import NativeLimiter

        self.limiter = NativeLimiter(self)
        if config.runtime_mode == "systemd":
            from open_node_agent.systemd_runtime import SystemdRuntime

            self.systemd = SystemdRuntime(config)

    async def binding(self):
        if self.systemd is None:
            return None
        try:
            value = await self.systemd.inspect()
        except (OSError, ValueError, TimeoutError) as exc:
            self.binding_error = str(exc)[:2048]
            raise
        self.binding_error = None
        return value

    def read_raw(self) -> bytes:
        if self.systemd:
            return self.systemd.read_config()
        fd = os.open(
            self.config.xray_config,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeFailure("Xray config must be one regular non-hard-linked file")
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Xray configuration exceeds 2 MiB")
        return raw

    def read(self) -> dict:
        raw = self.read_raw()
        return decode_config(raw.decode())

    async def validate(
        self, content: str | dict, *, binary: Path | None = None
    ) -> tuple[bool, str]:
        config = decode_config(content)
        await self.limiter.require_binary(binary)
        self.limiter.require_config(config)
        binding = await self.binding()
        if binding and binary is not None and binary != self.binary:
            raise RuntimeFailure("An external Xray executable is managed on its host")
        self.config.xray_config.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix=".open-node-test-", suffix=".json", dir=self.config.xray_config.parent
        )
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(config, stream)
            options = (
                {"env": binding.environment, "cwd": binding.directory}
                if binding
                else {"env": self.limiter.environment()}
            )
            code, output = await run_command(
                str(binary or self.binary), "run", "-test", "-config", path, **options
            )
            return code == 0, output[-8192:]
        finally:
            os.unlink(path)

    async def running(self) -> bool:
        if self.systemd:
            try:
                return (await self.binding()).running
            except (OSError, ValueError, TimeoutError):
                return False
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if not self.enabled:
            raise RuntimeFailure("Xray is removed; install a runtime before starting it")
        await self.binding()
        if await self.running():
            return
        ok, output = await self.validate(self.read())
        if not ok:
            raise RuntimeFailure(f"Xray validation failed: {output}")
        if self.systemd:
            await self.systemd.control("start")
        else:
            self.process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "run",
                "-config",
                str(self.config.xray_config),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self.limiter.environment(),
            )
            self.log_task = asyncio.create_task(self._capture_logs(self.process))
        await asyncio.sleep(0.25)
        if not await self.running():
            raise RuntimeFailure(
                self.binding_error
                or "Xray exited during startup; inspect its runtime log or unit journal"
            )

    async def _capture_logs(self, process) -> None:
        while block := await process.stdout.read(4096):
            self.log.info("%s", block.decode(errors="replace").rstrip())

    async def stop(self) -> None:
        if self.systemd:
            await self.systemd.control("stop")
        elif self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.log_task:
            await self.log_task
            self.log_task = None

    async def restart(self) -> None:
        if self.systemd:
            ok, output = await self.validate(self.read())
            if not ok:
                raise RuntimeFailure(f"Xray validation failed: {output}")
            await self.systemd.control("restart")
            await asyncio.sleep(0.25)
            if not await self.running():
                raise RuntimeFailure(
                    self.binding_error
                    or "External Xray failed to restart; inspect its unit journal"
                )
            return
        await self.stop()
        await self.start()

    async def write(
        self,
        value: str | dict,
        *,
        restart: bool = False,
        expected: dict | None = None,
        expected_sha256: str | None = None,
    ) -> dict:
        candidate = decode_config(value)
        encoded_candidate = json.dumps(candidate, indent=2).encode() + b"\n"
        ok, output = await self.validate(candidate)
        if not ok:
            raise RuntimeFailure(f"Xray validation failed: {output}")
        was_running = await self.running()
        await self.binding()
        try:
            old = self.read_raw()
        except FileNotFoundError:
            old = None
        if expected is not None and (
            old is None or decode_config(old.decode()) != expected
        ):
            raise RuntimeFailure("Xray configuration changed during the guarded update")
        if expected_sha256 is not None and (
            old is None or hashlib.sha256(old).hexdigest() != expected_sha256
        ):
            raise RuntimeFailure("Xray configuration changed since it was read")
        transaction = (
            guarded_atomic_write(
                self.config.xray_config,
                encoded_candidate,
                hashlib.sha256(old).hexdigest(),
            )
            if old is not None
            else None
        )
        if transaction is None:
            atomic_write(self.config.xray_config, encoded_candidate)
        try:
            if restart and was_running:
                await self.restart()
        except BaseException as exc:
            restored = transaction.rollback() if transaction is not None else False
            if transaction is None:
                try:
                    current = self.read_raw()
                except FileNotFoundError:
                    current = None
                if current == encoded_candidate:
                    self.config.xray_config.unlink(missing_ok=True)
                    restored = True
            if was_running and restored:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.restart())
            if not restored and not isinstance(exc, asyncio.CancelledError):
                raise RuntimeFailure(
                    "Xray restart failed after another writer changed the configuration; "
                    "the newer configuration was preserved"
                ) from exc
            raise
        if transaction is not None:
            transaction.commit()
        return {"success": True, "restart_required": was_running and not restart}

    async def scan(self) -> dict:
        running = await self.running()
        if self.binding_error:
            return {
                "xray_running": False,
                "xray_version": None,
                "xray_capabilities": {},
                "config_path": str(self.config.xray_config),
                "inbounds": [],
                "message": self.binding_error,
            }
        try:
            config = self.read()
        except (OSError, ValueError):
            config = {}
        try:
            _, output = await run_command(str(self.binary), "version", timeout=5)
            version = output.splitlines()[0][:120] if output else None
        except (OSError, ValueError, TimeoutError):
            version = None
        capabilities = {}
        if await self.limiter.mieru_udp_target_supported():
            capabilities["mieru_udp_target"] = 1
        return {
            "xray_running": running,
            "xray_version": version,
            "xray_capabilities": capabilities,
            "config_path": str(self.config.xray_config),
            "inbounds": config.get("inbounds", []),
            "message": None,
        }

    def stats_endpoint(self) -> str | None:
        if self.config.stats_address is not None:
            return self.config.stats_address or None
        try:
            config = self.read()
            api = config.get("api")
            if not isinstance(api, dict) or "StatsService" not in api.get("services", []):
                return None
            binding = xray_api_binding(config)
            if binding["mode"] in {"direct", "routed"}:
                return format_endpoint(binding["host"], binding["port"])
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        return None

    async def online_users(self) -> dict:
        return await collect_online(self, run_command)

    async def stats(self) -> dict | None:
        endpoint = self.stats_endpoint()
        if not endpoint or not await self.running():
            return None
        code, output = await run_command(
            str(self.binary),
            "api",
            "statsquery",
            "--server=" + endpoint,
            "-reset=false",
            timeout=5,
        )
        if code:
            return None
        data = json.loads(output)
        stats = {"inbound": {}, "outbound": {}, "user": {}}
        for item in data.get("stat", []):
            pieces = item.get("name", "").split(">>>")
            if (
                len(pieces) != 4
                or pieces[0] not in stats
                or pieces[3] not in {"uplink", "downlink"}
            ):
                continue
            entry = stats[pieces[0]].setdefault(pieces[1], {"uplink": 0, "downlink": 0})
            entry[pieces[3]] = max(0, int(item.get("value", 0)))
        return stats

    async def close(self) -> None:
        if self.config.runtime_mode == "managed":
            await self.stop()
        self.log_handler.close()
