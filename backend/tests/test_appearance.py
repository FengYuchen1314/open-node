"""Public appearance, versioned updates and bounded owned images."""

import io

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.appearance import AppearanceAsset
from PIL import Image


@pytest.fixture
def app(tmp_path):
    result = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'appearance.db'}",
                                 certificate_state_dir=tmp_path / "certificates", _env_file=None))
    yield result
    for engine in (result.state.auth.engine, result.state.inventory._engine,
                   result.state.certificates.engine):
        engine.dispose()
    result.state.backup_writes.close()


def payload(revision=0, **values):
    return dict(
        expected_revision=revision, default_theme="dark",
        logo_url="https://cdn.example.test/logo.png?public=1",
        wallpaper_url="https://cdn.example.test/background.webp", license_required=False,
    ) | values


def picture(format="PNG", size=(16, 12)):
    stream = io.BytesIO()
    if format == "ICO":
        size = (16, 16)
    Image.new("RGB", size, "#1890ff").save(stream, format=format)
    return stream.getvalue()


def upload(client, slot, data, revision=0, content_type="application/octet-stream"):
    return client.post(f"/api/v1/system-settings/appearance/{slot}", content=data,
                       headers={"X-Appearance-Revision": str(revision),
                                "Content-Type": content_type})


def test_public_defaults_and_private_administration(app):
    client = TestClient(app)
    response = client.get("/api/v1/appearance")
    assert response.json() == dict(default_theme="light", logo_url="", wallpaper_url="",
                                   license_required=False)
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/system-settings/appearance").status_code == 401
    assert client.put("/api/v1/system-settings/appearance", json=payload()).status_code == 401
    assert client.post(
        "/api/v1/system-settings/appearance/logo", content=picture(),
    ).status_code == 401


def test_versioned_external_urls_and_theme_are_public(app):
    client = authenticated_client(app)
    before = client.get("/api/v1/system-settings/appearance").json()
    assert before == dict(default_theme="light", logo_url="", wallpaper_url="",
                          license_required=False, revision=0)
    saved = client.put("/api/v1/system-settings/appearance", json=payload())
    assert saved.status_code == 200
    assert saved.json() == dict(default_theme="dark",
                                logo_url="https://cdn.example.test/logo.png?public=1",
                                wallpaper_url="https://cdn.example.test/background.webp",
                                license_required=False, revision=1)
    public = TestClient(app).get("/api/v1/appearance")
    assert public.json() == {key: value for key, value in saved.json().items() if key != "revision"}
    assert client.put("/api/v1/system-settings/appearance", json=payload()).status_code == 409


@pytest.mark.parametrize("changes", [
    {"default_theme": "pixel"}, {"default_theme": 1}, {"license_required": True},
    {"expected_revision": 0.0}, {"logo_url": "http://example.test/logo.png"},
    {"logo_url": "https://user:password@example.test/logo.png"},
    {"logo_url": "https://localhost/logo.png"}, {"logo_url": "https://image.local/logo.png"},
    {"wallpaper_url": "/api/v1/appearance/assets/logo/" + "a" * 64},
    {"PRIVATE-EXTRA": "PRIVATE"},
])
def test_strict_safe_update_inputs(app, changes):
    client = authenticated_client(app)
    response = client.put("/api/v1/system-settings/appearance", json=payload(**changes))
    assert response.status_code == 422
    expected = ("appearance_asset_missing" if "wallpaper_url" in changes
                and str(changes["wallpaper_url"]).startswith("/")
                else "appearance_invalid_request")
    assert response.json()["code"] == expected
    assert "PRIVATE" not in response.text
    assert app.state.appearance.get_settings().revision == 0


@pytest.mark.parametrize("body,status", [
    ('{"logo_url":"PRIVATE","logo_url":"PRIVATE"}', 422),
    ('{"expected_revision":NaN}', 422), ("PRIVATE" * 4000, 413),
])
def test_unique_bounded_json_never_reflects_input(app, body, status):
    client = authenticated_client(app)
    response = client.put("/api/v1/system-settings/appearance", content=body,
                          headers={"Content-Type": "application/json"})
    assert response.status_code == status and "PRIVATE" not in response.text


@pytest.mark.parametrize("format,media", [
    ("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"),
    ("GIF", "image/gif"), ("ICO", "image/x-icon"),
])
def test_raster_upload_is_immutable_public_and_replaces_old(app, format, media):
    client = authenticated_client(app)
    data = picture(format)
    saved = upload(client, "logo", data)
    assert saved.status_code == 200
    url = saved.json()["logo_url"]
    image = TestClient(app).get(url)
    assert image.status_code == 200 and image.content == data
    assert image.headers["content-type"] == media
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["content-security-policy"].endswith("sandbox")
    newer = upload(client, "logo", picture("PNG", (9, 7)), revision=1)
    assert newer.status_code == 200 and newer.json()["logo_url"] != url
    assert TestClient(app).get(url).status_code == 404
    assert upload(client, "logo", data, revision=0).status_code == 409


def test_safe_svg_is_isolated_and_active_or_external_svg_is_rejected(app):
    client = authenticated_client(app)
    safe = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<path d="M0 0h10v10z"/></svg>'
    )
    response = upload(client, "logo", safe)
    assert response.status_code == 200
    image = TestClient(app).get(response.json()["logo_url"])
    assert image.content == safe and image.headers["content-type"] == "image/svg+xml"
    assert "default-src 'none'" in image.headers["content-security-policy"]
    for unsafe in [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://private.test/a"/></svg>',
        b'<!DOCTYPE svg [<!ENTITY x "boom">]><svg xmlns="http://www.w3.org/2000/svg"/>',
        b'<html>PRIVATE</html>',
    ]:
        rejected = upload(client, "wallpaper", unsafe, revision=1)
        assert rejected.status_code == 422 and "PRIVATE" not in rejected.text


@pytest.mark.parametrize("data,status", [
    (b"", 422), (b"not-an-image", 422), (b"\x89PNG\r\n\x1a\n" + b"x" * 100, 422),
    (b"x" * (2 * 1024 * 1024 + 1), 413),
])
def test_invalid_or_large_upload_is_bounded(app, data, status):
    response = upload(authenticated_client(app), "logo", data)
    assert response.status_code == status
    assert app.state.appearance.get_settings().revision == 0


def test_decoder_eof_is_a_safe_invalid_image(app, monkeypatch):
    def broken_decoder(*_args, **_kwargs):
        raise EOFError("PRIVATE-DECODER-DETAIL")

    monkeypatch.setattr("open_node.services.appearance_images.Image.open", broken_decoder)
    response = upload(authenticated_client(app), "logo", picture())
    assert response.status_code == 422
    assert response.json()["code"] == "appearance_invalid_image"
    assert "PRIVATE" not in response.text


def test_clear_uploaded_asset_and_corrupt_rows_fail_closed(app):
    client = authenticated_client(app)
    first = upload(client, "logo", picture())
    url = first.json()["logo_url"]
    cleared = client.put("/api/v1/system-settings/appearance", json=payload(
        1, logo_url="", wallpaper_url="", default_theme="system"))
    assert cleared.status_code == 200 and cleared.json()["logo_url"] == ""
    assert TestClient(app).get(url).status_code == 404
    current = upload(client, "logo", picture(), revision=2)
    with app.state.inventory._session() as db:
        db.get(AppearanceAsset, "logo").content = b"PRIVATE-CORRUPTION"
        db.commit()
    response = TestClient(app).get(current.json()["logo_url"])
    assert response.status_code == 404 and "PRIVATE" not in response.text


def test_slot_revision_headers_and_media_type_are_not_trusted(app):
    client = authenticated_client(app)
    data = picture()
    for path, headers in [
        ("other", {"X-Appearance-Revision": "0"}),
        ("logo", {}), ("logo", {"X-Appearance-Revision": "00"}),
        ("logo", {"X-Appearance-Revision": str(2**53)}),
    ]:
        response = client.post(f"/api/v1/system-settings/appearance/{path}", content=data,
                               headers=headers | {"Content-Type": "text/html"})
        assert response.status_code == 422
    accepted = upload(client, "logo", data, content_type="text/html")
    assert accepted.status_code == 200
    assert TestClient(app).get(accepted.json()["logo_url"]).headers["content-type"] == "image/png"
