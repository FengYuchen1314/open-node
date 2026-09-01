"""Real external-source TLS, browser, saved exports and Mihomo on the isolated VPS.

This fixture enters a fresh network namespace before bringing up loopback and
assigning the VPS's public address *inside that namespace*. Nothing listens on
the host network; the application has no private-address bypass. Temporary
source certificates are trusted only by the fixture's application process.
"""

import argparse
import gzip
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "/api/v1/external-subscriptions"
FIXTURE_IP = "185.99.135.224"
MIHOMO_SHA256 = "8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a"
XRAY_SHA256 = "8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed"
STAGE = "preflight"


class FixtureFailure(AssertionError):
    """Only constant, fixture-owned descriptions can enter this exception."""


def require(condition, description):
    # Failure text must not contain provider URLs, response bodies or credentials.
    if not condition:
        raise FixtureFailure(description)


def private_file(path, content):
    encoded = content.encode() if isinstance(content, str) else content
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(check, description, process=None, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            require(process.poll() is None, description + ": process exited")
        try:
            if check():
                return
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(0.1)
    raise AssertionError(description + ": deadline exceeded")


@contextmanager
def process(work, name, arguments, **kwargs):
    with (work / (name + ".log")).open("wb") as log:
        child = subprocess.Popen(arguments, stdout=log, stderr=log, cwd=work, **kwargs)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)


@contextmanager
def http_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        require(not thread.is_alive(), "Fixture HTTP thread did not exit")


def certificate(work):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "External source fixture")]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(FIXTURE_IP))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = work / "source-ca.pem", work / "source-key.pem"
    private_file(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    private_file(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    return cert_path, key_path


class Provider:
    def __init__(self, work):
        self.path = "/private-subscription?token=" + secrets.token_urlsafe(24)
        self.body = b"proxies: []\n"
        self.mode = "yaml"
        self.calls = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                provider.calls.append(
                    {"path": self.path, "headers": dict(self.headers)}
                )
                if self.path != provider.path:
                    self.send_error(404)
                    return
                body = provider.body
                status = 200
                if provider.mode == "html":
                    body = b"<html>Provider error must not be echoed</html>"
                elif provider.mode == "empty":
                    body = b""
                elif provider.mode == "redirect":
                    status, body = 302, b""
                elif provider.mode == "gzip-bomb":
                    body = gzip.compress(b"x" * (2 * 1024 * 1024 + 1))
                elif provider.mode == "gzip":
                    body = gzip.compress(body)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/yaml")
                self.send_header(
                    "Subscription-Userinfo", "upload=7; download=11; total=999999"
                )
                self.send_header("Connection", "close")
                if provider.mode.startswith("gzip"):
                    self.send_header("Content-Encoding", "gzip")
                if status == 302:
                    self.send_header("Location", "https://127.0.0.1/private")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer((FIXTURE_IP, 0), Handler)
        self.ca, key = certificate(work)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.url = f"https://{FIXTURE_IP}:{self.server.server_port}{self.path}"

    def set_nodes(self, nodes):
        self.mode = "yaml"
        self.body = yaml.safe_dump({"proxies": nodes}, allow_unicode=True).encode()

    def check_requests(self):
        for call in self.calls:
            require(call["path"] == self.path, "Provider request path changed")
            headers = {key.lower(): value for key, value in call["headers"].items()}
            require(
                headers.get("user-agent") == "clash-meta/2.4.0",
                "Default user agent changed",
            )
            require(
                not {"authorization", "cookie", "referer", "proxy-authorization"}
                & headers.keys(),
                "Provider request leaked ambient credentials",
            )


def request(client, method, path, payload=None, status=200):
    response = client.request(
        method, path, **({"json": payload} if payload is not None else {})
    )
    require(
        response.status_code == status, "Unexpected API status for fixture operation"
    )
    return response


def login(client, password):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": password},
        headers={"X-Open-Node-Client": "browser"},
    )
    require(response.status_code == 200, "Fixture administrator login failed")
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def catalog(client, managed_port):
    server = request(
        client, "POST", "/api/v1/servers", {"name": "External test edge"}, 201
    ).json()
    node = request(
        client,
        "POST",
        "/api/v1/nodes",
        {
            "name": "Managed VLESS",
            "server_id": server["server"]["id"],
            "protocol": "vless",
            "inbound_tag": "managed",
            "client_template": {"id": "{username}", "email": "{username}"},
            "config": {
                "type": "vless",
                "server": "127.0.0.1",
                "port": managed_port,
                "tls": False,
            },
        },
        201,
    ).json()["node"]
    plan = request(
        client,
        "POST",
        "/api/v1/plans",
        {
            "name": "External test plan",
            "node_ids": [node["id"]],
            "traffic_limit_gb": 1,
        },
        201,
    ).json()["plan"]
    credentials, links = [], {}
    for username in ("alice", "bob"):
        request(
            client,
            "POST",
            "/api/v1/users",
            {
                "username": username,
                "display_name": username.title(),
            },
            201,
        )
        assigned = request(
            client,
            "POST",
            f"/api/v1/users/{username}/plan",
            {
                "plan_id": plan["id"],
                "queue_agent_commands": False,
            },
        ).json()
        require(
            not assigned["commands"], "Catalog fixture unexpectedly queued Agent work"
        )
        saved = request(client, "GET", f"/api/v1/users/{username}/credentials").json()
        credentials.append(saved["credentials"][0]["credential"]["id"])
        token = request(
            client, "POST", f"/api/v1/users/{username}/subscription-token", status=201
        )
        links[username] = "/api/v1/subscribe/" + token.json()["subscription"]["token"]
    return credentials, links


def upstream(name, port, credential):
    return {
        "name": name,
        "type": "vless",
        "server": "127.0.0.1",
        "port": port,
        "uuid": credential,
        "tls": False,
    }


def managed_state(client):
    return {
        path: request(client, "GET", path).json()
        for path in (
            "/api/v1/nodes",
            "/api/v1/plans",
            "/api/v1/users/alice/credentials",
            "/api/v1/users/alice/traffic",
            "/api/v1/users/bob/credentials",
        )
    }


def source_detail(client, source_id):
    return request(client, "GET", f"{PREFIX}/{source_id}").json()


def preview(client, source_id):
    source = source_detail(client, source_id)["source"]
    return request(
        client,
        "POST",
        f"{PREFIX}/{source_id}/previews",
        {
            "expected_revision": source["revision"],
        },
    ).json()


def confirmation(value, selected=None):
    return {
        "expected_revision": value["source_revision"],
        "accept_changes": True,
        "selected_node_ids": selected
        if selected is not None
        else [node["node_id"] for node in value["nodes"] if node["selectable"]],
    }


def capture(page, output, name, *, full_page=True):
    page.wait_for_function("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(
        path=str(output / (name + ".png")),
        full_page=full_page,
        mask=[page.locator('input[type="password"]')],
    )


def capture_dialog(page, dialog, output, name, *, fields=(), buttons=()):
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 1000})
        controls = [dialog.get_by_label(field, exact=True) for field in fields]
        controls.extend(
            dialog.get_by_role("button", name=button, exact=True) for button in buttons
        )
        for control in controls:
            control.scroll_into_view_if_needed()
            expect(control).to_be_visible()
            box = control.bounding_box()
            require(
                box
                and box["x"] >= 0
                and box["y"] >= 0
                and box["x"] + box["width"] <= width + 1
                and box["y"] + box["height"] <= 1001,
                "External dialog field or action is outside the viewport",
            )
        box = dialog.bounding_box()
        require(
            box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1,
            "External dialog exceeds viewport",
        )
        capture(page, output, f"{name}-{width}", full_page=False)


def browser_import(url, password, provider, client, output):
    global STAGE

    STAGE = "browser sign-in and source creation"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="zh-CN"
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda _error: errors.append("Browser page error"))
        page.goto(url + "/subscriptions")
        expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
        page.get_by_label("用户名", exact=True).fill("admin")
        page.get_by_label("密码", exact=True).fill(password)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(
            page.get_by_role("heading", name="订阅目录与用户绑定", exact=True)
        ).to_be_visible()
        expect(page).to_have_title("订阅管理 - Open Node")
        page.get_by_role(
            "button", name="管理外部订阅", exact=True
        ).click()
        page.get_by_role("button", name="添加外部订阅来源", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("combobox", name="外部订阅所属用户", exact=True).click()
        page.get_by_text("Alice (alice)", exact=True).click()
        dialog.get_by_label("外部订阅来源名称", exact=True).fill("Browser provider")
        secret = dialog.get_by_label("外部订阅链接", exact=True)
        expect(secret).to_have_attribute("type", "password")
        secret.fill(provider.url)
        capture_dialog(
            page,
            dialog,
            output,
            "create",
            fields=("外部订阅来源名称", "外部订阅链接"),
            buttons=("取消编辑外部订阅来源", "保存外部订阅来源"),
        )
        dialog.get_by_role("button", name="保存外部订阅来源", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(
            page.get_by_role("button", name="预览外部订阅来源", exact=True)
        ).to_be_visible()
        require(
            len(provider.calls) == 0, "Creating or opening a source fetched upstream"
        )
        source = request(client, "GET", PREFIX).json()["sources"][0]
        STAGE = "browser write-only source editor"
        page.get_by_role("button", name="编辑外部订阅来源", exact=True).click()
        expect(dialog.get_by_label("外部订阅链接", exact=True)).to_have_value("")
        capture_dialog(
            page,
            dialog,
            output,
            "edit-source",
            fields=("外部订阅来源名称", "外部订阅链接"),
            buttons=("取消编辑外部订阅来源", "保存外部订阅来源"),
        )
        dialog.get_by_role(
            "button", name="取消编辑外部订阅来源", exact=True
        ).click()
        expect(dialog).not_to_be_visible()
        page.get_by_role("button", name="预览外部订阅来源", exact=True).click()
        STAGE = "browser explicit preview and confirmation"
        require(len(provider.calls) == 0, "Opening a preview fetched upstream")
        dialog.get_by_role("button", name="抓取外部订阅预览", exact=True).click()
        expect(
            dialog.get_by_role(
                "checkbox", name="导入外部节点 Upstream A", exact=True
            )
        ).to_be_enabled()
        require(len(provider.calls) == 1, "Explicit preview must fetch exactly once")
        expect(
            dialog.get_by_role("button", name="确认外部订阅预览", exact=True)
        ).to_be_disabled()
        expect(
            dialog.get_by_role(
                "checkbox", name="导入外部节点 Unsupported", exact=True
            )
        ).to_be_disabled()
        dialog.get_by_role(
            "button", name="选择全部外部新节点", exact=True
        ).click()
        dialog.get_by_role(
            "checkbox", name="接受外部订阅预览变更", exact=True
        ).check()
        capture_dialog(
            page,
            dialog,
            output,
            "preview",
            fields=("接受外部订阅预览变更",),
            buttons=(
                "取消外部订阅预览",
                "关闭外部订阅预览",
                "确认外部订阅预览",
            ),
        )
        dialog.get_by_role(
            "button", name="确认外部订阅预览", exact=True
        ).click()
        expect(
            dialog.get_by_text("外部订阅预览已确认", exact=True)
        ).to_be_visible()
        dialog.get_by_role(
            "button", name="查询外部订阅确认结果", exact=True
        ).click()
        expect(
            dialog.get_by_text("外部订阅预览已确认", exact=True)
        ).to_be_visible()
        require(
            len(provider.calls) == 1,
            "Confirming or recovering a receipt fetched upstream",
        )
        dialog.get_by_role("button", name="关闭外部订阅预览", exact=True).click()
        expect(dialog).not_to_be_visible()
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            capture(page, output, f"saved-{width}")
        STAGE = "browser saved-node editor and narrow table actions"
        page.get_by_role(
            "button", name="编辑外部节点 Upstream A", exact=True
        ).click()
        capture_dialog(
            page,
            dialog,
            output,
            "edit-node",
            fields=("外部节点名称", "启用外部节点"),
            buttons=("取消编辑外部节点", "保存外部节点"),
        )
        dialog.get_by_role(
            "button", name="取消编辑外部节点", exact=True
        ).click()
        expect(dialog).not_to_be_visible()
        require(len(provider.calls) == 1, "Editing saved state fetched upstream")
        require(
            provider.url not in page.content(),
            "Saved source URL remains in browser DOM",
        )
        require(not errors, "Browser emitted a page error")
        context.close()
        browser.close()
    return source["id"]


def native_forwarding(
    work, args, exported, credentials, managed_port, upstream_port, upstream_uuid
):
    global STAGE

    STAGE = "native managed and external forwarding"
    marker = secrets.token_urlsafe(16).encode()

    class Echo(BaseHTTPRequestHandler):
        def do_GET(self):
            allowed = self.client_address[0] == "127.0.0.2"
            self.send_response(200 if allowed else 403)
            self.send_header("Content-Length", str(len(marker) if allowed else 0))
            self.end_headers()
            if allowed:
                self.wfile.write(marker)

        def log_message(self, *_args):
            pass

    inbounds = []
    for tag, port, identifiers in (
        ("managed", managed_port, credentials),
        ("external", upstream_port, [upstream_uuid]),
    ):
        inbounds.append(
            {
                "tag": tag,
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [{"id": value} for value in identifiers],
                },
            }
        )
    xray_config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [{"protocol": "freedom", "sendThrough": "127.0.0.2"}],
    }
    private_file(work / "xray.json", json.dumps(xray_config))
    socks, controller = free_port(), free_port()
    control_secret = secrets.token_urlsafe(24)
    config = yaml.safe_load(exported)
    require(len(config["proxies"]) >= 2, "Native export lacks managed/external nodes")
    config.update(
        {
            "mixed-port": socks,
            "allow-lan": False,
            "log-level": "warning",
            "external-controller": f"127.0.0.1:{controller}",
            "secret": control_secret,
        }
    )
    private_file(work / "mihomo.yaml", yaml.safe_dump(config, allow_unicode=True))
    checked = subprocess.run(
        [str(args.mihomo), "-t", "-d", str(work), "-f", str(work / "mihomo.yaml")],
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(checked.returncode == 0, "Mihomo rejected complete saved subscription")
    with ExitStack() as stack:
        echo = stack.enter_context(
            http_server(ThreadingHTTPServer(("127.0.0.1", 0), Echo))
        )
        target = f"http://127.0.0.1:{echo.server_port}/proof"
        core = stack.enter_context(
            process(
                work,
                "xray",
                [str(args.xray), "run", "-config", str(work / "xray.json")],
            )
        )
        native = stack.enter_context(
            process(
                work,
                "mihomo",
                [str(args.mihomo), "-d", str(work), "-f", str(work / "mihomo.yaml")],
            )
        )
        control = stack.enter_context(
            httpx.Client(
                base_url=f"http://127.0.0.1:{controller}",
                trust_env=False,
                headers={"Authorization": "Bearer " + control_secret},
            )
        )
        wait_for(
            lambda: control.get("/version").status_code == 200, "Mihomo startup", native
        )
        wait_for(
            lambda: (
                socket.create_connection(("127.0.0.1", upstream_port), 1).close()
                is None
            ),
            "Xray startup",
            core,
        )
        with httpx.Client(trust_env=False, timeout=3) as direct:
            require(
                direct.get(target).status_code == 403,
                "Echo target accepts direct traffic",
            )
        with httpx.Client(
            proxy=f"http://127.0.0.1:{socks}", trust_env=False, timeout=5
        ) as proxied:
            for name in ("Managed VLESS", "Upstream A"):
                require(
                    control.put("/proxies/Proxy", json={"name": name}).status_code
                    == 204,
                    "Native selector rejected a saved node",
                )
                require(
                    proxied.get(target).content == marker,
                    "Saved node did not forward through Xray",
                )
    print(
        "PASS real Mihomo managed/external forwarding and direct-traffic negative control",
        flush=True,
    )


def api_refresh_checks(client, provider, source_id, links, port, old_uuid, new_uuid):
    global STAGE

    STAGE = "real source refresh and failure preservation"
    before = request(client, "GET", links["alice"]).text
    state = managed_state(client)
    require(
        old_uuid in before
        and old_uuid not in request(client, "GET", links["bob"]).text,
        "External credential is absent or crosses subscriber ownership",
    )
    count = len(provider.calls)
    provider.set_nodes(
        [upstream("Upstream A", port, new_uuid), upstream("New C", port, new_uuid)]
    )
    value = preview(client, source_id)
    require(
        len(provider.calls) == count + 1, "Refresh issued multiple provider requests"
    )
    changes = {node["upstream_name"]: node for node in value["nodes"]}
    require(
        changes["Upstream A"]["changed_fields"] == ["uuid"],
        "Credential diff is incorrect",
    )
    require(changes["Old B"]["change"] == "missing", "Missing-node diff is absent")
    require(
        request(client, "GET", links["alice"]).text == before,
        "Preview changed active export",
    )
    payload = confirmation(value, [changes["New C"]["node_id"]])
    confirm_path = f"{PREFIX}/{source_id}/previews/{value['id']}/confirm"
    receipt = request(client, "POST", confirm_path, payload).json()
    require(
        request(client, "POST", confirm_path, payload).json() == receipt,
        "Receipt retry is not stable",
    )
    require(
        (receipt["imported_count"], receipt["updated_count"], receipt["missing_count"])
        == (1, 1, 1),
        "Refresh confirmation counts are incorrect",
    )
    exported = request(client, "GET", links["alice"]).text
    require(
        new_uuid in exported and old_uuid not in exported,
        "Confirmed credential did not rotate",
    )
    require(
        "Old B" not in exported and "New C" in exported,
        "Confirmed node selection is incorrect",
    )
    for mode in ("html", "empty", "redirect", "gzip-bomb"):
        provider.mode = mode
        revision = source_detail(client, source_id)["source"]["revision"]
        response = request(
            client,
            "POST",
            f"{PREFIX}/{source_id}/previews",
            {
                "expected_revision": revision,
            },
            422,
        )
        require(
            provider.url not in response.text and new_uuid not in response.text,
            "Invalid upstream response disclosed a credential",
        )
        require(
            request(client, "GET", links["alice"]).text == exported,
            "Failed refresh changed the confirmed snapshot",
        )
    provider.mode = "gzip"
    compressed = preview(client, source_id)
    require(
        all(
            node["change"] == "unchanged"
            for node in compressed["nodes"]
            if node["upstream_name"] != "Old B"
        ),
        "Valid compressed response changed its semantic snapshot",
    )
    request(client, "DELETE", f"{PREFIX}/{source_id}/previews/{compressed['id']}")
    require(
        managed_state(client) == state,
        "External refresh changed managed nodes, credentials or billing",
    )
    provider.check_requests()
    print(
        "PASS real TLS preview/confirm, credential rotation, ownership, "
        "gzip and failure preservation",
        flush=True,
    )
    return exported


def environment(database, ca):
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if not key.startswith("OPEN_NODE_")
        },
        "PYTHONPATH": str(ROOT / "backend/app"),
        "OPEN_NODE_DATABASE_URL": f"sqlite:///{database}",
        "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
        "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
        "OPEN_NODE_FRONTEND_DIR": str(ROOT / "frontend/dist"),
        "OPEN_NODE_CERTIFICATE_STATE_DIR": str(database.parent / "certificates"),
        "SSL_CERT_FILE": str(ca),
    }


@contextmanager
def backend(work, listener, env, name):
    with process(
        work,
        name,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "open_node.main:app",
            "--fd",
            str(listener.fileno()),
            "--no-access-log",
        ],
        env=env,
        pass_fds=(listener.fileno(),),
    ) as child:
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with httpx.Client(trust_env=False, timeout=2) as health:
            wait_for(
                lambda: health.get(url + "/api/v1/meta").status_code == 200,
                "Fixture backend startup",
                child,
            )
        yield url


def run(args):
    global STAGE

    require(
        sys.platform == "linux" and os.geteuid() == 0,
        "Run only as root on the isolated Linux VPS",
    )
    require(
        os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
        "Host network is forbidden",
    )
    links = json.loads(
        subprocess.run(
            ["ip", "-j", "link", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    )
    # A sysfs mount can retain the host's network-namespace view after unshare.
    # Netlink inspects the namespace this process actually inhabits.
    require(
        {link["ifname"] for link in links} == {"lo"},
        "Only a fresh loopback-only network namespace is allowed",
    )
    for binary, expected in ((args.mihomo, MIHOMO_SHA256), (args.xray, XRAY_SHA256)):
        require(
            binary.is_absolute() and binary.is_file(),
            "A pinned absolute native binary is required",
        )
        require(
            hashlib.sha256(binary.read_bytes()).hexdigest() == expected,
            "Native binary digest mismatch",
        )
    require(
        (ROOT / "frontend/dist/index.html").is_file(),
        "Build the production frontend first",
    )
    output = args.output.resolve()
    require(
        not output.exists() or (output.is_dir() and not any(output.iterdir())),
        "Use a new empty evidence directory",
    )
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    require(output.stat().st_mode & 0o077 == 0, "Evidence directory must be private")
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    subprocess.run(
        ["ip", "address", "add", FIXTURE_IP + "/32", "dev", "lo"], check=True
    )
    with tempfile.TemporaryDirectory(prefix="open-node-external-runtime-") as temporary:
        work = Path(temporary)
        provider = Provider(work)
        database = work / "open-node.db"
        env = environment(database, provider.ca)
        STAGE = "fixture administrator setup"
        password = secrets.token_urlsafe(32)
        admin = subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            input=password + "\n",
            text=True,
            env=env,
            cwd=work,
            capture_output=True,
            timeout=30,
            check=False,
        )
        require(admin.returncode == 0, "Fixture administrator setup failed")
        with socket.socket() as listener, http_server(provider.server):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with httpx.Client(base_url=url, trust_env=False, timeout=35) as client:
                with backend(work, listener, env, "backend-initial"):
                    STAGE = "fixture catalog setup"
                    login(client, password)
                    managed_port, upstream_port = free_port(), free_port()
                    credentials, links = catalog(client, managed_port)
                    original, rotated = str(uuid4()), str(uuid4())
                    provider.set_nodes(
                        [
                            upstream("Upstream A", upstream_port, original),
                            upstream("Old B", upstream_port, original),
                            {
                                "name": "Unsupported",
                                "type": "ssh",
                                "server": "example.org",
                                "port": 22,
                            },
                        ]
                    )
                    baseline = managed_state(client)
                    source_id = browser_import(url, password, provider, client, output)
                    first = request(client, "GET", links["alice"]).text
                    require(
                        managed_state(client) == baseline,
                        "Browser import changed managed state",
                    )
                    native_forwarding(
                        work,
                        args,
                        first,
                        credentials,
                        managed_port,
                        upstream_port,
                        original,
                    )
                    final = api_refresh_checks(
                        client,
                        provider,
                        source_id,
                        links,
                        upstream_port,
                        original,
                        rotated,
                    )
                    native_forwarding(
                        work,
                        args,
                        final,
                        credentials,
                        managed_port,
                        upstream_port,
                        rotated,
                    )
                    for path in (PREFIX, f"{PREFIX}/{source_id}"):
                        response = request(client, "GET", path)
                        require(
                            response.headers.get("cache-control") == "no-store",
                            "Source response is cacheable",
                        )
                        require(
                            response.headers.get("referrer-policy") == "no-referrer",
                            "Source referrer policy missing",
                        )
                        require(
                            all(
                                value not in response.text
                                for value in (provider.url, original, rotated)
                            ),
                            "Ordinary source response disclosed a secret",
                        )
                # Cold backup includes the database and its separate external key.
                STAGE = "cold database and key backup"
                backup = work / "backup"
                backup.mkdir(mode=0o700)
                shutil.copy2(database, backup / "open-node.db")
                shutil.copytree(
                    work / "external-subscriptions", backup / "external-subscriptions"
                )
                for path in work.glob("open-node.db*"):
                    require(
                        all(
                            value.encode() not in path.read_bytes()
                            for value in (provider.url, original, rotated)
                        ),
                        "Database stores external material in plaintext",
                    )
                key_path = work / "external-subscriptions/vault.key"
                require(
                    key_path.is_relative_to(work) and not key_path.is_symlink(),
                    "Unexpected fixture key path",
                )
                key_path.unlink()
                STAGE = "missing-key fail-closed"
                with backend(work, listener, env, "backend-missing-key"):
                    request(client, "GET", links["alice"], status=404)
                    revision = source_detail(client, source_id)["source"]["revision"]
                    request(
                        client,
                        "POST",
                        f"{PREFIX}/{source_id}/previews",
                        {"expected_revision": revision},
                        503,
                    )
                    require(
                        not key_path.exists(),
                        "Missing external key was silently replaced",
                    )
                restored = work / "restored"
                shutil.copytree(backup, restored)
                restored_env = environment(restored / "open-node.db", provider.ca)
                STAGE = "cold database and key restoration"
                with backend(work, listener, restored_env, "backend-restored"):
                    require(
                        request(client, "GET", links["alice"]).text == final,
                        "Cold database/key restore changed saved subscription",
                    )
                    require(
                        source_detail(client, source_id)["source"]["node_count"] == 3,
                        "Restored authenticated source state is incorrect",
                    )
                print(
                    "PASS original session, missing-key fail-closed and "
                    "cold database/key restoration",
                    flush=True,
                )
        report = {
            "result": "passed",
            "ui_locale": "zh-CN",
            "source_files": {
                str(path.relative_to(ROOT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(
                    [
                        *(ROOT / "backend/app").rglob("*.py"),
                        *(
                            path
                            for path in (ROOT / "frontend/dist").rglob("*")
                            if path.is_file()
                        ),
                    ]
                )
            },
            "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "network": "private namespace; loopback only; no host listener",
            "input": "real HTTPS Clash/Mihomo YAML",
            "viewports": [1440, 390, 320],
            "provider_fetches": len(provider.calls),
            "mihomo_sha256": MIHOMO_SHA256,
            "xray_sha256": XRAY_SHA256,
            "screenshots": 15,
            "checks": [
                "browser create/preview/select/confirm/receipt",
                "1440/390/320 source and node form/footer geometry",
                "write-only source URL",
                "managed/external native forwarding",
                "direct-traffic rejection",
                "owner isolation",
                "credential rotation/new/missing diff",
                "preview preserves active export",
                "HTML/empty/redirect/gzip-bomb preserve snapshot",
                "valid gzip",
                "idempotent receipt",
                "managed credentials/catalog/ledger unchanged",
                "encrypted database",
                "missing-key no regeneration",
                "cold database/key restore",
                "original session",
            ],
        }
        private_file(output / "report.json", json.dumps(report, indent=2) + "\n")
    print(
        "PASS external subscriptions end-to-end; all owned runtime processes cleaned up",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo", type=Path, required=True)
    parser.add_argument("--xray", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-netns", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if not options.private_netns:
        require(
            sys.platform == "linux" and os.geteuid() == 0,
            "Run only on the isolated Linux VPS",
        )
        raise SystemExit(
            subprocess.call(
                [
                    "unshare",
                    "--net",
                    "--",
                    sys.executable,
                    str(Path(__file__).resolve()),
                    *sys.argv[1:],
                    "--private-netns",
                ]
            )
        )
    try:
        run(options)
    except Exception as failure:  # noqa: BLE001
        # Playwright may include a filled password or received URL in its error
        # message/call log. Keep only stage/type/source locations, never its
        # message, traceback rendering, locals or provider-controlled values.
        diagnostic = {
            "result": "failed",
            "stage": STAGE,
            "type": type(failure).__name__,
            "frames": [
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(failure.__traceback__)
            ],
        }
        if isinstance(failure, FixtureFailure):
            diagnostic["check"] = str(failure)
        print(json.dumps(diagnostic), file=sys.stderr, flush=True)
        raise SystemExit(1) from None
