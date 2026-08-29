import json

import pytest
from open_node.services.inventory import AgentScanResultModel
from sqlalchemy import select
from test_inventory import make_client, scan_result_payload


def runtime_draft(tmp_path, settings, protocol="snell", xray_capabilities=None):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "protocol-import"}).json()
    base = f"/api/v1/servers/{created['server']['id']}"
    response = client.post(
        "/api/v1/agents/scan",
        json={
            "token": created["agent_token"],
            **scan_result_payload(),
            "xray_capabilities": xray_capabilities or {},
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


@pytest.mark.parametrize(
    ("xray_capabilities", "expected"),
    [({}, False), ({"mieru_udp_target": 1}, True)],
)
def test_mieru_runtime_draft_and_import_use_current_capability(
    tmp_path,
    xray_capabilities,
    expected,
):
    client, base, draft = runtime_draft(
        tmp_path,
        {"transport": "tcp"},
        "mieru",
        xray_capabilities=xray_capabilities,
    )

    assert draft["draft"]["config"]["udp"] is expected
    imported = client.post(base + "/xray/runtime/nodes", json={"source_index": 0})
    assert imported.status_code == 201
    assert imported.json()["node"]["config"]["udp"] is expected


def test_mieru_runtime_draft_and_import_reject_stale_capability(tmp_path):
    client, base, initial = runtime_draft(
        tmp_path,
        {"transport": "tcp"},
        "mieru",
        xray_capabilities={"mieru_udp_target": 1},
    )
    assert initial["draft"]["config"]["udp"] is True
    with client.app.state.inventory._session() as session:
        scan = session.scalar(select(AgentScanResultModel))
        scan.updated_at = scan.updated_at.replace(year=2000)
        session.commit()

    stale = client.get(base + "/xray/runtime/node-drafts").json()["drafts"][0]
    assert stale["draft"]["config"]["udp"] is False
    imported = client.post(base + "/xray/runtime/nodes", json={"source_index": 0})
    assert imported.status_code == 201
    assert imported.json()["node"]["config"]["udp"] is False


def test_mieru_transport_and_udp_participate_in_reconciliation_and_sync(tmp_path):
    client, base, _ = runtime_draft(
        tmp_path,
        {"transport": "udp"},
        "mieru",
        xray_capabilities={"mieru_udp_target": 1},
    )
    server_id = base.rsplit("/", 1)[-1]
    stale = client.post(
        "/api/v1/nodes",
        json={
            "name": "Stale Mieru",
            "server_id": server_id,
            "protocol": "mieru",
            "inbound_tag": "original",
            "client_template": {"email": "{username}__original"},
            "config": {
                "type": "mieru",
                "server": "operator.example.com",
                "port": 4443,
                "transport": "TCP",
                "udp": False,
            },
        },
    ).json()["node"]

    reconciliation = client.get(base + "/xray/runtime/nodes/reconciliation").json()
    assert reconciliation["managed_entries"][0]["drifts"] == [
        {"field": "config.transport", "runtime_value": "UDP", "managed_value": "TCP"},
        {"field": "config.udp", "runtime_value": True, "managed_value": False},
    ]

    synced = client.post(
        base + f"/xray/runtime/nodes/{stale['id']}/sync",
        json={},
    )
    assert synced.status_code == 200
    payload = synced.json()
    assert payload["updated_fields"] == ["config.transport", "config.udp"]
    assert payload["node"]["config"]["transport"] == "UDP"
    assert payload["node"]["config"]["udp"] is True
    assert payload["drifts_after"] == []
