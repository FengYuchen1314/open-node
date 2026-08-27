from fastapi.testclient import TestClient
from open_node.main import create_app


def test_health_endpoint_is_available() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_declares_fastapi_vue_vuetify_stack() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stack"]["backend"] == "fastapi"
    assert payload["stack"]["frontend"] == "vue3-vuetify"
    assert payload["license_required"] is False
