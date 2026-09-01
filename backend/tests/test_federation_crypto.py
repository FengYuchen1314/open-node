import base64
import json

import pytest
from open_node.domain.server_sharing import FederationCommandCreate
from open_node.services.federation_crypto import (
    FEDERATION_ENCRYPTED_HEADER,
    FEDERATION_KEY_EXCHANGE_HEADER,
    FederationSessionCache,
    derive_federation_session,
    generate_ephemeral,
)
from open_node.services.federation_transport import FederationHTTPTransport
from open_node.services.secure_channel import ChannelError, decode_public_key

TOKEN = "F" * 43


def paired_sessions(owner_token=TOKEN, consumer_token=TOKEN):
    owner_private, owner_public = generate_ephemeral()
    consumer_private, consumer_public = generate_ephemeral()
    owner = derive_federation_session(
        owner_private,
        owner_public,
        consumer_public,
        owner_token,
        is_initiator=False,
    )
    consumer = derive_federation_session(
        consumer_private,
        owner_public,
        consumer_public,
        consumer_token,
        is_initiator=True,
    )
    return owner, consumer


def test_official_token_bound_federation_session_round_trip_and_replay_rejection():
    owner, consumer = paired_sessions()
    request = consumer.encrypt(b'{"method":"GET"}')
    assert owner.decrypt(request) == b'{"method":"GET"}'
    response = owner.encrypt(b'{"success":true}')
    assert consumer.decrypt(response) == b'{"success":true}'
    with pytest.raises(ChannelError):
        owner.decrypt(request)

    wrong_owner, wrong_consumer = paired_sessions(owner_token="G" * 43)
    with pytest.raises(ChannelError):
        wrong_owner.decrypt(wrong_consumer.encrypt(b"secret"))


class OfficialOwnerTransport(FederationHTTPTransport):
    def __init__(self):
        super().__init__()
        self.owner_sessions = FederationSessionCache()
        self.handshakes = 0
        self.encrypted_requests = 0

    def _request(self, owner_url, token, method, endpoint, body=None, **kwargs):
        if endpoint == "/api/v1/federation/manage":
            return None
        return super()._request(owner_url, token, method, endpoint, body, **kwargs)

    @staticmethod
    def _result(payload):
        value = json.loads(payload.decode("utf-8"))
        body = json.loads(base64.b64decode(value["body"]))
        return json.dumps(
            {"success": True, "tag": body["tag"]}, separators=(",", ":")
        ).encode()

    def _request_bytes(
        self, owner_url, token, method, endpoint, encoded=None, *, extra_headers=None, **kwargs
    ):
        assert endpoint == "/api/federation/manage"
        headers = extra_headers or {}
        if key := headers.get(FEDERATION_KEY_EXCHANGE_HEADER):
            self.handshakes += 1
            consumer_public = decode_public_key(key)
            owner_private, owner_public = generate_ephemeral()
            session = derive_federation_session(
                owner_private,
                owner_public,
                consumer_public,
                token,
                is_initiator=False,
            )
            self.owner_sessions.set(token, session)
            return 200, {
                FEDERATION_KEY_EXCHANGE_HEADER: base64.b64encode(owner_public).decode()
            }, self._result(encoded)
        assert headers.get(FEDERATION_ENCRYPTED_HEADER) == "1"
        session = self.owner_sessions.get(token)
        if session is None:
            return 412, {}, b'{"error":"no session"}'
        self.encrypted_requests += 1
        result = self._result(session.decrypt(encoded))
        return 200, {FEDERATION_ENCRYPTED_HEADER: "1"}, session.encrypt(result)


def test_transport_negotiates_encrypts_and_recovers_an_official_owner_session():
    transport = OfficialOwnerTransport()
    payload = FederationCommandCreate(
        method="POST", path="/api/child/inbounds", body={"tag": "shared"}
    )
    first = transport.manage("https://owner.example", TOKEN, payload)
    second = transport.manage("https://owner.example", TOKEN, payload)
    assert first.result_body == second.result_body == {"success": True, "tag": "shared"}
    assert transport.handshakes == 1
    assert transport.encrypted_requests == 1

    transport.owner_sessions.delete(TOKEN)
    recovered = transport.manage("https://owner.example", TOKEN, payload)
    assert recovered.result_body["tag"] == "shared"
    assert transport.handshakes == 2
