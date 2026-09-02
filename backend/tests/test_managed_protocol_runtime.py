from pathlib import Path

from open_node.domain.inventory import (
    AgentCapabilities,
    AgentRegistrationRequest,
    ServerCreate,
    ServerKind,
)
from open_node.domain.node_management import NodeUpdate
from open_node.domain.subscriptions import ManagedNodeCreate
from open_node.services.inventory import InventoryStore


def store(tmp_path: Path) -> InventoryStore:
    value = InventoryStore("sqlite:///" + (tmp_path / "managed-runtime.db").as_posix())
    value.create_schema()
    return value


def camouflage(server_id, name, profile, protocol, pool, sni):
    return ManagedNodeCreate(
        name=name,
        server_id=server_id,
        protocol=protocol,
        protocol_profile=profile,
        camouflage_pool_id=pool,
        camouflage_sni=sni,
    )


def test_managed_profiles_compile_private_official_runtime_declaration(tmp_path: Path) -> None:
    inventory = store(tmp_path)
    server = inventory.create_server(
        ServerCreate(name="edge", ip_address="203.0.113.10")
    )
    nodes = [
        inventory.create_managed_node(
            camouflage(
                server.id,
                "Vision",
                "vless-reality-vision",
                "vless",
                "los-angeles-ucla",
                "www.ucla.edu",
            )
        ),
        inventory.create_managed_node(
            camouflage(
                server.id,
                "XHTTP",
                "vless-xhttp-reality-xmux",
                "vless",
                "los-angeles-ucsb",
                "www.ucsb.edu",
            )
        ),
        inventory.create_managed_node(
            camouflage(
                server.id,
                "AnyTLS",
                "anytls-shadowtls",
                "anytls",
                "los-angeles-apple",
                "www.apple.com",
            )
        ),
    ]

    assert len({node.runtime_port for node in nodes}) == 3
    assert all(49_152 <= node.runtime_port <= 65_535 for node in nodes)
    assert all(node.runtime_port not in {58_090, 62_031} for node in nodes)
    assert all(node.config["port"] == 443 for node in nodes)
    assert nodes[0].config["reality-opts"]["public-key"]
    assert nodes[0].config["flow"] == "xtls-rprx-vision"
    assert nodes[1].config["xhttp-opts"]["reuse-settings"]["max-concurrency"] == "16-32"
    assert nodes[2].config["shadow-tls-opts"] == {"version": 3}
    assert "reality_private_key" not in str(nodes[0].model_dump())

    command = inventory.managed_protocol_command(server.id)
    assert command.method == "PUT"
    assert command.path == "/api/child/managed-protocols"
    assert [item["profile"] for item in command.body["listeners"]] == [
        "vless_reality_vision",
        "vless_xhttp_reality_xmux",
        "anytls_shadowtls",
    ]
    assert all(item["listen"] == "127.0.0.1" for item in command.body["listeners"])
    assert command.body["listeners"][0]["server_config"]["reality_private_key"]
    assert all(item["users"] == [] for item in command.body["listeners"])

    ingress = inventory.reconcile_managed_shared_ingress(server.id)
    assert ingress is not None and ingress.method == "PUT"
    assert [route["upstream_port"] for route in ingress.body["configuration"]["routes"]] == [
        node.runtime_port for node in nodes
    ]
    assert [route["sni"] for route in ingress.body["configuration"]["routes"]] == [
        "www.ucla.edu",
        "www.ucsb.edu",
        "www.apple.com",
    ]


def test_mieru_and_socks_use_explicit_public_runtime_ports(tmp_path: Path) -> None:
    inventory = store(tmp_path)
    leased = inventory.create_server(
        ServerCreate(
            name="leased",
            server_kind=ServerKind.LEASED_LINE,
            ip_address="203.0.113.20",
        )
    )
    residential = inventory.create_server(
        ServerCreate(
            name="home",
            server_kind=ServerKind.RESIDENTIAL,
            ip_address="203.0.113.30",
        )
    )
    mieru = inventory.create_managed_node(
        ManagedNodeCreate(
            name="Mieru",
            server_id=leased.id,
            protocol="mieru",
            protocol_profile="mieru",
            domestic_entry_ip="198.51.100.10",
            domestic_entry_port=32_000,
            mieru_port_mapping_mode="one-to-one",
        )
    )
    socks = inventory.create_managed_node(
        ManagedNodeCreate(
            name="SOCKS5",
            server_id=residential.id,
            protocol="socks",
            protocol_profile="socks5",
        )
    )

    assert mieru.runtime_port == 32_000
    assert mieru.config["server"] == "198.51.100.10"
    assert mieru.config["port"] == 32_000
    assert socks.runtime_port is not None and 49_152 <= socks.runtime_port <= 65_535
    assert socks.config["server"] == "203.0.113.30"
    assert socks.config["port"] == socks.runtime_port
    assert inventory.managed_protocol_command(leased.id).body["listeners"][0]["listen"] == (
        "0.0.0.0"
    )
    assert inventory.managed_protocol_command(residential.id).body["listeners"][0][
        "profile"
    ] == "socks5"


def test_capable_agent_registration_reasserts_panel_owned_runtime_in_order(tmp_path: Path) -> None:
    inventory = store(tmp_path)
    server = inventory.create_server(
        ServerCreate(name="late-agent", ip_address="203.0.113.40")
    )
    inventory.create_managed_node(
        camouflage(
            server.id,
            "Vision",
            "vless-reality-vision",
            "vless",
            "los-angeles-ucla",
            "www.ucla.edu",
        )
    )
    assert inventory.reconcile_managed_shared_ingress(server.id) is not None

    inventory.register_agent(
        AgentRegistrationRequest(
            token=server.agent_token,
            hostname="late-agent",
            agent_version="open-node/0.3.0a3",
            capabilities=AgentCapabilities(managed_protocols=True),
        )
    )

    managed = [
        command
        for command in inventory.list_commands(server.id)
        if command.path
        in {"/api/child/managed-protocols", "/api/child/nginx/shared-ingress"}
    ]
    assert [command.path for command in managed] == [
        "/api/child/managed-protocols",
        "/api/child/nginx/shared-ingress",
    ]
    assert managed[1].depends_on_command_id == managed[0].id
    assert managed[0].body["listeners"][0]["profile"] == "vless_reality_vision"
    assert managed[1].body["configuration"]["routes"][0]["sni"] == "www.ucla.edu"


def test_generic_node_edit_cannot_desynchronise_managed_client_transport(tmp_path: Path) -> None:
    inventory = store(tmp_path)
    server = inventory.create_server(
        ServerCreate(name="canonical-edit", ip_address="203.0.113.50")
    )
    node = inventory.create_managed_node(
        camouflage(
            server.id,
            "XHTTP",
            "vless-xhttp-reality-xmux",
            "vless",
            "los-angeles-ucla",
            "www.ucla.edu",
        )
    )
    public_key = node.config["reality-opts"]["public-key"]
    path = node.config["xhttp-opts"]["path"]
    view = inventory._node_management().read(node.id)

    updated = inventory._node_management().update(
        node.id,
        NodeUpdate(
            name=node.name,
            tag=node.tag,
            tags=node.tags,
            enabled=True,
            parent_id=None,
            target_node_id=None,
            client_template=node.client_template,
            config={},
            expected_revision=view.revision,
            acknowledge_runtime_restart=True,
        ),
    )

    assert updated.node.config["network"] == "xhttp"
    assert updated.node.config["port"] == 443
    assert updated.node.config["reality-opts"]["public-key"] == public_key
    assert updated.node.config["xhttp-opts"]["path"] == path
    assert updated.node.config["xhttp-opts"]["reuse-settings"]["max-concurrency"] == (
        "16-32"
    )
