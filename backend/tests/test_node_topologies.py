import json
from pathlib import Path

import yaml
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import (
    NodeTopologyModel,
    ProductUserModel,
    SubscriptionPlanModel,
)
from sqlalchemy import select


def make_client(tmp_path: Path) -> TestClient:
    database = (tmp_path / "node-topologies.db").as_posix()
    return authenticated_client(
        create_app(Settings(database_url=f"sqlite:///{database}", short_links_enabled=True))
    )


def physical_node(client: TestClient, server_id: str, name: str, tag: str) -> dict:
    response = client.post(
        "/api/v1/nodes",
        json={
            "name": name,
            "server_id": server_id,
            "protocol": "vless",
            "inbound_tag": tag,
            "client_template": {"email": f"{{username}}__{tag}"},
            "config": {
                "name": name,
                "type": "vless",
                "server": f"{tag}.example.com",
                "port": 443,
                "tls": True,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["node"]


def fixture(client: TestClient):
    servers = []
    nodes = []
    for index in range(4):
        created = client.post("/api/v1/servers", json={"name": f"edge-{index + 1}"})
        assert created.status_code == 201, created.text
        server = created.json()["server"]
        servers.append(server)
        nodes.append(
            physical_node(
                client,
                server["id"],
                f"Physical {index + 1}",
                f"vless-{index + 1}",
            )
        )
    same_server = physical_node(client, servers[0]["id"], "Same server", "vless-same-server")
    return servers, nodes, same_server


def create_user_plan(
    client: TestClient,
    topology_id: str,
    *,
    multiplier: float | None = None,
) -> str:
    user = client.post(
        "/api/v1/users",
        json={"username": "alice", "display_name": "Alice"},
    )
    assert user.status_code == 201, user.text
    plan_payload = {
        "name": "Topology plan",
        "traffic_limit_gb": 10,
        "node_ids": [topology_id],
    }
    if multiplier is not None:
        plan_payload["node_multipliers"] = {topology_id: multiplier}
    plan = client.post(
        "/api/v1/plans",
        json=plan_payload,
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["plan"]["id"]
    assigned = client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id})
    assert assigned.status_code == 200, assigned.text
    assert {batch["server_id"] for batch in assigned.json()["provisioning_batches"]}
    return client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]


def test_topology_load_balance_is_atomic_and_renders_mihomo_syntax(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    _servers, nodes, _same_server = fixture(client)
    created = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Dual entry to exit",
            "stages": [
                {"node_ids": [nodes[0]["id"], nodes[1]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
            "layout": {
                nodes[0]["id"]: {"x": 10, "y": 20},
                nodes[2]["id"]: {"x": 500, "y": 20},
            },
        },
    )
    assert created.status_code == 201, created.text
    topology = created.json()["topology"]
    token = create_user_plan(client, topology["id"])

    clash_response = client.get(f"/api/v1/subscribe/{token}?format=clash")
    assert clash_response.status_code == 200, clash_response.text
    clash = yaml.safe_load(clash_response.text)
    assert len(clash["proxies"]) == 3
    final = next(proxy for proxy in clash["proxies"] if proxy["name"] == topology["name"])
    load_balance = next(group for group in clash["proxy-groups"] if group["type"] == "load-balance")
    assert load_balance["strategy"] == "round-robin"
    assert len(load_balance["proxies"]) == 2
    assert final["dialer-proxy"] == load_balance["name"]
    assert clash["proxy-groups"][0]["proxies"] == [topology["name"]]
    assert not any(key.startswith("_open_node_") for proxy in clash["proxies"] for key in proxy)

    stash_response = client.get(f"/api/v1/subscribe/{token}?format=stash")
    assert stash_response.status_code == 200, stash_response.text
    stash = yaml.safe_load(stash_response.text)
    stash_final = next(proxy for proxy in stash["proxies"] if proxy["name"] == topology["name"])
    stash_load_balance = next(
        group for group in stash["proxy-groups"] if group["type"] == "load-balance"
    )
    assert stash_load_balance["strategy"] == "round-robin"
    assert stash_final["dialer-proxy"] == stash_load_balance["name"]
    assert stash["proxy-groups"][0]["proxies"] == [topology["name"]]
    assert not any(key.startswith("_open_node_") for proxy in stash["proxies"] for key in proxy)

    for client_format in ("sing-box", "xray", "uri-list"):
        preview = client.get(f"/api/v1/users/alice/subscription-preview?format={client_format}")
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["nodes"]) == 1
        assert preview.json()["nodes"][0]["node_id"] == topology["id"]
        assert preview.json()["nodes"][0]["available"] is False
        reason = preview.json()["nodes"][0]["reason"]
        assert (
            "load-balancer" in reason
            if client_format in {"sing-box", "xray"}
            else "chained proxies" in reason
        )
        rejected = client.get(f"/api/v1/subscribe/{token}?format={client_format}")
        assert rejected.status_code == 404
        assert "no compatible nodes" in rejected.json()["detail"]


def test_single_chain_renders_sing_box_detour_and_xray_proxy_settings(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, nodes, _same_server = fixture(client)
    created = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Two hop",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
        },
    )
    assert created.status_code == 201, created.text
    topology = created.json()["topology"]
    token = create_user_plan(client, topology["id"], multiplier=2.5)

    credentials = client.get("/api/v1/users/alice/credentials").json()["credentials"]
    assert {credential["node_id"] for credential in credentials} == {
        nodes[0]["id"],
        nodes[2]["id"],
    }
    assert topology["id"] not in {credential["node_id"] for credential in credentials}
    store = client.app.state.inventory
    with store._session() as session:
        plan = session.scalar(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == "Topology plan")
        )
        assert store._subscription_billing_weight_for(plan, nodes[0]["id"], session) == 2.5
        assert store._subscription_billing_weight_for(plan, nodes[2]["id"], session) == 2.5

    sing_box = json.loads(client.get(f"/api/v1/subscribe/{token}?format=sing-box").text)
    visible_name = "[2.5] Two hop"
    selector = sing_box["outbounds"][0]
    assert selector["outbounds"] == [visible_name]
    final = next(outbound for outbound in sing_box["outbounds"] if outbound["tag"] == visible_name)
    upstream = next(
        outbound
        for outbound in sing_box["outbounds"]
        if outbound.get("tag", "").startswith("Two hop · hop 1")
    )
    assert final["detour"] == upstream["tag"]

    xray_response = client.get(f"/api/v1/subscribe/{token}?format=xray")
    assert xray_response.status_code == 200, xray_response.text
    xray = xray_response.json()
    assert xray["outbounds"][0]["tag"] == visible_name
    assert xray["outbounds"][0]["proxySettings"] == {"tag": upstream["tag"]}


def test_topology_rejects_same_server_loops_stale_writes_and_assigned_delete(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, nodes, same_server = fixture(client)
    same = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Invalid loop",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [same_server["id"]]},
            ],
        },
    )
    assert same.status_code == 409
    assert same.json()["detail"] == "A topology cannot revisit or reuse the same server"

    duplicate = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Repeated node",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[0]["id"]]},
            ],
        },
    )
    assert duplicate.status_code == 422

    created = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Editable topology",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
        },
    ).json()["topology"]
    stale = client.put(
        f"/api/v1/node-topologies/{created['id']}",
        json={
            "name": "Stale",
            "enabled": True,
            "stages": created["stages"],
            "layout": {},
            "expected_revision": "0" * 64,
        },
    )
    assert stale.status_code == 409

    updated_response = client.put(
        f"/api/v1/node-topologies/{created['id']}",
        json={
            "name": "Updated topology",
            "enabled": True,
            "stages": created["stages"],
            "layout": created["layout"],
            "expected_revision": created["revision"],
        },
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()["topology"]
    assert updated["revision"] != created["revision"]
    created = updated

    overlap = client.post(
        "/api/v1/plans",
        json={
            "name": "Ambiguous topology plan",
            "traffic_limit_gb": 10,
            "node_ids": [created["id"], nodes[0]["id"]],
        },
    )
    assert overlap.status_code == 409
    assert "overlapping topologies" in overlap.json()["detail"]

    token = create_user_plan(client, created["id"])
    assert token
    blocked = client.request(
        "DELETE",
        f"/api/v1/node-topologies/{created['id']}",
        json={
            "expected_revision": created["revision"],
            "confirm_name": created["name"],
        },
    )
    assert blocked.status_code == 409
    assert "subscription plans" in blocked.json()["detail"]

    listed = client.get("/api/v1/node-topologies")
    assert listed.status_code == 200
    assert len(listed.json()["topologies"]) == 1
    assert len(listed.json()["candidates"]) == 5


def test_topology_catalog_is_not_exported_or_imported_as_a_virtual_node(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, nodes, _same_server = fixture(client)
    topology = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Not a catalog node",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
        },
    ).json()["topology"]

    exported_response = client.get("/api/v1/catalog/export?include_credentials=true")
    assert exported_response.status_code == 200, exported_response.text
    catalog = exported_response.json()["catalog"]
    assert topology["name"] not in {node["name"] for node in catalog["nodes"]}
    assert all(node["node_type"] != "orchestrated" for node in catalog["nodes"])

    malicious = json.loads(json.dumps(catalog))
    malicious["nodes"][0]["node_type"] = "orchestrated"
    rejected = client.post("/api/v1/catalog/import", json={"catalog": malicious})
    assert rejected.status_code == 409
    assert "cannot be imported" in rejected.json()["detail"]

    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Catalog cannot restore this",
            "traffic_limit_gb": 10,
            "node_ids": [topology["id"]],
        },
    )
    assert plan.status_code == 201, plan.text
    blocked = client.get("/api/v1/catalog/export")
    assert blocked.status_code == 409
    assert "cannot represent node topologies" in blocked.json()["detail"]


def test_tampered_topology_fails_closed_without_virtual_credentials(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, nodes, same_server = fixture(client)
    topology = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Tampered topology",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
        },
    ).json()["topology"]
    client.post(
        "/api/v1/users",
        json={"username": "alice", "display_name": "Alice"},
    ).raise_for_status()
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Topology plan",
            "traffic_limit_gb": 10,
            "node_ids": [topology["id"]],
        },
    ).json()["plan"]

    with client.app.state.inventory._coordinated_session() as session:
        row = session.get(NodeTopologyModel, topology["id"])
        row.stages = [
            {
                "node_ids": [nodes[0]["id"], same_server["id"]],
                "load_balance_strategy": "round-robin",
            },
            {
                "node_ids": [nodes[2]["id"]],
                "load_balance_strategy": "round-robin",
            },
        ]
        session.commit()

    assigned = client.post("/api/v1/users/alice/plan", json={"plan_id": plan["id"]})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["provisioning_batches"] == []
    assert any("invalid or unavailable" in item for item in assigned.json()["warnings"])
    assert client.get("/api/v1/users/alice/credentials").json()["credentials"] == []

    preview = client.get("/api/v1/users/alice/subscription-preview?format=clash")
    assert preview.status_code == 200, preview.text
    assert preview.json()["nodes"] == []
    assert any("invalid or unavailable" in item for item in preview.json()["warnings"])


def test_delete_rejects_non_plan_user_override_reference(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    _servers, nodes, _same_server = fixture(client)
    topology = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Referenced topology",
            "stages": [
                {"node_ids": [nodes[0]["id"]]},
                {"node_ids": [nodes[2]["id"]]},
            ],
        },
    ).json()["topology"]
    client.post(
        "/api/v1/users",
        json={"username": "alice", "display_name": "Alice"},
    ).raise_for_status()
    with client.app.state.inventory._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        user.node_speed_limit_overrides = {topology["id"]: 10}
        session.commit()

    blocked = client.request(
        "DELETE",
        f"/api/v1/node-topologies/{topology['id']}",
        json={
            "expected_revision": topology["revision"],
            "confirm_name": topology["name"],
        },
    )
    assert blocked.status_code == 409
    assert "user limit overrides" in blocked.json()["detail"]
