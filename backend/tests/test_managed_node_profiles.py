from pathlib import Path

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from sqlalchemy import inspect


def make_client(tmp_path: Path) -> TestClient:
    database = (tmp_path / "managed-profiles.db").as_posix()
    return authenticated_client(create_app(Settings(database_url=f"sqlite:///{database}")))


def create_server(client: TestClient, name: str, server_kind: str = "direct") -> dict:
    response = client.post(
        "/api/v1/servers",
        json={"name": name, "server_kind": server_kind},
    )
    assert response.status_code == 201, response.text
    return response.json()["server"]


def camouflage_node(
    server_id: str,
    name: str,
    profile: str,
    protocol: str,
    pool: str,
    sni: str,
) -> dict:
    return {
        "name": name,
        "server_id": server_id,
        "protocol": protocol,
        "protocol_profile": profile,
        "inbound_tag": name.lower().replace(" ", "-"),
        "camouflage_pool_id": pool,
        "camouflage_sni": sni,
        "config": {"port": 12345},
    }


def test_server_kind_and_creation_metadata_are_public_api_contracts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    direct = create_server(client, "direct")
    leased = create_server(client, "leased", "leased-line")
    residential = create_server(client, "home", "residential")

    assert [direct["server_kind"], leased["server_kind"], residential["server_kind"]] == [
        "direct",
        "leased-line",
        "residential",
    ]
    metadata = client.get("/api/v1/nodes/creation-metadata")
    assert metadata.status_code == 200
    body = metadata.json()
    assert body["server_kinds"] == {
        "direct": "公网直连",
        "leased-line": "专线",
        "residential": "家宽落地",
    }
    assert [entry["profile"] for entry in body["profiles"]] == [
        "vless-reality-vision",
        "vless-xhttp-reality-xmux",
        "anytls-shadowtls",
        "mieru",
        "socks5",
    ]
    socks = body["profiles"][-1]
    assert socks["allowed_server_kinds"] == ["direct", "residential"]
    assert socks["warning_server_kinds"] == ["direct"]
    assert "极度不推荐" in socks["warning"]
    assert "端口转发" in body["mieru_mapping_modes"]["manual"]

    columns = {
        column["name"]
        for column in inspect(client.app.state.inventory._engine).get_columns("servers")
    }
    assert "server_kind" in columns
    node_columns = {
        column["name"]
        for column in inspect(client.app.state.inventory._engine).get_columns("managed_nodes")
    }
    assert {
        "protocol_profile",
        "camouflage_pool_id",
        "camouflage_sni",
        "domestic_entry_ip",
        "domestic_entry_port",
        "mieru_port_mapping_mode",
        "ix_port",
    } <= node_columns


def test_three_managed_tls_profiles_share_443_but_require_unique_camouflage(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    server = create_server(client, "shared-443")
    cases = [
        (
            "Vision",
            "vless-reality-vision",
            "vless",
            "los-angeles-ucla",
            "www.ucla.edu",
        ),
        (
            "XHTTP",
            "vless-xhttp-reality-xmux",
            "vless",
            "los-angeles-ucsb",
            "www.ucsb.edu",
        ),
        (
            "AnyTLS",
            "anytls-shadowtls",
            "anytls",
            "los-angeles-apple",
            "www.apple.com",
        ),
    ]
    created = []
    commands = []
    for name, profile, protocol, pool, sni in cases:
        response = client.post(
            "/api/v1/nodes",
            json=camouflage_node(server["id"], name, profile, protocol, pool, sni),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        created.append(body["node"])
        commands.append(body["commands"])

    assert [node["config"]["port"] for node in created] == [443, 443, 443]
    assert created[0]["config"]["flow"] == "xtls-rprx-vision"
    assert created[1]["config"]["network"] == "xhttp"
    assert created[1]["config"]["xhttp-opts"]["reuse-settings"]["max-concurrency"] == (
        "16-32"
    )
    assert created[2]["config"]["shadow-tls-opts"] == {"version": 3}
    assert len({node["runtime_port"] for node in created}) == 3
    assert all(node["runtime_port"] >= 49_152 for node in created)
    assert all(items[0]["path"] == "/api/child/managed-protocols" for items in commands)
    assert all(items[-1]["path"] == "/api/child/nginx/shared-ingress" for items in commands)

    duplicate_pool = client.post(
        "/api/v1/nodes",
        json=camouflage_node(
            server["id"],
            "Duplicate pool",
            "vless-reality-vision",
            "vless",
            "LOS-ANGELES-UCLA",
            "www.ucla.edu",
        ),
    )
    assert duplicate_pool.status_code == 409
    assert duplicate_pool.json()["detail"] == "Camouflage pool is already used on this server"

    other_server = create_server(client, "other-shared-443")
    reused_elsewhere = client.post(
        "/api/v1/nodes",
        json=camouflage_node(
            other_server["id"],
            "Same pool elsewhere",
            "vless-reality-vision",
            "vless",
            "los-angeles-ucla",
            "www.ucla.edu",
        ),
    )
    assert reused_elsewhere.status_code == 201, reused_elsewhere.text

    mismatched_sni = client.post(
        "/api/v1/nodes",
        json=camouflage_node(
            server["id"],
            "Mismatched SNI",
            "anytls-shadowtls",
            "anytls",
            "tokyo-keio",
            "www.apple.com",
        ),
    )
    assert mismatched_sni.status_code == 409
    assert mismatched_sni.json()["detail"] == (
        "Camouflage SNI must match the selected pool server name"
    )


def test_server_kind_protocol_limits_and_mieru_port_mapping(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    leased = create_server(client, "leased", "leased-line")
    residential = create_server(client, "residential", "residential")

    mieru = client.post(
        "/api/v1/nodes",
        json={
            "name": "Leased Mieru",
            "server_id": leased["id"],
            "protocol": "mieru",
            "protocol_profile": "mieru",
            "domestic_entry_ip": "203.0.113.8",
            "domestic_entry_port": 32000,
            "mieru_port_mapping_mode": "one-to-one",
        },
    )
    assert mieru.status_code == 201, mieru.text
    assert mieru.json()["node"]["ix_port"] == 32000
    assert mieru.json()["node"]["config"]["server"] == "203.0.113.8"

    managed_routed = client.post(
        "/api/v1/nodes",
        json={
            "name": "Invalid routed Mieru",
            "server_id": leased["id"],
            "protocol": "mieru",
            "protocol_profile": "mieru",
            "node_type": "routed",
            "domestic_entry_ip": "203.0.113.8",
            "domestic_entry_port": 32000,
            "mieru_port_mapping_mode": "one-to-one",
        },
    )
    assert managed_routed.status_code == 422
    assert "physical nodes" in managed_routed.text

    manual_missing_ix = client.post(
        "/api/v1/nodes",
        json={
            "name": "Missing IX",
            "server_id": leased["id"],
            "protocol": "mieru",
            "protocol_profile": "mieru",
            "domestic_entry_ip": "203.0.113.9",
            "domestic_entry_port": 32001,
            "mieru_port_mapping_mode": "manual",
        },
    )
    assert manual_missing_ix.status_code == 422

    mismatched_one_to_one = client.post(
        "/api/v1/nodes",
        json={
            "name": "Mismatched automatic IX",
            "server_id": leased["id"],
            "protocol": "mieru",
            "protocol_profile": "mieru",
            "domestic_entry_ip": "203.0.113.10",
            "domestic_entry_port": 32002,
            "mieru_port_mapping_mode": "one-to-one",
            "ix_port": 42002,
        },
    )
    assert mismatched_one_to_one.status_code == 409
    assert mismatched_one_to_one.json()["detail"] == (
        "One-to-one Mieru mapping must use the domestic entry port as IX port"
    )

    disallowed_vless = client.post(
        "/api/v1/nodes",
        json={
            "name": "Bad leased VLESS",
            "server_id": leased["id"],
            "protocol": "vless",
        },
    )
    assert disallowed_vless.status_code == 409
    assert disallowed_vless.json()["detail"] == "Leased-line servers only support Mieru nodes"

    socks = client.post(
        "/api/v1/nodes",
        json={
            "name": "Residential SOCKS",
            "server_id": residential["id"],
            "protocol": "socks5",
            "protocol_profile": "socks5",
        },
    )
    assert socks.status_code == 201, socks.text
    assert socks.json()["node"]["protocol"] == "socks"

    disallowed_mieru = client.post(
        "/api/v1/nodes",
        json={
            "name": "Bad residential Mieru",
            "server_id": residential["id"],
            "protocol": "mieru",
        },
    )
    assert disallowed_mieru.status_code == 409
    assert disallowed_mieru.json()["detail"] == "Residential servers only support SOCKS5 nodes"


def test_creation_catalog_hides_old_protocols_but_catalog_import_keeps_them(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    server = create_server(client, "external-compatible")

    profiles = client.get("/api/v1/nodes/creation-metadata").json()["profiles"]
    assert "trojan" not in {entry["protocol"] for entry in profiles}
    hidden = client.post(
        "/api/v1/node-presets/trojan-tls/nodes",
        json={"server_id": server["id"]},
    )
    assert hidden.status_code == 404

    imported = client.post(
        "/api/v1/catalog/import",
        json={
            "catalog": {
                "nodes": [
                    {
                        "name": "Imported Trojan",
                        "server_name": "source-name",
                        "protocol": "trojan",
                        "config": {"type": "trojan", "server": "edge.example.com", "port": 443},
                    }
                ]
            },
            "server_map": {"source-name": server["id"]},
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["summary"]["created_nodes"] == 1
    nodes = client.get("/api/v1/nodes").json()["nodes"]
    assert nodes[0]["name"] == "Imported Trojan"
    assert nodes[0]["protocol"] == "trojan"
    assert nodes[0]["protocol_profile"] is None
