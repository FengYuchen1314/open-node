from pathlib import Path

import yaml
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.subscription_access import ENDPOINT as ACCESS_ENDPOINT
from test_subscriber_auth import login, provision


def setup(tmp_path: Path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'private-routes.db'}"))
    operator = authenticated_client(app)
    server = operator.post("/api/v1/servers", json={"name": "private-edge"}).json()
    server_id = server["server"]["id"]
    parent = operator.post(
        "/api/v1/nodes",
        json={
            "name": "Entry",
            "server_id": server_id,
            "protocol": "vless",
            "node_type": "physical",
            "inbound_tag": "entry-443",
            "client_template": {"flow": "xtls-rprx-vision"},
            "config": {
                "name": "Entry",
                "type": "vless",
                "server": "entry.example.com",
                "port": 443,
                "uuid": "template-id",
                "tls": True,
            },
        },
    ).json()["node"]
    target = operator.post(
        "/api/v1/nodes",
        json={
            "name": "Exit",
            "server_id": server_id,
            "protocol": "trojan",
            "node_type": "physical",
            "inbound_tag": "exit-443",
            "config": {
                "name": "Exit",
                "type": "trojan",
                "server": "exit.example.com",
                "port": 443,
                "password": "template-password",
                "tls": True,
            },
        },
    ).json()["node"]
    user = operator.post("/api/v1/users", json={"username": "alice"})
    assert user.status_code == 201, user.text
    plan = operator.post(
        "/api/v1/plans",
        json={
            "name": "Private routes",
            "traffic_limit_gb": 100,
            "cycle_days": 30,
            "node_ids": [parent["id"], target["id"]],
        },
    ).json()["plan"]
    assigned = operator.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan["id"]}
    )
    assert assigned.status_code == 200, assigned.text
    provision(operator)
    subscriber = TestClient(app, base_url="https://testserver")
    assert login(subscriber).status_code == 200
    policy = operator.put(
        "/api/v1/private-routed-nodes/policy",
        json={"enabled": True, "max_nodes": 2, "daily_limit": 5},
    )
    assert policy.status_code == 200, policy.text
    registered = operator.post(
        "/api/v1/agents/register",
        json={
            "token": server["agent_token"],
            "hostname": "private-agent",
            "capabilities": {
                "rpc": True,
                "native_limiter": True,
                "subscription_access": True,
            },
        },
    )
    assert registered.status_code == 201, registered.text
    return app, operator, subscriber, server, parent, target, plan


def lease(client, token, path=None):
    for _ in range(20):
        response = client.post(
            "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 1}
        )
        assert response.status_code == 200, response.text
        commands = response.json()["commands"]
        assert commands, f"No command became ready for {path}"
        command = commands[0]
        if path is None or command["path"] == path:
            return command
        complete(client, token, command)
    raise AssertionError(f"Command did not become ready for {path}")


def complete(client, token, command, *, success=True):
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": token,
            "status": 200 if success else 500,
            "body": {"success": success},
            "error": None if success else "fixture failure",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["command"]


def complete_access(client, token):
    command = lease(client, token, ACCESS_ENDPOINT)
    entries = command["body"]["entries"]
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": token,
            "status": 200,
            "body": {
                "success": True,
                "access": {
                    "applied": True,
                    "revision": command["body"]["revision"],
                    "enabled": sum(entry["enabled"] for entry in entries),
                    "disabled": sum(not entry["enabled"] for entry in entries),
                },
            },
        },
    )
    assert response.status_code == 200, response.text


def create_route(subscriber, parent, target):
    return subscriber.post(
        "/api/v1/account/private-routed-nodes",
        json={
            "label": "My-Exit",
            "parent_id": parent["id"],
            "target_node_id": target["id"],
        },
    )


def test_private_route_lifecycle_appears_only_in_owners_subscription(tmp_path):
    _app, operator, subscriber, server, parent, target, plan = setup(tmp_path)
    listing = subscriber.get("/api/v1/account/private-routed-nodes")
    assert listing.status_code == 200, listing.text
    assert listing.json()["policy"]["enabled"] is True
    assert {item["name"] for item in listing.json()["candidates"]} == {"Entry", "Exit"}

    response = create_route(subscriber, parent, target)
    assert response.status_code == 201, response.text
    created = response.json()
    node_id = created["node"]["id"]
    assert created["node"]["status"] == "provisioning"
    assert [item["status"] for item in created["commands"]] == [
        "pending",
        "waiting",
        "waiting",
    ]
    assert created["commands"][1]["body"]["outbound"]["settings"]["servers"][0][
        "address"
    ] == "exit.example.com"

    token = server["agent_token"]
    for expected_path in (
        "/api/child/inbounds",
        "/api/child/outbounds",
        "/api/child/routing",
    ):
        complete(operator, token, lease(operator, token, expected_path))

    active = subscriber.get("/api/v1/account/private-routed-nodes").json()
    assert active["nodes"][0]["status"] == "active"
    assert active["used_nodes"] == 1
    complete_access(operator, token)

    subscription = operator.post("/api/v1/users/alice/subscription-token").json()
    rendered = operator.get(
        f"/api/v1/subscribe/{subscription['subscription']['token']}"
    )
    assert rendered.status_code == 200, rendered.text
    proxies = yaml.safe_load(rendered.text)["proxies"]
    assert {proxy["name"] for proxy in proxies} == {"Entry", "Exit", "My-Exit"}
    assert next(proxy for proxy in proxies if proxy["name"] == "My-Exit")["server"] == (
        "entry.example.com"
    )

    private_node = next(
        node for node in operator.get("/api/v1/nodes").json()["nodes"] if node["id"] == node_id
    )
    rejected = operator.post(
        "/api/v1/plans",
        json={
            "name": "Invalid shared private plan",
            "traffic_limit_gb": 10,
            "cycle_days": 30,
            "node_ids": [private_node["id"]],
        },
    )
    assert rejected.status_code == 409

    deletion = subscriber.delete(f"/api/v1/account/private-routed-nodes/{node_id}")
    assert deletion.status_code == 200, deletion.text
    assert deletion.json()["node"]["status"] == "removing"
    for expected_path in (
        "/api/child/inbounds",
        "/api/child/routing",
        "/api/child/outbounds",
    ):
        complete(operator, token, lease(operator, token, expected_path))
    assert subscriber.get("/api/v1/account/private-routed-nodes").json()["nodes"] == []
    assert operator.get("/api/v1/plans").json()["plans"][0]["node_ids"] == [
        parent["id"],
        target["id"],
    ]


def test_failed_create_rolls_back_and_can_be_removed_locally(tmp_path):
    _app, operator, subscriber, server, parent, target, _plan = setup(tmp_path)
    response = create_route(subscriber, parent, target)
    assert response.status_code == 201, response.text
    node_id = response.json()["node"]["id"]
    token = server["agent_token"]
    complete(operator, token, lease(operator, token, "/api/child/inbounds"))
    complete(operator, token, lease(operator, token, "/api/child/outbounds"), success=False)
    rollback = lease(operator, token, "/api/child/outbounds")
    assert rollback["body"] == {
        "action": "remove",
        "tag": response.json()["commands"][1]["body"]["outbound"]["tag"],
    }
    complete(operator, token, rollback)
    complete(operator, token, lease(operator, token, "/api/child/inbounds"))
    failed = subscriber.get("/api/v1/account/private-routed-nodes").json()["nodes"][0]
    assert failed["status"] == "failed"
    assert failed["last_error"]
    deleted = subscriber.delete(f"/api/v1/account/private-routed-nodes/{node_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_id"] == node_id
    assert deleted.json()["commands"] == []


def test_failed_delete_restores_the_exact_private_client(tmp_path):
    _app, operator, subscriber, server, parent, target, _plan = setup(tmp_path)
    created = create_route(subscriber, parent, target).json()
    token = server["agent_token"]
    for expected_path in (
        "/api/child/inbounds",
        "/api/child/outbounds",
        "/api/child/routing",
    ):
        complete(operator, token, lease(operator, token, expected_path))
    complete_access(operator, token)

    node_id = created["node"]["id"]
    deletion = subscriber.delete(f"/api/v1/account/private-routed-nodes/{node_id}")
    assert deletion.status_code == 200, deletion.text
    complete(operator, token, lease(operator, token, "/api/child/inbounds"))
    complete(
        operator,
        token,
        lease(operator, token, "/api/child/routing"),
        success=False,
    )
    complete(operator, token, lease(operator, token, "/api/child/routing"))
    restored = lease(operator, token, "/api/child/inbounds")
    assert restored["body"]["action"] == "add-client"
    assert restored["body"]["client"]["flow"] == "xtls-rprx-vision"
    complete(operator, token, restored)
    row = subscriber.get("/api/v1/account/private-routed-nodes").json()["nodes"][0]
    assert row["status"] == "active"


def test_policy_quota_and_subscriber_privacy_are_enforced(tmp_path):
    app, operator, subscriber, _server, parent, target, plan = setup(tmp_path)
    created = create_route(subscriber, parent, target)
    assert created.status_code == 201, created.text
    operator.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    operator.post(
        "/api/v1/users/bob/plan", json={"plan_id": plan["id"]}
    ).raise_for_status()
    provision(operator, "bob")
    bob = TestClient(app, base_url="https://testserver")
    assert login(bob, username="bob").status_code == 200
    assert bob.get("/api/v1/account/private-routed-nodes").json()["nodes"] == []
    assert (
        bob.delete(
            f"/api/v1/account/private-routed-nodes/{created.json()['node']['id']}"
        ).status_code
        == 404
    )

    operator.put(
        "/api/v1/private-routed-nodes/policy",
        json={"enabled": False, "max_nodes": 1, "daily_limit": 1},
    )
    assert create_route(subscriber, parent, target).status_code == 409
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/v1/account/private-routed-nodes").status_code == 401
    assert anonymous.post("/api/v1/account/private-routed-nodes", json={}).status_code == 401


def test_user_removal_cleans_private_runtime_before_deleting_account(tmp_path):
    app, operator, subscriber, server, parent, target, _plan = setup(tmp_path)
    created = create_route(subscriber, parent, target).json()
    token = server["agent_token"]
    for expected_path in (
        "/api/child/inbounds",
        "/api/child/outbounds",
        "/api/child/routing",
    ):
        complete(operator, token, lease(operator, token, expected_path))
    complete_access(operator, token)

    settings = operator.get("/api/v1/users/alice/settings").json()
    started = operator.post(
        "/api/v1/users/alice/remove",
        json={
            "expected_revision": settings["revision"],
            "confirm_name": "alice",
            "acknowledge_runtime_restart": True,
        },
    )
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "pending"
    assert len(started.json()["commands"]) == 4
    for expected_path in (
        "/api/child/inbounds",
        "/api/child/routing",
        "/api/child/outbounds",
    ):
        complete(operator, token, lease(operator, token, expected_path))
    complete_access(operator, token)
    app.state.inventory._subscription_access().run_once()
    assert operator.get("/api/v1/users/alice/settings").status_code == 404
    assert operator.get("/api/v1/private-routed-nodes").json()["nodes"] == []
    assert created["node"]["id"] not in {
        node["id"] for node in operator.get("/api/v1/nodes").json()["nodes"]
    }
