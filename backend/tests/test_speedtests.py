import json
import time
from pathlib import Path

from conftest import authenticated_client
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.mihomo_speedtest import Measurement


def browser(tmp_path: Path):
    app = create_app(Settings(
        database_url=f"sqlite:///{(tmp_path / 'speedtests.db').as_posix()}",
        speedtest_state_dir=tmp_path / "mihomo",
    ))
    return authenticated_client(app)


def node_with_credential(client):
    server = client.post("/api/v1/servers", json={
        "name": "测速节点服务器", "ip_address": "203.0.113.30", "xray_mode": "external",
    }).json()["server"]
    assert client.post("/api/v1/users", json={
        "username": "speed-user", "display_name": "测速用户",
    }).status_code == 201
    node = client.post("/api/v1/nodes", json={
        "name": "东京 VLESS", "server_id": server["id"], "protocol": "vless",
        "inbound_tag": "vless-443", "config": {
            "name": "东京 VLESS", "type": "vless", "server": "edge.example.com",
            "port": 443, "tls": True,
        },
    }).json()["node"]
    plan = client.post("/api/v1/plans", json={
        "name": "测速套餐", "traffic_limit_gb": 100, "node_ids": [node["id"]],
    }).json()["plan"]
    assigned = client.post("/api/v1/users/speed-user/plan", json={
        "plan_id": plan["id"], "start_date": "2026-09-01",
    })
    assert assigned.status_code == 200, assigned.text
    return node


def wait_result(client, identifier):
    for _attempt in range(50):
        rows = client.get("/api/v1/speedtest/results", params={"limit": 20}).json()["results"]
        row = next(item for item in rows if item["id"] == identifier)
        if row["status"] != "running":
            return row
        time.sleep(0.02)
    raise AssertionError("speed test did not finish")


def test_local_speedtest_is_async_and_keeps_secret_proxy_out_of_responses(tmp_path, monkeypatch):
    client = browser(tmp_path)
    node = node_with_credential(client)
    captured = {}

    async def measured(proxy, **options):
        captured["proxy"] = proxy
        captured["options"] = options
        return Measurement(321.45, 28.4, "198.51.100.9", 12_345_678)

    monkeypatch.setattr(client.app.state.mihomo_speedtest, "run", measured)
    queued = client.post("/api/v1/speedtest/run", json={
        "node_id": node["id"], "threads": 8, "latency_only": False,
    })
    assert queued.status_code == 200, queued.text
    assert queued.json()["queued"] is True
    result = wait_result(client, queued.json()["result"]["id"])
    assert result["status"] == "ok"
    assert result["down_mbps"] == 321.45
    assert result["latency_ms"] == 28.4
    assert result["egress_ip"] == "198.51.100.9"
    assert result["bytes"] == 12_345_678
    assert captured["options"]["threads"] == 8
    assert captured["proxy"]["type"] == "vless"
    assert captured["proxy"]["uuid"]
    assert captured["proxy"]["uuid"] not in queued.text
    assert captured["proxy"]["uuid"] not in client.get(
        "/api/v1/speedtest/results", params={"latest": True}
    ).text


def test_speedtester_pair_rotate_revoke_and_reverse_websocket_dispatch(tmp_path):
    client = browser(tmp_path)
    # A context-managed TestClient keeps one ASGI event loop alive across the
    # HTTP request and reverse WebSocket, matching Uvicorn's runtime topology.
    with client:
        node = node_with_credential(client)
        created = client.post(
            "/api/v1/speedtest/testers/create", json={"name": "上海家庭宽带"}
        )
        assert created.status_code == 200, created.text
        secret = created.json()
        token, tester = secret["token"], secret["tester"]
        assert token not in client.get("/api/v1/speedtest/testers").text

        with client.websocket_connect(f"/api/speedtest/ws?token={token}") as websocket:
            websocket.send_json({
                "type": "hello", "version": "1.2.3", "caps": ["speedtest", "probe"]
            })
            assert websocket.receive_json() == {"type": "pong"}
            online = client.get("/api/v1/speedtest/testers").json()["testers"][0]
            assert online["online"] is True and online["version"] == "1.2.3"

            queued = client.post("/api/v1/speedtest/run", json={
                "node_id": node["id"], "tester_id": tester["id"],
                "threads": 1, "latency_only": True,
            })
            assert queued.status_code == 200, queued.text
            command = websocket.receive_json()
            assert command["type"] == "run" and command["latency_only"] is True
            proxy = json.loads(command["clash_config"])
            assert proxy["uuid"] and proxy["server"] == "edge.example.com"
            websocket.send_json({
                "type": "result", "job_id": command["job_id"], "status": "ok",
                "latency_ms": 19.5, "egress_ip": "198.51.100.10",
            })
            result = wait_result(client, queued.json()["result"]["id"])
            assert result["status"] == "ok" and result["latency_ms"] == 19.5

        rotated = client.post(
            "/api/v1/speedtest/testers/rotate-token", json={"id": tester["id"]}
        )
        assert rotated.status_code == 200 and rotated.json()["token"] != token
        with client.websocket_connect(f"/api/speedtest/ws?token={token}") as rejected:
            try:
                rejected.receive_json()
            except Exception:
                pass
        revoked = client.post("/api/v1/speedtest/testers/revoke", json={"id": tester["id"]})
        assert revoked.status_code == 204
        assert client.get("/api/v1/speedtest/testers").json()["testers"] == []


def test_speedtest_requires_an_issued_real_credential(tmp_path):
    client = browser(tmp_path)
    server = client.post("/api/v1/servers", json={"name": "空节点服务器"}).json()["server"]
    node = client.post("/api/v1/nodes", json={
        "name": "尚未分配", "server_id": server["id"], "protocol": "vless",
        "config": {"name": "尚未分配", "type": "vless", "server": "edge.example.com", "port": 443},
    }).json()["node"]
    response = client.post("/api/v1/speedtest/run", json={
        "node_id": node["id"], "threads": 1, "latency_only": True,
    })
    assert response.status_code == 409
    assert response.json()["code"] == "speedtest_credential_unavailable"


def test_speedtest_request_validation_is_secret_free(tmp_path):
    client = browser(tmp_path)
    response = client.post("/api/v1/speedtest/run", json={
        "node_id": "not-a-node", "url": "http://127.0.0.1/private",
        "threads": 999, "latency_only": False, "private": "do-not-reflect",
    })
    assert response.status_code == 422
    assert "do-not-reflect" not in response.text
