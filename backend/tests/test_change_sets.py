from pathlib import Path

from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=sqlite_url(tmp_path / "open-node-test.db"))
    return TestClient(create_app(settings))


def test_change_set_plans_dispatches_and_rolls_back_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    edge_a = client.post("/api/v1/servers", json={"name": "change-edge-a"}).json()
    edge_b = client.post("/api/v1/servers", json={"name": "change-edge-b"}).json()
    server_a_id = edge_a["server"]["id"]
    server_b_id = edge_b["server"]["id"]

    create_response = client.post(
        "/api/v1/change-sets",
        json={
            "name": "Rotate edge configs",
            "description": "Apply two server config writes with rollback commands.",
            "rollback_on_failure": True,
            "steps": [
                {
                    "server_id": server_a_id,
                    "label": "Write xray config",
                    "forward": {
                        "method": "post",
                        "path": "/api/child/xray/config",
                        "body": {"config": "new-a"},
                        "timeout_ms": 5000,
                    },
                    "rollback": {
                        "method": "post",
                        "path": "/api/child/xray/config",
                        "body": {"config": "old-a"},
                        "timeout_ms": 5000,
                    },
                },
                {
                    "server_id": server_b_id,
                    "label": "Write nginx config",
                    "forward": {
                        "method": "post",
                        "path": "/api/child/nginx/config",
                        "body": {"config": "new-b"},
                        "timeout_ms": 5000,
                    },
                    "rollback": {
                        "method": "post",
                        "path": "/api/child/nginx/config",
                        "body": {"config": "old-b"},
                        "timeout_ms": 5000,
                    },
                },
            ],
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["license_required"] is False
    assert created["commands"] == []
    assert created["change_set"]["status"] == "planned"
    assert [step["sequence"] for step in created["change_set"]["steps"]] == [1, 2]
    assert created["change_set"]["steps"][0]["forward"]["method"] == "POST"
    assert created["change_set"]["steps"][0]["rollback"]["body"] == {"config": "old-a"}
    change_set_id = created["change_set"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": edge_a["agent_token"],
                    "hostname": "change-edge-a-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True

        dispatch_response = client.post(f"/api/v1/change-sets/{change_set_id}/dispatch")
        assert dispatch_response.status_code == 200
        dispatched = dispatch_response.json()
        assert dispatched["change_set"]["status"] == "dispatched"
        assert [command["server_id"] for command in dispatched["commands"]] == [
            server_a_id,
            server_b_id,
        ]
        assert [command["status"] for command in dispatched["commands"]] == [
            "leased",
            "pending",
        ]
        forward_rpc = websocket.receive_json()
        assert forward_rpc["type"] == "rpc_call"
        assert forward_rpc["payload"]["path"] == "/api/child/xray/config"
        assert forward_rpc["payload"]["body"] == {"config": "new-a"}

        fetched = client.get(f"/api/v1/change-sets/{change_set_id}")
        assert fetched.status_code == 200
        fetched_steps = fetched.json()["change_set"]["steps"]
        assert fetched_steps[0]["forward_command"]["status"] == "leased"
        assert fetched_steps[1]["forward_command"]["status"] == "pending"

        rollback_response = client.post(
            f"/api/v1/change-sets/{change_set_id}/rollback",
            json={"reason": "validation failed"},
        )
        assert rollback_response.status_code == 200
        rolled_back = rollback_response.json()
        assert rolled_back["change_set"]["status"] == "rollback_queued"
        assert rolled_back["change_set"]["rollback_reason"] == "validation failed"
        assert rolled_back["warnings"] == []
        assert [command["server_id"] for command in rolled_back["commands"]] == [
            server_b_id,
            server_a_id,
        ]
        assert [command["status"] for command in rolled_back["commands"]] == [
            "pending",
            "leased",
        ]
        rollback_rpc = websocket.receive_json()
        assert rollback_rpc["type"] == "rpc_call"
        assert rollback_rpc["payload"]["path"] == "/api/child/xray/config"
        assert rollback_rpc["payload"]["body"] == {"config": "old-a"}


def test_change_set_rejects_unknown_server(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/change-sets",
        json={
            "name": "Bad target",
            "steps": [
                {
                    "server_id": "00000000-0000-0000-0000-000000000000",
                    "forward": {"method": "GET", "path": "/api/child/system/info"},
                }
            ],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "server not found: 00000000-0000-0000-0000-000000000000"
