"""Real isolated Nginx HTTP/TLS, certificate rotation, rollback and systemd recovery."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "service_smoke", Path(__file__).with_name("smoke-agent-service.py")
)
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)
runtime = service.runtime


def certificate(domain="localhost"):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        cert.serial_number,
    )


def request(port, expected, cert=None):
    verify = ssl.create_default_context(cadata=cert) if cert else True
    with httpx.Client(verify=verify, trust_env=False, timeout=3) as browser:
        response = browser.get(f"{'https' if cert else 'http'}://localhost:{port}/")
        return response.status_code == 200 and response.content == expected


def tls_serial(port, cert):
    context = ssl.create_default_context(cadata=cert)
    with (
        socket.create_connection(("127.0.0.1", port), timeout=3) as connection,
        context.wrap_socket(connection, server_hostname="localhost") as secure,
    ):
        return x509.load_der_x509_certificate(
            secure.getpeercert(binary_form=True)
        ).serial_number


def exercise(work, fixture, wheel, nginx, module, xray, client, url, echo_port, mode):
    response = client.post("/api/v1/servers", json={"name": "nginx-" + mode})
    response.raise_for_status()
    created = response.json()
    base = f"/api/v1/servers/{created['server']['id']}"
    http_port, tls_port = runtime.free_port(), runtime.free_port()
    source = work / (mode + "-agent.json")
    xray_config = work / (mode + "-xray.json")
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
            "nginx_binary": str(nginx),
            "nginx_modules": [str(module)],
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
        wheel,
        "--config",
        source,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
    )
    assert fixture.properties()["User"] != "root"

    def result(command_id, expected="succeeded"):
        def read():
            items = client.get(base + "/commands").json()["commands"]
            return next(item for item in items if item["id"] == command_id)

        completed = runtime.poll(
            mode + " command completes",
            read,
            lambda item: item["status"] in {"succeeded", "failed"},
            timeout=40,
        )
        assert completed["status"] == expected, completed
        return completed

    def operation(name, body=None, expected="succeeded"):
        response = client.post(base + "/operations/" + name, json=body)
        response.raise_for_status()
        return result(response.json()["command"]["id"], expected)

    def raw(path, body=None, method="POST", query="", expected="succeeded"):
        response = client.post(
            base + "/commands",
            json={
                "path": "/api/child/" + path,
                "method": method,
                "body": body,
                "query": query,
                "timeout_ms": 25000,
            },
        )
        response.raise_for_status()
        return result(response.json()["command"]["id"], expected)

    installed = operation("nginx/install", {"domain": "localhost"})["result_body"]
    assert installed["running"] and installed["installed"]
    root = Path(installed["config_path"]).parent
    cert_root = Path(installed["certificate_dir"])
    runtime.poll(
        mode + " real HTTP under non-root systemd",
        lambda: request(http_port, b"Open Node\n"),
    )
    assert operation(
        "validate-site", {"site_type": "static", "site_value": installed["html_path"]}
    )["result_body"]["success"]
    assert operation(
        "validate-site",
        {"site_type": "proxy", "site_value": f"http://127.0.0.1:{echo_port}"},
    )["result_body"]["success"]
    raw("scan")
    reported = client.get(base + "/scan/latest").json()["scan"]["nginx"]
    assert reported["config_path"] == installed["config_path"] and reported["running"]

    cert, key, serial = certificate()
    deploy = {
        "domain": "localhost",
        "cert_pem": cert,
        "key_pem": key,
        "cert_path": "localhost.pem",
        "key_path": "localhost.key",
        "reload": "none",
    }
    deployed = operation("cert/deploy", deploy)
    assert key not in str(deployed["result_body"])
    assert (cert_root / "localhost.key").stat().st_mode & 0o777 == 0o600
    operation("nginx/setup-ssl", {"domain": "localhost"})
    runtime.poll(
        mode + " verified TLS and static website",
        lambda: request(tls_port, b"Open Node\n", cert),
    )
    assert tls_serial(tls_port, cert) == serial
    sites = operation("nginx/websites/list")["result_body"]["websites"]
    assert any(site["domain"] == "localhost" and site["managed"] for site in sites)
    config_file = root / "servers/localhost.conf"
    old_site = config_file.read_bytes()
    operation(
        "nginx/setup-ssl",
        {"domain": "localhost", "domain_config": "server { invalid yes; }"},
        expected="failed",
    )
    assert config_file.read_bytes() == old_site
    assert request(tls_port, b"Open Node\n", cert)
    print("PASS invalid Nginx configuration leaves live TLS unchanged", flush=True)

    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        blocked_port = occupied.getsockname()[1]
        operation(
            "nginx/setup-ssl",
            {
                "domain": "localhost",
                "domain_config": old_site.decode()
                + f"\nserver {{ listen 127.0.0.1:{blocked_port}; return 200 blocked; }}\n",
            },
            expected="failed",
        )
        assert config_file.read_bytes() == old_site
        assert request(tls_port, b"Open Node\n", cert)
    print(
        "PASS occupied listener fails reload and restores old workers/config",
        flush=True,
    )

    new_cert, new_key, new_serial = certificate()
    operation(
        "cert/deploy",
        {**deploy, "cert_pem": new_cert, "key_pem": new_key, "reload": "both"},
    )
    assert tls_serial(tls_port, new_cert) == new_serial
    operation(
        "cert/deploy",
        {**deploy, "cert_pem": new_cert, "reload": "nginx"},
        expected="failed",
    )
    assert (cert_root / "localhost.key").read_text() == new_key
    assert tls_serial(tls_port, new_cert) == new_serial
    print("PASS real certificate rotation and mismatched-key rejection", flush=True)
    operation("nginx/setup-ssl", {"domain": "../escape"}, expected="failed")
    raw(
        "nginx/config-files",
        method="GET",
        query="file=../agent.json",
        expected="failed",
    )
    raw(
        "cert/deploy",
        {**deploy, "cert_path": str(fixture.root / "config/agent.json")},
        expected="failed",
    )

    proxy = old_site.decode().replace(
        f"root {installed['html_path']};", f"proxy_pass http://127.0.0.1:{echo_port};"
    )
    operation("nginx/setup-ssl", {"domain": "localhost", "domain_config": proxy})
    assert request(tls_port, runtime.RESPONSE_BODY, new_cert)
    print("PASS TLS reverse proxy transfers actual response bytes", flush=True)

    stream_port, retained_port = runtime.free_port(), runtime.free_port()
    stream_config = (
        f"server {{ listen 127.0.0.1:{stream_port}; proxy_pass 127.0.0.1:{echo_port}; }}\n"
        f"server {{ listen 127.0.0.1:{retained_port}; proxy_pass 127.0.0.1:{echo_port}; }}\n"
    )
    operation(
        "nginx/config-files/write",
        {"path": "stream_servers/relay.conf", "content": stream_config},
    )
    main = operation("nginx/config/read")["result_body"]["config"]
    operation(
        "nginx/config/write",
        {"config": main + "\nstream { include stream_servers/*.conf; }\n"},
    )
    raw("services/control", {"service": "nginx", "action": "reload"})
    assert request(stream_port, runtime.RESPONSE_BODY)
    assert request(retained_port, runtime.RESPONSE_BODY)
    removed = operation("nginx/clear-stream-port", {"port": stream_port})["result_body"]
    assert removed["removed"] == 1
    runtime.poll(
        "removed stream listener closes", lambda: not runtime.port_open(stream_port)
    )
    assert request(retained_port, runtime.RESPONSE_BODY)
    print("PASS stream cleanup preserves adjacent server blocks", flush=True)

    raw("services/control", {"service": "nginx", "action": "stop"})
    subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=20)
    runtime.poll("stopped Nginx intent survives Agent restart", fixture.ready)
    assert not runtime.port_open(tls_port)
    raw("services/control", {"service": "nginx", "action": "start"})
    assert request(tls_port, runtime.RESPONSE_BODY, new_cert)

    old_master = int((fixture.root / "state/nginx/nginx.pid").read_text())
    old_workers = Path(f"/proc/{old_master}/task/{old_master}/children").read_text().split()
    os.kill(old_master, signal.SIGKILL)
    runtime.poll(
        "dead Nginx master and orphan workers are replaced",
        lambda: int((fixture.root / "state/nginx/nginx.pid").read_text()) != old_master
        and request(tls_port, runtime.RESPONSE_BODY, new_cert),
    )
    assert all(not Path(f"/proc/{pid}").exists() for pid in old_workers)
    old_master = int((fixture.root / "state/nginx/nginx.pid").read_text())
    old_agent = int(fixture.properties()["MainPID"])
    os.kill(old_agent, signal.SIGKILL)
    runtime.poll(
        "systemd replaces killed Agent and Nginx",
        lambda: fixture.ready() and int(fixture.properties()["MainPID"]) != old_agent,
        timeout=40,
    )
    assert not Path(f"/proc/{old_master}").exists()
    assert request(tls_port, runtime.RESPONSE_BODY, new_cert)

    # Emulate a process dying after the first file replacement of a recorded transaction.
    subprocess.run(["systemctl", "stop", fixture.unit], check=True, timeout=20)
    original = config_file.read_bytes()
    record = fixture.root / "state/host-transaction.json"
    runtime.write_private(
        record, {str(config_file): base64.b64encode(original).decode()}
    )
    owner = config_file.stat()
    os.chown(record, owner.st_uid, owner.st_gid)
    config_file.write_text("interrupted invalid config")
    subprocess.run(["systemctl", "start", fixture.unit], check=True, timeout=20)
    runtime.poll("durable file undo runs before runtime startup", fixture.ready)
    assert config_file.read_bytes() == original and not record.exists()
    assert request(tls_port, runtime.RESPONSE_BODY, new_cert)

    operation("nginx/websites/delete", {"domain": "localhost"})
    assert not config_file.exists()
    assert (cert_root / "localhost.key").read_text() == new_key
    runtime.poll(
        "deleted website listener closes", lambda: not runtime.port_open(tls_port)
    )
    raw("nginx/config-files", method="GET")
    logs = raw("logs", method="GET", query="service=nginx&lines=10")["result_body"][
        "logs"
    ]
    assert isinstance(logs, str) and new_key not in logs
    operation("nginx/remove")
    assert not runtime.port_open(retained_port)
    fixture.cli("uninstall")
    assert (cert_root / "localhost.key").read_text() == new_key
    assert (root / "nginx.conf").is_file()
    fixture.cli("install", "--wheel", wheel)
    assert fixture.ready() and not runtime.port_open(retained_port)
    print(
        f"PASS {mode} Nginx removal/reinstallation preserves data and stopped intent",
        flush=True,
    )


def run(wheel, nginx, module, archive, *, exercise_fn=exercise):
    if os.geteuid() != 0:
        raise RuntimeError("Run this smoke on the root-accessible systemd VPS")
    with tempfile.TemporaryDirectory(prefix="open-node-nginx-smoke-") as temporary:
        work = Path(temporary)
        xray = runtime.download_xray(work, archive)
        password = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend/app"),
            "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.db'}",
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
        }
        subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            input=password + "\n",
            text=True,
            env=env,
            cwd=work,
            capture_output=True,
            check=True,
            timeout=30,
        )
        with ExitStack() as stack:
            echo = runtime.ThreadingHTTPServer(("127.0.0.1", 0), runtime.EchoHandler)
            thread = threading.Thread(target=echo.serve_forever, daemon=True)
            thread.start()
            stack.callback(echo.server_close)
            stack.callback(thread.join, 5)
            stack.callback(echo.shutdown)
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            stack.enter_context(
                runtime.process(
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
            )
            client = stack.enter_context(
                httpx.Client(base_url=url, timeout=10, trust_env=False)
            )
            runtime.poll(
                "isolated backend starts",
                lambda: client.get("/healthz").status_code == 200,
            )
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
                headers={"X-Open-Node-Client": "browser"},
            )
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            for mode in ("websocket", "http"):
                fixture = service.Fixture(work)
                try:
                    exercise_fn(
                        work,
                        fixture,
                        wheel,
                        nginx,
                        module,
                        xray,
                        client,
                        url,
                        echo.server_port,
                        mode,
                    )
                except BaseException:
                    for file in (
                        fixture.root / "state/nginx.log",
                        work / "backend.log",
                    ):
                        if file.is_file():
                            print(file.read_text()[-8000:], file=sys.stderr)
                    raise
                finally:
                    fixture.cleanup()
    print("PASS real Nginx and certificate smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--nginx-stream-module", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    args = parser.parse_args()
    run(
        args.wheel.resolve(),
        args.nginx.resolve(),
        args.nginx_stream_module.resolve(),
        args.xray_archive.resolve() if args.xray_archive else None,
    )
