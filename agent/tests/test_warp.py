import asyncio
import base64
import copy
import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from open_node_agent.client import Agent
from open_node_agent.runtime import RuntimeFailure
from open_node_agent.warp import TAGS, Warp, WarpAPI, build_outbounds, peer_config


def registration():
    return {
        "id": "device-fixture",
        "token": "provider-secret-token",
        "account": {"license": "free-issued-license", "account_type": "free"},
        "config": {
            "client_id": "AQID",
            "interface": {"addresses": {"v4": "172.16.0.2", "v6": "fd00::2"}},
            "peers": [
                {
                    "public_key": base64.b64encode(b"p" * 32).decode(),
                    "endpoint": {"host": "engage.cloudflareclient.com:2408"},
                }
            ],
        },
    }


@pytest.fixture
async def agent(config):
    value = Agent(config)
    value.runtime.validate = AsyncMock(return_value=(True, "valid"))
    value.runtime.running = AsyncMock(return_value=False)
    value.runtime.restart = AsyncMock()
    value.operations.warp.api = AsyncMock()
    value.operations.warp.api.register.return_value = registration()
    value.operations.warp.api.refresh.return_value = registration()
    try:
        yield value
    finally:
        await value.close()


async def invoke(agent, action, body=None, request_id=None):
    return await agent.execute(
        {
            "request_id": request_id or action,
            "method": "GET" if action == "status" else "POST",
            "path": "/api/child/warp/" + action,
            "body": body,
        }
    )


async def installed(agent):
    result = await invoke(agent, "install", {"accept_terms": True})
    assert result["status"] == 200, result
    return agent.operations.warp


@pytest.mark.parametrize("consent", [None, False, "true", 1])
async def test_registration_requires_explicit_consent(agent, consent):
    result = await invoke(agent, "install", {"accept_terms": consent})
    assert result["status"] == 400 and "terms" in result["error"]
    agent.operations.warp.api.register.assert_not_awaited()


async def test_free_registration_secrets_tags_and_deduplication(agent):
    original = agent.runtime.read()
    warp = await installed(agent)
    state = warp.load()
    result = warp.status()
    assert result["installed"] and not result["license_active"]
    assert agent.registration()["warp_installed"]
    assert result["account_type"] == "free"
    assert warp.path.stat().st_mode & 0o777 == 0o600
    config = agent.runtime.read()
    assert config["outbounds"][0] == original["outbounds"][0]
    assert config["inbounds"] == original["inbounds"]
    first, second = config["outbounds"][1:]
    assert first["tag"] == "warp-v4" and second["tag"] == "warp-v6"
    assert first["settings"]["noKernelTun"] is True
    assert first["settings"]["reserved"] == [1, 2, 3]
    assert first["settings"]["domainStrategy"] == "ForceIPv4v6"
    assert second["settings"]["domainStrategy"] == "ForceIPv6v4"
    first["settings"]["address"].append("10.0.0.1/32")
    assert "10.0.0.1/32" not in second["settings"]["address"]
    for secret in (
        state.private_key.get_secret_value(),
        state.access_token.get_secret_value(),
        "free-issued-license",
    ):
        assert secret not in json.dumps(result)
        assert secret not in repr(state)
        assert secret not in json.dumps(agent.journal.pending_results())
    await invoke(agent, "install", {"accept_terms": True})
    assert warp.api.register.await_count == 1
    retry = await invoke(agent, "install", {}, "reapply")
    assert retry["status"] == 200
    assert warp.api.register.await_count == 1
    assert warp.api.refresh.await_count == 1
    assert len(agent.runtime.read()["outbounds"]) == 3


@pytest.mark.parametrize("bad", [False, "collision"])
async def test_preflight_does_not_register_if_runtime_unusable(agent, bad):
    if bad == "collision":
        config = agent.runtime.read()
        config["outbounds"].append({"tag": "warp-v4", "protocol": "freedom"})
        agent.runtime.config.xray_config.write_text(json.dumps(config))
    else:
        agent.runtime.validate.return_value = (False, "private input")
    result = await invoke(agent, "install", {"accept_terms": True})
    assert result["status"] == 400 and "private input" not in result["error"]
    agent.operations.warp.api.register.assert_not_awaited()


async def test_incomplete_provider_config_retains_device_for_refresh_and_removal(agent):
    warp = agent.operations.warp
    response = registration()
    del response["config"]
    warp.api.register.return_value = response
    result = await invoke(agent, "install", {"accept_terms": True})
    assert result["status"] == 400
    assert warp.status()["phase"] == "needs_apply"
    retry = await invoke(agent, "install", {}, "refresh")
    assert retry["status"] == 200
    assert warp.api.register.await_count == 1
    assert warp.status()["installed"]


@pytest.mark.parametrize("failure", [RuntimeFailure("start failed"), asyncio.CancelledError()])
async def test_apply_failure_restores_runtime_but_preserves_registration(agent, failure):
    warp = agent.operations.warp
    original = agent.runtime.config.xray_config.read_bytes()
    agent.runtime.running.return_value = True
    agent.runtime.restart.side_effect = [failure, None]
    with pytest.raises(type(failure)):
        await warp.install({"accept_terms": True})
    assert agent.runtime.config.xray_config.read_bytes() == original
    assert warp.load() is not None
    assert not warp.status()["installed"]
    assert not warp.transaction.record.exists()
    assert agent.runtime.restart.await_count == 2


async def test_apply_validation_failure_never_exposes_private_config(agent):
    warp = await installed(agent)
    original = agent.runtime.config.xray_config.read_bytes()
    agent.runtime.validate.return_value = (False, warp.load().private_key.get_secret_value())
    result = await invoke(agent, "install", {}, "invalid")
    assert result["status"] == 400
    assert "rejected" in result["error"]
    assert agent.runtime.config.xray_config.read_bytes() == original
    assert warp.load().private_key.get_secret_value() not in json.dumps(result)


async def test_provider_upgrade_is_optional_and_new_config_is_applied(agent):
    warp = await installed(agent)
    response = registration()
    response["account"]["account_type"] = "unlimited"
    response["config"]["interface"]["addresses"]["v4"] = "172.16.0.3"
    warp.api.refresh.return_value = response
    result = await invoke(agent, "license", {"license": "optional-plus-credential"})
    assert result["status"] == 200 and result["body"]["license_active"]
    assert "172.16.0.3/32" in agent.runtime.read()["outbounds"][1]["settings"]["address"]
    assert "optional-plus-credential" not in warp.path.read_text()
    assert "optional-plus-credential" not in json.dumps(result)


@pytest.mark.parametrize(
    "reference",
    [
        {"routing": {"rules": [{"outboundTag": "warp-v4"}]}},
        {"routing": {"balancers": [{"selector": ["direct"], "fallbackTag": "warp-v6"}]}},
        {"observatory": {"subjectSelector": ["warp-"]}},
        {"routing": {"balancers": [{"selector": ["warp-"]}]}},
        {"routing": {"balancers": [{"selector": [""]}]}},
        {"streamSettings": {"sockopt": {"dialerProxy": "warp-v6"}}},
        {"proxySettings": {"tag": "warp-v4"}},
    ],
)
async def test_removal_blocks_routing_and_proxy_references(agent, reference):
    warp = await installed(agent)
    candidate = agent.runtime.read()
    candidate.update(reference)
    agent.runtime.config.xray_config.write_text(json.dumps(candidate))
    result = await invoke(agent, "remove", {"confirm": True})
    assert result["status"] == 400 and "references" in result["error"]
    warp.api.delete.assert_not_awaited()
    assert warp.status()["installed"]


async def test_removal_blocks_implicit_default_and_edited_tags(agent):
    warp = await installed(agent)
    candidate = agent.runtime.read()
    candidate["outbounds"].reverse()
    agent.runtime.config.xray_config.write_text(json.dumps(candidate))
    with pytest.raises(RuntimeFailure, match="default"):
        await warp.remove({"confirm": True})
    candidate["outbounds"].reverse()
    candidate["outbounds"][1]["settings"]["mtu"] = 1300
    agent.runtime.config.xray_config.write_text(json.dumps(candidate))
    with pytest.raises(RuntimeFailure, match="conflict"):
        await warp.remove({"confirm": True})
    assert not warp.status()["installed"]
    warp.api.delete.assert_not_awaited()


async def test_provider_delete_failure_disables_locally_and_retains_retry_credentials(agent):
    warp = await installed(agent)
    warp.api.delete.side_effect = RuntimeFailure("provider unavailable")
    result = await invoke(agent, "remove", {"confirm": True})
    assert result["status"] == 400
    assert warp.status()["phase"] == "removal_pending"
    assert not warp.status()["installed"]
    assert len(agent.runtime.read()["outbounds"]) == 1
    assert warp.load().access_token.get_secret_value()
    with pytest.raises(RuntimeFailure, match="removal is pending"):
        await warp.install({"accept_terms": True})
    warp.api.delete.side_effect = None
    result = await invoke(agent, "remove", {"confirm": True}, "retry-delete")
    assert result["status"] == 200 and result["body"]["phase"] == "absent"
    assert not warp.path.exists()
    assert not agent.registration()["warp_installed"]


async def test_removal_confirmation_and_absent_idempotency(agent):
    for value in (None, False, "true", 1):
        with pytest.raises(RuntimeFailure, match="confirmation"):
            await agent.operations.warp.remove({"confirm": value})
    assert (await agent.operations.warp.remove({"confirm": True}))["phase"] == "absent"


async def test_interrupted_local_transaction_restores_config_and_account(agent):
    warp = await installed(agent)
    old = agent.runtime.config.xray_config.read_bytes()
    state = warp.path.read_bytes()
    warp.transaction.begin({agent.runtime.config.xray_config: b"{}", warp.path: b"{}"})
    recovered = Warp(agent.runtime)
    assert recovered.status()["installed"]
    assert agent.runtime.config.xray_config.read_bytes() == old
    assert warp.path.read_bytes() == state
    assert not warp.transaction.record.exists()


@pytest.mark.parametrize("kind", ["permissions", "corrupt", "symlink", "hardlink", "fifo"])
async def test_invalid_state_is_not_silently_replaced_or_registered(agent, kind, tmp_path):
    warp = await installed(agent)
    if kind == "permissions":
        warp.path.chmod(0o644)
    elif kind == "corrupt":
        warp.path.write_text('{"access_token": "must-not-leak"}')
    else:
        saved = tmp_path / "saved"
        warp.path.rename(saved)
        if kind == "symlink":
            warp.path.symlink_to(saved)
        elif kind == "hardlink":
            os.link(saved, warp.path)
        else:
            os.mkfifo(warp.path)
    with pytest.raises(RuntimeFailure) as error:
        await warp.install({"accept_terms": True})
    assert "must-not-leak" not in str(error.value)
    assert warp.api.register.await_count == 1
    assert not agent.registration()["warp_installed"]
    assert warp.snapshot()["phase"] == "error"


@pytest.mark.parametrize(
    "endpoint",
    ["https://host:443/path", "a:b@host:2408", "host:0", "host:99999", "[fe80::1%eth0]:2408"],
)
def test_invalid_provider_endpoints(endpoint):
    data = registration()
    data["config"]["peers"][0]["endpoint"]["host"] = endpoint
    with pytest.raises(RuntimeFailure):
        peer_config(data)


@pytest.mark.parametrize("code", [301, 401, 403, 429, 500])
async def test_api_never_follows_redirects_or_echoes_provider_secrets(code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            code,
            json={"error": "credential-should-not-appear"},
            headers={"location": "https://other.invalid"},
        )

    api = WarpAPI(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeFailure) as error:
        await api.request("GET", "/reg/id", token="credential-should-not-appear")
    assert "credential-should-not-appear" not in str(error.value)
    assert len(requests) == 1
    assert str(requests[0].url).startswith("https://api.cloudflareclient.com/")


@pytest.mark.parametrize("code", [200, 204, 404])
async def test_provider_delete_accepts_already_missing(code):
    api = WarpAPI(transport=httpx.MockTransport(lambda _: httpx.Response(code)))
    assert await api.request("DELETE", "/reg/id", deleting=True) == {}


@pytest.mark.parametrize("data", [b"not-json", b"[]", b"x" * 65537])
async def test_api_rejects_malformed_and_oversized_responses(data):
    api = WarpAPI(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data)))
    with pytest.raises(RuntimeFailure):
        await api.request("POST", "/reg", body={"key": "public"})


async def test_pending_provider_deletion_does_not_count_as_deleted():
    api = WarpAPI(transport=httpx.MockTransport(lambda _: httpx.Response(202, json={})))
    with pytest.raises(RuntimeFailure, match="202"):
        await api.request("DELETE", "/reg/id", deleting=True)


@pytest.mark.parametrize("code", [200, 204])
async def test_license_update_accepts_empty_success_body(code):
    api = WarpAPI(transport=httpx.MockTransport(lambda _: httpx.Response(code)))
    assert await api.request("PUT", "/reg/id/account", expect_json=False) == {}


async def test_runtime_stopped_intent_is_preserved(agent):
    warp = await installed(agent)
    agent.runtime.restart.assert_not_awaited()
    assert not agent.journal.desired_running(False)
    await warp.remove({"confirm": True})
    agent.runtime.restart.assert_not_awaited()


def test_outbound_builder_does_not_share_mutable_values():
    from open_node_agent.warp import WarpState

    state = WarpState(
        device_id="id",
        access_token="token",
        private_key=base64.b64encode(b"x" * 32).decode(),
        public_key=base64.b64encode(b"y" * 32).decode(),
        registered_at="now",
        config=peer_config(registration()),
    )
    outbounds = build_outbounds(state)
    before = copy.deepcopy(outbounds[1])
    outbounds[0]["settings"]["peers"][0]["endpoint"] = "localhost:1234"
    assert outbounds[1] == before
    assert [item["tag"] for item in outbounds] == list(TAGS)
