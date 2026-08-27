from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import yaml
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=sqlite_url(tmp_path / "open-node-test.db"))
    return TestClient(create_app(settings))


def create_catalog_fixture(client: TestClient) -> tuple[str, str, str, str]:
    server = client.post("/api/v1/servers", json={"name": "edge-sub"}).json()
    server_id = server["server"]["id"]
    user = client.post(
        "/api/v1/users",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "display_name": "Alice",
        },
    )
    assert user.status_code == 201
    node = client.post(
        "/api/v1/nodes",
        json={
            "name": "Tokyo vless",
            "server_id": server_id,
            "protocol": "vless",
            "node_type": "routed",
            "inbound_tag": "vless-443",
            "routed_outbound_tag": "tokyo-out",
            "routed_rule_marktag": "route-tokyo",
            "tags": ["jp", "premium", "jp"],
            "client_template": {
                "id": "client-{username}",
                "email": "{username}__tokyo",
                "flow": "xtls-rprx-vision",
            },
            "config": {
                "name": "Tokyo base",
                "type": "vless",
                "server": "tokyo.example.com",
                "port": 443,
                "uuid": "template-id",
                "tls": True,
            },
        },
    )
    assert node.status_code == 201
    node_id = node.json()["node"]["id"]
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Premium",
            "description": "Main paid-like plan without license gates",
            "traffic_limit_gb": 128,
            "cycle_days": 30,
            "is_reset": True,
            "reset_day": 1,
            "node_ids": [node_id],
            "node_multipliers": {node_id: 1.5},
            "speed_limit_mbps": 100,
            "device_limit": 3,
            "traffic_mode": "twoway",
        },
    )
    assert plan.status_code == 201
    return server["agent_token"], server_id, node_id, plan.json()["plan"]["id"]


def test_subscription_catalog_assigns_plan_without_license_gate(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    _token, _server_id, node_id, plan_id = create_catalog_fixture(client)

    response = client.post(
        "/api/v1/users/alice/plan",
        json={"plan_id": plan_id, "start_date": "2026-08-27"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["user"]["current_plan_id"] == plan_id
    assert payload["user"]["is_reset"] is True
    assert payload["user"]["reset_day"] == 1
    assert payload["plan"]["node_ids"] == [node_id]
    assert payload["plan"]["traffic_limit_bytes"] == 128 * 1024 * 1024 * 1024
    assert payload["commands"] == []
    batch = payload["provisioning_batches"][0]
    assert batch["server_name"] == "edge-sub"
    client_config = batch["body"]["inbound_clients"][0]["client"]
    generated_id = str(UUID(client_config["id"]))
    assert client_config == {
        "id": generated_id,
        "email": "alice__vless-443",
        "flow": "xtls-rprx-vision",
        "level": 0,
    }
    assert batch["body"] == {
        "inbound_clients": [
            {
                "tag": "vless-443",
                "client": client_config,
            }
        ],
        "routing_user_additions": [
            {
                "marktag": "route-tokyo",
                "outbound_tag": "tokyo-out",
                "user_email": "alice__vless-443",
            }
        ],
        "no_restart": True,
    }
    users = client.get("/api/v1/users").json()
    nodes = client.get("/api/v1/nodes").json()
    plans = client.get("/api/v1/plans").json()
    assert users["license_required"] is False
    assert nodes["nodes"][0]["tags"] == ["jp", "premium"]
    assert plans["plans"][0]["name"] == "Premium"
    credentials = client.get("/api/v1/users/alice/credentials").json()
    assert credentials["license_required"] is False
    assert credentials["credentials"][0]["email"] == "alice__vless-443"
    assert credentials["credentials"][0]["credential"]["id"] == generated_id


def test_subscription_token_renders_clash_yaml_and_traffic_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, _server_id, _node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": plan_id,
            "start_date": "2026-08-27",
            "expire_date": "2026-09-30",
        },
    ).json()
    client_id = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["id"]
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"][
        "email"
    ]

    token_response = client.post("/api/v1/users/alice/subscription-token")

    assert token_response.status_code == 201
    token_payload = token_response.json()
    assert token_payload["license_required"] is False
    token = token_payload["subscription"]["token"]
    short_code = token_payload["subscription"]["short_code"]
    assert f"/api/v1/subscribe/{token}" in token_payload["subscription"]["subscription_url"]
    assert f"/api/v1/subscribe/{short_code}" in token_payload["subscription"]["short_url"]

    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "stats": {
                "user": {
                    client_email: {
                        "uplink": 1024,
                        "downlink": 2048,
                    }
                }
            },
        },
    )

    response = client.get(f"/api/v1/subscribe/{token}")
    short_response = client.get(f"/api/v1/subscribe/{short_code}")

    assert response.status_code == 200
    assert short_response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert response.headers["profile-title"].startswith("base64:")
    expire = int(datetime(2026, 9, 30, tzinfo=UTC).timestamp())
    assert response.headers["subscription-userinfo"] == (
        f"upload=1024; download=2048; total={128 * 1024 * 1024 * 1024}; "
        f"expire={expire}"
    )
    doc = yaml.safe_load(response.text)
    proxy = doc["proxies"][0]
    assert proxy["name"] == "[1.5] Tokyo base"
    assert proxy["uuid"] == client_id
    assert proxy["server"] == "tokyo.example.com"
    assert doc["proxy-groups"][0]["proxies"] == ["[1.5] Tokyo base"]
    assert doc["rules"] == ["MATCH,Proxy"]


def test_plan_assignment_dispatches_agent_batch_apply(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, server_id, _node_id, plan_id = create_catalog_fixture(client)

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": agent_token,
                    "hostname": "edge-sub-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True

        response = client.post(
            "/api/v1/users/alice/plan",
            json={
                "plan_id": plan_id,
                "queue_agent_commands": True,
                "no_restart": False,
                "command_timeout_ms": 75_000,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["commands"][0]["status"] == "leased"
        assert payload["commands"][0]["path"] == "/api/child/batch-apply"
        assert payload["commands"][0]["server_id"] == server_id
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/batch-apply"
        assert rpc_call["payload"]["method"] == "POST"
        assert rpc_call["payload"]["timeout_ms"] == 75_000
        assert rpc_call["payload"]["body"]["no_restart"] is False
        UUID(rpc_call["payload"]["body"]["inbound_clients"][0]["client"]["id"])
