import json

import pytest
from test_inventory import make_client, scan_result_payload


def runtime_draft(tmp_path, settings, protocol="snell"):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "protocol-import"}).json()
    base = f"/api/v1/servers/{created['server']['id']}"
    response = client.post(
        "/api/v1/agents/scan",
        json={
            "token": created["agent_token"],
            **scan_result_payload(),
            "inbounds": [
                {"tag": "original", "protocol": protocol, "port": 4443, "settings": settings}
            ],
        },
    )
    assert response.status_code == 200
    response = client.get(base + "/xray/runtime/node-drafts")
    assert response.status_code == 200
    return client, base, response.json()["drafts"][0]


@pytest.mark.parametrize("mode", ["default", "unshaped"])
def test_snell_import_uses_first_user_transport_without_credentials(tmp_path, mode):
    _, _, draft = runtime_draft(
        tmp_path,
        {
            "version": 4,
            "users": [
                {"email": "legacy", "psk": "never-import-this-secret", "version": 6, "v6Mode": mode}
            ],
        },
    )
    assert draft["create_available"] is True
    assert draft["draft"]["client_template"] == {
        "email": "{username}__original",
        "version": 6,
        "v6Mode": mode,
    }
    assert draft["draft"]["config"]["version"] == 6
    assert draft["draft"]["config"]["mode"] == mode
    assert "obfs-opts" not in draft["draft"]["config"]
    assert "never-import-this-secret" not in json.dumps(draft)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("mode", ["none", "http", "tls"])
def test_snell_import_preserves_shared_obfs_and_empty_metadata(tmp_path, empty, mode):
    options = {"version": 5, "obfsMode": mode, "obfsHost": "example.org"}
    settings = {"users": [], **options} if empty else {"users": [options]}
    _, _, draft = runtime_draft(tmp_path, settings)
    assert draft["create_available"] is True
    assert draft["draft"]["client_template"] == {
        "email": "{username}__original",
        **options,
    }
    config = draft["draft"]["config"]
    assert config["version"] == 5
    if mode == "none":
        assert "obfs-opts" not in config
    else:
        assert config["obfs-opts"] == {"mode": mode, "host": "example.org"}


@pytest.mark.parametrize(
    "users,warning",
    [
        ([{"version": 6, "v6Mode": "unsafe-raw"}], "snell_unauthenticated_mode"),
        ([{"version": 6}, {"version": 4}], "snell_mixed_transport_options"),
        ([{"version": 4, "obfsMode": "http"}, {"version": 4}], "snell_mixed_transport_options"),
    ],
)
def test_snell_unsafe_or_mixed_transport_cannot_be_imported(tmp_path, users, warning):
    client, base, draft = runtime_draft(tmp_path, {"users": users})
    assert draft["create_available"] is False
    assert warning in draft["warnings"]
    response = client.post(base + "/xray/runtime/nodes", json={"source_index": 0})
    assert response.status_code == 400
    assert warning in response.json()["detail"]


@pytest.mark.parametrize("transport,expected", [("tcp", "TCP"), ("UDP", "UDP"), (None, "TCP")])
def test_mieru_import_normalizes_client_transport(tmp_path, transport, expected):
    _, _, draft = runtime_draft(tmp_path, {"transport": transport}, "mieru")
    assert draft["create_available"] is True
    assert draft["draft"]["config"]["transport"] == expected
    assert draft["draft"]["config"]["udp"] is False
