import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.server_sharing import FederationCommandRead, FederationServerInfo
from open_node.main import create_app
from open_node.services.external_fetch import ExternalFetchError
from open_node.services.federation_transport import FederationHTTPTransport


def make_app(tmp_path: Path):
    database = tmp_path / "open-node.db"
    app = create_app(Settings(database_url=f"sqlite:///{database.as_posix()}"))
    return app, database


def make_client(tmp_path: Path):
    app, database = make_app(tmp_path)
    return authenticated_client(app), database


def create_server(client: TestClient, name="共享源站"):
    response = client.post(
        "/api/v1/servers",
        json={
            "name": name,
            "ip_address": "203.0.113.10",
            "domain": "edge.example.com",
            "xray_mode": "external",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def lease_and_complete(client, agent_token, command_id, body):
    # Successful Xray writes can enqueue a snapshot refresh before the next
    # shared command. Drain those real durability commands in queue order.
    for _attempt in range(8):
        leased = client.post(
            "/api/v1/agents/commands/lease",
            json={"token": agent_token, "max_commands": 1},
        )
        assert leased.status_code == 200, leased.text
        commands = leased.json()["commands"]
        assert len(commands) == 1
        current = commands[0]
        completed = client.post(
            f"/api/v1/agents/commands/{current['id']}/result",
            json={
                "token": agent_token,
                "status": 200,
                "body": body if current["id"] == command_id else {"success": True},
            },
        )
        assert completed.status_code == 200, completed.text
        if current["id"] == command_id:
            return
    raise AssertionError(f"shared command was not dispatchable: {command_id}")


def test_limited_share_is_one_time_scoped_and_revocable(tmp_path: Path):
    client, database = make_client(tmp_path)
    server = create_server(client)
    server_id = server["server"]["id"]

    created = client.post(
        "/api/v1/server-shares",
        json={
            "server_id": server_id,
            "label": "租户甲",
            "allow_manage_xray": False,
        },
    )
    assert created.status_code == 201, created.text
    share = created.json()["share"]
    token = created.json()["share_token"]
    assert len(token) == 43
    assert token not in client.get(
        "/api/v1/server-shares", params={"server_id": server_id}
    ).text
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM server_shares WHERE id=?", (share["id"],)
        ).fetchone()[0]
        assert token not in stored

    assert client.get(
        "/api/v1/federation/server-info", headers={"X-Share-Token": "A" * 43}
    ).status_code == 401
    assert client.get("/api/v1/federation/commands/{command_id}").status_code == 401
    info = client.get(
        "/api/v1/federation/server-info", headers={"X-Share-Token": token}
    )
    assert info.status_code == 200, info.text
    assert info.json()["name"] == "共享源站"
    assert info.json()["license_required"] is False

    forbidden = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={"method": "GET", "path": "/api/child/outbounds"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "server_share_forbidden"

    added = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "add", "inbound": {"tag": "tenant-a"}},
        },
    )
    assert added.status_code == 201, added.text
    lease_and_complete(
        client, server["agent_token"], added.json()["id"], {"success": True}
    )

    foreign_remove = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "remove", "tag": "someone-else"},
        },
    )
    assert foreign_remove.status_code == 403

    listed = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={"method": "GET", "path": "/api/child/inbounds"},
    )
    assert listed.status_code == 201, listed.text
    command_id = listed.json()["id"]
    lease_and_complete(
        client,
        server["agent_token"],
        command_id,
        {"success": True, "inbounds": [{"tag": "tenant-a"}, {"tag": "private"}]},
    )
    result = client.get(
        f"/api/v1/federation/commands/{command_id}",
        headers={"X-Share-Token": token},
    )
    assert result.status_code == 200, result.text
    assert result.json()["result_body"]["inbounds"] == [{"tag": "tenant-a"}]

    another = client.post(
        "/api/v1/server-shares",
        json={"server_id": server_id, "label": "租户乙"},
    ).json()
    assert client.get(
        f"/api/v1/federation/commands/{command_id}",
        headers={"X-Share-Token": another["share_token"]},
    ).status_code == 404

    revoked = client.post(
        f"/api/v1/server-shares/{share['id']}/revoke",
        json={"expected_revision": share["revision"], "delete_inbounds": True},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True
    assert len(revoked.json()["cleanup_commands"]) == 1
    assert client.get(
        "/api/v1/federation/server-info", headers={"X-Share-Token": token}
    ).status_code == 401


def test_failed_add_is_removed_from_limited_share_ownership(tmp_path: Path):
    client, _database = make_client(tmp_path)
    server = create_server(client)
    share = client.post(
        "/api/v1/server-shares",
        json={"server_id": server["server"]["id"]},
    ).json()
    token = share["share_token"]
    added = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "add", "inbound": {"tag": "failed-tag"}},
        },
    ).json()
    leased = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": server["agent_token"], "max_commands": 1},
    ).json()["commands"]
    assert leased[0]["id"] == added["id"]
    failed = client.post(
        f"/api/v1/agents/commands/{added['id']}/result",
        json={"token": server["agent_token"], "status": 500, "error": "private"},
    )
    assert failed.status_code == 200
    polled = client.get(
        f"/api/v1/federation/commands/{added['id']}",
        headers={"X-Share-Token": token},
    )
    assert polled.json()["failed"] is True
    assert polled.json()["result_body"] is None
    remove = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "remove", "tag": "failed-tag"},
        },
    )
    assert remove.status_code == 403
    assert "private" not in polled.text


class FakeFederationTransport:
    def __init__(self):
        self.info = FederationServerInfo(
            name="上游共享机", status="connected", ip_address="198.51.100.20",
            ip_address_v6=None, domain="owner-edge.example", domain_v6=None,
            ipv6_enabled=False, xray_mode="external", traffic_limit=1024,
            traffic_used=16, current_upload_speed=2, current_download_speed=3,
            xray_running=True, xray_version="25.8.3", last_heartbeat=datetime.now(UTC),
        )
        self.managed = None

    def server_info(self, _owner_url, _token):
        return self.info

    def manage(self, _owner_url, _token, payload):
        self.managed = payload
        now = datetime.now(UTC)
        return FederationCommandRead(
            id=uuid4(), method=payload.method, path=payload.path, status="succeeded",
            result_status=200, result_body={"success": True}, failed=False,
            created_at=now, completed_at=now,
        )

    def command(self, _owner_url, _token, command_id):
        now = datetime.now(UTC)
        return FederationCommandRead(
            id=command_id, method="GET", path="/api/child/inbounds",
            status="succeeded", result_status=200, result_body={"inbounds": []},
            failed=False, created_at=now, completed_at=now,
        )


def test_imported_server_encrypts_token_prefixes_tags_and_uses_revision(tmp_path: Path):
    app, database = make_app(tmp_path)
    transport = FakeFederationTransport()
    app.state.server_sharing.transport = transport
    client = authenticated_client(app)
    token = "Z" * 43

    created = client.post(
        "/api/v1/server-federation",
        json={
            "owner_url": "https://owner.example/control/",
            "share_token": token,
            "name": "异地节点",
            "prefix": "site-",
        },
    )
    assert created.status_code == 201, created.text
    imported = created.json()
    imported_id = imported["id"]
    assert imported["owner_url"] == "https://owner.example/control"
    assert imported["info"]["name"] == "上游共享机"
    servers = client.get("/api/v1/servers").raise_for_status().json()
    projection = next(server for server in servers if server["id"] == imported_id)
    assert projection["name"] == "异地节点"
    assert projection["status"] == "connected"
    assert projection["ip_address"] == "198.51.100.20"
    assert projection["current_upload_speed"] == 2
    assert projection["current_download_speed"] == 3
    assert projection["is_federated"] is True
    assert projection["federation_owner_url"] == "https://owner.example/control"
    assert projection["federation_prefix"] == "site-"
    assert projection["federation_allow_manage_xray"] is False
    traffic = client.get(f"/api/v1/servers/{imported_id}/traffic").raise_for_status().json()
    assert traffic["used"] == 16
    scan = client.get(
        f"/api/v1/servers/{imported_id}/scan/latest"
    ).raise_for_status().json()["scan"]
    assert scan["xray_running"] is True
    assert scan["xray_version"] == "25.8.3"
    ddns = client.get("/api/v1/ddns").raise_for_status().json()["servers"]
    shared_ddns = next(server for server in ddns if server["server_id"] == imported_id)
    assert shared_ddns["is_federated"] is True
    assert client.get(f"/api/v1/servers/{imported_id}/removal").status_code == 409
    assert client.put(
        f"/api/v1/servers/{imported_id}/traffic",
        json={
            "traffic_limit": 2048,
            "traffic_reset_day": 1,
            "traffic_source": "xray",
            "traffic_stats_mode": "both",
        },
    ).status_code == 409
    assert client.post(f"/api/v1/servers/{imported_id}/traffic/reset").status_code == 409
    assert client.post(
        f"/api/v1/servers/{imported_id}/commands",
        json={"method": "GET", "path": "/api/child/system/info"},
    ).status_code == 409
    assert client.post(
        "/api/v1/server-shares",
        json={"server_id": imported_id, "label": "禁止二次分享"},
    ).status_code == 403
    with sqlite3.connect(database) as connection:
        sealed = connection.execute("SELECT token_secret FROM federated_servers").fetchone()[0]
        assert token not in sealed
    assert (tmp_path / "federation" / "vault.key").stat().st_mode & 0o077 == 0

    managed = client.post(
        f"/api/v1/server-federation/{imported['id']}/manage",
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "add", "inbound": {"tag": "demo"}},
        },
    )
    assert managed.status_code == 200, managed.text
    assert transport.managed.body["inbound"]["tag"] == "site-demo"

    transport.info = transport.info.model_copy(update={
        "status": "offline", "ip_address": "198.51.100.21",
        "traffic_limit": 2048, "traffic_used": 32,
        "current_upload_speed": 5, "current_download_speed": 8,
        "xray_running": False, "xray_version": "26.8.31",
    })
    refreshed = client.post(
        f"/api/v1/server-federation/{imported['id']}/refresh",
        json={"expected_revision": imported["revision"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["revision"] == 1
    projection = next(
        server for server in client.get("/api/v1/servers").raise_for_status().json()
        if server["id"] == imported_id
    )
    assert (projection["status"], projection["ip_address"]) == (
        "offline", "198.51.100.21"
    )
    assert (projection["current_upload_speed"], projection["current_download_speed"]) == (5, 8)
    assert client.get(f"/api/v1/servers/{imported_id}/traffic").json()["used"] == 32
    scan = client.get(f"/api/v1/servers/{imported_id}/scan/latest").json()["scan"]
    assert (scan["xray_running"], scan["xray_version"]) == (False, "26.8.31")
    stale = client.post(
        f"/api/v1/server-federation/{imported['id']}/refresh",
        json={"expected_revision": 0},
    )
    assert stale.status_code == 409
    deleted = client.post(
        f"/api/v1/server-federation/{imported['id']}/delete",
        json={"expected_revision": 1, "confirm": True},
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/server-federation").json()["servers"] == []
    assert all(
        server["id"] != imported_id
        for server in client.get("/api/v1/servers").raise_for_status().json()
    )
    assert all(
        server["server_id"] != imported_id
        for server in client.get("/api/v1/ddns").raise_for_status().json()["servers"]
    )


def test_federation_transport_maps_private_owner_without_network_disclosure():
    with patch(
        "open_node.services.federation_transport._resolve_public",
        side_effect=ExternalFetchError("unsafe_target"),
    ):
        try:
            FederationHTTPTransport().server_info(
                "https://owner.example", "A" * 43
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "server_share_owner_unavailable"
            assert "owner.example" not in str(exc)
        else:
            raise AssertionError("private owner address was accepted")


def test_sharing_requests_are_strict_bounded_and_non_echoing(tmp_path: Path):
    client, _database = make_client(tmp_path)
    server = create_server(client)
    response = client.post(
        "/api/v1/server-shares",
        content=(
            b'{"server_id":"' + server["server"]["id"].encode()
            + b'","label":"x","label":"secret"}'
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    oversized = client.post(
        "/api/v1/server-shares",
        content=b"{" + b" " * (64 * 1024) + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
