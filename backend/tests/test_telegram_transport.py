"""Run only in the isolated VPS test directory; never contacts the real Bot API.

The real HTTP/TLS peers below bind loopback and are injected only through private
constructor hooks. Tokens, certificates and message contents are fixture-only.
"""

import asyncio
import json
import logging
import ssl
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from open_node.services import telegram_transport as tg
from pydantic import SecretStr

TOKEN_VALUE = "123456789:FixtureOnlyTelegramTokenNeverARealCredential_123"
TOKEN = SecretStr(TOKEN_VALUE)
CHAT_ID = "-1001234567890"
TEXT = "Open Node 测试通知\n套餐名称：演示 _名称_* [测试]\t<纯文本>"


def _receipt(*, chat_id=CHAT_ID, text=TEXT):
    return {
        "ok": True,
        "result": {
            "message_id": 12345,
            "date": 1788192000,
            "chat": {"id": int(chat_id), "type": "supergroup"},
            "text": text,
        },
    }


def _body(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _http(body=None, *, status=200, framing="length", headers=()):
    if body is None:
        body = _body(_receipt())
    head = f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n".encode()
    for name, value in headers:
        head += name + b": " + value + b"\r\n"
    if framing == "length":
        return head + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    if framing == "chunked":
        chunks = [body[index:index + 7] for index in range(0, len(body), 7)]
        framed = b"".join(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" for chunk in chunks)
        return head + b"Transfer-Encoding: chunked\r\n\r\n" + framed + b"0\r\n\r\n"
    assert framing == "close"
    return head + b"\r\n" + body


class _Peer:
    def __init__(self, port, *, tls):
        self.port = port
        self.tls = tls
        self.requests = []
        self.connected = 0
        self.request_received = asyncio.Event()
        self.connection_closed = asyncio.Event()

    async def connect(self, context):
        self.connected += 1
        kwargs = {"limit": 4096}
        if self.tls:
            kwargs.update(ssl=context, server_hostname="api.telegram.org")
        return await asyncio.open_connection("127.0.0.1", self.port, **kwargs)

    def transport(self, *, context=None, timeout=3):
        return tg.TelegramTransport(
            _test_connector=self.connect,
            _test_ssl_context=context,
            _test_timeout=timeout,
        )


@asynccontextmanager
async def _peer(response=None, *, server_context=None, responder=None):
    tasks = set()
    peer = _Peer(0, tls=server_context is not None)

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            pairs = [line.split(b":", 1) for line in header.split(b"\r\n")[1:] if b":" in line]
            headers = {name.lower(): value.strip() for name, value in pairs}
            body = await asyncio.wait_for(reader.readexactly(int(headers[b"content-length"])), 3)
            peer.requests.append((header, body))
            peer.request_received.set()
            if responder is not None:
                await responder(reader, writer)
            else:
                writer.write(_http() if response is None else response)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)
            peer.connection_closed.set()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    peer.port = server.sockets[0].getsockname()[1]
    try:
        yield peer
    finally:
        server.close()
        await server.wait_closed()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def tls_files(tmp_path):
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Isolated fixture CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=3))
        .not_valid_after(now + timedelta(days=3))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "fixture-ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def build(*, hostname="api.telegram.org", expired=False):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_path = tmp_path / f"{hostname}-{expired}-cert.pem"
        key_path = tmp_path / f"{hostname}-{expired}-key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        key_path.chmod(0o600)
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.minimum_version = ssl.TLSVersion.TLSv1_2
        server.load_cert_chain(cert_path, key_path)
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.minimum_version = ssl.TLSVersion.TLSv1_2
        client.load_verify_locations(cafile=ca_path)
        return server, client

    return build, ca_path


def test_outcome_contract_is_frozen_bounded_and_secret_free():
    assert tg.SEND_TIMEOUT_SECONDS == 20
    assert tg.CONNECT_TIMEOUT_SECONDS == 5
    assert tg.MAX_RESPONSE_BYTES == 65536
    assert len(tg.TELEGRAM_OUTCOME_CODES) == 20
    result = tg.TelegramOutcome("accepted", "telegram_accepted", message_id=123)
    assert result.retryable is False
    with pytest.raises(FrozenInstanceError):
        result.code = TOKEN_VALUE
    assert TOKEN_VALUE not in repr(result)
    assert not hasattr(result, "__dict__")
    assert TOKEN_VALUE not in repr(tg.TelegramTransport())
    for code in ("notification_worker_interrupted", "notification_transport_failure"):
        assert tg.TelegramOutcome("unknown", code).retryable is False
    assert tg.TelegramOutcome("failed", "notification_claim_expired", retryable=True).retryable


@pytest.mark.parametrize("arguments", [
    {"state": "unknown", "code": TOKEN_VALUE},
    {"state": TOKEN_VALUE, "code": "telegram_invalid_response"},
    {"state": "accepted", "code": "telegram_accepted"},
    {"state": "accepted", "code": "telegram_accepted", "message_id": True},
    {"state": "accepted", "code": "telegram_accepted", "message_id": -1},
    {"state": "accepted", "code": "telegram_accepted", "message_id": 2**63},
    {"state": "accepted", "code": "telegram_accepted", "message_id": 1, "retryable": True},
    {"state": "failed", "code": "telegram_accepted", "message_id": 1},
    {"state": "unknown", "code": "telegram_invalid_response", "message_id": TOKEN_VALUE},
    {"state": "unknown", "code": "telegram_invalid_response", "retryable": True},
    {"state": "unknown", "code": "telegram_invalid_response", "retryable": 0},
    {"state": "failed", "code": "telegram_rate_limited", "retryable": True},
    {"state": "failed", "code": "telegram_rate_limited", "retryable": True, "retry_after": True},
    {"state": "failed", "code": "telegram_rate_limited", "retryable": True, "retry_after": 86401},
    {"state": "failed", "code": "telegram_connect_failed", "retryable": False},
    {"state": "failed", "code": "telegram_connect_failed", "retryable": True, "retry_after": 1},
    {"state": "failed", "code": "telegram_forbidden", "retryable": True},
])
def test_outcome_rejects_arbitrary_codes_and_inconsistent_states(arguments):
    with pytest.raises(ValueError, match="^Invalid Telegram outcome\\.$") as error:
        tg.TelegramOutcome(**arguments)
    assert TOKEN_VALUE not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    TOKEN_VALUE, None, 42, SecretStr(123), SecretStr(None), SecretStr(""),
    SecretStr("0:" + "a" * 20),
    SecretStr("01:" + "a" * 20), SecretStr("1:" + "a" * 19), SecretStr("1:" + "a" * 129),
    SecretStr("1" * 21 + ":" + "a" * 20), SecretStr("1:" + "a" * 20 + "/getMe"),
    SecretStr("1:" + "a" * 20 + "?x=1"), SecretStr("1:" + "a" * 20 + "#x"),
    SecretStr("1:" + "a" * 20 + "%2F"), SecretStr("1:" + "a" * 20 + "\\x"),
    SecretStr("1:" + "a" * 20 + "\r\nHost: attacker"), SecretStr(" 1:" + "a" * 20),
    SecretStr("1:" + "a" * 20 + "\n"), SecretStr("１:" + "a" * 20),
    SecretStr("1:" + "é" * 20), SecretStr("1:" + "a" * 20 + "\x00"),
])
async def test_invalid_tokens_never_connect(value):
    calls = []

    async def connect(_context):
        calls.append(True)
        raise AssertionError("Unexpected network use")

    outcome = await tg.TelegramTransport(_test_connector=connect).send(value, CHAT_ID, TEXT)
    assert outcome == tg.TelegramOutcome("failed", "telegram_invalid_token")
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "", None, 123, "0", "-0", "+123", "0123", "-0123", "123.0", " 123", "123\n",
    "１２３", "@name", "123/anything", str(2**52), str(-(2**52)), "9" * 200,
])
async def test_invalid_chat_ids_never_connect(value):
    calls = []

    async def connect(_context):
        calls.append(True)
        raise AssertionError("Unexpected network use")

    outcome = await tg.TelegramTransport(_test_connector=connect).send(TOKEN, value, TEXT)
    assert outcome.code == "telegram_invalid_chat_id"
    assert outcome.state == "failed" and not outcome.retryable
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "", None, 123, "a" * 4097, "😀" * 2049, "bad\ud800", "bad\r", "bad\x00", "bad\x7f",
])
async def test_invalid_text_never_connect(value):
    calls = []

    async def connect(_context):
        calls.append(True)
        raise AssertionError("Unexpected network use")

    outcome = await tg.TelegramTransport(_test_connector=connect).send(TOKEN, CHAT_ID, value)
    assert outcome.code == "telegram_invalid_text"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("framing", ["length", "chunked", "close"])
async def test_real_http_receipt_and_exact_plaintext_request(framing):
    async with _peer(_http(framing=framing)) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("accepted", "telegram_accepted", message_id=12345)
        assert peer.connected == 1
        assert len(peer.requests) == 1
        header, body = peer.requests[0]
        assert header.startswith(f"POST /bot{TOKEN_VALUE}/sendMessage HTTP/1.1\r\n".encode())
        assert b"Host: api.telegram.org\r\n" in header
        assert b"Accept-Encoding: identity\r\n" in header
        assert b"Connection: close\r\n" in header
        assert b"Authorization" not in header and b"?" not in header
        assert json.loads(body) == {
            "chat_id": CHAT_ID,
            "text": TEXT,
            "link_preview_options": {"is_disabled": True},
            "allow_paid_broadcast": False,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("token,chat_id,text", [
    (SecretStr("1:" + "a" * 20), "1", "a"),
    (SecretStr("9" * 20 + ":" + "A_-" * 42 + "aa"), str(2**52 - 1), "测" * 4096),
    (TOKEN, str(-(2**52 - 1)), "😀" * 2048),
])
async def test_valid_boundary_inputs_receive_matching_receipts(token, chat_id, text):
    async with _peer(_http(_body(_receipt(chat_id=chat_id, text=text)))) as peer:
        assert (await peer.transport().send(token, chat_id, text)).state == "accepted"
        assert len(peer.requests) == 1


@pytest.mark.asyncio
async def test_real_tls_verifies_fixture_ca_and_fixed_sni(tls_files):
    build, _ca_path = tls_files
    server, client = build()
    names = []
    server.set_servername_callback(lambda _socket, name, _context: names.append(name))
    async with _peer(server_context=server) as peer:
        result = await peer.transport(context=client).send(TOKEN, CHAT_ID, TEXT)
        assert result.state == "accepted"
        assert names == ["api.telegram.org"]
        assert len(peer.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code,state", [
    (_http(_body({"ok": False, "description": TOKEN_VALUE})),
     "telegram_invalid_response", "unknown"),
    (_http(b"{}", status=307, headers=[(b"Location", b"http://127.0.0.1:1/steal")]),
     "telegram_redirect_blocked", "unknown"),
    (_http(_body({"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}), status=429),
     "telegram_rate_limited", "failed"),
    (_http(b"x" * (tg.MAX_RESPONSE_BYTES + 1), framing="close"),
     "telegram_response_too_large", "unknown"),
    (b"HTTP/1.1 200 Fixture\r\nContent-Length: 1000\r\n\r\npartial",
     "telegram_connection_lost", "unknown"),
])
async def test_real_tls_error_responses_keep_the_same_conservative_classification(
    tls_files, response, code, state
):
    build, _ = tls_files
    server, client = build()
    async with _peer(response, server_context=server) as peer:
        result = await peer.transport(context=client).send(TOKEN, CHAT_ID, TEXT)
        assert result.code == code and result.state == state
        assert result.retryable is (code == "telegram_rate_limited")
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_tls_timeout_or_cancel_aborts_without_unbounded_tls_shutdown(tls_files, cancel):
    build, _ = tls_files
    server, client = build()

    async def hold(reader, _writer):
        await reader.read()

    async with _peer(server_context=server, responder=hold) as peer:
        started = time.monotonic()
        task = asyncio.create_task(peer.transport(context=client, timeout=0.15).send(
            TOKEN, CHAT_ID, TEXT
        ))
        await asyncio.wait_for(peer.request_received.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result == tg.TelegramOutcome("unknown", "telegram_response_timeout")
        await asyncio.wait_for(peer.connection_closed.wait(), 1)
        assert time.monotonic() - started < 1
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["untrusted", "hostname", "expired"])
async def test_real_tls_failure_is_pre_request_and_fails_closed(tls_files, mode):
    build, _ca_path = tls_files
    server, client = build(
        hostname="wrong.fixture.invalid" if mode == "hostname" else "api.telegram.org",
        expired=mode == "expired",
    )
    async with _peer(server_context=server) as peer:
        result = await peer.transport(context=None if mode == "untrusted" else client).send(
            TOKEN, CHAT_ID, TEXT
        )
        assert result == tg.TelegramOutcome("failed", "telegram_tls_failed")
        assert peer.connected == 1 and peer.requests == []


@pytest.mark.asyncio
async def test_unavailable_system_trust_fails_closed_without_leaking_errors(monkeypatch, caplog):
    def unavailable():
        raise OSError(TOKEN_VALUE)

    calls = []

    async def connect(_context):
        calls.append(True)
        raise AssertionError("Unexpected network use")

    monkeypatch.setattr(tg, "_tls_context", unavailable)
    result = await tg.TelegramTransport(_test_connector=connect).send(TOKEN, CHAT_ID, TEXT)
    assert result == tg.TelegramOutcome("failed", "telegram_tls_failed")
    assert calls == [] and TOKEN_VALUE not in caplog.text + repr(result)


@pytest.mark.asyncio
async def test_proxy_ca_endpoint_and_keylog_environment_cannot_redirect_or_trust(
    tls_files, monkeypatch
):
    build, ca_path = tls_files
    server, _client = build()
    keylog = ca_path.parent / "forbidden-keylog.txt"
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    monkeypatch.setenv("SSL_CERT_DIR", str(ca_path.parent))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_path))
    monkeypatch.setenv("CURL_CA_BUNDLE", str(ca_path))
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("TELEGRAM_API_BASE", "http://127.0.0.1:1")
    async with _peer(server_context=server) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result.code == "telegram_tls_failed" and peer.requests == []
    assert not keylog.exists()
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        raise ConnectionRefusedError(TOKEN_VALUE)

    monkeypatch.setattr(tg.asyncio, "open_connection", capture)
    outcome = await tg.TelegramTransport().send(TOKEN, CHAT_ID, TEXT)
    assert outcome.code == "telegram_connect_failed" and outcome.retryable
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("api.telegram.org", 443)
    assert kwargs["server_hostname"] == "api.telegram.org"
    assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["ssl"].check_hostname is True
    assert kwargs["ssl"].keylog_filename is None
    assert kwargs["ssl"].minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize("timeout", [0, -1, 20.01, float("inf"), float("nan"), True, "20"])
def test_constructor_cannot_extend_deadline(timeout):
    with pytest.raises(ValueError, match="Invalid Telegram test transport configuration"):
        tg.TelegramTransport(_test_timeout=timeout)


def test_constructor_has_no_production_endpoint_proxy_or_tls_bypass(tls_files):
    for name in ("endpoint", "base_url", "proxy", "verify", "ca_file", "timeout"):
        with pytest.raises(TypeError):
            tg.TelegramTransport(**{name: "forbidden"})
    build, _ = tls_files
    _server, client = build()
    with pytest.raises(ValueError):
        tg.TelegramTransport(_test_ssl_context=client)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="Invalid Telegram test TLS configuration"):
        tg.TelegramTransport(_test_connector=lambda _context: None, _test_ssl_context=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [
    (("ok",), False), (("ok",), 1), (("ok",), "true"), (("result",), None),
    (("result", "message_id"), 0), (("result", "message_id"), -1),
    (("result", "message_id"), True), (("result", "message_id"), "12345"),
    (("result", "message_id"), 2**63), (("result", "date"), 0),
    (("result", "date"), True), (("result", "date"), "1788192000"),
    (("result", "chat"), None), (("result", "chat", "id"), 123),
    (("result", "chat", "id"), CHAT_ID), (("result", "chat", "id"), True),
    (("result", "chat", "type"), "attacker"), (("result", "chat", "type"), None),
    (("result", "chat", "type"), ["private"]),
    (("result", "text"), "different notification"), (("result", "text"), None),
    (("error_code",), 429),
])
async def test_http_200_requires_strict_matching_message_receipt(path, value):
    payload = _receipt()
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    async with _peer(_http(_body(payload))) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_invalid_response")
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b"", b"not json", b"[]", b"null", b"true", b'{"ok":true}',
    b'{"ok":false,"error_code":429,"parameters":{"retry_after":1}}',
    b'{"ok":true,"ok":false}', b'{"ok":false,"ok":true}',
    b'{"ok":true,"result":{"chat":{"id":1,"id":2}}}',
    b'{"ok":true,"extra":NaN}', b'{"ok":true,"extra":Infinity}',
    b'{"ok":true,"extra":-Infinity}', b'{"ok":true}\xff',
    b"[" * 1100 + b"0" + b"]" * 1100,
    b'{"ok":true,"number":' + b"1" * 5000 + b"}",
])
async def test_malformed_or_duplicate_json_is_unknown_without_retry(body):
    async with _peer(_http(body)) as peer:
        outcome = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert outcome == tg.TelegramOutcome("unknown", "telegram_invalid_response")
        assert peer.connected == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [
    (400, "telegram_bad_request"), (401, "telegram_unauthorized"),
    (403, "telegram_forbidden"), (404, "telegram_rejected"), (408, "telegram_rejected"),
    (409, "telegram_rejected"), (418, "telegram_rejected"),
])
async def test_definite_http_rejections_ignore_untrusted_description(status, code, caplog):
    payload = {"ok": False, "description": TOKEN_VALUE + TEXT, "error_code": status}
    async with _peer(_http(_body(payload), status=status)) as peer:
        outcome = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert outcome == tg.TelegramOutcome("failed", code)
        assert peer.connected == 1
        assert TOKEN_VALUE not in repr(outcome) + caplog.text
        assert TEXT not in repr(outcome) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 500, 502, 503, 504])
async def test_redirects_and_ambiguous_server_errors_never_retry_or_follow(status):
    async with _peer() as forbidden:
        location = f"http://127.0.0.1:{forbidden.port}/steal".encode()
        async with _peer(_http(b"{}", status=status, headers=[(b"Location", location)])) as peer:
            result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
            code = "telegram_redirect_blocked" if status < 400 else "telegram_server_error"
            assert result == tg.TelegramOutcome("unknown", code)
            assert peer.connected == 1 and len(peer.requests) == 1
            assert forbidden.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", [1, 3, 86400])
async def test_only_valid_bounded_429_can_schedule_a_retry(retry_after):
    payload = {
        "ok": False,
        "error_code": 429,
        "description": TOKEN_VALUE,
        "parameters": {"retry_after": retry_after},
    }
    async with _peer(_http(_body(payload), status=429)) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome(
            "failed", "telegram_rate_limited", retry_after=retry_after, retryable=True
        )
        # Returning a safe delay is not permission for the transport to retry.
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"ok": False, "error_code": 429},
    {"ok": True, "error_code": 429, "parameters": {"retry_after": 1}},
    {"ok": False, "error_code": "429", "parameters": {"retry_after": 1}},
    {"ok": False, "error_code": 400, "parameters": {"retry_after": 1}},
    {"ok": False, "error_code": 429, "parameters": None},
    *[
        {"ok": False, "error_code": 429, "parameters": {"retry_after": value}}
        for value in (0, -1, 86401, True, 1.5, "1", None)
    ],
    {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}, "result": {}},
])
async def test_invalid_429_is_unknown_not_a_shortened_retry(payload):
    async with _peer(_http(_body(payload), status=429, headers=[(b"Retry-After", b"1")])) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_invalid_response")
        assert peer.connected == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    b"HTTP/2 200 Fixture\r\n\r\n{}",
    b"HTTP/1.1 100 Continue\r\n\r\n" + _http(),
    b"HTTP/1.1 200 Fixture\r\nFold: good\r\n bad\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: 2\r\ncontent-length: 2\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: -1\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: +2\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: 1.0\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nContent-Length: 1 2\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nX-Test: hidden\nbare\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nX-Test: bad\x00value\r\n\r\n{}",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: gzip, chunked\r\n\r\n0\r\n\r\n",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}xx",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n2;x=y\r\n{}\r\n0\r\n\r\n",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nContent-Length: 0\r\n\r\n",
    _http(headers=[(b"Content-Encoding", b"gzip")]),
    _http().replace(b"application/json", b"text/html"),
    _http() + b"hidden trailing bytes",
    _http(status=201),
])
async def test_ambiguous_http_framing_cannot_create_an_accepted_receipt(response):
    async with _peer(response) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_invalid_response")
        assert peer.connected == 1


def _response_of_size(size):
    base = _body(_receipt())
    body_size = size
    for _ in range(5):
        body = base + b" " * (body_size - len(base))
        body_size += size - len(_http(body))
    response = _http(base + b" " * (body_size - len(base)))
    assert len(response) == size
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("excess", [0, 1])
async def test_wire_limit_includes_headers_and_accepts_only_exact_bound(excess):
    async with _peer(_response_of_size(tg.MAX_RESPONSE_BYTES + excess)) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        if excess:
            assert result == tg.TelegramOutcome("unknown", "telegram_response_too_large")
        else:
            assert result.state == "accepted"
        assert peer.connected == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    _http(b"x" * (tg.MAX_RESPONSE_BYTES + 1), framing="close"),
    b"HTTP/1.1 200 Fixture\r\nContent-Length: 999999999\r\n\r\n",
    b"HTTP/1.1 200 Fixture\r\nX-Long: " + b"a" * 5000 + b"\r\n\r\n",
    b"HTTP/1.1 200 Fixture\r\n" + b"".join(
        f"X-{index}: ".encode() + b"a" * 1000 + b"\r\n" for index in range(20)
    ) + b"\r\n",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\nfffffff\r\n",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n"
    + b"1\r\n \r\n" * 11000 + b"0\r\n\r\n",
])
async def test_oversize_headers_declared_length_body_and_chunk_overhead_are_bounded(response):
    async with _peer(response) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_response_too_large")
        assert peer.connected == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    b"", b"HTTP/1.1 200", b"HTTP/1.1 200 Fixture\r\nContent-Length: 1000\r\n\r\npartial",
    b"HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n9\r\nshort",
])
async def test_disconnect_after_request_is_unknown_even_if_peer_accepted_it(response):
    async with _peer(response) as peer:
        result = await peer.transport().send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_connection_lost")
        assert len(peer.requests) == 1 and peer.connected == 1


@pytest.mark.asyncio
async def test_slow_response_has_a_total_deadline_and_no_background_retry():
    async def delayed(reader, _writer):
        await reader.read()

    async with _peer(responder=delayed) as peer:
        started = time.monotonic()
        result = await peer.transport(timeout=0.1).send(TOKEN, CHAT_ID, TEXT)
        assert time.monotonic() - started < 1
        assert result == tg.TelegramOutcome("unknown", "telegram_response_timeout")
        await asyncio.wait_for(peer.connection_closed.wait(), 1)
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
async def test_total_deadline_is_not_reset_after_connect_or_each_read():
    async def slow_response(_reader, writer):
        for byte in _http():
            writer.write(bytes([byte]))
            await writer.drain()
            await asyncio.sleep(0.015)

    async with _peer(responder=slow_response) as peer:
        async def slow_connect(context):
            await asyncio.sleep(0.06)
            return await peer.connect(context)

        transport = tg.TelegramTransport(_test_connector=slow_connect, _test_timeout=0.12)
        started = time.monotonic()
        result = await transport.send(TOKEN, CHAT_ID, TEXT)
        assert time.monotonic() - started < 0.6
        assert result == tg.TelegramOutcome("unknown", "telegram_response_timeout")
        assert peer.connected == 1 and len(peer.requests) == 1


@pytest.mark.asyncio
async def test_complete_content_length_with_missing_close_does_not_hide_response_bytes():
    async def never_close(reader, writer):
        writer.write(_http())
        await writer.drain()
        await reader.read()

    async with _peer(responder=never_close) as peer:
        result = await peer.transport(timeout=0.1).send(TOKEN, CHAT_ID, TEXT)
        assert result == tg.TelegramOutcome("unknown", "telegram_response_timeout")


@pytest.mark.asyncio
async def test_pre_send_connect_timeout_is_retryable_and_cancelled_once():
    calls = []
    cancelled = asyncio.Event()

    async def stalled(_context):
        calls.append(True)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    result = await tg.TelegramTransport(_test_connector=stalled, _test_timeout=0.05).send(
        TOKEN, CHAT_ID, TEXT
    )
    assert result == tg.TelegramOutcome("failed", "telegram_connect_timeout", retryable=True)
    assert calls == [True] and cancelled.is_set()


@pytest.mark.asyncio
async def test_real_connection_refusal_is_retryable_without_internal_retry():
    async with _peer() as peer:
        port = peer.port
    attempts = []

    async def refused(context):
        attempts.append(True)
        return await asyncio.open_connection(
            "127.0.0.1", port, ssl=context, server_hostname="api.telegram.org"
        )

    result = await tg.TelegramTransport(_test_connector=refused).send(TOKEN, CHAT_ID, TEXT)
    assert result == tg.TelegramOutcome("failed", "telegram_connect_failed", retryable=True)
    assert attempts == [True]


class _Writer:
    def __init__(self, *, write_error=None, stalled=False):
        self.transport = self
        self.write_error = write_error
        self.stalled = stalled
        self.writes = []
        self.draining = asyncio.Event()
        self.aborted = False
        self.closed = False

    def write(self, data):
        self.writes.append(data[:8])
        if self.write_error is not None:
            raise self.write_error

    async def drain(self):
        self.draining.set()
        if self.stalled:
            await asyncio.Event().wait()

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_partial_write_failure_is_unknown_and_connection_is_aborted():
    writer = _Writer(write_error=OSError(TOKEN_VALUE))
    calls = []

    async def connect(_context):
        calls.append(True)
        return asyncio.StreamReader(), writer

    result = await tg.TelegramTransport(_test_connector=connect).send(TOKEN, CHAT_ID, TEXT)
    assert result == tg.TelegramOutcome("unknown", "telegram_connection_lost")
    assert calls == [True] and len(writer.writes) == 1
    assert writer.aborted and writer.closed


@pytest.mark.asyncio
async def test_write_deadline_cannot_be_misclassified_as_unsent():
    writer = _Writer(stalled=True)

    async def connect(_context):
        return asyncio.StreamReader(), writer

    result = await tg.TelegramTransport(_test_connector=connect, _test_timeout=0.05).send(
        TOKEN, CHAT_ID, TEXT
    )
    assert result == tg.TelegramOutcome("unknown", "telegram_send_timeout")
    assert writer.aborted and writer.closed and len(writer.writes) == 1


@pytest.mark.asyncio
async def test_external_cancellation_during_read_propagates_and_closes_real_connection():
    async def hold(reader, _writer):
        await reader.read()

    async with _peer(responder=hold) as peer:
        task = asyncio.create_task(peer.transport().send(TOKEN, CHAT_ID, TEXT))
        await asyncio.wait_for(peer.request_received.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(peer.connection_closed.wait(), 1)
        assert len(peer.requests) == 1 and peer.connected == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "send"])
async def test_external_cancellation_is_not_swallowed_or_retried(phase):
    writer = _Writer(stalled=True)
    started = asyncio.Event()
    calls = []

    async def connect(_context):
        calls.append(True)
        if phase == "connect":
            started.set()
            await asyncio.Event().wait()
        return asyncio.StreamReader(), writer

    task = asyncio.create_task(
        tg.TelegramTransport(_test_connector=connect).send(TOKEN, CHAT_ID, TEXT)
    )
    await asyncio.wait_for((started if phase == "connect" else writer.draining).wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [True]
    assert writer.writes == [] if phase == "connect" else len(writer.writes) == 1
    if phase == "send":
        assert writer.aborted and writer.closed


@pytest.mark.asyncio
async def test_debug_logging_keeps_unrelated_logs_but_never_emits_token_body_or_raw_error(
    caplog, tls_files
):
    for name in ("asyncio", "httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    loggers = [logging.getLogger(name) for name in ("asyncio", "httpx", "httpcore", "")]
    snapshot = [(logger.level, tuple(logger.filters), logger.disabled) for logger in loggers]
    loop = asyncio.get_running_loop()
    previous_debug = loop.get_debug()
    loop.set_debug(True)
    try:
        async with _peer() as peer:
            assert (await peer.transport().send(TOKEN, CHAT_ID, TEXT)).state == "accepted"
        build, _ = tls_files
        server, client = build()
        async with _peer(server_context=server) as peer:
            result = await peer.transport(context=client).send(TOKEN, CHAT_ID, TEXT)
            assert result.state == "accepted"
        response = _http(_body({"ok": False, "description": TOKEN_VALUE + TEXT}))
        async with _peer(response, server_context=server) as peer:
            result = await peer.transport(context=client).send(TOKEN, CHAT_ID, TEXT)
            assert result.code == "telegram_invalid_response"
        for error, expected in [
            (OSError(TOKEN_VALUE + TEXT), "telegram_connect_failed"),
            (RuntimeError(TOKEN_VALUE + TEXT), "telegram_transport_failure"),
        ]:
            async def fail(_context, exception=error):
                raise exception

            result = await tg.TelegramTransport(_test_connector=fail).send(TOKEN, CHAT_ID, TEXT)
            assert result.code == expected
            assert TOKEN_VALUE not in repr(result)
        for logger in loggers:
            logger.warning("Unrelated fixture log remains visible: %s", logger.name)
    finally:
        loop.set_debug(previous_debug)
    actual = [(logger.level, tuple(logger.filters), logger.disabled) for logger in loggers]
    assert actual == snapshot
    for name in ("asyncio", "httpx", "httpcore", "root"):
        assert f"Unrelated fixture log remains visible: {name}" in caplog.text
    assert TOKEN_VALUE not in caplog.text
    assert TEXT not in caplog.text
    assert "/sendMessage" not in caplog.text
