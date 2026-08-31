"""Run on the isolated VPS; no fixture disables product SSRF or TLS checks."""

import gzip
import io
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from open_node.services import external_fetch as fetch

SECRET = "fixture-source-query-secret-not-for-logs"
URL = f"https://subscription.example/feed?token={SECRET}&signed=a%2Fb%3D"
PUBLIC_IP = "8.8.8.8"


@pytest.mark.parametrize("value", [
    "http://provider.example/a", "file:///etc/passwd", "https:///missing", "//provider.example/a",
    "https://user:password@provider.example/a", "https://provider.example/a#secret",
    "https://provider.example/a#", "https://provider.example/a\r\nX:secret",
    " https://provider.example/a", "https://provider.example/a\tsecret",
    "https://provider.example/a\x00", "https://provider.example/a\x7f",
    "https://provider.example/a\u200b", "https://provider.example/a b",
    "https://provider.example/a%0d%0aX:secret", "https://provider.example/a%00",
    "https://provider.example/a%7f", "https://provider.example/%", "https://provider.example/%x0",
    "https://provider.example\\@127.0.0.1/a", "https://provider.example:",
    "https://provider.example:0/a", "https://provider.example:65536/a",
    "https://provider.example:+443/a", "https://provider.example:443:443/a",
    "https://provider.example:４４３/a", "https://bad_host.example/a",
    "https://-bad.example/a", "https://bad-.example/a", "https://bad..example/a",
    "https://provider.example../a", "https://%31%32%37.0.0.1/a", "https://localhost/a",
    "https://name.local/a", "https://127.1/a", "https://2130706433/a",
    "https://0x7f000001/a", "https://0177.0.0.1/a", "https://[::1%25lo]/a",
    "https://[v1.example]/a", "https://[2001:4860::8888]extra/a",
    "https://" + "a" * 64 + ".example/a", "https://provider.example/" + "x" * 8192,
    "", None, 7,
])
def test_url_rejects_ambiguous_syntax_without_echoing(value):
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.normalize_external_url(value)
    assert error.value.code in {"invalid_url", "unsafe_target"}
    assert "password" not in str(error.value)
    assert "provider.example" not in str(error.value)


def test_url_normalization_preserves_signed_query_and_limits():
    assert fetch.normalize_external_url(URL) == URL
    assert fetch.normalize_external_url("HTTPS://PROVIDER.EXAMPLE:00443/a?x=%2f&x=&token=a+b") == (
        "https://provider.example/a?x=%2f&x=&token=a+b"
    )
    assert fetch.normalize_external_url("https://provider.example.") == "https://provider.example/"
    assert fetch.normalize_external_url("https://provider.example:8443?") == (
        "https://provider.example:8443/?"
    )
    assert fetch.normalize_external_url("https://例子.测试/订阅?token=%2f") == (
        "https://xn--fsqu00a.xn--0zwm56d/%E8%AE%A2%E9%98%85?token=%2f"
    )
    assert fetch.normalize_external_url("https://[2606:4700:4700:0:0:0:0:1111]:8443/") == (
        "https://[2606:4700:4700::1111]:8443/"
    )
    prefix = "https://provider.example/"
    maximum = prefix + "a" * (fetch.MAX_URL_BYTES - len(prefix))
    assert fetch.normalize_external_url(maximum) == maximum
    with pytest.raises(fetch.ExternalFetchError):
        fetch.normalize_external_url(maximum + "a")


@pytest.mark.parametrize("value", [
    "0.0.0.0", "0.1.2.3", "10.1.2.3", "100.64.0.1", "100.100.100.200", "127.0.0.1",
    "169.254.169.254", "169.254.170.2", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "192.0.0.9", "192.0.2.1", "192.88.99.1", "198.18.0.1", "198.19.255.255",
    "198.51.100.1", "203.0.113.1", "224.0.0.1", "239.255.255.255", "240.0.0.1",
    "255.255.255.255", "168.63.129.16", "::", "::1", "fe80::1", "fec0::1", "fc00::1",
    "fd00:ec2::254", "ff02::1", "::ffff:127.0.0.1", "::ffff:8.8.8.8", "::192.168.1.1",
    "64:ff9b::a9fe:a9fe", "64:ff9b::808:808", "64:ff9b:1::7f00:1",
    "2002:7f00:1::", "2002:a00:1::", "2002:808:808::",
    "2001:0000:4136:e378:8000:63bf:3fff:fdd2", "2001:2::1", "2001:10::1", "2001:20::1",
    "2001:db8::1", "2001:4860::5efe:7f00:1", "2001:4860::200:5efe:a00:1",
    "3ffe::1", "3fff::1", "4000::1", "2001:4860::1%2", "not-an-address",
])
def test_all_nonpublic_and_ipv6_transition_forms_are_blocked(value):
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch._public_ip(value)
    assert error.value.code == "unsafe_target"
    assert value not in str(error.value)


@pytest.mark.parametrize("value", [
    "1.1.1.1", "8.8.8.8", "185.99.135.224", "2001:4860:4860::8888", "2606:4700:4700::1111",
])
def test_public_addresses_remain_available(value):
    assert str(fetch._public_ip(value)) == str(ipaddress.ip_address(value))


def answer(address, port=443):
    if ":" in address:
        return socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0)
    return socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port)


@pytest.mark.parametrize("addresses", [
    [PUBLIC_IP, "127.0.0.1"], ["127.0.0.1", PUBLIC_IP], [PUBLIC_IP, "::1"],
    ["2606:4700::1111", "169.254.169.254"], [PUBLIC_IP, "::ffff:169.254.169.254"],
    [PUBLIC_IP, "2002:7f00:1::1"], [PUBLIC_IP, "64:ff9b::a00:1"],
])
def test_every_dns_result_is_validated_before_any_connection(monkeypatch, addresses):
    calls = []
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args: [answer(ip) for ip in addresses])
    monkeypatch.setattr(fetch.socket, "socket", lambda *args: calls.append(args))
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch._connect_tls("subscription.example", 443, time.monotonic() + 1)
    assert error.value.code == "unsafe_target"
    assert calls == []


@pytest.mark.parametrize("answers", [
    [], [answer(PUBLIC_IP)] * 65,
    [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700::1111", 443, 0, 2))],
    [(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 443))],
    [answer(PUBLIC_IP, port=444)],
    [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 443, 0, 0))],
])
def test_dns_shape_and_address_count_bounds(monkeypatch, answers):
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args: answers)
    with pytest.raises(fetch.ExternalFetchError):
        fetch._resolve_public("subscription.example", 443)


def test_dns_rebinding_is_prevented_and_tls_keeps_original_hostname(monkeypatch):
    resolved, connected, names, alpn = [], [], [], []

    def resolve(*args):
        resolved.append(args)
        return [answer(PUBLIC_IP)] if len(resolved) == 1 else [answer("127.0.0.1")]

    class RawSocket:
        closed = False

        def settimeout(self, value):
            assert 0 < value <= 1

        def connect(self, endpoint):
            connected.append(endpoint)

        def close(self):
            self.closed = True

    class Context:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def set_alpn_protocols(self, protocols):
            alpn.extend(protocols)

        def wrap_socket(self, raw, *, server_hostname):
            assert self.check_hostname and self.verify_mode == ssl.CERT_REQUIRED
            names.append(server_hostname)
            return raw

    monkeypatch.setattr(fetch.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(fetch.socket, "socket", lambda *args: RawSocket())
    monkeypatch.setattr(fetch.ssl, "create_default_context", Context)
    raw = fetch._connect_tls("subscription.example", 443, time.monotonic() + 1)
    raw.close()
    assert resolved == [("subscription.example", 443, socket.AF_UNSPEC,
                         socket.SOCK_STREAM, socket.IPPROTO_TCP)]
    assert connected == [(PUBLIC_IP, 443)]
    assert names == ["subscription.example"] and alpn == ["http/1.1"] and raw.closed


class MemoryTLS:
    def __init__(self, response, fragment=4096):
        self.response = io.BytesIO(response)
        self.fragment = fragment
        self.request = b""
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def recv(self, size):
        return self.response.read(min(size, self.fragment))

    def sendall(self, request):
        self.request += request

    def close(self):
        self.closed = True


def response(body=b"proxies: []\n", headers=(), *, status=b"200 OK", length=True):
    fields = list(headers)
    if length:
        fields.append(b"Content-Length: " + str(len(body)).encode())
    return b"HTTP/1.1 " + status + b"\r\n" + b"\r\n".join(fields) + b"\r\n\r\n" + body


def run_response(monkeypatch, payload, *, maximum=fetch.MAX_BODY_BYTES, fragment=4096):
    connection = MemoryTLS(payload, fragment=fragment)
    monkeypatch.setattr(fetch, "_connect_tls", lambda *args: connection)
    try:
        result = fetch._fetch_in_worker(URL, "clash-meta/2.4.0", time.monotonic() + 2, maximum)
    finally:
        assert connection.closed
    return result, connection


@pytest.mark.parametrize("fragment", [1, 7, 4096, 16384])
def test_http_query_user_agent_and_metadata_without_secret_headers(monkeypatch, fragment):
    body = b"proxies: []\n"
    raw = response(body, [
        b"Subscription-Userinfo: upload=0; download=17; total=100; expire=1700000000",
        b"Set-Cookie: provider-secret=hidden", b"Set-Cookie: other=hidden",
    ])
    result, connection = run_response(monkeypatch, raw, fragment=fragment)
    assert result.body == body
    assert result.metadata == {"upload": 0, "download": 17, "total": 100, "expire": 1700000000}
    assert connection.request.startswith(
        f"GET /feed?token={SECRET}&signed=a%2Fb%3D HTTP/1.1\r\n".encode()
    )
    assert b"Host: subscription.example\r\n" in connection.request
    assert b"User-Agent: clash-meta/2.4.0\r\n" in connection.request
    assert b"Connection: close\r\n" in connection.request
    assert not any(name in connection.request.lower() for name in (
        b"cookie:", b"authorization:", b"referer:", b"proxy-authorization:",
    ))
    assert "proxies" not in repr(result) and SECRET not in repr(result)


@pytest.mark.parametrize("raw,expected", [
    ("upload=0; download=1; total=2; expire=0",
     {"upload": 0, "download": 1, "total": 2, "expire": 0}),
    ("upload=-1; download=1.5; total=1e9; expire=NaN; url=" + SECRET, {}),
    ("upload=+1; download=１２; total=9223372036854775808; expire=" + "1" * 1000, {}),
    ("upload=9223372036854775807; download=00012", {"upload": 9223372036854775807, "download": 12}),
    ("upload=1; upload=2; upload=3; download=9; download=bad", {}),
    (" Upload = 3 ; ExPiRe=7; password=private", {"upload": 3, "expire": 7}),
])
def test_metadata_has_only_unambiguous_nonnegative_integer_fields(raw, expected):
    assert fetch._parse_metadata(raw) == expected


@pytest.mark.parametrize("raw", [
    b"HTTP/1.1 200 OK\nContent-Length: 0\n\n",
    b"HTTP/2 200 OK\r\n\r\n", b"HTTP/1.1 200 OK\r\n folded: x\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nBad Name: x\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nX-Test: a\x00b\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\ncontent-length: 1\r\n\r\na",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1, 1\r\n\r\na",
    b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: +1\r\n\r\na",
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\na",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nab",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nSubscription-Userinfo: upload=1\r\n"
    b"subscription-userinfo: upload=2\r\n\r\n",
])
def test_http_rejects_ambiguous_or_truncated_messages(monkeypatch, raw):
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, raw)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("status,code", [(b"302 Found", "redirect"), (b"307 Temporary", "redirect"),
                                       (b"404 Not Found", "http_status"),
                                       (b"100 Continue", "http_status")])
def test_http_status_and_redirect_never_follow_location(monkeypatch, status, code):
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, response(
            headers=[b"Location: https://127.0.0.1/" + SECRET.encode()], status=status,
        ))
    assert error.value.code == code and SECRET not in str(error.value)


@pytest.mark.parametrize("raw", [
    b"HTTP/1.1 200 OK\r\nX-Long: " + b"a" * 8192 + b"\r\n\r\n",
    b"HTTP/1.1 200 OK\r\n" + (b"X-Long: " + b"a" * 4000 + b"\r\n") * 9 + b"\r\n",
    b"HTTP/1.1 200 OK\r\n" + b"X-Count: a\r\n" * 101 + b"\r\n",
])
def test_headers_are_bounded_by_line_total_and_count(monkeypatch, raw):
    with pytest.raises(fetch.ExternalFetchError):
        run_response(monkeypatch, raw)


@pytest.mark.parametrize("framing", ["length", "close", "chunked"])
def test_actual_body_limits_apply_to_all_transfer_framings(monkeypatch, framing):
    body = b"a" * 1025
    fields = []
    if framing == "chunked":
        fields = [b"Transfer-Encoding: chunked"]
        body = b"401\r\n" + body + b"\r\n0\r\n\r\n"
    raw = response(body, fields, length=framing == "length")
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, raw, maximum=1024)
    assert error.value.code == "response_too_large"


def test_chunked_body_and_exact_limit_are_supported(monkeypatch):
    raw = response(
        b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n", [b"Transfer-Encoding: chunked"], length=False,
    )
    result, _ = run_response(monkeypatch, raw, maximum=5, fragment=1)
    assert result.body == b"abcde"


@pytest.mark.parametrize("body", [
    b"1;extension=secret\r\na\r\n0\r\n\r\n", b"+1\r\na\r\n0\r\n\r\n",
    b"z\r\na\r\n0\r\n\r\n", b"1\na\r\n0\r\n\r\n", b"2\r\na",
    b"1\r\naXX0\r\n\r\n", b"0\r\nSubscription-Userinfo: upload=10\r\n\r\n",
    b"0\r\n\r\nsecret", b"000000000\r\n\r\n",
])
def test_malformed_chunking_is_not_tolerated(monkeypatch, body):
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, response(body, [b"Transfer-Encoding: chunked"], length=False))
    assert error.value.code == "invalid_response"


def test_chunk_count_and_wire_framing_overhead_are_bounded(monkeypatch):
    for framing in (b"1\r\na\r\n" * 8192, b"00000001\r\na\r\n" * 6000):
        with pytest.raises(fetch.ExternalFetchError) as error:
            run_response(monkeypatch, response(framing + b"0\r\n\r\n",
                                              [b"Transfer-Encoding: chunked"], length=False))
        assert error.value.code == "response_too_large"


@pytest.mark.parametrize("chunked", [False, True])
def test_gzip_limits_decompressed_bytes_not_only_content_length(monkeypatch, chunked):
    compressed = gzip.compress(b"a" * (4 * 1024 * 1024))
    fields = [b"Content-Encoding: gzip"]
    if chunked:
        fields.append(b"Transfer-Encoding: chunked")
        compressed = f"{len(compressed):x}\r\n".encode() + compressed + b"\r\n0\r\n\r\n"
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, response(compressed, fields, length=not chunked))
    assert error.value.code == "response_too_large"


def test_gzip_success_and_encoded_byte_bound(monkeypatch):
    body = b"a" * 1024
    compressed = gzip.compress(body)
    result, _ = run_response(monkeypatch, response(compressed, [b"Content-Encoding: gzip"]),
                             maximum=1024, fragment=1)
    assert result.body == body
    # Empty gzip is larger than a five-byte wire limit even though its output is empty.
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(
            monkeypatch, response(gzip.compress(b""), [b"Content-Encoding: gzip"]), maximum=5,
        )
    assert error.value.code == "response_too_large"


@pytest.mark.parametrize("body", [
    b"not-gzip-provider-secret", gzip.compress(b"abc")[:-3],
    gzip.compress(b"abc") + b"secret", gzip.compress(b"abc") + gzip.compress(b"def"),
])
def test_invalid_truncated_and_concatenated_gzip_are_rejected(monkeypatch, body):
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, response(body, [b"Content-Encoding: gzip"]))
    assert error.value.code == "invalid_response"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("field", [
    b"Content-Encoding: br", b"Content-Encoding: gzip, gzip", b"Transfer-Encoding: gzip, chunked",
    b"Transfer-Encoding: identity",
])
def test_unsupported_encodings_are_fixed_safe_errors(monkeypatch, field):
    with pytest.raises(fetch.ExternalFetchError) as error:
        run_response(monkeypatch, response(b"", [field], length=False))
    assert error.value.code == "unsupported_response"


@pytest.mark.parametrize("agent", [
    "a" * 257, "bad\r\nX:" + SECRET, "bad\x00", "bad\x7f", "中文", None,
])
def test_user_agent_is_bounded_printable_ascii(agent):
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.fetch_external_subscription(URL, user_agent=agent)
    assert error.value.code == "invalid_user_agent" and SECRET not in str(error.value)


@pytest.mark.parametrize("options", [
    {"timeout": 0}, {"timeout": -1}, {"timeout": 31}, {"timeout": float("inf")},
    {"timeout": float("nan")}, {"timeout": True}, {"timeout": "30"}, {"timeout": 10 ** 1000},
    {"max_bytes": 0}, {"max_bytes": -1}, {"max_bytes": True}, {"max_bytes": 2 * 1024 * 1024 + 1},
])
def test_callers_cannot_remove_process_time_or_body_limits(options):
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.fetch_external_subscription(URL, **options)
    assert error.value.code == "invalid_limits"


def launch_custom_child(monkeypatch, code, *, on_start=None):
    original = subprocess.Popen
    processes = []
    calls = []

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        child = original([command[0], "-I", "-S", "-c", code], **kwargs)
        processes.append(child)
        if on_start:
            on_start(len(processes))
        return child

    monkeypatch.setattr(fetch.subprocess, "Popen", launch)
    return processes, calls


def protocol(header, body=b""):
    return fetch._PROTOCOL_MAGIC + json.dumps(header, separators=(",", ":")).encode() + b"\n" + body


def worker_wrapper(patch=""):
    # Only fixture code/path is on argv. The URL and user agent still travel
    # exclusively over the production bounded stdin protocol.
    return (
        "import runpy,sys,socket,time\n"
        f"namespace=runpy.run_path({str(Path(fetch.__file__).resolve())!r})\n"
        "assert not any(k in sys.modules for k in "
        "('open_node.main','open_node.services.inventory','sqlalchemy'))\n"
        + patch + "\nraise SystemExit(namespace['_worker_main']())\n"
    )


def test_real_child_protocol_keeps_secrets_off_argv_environment_and_repr(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid/" + SECRET)
    monkeypatch.setenv("PYTHONPATH", "/secret/import-hook")
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "tls-secrets.log"))
    monkeypatch.setenv("APP_DATABASE_URL", "private-database-password")
    code = (
        "import sys,json\n"
        "r=json.loads(sys.stdin.buffer.read(12289))\n"
        "b=(r['url']+'|'+r['user_agent']).encode()\n"
        "h={'ok':True,'size':len(b),'metadata':{'upload':0}}\n"
        "sys.stdout.buffer.write(b'ONFETCH1 '+json.dumps(h).encode()+b'\\n'+b)\n"
    )
    processes, calls = launch_custom_child(monkeypatch, code)
    result = fetch.fetch_external_subscription(URL, user_agent="provider-agent-private")
    assert result.body.decode() == URL + "|provider-agent-private"
    command, kwargs = calls[0]
    assert command[1:3] == ["-I", "-S"] and command[-1] == "--fetch-worker"
    assert SECRET not in repr(command) + repr(kwargs["env"])
    assert "provider-agent-private" not in repr(command) + repr(kwargs["env"])
    forbidden = {"HTTPS_PROXY", "PYTHONPATH", "SSLKEYLOGFILE", "APP_DATABASE_URL"}
    assert not forbidden & kwargs["env"].keys()
    assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["close_fds"]
    assert SECRET not in repr(result) and result.metadata == {"upload": 0}
    assert processes[0].returncode == 0 and processes[0].stdout.closed
    assert not (tmp_path / "tls-secrets.log").exists()


@pytest.mark.parametrize("phase", ["dns", "connect", "tls", "headers", "body"])
def test_hard_deadline_reaps_child_in_every_network_phase(monkeypatch, phase):
    if phase == "dns":
        patch = "socket.getaddrinfo=lambda *a: time.sleep(60)"
    else:
        target = {"connect": "_connect_tls", "tls": "_connect_tls",
                  "headers": "_read_headers", "body": "_read_body"}[phase]
        patch = (
            "class Connection:\n"
            " def settimeout(self,*a): pass\n"
            " def sendall(self,*a): pass\n"
            " def close(self): pass\n"
            "globals_=namespace['_fetch_in_worker'].__globals__\n"
            "globals_['_connect_tls']=lambda *a: Connection()\n"
            "globals_['_read_headers']=lambda *a: {}\n"
            f"globals_[{target!r}]=lambda *a: time.sleep(60)"
        )
    processes, _ = launch_custom_child(monkeypatch, worker_wrapper(patch))
    started = time.monotonic()
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.fetch_external_subscription(URL, timeout=0.2)
    assert error.value.code == "timeout" and time.monotonic() - started < 1.5
    assert processes[0].returncode is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed
    assert not any(thread.name == "external-fetch-pipe" for thread in threading.enumerate())


def test_repeated_timeouts_have_no_fd_thread_or_child_leak(monkeypatch):
    processes, _ = launch_custom_child(monkeypatch, "import time; time.sleep(60)")
    count = len(os.listdir("/proc/self/fd")) if Path("/proc/self/fd").is_dir() else None
    for _ in range(8):
        with pytest.raises(fetch.ExternalFetchError) as error:
            fetch.fetch_external_subscription(URL, timeout=0.08)
        assert error.value.code == "timeout"
    assert all(process.returncode is not None for process in processes)
    assert not any(thread.name == "external-fetch-pipe" for thread in threading.enumerate())
    if count is not None:
        assert len(os.listdir("/proc/self/fd")) <= count + 1


def test_capacity_is_four_with_no_waiting_queue(monkeypatch):
    monkeypatch.setattr(fetch, "_FETCH_SLOTS", threading.BoundedSemaphore(4))
    started = threading.Event()
    processes, _ = launch_custom_child(monkeypatch, "import time; time.sleep(60)",
                                       on_start=lambda count: started.set() if count == 4 else None)

    def fetch_one():
        with pytest.raises(fetch.ExternalFetchError) as error:
            fetch.fetch_external_subscription(URL, timeout=0.8)
        return error.value.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(fetch_one) for _ in range(4)]
        assert started.wait(2)
        before = time.monotonic()
        for _ in range(5):
            with pytest.raises(fetch.ExternalFetchError) as error:
                fetch.fetch_external_subscription(URL, timeout=0.8)
            assert error.value.code == "busy"
        assert time.monotonic() - before < 0.2 and len(processes) == 4
        assert [item.result() for item in pending] == ["timeout"] * 4
    assert all(process.returncode is not None for process in processes)


def test_process_creation_failure_is_safe_and_releases_capacity(monkeypatch):
    semaphore = threading.BoundedSemaphore(4)
    monkeypatch.setattr(fetch, "_FETCH_SLOTS", semaphore)

    def fail(*_args, **_kwargs):
        raise OSError(URL + " provider-body=" + SECRET)

    monkeypatch.setattr(fetch.subprocess, "Popen", fail)
    for _ in range(8):
        with pytest.raises(fetch.ExternalFetchError) as error:
            fetch.fetch_external_subscription(URL)
        assert error.value.code == "fetch_failed" and SECRET not in str(error.value)
    assert all(semaphore.acquire(blocking=False) for _ in range(4))
    assert not semaphore.acquire(blocking=False)
    for _ in range(4):
        semaphore.release()


def test_parent_bounds_output_even_if_worker_protocol_is_broken(monkeypatch):
    code = "import sys\nsys.stdin.buffer.read()\nwhile True: sys.stdout.buffer.write(b'x'*4096)\n"
    processes, _ = launch_custom_child(monkeypatch, code)
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.fetch_external_subscription(URL, max_bytes=128, timeout=2)
    assert error.value.code == "response_too_large" and processes[0].returncode is not None


@pytest.mark.parametrize("payload", [
    b"", b"provider secret", b"ONFETCH1 {}", b"ONFETCH1 " + b"x" * 1025 + b"\n",
    b"ONFETCH1 []\n", b"ONFETCH1 {\n", b"ONFETCH1 {}\n",
    b'ONFETCH1 {"ok":true,"size":0,"size":0,"metadata":{}}\n',
    b'ONFETCH1 {"ok":true,"size":0,"metadata":{"upload":1,"upload":2}}\n',
    protocol({"ok": 1, "size": 0, "metadata": {}}),
    protocol({"ok": True, "size": 1, "metadata": {}}),
    protocol({"ok": True, "size": True, "metadata": {}}, b"x"),
    protocol({"ok": True, "size": 0, "metadata": {"upload": -1}}),
    protocol({"ok": True, "size": 0, "metadata": {"upload": True}}),
    protocol({"ok": True, "size": 0, "metadata": {"url": SECRET}}),
    protocol({"ok": True, "size": 0, "metadata": {}, "extra": SECRET}),
    protocol({"ok": False, "code": "timeout"}, b"provider-secret-body"),
])
def test_parent_protocol_is_strict_and_safe(payload):
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch._decode_result(payload, 100)
    assert error.value.code == "invalid_response" and SECRET not in str(error.value)


def test_unrecognized_errors_and_body_repr_never_disclose_secrets():
    error = fetch.ExternalFetchError("provider said " + URL)
    assert error.code == "fetch_failed" and SECRET not in str(error) + repr(error)
    assert SECRET not in repr(fetch.ExternalFetchResult(SECRET.encode(), {"upload": 1}))
    with pytest.raises(fetch.ExternalFetchError) as result:
        fetch._decode_result(protocol({"ok": False, "code": URL}), 100)
    assert result.value.code == "fetch_failed" and SECRET not in str(result.value)


def test_worker_exception_protocol_and_import_isolation(monkeypatch):
    patch = (
        "def broken(*a): raise RuntimeError(a[0]+' provider-body-secret')\n"
        "namespace['_worker_main'].__globals__['_fetch_in_worker']=broken"
    )
    processes, _ = launch_custom_child(monkeypatch, worker_wrapper(patch))
    with pytest.raises(fetch.ExternalFetchError) as error:
        fetch.fetch_external_subscription(URL, timeout=2)
    assert error.value.code == "fetch_failed" and SECRET not in str(error.value)
    assert processes[0].returncode == 0


@pytest.fixture
def tls_identity(tmp_path):
    address = os.environ.get("OPEN_NODE_FETCH_TEST_PUBLIC_IP")
    if not address:
        pytest.skip("VPS-only real TLS fixture needs an assigned public IP")
    fetch._public_ip(address)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "subscription-fixture.example")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("subscription-fixture.example"),
            x509.IPAddress(ipaddress.ip_address(address)),
        ]), critical=False).sign(key, hashes.SHA256())
    )
    certificate, private_key = tmp_path / "fixture-ca.pem", tmp_path / "fixture-key.pem"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                             serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    private_key.chmod(0o600)
    return address, certificate, private_key


@contextmanager
def tls_server(identity, payload):
    address, certificate, private_key = identity
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.bind((address, 0))
    listener.listen(1)
    listener.settimeout(5)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    state = {"sni": [], "requests": []}
    context.set_servername_callback(lambda _sock, name, _ctx: state["sni"].append(name))

    def serve():
        try:
            raw, _peer = listener.accept()
            raw.settimeout(3)
            with context.wrap_socket(raw, server_side=True) as connection:
                request = bytearray()
                while b"\r\n\r\n" not in request and len(request) <= 12288:
                    part = connection.recv(4096)
                    if not part:
                        break
                    request.extend(part)
                state["requests"].append(bytes(request))
                if callable(payload):
                    payload(connection)
                else:
                    connection.sendall(payload)
        except (OSError, ssl.SSLError):
            pass  # Certificate-negative cases intentionally close before HTTP.

    server = threading.Thread(target=serve, name="external-fetch-tls-fixture", daemon=True)
    server.start()
    try:
        yield listener.getsockname()[1], state
    finally:
        listener.close()
        server.join(timeout=6)
        assert not server.is_alive()


def test_real_isolated_child_tls_ip_san_and_query(tls_identity, monkeypatch):
    address, certificate, _key = tls_identity
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1/" + SECRET)
    body = b"proxies: []\n"
    payload = response(body, [b"Subscription-Userinfo: upload=1; total=100"])
    with tls_server(tls_identity, payload) as pair:
        port, state = pair
        result = fetch.fetch_external_subscription(f"https://{address}:{port}/feed?token={SECRET}",
                                                    user_agent="clash-meta/2.4.0", timeout=3)
    assert result.body == body and result.metadata == {"upload": 1, "total": 100}
    assert SECRET.encode() in state["requests"][0] and state["sni"] == [None]


@pytest.mark.parametrize("hostname,success", [
    ("subscription-fixture.example", True), ("mismatched-fixture.example", False),
])
def test_real_isolated_tls_sni_and_hostname_validation(
    tls_identity, monkeypatch, hostname, success,
):
    address, certificate, _key = tls_identity
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    # The fixture changes DNS only, to a real assigned public address. It never
    # patches _public_ip, ssl validation, socket.connect or any allow-private flag.
    patch = (
        "socket.getaddrinfo=lambda host,port,*a: "
        f"[(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',({address!r},port))]"
    )
    launch_custom_child(monkeypatch, worker_wrapper(patch))
    with tls_server(tls_identity, response()) as pair:
        port, state = pair
        url = f"https://{hostname}:{port}/feed?token={SECRET}"
        if success:
            assert fetch.fetch_external_subscription(url, timeout=3).body == b"proxies: []\n"
        else:
            with pytest.raises(fetch.ExternalFetchError) as error:
                fetch.fetch_external_subscription(url, timeout=3)
            assert error.value.code == "tls_failed" and SECRET not in str(error.value)
    assert state["sni"] == [hostname]
    assert bool(state["requests"]) is success


def test_real_child_rejects_untrusted_tls_certificate(tls_identity, monkeypatch):
    address, _certificate, _key = tls_identity
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    with tls_server(tls_identity, response()) as pair:
        port, state = pair
        with pytest.raises(fetch.ExternalFetchError) as error:
            fetch.fetch_external_subscription(
                f"https://{address}:{port}/feed?token={SECRET}", timeout=3,
            )
    assert error.value.code == "tls_failed" and state["requests"] == []


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_real_tls_slow_stream_cannot_extend_total_deadline(tls_identity, monkeypatch, phase):
    address, certificate, _key = tls_identity
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    processes = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(fetch.subprocess, "Popen", launch)

    def slow_payload(connection):
        if phase == "body":
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n")
        for char in b"HTTP/1.1 200 OK\r\n" * 10:
            connection.sendall(bytes([char]))
            time.sleep(0.025)

    started = time.monotonic()
    with tls_server(tls_identity, slow_payload) as pair:
        port, state = pair
        with pytest.raises(fetch.ExternalFetchError) as error:
            fetch.fetch_external_subscription(f"https://{address}:{port}/", timeout=0.35)
    assert error.value.code == "timeout" and time.monotonic() - started < 1.5
    assert processes[0].returncode is not None and len(state["requests"]) == 1


@pytest.mark.parametrize("wire_input", [
    b"x" * (fetch._MAX_REQUEST_BYTES + 1), b"[]", b"malformed-provider-secret",
    b'{"url":"secret","url":"other-secret"}',
])
def test_worker_stdin_is_bounded_and_errors_have_no_stderr_or_app_side_effects(
    tmp_path, wire_input,
):
    child = subprocess.run(
        [sys.executable, "-I", "-S", fetch.__file__, "--fetch-worker"],
        input=wire_input, capture_output=True, timeout=2,
        cwd=tmp_path,
    )
    assert child.returncode == 0 and child.stderr == b""
    assert len(child.stdout) < 200 and b"secret" not in child.stdout
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(fetch.ExternalFetchError):
        fetch._decode_result(child.stdout, 100)
