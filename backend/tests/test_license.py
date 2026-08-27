from fastapi.testclient import TestClient
from open_node.main import create_app


def test_license_status_is_free_and_has_no_activation_path() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/license/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["edition"] == "free"
    assert payload["license_required"] is False
    assert payload["paid_entitlements_enabled"] is False
    assert payload["external_license_server"] is None
    assert payload["feature_gates"] == []
