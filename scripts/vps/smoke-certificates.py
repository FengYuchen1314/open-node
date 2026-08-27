"""Real DNS-01/EAB, renewal and Agent TLS deployment against a loopback-only Pebble CA."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import secrets
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from dnslib import NS, QTYPE, RR, SOA, TXT, A
from dnslib.server import BaseResolver, DNSLogger, DNSServer

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "nginx_smoke", Path(__file__).with_name("smoke-nginx.py")
)
nginx_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nginx_smoke)
runtime, service = nginx_smoke.runtime, nginx_smoke.service


class DNSFixture(BaseResolver):
    def __init__(self):
        self.address = "127.0.0.1"
        self.records = {}
        self.events = []
        self.queries = []
        self.lock = threading.Lock()
        self.password = secrets.token_urlsafe(32)
        self.reject = False

    def resolve(self, request, handler):
        response = request.reply()
        response.header.aa = 1
        name = str(request.q.qname).lower()
        kind = request.q.qtype
        with self.lock:
            self.queries.append((name, QTYPE[kind]))
            if kind == QTYPE.TXT:
                for value in self.records.get(name, set()):
                    response.add_answer(RR(name, QTYPE.TXT, ttl=0, rdata=TXT(value)))
        if kind == QTYPE.SOA:
            response.add_answer(
                RR(
                    "acme.test.",
                    QTYPE.SOA,
                    ttl=0,
                    rdata=SOA("localhost.", "admin.acme.test.", (1, 60, 60, 60, 0)),
                )
            )
        elif kind == QTYPE.NS:
            response.add_answer(
                RR("acme.test.", QTYPE.NS, ttl=0, rdata=NS("localhost."))
            )
            response.add_ar(RR("localhost.", QTYPE.A, ttl=0, rdata=A(self.address)))
        elif kind == QTYPE.A:
            response.add_answer(RR(name, QTYPE.A, ttl=0, rdata=A(self.address)))
        return response

    def start(self, stack):
        # lego resolves authoritative NS names through the OS resolver. localhost
        # keeps that lookup offline without modifying hosts or resolv.conf.
        for address, family in (
            ("127.0.0.1", socket.AF_INET),
            ("::1", socket.AF_INET6),
        ):
            for tcp in (False, True):
                base = (
                    socketserver.ThreadingTCPServer
                    if tcp
                    else socketserver.ThreadingUDPServer
                )
                server_type = type(
                    "ExclusiveDNS",
                    (base,),
                    {
                        "address_family": family,
                        "daemon_threads": True,
                        "allow_reuse_address": False,
                    },
                )
                server = DNSServer(
                    self,
                    port=53,
                    address=address,
                    tcp=tcp,
                    server=server_type,
                    logger=DNSLogger(log="error"),
                )
                server.start_thread()
                stack.callback(server.server.server_close)
                stack.callback(server.stop)
        fixture = self

        class Webhook(BaseHTTPRequestHandler):
            def do_POST(self):
                expected = (
                    "Basic "
                    + base64.b64encode(
                        ("fixture:" + fixture.password).encode()
                    ).decode()
                )
                valid = secrets.compare_digest(
                    self.headers.get("Authorization", ""), expected
                )
                if not valid or fixture.reject:
                    self.send_error(401)
                    return
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                name, value = payload["fqdn"].lower(), payload["value"]
                assert name == "_acme-challenge.acme.test."
                assert self.path in {"/present", "/cleanup"}
                with fixture.lock:
                    fixture.events.append((self.path, name, value))
                    values = fixture.records.setdefault(name, set())
                    if self.path == "/present":
                        values.add(value)
                    else:
                        values.discard(value)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_):
                pass

        webhook = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
        thread = threading.Thread(target=webhook.serve_forever, daemon=True)
        thread.start()
        stack.callback(webhook.server_close)
        stack.callback(thread.join, 5)
        stack.callback(webhook.shutdown)
        return f"http://127.0.0.1:{webhook.server_port}"


def write_pem(path, data):
    path.write_text(data)
    path.chmod(0o600)


def https_identity(work):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Open Node Pebble fixture")]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ip_address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = work / "https.pem", work / "https.key"
    write_pem(cert_path, cert.public_bytes(serialization.Encoding.PEM).decode())
    write_pem(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    return cert_path, key_path


def api(client, path, method="GET", body=None):
    response = client.request(method, "/api/v1/" + path, json=body)
    response.raise_for_status()
    return response.json()


def wait_job(client, identifier, expected="succeeded"):
    detail = runtime.poll(
        "ACME job completes",
        lambda: api(client, "certificates/" + identifier),
        lambda item: item["jobs"] and not item["certificate"]["active_job_id"],
        timeout=260,
    )
    assert detail["jobs"][0]["status"] == expected, detail["jobs"][0]
    return detail


def tls_read(port, trust):
    context = ssl.create_default_context(cadata=trust)
    with (
        socket.create_connection(("127.0.0.1", port), timeout=3) as raw,
        context.wrap_socket(raw, server_hostname="edge.acme.test") as secure,
    ):
        serial = x509.load_der_x509_certificate(
            secure.getpeercert(binary_form=True)
        ).serial_number
        secure.sendall(b"GET / HTTP/1.0\r\nHost: edge.acme.test\r\n\r\n")
        body = bytearray()
        while block := secure.recv(4096):
            body.extend(block)
        assert b"200 OK" in body and body.endswith(b"Open Node\n"), body
        return str(serial)


def deploy_to_agent(
    work,
    fixture,
    args,
    xray,
    client,
    url,
    identifier,
    first_version,
    current,
    trust,
    mode,
):
    created = api(client, "servers", "POST", {"name": "acme-" + mode})
    server_id = created["server"]["id"]
    base = "servers/" + server_id
    http_port, tls_port = runtime.free_port(), runtime.free_port()
    source, xray_config = work / (mode + "-agent.json"), work / (mode + "-xray.json")
    runtime.write_private(
        source,
        {
            "master_url": url,
            "token": created["agent_token"],
            "allow_insecure_http": True,
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
            "nginx_binary": str(args.nginx),
            "nginx_modules": [str(args.nginx_stream_module)],
            "nginx_http_port": http_port,
            "nginx_https_port": tls_port,
            "nginx_listen_address": "127.0.0.1",
        },
    )
    runtime.write_private(
        xray_config, {"inbounds": [], "outbounds": [{"protocol": "freedom"}]}
    )
    fixture.cli(
        "install",
        "--wheel",
        args.wheel,
        "--config",
        source,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
    )
    assert fixture.properties()["User"] != "root"

    def command_result(command_id):
        result = runtime.poll(
            mode + " Agent command",
            lambda: next(
                item
                for item in api(client, base + "/commands")["commands"]
                if item["id"] == command_id
            ),
            lambda item: item["status"] in {"succeeded", "failed"},
            timeout=40,
        )
        assert result["status"] == "succeeded", result
        return result

    def operation(name, body=None):
        command = api(client, base + "/operations/" + name, "POST", body)
        return command_result(command["command"]["id"])

    operation("nginx/install", {"domain": "edge.acme.test"})
    operation("scan")
    target = api(
        client,
        f"certificates/{identifier}/targets",
        "POST",
        {
            "server_id": server_id,
            "domain": "edge.acme.test",
            "cert_name": "edge.acme.test",
            "reload": "both",
            "auto_deploy": True,
        },
    )

    def deployed(version):
        return runtime.poll(
            mode + " automatic certificate deployment",
            lambda: next(
                item
                for item in api(client, "certificates/" + identifier)["targets"]
                if item["id"] == target["id"]
            ),
            lambda item: (
                item["status"] == "succeeded" and item["version_id"] == version
            ),
            timeout=45,
        )

    deployed(current["certificate"]["version_id"])
    operation("nginx/setup-ssl", {"domain": "edge.acme.test"})
    serial = current["versions"][0]["details"]["serial"]
    runtime.poll(
        mode + " trusted wildcard TLS and HTTP bytes",
        lambda: tls_read(tls_port, trust),
        lambda value: value == serial,
    )
    api(
        client,
        f"certificates/{identifier}/versions/{first_version['id']}/activate",
        "POST",
    )
    deployed(first_version["id"])
    runtime.poll(
        mode + " version rollback updates live TLS",
        lambda: tls_read(tls_port, trust),
        lambda value: value == first_version["details"]["serial"],
    )
    api(
        client,
        f"certificates/{identifier}/versions/{current['certificate']['version_id']}/activate",
        "POST",
    )
    deployed(current["certificate"]["version_id"])
    api(client, f"certificates/{identifier}/targets/{target['id']}", "DELETE")


def run(args):
    for binary, expected in ((args.lego, "4.35.2"), (args.pebble, "2.6.0")):
        result = subprocess.run(
            [str(binary), "--version" if binary == args.lego else "-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert expected in result.stdout, result.stdout
    with tempfile.TemporaryDirectory(prefix="open-node-acme-smoke-") as temporary:
        work = Path(temporary)
        with ExitStack() as stack:
            dns = DNSFixture()
            endpoint = dns.start(stack)
            cert_path, key_path = https_identity(work)
            ca_port, management_port = runtime.free_port(), runtime.free_port()
            directory = f"https://127.0.0.1:{ca_port}/dir"
            eab_kid, eab_key = "open-node-fixture", secrets.token_urlsafe(32)
            config = work / "pebble.json"
            runtime.write_private(
                config,
                {
                    "pebble": {
                        "listenAddress": f"127.0.0.1:{ca_port}",
                        "managementListenAddress": f"127.0.0.1:{management_port}",
                        "certificate": str(cert_path),
                        "privateKey": str(key_path),
                        "httpPort": 5002,
                        "tlsPort": 5001,
                        "certificateValidityPeriod": 240,
                        "externalAccountBindingRequired": True,
                        "externalAccountMACKeys": {eab_kid: eab_key},
                        "retryAfter": {"authz": 1, "order": 1},
                    }
                },
            )
            stack.enter_context(
                runtime.process(
                    work,
                    "pebble",
                    [
                        str(args.pebble),
                        "-config",
                        str(config),
                        "-dnsserver",
                        dns.address + ":53",
                    ],
                    env={
                        "PATH": os.defpath,
                        "PEBBLE_VA_NOSLEEP": "1",
                        "PEBBLE_WFE_NONCEREJECT": "0",
                        "PEBBLE_AUTHZREUSE": "0",
                    },
                )
            )
            ca_client = stack.enter_context(
                httpx.Client(
                    verify=ssl.create_default_context(cafile=str(cert_path)),
                    trust_env=False,
                    timeout=5,
                )
            )
            runtime.poll(
                "TLS-verified Pebble test CA",
                lambda: ca_client.get(directory).status_code == 200,
            )
            trust = ca_client.get(f"https://127.0.0.1:{management_port}/roots/0").text
            assert "BEGIN CERTIFICATE" in trust
            password = secrets.token_urlsafe(32)
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "backend/app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.db'}",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
                "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "vault"),
                "OPEN_NODE_CERTIFICATE_LEGO_BINARY": str(args.lego),
                "OPEN_NODE_CERTIFICATE_CA_FILE": str(cert_path),
                "OPEN_NODE_CERTIFICATE_ACME_DIRECTORIES": json.dumps([directory]),
                "OPEN_NODE_CERTIFICATE_DNS_RESOLVERS": json.dumps(
                    [dns.address + ":53"]
                ),
                "OPEN_NODE_CERTIFICATE_ALLOW_LOOPBACK_HTTP": "true",
                "OPEN_NODE_CERTIFICATE_POLL_SECONDS": "1",
            }
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=password + "\n",
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
                env=env,
                cwd=work,
            )
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"

            def start_backend():
                context = runtime.process(
                    work,
                    "backend",
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "open_node.main:app",
                        "--fd",
                        str(listener.fileno()),
                    ],
                    env=env,
                    pass_fds=(listener.fileno(),),
                )
                context.__enter__()
                return context

            backend = start_backend()
            stack.callback(lambda: backend.__exit__(None, None, None))
            client = stack.enter_context(
                httpx.Client(base_url=url, timeout=10, trust_env=False)
            )
            runtime.poll(
                "certificate backend starts",
                lambda: client.get("/healthz").status_code == 200,
            )
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
                headers={"X-Open-Node-Client": "browser"},
            )
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            provider = api(
                client,
                "certificates/providers",
                "POST",
                {
                    "name": "Private fixture DNS",
                    "provider": "httpreq",
                    "credentials": {
                        "HTTPREQ_ENDPOINT": endpoint,
                        "HTTPREQ_USERNAME": "fixture",
                        "HTTPREQ_PASSWORD": dns.password,
                    },
                },
            )
            profile = api(
                client,
                "certificates",
                "POST",
                {
                    "name": "Wildcard fixture",
                    "domains": ["acme.test", "*.acme.test"],
                    "email": "operator@example.com",
                    "provider_id": provider["id"],
                    "directory_url": directory,
                    "accept_terms": True,
                    "auto_renew": True,
                    "eab_kid": eab_kid,
                    "eab_hmac_key": eab_key,
                },
            )
            identifier = profile["id"]
            base = "certificates/" + identifier
            try:
                api(client, base + "/issue", "POST", {})
                first = wait_job(client, identifier)
                first_version = first["versions"][0]
                exported = api(client, base + "/material")
                assert "key_pem" not in exported and set(exported["domains"]) == {
                    "acme.test",
                    "*.acme.test",
                }
                assert len([item for item in dns.events if item[0] == "/present"]) >= 2
                assert not any(dns.records.values())
                assert ("_acme-challenge.acme.test.", "TXT") in dns.queries
                print(
                    "PASS real EAB account, apex/wildcard DNS-01 and challenge cleanup",
                    flush=True,
                )

                api(client, base + "/renew", "POST", {})
                skipped = wait_job(client, identifier, "skipped")
                assert len(skipped["versions"]) == 1
                dns.reject = True
                api(client, base + "/renew", "POST", {"force": True})
                failed = wait_job(client, identifier, "failed")
                assert failed["certificate"]["version_id"] == first_version["id"]
                assert (
                    api(client, base + "/material")["cert_pem"] == exported["cert_pem"]
                )
                dns.reject = False
                api(client, base + "/renew", "POST", {"force": True})
                renewed = wait_job(client, identifier)
                assert (
                    renewed["versions"][0]["details"]["serial"]
                    != first_version["details"]["serial"]
                )
                assert not any(dns.records.values())
                print(
                    "PASS not-due skip, DNS failure preservation and forced renewal",
                    flush=True,
                )

                backend.__exit__(None, None, None)
                backend = start_backend()
                runtime.poll(
                    "backend restart preserves certificate and operator session",
                    lambda: (
                        api(client, base)["certificate"]["version_id"]
                        == renewed["certificate"]["version_id"]
                    ),
                )
                current = runtime.poll(
                    "short-lived certificate renews automatically",
                    lambda: api(client, base),
                    lambda item: (
                        len(item["versions"]) == 3
                        and not item["certificate"]["active_job_id"]
                    ),
                    timeout=160,
                )
                assert (
                    current["jobs"][0]["status"] == "succeeded"
                    and not current["jobs"][0]["force"]
                )
                api(
                    client,
                    base,
                    "PATCH",
                    {"name": profile["name"], "auto_renew": False},
                )
                xray = runtime.download_xray(work, args.xray_archive)
                for mode in ("websocket", "http"):
                    fixture = service.Fixture(work)
                    try:
                        deploy_to_agent(
                            work,
                            fixture,
                            args,
                            xray,
                            client,
                            url,
                            identifier,
                            first_version,
                            current,
                            trust,
                            mode,
                        )
                    finally:
                        fixture.cleanup()
                assert not any(dns.records.values())
                public = json.dumps(api(client, base))
                assert (
                    dns.password not in public
                    and eab_key not in public
                    and "PRIVATE KEY" not in public
                )
                database = (work / "backend.db").read_bytes()
                assert (
                    dns.password.encode() not in database
                    and eab_key.encode() not in database
                )
                assert (work / "vault/vault.key").stat().st_mode & 0o777 == 0o600
                for path in (work / "vault").rglob("*"):
                    assert not path.stat().st_mode & 0o077, path
                print(
                    "PASS persisted secrets, private state and both Agent transports",
                    flush=True,
                )
            except BaseException:
                backend.__exit__(None, None, None)
                print(
                    {
                        "dns_queries": dns.queries[-30:],
                        "webhook_events": len(dns.events),
                    },
                    file=sys.stderr,
                )
                for path in [
                    work / "pebble.log",
                    work / "backend.log",
                    *(work / "vault").rglob("last-job.log"),
                ]:
                    if path.is_file():
                        text = path.read_text()[-6000:]
                        for secret in (password, dns.password, eab_key):
                            text = text.replace(secret, "[redacted]")
                        print(text, file=sys.stderr)
                raise
    print("PASS real ACME lifecycle and TLS deployment smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("lego", "pebble", "wheel", "nginx", "nginx-stream-module"):
        parser.add_argument(
            "--" + name, type=lambda value: Path(value).resolve(), required=True
        )
    parser.add_argument("--xray-archive", type=lambda value: Path(value).resolve())
    run(parser.parse_args())
