import json
from pathlib import Path

import yaml
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.external_fetch import ExternalFetchResult
from open_node.services.inventory import (
    ManagedNodeModel,
    NodeTopologyModel,
    ProductUserModel,
    SubscriptionPlanModel,
)
from sqlalchemy import select

EXTERNAL_PREFIX = "/api/v1/external-subscriptions"


def external_nodes(
    client: TestClient,
    owner: str,
    source_name: str,
    proxies: list[dict],
) -> tuple[dict, list[dict]]:
    body = yaml.safe_dump({"proxies": proxies}, allow_unicode=True).encode()
    client.app.state.external_subscriptions.fetcher = (
        lambda _url, *, user_agent: ExternalFetchResult(body=body, metadata={})
    )
    source_response = client.post(
        EXTERNAL_PREFIX,
        json={
            "owner_username": owner,
            "name": source_name,
            "url": (
                f"https://provider.example/{owner}/{source_name.replace(' ', '-')}"
                "?token=private-source-token"
            ),
        },
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()
    preview_response = client.post(
        f"{EXTERNAL_PREFIX}/{source['id']}/previews",
        json={"expected_revision": source["revision"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    confirmation = client.post(
        f"{EXTERNAL_PREFIX}/{source['id']}/previews/{preview['id']}/confirm",
        json={
            "expected_revision": preview["source_revision"],
            "accept_changes": True,
            "selected_node_ids": [
                node["node_id"] for node in preview["nodes"] if node["selectable"]
            ],
        },
    )
    assert confirmation.status_code == 200, confirmation.text
    detail = client.get(f"{EXTERNAL_PREFIX}/{source['id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()["source"], detail.json()["nodes"]


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


def test_topology_load_balance_is_atomic_and_renders_supported_client_graphs(
    tmp_path: Path,
) -> None:
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

    for client_format in ("sing-box", "xray"):
        preview = client.get(f"/api/v1/users/alice/subscription-preview?format={client_format}")
        assert preview.status_code == 200, preview.text
        assert len(preview.json()["nodes"]) == 1
        assert preview.json()["nodes"][0]["node_id"] == topology["id"]
        assert preview.json()["nodes"][0]["available"] is True
        assert preview.json()["nodes"][0]["reason"] is None

    sing_box_response = client.get(f"/api/v1/subscribe/{token}?format=sing-box")
    assert sing_box_response.status_code == 200, sing_box_response.text
    sing_box = sing_box_response.json()
    sing_group = next(
        outbound for outbound in sing_box["outbounds"] if outbound["type"] == "urltest"
    )
    assert len(sing_group["outbounds"]) == 2
    assert "strategy" not in sing_group
    sing_final = next(
        outbound for outbound in sing_box["outbounds"] if outbound["tag"] == topology["name"]
    )
    assert sing_final["detour"] == sing_group["tag"]
    assert sing_box["outbounds"][0]["outbounds"] == [topology["name"]]

    xray_response = client.get(f"/api/v1/subscribe/{token}?format=xray")
    assert xray_response.status_code == 200, xray_response.text
    xray = xray_response.json()
    balancer = xray["routing"]["balancers"][0]
    assert balancer["strategy"] == {"type": "roundRobin"}
    assert len(balancer["selector"]) == 1
    selector_prefix = balancer["selector"][0]
    balanced = [
        outbound for outbound in xray["outbounds"] if outbound["tag"].startswith(selector_prefix)
    ]
    assert len(balanced) == 2
    loopback = next(
        outbound for outbound in xray["outbounds"] if outbound["protocol"] == "loopback"
    )
    rule = xray["routing"]["rules"][0]
    assert rule == {
        "type": "field",
        "inboundTag": [loopback["settings"]["inboundTag"]],
        "balancerTag": balancer["tag"],
    }
    xray_final = next(
        outbound for outbound in xray["outbounds"] if outbound["tag"] == topology["name"]
    )
    assert xray_final["proxySettings"] == {"tag": loopback["tag"]}

    preview = client.get("/api/v1/users/alice/subscription-preview?format=uri-list")
    assert preview.status_code == 200, preview.text
    assert preview.json()["nodes"][0]["available"] is False
    assert "chained proxies" in preview.json()["nodes"][0]["reason"]
    rejected = client.get(f"/api/v1/subscribe/{token}?format=uri-list")
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


def test_multistage_load_balance_renders_every_sing_box_and_xray_group(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, nodes, _same_server = fixture(client)
    server = (
        client.post("/api/v1/servers", json={"name": "edge-5"}).raise_for_status().json()["server"]
    )
    exit_node = physical_node(client, server["id"], "Physical 5", "vless-5")
    topology = (
        client.post(
            "/api/v1/node-topologies",
            json={
                "name": "Dual entry through dual relay",
                "stages": [
                    {"node_ids": [nodes[0]["id"], nodes[1]["id"]]},
                    {"node_ids": [nodes[2]["id"], nodes[3]["id"]]},
                    {"node_ids": [exit_node["id"]]},
                ],
            },
        )
        .raise_for_status()
        .json()["topology"]
    )
    token = create_user_plan(client, topology["id"])

    sing_box = client.get(f"/api/v1/subscribe/{token}?format=sing-box").raise_for_status().json()
    sing_groups = [outbound for outbound in sing_box["outbounds"] if outbound["type"] == "urltest"]
    assert len(sing_groups) == 2
    assert all(len(group["outbounds"]) == 2 and "strategy" not in group for group in sing_groups)
    first_group, second_group = sing_groups
    relays = [
        outbound
        for outbound in sing_box["outbounds"]
        if outbound["tag"] in second_group["outbounds"]
    ]
    assert {outbound["detour"] for outbound in relays} == {first_group["tag"]}
    sing_final = next(
        outbound for outbound in sing_box["outbounds"] if outbound["tag"] == topology["name"]
    )
    assert sing_final["detour"] == second_group["tag"]

    xray = client.get(f"/api/v1/subscribe/{token}?format=xray").raise_for_status().json()
    assert len(xray["routing"]["balancers"]) == 2
    assert len(xray["routing"]["rules"]) == 2
    assert all(
        balancer["strategy"] == {"type": "roundRobin"} for balancer in xray["routing"]["balancers"]
    )
    loopbacks = {
        outbound["settings"]["inboundTag"]: outbound["tag"]
        for outbound in xray["outbounds"]
        if outbound["protocol"] == "loopback"
    }
    assert len(loopbacks) == 2
    loopback_by_balancer = {
        rule["balancerTag"]: loopbacks[rule["inboundTag"][0]] for rule in xray["routing"]["rules"]
    }
    first_balancer, second_balancer = xray["routing"]["balancers"]
    second_members = [
        outbound
        for outbound in xray["outbounds"]
        if outbound["tag"].startswith(second_balancer["selector"][0])
    ]
    assert len(second_members) == 2
    assert {outbound["proxySettings"]["tag"] for outbound in second_members} == {
        loopback_by_balancer[first_balancer["tag"]]
    }
    xray_final = next(
        outbound for outbound in xray["outbounds"] if outbound["tag"] == topology["name"]
    )
    assert xray_final["proxySettings"] == {"tag": loopback_by_balancer[second_balancer["tag"]]}


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


def test_external_candidates_and_pure_external_topology_are_owner_bound_and_secret_safe(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    for username in ("alice", "bob"):
        client.post(
            "/api/v1/users",
            json={"username": username, "display_name": username.title()},
        ).raise_for_status()
    alice_secrets = [
        "11111111-aaaa-4111-8111-111111111111",
        "22222222-bbbb-4222-8222-222222222222",
        "33333333-cccc-4333-8333-333333333333",
    ]
    source, external = external_nodes(
        client,
        "alice",
        "Alice provider",
        [
            {
                "name": f"External {index}",
                "type": "vless",
                "server": f"external-{index}.example.com",
                "port": 443,
                "uuid": secret,
                "tls": True,
            }
            for index, secret in enumerate(alice_secrets, start=1)
        ],
    )

    listed = client.get("/api/v1/node-topologies")
    assert listed.status_code == 200, listed.text
    assert TestClient(client.app).get("/api/v1/node-topologies").status_code == 401
    candidates = {
        candidate["id"]: candidate for candidate in listed.json()["candidates"]
    }
    for node in external:
        assert candidates[node["id"]] == {
            "id": node["id"],
            "name": node["name"],
            "kind": "external",
            "protocol": "vless",
            "server_id": None,
            "server_name": None,
            "server_kind": None,
            "source_id": source["id"],
            "source_name": source["name"],
            "owner_username": "alice",
        }
    assert "private-source-token" not in listed.text
    assert all(secret not in listed.text for secret in alice_secrets)

    created = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "External load-balanced route",
            "stages": [
                {"node_ids": [external[0]["id"], external[1]["id"]]},
                {"node_ids": [external[2]["id"]]},
            ],
        },
    )
    assert created.status_code == 201, created.text
    topology = created.json()["topology"]
    with client.app.state.inventory._session() as session:
        virtual = session.get(ManagedNodeModel, topology["id"])
        assert virtual.server_id is None

    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Alice external topology",
            "traffic_limit_gb": 10,
            "node_ids": [topology["id"]],
        },
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["plan"]["id"]
    assigned = client.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan_id}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["provisioning_batches"] == []
    assert client.get("/api/v1/users/alice/credentials").json()["credentials"] == []

    token = client.post("/api/v1/users/alice/subscription-token").json()[
        "subscription"
    ]["token"]
    rendered = client.get(f"/api/v1/subscribe/{token}?format=clash")
    assert rendered.status_code == 200, rendered.text
    clash = yaml.safe_load(rendered.text)
    topology_proxies = [
        proxy
        for proxy in clash["proxies"]
        if proxy["name"] == topology["name"]
        or proxy["name"].startswith(topology["name"] + " · hop 1")
    ]
    assert len(topology_proxies) == 3
    final = next(proxy for proxy in topology_proxies if proxy["name"] == topology["name"])
    group = next(group for group in clash["proxy-groups"] if group["type"] == "load-balance")
    assert final["dialer-proxy"] == group["name"]
    expected_secret = {
        f"External {index}": secret
        for index, secret in enumerate(alice_secrets, start=1)
    }[external[2]["name"]]
    assert final["uuid"] == expected_secret
    assert not any(
        key.startswith("_open_node_") for proxy in clash["proxies"] for key in proxy
    )

    selected = client.get(
        f"/api/v1/subscribe/{token}?format=clash&node_id={topology['id']}"
    )
    assert selected.status_code == 200, selected.text
    selected_clash = yaml.safe_load(selected.text)
    assert len(selected_clash["proxies"]) == 3
    assert {proxy["name"] for proxy in selected_clash["proxies"]} == {
        proxy["name"] for proxy in topology_proxies
    }

    rejected = client.post(
        "/api/v1/users/bob/plan", json={"plan_id": plan_id}
    )
    assert rejected.status_code == 409, rejected.text
    assert "another subscriber" in rejected.json()["detail"]
    assert "alice" not in rejected.text.lower()
    assert all(secret not in rejected.text for secret in alice_secrets)
    invitation = client.post(
        "/api/v1/registration-invitations",
        json={"plan_id": plan_id, "expires_minutes": 60},
    )
    assert invitation.status_code == 409, invitation.text
    assert "registration_url" not in invitation.text


def test_mixed_topology_provisions_only_managed_nodes_and_rejects_cross_owner_graphs(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    _servers, managed, _same_server = fixture(client)
    for username in ("alice", "bob"):
        client.post(
            "/api/v1/users",
            json={"username": username, "display_name": username.title()},
        ).raise_for_status()
    alice_secret = "44444444-dddd-4444-8444-444444444444"
    _alice_source, alice_nodes = external_nodes(
        client,
        "alice",
        "Alice exit",
        [
            {
                "name": "Alice external exit",
                "type": "vless",
                "server": "alice-exit.example.com",
                "port": 443,
                "uuid": alice_secret,
                "tls": True,
            }
        ],
    )
    _bob_source, bob_nodes = external_nodes(
        client,
        "bob",
        "Bob exit",
        [
            {
                "name": "Bob external exit",
                "type": "vless",
                "server": "bob-exit.example.com",
                "port": 443,
                "uuid": "55555555-eeee-4555-8555-555555555555",
                "tls": True,
            }
        ],
    )
    cross_owner = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Forbidden cross-owner route",
            "stages": [
                {"node_ids": [alice_nodes[0]["id"]]},
                {"node_ids": [bob_nodes[0]["id"]]},
            ],
        },
    )
    assert cross_owner.status_code == 409, cross_owner.text
    assert cross_owner.json()["detail"] == (
        "Topology external nodes must belong to one subscriber"
    )
    assert alice_secret not in cross_owner.text

    created = client.post(
        "/api/v1/node-topologies",
        json={
            "name": "Managed entry to external exit",
            "stages": [
                {"node_ids": [managed[0]["id"]]},
                {"node_ids": [alice_nodes[0]["id"]]},
            ],
        },
    )
    assert created.status_code == 201, created.text
    topology = created.json()["topology"]
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Mixed topology plan",
            "traffic_limit_gb": 10,
            "node_ids": [topology["id"]],
        },
    ).json()["plan"]
    assigned = client.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan["id"]}
    )
    assert assigned.status_code == 200, assigned.text
    assert len(assigned.json()["provisioning_batches"]) == 1
    credentials = client.get("/api/v1/users/alice/credentials").json()["credentials"]
    assert {credential["node_id"] for credential in credentials} == {managed[0]["id"]}

    owner_swap = client.put(
        f"/api/v1/node-topologies/{topology['id']}",
        json={
            "name": topology["name"],
            "enabled": True,
            "stages": [
                {"node_ids": [managed[0]["id"]]},
                {"node_ids": [bob_nodes[0]["id"]]},
            ],
            "layout": {},
            "expected_revision": topology["revision"],
        },
    )
    assert owner_swap.status_code == 409, owner_swap.text
    assert "another assigned subscriber" in owner_swap.json()["detail"]
    assert alice_secret not in owner_swap.text

    token = client.post("/api/v1/users/alice/subscription-token").json()[
        "subscription"
    ]["token"]
    clash = yaml.safe_load(
        client.get(
            f"/api/v1/subscribe/{token}?format=clash&node_id={topology['id']}"
        ).text
    )
    assert len(clash["proxies"]) == 2
    final = next(proxy for proxy in clash["proxies"] if proxy["name"] == topology["name"])
    assert final["uuid"] == alice_secret
    assert final["dialer-proxy"].startswith(topology["name"] + " · hop 1")
