import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from acme import challenges, messages
from open_node.services import certificate_remote_acme as remote
from open_node.services.certificate_acme import signing_key
from open_node.services.certificate_vault import CertificateVault


def request(tmp_path):
    vault = CertificateVault(tmp_path / "vault")
    work = vault.root / str(uuid4())
    data = {
        "job_id": str(uuid4()),
        "profile_work": str(work),
        "storage_email": "a@example.com",
        "email": "a@example.com",
        "directory_url": "https://ca.example.com/directory",
        "domains": ["example.com"],
        "timeout": 15,
    }
    return vault, data, work / "jobs" / data["job_id"]


def test_lost_new_order_response_never_creates_another_order_on_resume(tmp_path, monkeypatch):
    vault, data, work = request(tmp_path)
    client = Mock()
    client.directory = {"newOrder": "https://ca.example.com/new-order"}
    client._post.side_effect = OSError("response lost")
    monkeypatch.setattr(remote, "account", lambda *_: client)
    with pytest.raises(OSError, match="response lost"):
        remote.obtain(vault, data, work)
    before = vault.read(work / "certificate.key")
    csr = vault.read(work / "request.csr")
    with pytest.raises(remote.RemoteIssueError, match="order_unconfirmed"):
        remote.obtain(vault, data, work)
    assert client._post.call_count == 1
    assert vault.read(work / "certificate.key") == before
    assert vault.read(work / "request.csr") == csr


def test_location_is_durable_before_first_authorization_fetch(tmp_path, monkeypatch):
    vault, data, work = request(tmp_path)
    client = Mock()
    client.directory = {"newOrder": "https://ca.example.com/new-order"}
    client._post.return_value.headers = {"Location": "https://ca.example.com/order/1"}
    client._post_as_get.side_effect = OSError("authorization request lost")
    monkeypatch.setattr(remote, "account", lambda *_: client)
    for _ in range(2):
        with pytest.raises(OSError, match="authorization"):
            remote.obtain(vault, data, work)
    saved = json.loads(vault.read(work / "order.json"))
    assert saved["uri"] == "https://ca.example.com/order/1"
    assert saved["job_id"] == data["job_id"]
    assert client._post.call_count == 1 and client._post_as_get.call_count == 2


def test_saved_order_is_bound_to_job_and_csr(tmp_path, monkeypatch):
    vault, data, work = request(tmp_path)
    client = Mock()
    client.directory = {"newOrder": "https://ca.example.com/new-order"}
    client._post.return_value.headers = {"Location": "https://ca.example.com/order/1"}
    client._post_as_get.side_effect = OSError()
    monkeypatch.setattr(remote, "account", lambda *_: client)
    with pytest.raises(OSError):
        remote.obtain(vault, data, work)
    saved = json.loads(vault.read(work / "order.json"))
    saved["job_id"] = "different-job"
    vault.write(work / "order.json", json.dumps(saved).encode())
    with pytest.raises(ValueError, match="does not match"):
        remote.obtain(vault, data, work)
    assert client._post.call_count == 1 and client._post_as_get.call_count == 1


def test_account_key_is_saved_before_registration_and_reused(tmp_path, monkeypatch):
    vault, data, work = request(tmp_path)
    seen = []

    def connect(_request, pem):
        seen.append(pem)
        client = Mock()
        client.query_registration.side_effect = OSError("CA unavailable")
        return client

    monkeypatch.setattr(remote, "connect", connect)
    for _ in range(2):
        with pytest.raises(OSError):
            remote.account(vault, data)
    assert len(seen) == 2 and seen[0] == seen[1]


def test_eab_binding_contains_only_the_public_account_key(tmp_path, monkeypatch):
    vault, data, _work = request(tmp_path)
    data["eab"] = {"kid": "fixture", "hmac": base64.urlsafe_b64encode(b"a" * 32).decode()}
    key, _algorithm = signing_key(remote.key_pem().decode())
    client = Mock()
    client.net.key = key
    client.directory = messages.Directory({"newAccount": "https://ca.example.com/new-account"})
    client.query_registration.side_effect = messages.Error(
        typ="urn:ietf:params:acme:error:accountDoesNotExist",
    )
    client.new_account.return_value = messages.RegistrationResource(
        uri="https://ca.example.com/account/1",
        body=messages.Registration(status="valid", contact=("mailto:a@example.com",)),
    )
    monkeypatch.setattr(remote, "connect", lambda *_: client)
    remote.account(vault, data)
    binding = client.new_account.call_args[0][0].external_account_binding
    payload = binding["payload"]
    encoded_key = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert "d" not in encoded_key
    assert encoded_key == key.public_key().to_partial_json()


def test_order_already_valid_is_fetched_without_presentation_or_new_order(tmp_path, monkeypatch):
    vault, data, work = request(tmp_path)
    client = Mock()
    client.directory = {"newOrder": "https://ca.example.com/new-order"}
    client._post.return_value.headers = {"Location": "https://ca.example.com/order/1"}
    client._post_as_get.return_value.json.return_value = {
        "status": "valid",
        "identifiers": [{"type": "dns", "value": "example.com"}],
        "authorizations": [],
        "finalize": "https://ca.example.com/order/1/finalize",
        "certificate": "https://ca.example.com/certificate/1",
    }
    client.poll_finalization.return_value = SimpleNamespace(fullchain_pem="certificate")
    monkeypatch.setattr(remote, "account", lambda *_: client)
    monkeypatch.setattr(remote, "material", lambda cert, _key, names: (cert, names))
    present = Mock()
    result = remote.obtain(vault, data, work, present)
    assert result == ("certificate", ["example.com"])
    assert client.poll_finalization.call_args[0][0].body.status == messages.STATUS_VALID
    assert not present.called and not client.answer_challenge.called
    remote.obtain(vault, data, work, present)
    assert client._post.call_count == 1


@pytest.mark.parametrize(
    "status,answers", [(messages.STATUS_PENDING, 1), (messages.STATUS_PROCESSING, 0)]
)
def test_resume_serves_processing_challenge_without_answering_it_twice(
    tmp_path, monkeypatch, status, answers
):
    vault, data, work = request(tmp_path)
    client = Mock()
    client.net.key, _ = signing_key(remote.key_pem().decode())
    client.directory = {"newOrder": "https://ca.example.com/new-order"}
    client._post.return_value.headers = {"Location": "https://ca.example.com/order/1"}
    client._post_as_get.return_value.json.return_value = {
        "status": "pending",
        "identifiers": [{"type": "dns", "value": "example.com"}],
        "authorizations": ["https://ca.example.com/authz/1"],
        "finalize": "https://ca.example.com/order/1/finalize",
    }
    client._authzr_from_response.return_value = messages.AuthorizationResource(
        uri="https://ca.example.com/authz/1",
        body=messages.Authorization(
            identifier=messages.Identifier(typ=messages.IDENTIFIER_FQDN, value="example.com"),
            status=messages.STATUS_PENDING,
            challenges=(
                messages.ChallengeBody(
                    chall=challenges.HTTP01(token=b"a" * 32),
                    status=status,
                    uri="https://ca.example.com/challenge/1",
                ),
            ),
        ),
    )
    client.poll_finalization.return_value = SimpleNamespace(fullchain_pem="certificate")
    monkeypatch.setattr(remote, "account", lambda *_: client)
    monkeypatch.setattr(remote, "material", lambda *_: {})
    presenter = Mock()
    remote.obtain(vault, data, work, presenter)
    assert client.answer_challenge.call_count == answers
    assert presenter.call_count == 1
    assert presenter.call_args[0][0][0]["domain"] == "example.com"
