import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from open_node.core.authority import TrustedAuthorityMiddleware
from open_node.core.config import Settings

ADMIN_PASSWORD = "trusted-authority-test-password"


def close_app(app) -> None:
    for store in (app.state.auth, app.state.inventory, app.state.certificates):
        for name in ("engine", "_engine"):
            engine = getattr(store, name, None)
            if engine is not None:
                engine.dispose()
    app.state.backup_writes.close()


def invoke(authorities, headers, scope_type="http"):
    called = []
    sent = []

    async def application(scope, _receive, send):
        called.append(scope)
        if scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
        else:
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": f"{scope_type}.disconnect"}

    async def send(message):
        sent.append(message)

    async def run():
        middleware = TrustedAuthorityMiddleware(application, authorities)
        await middleware(
            {
                "type": scope_type,
                "headers": headers,
                "scheme": "https" if scope_type == "http" else "wss",
                "path": "/",
            },
            receive,
            send,
        )

    asyncio.run(run())
    return called, sent


@pytest.mark.parametrize(
    ("configured", "name", "received"),
    [
        ("192.0.2.10:58090", b"host", b"192.0.2.10:58090"),
        ("panel.example.com:58090", b"host", b"PANEL.EXAMPLE.COM:58090"),
        ("[2001:db8::a]:58090", b"host", b"[2001:DB8::A]:58090"),
        ("[2001:db8::a]:58090", b":authority", b"[2001:db8::a]:58090"),
    ],
)
def test_exact_case_normalized_host_or_authority_is_accepted(configured, name, received):
    called, sent = invoke([configured], [(name, received)])
    assert len(called) == 1
    assert sent[0] == {"type": "http.response.start", "status": 204, "headers": []}


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"host", b"192.0.2.10:58090"), (b"host", b"192.0.2.10:58090")],
        [(b"host", b"192.0.2.10:58090"), (b":authority", b"192.0.2.10:58090")],
        [(b"host", b"192.0.2.10:58090, attacker.example")],
        [(b"host", b" 192.0.2.10:58090")],
        [(b"host", b"https://192.0.2.10:58090")],
        [(b"host", b"user@192.0.2.10:58090")],
        [(b"host", b"192.0.2.10:58090/path")],
        [(b"host", b"2001:db8::a:58090")],
        [(b"host", b"192.0.2.10:0")],
        [(b"host", b"192.0.2.10:058090")],
        [(b"host", b"192.0.2.10:" + (b"9" * 5000))],
        [(b"host", b"[2001:db8::a]:" + (b"9" * 5000))],
        [(b"host", b"192.0.2.11:58090")],
        [(b"host", b"\xff.example")],
    ],
)
def test_missing_duplicate_malformed_or_untrusted_authority_is_rejected(headers):
    called, sent = invoke(["192.0.2.10:58090"], headers)
    assert called == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    assert sent[1] == {"type": "http.response.body", "body": b"Invalid Host header"}


def test_websocket_authority_is_checked_before_accept():
    called, sent = invoke(
        ["[2001:db8::a]:58090"],
        [(b":authority", b"[2001:DB8::A]:58090")],
        "websocket",
    )
    assert len(called) == 1
    assert sent[0] == {"type": "websocket.accept"}

    called, sent = invoke(["[2001:db8::a]:58090"], [(b"host", b"[2001:db8::b]:58090")], "websocket")
    assert called == []
    assert sent == [{"type": "websocket.close", "code": 1008, "reason": "Untrusted authority"}]


def test_empty_configuration_keeps_development_and_test_clients_compatible():
    called, sent = invoke([], [])
    assert len(called) == 1
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    "authority",
    [
        "",
        "*",
        "https://panel.example.com",
        "panel.example.com/",
        "panel.example.com:0",
        "panel.example.com:65536",
        "panel.example.com:058090",
        "panel.example.com:" + ("9" * 5000),
        "[2001:db8::a]:" + ("9" * 5000),
        "2001:db8::1",
        "[192.0.2.10]:58090",
        "[fe80::1%25eth0]:58090",
        "999.999.999.999:58090",
        "bad_host.example:58090",
        " panel.example.com:58090",
    ],
)
def test_settings_reject_malformed_authorities(authority):
    with pytest.raises(ValueError):
        Settings(trusted_authorities=[authority])


def test_settings_normalizes_case_and_rejects_duplicates():
    settings = Settings(trusted_authorities=["PANEL.EXAMPLE.COM:58090", "[2001:DB8::A]:58090"])
    assert settings.trusted_authorities == [
        "panel.example.com:58090",
        "[2001:db8::a]:58090",
    ]
    with pytest.raises(ValueError, match="unique"):
        Settings(trusted_authorities=["panel.example.com", "PANEL.EXAMPLE.COM"])


def test_real_ip_port_login_cookie_origin_csrf_and_ipv6_authority(tmp_path: Path):
    from open_node.main import create_app

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'authority.db'}",
        control_state_dir=tmp_path,
        certificate_state_dir=tmp_path / "certificates",
        trusted_authorities=["192.0.2.10:58090", "[2001:db8::a]:58090"],
    )
    app = create_app(settings)
    app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    try:
        with TestClient(app, base_url="https://192.0.2.10:58090") as client:
            login = client.post(
                "/api/v1/auth/login",
                headers={
                    "Origin": "https://192.0.2.10:58090",
                    "X-Open-Node-Client": "browser",
                },
                json={"username": "admin", "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 200
            assert all(
                flag in login.headers["set-cookie"]
                for flag in ("Secure", "HttpOnly", "SameSite=strict", "Path=/")
            )
            client.headers["Origin"] = "https://192.0.2.10:58090"
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            assert client.post("/api/v1/servers", json={"name": "authority"}).status_code == 201
            assert client.get("/healthz", headers={"Host": "attacker.invalid"}).status_code == 400

        # This Starlette TestClient release cannot parse an IPv6 base_url itself;
        # override Host so both the middleware and the application's Origin check
        # still exercise the bracketed production authority.
        with TestClient(app, base_url="https://testserver") as client:
            login = client.post(
                "/api/v1/auth/login",
                headers={
                    "Host": "[2001:db8::a]:58090",
                    "Origin": "https://[2001:db8::a]:58090",
                    "X-Open-Node-Client": "browser",
                },
                json={"username": "admin", "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 200
    finally:
        close_app(app)
