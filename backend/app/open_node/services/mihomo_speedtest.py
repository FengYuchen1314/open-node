"""Pinned Mihomo acquisition and real proxy speed measurement."""

import asyncio
import gzip
import hashlib
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import yaml

from open_node.domain.speedtests import MihomoStatusRead, SpeedTestError

DEFAULT_DOWNLOAD_URL = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
DEFAULT_LATENCY_URL = "https://www.gstatic.com/generate_204"
LATENCY_ONLY_URL = "https://cp.cloudflare.com/generate_204"
EGRESS_URL = "https://api.ipify.org"
DOWNLOAD_SECONDS = 8.0
MAX_DOWNLOAD_BYTES = 2_147_483_648
MAX_RUNTIME_BYTES = 128 * 1024 * 1024
USER_AGENT = "OpenNode-SpeedTest/1"


@dataclass(frozen=True)
class Measurement:
    down_mbps: float | None
    latency_ms: float | None
    egress_ip: str | None
    bytes: int


def _platform_key() -> str:
    machine = platform.machine().lower()
    arch = "amd64" if machine in {"x86_64", "amd64"} else "arm64" if machine in {
        "aarch64", "arm64"
    } else machine
    return f"{platform.system().lower()}-{arch}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MihomoSpeedTest:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self._install_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._downloading = False
        self.manifest = json.loads(
            files("open_node.resources").joinpath("mihomo-release.json").read_text("utf-8")
        )

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def binary(self) -> Path:
        return self.state_dir / self.version / "mihomo"

    @property
    def marker(self) -> Path:
        return self.state_dir / self.version / "installed.json"

    @property
    def singbox_version(self) -> str:
        return str(self.manifest["sing_box"]["version"])

    @property
    def singbox_binary(self) -> Path:
        return self.state_dir / self.singbox_version / "sing-box"

    @property
    def singbox_marker(self) -> Path:
        return self.state_dir / self.singbox_version / "installed.json"

    def _asset(self) -> dict[str, Any] | None:
        asset = self.manifest.get("assets", {}).get(_platform_key())
        return asset if isinstance(asset, dict) else None

    def _singbox_asset(self) -> dict[str, Any] | None:
        asset = self.manifest.get("sing_box", {}).get("assets", {}).get(_platform_key())
        return asset if isinstance(asset, dict) else None

    def _ready(self) -> bool:
        return self._runtime_ready(self.binary, self.marker, self.version, self._asset())

    @staticmethod
    def _runtime_ready(
        binary: Path, marker_path: Path, version: str, asset: dict[str, Any] | None
    ) -> bool:
        try:
            marker = json.loads(marker_path.read_text("utf-8"))
            info = binary.stat()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return bool(
            asset
            and stat.S_ISREG(info.st_mode)
            and info.st_size > 1024 * 1024
            and marker == {
                "version": version,
                "asset_sha256": asset.get("sha256"),
                "binary_sha256": _sha256(binary),
            }
        )

    def _singbox_ready(self) -> bool:
        return self._runtime_ready(
            self.singbox_binary,
            self.singbox_marker,
            self.singbox_version,
            self._singbox_asset(),
        )

    def status(self) -> MihomoStatusRead:
        supported = self._asset() is not None
        ready = supported and self._ready()
        message = (
            "Mihomo 已就绪。" if ready else
            "首次本机测速时会下载并校验固定版本的 Mihomo。" if supported else
            "当前系统架构不支持内置 Mihomo 测速。"
        )
        return MihomoStatusRead(
            supported=supported, ready=ready, version=self.version,
            platform=_platform_key(), downloading=self._downloading, message=message,
        )

    async def ensure(self) -> Path:
        if self._ready():
            return self.binary
        if self._asset() is None:
            raise SpeedTestError(503, "speedtest_runtime_unavailable")
        async with self._install_lock:
            if self._ready():
                return self.binary
            self._downloading = True
            try:
                await asyncio.to_thread(self._install)
            except SpeedTestError:
                raise
            except Exception:
                raise SpeedTestError(503, "speedtest_runtime_unavailable") from None
            finally:
                self._downloading = False
        return self.binary

    async def ensure_singbox(self) -> Path:
        if self._singbox_ready():
            return self.singbox_binary
        if self._singbox_asset() is None:
            raise SpeedTestError(503, "speedtest_runtime_unavailable")
        async with self._install_lock:
            if self._singbox_ready():
                return self.singbox_binary
            self._downloading = True
            try:
                await asyncio.to_thread(self._install_singbox)
            except SpeedTestError:
                raise
            except Exception:
                raise SpeedTestError(503, "speedtest_runtime_unavailable") from None
            finally:
                self._downloading = False
        return self.singbox_binary

    def _install(self) -> None:
        asset = self._asset()
        if asset is None:
            raise SpeedTestError(503, "speedtest_runtime_unavailable")
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.binary.parent
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        request = urllib.request.Request(
            str(asset["url"]), headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        temporary = destination / f"download-{os.getpid()}.gz"
        unpacked = destination / f"mihomo-{os.getpid()}.tmp"
        try:
            digest = hashlib.sha256()
            total = 0
            with (
                urllib.request.urlopen(request, timeout=45) as response,
                temporary.open("xb") as out,
            ):
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > int(asset["compressed_bytes"]):
                        raise OSError("oversized runtime asset")
                    digest.update(chunk)
                    out.write(chunk)
            if total != int(asset["compressed_bytes"]) or digest.hexdigest() != asset["sha256"]:
                raise OSError("runtime checksum mismatch")
            written = 0
            with gzip.open(temporary, "rb") as source, unpacked.open("xb") as out:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > MAX_RUNTIME_BYTES:
                        raise OSError("oversized unpacked runtime")
                    out.write(chunk)
            if written < 1024 * 1024:
                raise OSError("invalid unpacked runtime")
            os.chmod(unpacked, 0o700)
            binary_digest = _sha256(unpacked)
            os.replace(unpacked, self.binary)
            marker_tmp = destination / f"installed-{os.getpid()}.tmp"
            marker_tmp.write_text(json.dumps({
                "version": self.version,
                "asset_sha256": asset["sha256"],
                "binary_sha256": binary_digest,
            }, separators=(",", ":")), "utf-8")
            os.chmod(marker_tmp, 0o600)
            os.replace(marker_tmp, self.marker)
        finally:
            for path in (temporary, unpacked, destination / f"installed-{os.getpid()}.tmp"):
                with suppress(OSError):
                    path.unlink()

    def _install_singbox(self) -> None:
        asset = self._singbox_asset()
        if asset is None:
            raise SpeedTestError(503, "speedtest_runtime_unavailable")
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.singbox_binary.parent
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        request = urllib.request.Request(
            str(asset["url"]), headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        archive = destination / f"download-{os.getpid()}.tar.gz"
        unpacked = destination / f"sing-box-{os.getpid()}.tmp"
        marker_tmp = destination / f"installed-{os.getpid()}.tmp"
        try:
            digest = hashlib.sha256()
            total = 0
            with (
                urllib.request.urlopen(request, timeout=45) as response,
                archive.open("xb") as out,
            ):
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > int(asset["compressed_bytes"]):
                        raise OSError("oversized runtime asset")
                    digest.update(chunk)
                    out.write(chunk)
            if total != int(asset["compressed_bytes"]) or digest.hexdigest() != asset["sha256"]:
                raise OSError("runtime checksum mismatch")
            with tarfile.open(archive, "r:gz") as source:
                members = source.getmembers()
                candidates = [
                    member for member in members
                    if member.isfile() and Path(member.name).name == "sing-box"
                ]
                if len(members) > 256 or len(candidates) != 1:
                    raise OSError("invalid runtime archive")
                member = candidates[0]
                if not 1024 * 1024 < member.size <= MAX_RUNTIME_BYTES:
                    raise OSError("invalid runtime size")
                stream = source.extractfile(member)
                if stream is None:
                    raise OSError("runtime is missing")
                written = 0
                with stream, unpacked.open("xb") as out:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        written += len(chunk)
                        if written > member.size:
                            raise OSError("oversized unpacked runtime")
                        out.write(chunk)
                if written != member.size:
                    raise OSError("truncated unpacked runtime")
            os.chmod(unpacked, 0o700)
            binary_digest = _sha256(unpacked)
            os.replace(unpacked, self.singbox_binary)
            marker_tmp.write_text(json.dumps({
                "version": self.singbox_version,
                "asset_sha256": asset["sha256"],
                "binary_sha256": binary_digest,
            }, separators=(",", ":")), "utf-8")
            os.chmod(marker_tmp, 0o600)
            os.replace(marker_tmp, self.singbox_marker)
        finally:
            for path in (archive, unpacked, marker_tmp):
                with suppress(OSError):
                    path.unlink()

    async def run(
        self,
        proxy: dict[str, Any],
        *,
        requested_bytes: int,
        url: str | None,
        threads: int,
        buf_size: int,
        latency_only: bool,
    ) -> Measurement:
        singbox = self._is_snell_v6(proxy)
        binary = await (self.ensure_singbox() if singbox else self.ensure())
        async with self._run_lock:
            return await self._run_core(
                binary, proxy, requested_bytes=requested_bytes, url=url,
                threads=threads, buf_size=buf_size, latency_only=latency_only,
                singbox=singbox,
            )

    @staticmethod
    def _is_snell_v6(proxy: dict[str, Any]) -> bool:
        if str(proxy.get("type") or "").lower() != "snell":
            return False
        try:
            return int(proxy.get("version") or 0) >= 6
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _singbox_outbound(proxy: dict[str, Any]) -> dict[str, Any]:
        try:
            port = int(proxy.get("port"))
            version = int(proxy.get("version") or 6)
        except (TypeError, ValueError):
            raise SpeedTestError(409, "speedtest_credential_unavailable") from None
        server, psk = proxy.get("server"), proxy.get("psk")
        if (
            not isinstance(server, str) or not server or len(server) > 255
            or not isinstance(psk, str) or not psk or len(psk) > 1024
            or not 1 <= port <= 65535 or version < 6
        ):
            raise SpeedTestError(409, "speedtest_credential_unavailable")
        mode = proxy.get("mode") or "default"
        if not isinstance(mode, str) or mode not in {"default", "plain", "http"}:
            raise SpeedTestError(409, "speedtest_credential_unavailable")
        return {
            "type": "snell", "tag": "out", "server": server,
            "server_port": port, "psk": psk, "version": version, "mode": mode,
        }

    async def _run_core(
        self, binary: Path, proxy: dict[str, Any], *, singbox: bool = False, **options
    ) -> Measurement:
        port = self._free_port()
        root = Path(tempfile.mkdtemp(prefix="open-node-speedtest-", dir=self.state_dir))
        if singbox:
            config = root / "config.json"
            config.write_text(json.dumps({
                "log": {"level": "warn"},
                "inbounds": [{
                    "type": "mixed", "tag": "in", "listen": "127.0.0.1",
                    "listen_port": port,
                }],
                "outbounds": [self._singbox_outbound(proxy)],
                "route": {"final": "out"},
            }, ensure_ascii=False, separators=(",", ":")), "utf-8")
            arguments = (str(binary), "run", "-D", str(root), "-c", "config.json")
        else:
            config = root / "config.yaml"
            proxy = dict(proxy)
            proxy["name"] = "OPEN_NODE_SPEEDTEST_PROXY"
            config.write_text(yaml.safe_dump({
                "mixed-port": port,
                "allow-lan": False,
                "mode": "rule",
                "log-level": "silent",
                "proxies": [proxy],
                "proxy-groups": [{
                    "name": "OPEN_NODE_SPEEDTEST_GROUP", "type": "select",
                    "proxies": ["OPEN_NODE_SPEEDTEST_PROXY"],
                }],
                "rules": ["MATCH,OPEN_NODE_SPEEDTEST_GROUP"],
            }, allow_unicode=True, sort_keys=False), "utf-8")
            arguments = (str(binary), "-d", str(root), "-f", str(config))
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            await self._wait_port(process, port)
            return await asyncio.to_thread(self._measure, port, **options)
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            await asyncio.to_thread(shutil.rmtree, root, True)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    async def _wait_port(process, port: int) -> None:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                break
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.1)
            else:
                writer.close()
                await writer.wait_closed()
                return
        raise SpeedTestError(503, "speedtest_runtime_unavailable")

    @staticmethod
    def _opener(port: int):
        proxy = f"http://127.0.0.1:{port}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": proxy, "https": proxy,
        }))

    @classmethod
    def _request(cls, opener, url: str, *, timeout: float = 15):
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "identity",
        })
        return opener.open(request, timeout=timeout)

    @classmethod
    def _latency(cls, opener, url: str, samples: int) -> float:
        values = []
        for _ in range(samples):
            started = time.monotonic()
            with cls._request(opener, url, timeout=10) as response:
                response.read(1)
            values.append((time.monotonic() - started) * 1000)
        values.sort()
        selected = values[:2] if samples >= 3 else values
        return round(sum(selected) / len(selected), 2)

    @classmethod
    def _measure(
        cls, port: int, *, requested_bytes: int, url: str | None,
        threads: int, buf_size: int, latency_only: bool,
    ) -> Measurement:
        opener = cls._opener(port)
        latency_url = LATENCY_ONLY_URL if latency_only else DEFAULT_LATENCY_URL
        try:
            latency = cls._latency(opener, latency_url, 3 if latency_only else 1)
        except Exception:
            raise SpeedTestError(502, "speedtest_latency_failed") from None
        egress = None
        try:
            with cls._request(opener, EGRESS_URL, timeout=10) as response:
                candidate = response.read(64).decode("ascii").strip()
            egress = str(ip_address(candidate))
        except Exception:
            pass
        if latency_only:
            return Measurement(None, latency, egress, 0)

        target = min(requested_bytes or MAX_DOWNLOAD_BYTES, MAX_DOWNLOAD_BYTES)
        deadline = time.monotonic() + DOWNLOAD_SECONDS

        def download(limit: int) -> int:
            received = 0
            with cls._request(opener, url or DEFAULT_DOWNLOAD_URL, timeout=15) as response:
                while received < limit and time.monotonic() < deadline:
                    chunk = response.read(min(buf_size, limit - received))
                    if not chunk:
                        break
                    received += len(chunk)
            return received

        per_thread = max(1, (target + threads - 1) // threads)
        started = time.monotonic()
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=threads) as pool:
                received = sum(pool.map(download, [per_thread] * threads))
        except Exception:
            raise SpeedTestError(502, "speedtest_download_failed") from None
        elapsed = max(time.monotonic() - started, 0.001)
        if received <= 0:
            raise SpeedTestError(502, "speedtest_download_failed")
        return Measurement(round(received * 8 / elapsed / 1_000_000, 2), latency, egress, received)
