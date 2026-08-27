"""Exercise the shipped Compose deployment using disposable VPS-only resources."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


nginx_fixture = module("packaging_nginx", "smoke-nginx.py")
runtime = nginx_fixture.runtime


def command(args, *, env=None, input=None, check=True):
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    result = subprocess.run(
        args, env=env, capture_output=True, check=False, timeout=180, **stdin
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed: {args}\n{result.stderr.decode(errors='replace')}"
        )
    return result


class Deployment:
    def __init__(self, tag):
        self.project = "open-node-package-smoke-" + secrets.token_hex(6)
        self.port = runtime.free_port()
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OPEN_NODE_", "COMPOSE_"))
        }
        self.env.update(OPEN_NODE_IMAGE_TAG=tag, OPEN_NODE_HTTP_PORT=str(self.port))
        self.args = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            self.project,
            "-f",
            str(ROOT / "deploy/compose.yaml"),
        ]

    def compose(self, *args, **kwargs):
        return command([*self.args, *args], env=self.env, **kwargs)

    def up(self):
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "65")

    def inspect(self):
        identifier = self.compose("ps", "-a", "-q", "open-node").stdout.decode().strip()
        assert identifier
        return json.loads(command(["docker", "inspect", identifier]).stdout)[0]

    def volume(self):
        mounts = self.inspect()["Mounts"]
        mount = next(
            item for item in mounts if item["Destination"] == "/var/lib/open-node"
        )
        assert mount["Type"] == "volume"
        info = json.loads(
            command(["docker", "volume", "inspect", mount["Name"]]).stdout
        )[0]
        assert info["Labels"]["com.docker.compose.project"] == self.project
        return Path(mount["Source"])

    def close(self):
        # Only these freshly randomized projects are ever removed, including their data.
        assert self.project.startswith("open-node-package-smoke-")
        self.compose("down", "--volumes", "--remove-orphans", "--timeout", "30")


def login(client, password):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": password},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def preserved(client, server_id, certificate_id, cert, key):
    assert client.get("/api/v1/auth/session").json()["authenticated"]
    assert any(
        server["id"] == server_id for server in client.get("/api/v1/servers").json()
    )
    response = client.get(
        f"/api/v1/certificates/{certificate_id}/material?include_private_key=true"
    )
    assert response.status_code == 200, response.text
    assert response.json()["cert_pem"] == cert and response.json()["key_pem"] == key


def hardening(deployment):
    info = deployment.inspect()
    assert info["Config"]["User"] == "10001:10001"
    assert info["HostConfig"]["ReadonlyRootfs"]
    assert "ALL" in info["HostConfig"]["CapDrop"]
    assert "no-new-privileges:true" in info["HostConfig"]["SecurityOpt"]
    assert info["HostConfig"]["Init"] and not info["HostConfig"]["Privileged"]
    assert info["HostConfig"]["PortBindings"]["8080/tcp"][0]["HostIp"] == "127.0.0.1"
    assert info["State"]["Health"]["Status"] == "healthy"
    assert all(mount["Type"] != "bind" for mount in info["Mounts"])
    assert deployment.volume().stat().st_uid == 10001
    assert deployment.volume().stat().st_mode & 0o777 == 0o700
    deployment.compose(
        "exec",
        "-T",
        "open-node",
        "python",
        "-c",
        """
import os
from pathlib import Path
assert os.getuid() == 10001
try:
    Path('/opt/open-node/forbidden').write_text('no')
except OSError:
    pass
else:
    raise AssertionError('Root filesystem is writable')
Path('/tmp/writable').write_text('yes')
assert Path('/usr/local/bin/lego').is_file()
""",
    )
    version = deployment.compose("exec", "-T", "open-node", "lego", "--version").stdout
    assert b"4.35.2" in version
    print(
        "PASS non-root, read-only root, private volume, loopback port and pinned lego",
        flush=True,
    )


def run(tag, nginx, output, agent_python, archive):
    output.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(24)
    with (
        tempfile.TemporaryDirectory(prefix="open-node-package-") as temporary,
        ExitStack() as stack,
    ):
        work = Path(temporary)
        deployment = Deployment(tag)
        stack.callback(deployment.close)
        deployment.up()
        hardening(deployment)
        identity_path = "/var/lib/open-node/agent-identity/seed"
        identity_command = ["exec", "-T", "open-node", "python", "-m", "open_node.agent_identity"]
        identity = json.loads(deployment.compose(*identity_command, "create", identity_path).stdout)
        assert deployment.compose(*identity_command, "create", identity_path, check=False).returncode != 0
        assert json.loads(deployment.compose(*identity_command, "show", identity_path).stdout) == identity
        deployment.env["OPEN_NODE_AGENT_IDENTITY_FILE"] = identity_path
        plain = f"http://127.0.0.1:{deployment.port}"
        with httpx.Client(base_url=plain, trust_env=False) as anonymous:
            assert not anonymous.get("/api/v1/auth/session").json()["configured"]
            assert anonymous.get("/api/v1/servers").status_code == 401
            spoof = anonymous.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
                headers={
                    "Origin": "https://localhost",
                    "Host": "localhost",
                    "X-Forwarded-Proto": "https",
                    "X-Open-Node-Client": "browser",
                },
            )
            assert spoof.status_code == 403
        gateway = next(
            iter(deployment.inspect()["NetworkSettings"]["Networks"].values())
        )["Gateway"]
        assert gateway
        deployment.env["OPEN_NODE_TRUSTED_PROXIES"] = gateway
        deployment.up()
        deployment.compose(
            "exec",
            "-T",
            "open-node",
            "open-node-admin",
            "create",
            "--password-stdin",
            input=(password + "\n").encode(),
        )
        duplicate = deployment.compose(
            "exec",
            "-T",
            "open-node",
            "open-node-admin",
            "create",
            "--password-stdin",
            input=(secrets.token_urlsafe(24) + "\n").encode(),
            check=False,
        )
        assert duplicate.returncode != 0
        cert, key, _ = nginx_fixture.certificate()
        for name, value in (("cert.pem", cert), ("key.pem", key)):
            (work / name).write_text(value)
            (work / name).chmod(0o600)
        tls_port, redirect_port = runtime.free_port(), runtime.free_port()
        url = f"https://localhost:{tls_port}"
        template = (ROOT / "deploy/nginx.conf.example").read_text()
        template = template.replace("listen 80;", f"listen 127.0.0.1:{redirect_port};")
        template = template.replace(
            "listen 443 ssl;", f"listen 127.0.0.1:{tls_port} ssl;"
        )
        template = template.replace("https://panel.example.com", url)
        template = template.replace(
            "/etc/letsencrypt/live/panel.example.com/fullchain.pem",
            str(work / "cert.pem"),
        )
        template = template.replace(
            "/etc/letsencrypt/live/panel.example.com/privkey.pem", str(work / "key.pem")
        )
        template = template.replace("panel.example.com", "localhost")
        template = template.replace("http://127.0.0.1:8080", plain)
        config = work / "nginx.conf"
        temporary_paths = "\n".join(
            f"{kind}_temp_path {work}/{kind};"
            for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
        )
        config.write_text(
            f"user root; pid {work}/nginx.pid; error_log {work}/error.log;\n"
            f"events {{}}\nhttp {{ access_log off; {temporary_paths}\n{template} }}\n"
        )
        (work / "logs").mkdir()
        command([str(nginx), "-p", str(work), "-c", str(config), "-t"])
        stack.enter_context(
            runtime.process(
                work,
                "proxy",
                [str(nginx), "-p", str(work), "-c", str(config), "-g", "daemon off;"],
            )
        )
        tls = ssl.create_default_context(cadata=cert)
        client = stack.enter_context(
            httpx.Client(
                base_url=url,
                verify=tls,
                trust_env=False,
                timeout=5,
                headers={"Origin": url},
            )
        )
        try:
            runtime.poll(
                "HTTPS reverse proxy", lambda: client.get("/healthz").status_code == 200
            )
        except TimeoutError:
            for name in ("proxy.log", "error.log"):
                print((work / name).read_text()[-6000:], flush=True)
            raise
        with httpx.Client(trust_env=False) as anonymous:
            redirect = anonymous.get(f"http://127.0.0.1:{redirect_port}/config")
            assert (
                redirect.status_code == 308
                and redirect.headers["location"] == url + "/config"
            )
        assert (
            client.get("/healthz", headers={"Host": "invalid.example"}).status_code
            == 421
        )
        for path in ("/", "/config", "/certificates", "/subscriptions"):
            response = client.get(path, headers={"Accept": "text/html"})
            assert response.status_code == 200 and '<div id="app">' in response.text
            assert response.headers["cache-control"] == "no-cache"
        for path in ("/.env", "/api/missing", "/assets/missing.js"):
            assert client.get(path, headers={"Accept": "text/html"}).status_code == 404
        login(client, password)
        denied = client.post(
            "/api/v1/servers",
            json={"name": "must-not-exist"},
            headers={"Origin": "https://untrusted.example"},
        )
        assert denied.status_code == 403
        assert (
            client.post(
                "/api/v1/servers",
                json={"name": "no-csrf"},
                headers={"X-CSRF-Token": ""},
            ).status_code
            == 403
        )
        with connect(
            url.replace("https://", "wss://") + "/api/public/probe-ws",
            ssl=tls,
            proxy=None,
        ) as websocket:
            assert "servers" in json.loads(websocket.recv(timeout=5))
        print(
            "PASS HTTPS cookies, origin/CSRF boundaries, SPA deep links and WSS",
            flush=True,
        )
        created = client.post("/api/v1/servers", json={"name": "deployment-edge"})
        assert created.status_code == 201, created.text
        server_id = created.json()["server"]["id"]
        secret = secrets.token_urlsafe(32)
        provider = client.post(
            "/api/v1/certificates/providers",
            json={
                "name": "Deployment DNS",
                "provider": "cloudflare",
                "credentials": {"CF_DNS_API_TOKEN": secret},
            },
        )
        assert provider.status_code == 201 and secret not in provider.text
        imported = client.post(
            "/api/v1/certificates/import",
            json={
                "name": "Deployment TLS",
                "cert_pem": cert,
                "key_pem": key,
            },
        )
        assert imported.status_code == 201, imported.text
        certificate_id = imported.json()["id"]
        volume = deployment.volume()
        vault = volume / "certificates/vault.key"
        original_vault = vault.read_bytes()
        identity_file = volume / "agent-identity/seed"
        original_identity = identity_file.read_bytes()
        assert identity_file.stat().st_mode & 0o777 == 0o600
        assert vault.stat().st_mode & 0o777 == 0o600
        assert (volume / "open-node.db").stat().st_mode & 0o777 == 0o600
        assert secret.encode() not in (volume / "open-node.db").read_bytes()
        original = deployment.inspect()["Id"]
        deployment.compose("down")
        deployment.up()
        # The network may get a new gateway after down; trust only its current address.
        gateway = next(
            iter(deployment.inspect()["NetworkSettings"]["Networks"].values())
        )["Gateway"]
        deployment.env["OPEN_NODE_TRUSTED_PROXIES"] = gateway
        deployment.up()
        assert deployment.inspect()["Id"] != original
        preserved(client, server_id, certificate_id, cert, key)
        assert vault.read_bytes() == original_vault
        assert identity_file.read_bytes() == original_identity
        assert client.get("/api/v1/agents/identity").json() == identity
        print(
            "PASS container recreation preserves login, inventory and encrypted private key",
            flush=True,
        )
        backup_restore(deployment, work, client, server_id, certificate_id, cert, key)
        upgrade_rollback(
            deployment, work, tag, client, server_id, certificate_id, cert, key
        )
        xray = runtime.download_xray(work, archive)
        echo = runtime.ThreadingHTTPServer(("127.0.0.1", 0), runtime.EchoHandler)
        echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
        echo_thread.start()
        stack.callback(echo.server_close)
        stack.callback(echo_thread.join, 5)
        stack.callback(echo.shutdown)
        for mode in ("websocket", "http"):
            runtime.exercise_mode(
                work,
                xray,
                agent_python,
                client,
                url,
                volume / "open-node.db",
                echo.server_port,
                mode,
                ca_file=work / "cert.pem",
            )
        print(
            "PASS installed Agent and real Xray through HTTPS/WSS on both transports",
            flush=True,
        )
        spki = (
            x509.load_pem_x509_certificate(cert.encode())
            .public_key()
            .public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        browser = module("packaging_browser", "smoke-operator-ui.py")
        browser.exercise(
            url,
            password,
            output,
            f"sqlite:///{volume / 'open-node.db'}",
            certificate_spki=base64.b64encode(hashlib.sha256(spki).digest()).decode(),
            agent_identity=identity,
        )
        deployment.compose(
            "exec",
            "-T",
            "open-node",
            "open-node-admin",
            "reset-password",
            "--password-stdin",
            input=(password + "\n").encode(),
        )
        client.cookies.clear()
        login(client, password)
        print(
            "PASS production browser workflow and administrator recovery CLI",
            flush=True,
        )


def backup_restore(deployment, work, client, server_id, certificate_id, cert, key):
    identity = client.get("/api/v1/agents/identity").json()
    deployment.compose("stop")
    backup = deployment.compose(
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "--entrypoint",
        "tar",
        "open-node",
        "-C",
        "/var/lib/open-node",
        "-czf",
        "-",
        ".",
    ).stdout
    path = work / "backup.tar.gz"
    path.write_bytes(backup)
    path.chmod(0o600)
    deployment.up()
    preserved(client, server_id, certificate_id, cert, key)
    restored = Deployment(deployment.env["OPEN_NODE_IMAGE_TAG"])
    restored.env["OPEN_NODE_AGENT_IDENTITY_FILE"] = deployment.env["OPEN_NODE_AGENT_IDENTITY_FILE"]
    try:
        restored.compose("create", "--no-build")
        assert not list(restored.volume().iterdir())
        restored.compose(
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "tar",
            "open-node",
            "-C",
            "/var/lib/open-node",
            "-xzpf",
            "-",
            input=backup,
        )
        restored.up()
        assert (restored.volume() / "certificates/vault.key").read_bytes() == (
            deployment.volume() / "certificates/vault.key"
        ).read_bytes()
        with httpx.Client(
            base_url=f"http://127.0.0.1:{restored.port}", trust_env=False
        ) as restored_client:
            # Send the backed-up Secure cookie explicitly to this isolated loopback fixture.
            restored_client.headers["Cookie"] = "; ".join(
                f"{k}={v}" for k, v in client.cookies.items()
            )
            preserved(restored_client, server_id, certificate_id, cert, key)
            assert restored_client.get("/api/v1/agents/identity").json() == identity
        print(
            "PASS stopped-volume backup restores sessions, database and vault into a fresh install",
            flush=True,
        )
    finally:
        restored.close()


def upgrade_rollback(
    deployment, work, tag, client, server_id, certificate_id, cert, key
):
    candidate = deployment.project + "-candidate"
    broken = deployment.project + "-broken"
    context = work / "images"
    context.mkdir()
    dockerfile = context / "Dockerfile"
    try:
        dockerfile.write_text(
            f'FROM open-node:{tag}\nENV OPEN_NODE_APP_NAME="Open Node candidate"\n'
        )
        command(["docker", "build", "-t", f"open-node:{candidate}", str(context)])
        deployment.env["OPEN_NODE_IMAGE_TAG"] = candidate
        deployment.up()
        assert client.get("/healthz").json()["service"] == "Open Node candidate"
        preserved(client, server_id, certificate_id, cert, key)
        dockerfile.write_text(
            f"FROM open-node:{candidate}\nENV OPEN_NODE_FRONTEND_DIR=/missing-build\n"
        )
        command(["docker", "build", "-t", f"open-node:{broken}", str(context)])
        deployment.env["OPEN_NODE_IMAGE_TAG"] = broken
        deployment.compose("up", "-d", "--no-build")
        runtime.poll(
            "invalid release fails startup",
            lambda: deployment.inspect()["RestartCount"] > 0,
            timeout=30,
        )
        deployment.env["OPEN_NODE_IMAGE_TAG"] = tag
        deployment.up()
        assert client.get("/healthz").json()["service"] == "Open Node"
        preserved(client, server_id, certificate_id, cert, key)
        print(
            "PASS changed image upgrade and explicit rollback after failed startup",
            flush=True,
        )
    finally:
        deployment.env["OPEN_NODE_IMAGE_TAG"] = tag
        deployment.up()
        command(
            ["docker", "image", "rm", f"open-node:{candidate}", f"open-node:{broken}"],
            check=False,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    run(
        args.image_tag,
        args.nginx.resolve(),
        args.output.resolve(),
        args.agent_python.absolute(),
        args.xray_archive.resolve() if args.xray_archive else None,
    )
    print(
        f"PASS control-plane deployment ({time.monotonic() - started:.1f}s)", flush=True
    )
