import asyncio
import contextlib
import json
import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from open_node_agent.config import AgentConfig

MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


class RuntimeFailure(ValueError):
    pass


async def run_command(*args: str, timeout: float = 20) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
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

    def read(self) -> dict:
        with self.config.xray_config.open("rb") as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Xray configuration exceeds 2 MiB")
        return decode_config(raw.decode())

    async def validate(self, content: str | dict) -> tuple[bool, str]:
        config = decode_config(content)
        self.config.xray_config.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, path = tempfile.mkstemp(
            prefix=".open-node-test-", suffix=".json", dir=self.config.xray_config.parent
        )
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(config, stream)
            code, output = await run_command(
                str(self.config.xray_binary), "run", "-test", "-config", path
            )
            return code == 0, output[-8192:]
        finally:
            os.unlink(path)

    async def running(self) -> bool:
        if self.config.runtime_mode == "systemd":
            code, _ = await run_command(
                "systemctl", "is-active", "--quiet", self.config.xray_service
            )
            return code == 0
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if await self.running():
            return
        ok, output = await self.validate(self.read())
        if not ok:
            raise RuntimeFailure(f"Xray validation failed: {output}")
        if self.config.runtime_mode == "systemd":
            code, output = await run_command("systemctl", "start", self.config.xray_service)
            if code:
                raise RuntimeFailure(f"Xray start failed: {output[-8192:]}")
        else:
            self.process = await asyncio.create_subprocess_exec(
                str(self.config.xray_binary),
                "run",
                "-config",
                str(self.config.xray_config),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.log_task = asyncio.create_task(self._capture_logs(self.process))
        await asyncio.sleep(0.25)
        if not await self.running():
            raise RuntimeFailure("Xray exited during startup; inspect the agent Xray log")

    async def _capture_logs(self, process) -> None:
        while block := await process.stdout.read(4096):
            self.log.info("%s", block.decode(errors="replace").rstrip())

    async def stop(self) -> None:
        if self.config.runtime_mode == "systemd":
            code, output = await run_command("systemctl", "stop", self.config.xray_service)
            if code:
                raise RuntimeFailure(f"Xray stop failed: {output[-8192:]}")
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
        await self.stop()
        await self.start()

    async def write(self, value: str | dict, *, restart: bool = False) -> dict:
        candidate = decode_config(value)
        ok, output = await self.validate(candidate)
        if not ok:
            raise RuntimeFailure(f"Xray validation failed: {output}")
        old = self.config.xray_config.read_bytes() if self.config.xray_config.exists() else None
        was_running = await self.running()
        atomic_write(self.config.xray_config, json.dumps(candidate, indent=2).encode() + b"\n")
        try:
            if restart and was_running:
                await self.restart()
        except BaseException:
            if old is None:
                self.config.xray_config.unlink(missing_ok=True)
            else:
                atomic_write(self.config.xray_config, old)
            if was_running:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.restart())
            raise
        return {"success": True, "restart_required": was_running and not restart}

    async def scan(self) -> dict:
        try:
            config = self.read()
        except (OSError, ValueError):
            config = {}
        try:
            _, output = await run_command(str(self.config.xray_binary), "version", timeout=5)
            version = output.splitlines()[0][:120] if output else None
        except (OSError, ValueError, TimeoutError):
            version = None
        return {
            "xray_running": await self.running(),
            "xray_version": version,
            "config_path": str(self.config.xray_config),
            "inbounds": config.get("inbounds", []),
        }

    async def stats(self) -> dict | None:
        if not self.config.stats_address or not await self.running():
            return None
        code, output = await run_command(
            str(self.config.xray_binary),
            "api",
            "statsquery",
            "--server=" + self.config.stats_address,
            "-reset=false",
            timeout=5,
        )
        if code:
            return None
        data = json.loads(output)
        stats = {"inbound": {}, "outbound": {}, "user": {}}
        for stat in data.get("stat", []):
            pieces = stat.get("name", "").split(">>>")
            if (
                len(pieces) != 4
                or pieces[0] not in stats
                or pieces[3] not in {"uplink", "downlink"}
            ):
                continue
            entry = stats[pieces[0]].setdefault(pieces[1], {"uplink": 0, "downlink": 0})
            entry[pieces[3]] = max(0, int(stat.get("value", 0)))
        return stats

    async def close(self) -> None:
        if self.config.runtime_mode == "managed":
            await self.stop()
        self.log_handler.close()
