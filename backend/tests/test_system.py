from pathlib import Path

from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    database_url = f"sqlite:///{(tmp_path / 'open-node-test.db').as_posix()}"
    return TestClient(create_app(Settings(database_url=database_url)))


def test_health_endpoint_is_available(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_declares_fastapi_vue_vuetify_stack(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stack"]["backend"] == "fastapi"
    assert payload["stack"]["frontend"] == "vue3-vuetify"
    assert payload["license_required"] is False
    assert payload["short_links_enabled"] is False
