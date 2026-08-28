import asyncio
import ipaddress
import os
import re
import shutil
import signal
import socket
from contextlib import suppress
from time import monotonic
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from open_node_agent.config import AgentConfig
from open_node_agent.runtime import MAX_OUTPUT_BYTES, RuntimeFailure


class LatencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    domains: list[str] = Field(min_length=1, max_length=200)
    timeout_ms: int = Field(default=2000, ge=200, le=10000)
    allow_icmp: bool = False


def host_name(value: str) -> str:
    if not value or len(value) > 253 or any(char.isspace() for char in value):
        raise RuntimeFailure("Invalid diagnostic host")
    if "%" in value:
        raise RuntimeFailure("Scoped addresses are not supported")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            value = value.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            raise RuntimeFailure("Invalid diagnostic host") from None
        if not value or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ):
            raise RuntimeFailure("Invalid diagnostic host") from None
        return value
    if address.is_multicast or address.is_unspecified or str(address) == "255.255.255.255":
        raise RuntimeFailure("Diagnostic targets must be unicast addresses")
    return str(address)


def latency_target(raw: str) -> tuple[str, int, str]:
    value = raw.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise RuntimeFailure("Invalid diagnostic target")
    try:
        # Bare IPv6 has no port. Explicit ports require the standard bracket notation.
        address = ipaddress.ip_address(value)
    except ValueError:
        parts = urlsplit(value if "://" in value else "//" + value)
        if parts.scheme and parts.scheme not in {"http", "https"}:
            raise RuntimeFailure("Only HTTP(S) URLs or host targets are accepted") from None
        if parts.username is not None or parts.password is not None or not parts.hostname:
            raise RuntimeFailure("Diagnostic targets cannot contain credentials") from None
        host, port = host_name(parts.hostname), parts.port
        if port is None:
            port = 443
    else:
        host, port = host_name(str(address)), 443
    if not 1 <= port <= 65535:
        raise RuntimeFailure("Diagnostic port must be between 1 and 65535")
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return host, port, target


async def probe_process(*args: str, timeout: float) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env={"PATH": os.defpath, "LC_ALL": "C", "LANG": "C", "NO_COLOR": "1", "TERM": "dumb"},
    )

    async def bounded_read(stream):
        data = bytearray()
        while block := await stream.read(4096):
            data.extend(block)
            if len(data) > MAX_OUTPUT_BYTES:
                raise RuntimeFailure("Diagnostic tool exceeded the output limit")
        return data.decode(errors="replace")

    readers = [
        asyncio.create_task(bounded_read(stream)) for stream in (process.stdout, process.stderr)
    ]
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await asyncio.gather(*readers)
            return await process.wait(), stdout, stderr
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Drain pipes after killing the owned process group so a full pipe cannot block wait().
        await process.communicate()


def network_error(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "Probe timed out"
    if isinstance(error, socket.gaierror):
        return "DNS resolution failed"
    if isinstance(error, ConnectionRefusedError):
        return "TCP connection refused"
    if isinstance(error, PermissionError):
        return "Probe permission denied"
    return str(error)[:512] or type(error).__name__


class Diagnostics:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.slots = asyncio.Semaphore(16)

    def route_available(self) -> bool:
        binary = self.config.nexttrace_binary
        if binary is None or not binary.is_file() or not os.access(binary, os.X_OK):
            return False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP):
                return True
        except OSError:
            return False

    async def latency(self, body: dict) -> dict:
        request = LatencyRequest.model_validate(body)
        targets = list(dict.fromkeys(latency_target(value) for value in request.domains))
        results = await asyncio.gather(
            *(self.probe_latency(host, port, target, request) for host, port, target in targets)
        )
        results.sort(
            key=lambda item: (not item["success"], item.get("latency_ms", 0), item["target"])
        )
        return {"success": True, "count": len(results), "results": results}

    async def probe_latency(
        self, host: str, port: int, target: str, request: LatencyRequest
    ) -> dict:
        result = {
            "domain": host,
            "target": target,
            "key": target,
            "success": False,
            "method": "tcp",
        }
        timeout = request.timeout_ms / 1000
        async with self.slots:
            started = monotonic()
            try:
                async with asyncio.timeout(timeout):
                    _, writer = await asyncio.open_connection(host, port, happy_eyeballs_delay=0.25)
                    elapsed = monotonic() - started
                    writer.close()
                    await writer.wait_closed()
                return {**result, "success": True, "latency_ms": round(elapsed * 1000)}
            except (OSError, TimeoutError) as error:
                result["error"] = network_error(error)
            if request.allow_icmp:
                ping = shutil.which("ping")
                if ping is None:
                    result["icmp_error"] = "ICMP probe tool is not installed"
                    return result
                try:
                    code, stdout, stderr = await probe_process(
                        ping,
                        "-n",
                        "-c",
                        "1",
                        "-W",
                        str(timeout),
                        "--",
                        host,
                        timeout=timeout + 0.25,
                    )
                    sample = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", stdout)
                    if code == 0 and sample:
                        return {
                            **result,
                            "success": True,
                            "method": "icmp",
                            "latency_ms": round(float(sample[1])),
                            "tcp_error": result["error"],
                            "error": "",
                        }
                    result["icmp_error"] = stderr.strip()[:512] or "No ICMP echo reply"
                except (OSError, TimeoutError, RuntimeFailure) as error:
                    result["icmp_error"] = network_error(error)
        return result

    async def return_route(self, body: dict) -> dict:
        from open_node_agent.route_trace import RouteRequest, decode_trace, trace_result

        request = RouteRequest.model_validate(body)
        results = []
        for target in request.targets:
            host = host_name(target.host.strip())
            result = {
                "carrier": target.carrier,
                "region": target.region,
                "target": host,
                "success": False,
                "source": "nexttrace",
                "route_type": "Unknown",
                "hops": [],
                "path_asns": [],
            }
            if not self.route_available():
                results.append(
                    {
                        **result,
                        "error": "NextTrace and raw socket permission are required",
                    }
                )
                continue
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    addresses = await asyncio.get_running_loop().getaddrinfo(
                        host,
                        target.port,
                        family=socket.AF_INET6 if request.ip_version == 6 else socket.AF_INET,
                        type=socket.SOCK_STREAM,
                    )
                    resolved = host_name(addresses[0][4][0])
                    result["resolved_target"] = resolved
                    code, output, error = await probe_process(
                        str(self.config.nexttrace_binary),
                        f"-{request.ip_version}",
                        "-T",
                        "-p",
                        str(target.port),
                        "-q",
                        "2",
                        "--max-attempts",
                        "2",
                        "--timeout",
                        "1000",
                        "-m",
                        "30",
                        "--no-rdns",
                        "-M",
                        "-j",
                        "-g",
                        "en",
                        "-d",
                        "LeoMoeAPI" if self.config.nexttrace_geoip else "disable-geoip",
                        resolved,
                        timeout=request.timeout_seconds,
                    )
                if code:
                    result["error"] = error.strip()[:512] or f"NextTrace exited with status {code}"
                else:
                    result.update(trace_result(decode_trace(output), resolved))
            except (OSError, TimeoutError, ValueError) as error:
                result["error"] = network_error(error)
            results.append(result)
        return {"success": True, "results": results}
