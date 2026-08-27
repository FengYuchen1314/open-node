from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def web(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><body><div id="app"></div></body></html>')
    (dist / "assets/app-a1b2c3.js").write_text("console.log('app');")
    (dist / ".env").write_text("PRIVATE=value")
    (dist / "api").mkdir()
    (dist / "api/unknown").write_text("must not shadow API routes")
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", frontend_dir=dist)
    return TestClient(create_app(settings)), dist


def test_spa_routes_cache_and_api_boundaries(web):
    client, _ = web
    for path in ("/", "/config", "/certificates", "/subscriptions"):
        response = client.get(path, headers={"accept": "text/html"})
        assert response.status_code == 200
        assert '<div id="app">' in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/config", headers={"accept": "application/json"}).status_code == 404
    assert client.get("/api/unknown", headers={"accept": "text/html"}).status_code == 404
    assert client.get("/api/v1/servers").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/openapi.json").headers["content-type"] == "application/json"
    assert client.post("/config").status_code == 405
    assert client.head("/config", headers={"accept": "text/html"}).content == b""


def test_assets_preserve_range_and_conditional_requests(web):
    client, _ = web
    response = client.get("/assets/app-a1b2c3.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    cached = client.get(
        "/assets/app-a1b2c3.js", headers={"if-none-match": response.headers["etag"]}
    )
    assert cached.status_code == 304
    partial = client.get("/assets/app-a1b2c3.js", headers={"range": "bytes=0-6"})
    assert partial.status_code == 206 and partial.content == b"console"
    assert client.get("/assets/missing.js", headers={"accept": "text/html"}).status_code == 404
    index = client.get("/changes", headers={"accept": "text/html"})
    assert index.status_code == 200
    cached_index = client.get("/", headers={"if-none-match": index.headers["etag"]})
    assert cached_index.status_code == 304
    assert cached_index.headers["cache-control"] == "no-cache"


def test_static_paths_and_websockets(web):
    client, dist = web
    outside = dist.parent / "private.txt"
    outside.write_text("private outside build")
    (dist / "assets/escape.txt").symlink_to(outside)
    for path in ("/.env", "/%2e%2e/private.txt", "/assets/escape.txt"):
        response = client.get(path, headers={"accept": "text/html"})
        assert response.status_code == 404
        assert "PRIVATE" not in response.text and "private outside" not in response.text
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/unknown-websocket"):
            pass
    assert error.value.code == 1008


def test_frontend_directory_is_optional_and_validated(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'plain.db'}")
    assert TestClient(create_app(settings)).get("/").status_code == 404
    with pytest.raises(ValidationError):
        Settings(frontend_dir=Path("relative"))
    with pytest.raises(ValidationError):
        Settings(frontend_dir=Path("/"))
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(ValueError, match="index.html"):
        create_app(Settings(database_url=settings.database_url, frontend_dir=missing))
