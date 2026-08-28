import json
import os
import socket
from time import time
from uuid import uuid4

import httpx
import pytest
from open_node_agent import http01
from open_node_agent.config import AgentConfig
from open_node_agent.http01 import HttpChallenges
from open_node_agent.journal import CommandJournal
from open_node_agent.runtime import RuntimeFailure
from pydantic import ValidationError


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def payload(**changes):
    token = uuid4().hex
    return {
        "lease_id": str(uuid4()),
        "expires_at": time() + 120,
        "mode": "standalone",
        "challenges": [
            {"domain": "node.example", "token": token, "key_authorization": token + "." + "A" * 43}
        ],
        **changes,
    }


def cleanup(request):
    return {key: request[key] for key in ("lease_id", "expires_at")}


@pytest.fixture
async def receiver(config):
    config.certificate_http_address = f"127.0.0.1:{free_port()}"
    config.certificate_webroots = ["site"]
    journal = CommandJournal(config.state_dir)
    instance = HttpChallenges(config, journal)
    try:
        yield instance
    finally:
        await instance.close()
        journal.close()


def url(instance, token):
    return (
        "http://"
        + instance.config.certificate_http_address
        + "/.well-known/acme-challenge/"
        + token
    )


async def test_standalone_serves_only_exact_host_token_and_path(receiver):
    request = payload()
    challenge = request["challenges"][0]
    assert receiver.runner is None
    await receiver.present(request)
    async with httpx.AsyncClient(trust_env=False) as client:
        result = await client.get(
            url(receiver, challenge["token"]), headers={"Host": "node.example"}
        )
        assert result.status_code == 200
        assert result.text == challenge["key_authorization"]
        assert result.headers["cache-control"] == "no-store"
        for host in ("elsewhere.example", "user@node.example", "node.example/other"):
            assert (
                await client.get(url(receiver, challenge["token"]), headers={"Host": host})
            ).status_code == 404
        for path in ("missing", challenge["token"] + "?secret=true", "../commands.sqlite"):
            assert (
                await client.get(url(receiver, path), headers={"Host": "node.example"})
            ).status_code == 404
        assert (await client.post(url(receiver, challenge["token"]))).status_code == 405
        head = await client.head(
            url(receiver, challenge["token"]), headers={"Host": "node.example"}
        )
        assert head.status_code == 200 and not head.content
    await receiver.release(cleanup(request))
    assert receiver.runner is None


async def test_host_opt_out_does_not_grant_listener_or_webroot(receiver):
    receiver.config.certificate_http_address = None
    receiver.config.certificate_webroots = []
    for request in (payload(), payload(mode="webroot", webroot_id="site")):
        with pytest.raises(RuntimeFailure, match="host owner"):
            await receiver.present(request)
    assert not receiver._leases()
    assert not receiver.html.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": 0},
        {"expires_at": float("nan")},
        {"expires_at": time() + 3600},
        {"mode": "dns"},
        {"webroot_id": "../escape"},
        {"webroot_id": "site"},
        {"mode": "webroot"},
        {"path": "/etc/passwd"},
        {"lease_id": "../../other"},
        {"challenges": []},
    ],
)
async def test_invalid_requests_leave_no_lease_or_files(receiver, changes):
    with pytest.raises((ValidationError, RuntimeFailure)):
        await receiver.present(payload(**changes))
    assert not receiver._leases()


async def test_bad_challenge_binding_and_duplicate_tokens_are_rejected(receiver):
    request = payload()
    request["challenges"][0]["key_authorization"] = "other." + "A" * 43
    with pytest.raises(ValidationError):
        await receiver.present(request)
    request = payload()
    request["challenges"] *= 2
    with pytest.raises(ValidationError):
        await receiver.present(request)


async def test_release_before_delayed_present_cannot_resurrect_a_lease(receiver):
    request = payload()
    await receiver.release(cleanup(request))
    with pytest.raises(RuntimeFailure, match="released"):
        await receiver.present(request)
    assert receiver.runner is None
    await receiver.release(cleanup(request))


async def test_presentation_is_idempotent_but_lease_identity_is_immutable(receiver):
    request = payload()
    assert await receiver.present(request) == await receiver.present(request)
    with pytest.raises(RuntimeFailure, match="different content"):
        await receiver.present({**request, "expires_at": time() + 125})
    duplicate = {**request, "lease_id": str(uuid4())}
    with pytest.raises(RuntimeFailure, match="another lease"):
        await receiver.present(duplicate)


async def test_listener_reloads_a_persisted_lease_after_restart(receiver):
    request = payload()
    await receiver.present(request)
    await receiver.close()
    again = HttpChallenges(receiver.config, type("Journal", (), {"db": receiver.db})())
    try:
        await again._listener()
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(
                url(again, request["challenges"][0]["token"]), headers={"Host": "node.example"}
            )
            assert response.status_code == 200
        await again.release(cleanup(request))
    finally:
        await again.close()


async def test_busy_port_never_stops_its_existing_owner(receiver):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        receiver.config.certificate_http_address = "127.0.0.1:" + str(listener.getsockname()[1])
        request = payload()
        with pytest.raises(OSError):
            await receiver.present(request)
        assert receiver._leases()[0]["status"] == "released"
        assert listener.fileno() >= 0


async def test_webroot_writes_public_responses_and_preserves_website(receiver):
    request = payload(mode="webroot", webroot_id="site")
    await receiver.present(request)
    root = receiver.html / "site"
    website = root / "index.html"
    website.write_text("retain website")
    file = root / ".well-known/acme-challenge" / request["challenges"][0]["token"]
    assert file.read_text() == request["challenges"][0]["key_authorization"]
    assert file.stat().st_mode & 0o777 == 0o644
    assert receiver.runner is None
    await receiver.present(request)
    receiver.config.certificate_webroots = []
    await receiver.release(cleanup(request))
    assert not file.exists()
    assert website.read_text() == "retain website"


@pytest.mark.parametrize("foreign", ["content", "hardlink", "symlink", "directory", "owner"])
async def test_webroot_cleanup_refuses_foreign_files(receiver, tmp_path, foreign):
    request = payload(mode="webroot", webroot_id="site")
    await receiver.present(request)
    root = receiver.html / "site/.well-known/acme-challenge"
    file = root / request["challenges"][0]["token"]
    if foreign == "content":
        file.write_text("operator replacement")
    elif foreign == "hardlink":
        os.link(file, tmp_path / "linked")
    elif foreign == "symlink":
        file.unlink()
        file.symlink_to(tmp_path / "outside")
    elif foreign == "directory":
        root.rename(root.with_name("previous"))
        root.mkdir()
        file.write_text("replacement directory")
    else:
        os.chown(file, 12345, 12345)
    with pytest.raises(RuntimeFailure):
        await receiver.release(cleanup(request))
    assert file.exists() or file.is_symlink()
    assert receiver._leases()[0]["status"] == "releasing"


async def test_existing_unowned_webroot_content_is_not_adopted(receiver):
    directory = receiver.html / "site/.well-known/acme-challenge"
    directory.mkdir(parents=True)
    sentinel = directory / "other-acme-client"
    sentinel.write_text("retain")
    with pytest.raises(RuntimeFailure, match="must be empty"):
        await receiver.present(payload(mode="webroot", webroot_id="site"))
    assert sentinel.read_text() == "retain"
    assert not receiver._leases()


async def test_partial_webroot_write_is_recovered_without_other_file_loss(receiver, monkeypatch):
    request = payload(mode="webroot", webroot_id="site")
    request["challenges"].extend(payload()["challenges"])
    original, count = http01.atomic_write, 0

    def crash(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("disk full")
        return original(*args)

    monkeypatch.setattr(http01, "atomic_write", crash)
    with pytest.raises(OSError, match="disk full"):
        await receiver.present(request)
    assert not list((receiver.html / "site/.well-known/acme-challenge").iterdir())
    assert receiver._leases()[0]["status"] == "released"


async def test_expiration_cleans_responses_and_stops_listener(receiver, monkeypatch):
    standalone = payload()
    rooted = payload(mode="webroot", webroot_id="site")
    await receiver.present(standalone)
    await receiver.present(rooted)
    monkeypatch.setattr(http01, "time", lambda: rooted["expires_at"] + 1)
    receiver._expire()
    await receiver._listener()
    assert receiver.runner is None
    assert not list((receiver.html / "site/.well-known/acme-challenge").iterdir())
    assert all(lease["status"] == "released" for lease in receiver._leases())


async def test_active_lease_limit_is_bounded(receiver):
    for _ in range(16):
        await receiver.present(payload())
    with pytest.raises(RuntimeFailure, match="Too many"):
        await receiver.present(payload())


async def test_one_changed_webroot_does_not_block_other_expiry(receiver, monkeypatch):
    damaged = payload(mode="webroot", webroot_id="site")
    other = payload()
    await receiver.present(damaged)
    await receiver.present(other)
    path = receiver.html / "site/.well-known/acme-challenge" / damaged["challenges"][0]["token"]
    path.write_text("host replacement")
    monkeypatch.setattr(http01, "time", lambda: other["expires_at"] + 1)
    with pytest.raises(RuntimeFailure, match="changed"):
        receiver._expire()
    assert receiver._leases("id=?", (other["lease_id"],))[0]["status"] == "released"
    assert path.read_text() == "host replacement"


@pytest.mark.parametrize(
    "address",
    [
        "localhost:80",
        "127.0.0.1",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "user@127.0.0.1:80",
        "127.0.0.1:80/path",
    ],
)
def test_host_listener_requires_a_literal_ip_and_port(config, address):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**config.model_dump(), "certificate_http_address": address})


def test_snapshot_contains_only_host_capabilities(receiver):
    assert receiver.snapshot() == {
        "version": 1,
        "standalone": True,
        "webroots": ["site"],
        "cleanup_error": None,
    }
    assert "test-node-token" not in json.dumps(receiver.snapshot())
