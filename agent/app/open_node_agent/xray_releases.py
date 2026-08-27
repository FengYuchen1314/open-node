"""Checksum-pinned Xray releases with a durable, reversible runtime selection."""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from open_node_agent.host_files import FileTransaction, guarded_path, read_private
from open_node_agent.runtime import RuntimeFailure, atomic_write, run_command

DEFAULT_VERSION = "v26.3.27"
VERSION_PATTERN = r"^v[0-9]{1,4}\.[0-9]{1,2}\.[0-9]{1,2}$"
SHA_PATTERN = r"^[0-9a-f]{64}$"
ARCHIVES = {"x86_64": "Xray-linux-64.zip", "aarch64": "Xray-linux-arm64-v8a.zip"}
PINNED_RELEASES = {
    "v26.3.27": {
        "x86_64": "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae",
        "aarch64": "4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c",
    },
    "v26.2.6": {
        "x86_64": "29ce535b56e207a406ffa1c2d4842dcc410be003eff8ec508bb732abc9f8e385",
        "aarch64": "b52d8263453fbd6f4747fd6a1ecf70cd43a664243615dc892ea4674c01b2b5ee",
    },
}
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
RELEASE_FILES = {"xray", "geoip.dat", "geosite.dat", "LICENSE", "README.md"}


class ReleaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(pattern=VERSION_PATTERN)
    sha256: str = Field(pattern=SHA_PATTERN)


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(default=DEFAULT_VERSION, pattern=VERSION_PATTERN)
    sha256: str | None = Field(default=None, pattern=SHA_PATTERN)
    start: bool | None = None

    def release(self) -> ReleaseSpec:
        architecture = platform.machine()
        if platform.system() != "Linux" or architecture not in ARCHIVES:
            raise RuntimeFailure("Xray package installation supports Linux amd64 and arm64")
        checksum = self.sha256 or PINNED_RELEASES.get(self.version, {}).get(architecture)
        if checksum is None:
            raise RuntimeFailure("This version requires an explicit official archive SHA-256")
        return ReleaseSpec(version=self.version, sha256=checksum)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = True
    release: ReleaseSpec | None = None


class ReleaseState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    current: Selection = Field(default_factory=Selection)
    previous: Selection | None = None


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_state(directory: Path) -> ReleaseState:
    path = guarded_path(directory, "xray-release.json")
    return ReleaseState.model_validate_json(read_private(path)) if path.exists() else ReleaseState()


def release_binary(directory: Path, release: ReleaseSpec) -> Path:
    root = directory / "xray-releases" / release.sha256
    manifest = json.loads(read_private(guarded_path(root, "manifest.json")))
    if set(manifest) != {"release", "files"} or manifest["release"] != release.model_dump():
        raise RuntimeFailure("Xray release identity does not match its manifest")
    files = manifest["files"]
    if not isinstance(files, dict) or "xray" not in files or set(files) - RELEASE_FILES:
        raise RuntimeFailure("Invalid Xray release file manifest")
    for name, checksum in files.items():
        path = guarded_path(root, name)
        info = path.stat()
        if (
            info.st_uid != os.geteuid()
            or info.st_mode & 0o022
            or info.st_size > MAX_FILE_BYTES
            or file_digest(path) != checksum
        ):
            raise RuntimeFailure("Xray release file integrity check failed")
    binary = guarded_path(root, "xray")
    if not os.access(binary, os.X_OK):
        raise RuntimeFailure("Selected Xray binary is not executable")
    return binary


def selected_binary(config) -> Path:
    state = read_state(config.state_dir)
    if state.current.release is None:
        return config.xray_binary
    return release_binary(config.state_dir, state.current.release)


async def download_release(release: ReleaseSpec, archive: Path) -> None:
    url = f"https://github.com/XTLS/Xray-core/releases/download/{release.version}/{ARCHIVES[platform.machine()]}"
    digest, size = hashlib.sha256(), 0
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for _ in range(4):
            parts = urlsplit(url)
            if (
                parts.scheme != "https"
                or parts.username
                or parts.password
                or parts.hostname
                not in {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }
                or parts.port not in {None, 443}
            ):
                raise RuntimeFailure("Xray download redirect left the official release hosts")
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                with archive.open("xb") as target:
                    async for block in response.aiter_bytes(64 * 1024):
                        size += len(block)
                        if size > MAX_ARCHIVE_BYTES:
                            raise RuntimeFailure("Xray archive exceeds the download limit")
                        digest.update(block)
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                if digest.hexdigest() != release.sha256:
                    raise RuntimeFailure("Xray archive SHA-256 mismatch; runtime was not changed")
                return
    raise RuntimeFailure("Too many Xray download redirects")


def extract_release(archive: Path, directory: Path, release: ReleaseSpec) -> None:
    files, total = {}, 0
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            mode = item.external_attr >> 16
            total += item.file_size
            if (
                item.filename not in RELEASE_FILES
                or item.filename in files
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                or item.file_size > MAX_FILE_BYTES
                or total > MAX_EXTRACTED_BYTES
            ):
                raise RuntimeFailure("Xray archive contains unsupported or oversized entries")
            target = directory / item.filename
            with source.open(item) as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            target.chmod(0o700 if item.filename == "xray" else 0o600)
            files[item.filename] = file_digest(target)
    if "xray" not in files:
        raise RuntimeFailure("Xray archive has no executable")
    atomic_write(
        directory / "manifest.json",
        json.dumps(
            {
                "release": release.model_dump(),
                "files": files,
            }
        ).encode(),
    )


class XrayReleases:
    def __init__(self, runtime, journal):
        self.runtime, self.journal = runtime, journal
        self.directory = runtime.config.state_dir
        self.manifest = self.directory / "xray-release.json"
        self.root = self.directory / "xray-releases"
        self.transaction = FileTransaction(
            self.directory / "xray-release-transaction.json",
            self.transaction_path,
            self.restore_intents,
        )
        self.transaction.recover()
        self.select()

    def transaction_path(self, path):
        if path == self.manifest:
            return guarded_path(self.directory, path)
        if path == self.runtime.config.xray_config:
            return guarded_path(path.parent, path)
        raise RuntimeFailure("File is not owned by the Xray release transaction")

    def restore_intents(self, intents):
        for service, desired in intents.items():
            self.journal.set_desired_running(desired, service)

    def select(self):
        state = read_state(self.directory)
        self.runtime.binary = selected_binary(self.runtime.config)
        self.runtime.enabled = state.current.enabled

    def require_managed(self):
        if self.runtime.config.runtime_mode != "managed":
            raise RuntimeFailure("Remote Xray packages require the Agent-owned managed runtime")
        if self.transaction.record.exists():
            raise RuntimeFailure("Unresolved Xray release transaction requires recovery")

    def status(self):
        state = read_state(self.directory)
        return {
            "success": True,
            "enabled": state.current.enabled,
            "installed": state.current.enabled and self.runtime.binary.is_file(),
            "release": state.current.release.model_dump() if state.current.release else None,
            "rollback_available": state.previous is not None,
        }

    async def prepare(self, release):
        guarded_path(self.directory, self.root / ".check")
        self.root.mkdir(mode=0o700, exist_ok=True)
        target = self.root / release.sha256
        guarded_path(self.root, target / ".check")
        if target.exists():
            return release_binary(self.directory, release)
        with tempfile.TemporaryDirectory(prefix=".download-", dir=self.root) as temporary:
            stage = Path(temporary)
            archive = stage / "archive.zip"
            try:
                await download_release(release, archive)
            except httpx.HTTPError as exc:
                raise RuntimeFailure("Official Xray package download failed") from exc
            extract_release(archive, stage, release)
            archive.unlink()
            code, output = await run_command(str(stage / "xray"), "version", timeout=5)
            if code or not output.startswith("Xray " + release.version.removeprefix("v") + " "):
                raise RuntimeFailure("Downloaded Xray binary does not report the requested version")
            os.rename(stage, target)
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return release_binary(self.directory, release)

    async def activate(self, selection: Selection, *, start: bool | None = None):
        self.require_managed()
        state = read_state(self.directory)
        binary = (
            release_binary(self.directory, selection.release)
            if selection.release
            else self.runtime.config.xray_binary
        )
        config_path = self.runtime.config.xray_config
        config = None
        if selection.enabled:
            config = (
                self.runtime.read()
                if config_path.exists()
                else {"inbounds": [], "outbounds": [{"protocol": "freedom", "tag": "direct"}]}
            )
            ok, output = await self.runtime.validate(config, binary=binary)
            if not ok:
                raise RuntimeFailure(
                    f"Candidate Xray rejected the existing configuration: {output}"
                )
        intents = {
            "xray": self.journal.desired_running(self.runtime.config.auto_start),
            "nginx": self.journal.desired_running(False, "nginx"),
        }
        desired = selection.enabled and (intents["xray"] if start is None else start)
        was_running = await self.runtime.running()
        unchanged = selection == state.current
        if (
            unchanged
            and self.manifest.exists()
            and config_path.exists()
            and was_running == desired
            and intents["xray"] == desired
        ):
            return {**self.status(), "running": was_running}
        previous = state.previous if unchanged else state.current
        updated = ReleaseState(current=selection, previous=previous)
        changes = {self.manifest: updated.model_dump_json().encode()}
        if config is not None and not config_path.exists():
            changes[config_path] = json.dumps(config).encode()
        self.transaction.begin(changes, intents=intents)
        try:
            await self.runtime.stop()
            self.select()
            self.journal.set_desired_running(desired)
            if desired:
                await self.runtime.start()
                for _ in range(20):
                    await asyncio.sleep(0.1)
                    if not await self.runtime.running():
                        raise RuntimeFailure("Candidate Xray exited during readiness verification")
            self.transaction.commit()
        except BaseException:

            async def restore():
                await self.runtime.stop()
                self.transaction.recover()
                self.select()
                if was_running:
                    await self.runtime.start()

            recovery = asyncio.create_task(restore())
            try:
                await asyncio.shield(recovery)
            except asyncio.CancelledError:
                await recovery
                raise
            except Exception as exc:
                raise RuntimeFailure(
                    "Xray activation failed and recovery needs operator review"
                ) from exc
            raise
        return {**self.status(), "running": await self.runtime.running()}

    async def install(self, body):
        self.require_managed()
        request = InstallRequest.model_validate(body)
        release = request.release()
        await self.prepare(release)
        return await self.activate(Selection(release=release), start=request.start)

    async def rollback(self):
        self.require_managed()
        state = read_state(self.directory)
        if state.previous is None:
            raise RuntimeFailure("No previous Xray selection is available")
        return await self.activate(state.previous)

    async def remove(self):
        return await self.activate(Selection(enabled=False), start=False)
