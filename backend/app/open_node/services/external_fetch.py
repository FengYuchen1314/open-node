"""Bounded, one-shot HTTPS subscription fetching without application imports.

The network operation runs in this same file as an isolated stdlib-only child.
In particular, a blocked system resolver cannot outlive the parent's deadline.
URLs and response bodies are secrets: IPC is through bounded anonymous pipes,
the command/environment contain neither URL nor user agent, and errors are an
allowlist of fixed messages. No network library with proxy/redirect state is used.
"""

import ipaddress
import json
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import unicodedata
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

MAX_URL_BYTES = 8192
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_TIMEOUT = 30.0
MAX_USER_AGENT = 256
MAX_CONCURRENT_FETCHES = 4
_MAX_REQUEST_BYTES = 12 * 1024
_MAX_RESULT_HEADER_BYTES = 1024
_MAX_HEADER_BYTES = 32 * 1024
_MAX_HEADER_LINE = 8192
_MAX_HEADERS = 100
_MAX_TRANSFER_OVERHEAD = 64 * 1024
_MAX_CHUNKS = 8192
_MAX_ADDRESSES = 64
_READ_SIZE = 16 * 1024
_MAX_METADATA_INT = (1 << 63) - 1
_METADATA_KEYS = frozenset({"upload", "download", "total", "expire"})
_FETCH_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)
_PROTOCOL_MAGIC = b"ONFETCH1 "

_MESSAGES = {
    "invalid_url": "External subscription URL must be a valid HTTPS URL",
    "unsafe_target": "External subscription target is not a public Internet address",
    "invalid_user_agent": "External subscription user agent is invalid",
    "invalid_limits": "External subscription fetch limits are invalid",
    "busy": "External subscription fetch capacity is busy; retry later",
    "timeout": "External subscription fetch timed out",
    "dns_failed": "External subscription hostname could not be resolved",
    "connection_failed": "External subscription connection failed",
    "tls_failed": "External subscription TLS verification or handshake failed",
    "redirect": "External subscription redirects are not allowed",
    "http_status": "External subscription returned an unsuccessful HTTP status",
    "response_too_large": "External subscription response exceeds the size limit",
    "invalid_response": "External subscription response is invalid",
    "unsupported_response": "External subscription response encoding is not supported",
    "fetch_failed": "External subscription fetch failed",
}

# Explicit ranges keep the policy stable across Python/ipaddress releases. The
# is_global checks below also exclude all other special-purpose addresses known
# to the installed standard library. Transition mechanisms fail closed even if
# their embedded IPv4 happens to be public.
_BLOCKED_V4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "168.63.129.16/32",
))
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")
_BLOCKED_V6 = tuple(ipaddress.ip_network(value) for value in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3ffe::/16", "3fff::/20",
))
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_HEADER_NAME = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")


class ExternalFetchError(ValueError):
    """Only known codes and constant, non-provider error messages may escape."""

    def __init__(self, code: str = "fetch_failed"):
        self.code = code if isinstance(code, str) and code in _MESSAGES else "fetch_failed"
        super().__init__(_MESSAGES[self.code])


def _valid_metadata(metadata) -> bool:
    return (
        isinstance(metadata, dict)
        and metadata.keys() <= _METADATA_KEYS
        and all(type(value) is int and 0 <= value <= _MAX_METADATA_INT
                for value in metadata.values())
    )


@dataclass(frozen=True)
class ExternalFetchResult:
    body: bytes = field(repr=False)
    metadata: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.body, bytes) or not _valid_metadata(self.metadata):
            raise ExternalFetchError("invalid_response")
        # Do not share the caller's mutable dictionary with the returned snapshot.
        object.__setattr__(self, "metadata", dict(self.metadata))


def _public_ip(value: str):
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ExternalFetchError("unsafe_target") from None
    if (
        not address.is_global or address.is_private or address.is_reserved
        or address.is_loopback or address.is_link_local or address.is_multicast
        or address.is_unspecified
    ):
        raise ExternalFetchError("unsafe_target")
    if isinstance(address, ipaddress.IPv4Address):
        blocked = any(address in network for network in _BLOCKED_V4)
    else:
        # This also blocks mapped/compatible IPv4 and all NAT64 translation
        # prefixes outside native global unicast. Reject ISATAP within it too.
        interface_prefix = (int(address) >> 32) & 0xFFFFFFFF
        blocked = (
            address not in _GLOBAL_V6
            or any(address in network for network in _BLOCKED_V6)
            or address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or interface_prefix in (0x00005EFE, 0x02005EFE)
        )
    if blocked:
        raise ExternalFetchError("unsafe_target")
    return address


def normalize_external_url(value: str) -> str:
    """Validate without DNS or logging; preserve the provider's path and query."""
    try:
        if not isinstance(value, str) or not value or len(value) > MAX_URL_BYTES:
            raise ExternalFetchError("invalid_url")
        if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
            raise ExternalFetchError("invalid_url")
        if "\\" in value or "#" in value or len(value.encode("utf-8")) > MAX_URL_BYTES:
            raise ExternalFetchError("invalid_url")
        parts = urlsplit(value)
        if parts.scheme.lower() != "https" or not parts.netloc or "@" in parts.netloc:
            raise ExternalFetchError("invalid_url")
        host = parts.hostname
        if not host or "%" in host:
            raise ExternalFetchError("invalid_url")
        # urlsplit accepts an empty port and some non-canonical authorities;
        # validate the entire authority instead of merely reading .hostname.
        if parts.netloc.startswith("["):
            close = parts.netloc.find("]")
            if close < 0 or ":" not in host:
                raise ExternalFetchError("invalid_url")
            suffix = parts.netloc[close + 1:]
        else:
            if ":" in host or "[" in parts.netloc or "]" in parts.netloc:
                raise ExternalFetchError("invalid_url")
            suffix = parts.netloc[len(host):]
        if suffix and not re.fullmatch(r":[0-9]{1,5}", suffix):
            raise ExternalFetchError("invalid_url")
        port = parts.port if suffix else 443
        if port is None or not 1 <= port <= 65535:
            raise ExternalFetchError("invalid_url")
        if host.endswith(".."):
            raise ExternalFetchError("invalid_url")
        host = host.removesuffix(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
            labels = host.split(".")
            if (
                len(host) > 253 or len(labels) < 2
                or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
                or re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", labels[-1])
                or labels[-1] in {"localhost", "local", "internal", "home", "lan"}
            ):
                raise ExternalFetchError("invalid_url") from None
        else:
            address = _public_ip(str(address))
            host = f"[{address}]" if address.version == 6 else str(address)
        if port != 443:
            host += f":{port}"
        # Existing percent escapes are preserved byte-for-byte, including case.
        # Unicode paths are encoded once; malformed/control escapes are rejected.
        target = parts.path or "/"
        if parts.query or "?" in value:
            target += "?" + parts.query
        if re.search(r"%(?![0-9A-Fa-f]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f)", target, re.I):
            raise ExternalFetchError("invalid_url")
        target = quote(target, safe="/:?@!$&'()*+,;=-._~%[]")
        result = "https://" + host + target
        if len(result) > MAX_URL_BYTES:
            raise ExternalFetchError("invalid_url")
        return result
    except ExternalFetchError:
        raise
    except (ValueError, UnicodeError, OverflowError):
        raise ExternalFetchError("invalid_url") from None


def _validate_user_agent(value: str) -> str:
    if (
        not isinstance(value, str) or len(value) > MAX_USER_AGENT
        or any(not 32 <= ord(char) <= 126 for char in value)
    ):
        raise ExternalFetchError("invalid_user_agent")
    return value


def _validate_limits(timeout, max_bytes) -> float:
    if (
        type(timeout) not in (int, float) or not 0 < timeout <= MAX_TIMEOUT
        or not math.isfinite(timeout)
        or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BODY_BYTES
    ):
        raise ExternalFetchError("invalid_limits")
    return float(timeout)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExternalFetchError("timeout")
    return remaining


def _resolve_public(host: str, port: int):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            # No AI_ADDRCONFIG: validate both A and AAAA, including an address
            # family that the current server would not choose for its connection.
            answers = socket.getaddrinfo(
                host, port, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP,
            )
        except OSError:
            raise ExternalFetchError("dns_failed") from None
    else:
        address = _public_ip(str(literal))
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return [(family, str(address))]
    if not answers or len(answers) > _MAX_ADDRESSES:
        raise ExternalFetchError("dns_failed")
    approved = []
    for family, kind, protocol, _canonical, sockaddr in answers:
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or kind != socket.SOCK_STREAM or protocol not in (0, socket.IPPROTO_TCP)
            or not isinstance(sockaddr, tuple)
            or len(sockaddr) != (4 if family == socket.AF_INET6 else 2)
            or sockaddr[1] != port
            or (family == socket.AF_INET6 and sockaddr[2:] != (0, 0))
        ):
            raise ExternalFetchError("unsafe_target")
        address = _public_ip(sockaddr[0])
        if address.version != (6 if family == socket.AF_INET6 else 4):
            raise ExternalFetchError("unsafe_target")
        entry = (family, str(address))
        if entry not in approved:
            approved.append(entry)
    return approved


def _connect_tls(host: str, port: int, deadline: float):
    addresses = _resolve_public(host, port)
    _remaining(deadline)
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    for family, address in addresses:
        raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            raw.settimeout(_remaining(deadline))
            # socket.connect on a canonical numeric literal performs no second
            # DNS lookup. Never substitute create_connection/HTTPSConnection.
            endpoint = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            raw.connect(endpoint)
            raw.settimeout(_remaining(deadline))
            return context.wrap_socket(raw, server_hostname=host)
        except ExternalFetchError:
            raw.close()
            raise
        except TimeoutError:
            raw.close()
            raise ExternalFetchError("timeout") from None
        except ssl.SSLError:
            raw.close()
            raise ExternalFetchError("tls_failed") from None
        except OSError:
            raw.close()
    raise ExternalFetchError("connection_failed")


class _ResponseReader:
    def __init__(self, connection, deadline: float, max_bytes: int):
        self.connection = connection
        self.deadline = deadline
        self.buffer = bytearray()
        self.received = 0
        self.wire_limit = max_bytes + _MAX_HEADER_BYTES + _MAX_TRANSFER_OVERHEAD
        self.eof = False

    def _fill(self) -> bool:
        self.connection.settimeout(_remaining(self.deadline))
        chunk = self.connection.recv(min(_READ_SIZE, self.wire_limit - self.received + 1))
        self.received += len(chunk)
        if self.received > self.wire_limit:
            raise ExternalFetchError("response_too_large")
        self.buffer.extend(chunk)
        self.eof = not chunk
        return bool(chunk)

    def read_some(self, size: int) -> bytes:
        if not self.buffer and (self.eof or not self._fill()):
            return b""
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def read_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.read_some(size - len(result))
            if not chunk:
                raise ExternalFetchError("invalid_response")
            result.extend(chunk)
        return bytes(result)

    def readline(self, limit: int) -> bytes:
        while True:
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end + 1 > limit or end == 0 or self.buffer[end - 1] != 13:
                    raise ExternalFetchError("invalid_response")
                return self.read_exact(end + 1)
            if len(self.buffer) >= limit:
                raise ExternalFetchError("response_too_large")
            if self.eof or not self._fill():
                raise ExternalFetchError("invalid_response")


def _read_headers(reader: _ResponseReader) -> dict[str, str]:
    status = reader.readline(_MAX_HEADER_LINE)
    if re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [\x20-\x7e]*)?\r\n", status) is None:
        raise ExternalFetchError("invalid_response")
    code = int(status[9:12])
    if 300 <= code <= 399:
        raise ExternalFetchError("redirect")
    if code != 200:
        raise ExternalFetchError("http_status")
    total = len(status)
    selected = {}
    for _ in range(_MAX_HEADERS + 1):
        line = reader.readline(_MAX_HEADER_LINE)
        total += len(line)
        if total > _MAX_HEADER_BYTES:
            raise ExternalFetchError("response_too_large")
        if line == b"\r\n":
            return selected
        name, colon, value = line[:-2].partition(b":")
        if not colon or _HEADER_NAME.fullmatch(name) is None:
            raise ExternalFetchError("invalid_response")
        if any(char != 9 and not 32 <= char <= 126 for char in value):
            raise ExternalFetchError("invalid_response")
        key = name.decode("ascii").lower()
        if key in {"content-length", "transfer-encoding", "content-encoding",
                   "subscription-userinfo"}:
            if key in selected:
                raise ExternalFetchError("invalid_response")
            selected[key] = value.decode("ascii").strip(" \t")
    raise ExternalFetchError("response_too_large")


def _encoded_body(reader: _ResponseReader, headers: dict[str, str], max_bytes: int):
    transfer = headers.get("transfer-encoding")
    length = headers.get("content-length")
    if transfer is not None and length is not None:
        raise ExternalFetchError("invalid_response")
    received = 0
    if transfer is not None:
        if transfer.lower() != "chunked":
            raise ExternalFetchError("unsupported_response")
        overhead = 0
        for _ in range(_MAX_CHUNKS):
            line = reader.readline(128)
            overhead += len(line) + 2
            if overhead > _MAX_TRANSFER_OVERHEAD:
                raise ExternalFetchError("response_too_large")
            # Chunk extensions and nonempty trailers are deliberately not part
            # of this first, strict subscription transport implementation.
            if re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line) is None:
                raise ExternalFetchError("invalid_response")
            size = int(line, 16)
            if size > max_bytes - received:
                raise ExternalFetchError("response_too_large")
            if not size:
                if reader.readline(_MAX_HEADER_LINE) != b"\r\n":
                    raise ExternalFetchError("invalid_response")
                break
            received += size
            while size:
                chunk = reader.read_exact(min(size, _READ_SIZE))
                size -= len(chunk)
                yield chunk
            if reader.read_exact(2) != b"\r\n":
                raise ExternalFetchError("invalid_response")
        else:
            raise ExternalFetchError("response_too_large")
    elif length is not None:
        if re.fullmatch(r"[0-9]{1,10}", length) is None:
            raise ExternalFetchError("invalid_response")
        remaining = int(length)
        if remaining > max_bytes:
            raise ExternalFetchError("response_too_large")
        while remaining:
            chunk = reader.read_exact(min(remaining, _READ_SIZE))
            remaining -= len(chunk)
            yield chunk
    else:
        while chunk := reader.read_some(_READ_SIZE):
            received += len(chunk)
            if received > max_bytes:
                raise ExternalFetchError("response_too_large")
            yield chunk
    # We requested Connection: close and never reuse a connection. Requiring
    # EOF also detects falsely small Content-Length and bytes after last-chunk.
    if reader.read_some(1):
        raise ExternalFetchError("invalid_response")


def _read_body(reader: _ResponseReader, headers: dict[str, str], max_bytes: int) -> bytes:
    encoding = headers.get("content-encoding", "identity").lower()
    if encoding not in {"identity", "gzip"}:
        raise ExternalFetchError("unsupported_response")
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    body = bytearray()
    try:
        for chunk in _encoded_body(reader, headers, max_bytes):
            if decompressor is not None:
                chunk = decompressor.decompress(chunk, max_bytes - len(body) + 1)
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ExternalFetchError("response_too_large")
            if decompressor is not None:
                if decompressor.unconsumed_tail:
                    raise ExternalFetchError("response_too_large")
                if decompressor.unused_data:
                    raise ExternalFetchError("invalid_response")
        if decompressor is not None and not decompressor.eof:
            raise ExternalFetchError("invalid_response")
    except zlib.error:
        raise ExternalFetchError("invalid_response") from None
    # Do not call zlib.flush(length): its length is NOT an output-size ceiling.
    return bytes(body)


def _parse_metadata(value: str) -> dict[str, int]:
    result = {}
    seen = set()
    for item in value.split(";"):
        key, separator, raw = item.partition("=")
        key, raw = key.strip().lower(), raw.strip()
        if not separator or key not in _METADATA_KEYS:
            continue
        if key in seen:
            result.pop(key, None)
            continue
        seen.add(key)
        if re.fullmatch(r"[0-9]{1,19}", raw) is not None:
            number = int(raw)
            if number <= _MAX_METADATA_INT:
                result[key] = number
    return result


def _fetch_in_worker(url: str, user_agent: str, deadline: float, max_bytes: int):
    parts = urlsplit(normalize_external_url(url))
    agent = _validate_user_agent(user_agent)
    _remaining(deadline)
    connection = _connect_tls(parts.hostname, parts.port or 443, deadline)
    try:
        target = parts.path or "/"
        if parts.query or "?" in url:
            target += "?" + parts.query
        request = (
            f"GET {target} HTTP/1.1\r\nHost: {parts.netloc}\r\nUser-Agent: {agent}\r\n"
            "Accept: */*\r\nAccept-Encoding: gzip\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        connection.settimeout(_remaining(deadline))
        connection.sendall(request)
        reader = _ResponseReader(connection, deadline, max_bytes)
        headers = _read_headers(reader)
        body = _read_body(reader, headers, max_bytes)
        return ExternalFetchResult(body, _parse_metadata(headers.get("subscription-userinfo", "")))
    finally:
        connection.close()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protocol field")
        result[key] = value
    return result


def _decode_result(output: bytes, max_bytes: int) -> ExternalFetchResult:
    header, newline, body = output.partition(b"\n")
    if (
        not newline or len(header) > _MAX_RESULT_HEADER_BYTES
        or not header.startswith(_PROTOCOL_MAGIC)
    ):
        raise ExternalFetchError("invalid_response")
    try:
        message = json.loads(header[len(_PROTOCOL_MAGIC):], object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise ExternalFetchError("invalid_response") from None
    if not isinstance(message, dict):
        raise ExternalFetchError("invalid_response")
    if message.get("ok") is False and message.keys() == {"ok", "code"} and not body:
        raise ExternalFetchError(message["code"])
    if (
        message.keys() != {"ok", "size", "metadata"} or message["ok"] is not True
        or type(message["size"]) is not int or not 0 <= message["size"] <= max_bytes
        or len(body) != message["size"] or not _valid_metadata(message["metadata"])
    ):
        raise ExternalFetchError("invalid_response")
    return ExternalFetchResult(body, message["metadata"])


def _exchange_worker(process, request: bytes, deadline: float, max_bytes: int):
    """One bounded pipe helper per slot, always joined after killing/reaping.

    A pipe helper (not a DNS/network thread) makes this work on Windows as well
    as POSIX. Killing this fixed, non-forking child closes both pipe peers, so
    even a child stuck before reading stdin cannot leave an I/O thread behind.
    """
    ready = threading.Event()
    state = {}
    output_limit = max_bytes + _MAX_RESULT_HEADER_BYTES + 1

    def exchange():
        try:
            process.stdin.write(request)
            process.stdin.close()
            state["output"] = process.stdout.read(output_limit + 1)
        except BaseException:
            # The exception may contain a provider-controlled fragment in a
            # test double; never retain it in state or propagate its text.
            pass
        finally:
            ready.set()

    helper = threading.Thread(target=exchange, name="external-fetch-pipe", daemon=True)
    started = False
    try:
        helper.start()
        started = True
        if not ready.wait(_remaining(deadline)):
            raise ExternalFetchError("timeout")
        output = state.get("output")
        if output is None:
            raise ExternalFetchError("fetch_failed")
        if len(output) > output_limit:
            raise ExternalFetchError("response_too_large")
        try:
            process.wait(timeout=_remaining(deadline))
        except subprocess.TimeoutExpired:
            raise ExternalFetchError("timeout") from None
        if process.returncode != 0:
            raise ExternalFetchError("fetch_failed")
        return _decode_result(output, max_bytes)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()  # reap; no worker or unresolved network thread survives
        if started:
            helper.join()
        for pipe in (process.stdin, process.stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass


def fetch_external_subscription(
    url: str, *, user_agent: str = "OpenNode/0.1", timeout: float = 30,
    max_bytes: int = MAX_BODY_BYTES,
) -> ExternalFetchResult:
    timeout = _validate_limits(timeout, max_bytes)
    deadline = time.monotonic() + timeout
    url = normalize_external_url(url)
    user_agent = _validate_user_agent(user_agent)
    if not _FETCH_SLOTS.acquire(blocking=False):
        raise ExternalFetchError("busy")
    try:
        request = json.dumps({
            "url": url, "user_agent": user_agent, "deadline": deadline, "max_bytes": max_bytes,
        }, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if len(request) > _MAX_REQUEST_BYTES:
            raise ExternalFetchError("invalid_limits")
        # In particular, no proxy, Python import hook, TLS key log or application
        # secret environment is inherited. Trust-store overrides remain valid
        # operator configuration; they never disable hostname/cert verification.
        environment = {key: os.environ[key] for key in (
            "SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
        ) if key in os.environ}
        _remaining(deadline)
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--fetch-worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return _exchange_worker(process, request, deadline, max_bytes)
    except ExternalFetchError:
        raise
    except Exception:
        raise ExternalFetchError("fetch_failed") from None
    finally:
        _FETCH_SLOTS.release()


def _worker_main() -> int:
    try:
        if os.name == "posix":
            import resource

            # Prevent secret-bearing crash dumps. Limits affect only this child.
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        request = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(request) > _MAX_REQUEST_BYTES:
            raise ExternalFetchError("invalid_limits")
        message = json.loads(request, object_pairs_hook=_unique_object)
        if not isinstance(message, dict) or message.keys() != {
            "url", "user_agent", "deadline", "max_bytes",
        }:
            raise ExternalFetchError("invalid_limits")
        deadline = message["deadline"]
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ExternalFetchError("invalid_limits")
        _validate_limits(_remaining(deadline), message["max_bytes"])
        result = _fetch_in_worker(
            message["url"], message["user_agent"], deadline, message["max_bytes"],
        )
        header = {"ok": True, "size": len(result.body), "metadata": result.metadata}
        body = result.body
    except ExternalFetchError as exc:
        header, body = {"ok": False, "code": exc.code}, b""
    except TimeoutError:
        header, body = {"ok": False, "code": "timeout"}, b""
    except ssl.SSLError:
        header, body = {"ok": False, "code": "tls_failed"}, b""
    except BaseException:
        header, body = {"ok": False, "code": "fetch_failed"}, b""
    try:
        encoded = json.dumps(header, separators=(",", ":")).encode("ascii")
        sys.stdout.buffer.write(_PROTOCOL_MAGIC + encoded + b"\n" + body)
        sys.stdout.buffer.flush()
    except BaseException:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--fetch-worker"] else 2)
