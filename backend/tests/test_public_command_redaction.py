import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from open_node.api.redaction import public_command_read
from open_node.domain.inventory import (
    AgentCommandRead,
    AgentCommandStatus,
    XrayConfigSnapshotSource,
)
from open_node.services.inventory import ServerModel
from test_inventory import make_client


def command_read(*, method="GET", path, query="", body=None, result_body=None, error=None):
    now = datetime.now(UTC)
    return AgentCommandRead(
        id=uuid4(),
        server_id=uuid4(),
        request_id="redaction-test",
        method=method,
        path=path,
        query=query,
        body=body,
        timeout_ms=30_000,
        stream=False,
        status=AgentCommandStatus.SUCCEEDED,
        attempts=1,
        result_status=200,
        result_body=result_body,
        result_error=error,
        created_at=now,
        completed_at=now,
        updated_at=now,
    )


def test_public_xray_command_results_only_hide_control_plane_managed_secrets():
    warp_private_key = "warp-private-key-must-not-leak"
    ordinary_password = "operator-owned-proxy-password"
    outbounds = command_read(
        path="/api/child/outbounds",
        result_body={
            "success": True,
            "outbounds": [
                {
                    "tag": "warp-v4",
                    "protocol": "wireguard",
                    "settings": {"secretKey": warp_private_key},
                },
                {
                    "tag": "ordinary-proxy",
                    "protocol": "trojan",
                    "settings": {"servers": [{"password": ordinary_password}]},
                },
                {
                    "tag": "managed-egress:edge:node",
                    "protocol": "vless",
                    "settings": {"vnext": [{"users": [{"id": "managed-secret"}]}]},
                },
            ],
        },
    )

    public_outbounds = public_command_read(outbounds)

    assert public_outbounds.result_body == {
        "success": True,
        "outbounds": [
            {"tag": "warp-v4", "protocol": "wireguard"},
            {
                "tag": "ordinary-proxy",
                "protocol": "trojan",
                "settings": {"servers": [{"password": ordinary_password}]},
            },
            {"tag": "managed-egress:edge:node", "protocol": "vless"},
        ],
    }
    assert warp_private_key not in public_outbounds.model_dump_json()
    assert "managed-secret" not in public_outbounds.model_dump_json()
    assert ordinary_password in public_outbounds.model_dump_json()
    assert outbounds.result_body["outbounds"][0]["settings"]["secretKey"] == warp_private_key

    full_config = command_read(
        path="/api/child/xray/config",
        result_body={"success": True, "config": '{"password":"client-secret"}'},
    )
    selected_file = command_read(
        path="/api/child/xray/config-files",
        query="file=config.json",
        result_body={"success": True, "content": {"password": "file-secret"}},
    )
    file_listing = command_read(
        path="/api/child/xray/config-files",
        result_body={"success": True, "files": {"main": [{"name": "config.json"}]}},
    )
    malformed_listing_with_content = command_read(
        path="/api/child/xray/config-files",
        result_body={
            "success": True,
            "files": {"main": [{"name": "config.json", "content": "file-secret"}]},
        },
    )

    assert public_command_read(full_config).result_body == full_config.result_body
    assert public_command_read(selected_file).result_body == selected_file.result_body
    assert public_command_read(file_listing).result_body == file_listing.result_body
    assert (
        public_command_read(malformed_listing_with_content).result_body
        == malformed_listing_with_content.result_body
    )
    managed_config = command_read(
        path="/api/child/xray/config",
        result_body={
            "success": True,
            "config": json.dumps(
                {
                    "outbounds": [
                        {
                            "tag": "managed-egress:source:target",
                            "protocol": "vless",
                            "settings": {"vnext": [{"users": [{"id": "managed-id"}]}]},
                        }
                    ]
                }
            ),
        },
    )
    assert public_command_read(managed_config).result_body == {"redacted": True}
    assert "managed-id" not in public_command_read(managed_config).model_dump_json()
    warp_license = command_read(
        method="POST",
        path="/api/child/warp/license",
        body={"license": "warp-license-secret"},
        result_body={"success": True, "license": "warp-license-secret"},
    )
    assert public_command_read(warp_license).body == {"redacted": True}
    assert public_command_read(warp_license).result_body == {"redacted": True}
    assert "warp-license-secret" not in public_command_read(warp_license).model_dump_json()


def test_server_command_api_redacts_credentials_but_internal_queue_keeps_them(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "redaction-edge"}).json()
    server_id = created["server"]["id"]
    token = created["agent_token"]
    ordinary_client_secret = "ordinary-client-id-must-not-leak"

    created_command = client.post(
        f"/api/v1/servers/{server_id}/operations/inbounds/manage",
        json={
            "action": "add-client",
            "tag": "vless-in",
            "client": {"id": ordinary_client_secret, "email": "alice@example.com"},
        },
    )
    assert created_command.status_code == 201, created_command.text
    public_created = created_command.json()["command"]
    assert public_created["body"] == {"redacted": True}
    assert ordinary_client_secret not in created_command.text

    stored = client.app.state.inventory.list_commands(UUID(server_id))
    internal = next(command for command in stored if str(command.id) == public_created["id"])
    assert internal.body["client"]["id"] == ordinary_client_secret

    leased = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": token, "max_commands": 10},
    ).json()["commands"]
    internal_wire = next(command for command in leased if command["id"] == public_created["id"])
    assert internal_wire["body"]["client"]["id"] == ordinary_client_secret

    completed = client.post(
        f"/api/v1/agents/commands/{public_created['id']}/result",
        json={
            "token": token,
            "status": 200,
            "body": {
                "success": True,
                "inbounds": [
                    {
                        "tag": "vless-in",
                        "protocol": "vless",
                        "settings": {"clients": [{"id": ordinary_client_secret}]},
                    }
                ],
            },
        },
    )
    assert completed.status_code == 200, completed.text

    history = client.get(f"/api/v1/servers/{server_id}/commands")
    assert history.status_code == 200
    public_history = next(
        command
        for command in history.json()["commands"]
        if command["id"] == public_created["id"]
    )
    assert public_history["body"] == {"redacted": True}
    assert public_history["result_body"] == {
        "success": True,
        "inbounds": [{"tag": "vless-in", "protocol": "vless"}],
    }
    assert ordinary_client_secret not in history.text


def test_scan_endpoints_and_history_remove_only_managed_egress_accounts(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "scan-redaction"}).json()
    server_id = created["server"]["id"]
    payload = {
        "token": created["agent_token"],
        "xray_running": True,
        "xray_capabilities": {},
        "inbounds": [
            {
                "tag": "socks5-1080",
                "protocol": "socks",
                "settings": {
                    "accounts": [
                        {
                            "user": "open-node",
                            "pass": "managed-pass",
                            "email": "open_node_egress__source__target",
                        },
                        {
                            "user": "ordinary",
                            "pass": "ordinary-pass",
                            "email": "ordinary@example.com",
                        },
                    ]
                },
            }
        ],
    }
    reported = client.post("/api/v1/agents/scan", json=payload)
    assert reported.status_code == 200, reported.text

    latest = client.get(f"/api/v1/servers/{server_id}/scan/latest")
    assert latest.status_code == 200
    assert "managed-pass" not in latest.text
    assert "open_node_egress__" not in latest.text
    assert "ordinary-pass" in latest.text

    command = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={"method": "POST", "path": "/api/child/scan"},
    ).json()["command"]
    leased = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 10},
    )
    assert command["id"] in {item["id"] for item in leased.json()["commands"]}
    completed = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "inbounds": payload["inbounds"]},
        },
    )
    assert completed.status_code == 200, completed.text
    history = client.get(f"/api/v1/servers/{server_id}/commands")
    assert "managed-pass" not in history.text
    assert "ordinary-pass" in history.text


def test_snapshots_never_expose_config_while_restore_and_apply_use_internal_copy(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "snapshot-redaction"}).json()
    server_id = created["server"]["id"]
    full_config = json.dumps(
        {
            "inbounds": [
                {
                    "tag": "vless-in",
                    "protocol": "vless",
                    "settings": {"clients": [{"id": "snapshot-client-secret"}]},
                }
            ],
            "outbounds": [
                {
                    "tag": "warp-v4",
                    "protocol": "wireguard",
                    "settings": {"secretKey": "snapshot-warp-private-key"},
                }
            ],
        },
        separators=(",", ":"),
    )
    store = client.app.state.inventory
    with store._session() as session:
        server = session.get(ServerModel, server_id)
        snapshot = store._upsert_current_xray_config_snapshot(
            session,
            server,
            full_config,
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            datetime.now(UTC),
        )
        snapshot_id = snapshot.id
        session.commit()

    listed = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots?with_config=true"
    )
    recovery = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery?with_config=true"
    )
    assert listed.json()["snapshots"][0]["config"] is None
    assert recovery.json()["current"]["config"] is None
    assert "snapshot-client-secret" not in listed.text + recovery.text
    assert "snapshot-warp-private-key" not in listed.text + recovery.text

    restored = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/{snapshot_id}/restore"
    )
    assert restored.status_code == 201, restored.text
    restore_command = restored.json()["command"]
    assert restore_command["body"] == {"redacted": True}
    internal_restore = next(
        command
        for command in store.list_commands(UUID(server_id))
        if str(command.id) == restore_command["id"]
    )
    assert internal_restore.body == {"config": full_config}

    applied = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery/apply",
        json={"restart_xray": False, "merge_agent_only": False},
    )
    assert applied.status_code == 201, applied.text
    apply_commands = applied.json()["commands"]
    assert [command["body"] for command in apply_commands] == [
        {"redacted": True},
        {"redacted": True},
    ]
    internal_by_id = {
        str(command.id): command for command in store.list_commands(UUID(server_id))
    }
    assert internal_by_id[apply_commands[0]["id"]].body == {"config": full_config}
    assert internal_by_id[apply_commands[1]["id"]].body == {
        "config": full_config,
        "force": True,
    }
    assert full_config not in applied.text


def test_public_raw_commands_cannot_bypass_managed_egress_preview(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "egress-guard"}).json()
    server_id = created["server"]["id"]
    before = len(client.app.state.inventory.list_commands(UUID(server_id)))

    response = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={
            "method": "POST",
            "path": "/api/child/egress/apply",
            "body": {"config": {"outbounds": []}, "expected_config": {}},
        },
    )

    assert response.status_code == 403
    assert "previewed server egress workflow" in response.json()["detail"]
    assert len(client.app.state.inventory.list_commands(UUID(server_id))) == before


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/child/node-cleanup", "dedicated node management workflow"),
        ("/api/child/subscription-access", "dedicated user and plan workflows"),
    ],
)
def test_public_raw_commands_cannot_inject_internal_cleanup_or_access(
    tmp_path, path, detail
):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "internal-guard"}).json()
    server_id = created["server"]["id"]
    before = len(client.app.state.inventory.list_commands(UUID(server_id)))

    response = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={"method": "POST", "path": path, "body": {}},
    )

    assert response.status_code == 403
    assert detail in response.json()["detail"]
    assert len(client.app.state.inventory.list_commands(UUID(server_id))) == before


def test_server_command_history_can_be_bounded_to_requested_ids(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "filtered-history"}).json()
    server_id = created["server"]["id"]
    first = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={"method": "GET", "path": "/api/child/system/info"},
    ).json()["command"]
    second = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={"method": "GET", "path": "/api/child/traffic"},
    ).json()["command"]

    filtered = client.get(
        f"/api/v1/servers/{server_id}/commands",
        params=[("id", first["id"])],
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["commands"]] == [first["id"]]
    assert second["id"] not in filtered.text


@pytest.mark.parametrize("position", ["forward", "rollback"])
def test_public_change_sets_cannot_inject_managed_egress_apply(tmp_path, position):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": f"change-{position}"}).json()
    safe = {"method": "GET", "path": "/api/child/system/info"}
    internal = {
        "method": "POST",
        "path": "/api/child/egress/apply",
        "body": {"config": {"outbounds": []}, "expected_config": {}},
    }
    step = {
        "server_id": created["server"]["id"],
        "forward": internal if position == "forward" else safe,
        "rollback": internal if position == "rollback" else None,
    }

    response = client.post(
        "/api/v1/change-sets",
        json={"name": "forbidden managed egress", "steps": [step]},
    )

    assert response.status_code == 403
    assert "previewed server egress workflow" in response.json()["detail"]
    assert client.get("/api/v1/change-sets").json()["change_sets"] == []


@pytest.mark.parametrize("position", ["forward", "rollback"])
@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/child/node-cleanup", "dedicated node management workflow"),
        ("/api/child/subscription-access", "dedicated user and plan workflows"),
    ],
)
def test_public_change_sets_cannot_inject_internal_cleanup_or_access(
    tmp_path, position, path, detail
):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "internal-change"}).json()
    safe = {"method": "GET", "path": "/api/child/system/info"}
    internal = {"method": "POST", "path": path, "body": {}}
    step = {
        "server_id": created["server"]["id"],
        "forward": internal if position == "forward" else safe,
        "rollback": internal if position == "rollback" else None,
    }

    response = client.post(
        "/api/v1/change-sets",
        json={"name": "forbidden internal operation", "steps": [step]},
    )

    assert response.status_code == 403
    assert detail in response.json()["detail"]
    assert client.get("/api/v1/change-sets").json()["change_sets"] == []
