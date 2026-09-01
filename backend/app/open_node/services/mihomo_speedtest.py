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

    def _asset(self) -> dict[str, Any] | None:
        asset = self.manifest.get("assets", {}).get(_platform_key())
        return asset if isinstance(asset, dict) else None

    def _ready(self) -> bool:
        try:
            marker = json.loads(self.marker.read_text("utf-8"))
            info = self.binary.stat()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        asset = self._asset()
        return bool(
            asset
            and stat.S_ISREG(info.st_mode)
            and info.st_size > 1024 * 1024
            and marker == {
                "version": self.version,
                "asset_sha256": asset.get("sha256"),
                "binary_sha256": _sha256(self.binary),
            }
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
        binary = await self.ensure()
        async with self._run_lock:
            return await self._run_core(
                binary, proxy, requested_bytes=requested_bytes, url=url,
                threads=threads, buf_size=buf_size, latency_only=latency_only,
            )

    async def _run_core(self, binary: Path, proxy: dict[str, Any], **options) -> Measurement:
        port = self._free_port()
        root = Path(tempfile.mkdtemp(prefix="open-node-speedtest-", dir=self.state_dir))
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
        process = await asyncio.create_subprocess_exec(
            str(binary), "-d", str(root), "-f", str(config),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            await self._wait_port(process, port)
            return await asyncio.to_thread(self._measure, port, **options)
        finally:
            if process.returncode is None:
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
