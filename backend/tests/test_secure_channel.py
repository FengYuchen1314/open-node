import asyncio
import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import authenticated_client
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.inventory import AgentCommandCreate, AgentCommandPayloadError
from open_node.main import create_app
from open_node.services import secure_channel
from open_node.services.inventory import CommandModel
from open_node.services.secure_channel import (
    MAX_SEQUENCE,
    AgentIdentity,
    AgentSocket,
    ChannelError,
    ChannelSession,
)
from sqlalchemy import update


def sessions():
    material = (bytes(range(32)), bytes(range(32, 64)), bytes(range(64, 96)))
    return ChannelSession(*material), ChannelSession(*material, is_master=False)


def test_directional_keys_window_and_authenticated_advancement():
    master, agent = sessions()
    packets = [agent.encrypt(str(index).encode()) for index in range(70)]
    assert master.decrypt(packets[69]) == b"69"
    assert master.decrypt(packets[6]) == b"6"
    for packet in (packets[5], packets[69]):
        with pytest.raises(ChannelError):
            master.decrypt(packet)
    assert agent.decrypt(master.encrypt(b"reply")) == b"reply"
    fresh = agent.encrypt(b"valid after forgery")
    forged = fresh[:1] + MAX_SEQUENCE.to_bytes(8, "big") + fresh[9:]
    with pytest.raises(ChannelError):
        master.decrypt(forged)
    assert master.decrypt(fresh) == b"valid after forgery"


@pytest.mark.parametrize("corruption", ["version", "sequence", "tag", "truncated", "reflection"])
def test_envelopes_reject_corruption_without_consuming_valid_packet(corruption):
    master, agent = sessions()
    packet = agent.encrypt(b"protected")
    invalid = {
        "version": b"\x02" + packet[1:],
        "sequence": packet[:1] + b"\0" * 8 + packet[9:],
        "tag": packet[:-1] + bytes([packet[-1] ^ 1]),
        "truncated": packet[:24],
        "reflection": master.encrypt(b"wrong direction"),
    }[corruption]
    with pytest.raises(ChannelError):
        master.decrypt(invalid)
    assert master.decrypt(packet) == b"protected"


def test_send_sequence_never_wraps():
    master, _ = sessions()
    master.send_sequence = MAX_SEQUENCE - 1
    assert master.encrypt(b"last")[1:9] == MAX_SEQUENCE.to_bytes(8, "big")
    with pytest.raises(ChannelError):
        master.encrypt(b"must not reuse nonce")


@pytest.mark.parametrize("value", [float("nan"), "\ud800"])
def test_invalid_json_command_is_rejected(value):
    command = AgentCommandCreate(
        method="POST", path="/api/child/xray/config", body={"value": value}
    )
    with pytest.raises(AgentCommandPayloadError):
        command.validate_wire_payload()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        b"\xff",
        b'{"number":NaN}',
        b'{"number":Infinity}',
        b'{"number":-Infinity}',
        b'{"incomplete":',
        b"[" * 1100 + b"]" * 1100,
    ],
)
async def test_encrypted_messages_require_bounded_utf8_json(data):
    master, agent = sessions()

    class Socket:
        async def receive(self):
            return {"type": "websocket.receive", "bytes": agent.encrypt(data)}

    channel = AgentSocket(Socket())
    channel.session = master
    with pytest.raises(ChannelError):
        await channel.receive_json()


def test_oversized_command_is_rejected_before_queuing(setup):
    client, edge, _ = setup
    path = f"/api/v1/servers/{edge['server']['id']}/commands"
    response = client.post(
        path,
        json={
            "method": "POST",
            "path": "/api/child/xray/config",
            "body": "x" * secure_channel.MAX_MESSAGE_BYTES,
        },
    )
    assert response.status_code == 422
    assert client.get(path).json()["commands"] == []


@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("inflight", [False, True])
def test_legacy_oversized_work_is_not_claimed_or_falsely_completed(setup, transport, inflight):
    client, edge, _ = setup
    store = client.app.state.inventory
    commands = store.create_command_sequence(
        UUID(edge["server"]["id"]),
        [
            AgentCommandCreate(method="POST", path="/api/child/outbounds", body={}),
            AgentCommandCreate(method="GET", path="/api/child/scan"),
        ],
    )
    with store._session() as session:
        session.execute(
            update(CommandModel)
            .where(CommandModel.id == str(commands[0].id))
            .values(
                body="x" * secure_channel.MAX_MESSAGE_BYTES,
                attempts=int(inflight),
                status="leased" if inflight else "pending",
                leased_at=datetime.now(UTC) - timedelta(hours=1) if inflight else None,
            )
        )
        session.commit()
    if transport == "http":
        result = client.post("/api/v1/agents/commands/lease", json={"token": edge["agent_token"]})
        assert result.json()["commands"] == []
    else:
        assert store.lease_command_for_push(commands[0].id) is None
    rows = client.get(f"/api/v1/servers/{edge['server']['id']}/commands").json()["commands"]
    indexed = {row["id"]: row for row in rows}
    first, second = [indexed[str(command.id)] for command in commands]
    assert first["status"] == ("failed" if inflight else "skipped")
    assert first["attempts"] == int(inflight)
    assert first["result_error"] == "Sensitive Agent command failed"
    internal = next(
        item for item in store.list_commands(UUID(edge["server"]["id"]))
        if item.id == commands[0].id
    )
    assert "wire limit" in internal.result_error
    assert second["status"] == "skipped"


def test_identity_is_persistent_private_and_never_silently_replaced(tmp_path):
    path = tmp_path / "identity" / "seed"
    identity = AgentIdentity.create(path)
    before = path.read_bytes()
    assert len(before) == 32 and path.stat().st_mode & 0o777 == 0o600
    assert AgentIdentity.load(path).public_metadata() == identity.public_metadata()
    with pytest.raises(FileExistsError):
        AgentIdentity.create(path)
    assert path.read_bytes() == before
    path.write_bytes(b"damaged")
    with pytest.raises(ValueError):
        AgentIdentity.load(path)
    assert path.read_bytes() == b"damaged"


def test_identity_rejects_exposed_symlink_and_non_regular_files(tmp_path):
    path = tmp_path / "seed"
    AgentIdentity.create(path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        AgentIdentity.load(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        AgentIdentity.load(link)
    with pytest.raises(ValueError):
        AgentIdentity.load(tmp_path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError):
        AgentIdentity.load(fifo)


def test_identity_cli_outputs_only_public_material(tmp_path):
    path = tmp_path / "seed"
    command = [sys.executable, "-m", "open_node.agent_identity"]
    created = subprocess.run(
        [*command, "create", str(path)], capture_output=True, text=True, check=True, timeout=10
    )
    metadata = json.loads(created.stdout)
    assert metadata["enabled"] and len(metadata["fingerprint"]) == 64
    assert base64.b64encode(path.read_bytes()).decode() not in created.stdout
    shown = subprocess.run(
        [*command, "show", str(path)], capture_output=True, text=True, check=True, timeout=10
    )
    assert json.loads(shown.stdout) == metadata
    refused = subprocess.run(
        [*command, "create", str(path)], capture_output=True, text=True, timeout=10
    )
    assert refused.returncode == 1
    assert AgentIdentity.load(path).public_metadata() == metadata


@pytest.fixture
def setup(tmp_path):
    key = tmp_path / "seed"
    identity = AgentIdentity.create(key)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", agent_identity_file=key)
    client = authenticated_client(create_app(settings))
    edge = client.post("/api/v1/servers", json={"name": "encrypted-edge"}).json()
    return client, edge, identity


def exchange(websocket, identity):
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    websocket.send_json(
        {
            "type": "key_exchange",
            "payload": {
                "agent_ephemeral_pub": base64.b64encode(public).decode(),
            },
        }
    )
    reply = websocket.receive_json()
    assert reply["type"] == "key_exchange_resp"
    master = base64.b64decode(reply["payload"]["master_ephemeral_pub"])
    pinned = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(identity.public_metadata()["public_key"])
    )
    pinned.verify(base64.b64decode(reply["payload"]["signature"]), master)
    return ChannelSession(
        private.exchange(X25519PublicKey.from_public_bytes(master)), public, master, is_master=False
    )


def send(websocket, session, message):
    packet = session.encrypt(json.dumps(message).encode())
    websocket.send_bytes(packet)
    return packet


def receive(websocket, session):
    return json.loads(session.decrypt(websocket.receive_bytes()))


@pytest.mark.parametrize("path", ["/api/remote/ws", "/api/v1/agents/ws"])
def test_encrypted_auth_rpc_stream_and_heartbeat(setup, path):
    client, edge, identity = setup
    server = edge["server"]["id"]
    with client.websocket_connect(path) as websocket:
        session = exchange(websocket, identity)
        send(
            websocket,
            session,
            {
                "type": "auth",
                "payload": {
                    "token": edge["agent_token"],
                    "capabilities": {"rpc": True, "stream": True},
                },
            },
        )
        assert receive(websocket, session)["payload"]["success"]
        assert receive(websocket, session)["payload"]["path"] == "/api/child/xray/config"
        send(websocket, session, {"type": "heartbeat", "payload": {}})
        assert receive(websocket, session)["type"] == "heartbeat_ack"
        command = client.post(
            f"/api/v1/servers/{server}/commands",
            json={
                "method": "GET",
                "path": "/api/child/system/info",
                "stream": True,
            },
        ).json()["command"]
        assert receive(websocket, session)["payload"]["request_id"] == command["request_id"]
        send(
            websocket,
            session,
            {
                "type": "rpc_stream_data",
                "payload": {
                    "request_id": command["request_id"],
                    "data": "protected stream frame",
                },
            },
        )
        send(
            websocket,
            session,
            {
                "type": "rpc_reply",
                "payload": {
                    "request_id": command["request_id"],
                    "status": 200,
                    "body": {"success": True},
                },
            },
        )
        assert receive(websocket, session)["type"] == "rpc_reply_ack"
    commands = client.get(f"/api/v1/servers/{server}/commands").json()["commands"]
    assert next(item for item in commands if item["id"] == command["id"])["status"] == "succeeded"
    frames = client.get(f"/api/v1/servers/{server}/commands/{command['id']}/stream").json()[
        "frames"
    ]
    assert frames[0]["data"] == "protected stream frame"
    assert not client.app.state.agent_connections.is_connected(UUID(server))


@pytest.mark.parametrize("attack", ["plaintext", "replay", "tamper"])
def test_established_channel_rejects_downgrade_replay_and_tampering(setup, attack):
    client, edge, identity = setup
    with client.websocket_connect("/api/remote/ws") as websocket:
        session = exchange(websocket, identity)
        send(websocket, session, {"type": "auth", "payload": {"token": edge["agent_token"]}})
        assert receive(websocket, session)["payload"]["success"]
        packet = send(websocket, session, {"type": "ping"})
        assert receive(websocket, session)["type"] == "pong"
        if attack == "plaintext":
            websocket.send_json({"type": "ping"})
        elif attack == "replay":
            websocket.send_bytes(packet)
        else:
            packet = session.encrypt(b'{"type":"heartbeat"}')
            websocket.send_bytes(packet[:-1] + bytes([packet[-1] ^ 1]))
        assert websocket.receive() == {"type": "websocket.close", "code": 1008, "reason": ""}


@pytest.mark.parametrize("probe,valid", [(True, True), (True, False), (False, False)])
def test_encrypted_probe_and_invalid_token_have_no_registration_side_effects(setup, probe, valid):
    client, edge, identity = setup
    with client.websocket_connect("/api/remote/ws") as websocket:
        session = exchange(websocket, identity)
        send(
            websocket,
            session,
            {
                "type": "auth",
                "payload": {
                    "token": edge["agent_token"] if valid else "incorrect",
                    "probe": probe,
                },
            },
        )
        assert receive(websocket, session)["payload"]["success"] is valid
        assert websocket.receive()["code"] == (1000 if valid else 1008)
    assert client.get("/api/v1/agents").json() == []


@pytest.mark.parametrize(
    "public", [None, "invalid", "A" * 44, base64.b64encode(bytes(32)).decode()]
)
def test_invalid_exchange_is_closed_without_registering(setup, public):
    client, _, _ = setup
    with client.websocket_connect("/api/remote/ws") as websocket:
        websocket.send_json({"type": "key_exchange", "payload": {"agent_ephemeral_pub": public}})
        assert websocket.receive()["code"] == 1008
    assert client.get("/api/v1/agents").json() == []


def test_handshake_deadline_and_missing_identity_fail_closed(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(secure_channel, "AUTH_TIMEOUT_SECONDS", 0.05)
    with client.websocket_connect("/api/remote/ws") as websocket:
        assert websocket.receive()["code"] == 1008
    client.app.state.agent_identity = None
    with client.websocket_connect("/api/remote/ws") as websocket:
        websocket.send_json({"type": "key_exchange", "payload": {}})
        assert websocket.receive()["code"] == 1008
    assert client.get("/api/v1/agents").json() == []


def test_identity_api_requires_operator_and_never_exposes_private_key(setup):
    client, _, identity = setup
    anonymous = TestClient(client.app, base_url="https://testserver")
    assert anonymous.get("/api/v1/agents/identity").status_code == 401
    result = client.get("/api/v1/agents/identity")
    assert result.json() == identity.public_metadata()
    assert result.headers["cache-control"] == "no-store"
    client.app.state.agent_identity = None
    assert client.get("/api/v1/agents/identity").json()["enabled"] is False


def test_configured_legacy_identity_requires_exchange_but_native_transport_still_works(setup):
    client, edge, _ = setup
    message = {"type": "auth", "payload": {"token": edge["agent_token"]}}
    with client.websocket_connect("/api/remote/ws") as websocket:
        websocket.send_json(message)
        assert websocket.receive()["code"] == 1008
    assert client.get("/api/v1/agents").json() == []
    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(message)
        assert websocket.receive_json()["payload"]["success"]


def test_old_encrypted_auth_cannot_be_reused_on_a_fresh_connection(setup):
    client, edge, identity = setup
    with client.websocket_connect("/api/remote/ws") as websocket:
        session = exchange(websocket, identity)
        packet = send(
            websocket,
            session,
            {
                "type": "auth",
                "payload": {
                    "token": edge["agent_token"],
                    "probe": True,
                },
            },
        )
        assert receive(websocket, session)["payload"]["success"]
        assert websocket.receive()["code"] == 1000
    with client.websocket_connect("/api/remote/ws") as websocket:
        exchange(websocket, identity)
        websocket.send_bytes(packet)
        assert websocket.receive()["code"] == 1008
    assert client.get("/api/v1/agents").json() == []


@pytest.mark.asyncio
async def test_concurrent_writes_keep_sequence_and_wire_order():
    master, agent = sessions()

    class Socket:
        def __init__(self):
            self.frames = []

        async def send_bytes(self, data):
            await asyncio.sleep(0.001)
            self.frames.append(data)

    raw = Socket()
    channel = AgentSocket(raw)
    channel.session = master
    await asyncio.gather(*(channel.send_json({"index": i}) for i in range(20)))
    assert [int.from_bytes(frame[1:9], "big") for frame in raw.frames] == list(range(1, 21))
    assert [json.loads(agent.decrypt(frame))["index"] for frame in raw.frames] == list(range(20))
