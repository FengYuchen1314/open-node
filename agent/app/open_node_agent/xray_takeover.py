"""Consolidate an explicitly authorized Xray file layout without changing its unit."""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, atomic_write, decode_config

ENDPOINT = "/api/child/external-xray/takeover"
PENDING = {"prepared", "stopping", "writing", "activating", "restoring"}
EMPTY = b"{}\n"


class XrayTakeover:
    def __init__(self, runtime, journal):
        self.runtime = runtime
        self.journal = journal
        self.config = runtime.config
        self.adapter = runtime.systemd
        self.path = self.config.state_dir / "xray-takeover.json"
        self.state = self.load()
        if self.adapter:
            self.adapter.pending_takeover = bool(self.state and self.state["phase"] in PENDING)

    def load(self):
        if not self.path.exists() and not self.path.is_symlink():
            return None
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o777 != 0o600
        ):
            raise RuntimeFailure("Xray takeover journal must be a private owned regular file")
        with self.path.open("rb") as source:
            raw = source.read(MAX_CONFIG_BYTES * 3 + 1)
        if len(raw) > MAX_CONFIG_BYTES * 3:
            raise RuntimeFailure("Xray takeover journal exceeds its size limit")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("service") != self.config.xray_service
            or value.get("target") != str(self.config.xray_config)
            or value.get("phase") not in PENDING | {"complete", "rolled_back"}
            or not isinstance(value.get("files"), dict)
            or not 1 <= len(value["files"]) <= 128
            or not isinstance(value.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["id"])
            or not isinstance(value.get("identity"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["identity"])
            or type(value.get("running")) is not bool
            or type(value.get("desired")) is not bool
            or not isinstance(value.get("merged"), str)
            or any(
                not isinstance(name, str) or not isinstance(raw, str)
                for name, raw in value["files"].items()
            )
        ):
            raise RuntimeFailure("Xray takeover journal does not match this runtime")
        return value

    def save(self, phase):
        self.state["phase"] = phase
        encoded = json.dumps(self.state).encode()
        if phase not in PENDING:
            directory = self.config.state_dir / "xray-takeover-backups"
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise RuntimeFailure("Xray takeover backup directory is not private")
            atomic_write(directory / (self.state["id"] + ".json"), encoded)
        atomic_write(self.path, encoded)
        self.adapter.pending_takeover = phase in PENDING

    def require_enabled(self):
        if not self.adapter or not self.config.allow_xray_takeover:
            raise RuntimeFailure(
                "The host owner must enable allow_xray_takeover for a bound systemd runtime"
            )

    def snapshot(self, binding):
        values = {str(path): self.adapter.read_private(path) for path in binding.layout.files}
        if sum(map(len, values.values())) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Combined Xray input files exceed 2 MiB")
        return values

    async def unchanged(self, binding, values):
        current = await self.adapter.inspect(allow_multifile=True)
        if (
            current.identity != binding.identity
            or current.layout != binding.layout
            or self.snapshot(current) != values
        ):
            raise RuntimeFailure("External Xray binding or source files changed during takeover")
        return current

    async def native(self, binding, args, *, dump=False):
        process = await asyncio.create_subprocess_exec(
            str(self.config.xray_binary),
            "run",
            *args,
            "-dump" if dump else "-test",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=binding.environment,
            cwd=binding.directory,
        )
        output = bytearray()
        try:
            async with asyncio.timeout(20):
                while block := await process.stdout.read(4096):
                    output.extend(block)
                    if len(output) > MAX_CONFIG_BYTES:
                        raise RuntimeFailure("Native Xray merge output exceeds 2 MiB")
                code = await process.wait()
            if code:
                raise RuntimeFailure(
                    "Native Xray merge or validation failed; source files were retained"
                )
            if dump:
                try:
                    return decode_config(output.decode())
                except (UnicodeError, ValueError) as exc:
                    raise RuntimeFailure(
                        "Xray did not return a supported merged JSON configuration"
                    ) from exc
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def prepare(self):
        self.require_enabled()
        if self.adapter.pending_takeover:
            raise RuntimeFailure(
                "Xray takeover recovery is pending; retry after recovery or review the host"
            )
        binding = await self.adapter.inspect(allow_multifile=True)
        values = self.snapshot(binding)
        merged = await self.native(binding, binding.layout.argv[2:], dump=True)
        await self.native(binding, binding.layout.argv[2:])
        # The core, including its filename-dependent outbound order, owns the merge rules.
        with tempfile.TemporaryDirectory(
            prefix="xray-merge-", dir=self.config.state_dir
        ) as directory:
            candidate = Path(directory) / "candidate.json"
            atomic_write(candidate, json.dumps(merged).encode())
            await self.native(binding, ["-config", str(candidate)])
            if await self.native(binding, ["-config", str(candidate)], dump=True) != merged:
                raise RuntimeFailure(
                    "Native merged config cannot round-trip without changing its meaning"
                )
        await self.unchanged(binding, values)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "binding": binding.identity,
                    "files": {
                        name: hashlib.sha256(raw).hexdigest() for name, raw in values.items()
                    },
                    "merged": merged,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return binding, values, merged, digest

    def report(self, binding, values, digest):
        return {
            "success": True,
            "detected": True,
            "config_path": str(self.config.xray_config),
            "conf_dir": str(binding.layout.directory) if binding.layout.directory else None,
            "source_files": list(values),
            "source_sha256": digest,
            "merged_files": len(values) - 1,
            "running": binding.running,
            "restart_required": binding.running,
            "last_phase": self.state["phase"] if self.state else None,
        }

    async def control(self, action):
        await self.adapter.control(action, allow_multifile=True)
        desired = action == "start"
        async with asyncio.timeout(15):
            consecutive = 0
            while consecutive < 3:
                running = (await self.adapter.inspect(allow_multifile=True)).running
                consecutive = consecutive + 1 if running == desired else 0
                await asyncio.sleep(0.2)

    async def recover(self):
        if not self.adapter or not self.adapter.pending_takeover:
            return
        self.require_enabled()
        try:
            await self.restore()
        except (OSError, ValueError, TimeoutError):
            self.runtime.binding_error = (
                "Xray takeover recovery is pending; inspect the private journal and host service"
            )

    async def restore(self):
        state = self.state
        binding = await self.adapter.inspect(allow_multifile=True)
        if binding.identity != state["identity"] or list(map(str, binding.layout.files)) != list(
            state["files"]
        ):
            raise RuntimeFailure("Xray takeover recovery found a changed service binding")
        old = {
            name: base64.b64decode(value, validate=True) for name, value in state["files"].items()
        }
        merged = base64.b64decode(state["merged"], validate=True)
        current = self.snapshot(binding)
        for name, raw in current.items():
            replacement = merged if name == state["target"] else EMPTY
            if raw not in (old[name], replacement):
                raise RuntimeFailure(
                    "Xray takeover recovery found an independently modified config file"
                )
        self.save("restoring")
        if binding.running:
            await self.control("stop")
        await self.unchanged(binding, current)
        for name, raw in old.items():
            if self.adapter.read_private(Path(name)) != current[name]:
                raise RuntimeFailure("Xray config changed during recovery")
            atomic_write(Path(name), raw)
        await self.native(binding, binding.layout.argv[2:])
        if state["running"]:
            await self.control("start")
        self.journal.set_desired_running(state["desired"])
        self.save("rolled_back")

    async def handle(self, body):
        if not isinstance(body, dict) or set(body) - {"preview", "confirm", "expected_sha256"}:
            raise RuntimeFailure("Invalid Xray takeover request")
        preview = body.get("preview", False)
        if type(preview) is not bool or (not preview and body.get("confirm") is not True):
            raise RuntimeFailure("Explicit Xray takeover confirmation is required")
        binding, values, merged, digest = await self.prepare()
        result = self.report(binding, values, digest)
        if preview:
            return {**result, "preview": True}
        if body.get("expected_sha256") is not None and body["expected_sha256"] != digest:
            raise RuntimeFailure("Xray takeover preview is stale; inspect the source files again")
        try:
            plain_target = isinstance(json.loads(values[str(self.config.xray_config)]), dict)
        except (UnicodeError, ValueError):
            plain_target = False
        if plain_target and all(
            name == str(self.config.xray_config) or raw.strip() == b"{}"
            for name, raw in values.items()
        ):
            return {**result, "restarted": False, "unchanged": True}
        encoded = json.dumps(merged, indent=2).encode() + b"\n"
        if len(encoded) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Consolidated Xray configuration exceeds 2 MiB")
        self.state = {
            "version": 1,
            "id": uuid4().hex,
            "service": self.config.xray_service,
            "target": str(self.config.xray_config),
            "identity": binding.identity,
            "files": {name: base64.b64encode(raw).decode() for name, raw in values.items()},
            "merged": base64.b64encode(encoded).decode(),
            "running": binding.running,
            "desired": self.journal.desired_running(self.config.auto_start),
        }
        self.save("prepared")
        try:
            await self.unchanged(binding, values)
            self.save("stopping")
            if binding.running:
                await self.control("stop")
            await self.unchanged(binding, values)
            self.save("writing")
            for name, raw in values.items():
                if self.adapter.read_private(Path(name)) != raw:
                    raise RuntimeFailure("Xray config changed during takeover")
                atomic_write(Path(name), encoded if name == str(self.config.xray_config) else EMPTY)
            expected = {
                name: encoded if name == str(self.config.xray_config) else EMPTY for name in values
            }
            await self.unchanged(binding, expected)
            if await self.native(binding, binding.layout.argv[2:], dump=True) != merged:
                raise RuntimeFailure("Consolidated Xray layout differs from the native merge")
            await self.native(binding, binding.layout.argv[2:])
            self.save("activating")
            if binding.running:
                await self.control("start")
            await self.unchanged(binding, expected)
            self.journal.set_desired_running(binding.running)
            self.save("complete")
        except BaseException:
            with contextlib.suppress(Exception):
                await asyncio.shield(self.restore())
            raise
        return {
            **result,
            "restarted": binding.running,
            "last_phase": "complete",
            "backup_id": self.state["id"],
        }
