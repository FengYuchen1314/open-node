import copy

import pytest
from open_node_agent.operations import edit_client
from open_node_agent.runtime import RuntimeFailure


@pytest.mark.parametrize(
    "protocol,container,credential",
    [
        ("vless", "clients", {"id": "uuid"}),
        ("vmess", "clients", {"id": "uuid"}),
        ("trojan", "clients", {"password": "password"}),
        ("shadowsocks", "clients", {"password": "password"}),
        ("hysteria", "clients", {"auth": "password"}),
        ("anytls", "users", {"password": "password"}),
        ("mieru", "users", {"username": "alice", "password": "password"}),
    ],
)
def test_user_container_replacement_removal_and_preservation(protocol, container, credential):
    other = {"email": "other", **credential}
    inbound = {
        "protocol": protocol,
        "settings": {container: [other], "preserved": {"option": True}},
        "sniffing": {"enabled": True},
    }
    client = {"email": "alice", **credential}
    edit_client(inbound, client)
    client["level"] = 2
    assert "level" not in inbound["settings"][container][-1]
    edit_client(inbound, client)
    assert inbound["settings"][container] == [other, client]
    edit_client(inbound, {"email": "alice"}, remove=True)
    assert inbound["settings"][container] == [other]
    edit_client(inbound, {"email": "other"}, remove=True)
    assert inbound["settings"][container] == []
    assert inbound["settings"]["preserved"] == {"option": True}
    assert inbound["sniffing"] == {"enabled": True}
    assert ("users" if container == "clients" else "clients") not in inbound["settings"]


@pytest.mark.parametrize(
    "options",
    [
        {"version": 4, "obfsMode": "none", "obfsHost": ""},
        {"version": 5, "obfsMode": "http", "obfsHost": "example.org"},
        {"version": 6, "v6Mode": "default"},
        {"version": 6, "v6Mode": "unshaped"},
    ],
)
def test_snell_first_user_order_and_last_user_options_survive(options):
    alice = {"email": "alice", "psk": "alice-password", **options}
    bob = {"email": "bob", "psk": "bob-password", **options}
    inbound = {"protocol": "snell", "settings": {"users": [alice, bob]}}
    edit_client(inbound, {"email": "alice", "psk": "new-password"})
    assert inbound["settings"]["users"] == [{**alice, "psk": "new-password"}, bob]
    edit_client(inbound, {"email": "alice"}, remove=True)
    assert inbound["settings"]["users"] == [bob]
    edit_client(inbound, {"email": "bob"}, remove=True)
    assert inbound["settings"] == {"users": [], **options}
    edit_client(inbound, {"email": "returned", "psk": "returned-password"})
    assert inbound["settings"]["users"] == [
        {"email": "returned", "psk": "returned-password", **options}
    ]


@pytest.mark.parametrize("change", [{"version": 6}, {"obfsMode": "http"}])
def test_snell_rejects_per_user_transport_changes_without_mutation(change):
    inbound = {
        "protocol": "snell",
        "settings": {
            "users": [
                {"email": "alice", "psk": "alice-password"},
                {"email": "bob", "psk": "bob-password"},
            ]
        },
    }
    before = copy.deepcopy(inbound)
    with pytest.raises(RuntimeFailure, match="whole inbound"):
        edit_client(inbound, {"email": "alice", "psk": "new-password", **change})
    assert inbound == before


def test_snell_mixed_existing_options_are_rejected_without_mutation():
    inbound = {
        "protocol": "snell",
        "settings": {
            "users": [
                {"email": "alice", "psk": "alice-password", "version": 6},
                {"email": "bob", "psk": "bob-password", "version": 4},
            ]
        },
    }
    before = copy.deepcopy(inbound)
    with pytest.raises(RuntimeFailure, match="share"):
        edit_client(inbound, {"email": "alice"}, remove=True)
    assert inbound == before


@pytest.mark.parametrize(
    "options",
    [
        {"version": 6, "v6Mode": "unsafe-raw"},
        {"version": [4]},
        {"version": True},
        {"version": 7},
        {"version": 6, "v6Mode": ["default"]},
        {"obfsMode": ["none"]},
        {"obfsMode": "invalid"},
        {"obfsHost": 123},
    ],
)
def test_snell_invalid_or_unauthenticated_options_are_rejected(options):
    inbound = {
        "protocol": "snell",
        "settings": {
            "users": [
                {"email": "alice", "psk": "alice-password", **options},
            ]
        },
    }
    before = copy.deepcopy(inbound)
    with pytest.raises(RuntimeFailure):
        edit_client(inbound, {"email": "bob", "psk": "bob-password"})
    assert inbound == before


@pytest.mark.parametrize(
    "settings",
    [None, [], {"users": {}}, {"users": [None]}, {"users": [], "clients": [{"email": "ignored"}]}],
)
def test_invalid_user_container_does_not_mutate_config(settings):
    inbound = {"protocol": "anytls", "settings": settings}
    before = copy.deepcopy(inbound)
    with pytest.raises(RuntimeFailure):
        edit_client(inbound, {"email": "alice", "password": "password"})
    assert inbound == before


@pytest.mark.parametrize("client", [None, [], {}, {"email": ""}, {"email": 1}])
def test_user_email_is_required(client):
    with pytest.raises(RuntimeFailure, match="email"):
        edit_client({"protocol": "anytls"}, client)
