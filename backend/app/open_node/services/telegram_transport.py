"""One bounded, receipt-checked Telegram request, without secret-bearing HTTP logs.

The caller must commit its durable ``sending`` claim before calling ``send``.
Only a proven pre-request connection failure or a valid flood-control rejection
is safe to retry. Cancellation propagates after closing the owned connection;
the caller must retain the durable attempt as unknown, not enqueue it again.

There is deliberately no general-purpose HTTP client, proxy lookup, redirect,
endpoint setting, environment-provided CA, key log, or HTTP retry here. Private
constructor hooks are exclusively for isolated loopback HTTP/TLS fixtures.
"""

import asyncio
import json
import math
import re
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

SEND_TIMEOUT_SECONDS = 20
CONNECT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TEXT_UTF16_UNITS = 4096
MAX_RETRY_AFTER_SECONDS = 86400
MAX_CHAT_ID = (1 << 52) - 1

_HOST = "api.telegram.org"
_PORT = 443
_MAX_LINE_BYTES = 4096
_MAX_HEADER_BYTES = 16 * 1024
_MAX_HEADERS = 64
_MAX_MESSAGE_ID = (1 << 63) - 1
_TOKEN = re.compile(r"[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,128}", re.ASCII)
_CHAT_ID = re.compile(r"-?[1-9][0-9]{0,18}", re.ASCII)
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_STATUS = re.compile(rb"HTTP/1\.[01] ([1-5][0-9]{2}) [\x20-\x7e]*\r\n")
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,8}\r\n")

TelegramState = Literal["accepted", "failed", "unknown"]

_FAILED_CODES = frozenset({
    "telegram_invalid_token",
    "telegram_invalid_chat_id",
    "telegram_invalid_text",
    "telegram_tls_failed",
    "telegram_bad_request",
    "telegram_unauthorized",
    "telegram_forbidden",
    "telegram_rejected",
})
_RETRYABLE_CODES = frozenset({
    "telegram_connect_timeout",
    "telegram_connect_failed",
    "telegram_rate_limited",
})
_UNKNOWN_CODES = frozenset({
    "telegram_send_timeout",
    "telegram_response_timeout",
    "telegram_connection_lost",
    "telegram_redirect_blocked",
    "telegram_server_error",
    "telegram_invalid_response",
    "telegram_response_too_large",
    "telegram_transport_failure",
})
TELEGRAM_OUTCOME_CODES = (
    frozenset({"telegram_accepted"}) | _FAILED_CODES | _RETRYABLE_CODES | _UNKNOWN_CODES
)
_WORKER_UNKNOWN_CODES = frozenset({
    "notification_worker_interrupted",
    "notification_transport_failure",
})


def _integer_in_range(value: object, minimum: int, maximum: int) -> bool:
    # bool is an int subclass, but never a valid API identifier or delay.
    return type(value) is int and minimum <= value <= maximum


@dataclass(frozen=True, slots=True)
class TelegramOutcome:
    """A secret-free receipt or a fixed operational classification, never raw errors."""

    state: TelegramState
    code: str
    message_id: int | None = None
    retry_after: int | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        invalid = "Invalid Telegram outcome."
        if type(self.code) is not str or type(self.state) is not str:
            raise ValueError(invalid)
        if type(self.retryable) is not bool:
            raise ValueError(invalid)
        if self.code == "telegram_accepted":
            valid = (
                self.state == "accepted"
                and _integer_in_range(self.message_id, 1, _MAX_MESSAGE_ID)
                and self.retry_after is None
                and not self.retryable
            )
        else:
            retryable = self.code in _RETRYABLE_CODES or self.code == "notification_claim_expired"
            failed = self.code in _FAILED_CODES or retryable
            unknown = self.code in _UNKNOWN_CODES or self.code in _WORKER_UNKNOWN_CODES
            delay_valid = (
                _integer_in_range(self.retry_after, 1, MAX_RETRY_AFTER_SECONDS)
                if self.code == "telegram_rate_limited"
                else self.retry_after is None
            )
            valid = (
                ((failed and self.state == "failed") or (unknown and self.state == "unknown"))
                and self.message_id is None
                and self.retryable is retryable
                and delay_valid
            )
        if not valid:
            raise ValueError(invalid)


def _failed(code: str, *, retry_after: int | None = None) -> TelegramOutcome:
    return TelegramOutcome(
        state="failed",
        code=code,
        retry_after=retry_after,
        retryable=code in _RETRYABLE_CODES,
    )


def _unknown(code: str) -> TelegramOutcome:
    return TelegramOutcome(state="unknown", code=code)


class _InvalidResponse(Exception):
    pass


class _ResponseTooLarge(Exception):
    pass


class _ConnectionLost(Exception):
    pass


class _WireReader:
    """Account for headers, framing and body, not merely the decoded JSON bytes."""

    def __init__(self, reader: asyncio.StreamReader):
        self.reader = reader
        self.used = 0

    def _account(self, size: int) -> None:
        self.used += size
        if self.used > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge

    async def line(self) -> bytes:
        try:
            data = await self.reader.readuntil(b"\r\n")
        except asyncio.LimitOverrunError:
            raise _ResponseTooLarge from None
        except asyncio.IncompleteReadError:
            raise _ConnectionLost from None
        self._account(len(data))
        if len(data) > _MAX_LINE_BYTES:
            raise _ResponseTooLarge
        if b"\r" in data[:-2] or b"\n" in data[:-2]:
            raise _InvalidResponse
        return data

    async def exactly(self, size: int) -> bytes:
        if size > MAX_RESPONSE_BYTES - self.used:
            raise _ResponseTooLarge
        try:
            data = await self.reader.readexactly(size)
        except asyncio.IncompleteReadError:
            raise _ConnectionLost from None
        self._account(len(data))
        return data

    async def to_eof(self) -> bytes:
        parts = []
        while True:
            # One look-ahead byte distinguishes an exact-limit body from excess.
            data = await self.reader.read(min(4096, MAX_RESPONSE_BYTES - self.used + 1))
            if not data:
                return b"".join(parts)
            self._account(len(data))
            parts.append(data)

    async def require_eof(self) -> None:
        # This is a single-use connection and the request says Connection: close.
        # Do not accept a short Content-Length hiding additional response bytes.
        data = await self.reader.read(1)
        if data:
            self._account(len(data))
            raise _InvalidResponse


async def _headers(wire: _WireReader) -> dict[bytes, bytes]:
    start = wire.used
    headers: dict[bytes, bytes] = {}
    while True:
        line = await wire.line()
        if wire.used - start > _MAX_HEADER_BYTES:
            raise _ResponseTooLarge
        if line == b"\r\n":
            return headers
        if len(headers) >= _MAX_HEADERS or b":" not in line:
            raise _InvalidResponse
        name, value = line[:-2].split(b":", 1)
        name = name.lower()
        if not _HEADER_NAME.fullmatch(name) or name in headers:
            raise _InvalidResponse
        if any(byte < 32 and byte != 9 or byte == 127 for byte in value):
            raise _InvalidResponse
        headers[name] = value.strip(b" \t")


async def _read_response(reader: asyncio.StreamReader) -> tuple[int, dict[bytes, bytes], bytes]:
    wire = _WireReader(reader)
    match = _STATUS.fullmatch(await wire.line())
    if match is None:
        raise _InvalidResponse
    status = int(match[1])
    if status < 200:
        raise _InvalidResponse
    headers = await _headers(wire)
    length = headers.get(b"content-length")
    transfer = headers.get(b"transfer-encoding")
    encoding = headers.get(b"content-encoding", b"identity").lower()
    if encoding != b"identity" or (length is not None and transfer is not None):
        raise _InvalidResponse
    if transfer is not None:
        if transfer.lower() != b"chunked":
            raise _InvalidResponse
        parts = []
        while True:
            line = await wire.line()
            if not _CHUNK_SIZE.fullmatch(line):
                raise _InvalidResponse
            size = int(line[:-2], 16)
            if size == 0:
                # No trailers are needed by sendMessage; reject ambiguous ones.
                if await wire.line() != b"\r\n":
                    raise _InvalidResponse
                break
            parts.append(await wire.exactly(size))
            if await wire.exactly(2) != b"\r\n":
                raise _InvalidResponse
        body = b"".join(parts)
        await wire.require_eof()
    elif length is not None:
        if not re.fullmatch(rb"[0-9]{1,9}", length):
            raise _InvalidResponse
        body = await wire.exactly(int(length))
        await wire.require_eof()
    else:
        body = await wire.to_eof()
    return status, headers, body


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidResponse
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> None:
    raise _InvalidResponse


def _json_object(headers: dict[bytes, bytes], body: bytes) -> dict[str, object]:
    content_type = headers.get(b"content-type", b"").lower().replace(b" ", b"")
    if content_type not in {
        b"application/json",
        b"application/json;charset=utf-8",
        b'application/json;charset="utf-8"',
    }:
        raise _InvalidResponse
    try:
        result = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise _InvalidResponse from None
    if type(result) is not dict:
        raise _InvalidResponse
    return result


def _classify_response(
    status: int, headers: dict[bytes, bytes], body: bytes, chat_id: str, text: str
) -> TelegramOutcome:
    if 300 <= status < 400:
        return _unknown("telegram_redirect_blocked")
    if status >= 500:
        return _unknown("telegram_server_error")
    if 400 <= status < 500 and status != 429:
        code = {
            400: "telegram_bad_request",
            401: "telegram_unauthorized",
            403: "telegram_forbidden",
        }.get(status, "telegram_rejected")
        return _failed(code)
    data = _json_object(headers, body)
    if status == 429:
        parameters = data.get("parameters")
        if (
            data.get("ok") is not False
            or type(data.get("error_code")) is not int
            or data["error_code"] != 429
            or type(parameters) is not dict
            or not _integer_in_range(parameters.get("retry_after"), 1, MAX_RETRY_AFTER_SECONDS)
            or "result" in data
        ):
            raise _InvalidResponse
        return _failed("telegram_rate_limited", retry_after=parameters["retry_after"])
    if status != 200 or data.get("ok") is not True or "error_code" in data:
        raise _InvalidResponse
    result = data.get("result")
    if type(result) is not dict:
        raise _InvalidResponse
    chat = result.get("chat")
    if (
        not _integer_in_range(result.get("message_id"), 1, _MAX_MESSAGE_ID)
        or not _integer_in_range(result.get("date"), 1, _MAX_MESSAGE_ID)
        or type(chat) is not dict
        or type(chat.get("id")) is not int
        or chat["id"] != int(chat_id)
        or type(chat.get("type")) is not str
        or chat["type"] not in {"private", "group", "supergroup", "channel"}
        or type(result.get("text")) is not str
        or result["text"] != text
    ):
        raise _InvalidResponse
    return TelegramOutcome(
        state="accepted", code="telegram_accepted", message_id=result["message_id"]
    )


def _tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    # Use OpenSSL's compiled trust paths, not SSL_CERT_FILE / SSL_CERT_DIR.
    # SSLContext (unlike create_default_context) does not enable SSLKEYLOGFILE.
    paths = ssl.get_default_verify_paths()
    cafile = paths.openssl_cafile
    capath = paths.openssl_capath
    cafile = cafile if cafile and Path(cafile).is_file() else None
    capath = capath if capath and Path(capath).is_dir() else None
    if cafile is None and capath is None:
        raise ssl.SSLError("System trust store unavailable.")
    context.load_verify_locations(cafile=cafile, capath=capath)
    return context


async def _connect(context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(
        _HOST,
        _PORT,
        ssl=context,
        server_hostname=_HOST,
        limit=_MAX_LINE_BYTES,
        ssl_handshake_timeout=CONNECT_TIMEOUT_SECONDS,
    )


_Connector = Callable[
    [ssl.SSLContext], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]


def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is not None:
        # No wait_closed(): TLS shutdown must not extend the hard send deadline.
        with suppress(Exception):
            writer.transport.abort()
        with suppress(Exception):
            writer.close()


class TelegramTransport:
    """Fixed Telegram endpoint. Underscored constructor hooks are test-only."""

    def __init__(
        self,
        *,
        _test_connector: _Connector | None = None,
        _test_ssl_context: ssl.SSLContext | None = None,
        _test_timeout: float | None = None,
    ):
        timeout = SEND_TIMEOUT_SECONDS if _test_timeout is None else _test_timeout
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or not 0 < timeout <= SEND_TIMEOUT_SECONDS
            or (_test_ssl_context is not None and _test_connector is None)
        ):
            raise ValueError("Invalid Telegram test transport configuration.")
        self._timeout = timeout
        self._connector = _connect if _test_connector is None else _test_connector
        self._context: ssl.SSLContext | None = None
        if _test_ssl_context is not None:
            if (
                not isinstance(_test_ssl_context, ssl.SSLContext)
                or not _test_ssl_context.check_hostname
                or _test_ssl_context.verify_mode != ssl.CERT_REQUIRED
                or _test_ssl_context.minimum_version < ssl.TLSVersion.TLSv1_2
            ):
                raise ValueError("Invalid Telegram test TLS configuration.")
            self._context = _test_ssl_context
        else:
            try:
                self._context = _tls_context()
            except (OSError, ValueError):
                # A missing system trust store fails closed without an exception
                # string or a credential ever reaching the caller/logging stack.
                pass

    async def send(self, token: SecretStr, chat_id: str, text: str) -> TelegramOutcome:
        """Send once. Accepted means a matching Telegram receipt, never a read receipt."""
        if not isinstance(token, SecretStr):
            return _failed("telegram_invalid_token")
        secret = token.get_secret_value()
        if type(secret) is not str or len(secret) > 149 or not _TOKEN.fullmatch(secret):
            return _failed("telegram_invalid_token")
        if (
            type(chat_id) is not str
            or len(chat_id) > 20
            or not _CHAT_ID.fullmatch(chat_id)
            or not -MAX_CHAT_ID <= int(chat_id) <= MAX_CHAT_ID
        ):
            return _failed("telegram_invalid_chat_id")
        if type(text) is not str or not 1 <= len(text) <= MAX_TEXT_UTF16_UNITS:
            return _failed("telegram_invalid_text")
        try:
            units = len(text.encode("utf-16-le")) // 2
        except UnicodeError:
            return _failed("telegram_invalid_text")
        if units > MAX_TEXT_UTF16_UNITS or any(
            ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in text
        ):
            return _failed("telegram_invalid_text")
        if self._context is None:
            return _failed("telegram_tls_failed")
        body = json.dumps(
            {
                "chat_id": chat_id,
                "text": text,
                "link_preview_options": {"is_disabled": True},
                "allow_paid_broadcast": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = (
            f"POST /bot{secret}/sendMessage HTTP/1.1\r\n"
            f"Host: {_HOST}\r\n"
            "Content-Type: application/json\r\n"
            "Accept: application/json\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii") + body
        writer = None
        phase = "connect"
        try:
            async with asyncio.timeout(self._timeout):
                async with asyncio.timeout(min(CONNECT_TIMEOUT_SECONDS, self._timeout)):
                    reader, writer = await self._connector(self._context)
                # Set before write: even a synchronous write error may be partial.
                phase = "send"
                writer.write(request)
                await writer.drain()
                phase = "response"
                status, headers, response = await _read_response(reader)
                return _classify_response(status, headers, response, chat_id, text)
        except TimeoutError:
            if phase == "connect":
                return _failed("telegram_connect_timeout")
            code = "telegram_send_timeout" if phase == "send" else "telegram_response_timeout"
            return _unknown(code)
        except ssl.SSLError:
            if phase == "connect":
                return _failed("telegram_tls_failed")
            return _unknown("telegram_connection_lost")
        except (OSError, EOFError, _ConnectionLost):
            if phase == "connect":
                return _failed("telegram_connect_failed")
            return _unknown("telegram_connection_lost")
        except _ResponseTooLarge:
            return _unknown("telegram_response_too_large")
        except _InvalidResponse:
            return _unknown("telegram_invalid_response")
        except Exception:
            # Do not interpolate/log an exception, response, text or token.
            return _unknown("telegram_transport_failure")
        finally:
            _close(writer)
