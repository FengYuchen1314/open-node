from pathlib import Path

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import ManagedNodeModel
from sqlalchemy import inspect, update


def make_client(tmp_path: Path) -> TestClient:
    database = (tmp_path / "shared-ingress.db").as_posix()
    return authenticated_client(create_app(Settings(database_url=f"sqlite:///{database}")))


def create_server_and_nodes(client: TestClient, suffix: str = "") -> tuple[dict, list[dict]]:
    response = client.post("/api/v1/servers", json={"name": "ingress" + suffix})
    assert response.status_code == 201, response.text
    server = response.json()["server"]
    cases = [
        (
            "Vision" + suffix,
            "vless-reality-vision",
            "vless",
            "los-angeles-ucla",
            "www.ucla.edu",
        ),
        (
            "XHTTP" + suffix,
            "vless-xhttp-reality-xmux",
            "vless",
            "los-angeles-ucsb",
            "www.ucsb.edu",
        ),
        (
            "AnyTLS" + suffix,
            "anytls-shadowtls",
            "anytls",
            "los-angeles-apple",
            "www.apple.com",
        ),
    ]
    nodes = []
    for name, profile, protocol, pool, sni in cases:
        created = client.post(
            "/api/v1/nodes",
            json={
                "name": name,
                "server_id": server["id"],
                "protocol": protocol,
                "protocol_profile": profile,
                "inbound_tag": name.lower(),
                "camouflage_pool_id": pool,
                "camouflage_sni": sni,
            },
        )
        assert created.status_code == 201, created.text
        nodes.append(created.json()["node"])
    return server, nodes


def configuration(nodes: list[dict], *, website: bool = True) -> dict:
    occupied = {node["runtime_port"] for node in nodes}
    website_port = next(port for port in range(62_044, 65_536) if port not in occupied)
    result = {
        "listen_port": 443,
        "listen_ipv6": True,
        "routes": [
            {
                "node_id": node["id"],
                "profile": node["protocol_profile"],
                "sni": node["camouflage_sni"],
                "upstream_address": "127.0.0.1",
                "upstream_port": node["runtime_port"],
            }
            for index, node in enumerate(nodes)
        ],
        "website": None,
    }
    if website:
        result["website"] = {
            "sni": "site.example.com",
            "upstream_url": "https://origin.example.net/app",
            "tls_address": "127.0.0.1",
            "tls_port": website_port,
            "certificate_name": "site.example.com",
            "redirect_http": True,
        }
    return result


def test_shared_ingress_api_persists_auditable_revision_and_queues_agent_command(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    server, nodes = create_server_and_nodes(client)
    path = f"/api/v1/servers/{server['id']}/shared-ingress"
    initial = client.get(path)
    assert initial.status_code == 200
    initial_body = initial.json()
    assert initial_body["configuration"] == configuration(nodes, website=False)
    assert initial_body["revision"] == 3

    declaration = configuration(nodes)
    applied = client.put(
        path,
        json={"configuration": declaration, "expected_revision": initial_body["revision"]},
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["state"]["configuration"] == declaration
    assert payload["state"]["revision"] == 4
    assert payload["command"]["method"] == "PUT"
    assert payload["command"]["path"] == "/api/child/nginx/shared-ingress"
    assert payload["command"]["body"] == {
        "revision": 4,
        "configuration": declaration,
    }
    assert payload["command"]["status"] in {"waiting", "pending"}
    persisted = client.get(path).json()
    assert persisted["configuration"] == declaration and persisted["revision"] == 4

    stale = client.put(
        path,
        json={"configuration": declaration, "expected_revision": 3},
    )
    assert stale.status_code == 409 and "revision changed" in stale.text

    removed = client.request(
        "DELETE",
        path,
        json={"expected_revision": 4},
    )
    assert removed.status_code == 200, removed.text
    removed_payload = removed.json()
    assert removed_payload["state"]["configuration"] is None
    assert removed_payload["state"]["revision"] == 5
    assert removed_payload["command"]["method"] == "DELETE"
    assert removed_payload["command"]["body"] == {"revision": 5}
    assert client.get(path).json()["revision"] == 5

    tables = set(inspect(client.app.state.inventory._engine).get_table_names())
    assert "shared_ingress_configurations" in tables


def test_shared_ingress_rejects_unsafe_listener_and_ambiguous_routes(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    server, nodes = create_server_and_nodes(client)
    path = f"/api/v1/servers/{server['id']}/shared-ingress"

    cases = []
    wrong_public_port = configuration(nodes, website=False)
    wrong_public_port["listen_port"] = 444
    cases.append(wrong_public_port)
    public_upstream = configuration(nodes, website=False)
    public_upstream["routes"][0]["upstream_address"] = "0.0.0.0"
    cases.append(public_upstream)
    low_upstream = configuration(nodes, website=False)
    low_upstream["routes"][0]["upstream_port"] = 443
    cases.append(low_upstream)
    duplicate_sni = configuration(nodes, website=False)
    duplicate_sni["routes"][1]["sni"] = duplicate_sni["routes"][0]["sni"]
    cases.append(duplicate_sni)
    duplicate_port = configuration(nodes, website=False)
    duplicate_port["routes"][1]["upstream_port"] = duplicate_port["routes"][0]["upstream_port"]
    cases.append(duplicate_port)

    for candidate in cases:
        response = client.put(path, json={"configuration": candidate})
        assert response.status_code == 422, response.text
    assert client.get(path).json()["revision"] == 3


def test_shared_ingress_binds_each_route_to_server_profile_and_camouflage_sni(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    server, nodes = create_server_and_nodes(client)
    _, other_nodes = create_server_and_nodes(client, "-other")
    path = f"/api/v1/servers/{server['id']}/shared-ingress"

    wrong_server = configuration(nodes, website=False)
    wrong_server["routes"][0]["node_id"] = other_nodes[0]["id"]
    response = client.put(path, json={"configuration": wrong_server})
    assert response.status_code == 400 and "different server" in response.text

    wrong_profile = configuration(nodes, website=False)
    wrong_profile["routes"][0]["profile"] = "anytls-shadowtls"
    response = client.put(path, json={"configuration": wrong_profile})
    assert response.status_code == 400 and "profile does not match" in response.text

    wrong_sni = configuration(nodes, website=False)
    wrong_sni["routes"][0]["sni"] = "other.example.com"
    response = client.put(path, json={"configuration": wrong_sni})
    assert response.status_code == 400 and "camouflage SNI" in response.text

    with client.app.state.inventory._engine.begin() as connection:
        connection.execute(
            update(ManagedNodeModel)
            .where(ManagedNodeModel.id == nodes[0]["id"])
            .values(config={"port": 444})
        )
    response = client.put(path, json={"configuration": configuration(nodes, website=False)})
    assert response.status_code == 400 and "shared public 443" in response.text
    assert client.get(path).json()["configuration"] == configuration(nodes, website=False)
