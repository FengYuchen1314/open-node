from pathlib import Path

from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


def test_license_status_is_free_and_has_no_activation_path(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'open-node-test.db').as_posix()}"
    client = TestClient(create_app(Settings(database_url=database_url)))

    response = client.get("/api/v1/license/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["edition"] == "free"
    assert payload["license_required"] is False
    assert payload["paid_entitlements_enabled"] is False
    assert payload["external_license_server"] is None
    assert payload["feature_gates"] == []
