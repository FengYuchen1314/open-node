#!/usr/bin/env python3
"""Exercise the production public-probe bundle through a local Cloudflare runtime.

Run only on the isolated Linux VPS candidate, after building the probe bundle::

    backend/.venv/bin/python scripts/vps/smoke-public-probe-worker.py \
        --output /tmp/open-node-public-probe-worker-smoke

Requires the candidate's locked probe-worker Wrangler/Miniflare dependencies and the
backend browser extra with Playwright Chromium. No Cloudflare login, deploy,
real backend, database, production container, or public listener is used. The
fixture and Miniflare bind only ephemeral loopback ports; generated configuration,
credentials, runtime state, and logs live in a private temporary directory.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_PREFIX = "/fixture-control"
PUBLIC_PREFIX = "/api/v1/public/"
HTTP_ROUTES = {
    "/api/probe": "probe-servers",
    "/api/series": "probe-series",
    "/api/targets": "probe-targets",
    "/api/public/probe-servers": "probe-servers",
    "/api/public/probe-series": "probe-series",
    "/api/public/probe-targets": "probe-targets",
    "/api/v1/public/probe-servers": "probe-servers",
    "/api/v1/public/probe-series": "probe-series",
    "/api/v1/public/probe-targets": "probe-targets",
}
WS_ROUTES = (
    "/api/stream",
    "/api/public/probe-ws",
    "/api/v1/public/probe-ws",
)
ALLOWED_BROWSER_API = {PUBLIC_PREFIX + value for value in HTTP_ROUTES.values()}
ALLOWED_BROWSER_API.add(PUBLIC_PREFIX + "probe-ws")
COOKIE_HEADERS = {
    "Set-Cookie": "upstream_operator=must-not-reach-browser; HttpOnly; Path=/",
    "Set-Cookie2": "upstream_secondary=must-not-reach-browser; Path=/",
}


class Fixture:
    """Thread-safe controls and non-secret observations for the origin fixture."""

    def __init__(self, token: str, worker_host: str):
        self.token = token
        self.worker_host = worker_host
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.http_title = "Anonymous Worker Probe"
        self.color_mode = "light"
        self.pause_http = False
        self.messages: list[str] = []
        self.close_generation = 0
        self.reject_ws = False
        self.accepted_ws = 0
        self.rejected_ws = 0
        self.active_ws = 0
        self.sent_frames = 0
        self.redirect_traps = 0

    def record(self, connection, kind: str) -> dict:
        headers = connection.headers
        event = {
            "kind": kind,
            "path": connection.url.path,
            "query": parse_qs(connection.url.query),
            "authorization_present": "authorization" in headers,
            "cookie_present": "cookie" in headers,
            "token_valid": secrets.compare_digest(
                headers.get("x-mmwx-probe-token", ""), self.token
            ),
            "forwarded_host_valid": headers.get("x-forwarded-host") == self.worker_host,
        }
        with self.lock:
            self.events.append(event)
        return event

    @staticmethod
    def authorized(event: dict) -> bool:
        return (
            event["token_valid"]
            and event["forwarded_host_valid"]
            and not event["authorization_present"]
            and not event["cookie_present"]
        )

    @staticmethod
    def ping() -> dict:
        return {
            "key": "fixture-target",
            "label": "Fixture target",
            "current_ms": 28,
            "loss_pct": 0,
            "buckets": [{"ms": value, "loss": 0} for value in (22, 31, 24, 28)],
        }

    def payload(self, title: str | None = None) -> dict:
        with self.lock:
            return {
                "enabled": True,
                "title": title or self.http_title,
                "description": "Anonymous read-only fixture served by the production Worker.",
                "refresh_interval_sec": 1,
                "has_access_token": True,
                "require_access_token": True,
                "appearance": {"theme": "open-node", "color_mode": self.color_mode},
                "show_resource_heatmap": True,
                "show_traffic_quota": True,
                "show_health_score": True,
                "servers": [
                    {
                        "name": "worker-edge",
                        "region": "Singapore",
                        "region_country": "SG",
                        "online": True,
                        "cpu_pct": 23,
                        "mem_used": 1_073_741_824,
                        "mem_total": 4_294_967_296,
                        "disk_used": 10_737_418_240,
                        "disk_total": 42_949_672_960,
                        "upload_speed": 1024,
                        "download_speed": 4096,
                        "traffic_used_total": 536_870_912,
                        "traffic_limit": 10_737_418_240,
                        "ping": [self.ping()],
                    },
                    {"name": "offline-edge", "region_country": "US", "online": False},
                ],
                "license_required": False,
            }

    def targets(self) -> dict:
        return {
            "success": True,
            "targets": [
                {
                    "key": "fixture-target",
                    "label": "Fixture target",
                    "server_count": 1,
                    "healthy_count": 1,
                    "average_ms": 28,
                    "best_ms": 28,
                    "worst_ms": 28,
                    "average_loss_pct": 0,
                    "servers": [
                        {
                            "server_index": 0,
                            "server_name": "worker-edge",
                            "region": "SG",
                            "current_ms": 28,
                            "loss_pct": 0,
                            "buckets": self.ping()["buckets"],
                        }
                    ],
                }
            ],
            "bucket_sec": 60,
            "generated_at": int(time.time()),
            "license_required": False,
        }

    def series(self, metric: str) -> dict:
        series = self.ping()
        if metric == "system":
            now = int(time.time())
            values = {
                "cpu_pct": (18, 25, 20, 23),
                "mem_used": (800_000_000, 900_000_000, 950_000_000, 1_073_741_824),
                "mem_total": (4_294_967_296,) * 4,
                "upload_speed": (800, 1300, 1100, 1024),
                "download_speed": (3800, 4600, 4200, 4096),
                "cumulative_up": (1000, 2000, 3000, 4000),
                "cumulative_down": (4000, 8000, 12000, 16000),
            }
            series = {
                key: [
                    {"t": now - 180 + index * 60, "value": value}
                    for index, value in enumerate(samples)
                ]
                for key, samples in values.items()
            }
        return {
            "success": True,
            "series": series,
            "all_series": [self.ping()] if metric == "ping" else [],
            "bucket_sec": 60,
            "generated_at": int(time.time()),
            "license_required": False,
        }

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "events": list(self.events),
                "accepted_ws": self.accepted_ws,
                "rejected_ws": self.rejected_ws,
                "active_ws": self.active_ws,
                "sent_frames": self.sent_frames,
                "redirect_traps": self.redirect_traps,
            }

    def count(self, resource: str) -> int:
        with self.lock:
            return sum(
                item["kind"] == "http"
                and item["path"] == UPSTREAM_PREFIX + PUBLIC_PREFIX + resource
                and self.authorized(item)
                for item in self.events
            )


def fixture_app(state: Fixture) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health():
        return {"fixture": "open-node-public-probe-worker"}

    @app.get(UPSTREAM_PREFIX + "/redirect-trap")
    async def redirect_trap():
        with state.lock:
            state.redirect_traps += 1
        return JSONResponse({"unexpected": True}, status_code=500)

    @app.get(UPSTREAM_PREFIX + PUBLIC_PREFIX + "{resource}")
    async def public(resource: str, request: Request):
        event = state.record(request, "http")
        if not state.authorized(event):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if resource not in {"probe-servers", "probe-series", "probe-targets"}:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if request.query_params.get("redirect") == "1":
            return JSONResponse(
                {"redirect": True},
                status_code=302,
                headers={
                    **COOKIE_HEADERS,
                    "Location": UPSTREAM_PREFIX + "/redirect-trap",
                },
            )
        deadline = time.monotonic() + 15
        while state.pause_http and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if state.pause_http:
            return JSONResponse({"detail": "Fixture pause timed out"}, status_code=503)
        body = (
            state.payload()
            if resource == "probe-servers"
            else state.targets()
            if resource == "probe-targets"
            else state.series(request.query_params.get("metric", "ping"))
        )
        return JSONResponse(body, headers=COOKIE_HEADERS)

    @app.websocket(UPSTREAM_PREFIX + PUBLIC_PREFIX + "probe-ws")
    async def stream(websocket: WebSocket):
        event = state.record(websocket, "ws")
        with state.lock:
            reject = state.reject_ws or not state.authorized(event)
            if reject:
                state.rejected_ws += 1
        if reject:
            await websocket.close(code=1008)
            return
        await websocket.accept(
            headers=[
                (key.lower().encode(), value.encode())
                for key, value in COOKIE_HEADERS.items()
            ]
        )
        with state.lock:
            state.accepted_ws += 1
            state.active_ws += 1
            generation = state.close_generation
            cursor = len(state.messages)
        try:
            await websocket.send_json(state.payload())
            with state.lock:
                state.sent_frames += 1
            while True:
                with state.lock:
                    close = state.close_generation != generation
                    messages = state.messages[cursor:]
                    cursor = len(state.messages)
                if close:
                    await websocket.close(
                        code=1012, reason="fixture reconnect exercise"
                    )
                    return
                for message in messages:
                    await websocket.send_text(message)
                    with state.lock:
                        state.sent_frames += 1
                try:
                    received = await asyncio.wait_for(websocket.receive(), timeout=0.05)
                    if received["type"] == "websocket.disconnect":
                        return
                except TimeoutError:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            with state.lock:
                state.active_ws -= 1

    return app


def wait_until(predicate, description: str, *, page: Page | None = None, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if page is None:
            time.sleep(0.1)
        else:
            page.wait_for_timeout(100)
    raise AssertionError(f"Timed out: {description}")


def hardened(headers, *, dynamic: bool = True):
    normalized = {key.lower(): value for key, value in headers.items()}
    assert "set-cookie" not in normalized, "Worker leaked an upstream Set-Cookie"
    assert "set-cookie2" not in normalized, "Worker leaked an upstream Set-Cookie2"
    assert normalized.get("x-content-type-options") == "nosniff", normalized
    if dynamic:
        assert normalized.get("cache-control") == "no-store", normalized


class AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        value = (
            attrs.get("src")
            if tag == "script"
            else attrs.get("href")
            if tag == "link"
            else None
        )
        if value:
            assert value.startswith("/assets/"), f"Not a production asset path: {value}"
            self.paths.add(value)


def http_boundary(url: str, origin: str, assets: Path, state: Fixture) -> dict:
    manifest = {}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        direct = client.get(origin + UPSTREAM_PREFIX + PUBLIC_PREFIX + "probe-servers")
        assert direct.status_code == 404, (
            "Fixture origin must reject anonymous direct access"
        )
        html = client.get(url + "/")
        assert html.status_code == 200
        hardened(html.headers, dynamic=False)
        assert html.content == (assets / "index.html").read_bytes(), (
            "Not the production HTML"
        )
        assert "/@vite/client" not in html.text and "/main.ts" not in html.text
        parser = AssetLinks()
        parser.feed(html.text)
        assert any(path.endswith(".js") for path in parser.paths)
        assert any(path.endswith(".css") for path in parser.paths)
        for path in sorted(parser.paths):
            response = client.get(url + path)
            assert response.status_code == 200, path
            hardened(response.headers, dynamic=False)
            expected_path = (assets / path.lstrip("/")).resolve()
            assert expected_path.is_relative_to(assets)
            assert response.content == expected_path.read_bytes(), path
            manifest[path] = hashlib.sha256(response.content).hexdigest()

        sentinels = {
            "Authorization": "Bearer " + secrets.token_urlsafe(24),
            "Cookie": "open_node_session=" + secrets.token_urlsafe(24),
            "X-MMwx-Probe-Token": "caller-must-not-select-origin-token",
            "X-Forwarded-Host": "caller.invalid",
        }
        for index, (path, resource) in enumerate(HTTP_ROUTES.items()):
            case = f"http-alias-{index}"
            response = client.get(
                url + path + "/",
                params={"case": case, "server": "0", "range": "6h", "target": "a+b&c"},
                headers=sentinels,
            )
            assert response.status_code == 200, (path, response.status_code)
            hardened(response.headers)
            assert state.token not in response.text, (
                "Worker exposed its token in API data"
            )
            assert state.token not in json.dumps(dict(response.headers)), (
                "Token in response headers"
            )
            assert response.json()["license_required"] is False
            observed = [
                item
                for item in state.snapshot()["events"]
                if item["query"].get("case") == [case]
            ]
            assert len(observed) == 1 and state.authorized(observed[0]), observed
            assert observed[0]["path"] == UPSTREAM_PREFIX + PUBLIC_PREFIX + resource
            assert observed[0]["query"]["target"] == ["a+b&c"]

        before = len(state.snapshot()["events"])
        for path in (
            "/api",
            "/api/unknown",
            "/api/v1/auth/session",
            "/api/v1/servers",
            "/api/v1/probe/tasks",
            "/api/v1/probe/access-token",
            "/api/v1/public/probe-settings",
            "/api/public/probe-settings",
        ):
            response = client.get(url + path)
            assert response.status_code == 404, path
            assert response.headers.get("content-type", "").startswith(
                "application/json"
            )
            assert response.json()["success"] is False
            hardened(response.headers)
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            response = client.request(method, url + "/api/probe")
            assert response.status_code == 405, (method, response.status_code)
            assert response.headers["allow"] == "GET"
            hardened(response.headers)
        assert len(state.snapshot()["events"]) == before, (
            "Private or write route reached origin"
        )

        redirect = client.get(url + "/api/probe?redirect=1")
        assert redirect.status_code == 302
        assert redirect.headers["location"] == UPSTREAM_PREFIX + "/redirect-trap"
        hardened(redirect.headers)
        assert state.snapshot()["redirect_traps"] == 0, (
            "Worker followed an upstream redirect"
        )
        login = client.get(url + "/login?next=must-not-be-preserved")
        assert login.status_code == 302
        assert login.headers["location"] == origin + UPSTREAM_PREFIX + "/login"
        hardened(login.headers, dynamic=False)
    print(
        "PASS production assets, public aliases, credential stripping and private/write denial",
        flush=True,
    )
    return manifest


def websocket_boundary(url: str, state: Fixture):
    for index, path in enumerate(WS_ROUTES):
        case = f"ws-alias-{index}"
        with connect(
            url.replace("http://", "ws://", 1) + path + "/?case=" + case,
            additional_headers={
                "Authorization": "Bearer fixture-browser-authorization",
                "Cookie": "open_node_session=fixture-browser-cookie",
                "X-MMwx-Probe-Token": "fixture-wrong-caller-token",
                "X-Forwarded-Host": "caller.invalid",
            },
            proxy=None,
            open_timeout=10,
            close_timeout=3,
            ping_interval=None,
        ) as connection:
            response = connection.response
            assert response.status_code == 101, (path, response.status_code)
            hardened(dict(response.headers))
            key = connection.request.headers["Sec-WebSocket-Key"]
            expected = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()
            ).decode()
            assert response.headers["Sec-WebSocket-Accept"] == expected
            frame = connection.recv(timeout=10)
            assert state.token not in frame, (
                "Worker exposed its token in a WebSocket frame"
            )
            assert state.token not in json.dumps(dict(response.headers)), (
                "Token in upgrade headers"
            )
            assert json.loads(frame)["enabled"] is True
            observed = [
                item
                for item in state.snapshot()["events"]
                if item["query"].get("case") == [case]
            ]
            assert len(observed) == 1 and state.authorized(observed[0]), observed
            assert observed[0]["path"] == UPSTREAM_PREFIX + PUBLIC_PREFIX + "probe-ws"
    wait_until(
        lambda: state.snapshot()["active_ws"] == 0, "fixture handshake clients close"
    )
    print(
        "PASS real WebSocket upgrades, aliases and bidirectional credential stripping",
        flush=True,
    )


def check_layout(page: Page):
    page.wait_for_function("document.documentElement.scrollWidth <= innerWidth + 1")
    refresh = page.get_by_role(
        "button", name="刷新探针状态", exact=True
    ).bounding_box()
    assert refresh and refresh["width"] >= 28, (
        "Probe refresh control was squeezed on a narrow screen"
    )
    title = (
        page.get_by_role("region", name="目标对比", exact=True)
        .locator(".ant-card-head-title")
        .first
    )
    assert title.evaluate(
        "element => element.scrollWidth <= element.clientWidth + 1"
    ), "Target comparison title was clipped"


def assert_theme(page: Page, dark: bool):
    page.wait_for_function(
        """dark => {
        const surface = document.querySelector('.probe-page');
        const heading = surface?.querySelector('h1');
        if (!surface || !heading) return false;
        const channels = value => (value.match(/[\\d.]+/g) || []).map(Number);
        const background = channels(getComputedStyle(surface).backgroundColor);
        const foreground = channels(getComputedStyle(heading).color);
        if (background.length < 3 || (background[3] ?? 1) < 0.99) return false;
        return dark ? background.slice(0, 3).every(value => value < 64)
            && foreground.slice(0, 3).every(value => value > 200)
            : background.slice(0, 3).every(value => value > 230)
            && foreground.slice(0, 3).every(value => value < 64);
    }""",
        arg=dark,
    )


def screenshot_pair(page: Page, output: Path, name: str):
    for width, height, suffix in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        check_layout(page)
        page.screenshot(
            path=output / f"{name}-{suffix}.png", full_page=True, animations="disabled"
        )
        if width == 390 and name == "public-probe-series":
            drawer = page.locator(".probe-detail-drawer")
            last_chart = drawer.locator(".probe-trend-grid svg").last
            last_chart.scroll_into_view_if_needed()
            expect(last_chart).to_be_in_viewport(ratio=1)
            page.screenshot(
                path=output / f"{name}-mobile-scrolled.png",
                animations="disabled",
            )
            drawer.get_by_role(
                "heading", name="worker-edge", exact=True
            ).scroll_into_view_if_needed()
    page.set_viewport_size({"width": 1440, "height": 1000})


def assert_public_surface(page: Page):
    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
    expect(page.get_by_text("公开只读视图", exact=True)).to_be_visible()
    text = page.locator("body").inner_text()
    for forbidden in (
        "Administrator Sign-In",
        "Probe settings",
        "Worker access",
        "Scheduled probes",
        "Dispatch due",
        "Sign out",
        "Create server",
        "Generate access token",
        "管理员登录",
        "探针设置",
        "Worker 访问",
        "定时探针",
        "下发到期任务",
        "退出登录",
        "创建服务器",
        "生成服务器安装命令",
        "生成访问令牌",
    ):
        assert forbidden not in text, (
            f"Administrator UI leaked into public bundle: {forbidden}"
        )


def publish_and_observe(page: Page, state: Fixture, title: str, *, malformed=False):
    # Pause fixture HTTP replies briefly, so a matching heading proves that the
    # real browser received and applied a WebSocket frame rather than an HTTP poll.
    with state.lock:
        state.pause_http = True
        if malformed:
            state.messages.append("{this is not valid JSON")
        state.messages.append(json.dumps(state.payload(title)))
    try:
        expect(page.get_by_role("heading", level=1)).to_have_text(title, timeout=8000)
    finally:
        with state.lock:
            state.http_title = title
            state.pause_http = False


def browser_surface(url: str, state: Fixture, output: Path) -> dict:
    errors, requests, responses, frames, upgrades = [], [], [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="zh-CN"
        )
        page = context.new_page()
        page.set_default_timeout(10_000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "credential_present": any(
                        key
                        in (
                            "authorization",
                            "cookie",
                            "x-mmwx-probe-token",
                        )
                        for key in request.all_headers()
                    ),
                }
            ),
        )

        def observe_response(response):
            path = urlparse(response.url).path
            responses.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "headers": response.all_headers(),
                    "token_in_body": (
                        state.token.encode() in response.body()
                        if path.startswith("/api/") and not path.endswith("/probe-ws")
                        else False
                    ),
                }
            )

        page.on("response", observe_response)
        page.on(
            "websocket",
            lambda stream: stream.on(
                "framereceived", lambda frame: frames.append(frame)
            ),
        )
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.on(
            "Network.webSocketHandshakeResponseReceived",
            lambda event: upgrades.append(event["response"]),
        )
        try:
            assert context.cookies() == [], "Browser must start anonymous"
            before_ws = state.snapshot()["accepted_ws"]
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_role("heading", level=1)).to_have_text(
                "Anonymous Worker Probe"
            )
            expect(
                page.get_by_text("实时连接已建立", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_role(
                    "button", name="查看 worker-edge 的探针详情", exact=True
                )
            ).to_be_visible()
            assert_public_surface(page)
            wait_until(
                lambda: len(frames) >= 1, "browser receives initial snapshot", page=page
            )
            assert state.snapshot()["accepted_ws"] == before_ws + 1
            initial = state.snapshot()
            servers_before = state.count("probe-servers")
            targets_before = state.count("probe-targets")
            with state.lock:
                state.http_title = "HTTP polling survives an idle live stream"
            expect(page.get_by_role("heading", level=1)).to_have_text(
                "HTTP polling survives an idle live stream", timeout=12_000
            )
            wait_until(
                lambda: (
                    state.count("probe-servers") >= servers_before + 2
                    and state.count("probe-targets") >= targets_before + 2
                ),
                "repeated status and target polling while socket is open",
                page=page,
            )
            current = state.snapshot()
            assert (
                current["active_ws"] == 1
                and current["accepted_ws"] == initial["accepted_ws"]
            )
            assert current["sent_frames"] == initial["sent_frames"], (
                "Socket must remain idle"
            )
            expect(
                page.get_by_text("实时连接已建立", exact=True)
            ).to_be_visible()
            print(
                "PASS anonymous browser and continuous polling while WebSocket stays silent",
                flush=True,
            )

            publish_and_observe(
                page,
                state,
                "Live frame applied after malformed input",
                malformed=True,
            )
            assert any(
                isinstance(frame, str) and "Live frame applied" in frame
                for frame in frames
            )
            assert state.snapshot()["accepted_ws"] == initial["accepted_ws"]
            compare = page.get_by_role("region", name="目标对比", exact=True)
            compare.get_by_text("6 小时", exact=True).click()
            expect(compare.get_by_role("radio", name="6 小时", exact=True)).to_be_checked()
            wait_until(
                lambda: any(
                    event["path"].endswith("/probe-targets")
                    and event["query"].get("range") == ["6h"]
                    and "case" not in event["query"]
                    for event in state.snapshot()["events"]
                ),
                "target range reaches origin through Worker",
                page=page,
            )
            screenshot_pair(page, output, "public-probe")

            row = page.locator(".server-table tbody tr").filter(has_text="worker-edge")
            row.get_by_role(
                "button", name="查看 worker-edge 的探针详情", exact=True
            ).click()
            drawer = page.locator(".probe-detail-drawer")
            expect(
                drawer.get_by_role("heading", name="worker-edge", exact=True)
            ).to_be_visible()
            expect(drawer.locator(".probe-trend-grid polyline").first).to_be_visible()
            drawer.get_by_text("系统", exact=True).click()
            expect(
                drawer.get_by_role("radio", name="系统", exact=True)
            ).to_be_checked()
            drawer.get_by_text("24 小时", exact=True).click()
            expect(drawer.get_by_role("radio", name="24 小时", exact=True)).to_be_checked()
            wait_until(
                lambda: any(
                    event["path"].endswith("/probe-series")
                    and event["query"].get("metric") == ["system"]
                    and event["query"].get("range") == ["24h"]
                    and event["query"].get("server") == ["0"]
                    for event in state.snapshot()["events"]
                ),
                "system series request reaches origin through Worker",
                page=page,
            )
            expect(drawer.locator(".probe-trend-grid polyline").first).to_be_visible()
            screenshot_pair(page, output, "public-probe-series")
            drawer.get_by_role("button", name="关闭", exact=True).click()
            print(
                "PASS target range selection and real ping/system series on desktop/mobile",
                flush=True,
            )

            before = state.snapshot()
            servers_before = state.count("probe-servers")
            targets_before = state.count("probe-targets")
            with state.lock:
                state.reject_ws = True
                state.close_generation += 1
                state.http_title = "Polling survives WebSocket disconnect"
            expect(
                page.get_by_text("实时连接已建立", exact=True)
            ).not_to_be_visible()
            expect(page.get_by_role("heading", level=1)).to_have_text(
                "Polling survives WebSocket disconnect", timeout=10_000
            )
            wait_until(
                lambda: (
                    state.snapshot()["rejected_ws"] > before["rejected_ws"]
                    and state.count("probe-servers") > servers_before
                    and state.count("probe-targets") > targets_before
                ),
                "failed reconnect retains both polling routes",
                page=page,
            )
            with state.lock:
                state.reject_ws = False
            wait_until(
                lambda: state.snapshot()["accepted_ws"] > before["accepted_ws"],
                "automatic successful WebSocket reconnect",
                page=page,
            )
            expect(
                page.get_by_text("实时连接已建立", exact=True)
            ).to_be_visible()
            publish_and_observe(page, state, "Reconnected live snapshot")
            assert any(
                isinstance(frame, str) and "Reconnected live snapshot" in frame
                for frame in frames
            )
            screenshot_pair(page, output, "public-probe-reconnected")
            print(
                "PASS polling through disconnect/rejected retry and automatic live reconnect",
                flush=True,
            )

            with state.lock:
                state.color_mode = "dark"
            publish_and_observe(page, state, "Dark Probe surface")
            assert_theme(page, True)
            screenshot_pair(page, output, "public-probe-dark")
            with state.lock:
                state.color_mode = "system"
            page.emulate_media(color_scheme="dark")
            publish_and_observe(page, state, "System Probe appearance")
            assert_theme(page, True)
            page.emulate_media(color_scheme="light")
            assert_theme(page, False)
            with state.lock:
                state.color_mode = "light"
            publish_and_observe(page, state, "Reconnected live snapshot")
            assert_theme(page, False)
            print(
                "PASS built-in light/dark/system themes and readable surface colors",
                flush=True,
            )

            # A deep link receives this same read-only SPA, not an operator shell.
            page.goto(url + "/access", wait_until="networkidle")
            expect(page.get_by_role("heading", level=1)).to_have_text(
                "Reconnected live snapshot"
            )
            assert_public_surface(page)
            assert not errors, errors
            assert context.cookies() == [], (
                "Upstream cookies were installed by HTTP or WebSocket"
            )
            storage = page.evaluate(
                "JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})"
            )
            assert state.token not in storage and state.token not in page.content()
            for request in requests:
                parsed = urlparse(request["url"])
                assert parsed.netloc == urlparse(url).netloc, request["url"]
                assert request["method"] == "GET", request
                assert not request["credential_present"], (
                    "Public bundle used credentials"
                )
                if parsed.path == "/api" or parsed.path.startswith("/api/"):
                    assert parsed.path in ALLOWED_BROWSER_API, request
                assert state.token not in request["url"], (
                    "Worker token exposed in browser URL"
                )
            for response in responses:
                path = urlparse(response["url"]).path
                assert not response["token_in_body"], (
                    "Worker token exposed in an HTTP body"
                )
                assert state.token not in json.dumps(response["headers"]), (
                    "Token in response headers"
                )
                if path.endswith("/probe-ws"):
                    allowed_statuses = {101, 403}
                elif path.startswith("/api/"):
                    allowed_statuses = {200}
                else:
                    # The real asset service may revalidate cached JS/CSS after
                    # the deep-link navigation. Dynamic APIs must never use 304.
                    allowed_statuses = {200, 304}
                assert response["status"] in allowed_statuses, (
                    response["url"],
                    response["status"],
                )
                hardened(response["headers"], dynamic="/api/" in path)
            accepted_upgrades = [item for item in upgrades if item["status"] == 101]
            assert len(accepted_upgrades) >= 2, (
                "No browser-level reconnect handshake evidence"
            )
            for response in accepted_upgrades:
                hardened(response["headers"])
                assert state.token not in json.dumps(response["headers"]), (
                    "Token in upgrade headers"
                )
            assert len(frames) >= 4
            assert all(
                state.token not in frame
                if isinstance(frame, str)
                else state.token.encode() not in frame
                for frame in frames
            ), "Worker token exposed in a browser WebSocket frame"
            result = {
                "browser_requests": [
                    {"path": urlparse(item["url"]).path, "method": item["method"]}
                    for item in requests
                ],
                "browser_websocket_upgrades": len(accepted_upgrades),
                "browser_frames": len(frames),
                "browser_page_errors": errors,
                "browser_cookies": [],
            }
        except BaseException:
            (output / "browser-errors.json").write_text(json.dumps(errors, indent=2))
            if errors:
                print("Browser errors: " + json.dumps(errors), file=sys.stderr)
            try:
                page.screenshot(
                    path=output / "public-probe-failure.png",
                    full_page=True,
                    timeout=5000,
                )
            except (PlaywrightError, OSError):
                print("Could not capture the failure screenshot", file=sys.stderr)
            raise
        finally:
            with state.lock:
                state.pause_http = False
                state.reject_ws = False
            context.close()
            browser.close()
    print(
        "PASS probe-only deep link, anonymous request surface and secret-free browser state",
        flush=True,
    )
    return result


@contextmanager
def origin_runtime(state: Fixture):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(
            uvicorn.Config(
                fixture_app(state),
                log_level="error",
                access_log=False,
                ws_ping_interval=None,
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            wait_until(lambda: server.started, "loopback origin fixture starts")
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=5)
            assert not thread.is_alive(), "Origin fixture did not stop"


def stop_process_group(process: subprocess.Popen):
    # Only the new process group created for this exact smoke is addressed.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
    finally:
        # A CLI parent can exit before a child workerd process. Reap any remaining
        # members of only this smoke's dedicated process group as well.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@contextmanager
def smoke_evidence(output: Path, work: Path, report: dict, state: Fixture):
    """Keep diagnostics even if dry-run fails, times out, or cleanup fails."""
    try:
        yield
    except BaseException as error:
        report["status"] = "failed"
        report["failure_type"] = type(error).__name__
        for name in ("runtime.log", "debug.log"):
            source = work / name
            if source.is_file():
                contents = source.read_text(errors="replace")[-30_000:].replace(
                    state.token, "[redacted]"
                )
                (output / name).write_text(contents)
                if name == "runtime.log":
                    print(contents, file=sys.stderr)
        raise
    else:
        report["status"] = "passed"
        report["stage"] = "complete"
    finally:
        report["fixture"] = state.snapshot()
        (output / "report.json").write_text(json.dumps(report, indent=2))


def run(output: Path, assets_override: Path | None, repository: Path):
    if sys.platform != "linux":
        raise SystemExit(
            "Run this smoke on the isolated Linux VPS candidate, not locally."
        )
    worker = repository.resolve() / "probe-worker"
    wrangler = worker / "node_modules" / "wrangler" / "bin" / "wrangler.js"
    runner = Path(__file__).with_name("run-probe-worker-runtime.mjs")
    node = shutil.which("node")
    if not wrangler.is_file() or not node or not runner.is_file():
        raise SystemExit(
            "Install candidate probe-worker dependencies with npm ci first."
        )
    source_config = json.loads((worker / "wrangler.jsonc").read_text())
    assets = (
        assets_override or worker / source_config["assets"]["directory"]
    ).resolve()
    if not (assets / "index.html").is_file():
        raise SystemExit(
            "Build the production probe bundle with npm --prefix frontend run build:probe."
        )
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(
            "Choose a new or empty --output directory; existing artifacts are preserved."
        )
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        worker_port = reserved.getsockname()[1]
    url = f"http://127.0.0.1:{worker_port}"
    token = secrets.token_urlsafe(32)
    state = Fixture(token, urlparse(url).netloc)
    report = {
        "status": "failed",
        "stage": "configuration",
        "runtime": "Miniflare/workerd (Wrangler dry-run bundle)",
        "assets": str(assets),
        "versions": {
            name: json.loads(
                (worker / "node_modules" / name / "package.json").read_text()
            )["version"]
            for name in ("wrangler", "miniflare", "workerd")
        },
    }
    with tempfile.TemporaryDirectory(prefix="open-node-probe-worker-") as temporary:
        work = Path(temporary)
        with (
            smoke_evidence(output, work, report, state),
            origin_runtime(state) as origin,
        ):
            # Carry the repository's entry point, compatibility date and complete
            # asset-routing configuration into a private, local-only config. Do
            # not copy account identifiers, remote bindings or real .dev.vars.
            config = {
                "name": "open-node-probe-smoke",
                "main": str((worker / source_config["main"]).resolve()),
                "compatibility_date": source_config["compatibility_date"],
                "assets": {**source_config["assets"], "directory": str(assets)},
                "vars": {"MMWX_ORIGIN": origin + UPSTREAM_PREFIX + "/"},
                "observability": {"enabled": False},
            }
            if "compatibility_flags" in source_config:
                config["compatibility_flags"] = source_config["compatibility_flags"]
            config_path = work / "wrangler.json"
            config_path.write_text(json.dumps(config))
            config_path.chmod(0o600)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("CLOUDFLARE_", "CF_", "WRANGLER_", "OPEN_NODE_"))
            }
            env.update(
                {
                    "CI": "1",
                    "NO_COLOR": "1",
                    "WRANGLER_SEND_METRICS": "false",
                    "WRANGLER_LOG_SANITIZE": "true",
                    "WRANGLER_LOG_PATH": str(work / "debug.log"),
                    "XDG_CONFIG_HOME": str(work / "config"),
                    "XDG_CACHE_HOME": str(work / "cache"),
                }
            )
            with (work / "runtime.log").open("w+") as log:
                compiled = work / "compiled"
                # Use the deployment CLI only for its official offline build.
                # Its development ProxyController has a known fatal transient-
                # connection regression; it is not part of a deployed Worker.
                report["stage"] = "wrangler_dry_run"
                build = subprocess.Popen(
                    [
                        node,
                        str(wrangler),
                        "deploy",
                        "--dry-run",
                        "--outdir",
                        str(compiled),
                        "--config",
                        str(config_path),
                    ],
                    cwd=work,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    build.wait(timeout=60)
                finally:
                    stop_process_group(build)
                bundle = compiled / "index.js"
                if build.returncode or not bundle.is_file():
                    raise AssertionError(
                        "Wrangler dry-run did not produce the Worker bundle"
                    )
                report["worker_bundle_sha256"] = hashlib.sha256(
                    bundle.read_bytes()
                ).hexdigest()
                runtime_config = work / "runtime.json"
                runtime_config.write_text(
                    json.dumps(
                        {
                            "workerDirectory": str(worker),
                            "bundlePath": str(bundle),
                            "workDirectory": str(work),
                            "port": worker_port,
                            "wrangler": config,
                            "bindings": {
                                "MMWX_ORIGIN": config["vars"]["MMWX_ORIGIN"],
                                "PROBE_TOKEN": token,
                            },
                        }
                    )
                )
                runtime_config.chmod(0o600)
                report["stage"] = "runtime_start"
                process = subprocess.Popen(
                    [
                        node,
                        str(runner),
                        str(runtime_config),
                    ],
                    cwd=work,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    with httpx.Client(timeout=1, trust_env=False) as client:

                        def ready():
                            if process.poll() is not None:
                                raise AssertionError(
                                    "Local Miniflare exited before readiness"
                                )
                            try:
                                return client.get(url).status_code == 200
                            except httpx.HTTPError:
                                return False

                        wait_until(
                            ready, "local Miniflare serves built assets", timeout=45
                        )
                    report["stage"] = "http_boundary"
                    report["asset_sha256"] = http_boundary(url, origin, assets, state)
                    report["stage"] = "websocket_boundary"
                    websocket_boundary(url, state)
                    report["stage"] = "browser"
                    report.update(browser_surface(url, state, output))
                    wait_until(
                        lambda: state.snapshot()["active_ws"] == 0,
                        "browser sockets close",
                    )
                    report["stage"] = "cleanup"
                except BaseException:
                    report["runtime_exit_code"] = process.poll()
                    raise
                finally:
                    stop_process_group(process)
    print(
        f"PASS public Probe Worker production-bundle browser gate; evidence: {output}",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--assets",
        type=Path,
        help="Override the built asset directory from wrangler.jsonc",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=ROOT,
        help="Read the Worker source and built assets from this isolated checkout",
    )
    arguments = parser.parse_args()
    run(arguments.output, arguments.assets, arguments.repository)
