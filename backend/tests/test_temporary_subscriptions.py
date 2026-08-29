import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import TemporarySubscriptionModel
from test_subscriptions import create_catalog_fixture, make_client, sqlite_url


def assigned(client):
    _token, server_id, node_id, plan_id = create_catalog_fixture(client)
    client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    return server_id, node_id, plan_id


def create(client, node_id, *, max_access=2, label="Weekend share"):
    return client.post(
        "/api/v1/temporary-subscriptions",
        json={
            "username": "alice",
            "label": label,
            "node_ids": [node_id],
            "max_access": max_access,
            "expires_in_seconds": 300,
        },
    )


def test_temporary_share_survives_restart_counts_successes_and_supports_formats(tmp_path):
    database = tmp_path / "open-node-test.db"
    client = make_client(tmp_path)
    _server_id, node_id, _plan_id = assigned(client)

    created = create(client, node_id)
    assert created.status_code == 201, created.text
    assert created.headers["Cache-Control"] == "no-store"
    share = created.json()
    assert share["status"] == "active" and share["access_count"] == 0
    assert "/t/" in share["subscription_url"]
    path = share["subscription_url"].split("testserver", 1)[1]

    invalid_node = client.get(path + f"?format=xray&node_id={uuid4()}")
    assert invalid_node.status_code == 404
    assert (
        client.get("/api/v1/temporary-subscriptions").json()["subscriptions"][0]["access_count"]
        == 0
    )

    xray = client.get(path + "?format=xray")
    assert xray.status_code == 200
    assert "subscription-userinfo" not in xray.headers
    assert base64.b64decode(xray.headers["profile-title"].split(":", 1)[1]).decode() == (
        "Weekend share"
    )
    assert json.loads(xray.text)["outbounds"][0]["protocol"] == "vless"

    restarted = TestClient(create_app(Settings(database_url=sqlite_url(database))))
    uri = restarted.get(path + "?format=uri-list")
    assert uri.status_code == 200 and uri.text.startswith("vless://")
    exhausted = restarted.get(path)
    assert exhausted.status_code == 404
    assert exhausted.headers["Cache-Control"] == "no-store"
    assert exhausted.json()["detail"] == "temporary subscription not found"

    listed = client.get("/api/v1/temporary-subscriptions")
    assert listed.headers["Cache-Control"] == "no-store"
    assert listed.json()["subscriptions"][0]["status"] == "exhausted"
    assert listed.json()["subscriptions"][0]["access_count"] == 2
    removed = client.delete(f"/api/v1/temporary-subscriptions/{share['id']}")
    assert removed.json() == {"id": share["id"], "deleted": True, "license_required": False}
    assert client.get("/api/v1/temporary-subscriptions").json()["subscriptions"] == []


def test_temporary_share_validates_plan_nodes_and_expiry(tmp_path):
    client = make_client(tmp_path)
    server_id, node_id, _plan_id = assigned(client)
    outside = client.post(
        "/api/v1/nodes",
        json={
            "name": "Outside",
            "server_id": server_id,
            "protocol": "vmess",
            "node_type": "physical",
            "inbound_tag": "outside",
            "config": {
                "name": "Outside",
                "type": "vmess",
                "server": "outside.example",
                "port": 443,
                "uuid": "template",
            },
        },
    ).json()["node"]["id"]
    assert create(client, outside).status_code == 409
    missing = create(client, str(uuid4()))
    assert missing.status_code == 404

    share = create(client, node_id, max_access=1).json()
    application = client.app
    with application.state.inventory._coordinated_session() as session:
        row = session.get(TemporarySubscriptionModel, share["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    path = share["subscription_url"].split("testserver", 1)[1]
    expired = client.get(path)
    assert expired.status_code == 404
    assert expired.headers["Cache-Control"] == "no-store"
    assert expired.json()["detail"] == "temporary subscription not found"
    assert (
        client.get("/api/v1/temporary-subscriptions").json()["subscriptions"][0]["status"]
        == "expired"
    )


def test_temporary_share_access_limit_is_atomic(tmp_path):
    client = make_client(tmp_path)
    _server_id, node_id, _plan_id = assigned(client)
    share = create(client, node_id, max_access=1).json()
    path = share["subscription_url"].split("testserver", 1)[1] + "?format=xray"

    clients = [TestClient(client.app), TestClient(client.app)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda item: item.get(path).status_code, clients))
    assert statuses == [200, 404]
    listed = client.get("/api/v1/temporary-subscriptions").json()["subscriptions"][0]
    assert listed["access_count"] == 1 and listed["status"] == "exhausted"
