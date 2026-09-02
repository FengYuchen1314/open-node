import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import yaml
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import (
    SubscriptionArchivedTrafficModel,
    SubscriptionPlanModel,
)
from sqlalchemy import inspect, text


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=sqlite_url(tmp_path / "open-node-test.db"), short_links_enabled=True
    )
    return authenticated_client(create_app(settings))


def create_plan_node_fixture(client: TestClient, *, namespace: str) -> str:
    server = client.post("/api/v1/servers", json={"name": f"{namespace}-edge"})
    assert server.status_code == 201, server.text
    server_id = server.json()["server"]["id"]
    node = client.post(
        "/api/v1/nodes",
        json={
            "name": f"{namespace} vless",
            "server_id": server_id,
            "protocol": "vless",
            "node_type": "routed",
            "inbound_tag": f"{namespace}-inbound",
            "routed_outbound_tag": f"{namespace}-outbound",
            "routed_rule_marktag": f"route-{namespace}",
            "client_template": {
                "id": "client-{username}",
                "email": f"{{username}}__{namespace}",
                "flow": "xtls-rprx-vision",
            },
            "config": {
                "name": f"{namespace} base",
                "type": "vless",
                "server": "edge.example.com",
                "port": 443,
                "uuid": "template-id",
                "tls": True,
            },
        },
    )
    assert node.status_code == 201, node.text
    return node.json()["node"]["id"]


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
        "no_restart": False,
        "limiter_users": [
            {
                "inbound_tag": "vless-443",
                "user": {
                    "uid": 0,
                    "email": "alice__vless-443",
                    "speed_limit": 12500000,
                    "device_limit": 3,
                    "conn_group": "account-"
                    + hashlib.sha256(
                        json.dumps(
                            ["alice", _server_id, "vless-443"], separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                },
            }
        ],
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
    agent_token, _server_id, node_id, plan_id = create_catalog_fixture(client)
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
        f"upload=0; download=9216; total={128 * 1024 * 1024 * 1024}; expire={expire}"
    )
    doc = yaml.safe_load(response.text)
    proxy = doc["proxies"][0]
    assert proxy["name"] == "[1.5] Tokyo base"
    assert proxy["uuid"] == client_id
    assert proxy["server"] == "tokyo.example.com"
    assert doc["proxy-groups"][0]["proxies"] == ["[1.5] Tokyo base"]
    assert doc["rules"] == ["MATCH,Proxy"]


def test_subscription_formats_include_sing_box_uri_list_and_base64(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    _agent_token, _server_id, _node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).json()
    client_id = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["id"]
    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]

    sing_box_response = client.get(f"/api/v1/subscribe/{token}?format=sing-box")
    uri_response = client.get(f"/api/v1/subscribe/{token}?format=uri-list")
    base64_response = client.get(f"/api/v1/subscribe/{token}?format=base64")

    assert sing_box_response.status_code == 200
    assert sing_box_response.headers["content-type"].startswith("application/json")
    sing_box = json.loads(sing_box_response.text)
    assert sing_box["outbounds"][0]["type"] == "selector"
    assert sing_box["outbounds"][2]["type"] == "vless"
    assert sing_box["outbounds"][2]["tag"] == "[1.5] Tokyo base"
    assert sing_box["outbounds"][2]["uuid"] == client_id
    assert sing_box["outbounds"][2]["tls"] == {"enabled": True}

    assert uri_response.status_code == 200
    assert uri_response.headers["content-type"].startswith("text/plain")
    assert uri_response.text.startswith(f"vless://{client_id}@tokyo.example.com:443")
    assert "security=tls" in uri_response.text
    assert "#%5B1.5%5D%20Tokyo%20base" in uri_response.text

    decoded = base64.b64decode(base64_response.text.strip()).decode("utf-8")
    assert decoded == uri_response.text


def test_subscription_traffic_ledger_tracks_deltas_and_counter_resets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, _server_id, node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": plan_id,
            "start_date": "2026-08-27",
            "expire_date": "2026-09-30",
        },
    ).json()
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"][
        "email"
    ]

    for reported_at, uplink, downlink in [
        ("2026-08-27T00:00:00Z", 100, 200),
        ("2026-08-27T00:05:00Z", 150, 260),
        ("2026-08-27T00:10:00Z", 20, 40),
    ]:
        response = client.post(
            "/api/v1/agents/telemetry",
            json={
                "token": agent_token,
                "reported_at": reported_at,
                "stats": {"user": {client_email: {"uplink": uplink, "downlink": downlink}}},
            },
        )
        assert response.status_code == 200

    traffic = client.get("/api/v1/users/alice/traffic")

    assert traffic.status_code == 200
    payload = traffic.json()
    assert payload["license_required"] is False
    assert payload["upload"] == 170
    assert payload["download"] == 300
    assert payload["total"] == 470
    assert payload["weighted_upload"] == 510
    assert payload["weighted_download"] == 900
    assert payload["charged_usage_bytes"] == 1410
    assert payload["entries"][0]["email"] == client_email
    assert payload["entries"][0]["attributed_node_id"] == node_id
    assert payload["entries"][0]["charged_usage_bytes"] == 1410
    assert payload["entries"][0]["last_reported_at"] == "2026-08-27T00:10:00Z"

    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    header = client.get(f"/api/v1/subscribe/{token}").headers["subscription-userinfo"]
    expire = int(datetime(2026, 9, 30, tzinfo=UTC).timestamp())
    assert header == f"upload=0; download=1410; total={128 * 1024 * 1024 * 1024}; expire={expire}"


def test_subscription_billing_freezes_weight_for_each_telemetry_delta(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, _server_id, node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={"plan_id": plan_id, "start_date": "2026-08-27", "expire_date": "2026-09-30"},
    ).json()
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0][
        "client"
    ]["email"]

    first = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-27T00:00:00Z",
            "stats": {"user": {client_email: {"uplink": 100, "downlink": 200}}},
        },
    )
    assert first.status_code == 200
    assert client.get("/api/v1/users/alice/quota").json()["quota"]["charged_usage_bytes"] == 900

    store = client.app.state.inventory
    with store._session() as session:
        plan = session.get(SubscriptionPlanModel, plan_id)
        assert plan is not None
        plan.traffic_mode = "oneway"
        plan.node_multipliers = {node_id: 0.5}
        session.commit()

    # A plan edit never reweights traffic that was already ingested.
    assert client.get("/api/v1/users/alice/quota").json()["quota"]["charged_usage_bytes"] == 900
    second = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-27T00:05:00Z",
            "stats": {"user": {client_email: {"uplink": 140, "downlink": 260}}},
        },
    )
    assert second.status_code == 200
    traffic = client.get("/api/v1/users/alice/traffic").json()
    assert (traffic["upload"], traffic["download"]) == (140, 260)
    assert (traffic["weighted_upload"], traffic["weighted_download"]) == (320, 630)
    assert traffic["charged_usage_bytes"] == 950
    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    assert "upload=0; download=950;" in client.get(f"/api/v1/subscribe/{token}").headers[
        "subscription-userinfo"
    ]


def test_existing_sqlite_traffic_rows_receive_one_time_billing_backfill(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    database_url = sqlite_url(tmp_path / "open-node-test.db")
    agent_token, server_id, node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={"plan_id": plan_id, "start_date": "2026-08-27"},
    ).json()
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0][
        "client"
    ]["email"]
    response = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-27T00:00:00Z",
            "stats": {"user": {client_email: {"uplink": 100, "downlink": 200}}},
        },
    )
    assert response.status_code == 200
    store = client.app.state.inventory
    with store._session() as session:
        session.add(
            SubscriptionArchivedTrafficModel(
                username="alice",
                server_id=server_id,
                server_name="Removed legacy server",
                upload=10,
                download=20,
                weighted_upload=0,
                weighted_download=0,
                updated_at=datetime.now(tz=UTC),
            )
        )
        session.commit()
    client.close()

    with store._engine.begin() as connection:
        for column in ("attributed_node_id", "weighted_upload", "weighted_download"):
            connection.execute(
                text(f"ALTER TABLE subscription_traffic_ledger DROP COLUMN {column}")
            )
        for column in ("weighted_upload", "weighted_download"):
            connection.execute(
                text(f"ALTER TABLE subscription_archived_traffic DROP COLUMN {column}")
            )

    upgraded = authenticated_client(
        create_app(Settings(database_url=database_url, short_links_enabled=True))
    )
    columns = {
        column["name"]
        for column in inspect(upgraded.app.state.inventory._engine).get_columns(
            "subscription_traffic_ledger"
        )
    }
    assert {"attributed_node_id", "weighted_upload", "weighted_download"} <= columns
    traffic = upgraded.get("/api/v1/users/alice/traffic").json()
    live = next(entry for entry in traffic["entries"] if not entry["archived"])
    archived = next(entry for entry in traffic["entries"] if entry["archived"])
    assert live["attributed_node_id"] == node_id
    assert (live["weighted_upload"], live["weighted_download"]) == (300, 600)
    # The legacy archive has no node identity, so only the twoway package factor is recoverable.
    assert (archived["weighted_upload"], archived["weighted_download"]) == (20, 40)
    assert traffic["charged_usage_bytes"] == 960
    assert traffic["entries"][0]["server_id"] == server_id


def test_subscription_quota_blocks_over_limit_and_resets_ledger(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, _server_id, node_id, _plan_id = create_catalog_fixture(client)
    tiny_plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Tiny",
            "traffic_limit_gb": 0.0000001,
            "node_ids": [node_id],
            "traffic_mode": "twoway",
        },
    ).json()["plan"]
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": tiny_plan["id"],
            "start_date": "2026-08-27",
            "expire_date": "2026-09-30",
            "is_reset": True,
            "reset_day": 1,
        },
    ).json()
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"][
        "email"
    ]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-27T00:00:00Z",
            "stats": {"user": {client_email: {"uplink": 80, "downlink": 80}}},
        },
    )

    quota_response = client.get(
        "/api/v1/users/alice/quota",
        params={"now": "2026-08-27T00:01:00Z"},
    ).json()
    assert quota_response["license_required"] is False
    quota = quota_response["quota"]
    assert quota["traffic_limit_bytes"] == tiny_plan["traffic_limit_bytes"]
    assert quota["charged_usage_bytes"] == 320
    assert quota["remaining_bytes"] == 0
    assert quota["over_quota"] is True
    assert quota["available"] is False

    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    blocked = client.get(f"/api/v1/subscribe/{token}")
    assert blocked.status_code == 404
    assert blocked.json()["detail"] == "subscription traffic quota exceeded"

    reset = client.post(
        "/api/v1/users/alice/traffic/reset",
        params={"now": "2026-08-27T00:02:00Z"},
    ).json()
    assert reset["license_required"] is False
    assert reset["quota"]["charged_usage_bytes"] == 0
    assert reset["quota"]["over_quota"] is False
    assert reset["quota"]["last_traffic_reset_at"] == "2026-08-27T00:02:00Z"
    assert client.get(f"/api/v1/subscribe/{token}").status_code == 200

    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-27T00:05:00Z",
            "stats": {"user": {client_email: {"uplink": 100, "downlink": 100}}},
        },
    )
    traffic = client.get("/api/v1/users/alice/traffic").json()
    assert traffic["upload"] == 20
    assert traffic["download"] == 20
    assert traffic["charged_usage_bytes"] == 80


def test_subscription_due_reset_runs_once_per_reset_window(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    agent_token, _server_id, _node_id, plan_id = create_catalog_fixture(client)
    assigned = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": plan_id,
            "start_date": "2026-08-27",
            "expire_date": "2026-10-31",
            "is_reset": True,
            "reset_day": 1,
        },
    ).json()
    client_email = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"][
        "email"
    ]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": agent_token,
            "reported_at": "2026-08-31T23:50:00Z",
            "stats": {"user": {client_email: {"uplink": 200, "downlink": 300}}},
        },
    )

    quota = client.get(
        "/api/v1/users/alice/quota",
        params={"now": "2026-09-01T00:00:00Z"},
    ).json()["quota"]
    assert quota["reset_due"] is True
    assert quota["reset_due_at"] == "2026-09-01T00:00:00Z"

    dry_run = client.post(
        "/api/v1/traffic/reset-due",
        json={"now": "2026-09-01T00:00:00Z", "dry_run": True},
    ).json()["summary"]
    assert dry_run == {
        "checked_users": 1,
        "reset_users": 1,
        "skipped_users": 0,
        "usernames": ["alice"],
        "dry_run": True,
        "warnings": [],
    }
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 500

    reset = client.post(
        "/api/v1/traffic/reset-due",
        json={"now": "2026-09-01T00:00:00Z"},
    ).json()["summary"]
    assert reset["reset_users"] == 1
    assert reset["usernames"] == ["alice"]
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 0
    assert client.get("/api/v1/users").json()["users"][0]["last_traffic_reset_at"] == (
        "2026-09-01T00:00:00Z"
    )

    repeated = client.post(
        "/api/v1/traffic/reset-due",
        json={"now": "2026-09-01T00:05:00Z"},
    ).json()["summary"]
    assert repeated["reset_users"] == 0
    assert repeated["skipped_users"] == 1


def test_subscription_node_preset_creates_renderable_node(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    server = client.post(
        "/api/v1/servers",
        json={"name": "preset-edge", "domain": "preset.example.com"},
    ).json()
    server_id = server["server"]["id"]

    presets_response = client.get("/api/v1/node-presets")
    assert presets_response.status_code == 200
    presets = presets_response.json()["presets"]
    assert presets_response.json()["license_required"] is False
    assert [preset["id"] for preset in presets] == [
        "vless-reality-vision",
        "vless-xhttp-reality-xmux",
        "anytls-shadowtls",
        "mieru",
        "socks5",
    ]
    presets_by_id = {preset["id"]: preset for preset in presets}
    assert presets_by_id["anytls-shadowtls"]["config"]["idle-session-check-interval"] == 30
    assert "shadow-tls" not in presets_by_id["anytls-shadowtls"]["config"]
    assert presets_by_id["vless-xhttp-reality-xmux"]["config"]["xhttp-opts"][
        "reuse-settings"
    ]["max-concurrency"] == "16-32"
    assert presets_by_id["anytls-shadowtls"]["config"]["shadow-tls-opts"] == {"version": 3}
    assert presets_by_id["mieru"]["config"]["transport"] == "TCP"
    assert presets_by_id["mieru"]["config"]["udp"] is False

    create_response = client.post(
        "/api/v1/node-presets/vless-reality-vision/nodes",
        json={
            "server_id": server_id,
            "name": "Preset vless",
            "host": "edge.example.com",
            "port": 8443,
            "camouflage_pool_id": "los-angeles-ucla",
            "camouflage_sni": "www.ucla.edu",
            "tags": ["preset", "premium"],
        },
    )

    assert create_response.status_code == 201
    node = create_response.json()["node"]
    assert node["protocol"] == "vless"
    assert node["inbound_tag"] == f"open-node-{node['id']}"
    assert node["tags"] == ["preset", "premium"]
    assert node["config"]["server"] == "preset.example.com"
    assert node["config"]["port"] == 443
    assert node["protocol_profile"] == "vless-reality-vision"
    assert node["camouflage_pool_id"] == "los-angeles-ucla"
    assert node["camouflage_sni"] == "www.ucla.edu"
    assert node["client_template"]["flow"] == "xtls-rprx-vision"

    hidden_response = client.post(
        "/api/v1/node-presets/snell-v6/nodes",
        json={
            "server_id": server_id,
            "host": "snell.example.com",
        },
    )
    assert hidden_response.status_code == 404


def test_xray_fork_protocols_provision_and_render_subscriptions(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    server = client.post(
        "/api/v1/servers",
        json={"name": "fork-edge", "domain": "fork.example.com"},
    ).json()["server"]
    user_response = client.post(
        "/api/v1/users",
        json={"username": "bob", "email": "bob@example.com"},
    )
    assert user_response.status_code == 201

    node_payloads = [
        {
            "name": "AnyTLS Edge",
            "server_id": server["id"],
            "protocol": "anytls",
            "inbound_tag": "anytls-443",
            "client_template": {"email": "{username}__anytls-443"},
            "config": {
                "name": "AnyTLS Edge",
                "type": "anytls",
                "server": "fork.example.com",
                "port": 443,
                "udp": True,
                "sni": "fork.example.com",
                "idle-session-check-interval": 30,
                "idle-session-timeout": 45,
                "min-idle-session": 0,
            },
        },
        {
            "name": "Snell Edge",
            "server_id": server["id"],
            "protocol": "snell",
            "inbound_tag": "snell-443",
            "client_template": {
                "email": "{username}__snell-443",
                "version": 4,
                "obfsMode": "http",
                "obfsHost": "example.org",
            },
            "config": {
                "name": "Snell Edge",
                "type": "snell",
                "server": "fork.example.com",
                "port": 443,
                "version": 4,
                "udp": True,
                "reuse": True,
                "obfs-opts": {"mode": "http", "host": "example.org"},
            },
        },
        {
            "name": "Snell v6 Edge",
            "server_id": server["id"],
            "protocol": "snell",
            "inbound_tag": "snell-v6-443",
            "client_template": {
                "email": "{username}__snell-v6-443",
                "version": 6,
                "v6Mode": "default",
            },
            "config": {
                "name": "Snell v6 Edge",
                "type": "snell",
                "server": "fork.example.com",
                "port": 443,
                "version": 6,
                "mode": "default",
                "udp": True,
            },
        },
        {
            "name": "Mieru Edge",
            "server_id": server["id"],
            "protocol": "mieru",
            "inbound_tag": "mieru-2999",
            "client_template": {"email": "{username}__mieru-2999"},
            "config": {
                "name": "Mieru Edge",
                "type": "mieru",
                "server": "fork.example.com",
                "port": 2999,
                "transport": "TCP",
                "udp": True,
            },
        },
    ]
    node_ids = []
    for payload in node_payloads:
        response = client.post("/api/v1/nodes", json=payload)
        assert response.status_code == 201
        node_ids.append(response.json()["node"]["id"])

    plan_response = client.post(
        "/api/v1/plans",
        json={"name": "Fork Protocols", "traffic_limit_gb": 64, "node_ids": node_ids},
    )
    assert plan_response.status_code == 201
    assigned = client.post(
        "/api/v1/users/bob/plan",
        json={"plan_id": plan_response.json()["plan"]["id"]},
    ).json()
    clients_by_tag = {
        item["tag"]: item["client"]
        for item in assigned["provisioning_batches"][0]["body"]["inbound_clients"]
    }

    anytls_client = clients_by_tag["anytls-443"]
    snell_client = clients_by_tag["snell-443"]
    snell_v6_client = clients_by_tag["snell-v6-443"]
    mieru_client = clients_by_tag["mieru-2999"]
    assert str(UUID(anytls_client["password"])) == anytls_client["password"]
    assert anytls_client["email"] == "bob__anytls-443"
    assert str(UUID(snell_client["psk"])) == snell_client["psk"]
    assert snell_client["version"] == 4
    assert snell_client["obfsMode"] == "http"
    assert snell_client["obfsHost"] == "example.org"
    assert str(UUID(snell_v6_client["psk"])) == snell_v6_client["psk"]
    assert snell_v6_client["version"] == 6
    assert snell_v6_client["v6Mode"] == "default"
    assert "clientId" not in snell_v6_client
    assert mieru_client["username"] == "bob"
    assert str(UUID(mieru_client["password"])) == mieru_client["password"]

    token = client.post("/api/v1/users/bob/subscription-token").json()["subscription"]["token"]
    clash_response = client.get(f"/api/v1/subscribe/{token}")
    sing_box_response = client.get(f"/api/v1/subscribe/{token}?format=sing-box")

    assert clash_response.status_code == 200
    clash = yaml.safe_load(clash_response.text)
    clash_proxies = {proxy["name"]: proxy for proxy in clash["proxies"]}
    assert clash_proxies["AnyTLS Edge"]["password"] == anytls_client["password"]
    assert clash_proxies["AnyTLS Edge"]["idle-session-timeout"] == 45
    assert clash_proxies["Snell Edge"]["psk"] == snell_client["psk"]
    assert clash_proxies["Snell Edge"]["obfs-opts"] == {"mode": "http", "host": "example.org"}
    assert "Snell v6 Edge" not in clash_proxies
    assert clash_response.headers["x-open-node-excluded-nodes"] == "1"
    assert clash_proxies["Mieru Edge"]["username"] == "bob"
    assert clash_proxies["Mieru Edge"]["password"] == mieru_client["password"]
    assert clash_proxies["Mieru Edge"]["transport"] == "TCP"
    assert clash_proxies["Mieru Edge"]["udp"] is False

    assert sing_box_response.status_code == 200
    sing_box = json.loads(sing_box_response.text)
    assert sing_box["outbounds"][0]["outbounds"] == [
        "AnyTLS Edge",
    ]
    sing_box_outbounds = {outbound["tag"]: outbound for outbound in sing_box["outbounds"][2:]}
    anytls_outbound = sing_box_outbounds["AnyTLS Edge"]
    assert anytls_outbound["type"] == "anytls"
    assert anytls_outbound["password"] == anytls_client["password"]
    assert anytls_outbound["idle_session_check_interval"] == "30s"
    assert anytls_outbound["idle_session_timeout"] == "45s"
    assert anytls_outbound["min_idle_session"] == 0
    assert anytls_outbound["tls"] == {"enabled": True, "server_name": "fork.example.com"}
    assert "Snell Edge" not in sing_box_outbounds
    assert "Snell v6 Edge" not in sing_box_outbounds
    assert "Mieru Edge" not in sing_box_outbounds
    xray_response = client.get(f"/api/v1/subscribe/{token}?format=xray")
    assert xray_response.status_code == 200
    xray = {outbound["tag"]: outbound for outbound in xray_response.json()["outbounds"]}
    assert xray["Snell v6 Edge"]["settings"] == {
        "address": "fork.example.com",
        "port": 443,
        "version": 6,
        "v6Mode": "default",
        "psk": snell_v6_client["psk"],
    }
    assert xray["Snell Edge"]["settings"]["obfsMode"] == "http"
    assert xray["Snell Edge"]["settings"]["obfsHost"] == "example.org"
    assert "Mieru Edge" not in xray


def test_subscription_catalog_export_import_round_trips_by_names(tmp_path: Path) -> None:
    source = authenticated_client(
        create_app(Settings(database_url=sqlite_url(tmp_path / "source.db")))
    )
    _agent_token, _server_id, _node_id, plan_id = create_catalog_fixture(source)
    assigned = source.post(
        "/api/v1/users/alice/plan",
        json={"plan_id": plan_id, "start_date": "2026-08-27", "expire_date": "2026-09-30"},
    ).json()
    client_id = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["id"]

    export_response = source.get("/api/v1/catalog/export?include_credentials=true")
    assert export_response.status_code == 200
    catalog = export_response.json()["catalog"]
    assert export_response.json()["license_required"] is False
    assert catalog["users"][0]["current_plan_name"] == "Premium"
    assert catalog["plans"][0]["node_names"] == ["Tokyo vless"]
    assert catalog["credentials"][0]["credential"]["id"] == client_id

    target = authenticated_client(
        create_app(Settings(database_url=sqlite_url(tmp_path / "target.db")))
    )
    target.post("/api/v1/servers", json={"name": "edge-sub"}).json()
    import_response = target.post(
        "/api/v1/catalog/import",
        json={"catalog": catalog, "import_credentials": True},
    )

    assert import_response.status_code == 200
    summary = import_response.json()["summary"]
    assert import_response.json()["license_required"] is False
    assert summary == {
        "created_users": 1,
        "updated_users": 0,
        "created_nodes": 1,
        "updated_nodes": 0,
        "created_plans": 1,
        "updated_plans": 0,
        "imported_credentials": 1,
        "warnings": [],
    }
    users = target.get("/api/v1/users").json()["users"]
    nodes = target.get("/api/v1/nodes").json()["nodes"]
    plans = target.get("/api/v1/plans").json()["plans"]
    credentials = target.get("/api/v1/users/alice/credentials").json()["credentials"]
    assert users[0]["current_plan_id"] == plans[0]["id"]
    assert nodes[0]["name"] == "Tokyo vless"
    assert plans[0]["node_ids"] == [nodes[0]["id"]]
    assert plans[0]["node_multipliers"] == {nodes[0]["id"]: 1.5}
    assert credentials[0]["credential"]["id"] == client_id


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
                    "capabilities": {
                        "rpc": True,
                        "native_limiter": True,
                        "subscription_access": True,
                    },
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

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
        assert payload["commands"][0]["path"] == "/api/child/subscription-access"
        assert payload["commands"][0]["server_id"] == server_id
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/subscription-access"
        assert rpc_call["payload"]["method"] == "POST"
        assert rpc_call["payload"]["timeout_ms"] == 75_000
        assert rpc_call["payload"]["body"]["entries"][0]["enabled"] is True
        UUID(rpc_call["payload"]["body"]["entries"][0]["client"]["id"])
