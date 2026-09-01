import base64
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.inventory import AgentCommandCreate, AgentCommandResultRequest
from open_node.domain.server_sharing import FederationCommandRead, FederationServerInfo
from open_node.main import create_app
from open_node.services.external_fetch import ExternalFetchError
from open_node.services.federation_crypto import (
    FEDERATION_ENCRYPTED_HEADER,
    FEDERATION_KEY_EXCHANGE_HEADER,
    derive_federation_session,
    generate_ephemeral,
)
from open_node.services.federation_transport import FederationHTTPTransport
from open_node.services.secure_channel import decode_public_key
from open_node.services.subscription_access import revision


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

    allowed_access = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/subscription-access",
            "body": {"revision": "0" * 64, "entries": [{"tag": "tenant-a"}]},
        },
    )
    assert allowed_access.status_code == 201, allowed_access.text
    forbidden_access = client.post(
        "/api/v1/federation/manage",
        headers={"X-Share-Token": token},
        json={
            "method": "POST",
            "path": "/api/child/subscription-access",
            "body": {"revision": "0" * 64, "entries": [{"tag": "private"}]},
        },
    )
    assert forbidden_access.status_code == 403

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


def test_official_consumer_can_manage_through_encrypted_legacy_owner_route(tmp_path: Path):
    app, _database = make_app(tmp_path)
    client = authenticated_client(app)
    server = create_server(client)
    shared = client.post(
        "/api/v1/server-shares",
        json={"server_id": server["server"]["id"]},
    ).raise_for_status().json()
    token = shared["share_token"]

    async def complete_immediately(store, command):
        leased = store.lease_command_for_push(command.id)
        assert leased is not None
        return store.complete_command(
            leased.id,
            AgentCommandResultRequest(
                token=server["agent_token"],
                status=200,
                body={"success": True, "owner": "open-node"},
            ),
        )

    app.state.agent_connections.dispatch_command = complete_immediately

    def wire(tag):
        body = json.dumps(
            {"action": "add", "inbound": {"tag": tag}}, separators=(",", ":")
        ).encode()
        return json.dumps(
            {
                "method": "POST",
                "path": "/api/child/inbounds",
                "body": base64.b64encode(body).decode(),
            },
            separators=(",", ":"),
        ).encode()

    consumer_private, consumer_public = generate_ephemeral()
    negotiated = client.post(
        "/api/federation/manage",
        content=wire("official-one"),
        headers={
            "Content-Type": "application/json",
            "X-Share-Token": token,
            FEDERATION_KEY_EXCHANGE_HEADER: base64.b64encode(consumer_public).decode(),
        },
    )
    assert negotiated.status_code == 200, negotiated.text
    assert negotiated.json() == {"success": True, "owner": "open-node"}
    owner_public = decode_public_key(negotiated.headers[FEDERATION_KEY_EXCHANGE_HEADER])
    session = derive_federation_session(
        consumer_private,
        owner_public,
        consumer_public,
        token,
        is_initiator=True,
    )

    encrypted = client.post(
        "/api/federation/manage",
        content=session.encrypt(wire("official-two")),
        headers={
            "Content-Type": "application/octet-stream",
            "X-Share-Token": token,
            FEDERATION_ENCRYPTED_HEADER: "1",
        },
    )
    assert encrypted.status_code == 200
    assert encrypted.headers[FEDERATION_ENCRYPTED_HEADER] == "1"
    assert json.loads(session.decrypt(encrypted.content)) == {
        "success": True,
        "owner": "open-node",
    }


class FakeFederationTransport:
    def __init__(self):
        self.info = FederationServerInfo(
            name="上游共享机", status="connected", ip_address="198.51.100.20",
            ip_address_v6=None, domain="owner-edge.example", domain_v6=None,
            ipv6_enabled=False, xray_mode="external", traffic_limit=1024,
            traffic_used=16, current_upload_speed=2, current_download_speed=3,
            xray_running=True, xray_version="25.8.3",
            nginx={
                "running": True, "installed": True, "available": True,
                "version": "nginx version: nginx/1.29.1",
                "tunnel_deploy": 1, "mode": "managed",
                "config_path": "/etc/nginx/nginx.conf",
                "certificate_dir": "/etc/nginx/certs", "html_path": "/var/www/html",
            },
            probe_sys={
                "cpu_pct": 12.5, "loadavg": "0.1 0.2 0.3",
                "mem_used": 128, "mem_total": 512,
                "disk_used": 1024, "disk_total": 4096,
                "uptime": 3600, "cpu_model": "Shared CPU", "cpu_cores": 2,
                "cpu_threads": 4, "os": "Debian", "kernel": "6.1", "arch": "amd64",
                "upload_speed": 2, "download_speed": 3,
                "cumulative_up": 100, "cumulative_down": 200,
                "has_cpu": True, "has_mem": True, "has_disk": True,
                "has_network": True, "at": int(datetime.now(UTC).timestamp()),
            },
            last_heartbeat=datetime.now(UTC),
        )
        self.managed = None

    def server_info(self, _owner_url, _token):
        return self.info

    def manage(self, _owner_url, _token, payload):
        self.managed = payload
        now = datetime.now(UTC)
        body = {"success": True}
        if payload.method == "GET" and payload.path == "/api/child/inbounds":
            body = {"success": True, "inbounds": [{
                "tag": "site-demo", "protocol": "vless", "port": 443,
                "settings": {"clients": []},
                "streamSettings": {"network": "tcp"},
            }]}
        elif payload.path == "/api/child/subscription-access":
            entries = payload.body["entries"]
            body = {
                "success": True,
                "restart_required": False,
                "access": {
                    "applied": True,
                    "revision": payload.body["revision"],
                    "enabled": sum(item["enabled"] for item in entries),
                    "disabled": sum(not item["enabled"] for item in entries),
                },
            }
        return FederationCommandRead(
            id=uuid4(), method=payload.method, path=payload.path, status="succeeded",
            result_status=200, result_body=body, failed=False,
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
    assert scan["nginx"]["running"] is True
    assert scan["nginx"]["version"] == "nginx version: nginx/1.29.1"
    telemetry = client.get(
        f"/api/v1/servers/{imported_id}/telemetry/latest"
    ).raise_for_status().json()["latest"]
    assert telemetry["sysmetrics"]["cpu_pct"] == 12.5
    assert telemetry["system"]["tx_total"] == 100
    assert telemetry["system"]["rx_total"] == 200
    ddns = client.get("/api/v1/ddns").raise_for_status().json()["servers"]
    shared_ddns = next(server for server in ddns if server["server_id"] == imported_id)
    assert shared_ddns["is_federated"] is True

    transport.info = transport.info.model_copy(update={
        "traffic_used": 24, "current_upload_speed": 4, "current_download_speed": 6,
    })
    auto_now = datetime.now(UTC) + timedelta(seconds=6)
    jobs = app.state.server_sharing.automatic_refresh_jobs(now=auto_now)
    assert len(jobs) == 1
    assert app.state.server_sharing.apply_automatic_refresh(
        jobs[0], transport.info, now=auto_now
    ) is True
    automatically_refreshed = client.get("/api/v1/server-federation").json()["servers"][0]
    assert automatically_refreshed["revision"] == 0
    assert automatically_refreshed["info"]["traffic_used"] == 24
    assert client.get(f"/api/v1/servers/{imported_id}/traffic").json()["used"] == 24
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

    synced = client.post(
        f"/api/v1/server-federation/{imported_id}/manage",
        json={
            "method": "GET", "path": "/api/child/inbounds",
            "body": None, "timeout_ms": 30_000,
        },
    )
    assert synced.status_code == 200, synced.text
    drafts = client.get(
        f"/api/v1/servers/{imported_id}/xray/runtime/node-drafts"
    ).raise_for_status().json()
    assert drafts["has_scan"] is True
    assert drafts["drafts"][0]["source_tag"] == "site-demo"
    assert drafts["drafts"][0]["create_available"] is True
    imported_nodes = client.post(
        f"/api/v1/servers/{imported_id}/xray/runtime/nodes/import", json={}
    ).raise_for_status().json()
    assert imported_nodes["created_count"] == 1
    assert imported_nodes["created_nodes"][0]["server_id"] == imported_id
    assert imported_nodes["created_nodes"][0]["inbound_tag"] == "site-demo"

    entries = [{
        "tag": "site-demo", "protocol": "vless",
        "client": {"id": str(uuid4()), "email": "alice@shared.example"},
        "enabled": True, "routing_user_additions": [], "limiter": None,
    }]
    relay_command = app.state.inventory.create_command(
        imported_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/subscription-access",
            body={"revision": revision(entries), "entries": entries},
            timeout_ms=60_000,
        ),
    )
    relayed = app.state.server_sharing.dispatch_agent_command(relay_command)
    assert relayed.status.value == "succeeded"
    assert relayed.result_body["access"]["enabled"] == 1
    assert transport.managed.path == "/api/child/subscription-access"
    assert app.state.server_sharing.dispatch_agent_command(relayed).attempts == 1

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
        node["server_id"] != imported_id
        for node in client.get("/api/v1/nodes").raise_for_status().json()["nodes"]
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
