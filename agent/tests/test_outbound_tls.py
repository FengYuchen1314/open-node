import copy
import socket
from unittest.mock import AsyncMock, Mock

import open_node_agent.operations as operations_module
import open_node_agent.outbound_tls as outbound_tls
import pytest
from open_node_agent.operations import Operations, edit_entries
from open_node_agent.runtime import RuntimeFailure


def tls_outbound(pin: str | None = None, **tls_updates):
    tls_settings = {"serverName": "proxy.example", **tls_updates}
    if pin is not None:
        tls_settings["pinnedPeerCertSha256"] = pin
    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {"vnext": [{"address": "proxy.example", "port": 443}]},
        "streamSettings": {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": tls_settings,
        },
    }


def test_manual_outbound_requires_and_normalizes_fork_tls_pin():
    config = {"outbounds": []}
    with pytest.raises(RuntimeFailure, match="requires pinnedPeerCertSha256"):
        edit_entries(config, "outbounds", {"action": "add", "outbound": tls_outbound()})

    colon_pin = ":".join(["AB"] * 32)
    outbound = tls_outbound(colon_pin)
    edit_entries(config, "outbounds", {"action": "add", "outbound": outbound})
    assert config["outbounds"][0]["streamSettings"]["tlsSettings"][
        "pinnedPeerCertSha256"
    ] == "ab" * 32


def test_manual_outbound_rejects_allow_insecure_even_when_false():
    outbound = tls_outbound("ab" * 32, allowInsecure=False)
    with pytest.raises(RuntimeFailure, match="must not use allowInsecure"):
        edit_entries(
            {"outbounds": []},
            "outbounds",
            {"action": "add", "outbound": outbound},
        )


def test_changed_managed_tls_requires_pin_but_existing_outbounds_are_untouched():
    existing_managed = tls_outbound()
    existing_managed["tag"] = "managed-egress:source:existing"
    existing_operator = tls_outbound()
    existing_operator["tag"] = "operator-tls"
    expected = {"outbounds": [existing_managed, existing_operator]}

    unchanged = copy.deepcopy(expected)
    unchanged["outbounds"][1]["settings"]["vnext"][0]["port"] = 8443
    outbound_tls.validate_changed_managed_outbound_tls(expected, unchanged)

    added = copy.deepcopy(unchanged)
    managed = tls_outbound()
    managed["tag"] = "managed-egress:source:new"
    added["outbounds"].append(managed)
    with pytest.raises(RuntimeFailure, match="requires pinnedPeerCertSha256"):
        outbound_tls.validate_changed_managed_outbound_tls(expected, added)

    managed["streamSettings"]["tlsSettings"]["pinnedPeerCertSha256"] = ":".join(
        ["AB"] * 32
    )
    outbound_tls.validate_changed_managed_outbound_tls(expected, added)
    assert managed["streamSettings"]["tlsSettings"]["pinnedPeerCertSha256"] == "ab" * 32


async def test_egress_apply_rejects_unpinned_new_managed_tls_before_write():
    managed = tls_outbound()
    managed["tag"] = "managed-egress:source:new"
    runtime = Mock()
    runtime.lock = AsyncMock()
    runtime.read.return_value = {"outbounds": []}
    runtime.write = AsyncMock()
    operations = object.__new__(Operations)
    operations.runtime = runtime
    operations.managed_protocols = Mock()
    operations.managed_protocols.assert_xray_compatible = Mock()
    operations.node_cleanup = Mock()
    operations.node_cleanup.recover = AsyncMock()

    with pytest.raises(RuntimeFailure, match="requires pinnedPeerCertSha256"):
        await operations.handle(
            {
                "method": "POST",
                "path": "/api/child/egress/apply",
                "body": {
                    "expected_config": {"outbounds": []},
                    "config": {"outbounds": [managed]},
                },
            }
        )

    runtime.write.assert_not_awaited()
    operations.managed_protocols.assert_xray_compatible.assert_not_called()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "224.0.0.1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "64:ff9b::a00:1",
        "2002:7f00:1::",
        "2001:0000:0808:0808:0000:0000:f5ff:fffe",
    ],
)
async def test_probe_rejects_non_public_peer_before_tls(monkeypatch, address):
    tls = AsyncMock()
    monkeypatch.setattr(outbound_tls, "_leaf_sha256", tls)
    with pytest.raises(RuntimeFailure, match="publicly routable"):
        await outbound_tls.probe_tls_certificate(
            {
                "protocol": "vless",
                "address": address,
                "port": 443,
                "server_name": address,
                "alpn": ["h2"],
                "timeout_ms": 1_000,
            }
        )
    tls.assert_not_awaited()


async def test_leaf_probe_connects_only_to_the_revalidated_numeric_address(monkeypatch):
    class TLSObject:
        @staticmethod
        def getpeercert(*, binary_form):
            assert binary_form is True
            return b"leaf-certificate"

    class Writer:
        @staticmethod
        def get_extra_info(name):
            assert name == "ssl_object"
            return TLSObject()

        @staticmethod
        def close():
            return None

        @staticmethod
        async def wait_closed():
            return None

    connect = AsyncMock(return_value=(object(), Writer()))
    monkeypatch.setattr(outbound_tls.asyncio, "open_connection", connect)

    fingerprint = await outbound_tls._leaf_sha256(
        "93.184.216.34", 443, "example.com", ["h2"], 2.0
    )

    assert len(fingerprint) == 64
    connect.assert_awaited_once()
    assert connect.await_args.args == ("93.184.216.34", 443)
    assert connect.await_args.kwargs["family"] == socket.AF_INET
    assert connect.await_args.kwargs["flags"] == socket.AI_NUMERICHOST


async def test_leaf_probe_rejects_non_public_numeric_address_before_connect(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr(outbound_tls.asyncio, "open_connection", connect)

    with pytest.raises(RuntimeFailure, match="publicly routable"):
        await outbound_tls._leaf_sha256("64:ff9b::a00:1", 443, "example.com", [], 2.0)

    connect.assert_not_awaited()


async def test_probe_operation_returns_only_normalized_leaf_hash(monkeypatch):
    resolve = AsyncMock(return_value=["93.184.216.34"])
    leaf = AsyncMock(return_value="cd" * 32)
    monkeypatch.setattr(outbound_tls, "_resolve_public", resolve)
    monkeypatch.setattr(outbound_tls, "_leaf_sha256", leaf)
    # Operations imports the function directly, so bind the tested implementation
    # here while keeping all actual validation and response shaping in place.
    monkeypatch.setattr(
        operations_module,
        "probe_tls_certificate",
        outbound_tls.probe_tls_certificate,
    )
    operations = object.__new__(Operations)

    result = await operations.handle(
        {
            "method": "POST",
            "path": "/api/child/outbound-tls-pin/probe",
            "body": {
                "protocol": "trojan",
                "address": "EXAMPLE.COM",
                "port": 443,
                "server_name": "tls.example.com",
                "alpn": ["h2"],
                "timeout_ms": 2_000,
            },
        }
    )

    assert result == {"success": True, "pinned_peer_cert_sha256": "cd" * 32}
    resolve.assert_awaited_once_with("example.com", 443, 2.0)
    call = leaf.await_args.args
    assert call[:4] == ("93.184.216.34", 443, "tls.example.com", ["h2"])
    assert 0 < call[4] <= 2.0


async def test_probe_does_not_echo_invalid_input_in_error():
    secret = "token-secret.invalid/path"
    with pytest.raises(RuntimeFailure) as failure:
        await outbound_tls.probe_tls_certificate(
            {"protocol": "vless", "address": secret, "port": 443}
        )
    assert secret not in str(failure.value)
