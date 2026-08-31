import asyncio
import json
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from conftest import authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.notifications import NotificationError
from open_node.main import create_app
from open_node.services import notifications as store_module
from open_node.services.notification_worker import NotificationWorker
from open_node.services.telegram_transport import TelegramOutcome
from pydantic import ValidationError
from test_subscriber_auth import login as subscriber_login
from test_subscriber_auth import make as make_subscriber

PREFIX = "/api/v1/notifications"
TOKEN = "123456:notification-api-test-secret-only"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'notifications.db'}",
            certificate_state_dir=tmp_path / "certificates",
        )
    )
    return authenticated_client(app)


def save(client, **changes):
    current = client.get(PREFIX + "/settings").json()
    payload = {
        "expected_revision": current["revision"],
        "enabled": False,
        "chat_id": "-1001234567890",
        "advance_days": 7,
        "timezone": "Asia/Shanghai",
        "token_action": "replace",
        "token": TOKEN,
        **changes,
    }
    response = client.put(PREFIX + "/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert TOKEN not in response.text


def test_notification_defaults_and_offline_actions_do_not_create_a_key_or_delivery(
    client, tmp_path
):
    response = client.get(PREFIX + "/settings")
    assert response.status_code == 200
    assert_private(response)
    settings = response.json()
    assert settings["revision"] == 0
    assert settings["enabled"] is False and settings["has_token"] is False
    assert settings["chat_id"] == ""
    assert settings["timezone"] == "Asia/Shanghai"
    assert settings["local_time"] == "09:00" and settings["advance_days"] == 7
    assert not (tmp_path / "notifications").exists()
    preview = client.post(PREFIX + "/preview", json={"expected_revision": 0})
    assert preview.status_code == 200, preview.text
    assert preview.json()["is_sample"] is True and preview.json()["total"] == 0
    assert_private(preview)
    assert client.get(PREFIX + "/deliveries").json()["deliveries"] == []
    assert not (tmp_path / "notifications").exists()


def test_saving_token_is_private_encrypted_and_does_not_send(client, tmp_path):
    calls = []

    async def should_not_send(*args):
        calls.append(args)
        raise AssertionError("Request handler unexpectedly attempted a notification")

    client.app.state.notification_transport.send = should_not_send
    settings = save(client)
    assert settings["has_token"] is True and settings["enabled"] is False
    assert "token" not in settings and TOKEN not in json.dumps(settings)
    assert settings["revision"] == 1
    assert (tmp_path / "notifications").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "notifications" / "telegram.key").stat().st_mode & 0o777 == 0o600
    assert TOKEN.encode() not in (tmp_path / "notifications.db").read_bytes()
    preview = client.post(PREFIX + "/preview", json={"expected_revision": 1})
    assert preview.status_code == 200
    assert client.get(PREFIX + "/deliveries").json()["deliveries"] == []
    assert calls == []
    for response in (client.get(PREFIX + "/settings"), preview):
        assert_private(response)


def test_explicit_test_is_durable_and_idempotent_while_reminders_are_disabled(client):
    settings = save(client)
    request_id = str(uuid4())
    payload = {"expected_revision": settings["revision"], "request_id": request_id}
    first = client.post(PREFIX + "/test", json=payload)
    assert first.status_code == 202, first.text
    first_value = first.json()["delivery"]
    assert first_value["state"] == "queued" and first_value["kind"] == "test"
    assert first_value["request_id"] == request_id
    assert first.json()["attempts"] == []
    second = client.post(PREFIX + "/test", json=payload)
    assert second.status_code == 202
    assert second.json()["delivery"]["id"] == first_value["id"]
    listed = client.get(PREFIX + "/deliveries")
    assert len(listed.json()["deliveries"]) == 1
    receipt = client.get(PREFIX + f"/requests/{request_id}")
    assert receipt.status_code == 200 and receipt.json()["id"] == first_value["id"]
    for response in (first, second, listed, receipt):
        assert_private(response)


@pytest.mark.asyncio
async def test_delivery_receipt_and_repeat_post_after_acceptance_do_not_resend(client):
    settings = save(client)
    payload = {"expected_revision": settings["revision"], "request_id": str(uuid4())}
    queued = client.post(PREFIX + "/test", json=payload).json()["delivery"]
    calls = []

    class Accepted:
        async def send(self, token, chat_id, text):
            calls.append((token, chat_id, text))
            return TelegramOutcome(state="accepted", code="telegram_accepted", message_id=987)

    worker = NotificationWorker(client.app.state.notifications, Accepted())
    assert await worker.tick()
    receipt = client.get(PREFIX + f"/deliveries/{queued['id']}")
    assert receipt.status_code == 200
    assert receipt.json()["delivery"]["state"] == "accepted"
    assert receipt.json()["delivery"]["manual_retry_allowed"] is False
    assert receipt.json()["attempts"][0]["message_id"] == 987
    replay = client.post(PREFIX + "/test", json=payload)
    assert replay.status_code == 202
    assert replay.json()["delivery"]["state"] == "accepted"
    assert await worker.tick() is False
    assert len(calls) == 1
    assert_private(receipt)


def test_cas_conflicts_and_unknown_receipt_lookup_are_fixed_and_private(client):
    save(client)
    stale = client.post(PREFIX + "/preview", json={"expected_revision": 0})
    assert stale.status_code == 409
    assert stale.json()["code"] == "notification_revision_conflict"
    missing = client.get(PREFIX + f"/requests/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "notification_request_not_found"
    for response in (stale, missing):
        assert_private(response)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/settings"),
        ("PUT", "/settings"),
        ("POST", "/preview"),
        ("POST", "/test"),
        ("GET", "/deliveries"),
        ("GET", f"/deliveries/{uuid4()}"),
        ("POST", f"/deliveries/{uuid4()}/retry"),
        ("GET", f"/requests/{uuid4()}"),
    ],
)
def test_all_notification_routes_require_administrator_before_body_validation(client, method, path):
    outsider = TestClient(client.app, base_url="https://testserver")
    response = outsider.request(method, PREFIX + path, content=TOKEN)
    assert response.status_code == 401
    assert_private(response)


def test_real_subscriber_session_has_no_access_to_notifications(tmp_path):
    _app, _admin, subscriber = make_subscriber(tmp_path)
    assert subscriber_login(subscriber).json()["authenticated"] is True
    for method, path in (("GET", "/settings"), ("GET", "/deliveries"), ("POST", "/test")):
        response = subscriber.request(method, PREFIX + path, json={"request_id": str(uuid4())})
        assert response.status_code == 401
        assert_private(response)


def test_csrf_and_exact_origin_are_checked_before_secret_body_validation(client):
    csrf = client.headers.pop("X-CSRF-Token")
    no_csrf = client.put(PREFIX + "/settings", content=TOKEN)
    assert no_csrf.status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    bad_origin = client.post(
        PREFIX + "/test", content=TOKEN, headers={"Origin": "https://attacker.invalid"}
    )
    assert bad_origin.status_code == 403
    for response in (no_csrf, bad_origin):
        assert_private(response)


@pytest.mark.parametrize(
    "body",
    [
        b"null",
        b"[]",
        b"false",
        b'{"expected_revision":0,"expected_revision":1}',
        b'{"expected_revision":NaN}',
        b'{"expected_revision":Infinity}',
        b'{"expected_revision":true}',
        b'{"expected_revision":0,"token":"' + TOKEN.encode() + b'"}',
        b'{"expected_revision":0,"nested":{"token":"' + TOKEN.encode() + b'"}}',
        b"\xff\xfe",
        b"[" * 4000 + b"]" * 4000,
    ],
)
def test_strict_json_errors_never_echo_secret_or_untrusted_inputs(client, body):
    response = client.post(
        PREFIX + "/preview", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "code": "notification_invalid_request",
        "detail": "Invalid notification request.",
        "license_required": False,
    }
    assert_private(response)


def test_payload_size_media_query_and_path_failures_are_bounded_safe_errors(client):
    responses = [
        client.put(PREFIX + "/settings", content=TOKEN),
        client.put(
            PREFIX + "/settings",
            content=b"x" * 8193,
            headers={"Content-Type": "application/json"},
        ),
        client.get(PREFIX + "/deliveries", params={"limit": 0}),
        client.get(PREFIX + "/deliveries", params={"limit": 101}),
        client.get(PREFIX + "/requests/untrusted-secret-not-a-uuid"),
    ]
    assert [response.status_code for response in responses] == [415, 413, 422, 422, 422]
    for response in responses:
        assert_private(response)
        assert response.json()["code"] == "notification_invalid_request"
        assert "untrusted-secret-not-a-uuid" not in response.text


def test_storage_error_uses_only_fixed_code_and_message(client, monkeypatch):
    def unavailable():
        raise NotificationError(503, "notification_storage_key_missing")

    monkeypatch.setattr(client.app.state.notifications, "get_settings", unavailable)
    response = client.get(PREFIX + "/settings")
    assert response.status_code == 503
    assert response.json()["code"] == "notification_storage_key_missing"
    assert_private(response)


@pytest.mark.parametrize("fault", ["missing", "wrong"])
def test_key_fault_cannot_clear_ciphertext_but_can_disable_then_restore(client, tmp_path, fault):
    settings = save(client, enabled=True)
    store = client.app.state.notifications
    key_path = tmp_path / "notifications" / "telegram.key"
    original_key = key_path.read_bytes()
    with store.inventory._session() as session:
        ciphertext = session.get(store_module.NotificationSettingsModel, 1).token_ciphertext
    if fault == "missing":
        key_path.unlink()
    else:
        key_path.write_bytes(Fernet.generate_key())
    payload = {
        "expected_revision": settings["revision"],
        "enabled": False,
        "chat_id": settings["chat_id"],
        "advance_days": settings["advance_days"],
        "timezone": settings["timezone"],
        "token_action": "clear",
    }
    blocked = client.put(PREFIX + "/settings", json=payload)
    assert blocked.status_code == 503
    assert blocked.json()["code"] == (
        "notification_storage_key_missing"
        if fault == "missing" else "notification_storage_key_invalid"
    )
    assert_private(blocked)
    unchanged = client.get(PREFIX + "/settings").json()
    assert unchanged["revision"] == settings["revision"]
    assert unchanged["has_token"] and unchanged["enabled"]
    disabled = client.put(PREFIX + "/settings", json={**payload, "token_action": "keep"})
    assert disabled.status_code == 200
    assert disabled.json()["has_token"] and not disabled.json()["enabled"]
    with store.inventory._session() as session:
        assert session.get(store_module.NotificationSettingsModel, 1).token_ciphertext == ciphertext
    if fault == "missing":
        assert not key_path.exists()
    else:
        assert key_path.read_bytes() != original_key
    key_path.write_bytes(original_key)
    key_path.chmod(0o600)
    assert client.get(PREFIX + "/settings").json()["storage_ready"]
    cleared = client.put(
        PREFIX + "/settings", json={**payload, "expected_revision": disabled.json()["revision"]}
    )
    assert cleared.status_code == 200 and not cleared.json()["has_token"]
    assert key_path.read_bytes() == original_key


def test_restart_keeps_settings_requests_and_unstarted_delivery_without_sending(client, tmp_path):
    settings = save(client)
    request_id = str(uuid4())
    queued = client.post(
        PREFIX + "/test", json={"expected_revision": settings["revision"], "request_id": request_id}
    ).json()["delivery"]
    key = (tmp_path / "notifications" / "telegram.key").read_bytes()
    restarted_app = create_app(client.app.state.settings)
    restarted = TestClient(restarted_app, base_url="https://testserver")
    restarted.cookies.update(client.cookies)
    assert restarted.get(PREFIX + "/settings").json()["revision"] == settings["revision"]
    receipt = restarted.get(PREFIX + f"/requests/{request_id}")
    assert receipt.json()["id"] == queued["id"] and receipt.json()["state"] == "queued"
    assert (tmp_path / "notifications" / "telegram.key").read_bytes() == key
    assert receipt.json()["attempt_count"] == 0


def test_expired_inflight_delivery_is_unknown_not_requeued_on_restart(client):
    settings = save(client)
    request_id = str(uuid4())
    response = client.post(
        PREFIX + "/test", json={"expected_revision": settings["revision"], "request_id": request_id}
    )
    assert response.status_code == 202
    now = datetime.now(UTC)
    claimed = client.app.state.notifications.claim(now=now)
    assert claimed is not None
    restarted_app = create_app(client.app.state.settings)
    store = restarted_app.state.notifications
    store.recover(now=claimed.deadline_at + timedelta(seconds=1))
    assert store.claim(now=claimed.deadline_at + timedelta(seconds=2)) is None
    delivery = store.delivery(claimed.delivery_id)
    assert delivery.delivery.state == "unknown"
    assert delivery.delivery.attempt_count == 1


def test_unknown_manual_retry_requires_deadline_risk_fence_and_request_idempotency(
    client, monkeypatch
):
    current = [datetime.now(UTC)]
    monkeypatch.setattr(store_module, "_now", lambda value=None: value or current[0])
    settings = save(client)
    request_id = str(uuid4())
    queued = client.post(
        PREFIX + "/test", json={"expected_revision": settings["revision"], "request_id": request_id}
    ).json()["delivery"]
    store = client.app.state.notifications
    claimed = store.claim()
    store.finish(claimed, TelegramOutcome(state="unknown", code="telegram_response_timeout"))
    retry_path = PREFIX + f"/deliveries/{queued['id']}/retry"
    payload = {
        "expected_revision": settings["revision"],
        "request_id": str(uuid4()),
        "expected_attempt_id": str(claimed.attempt_id),
        "confirm_duplicate_risk": True,
    }
    assert client.post(retry_path, json=payload).json()["code"] == "notification_retry_too_early"
    current[0] = claimed.deadline_at + timedelta(seconds=1)
    unconfirmed = client.post(retry_path, json={**payload, "confirm_duplicate_risk": False})
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "notification_duplicate_risk_required"
    wrong_attempt = client.post(retry_path, json={**payload, "expected_attempt_id": str(uuid4())})
    assert wrong_attempt.status_code == 409
    assert wrong_attempt.json()["code"] == "notification_attempt_conflict"
    retried = client.post(retry_path, json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["delivery"]["state"] == "queued"
    assert retried.json()["delivery"]["id"] == queued["id"]
    assert len(retried.json()["attempts"]) == 1
    repeated = client.post(retry_path, json=payload)
    assert repeated.status_code == 200
    next_claim = store.claim()
    assert next_claim.attempt_id != claimed.attempt_id
    store.finish(
        next_claim, TelegramOutcome(state="accepted", code="telegram_accepted", message_id=1234)
    )
    accepted_replay = client.post(retry_path, json=payload)
    assert accepted_replay.json()["delivery"]["state"] == "accepted"
    assert len(accepted_replay.json()["attempts"]) == 2
    assert store.claim() is None
    for identifier in (request_id, payload["request_id"]):
        receipt = client.get(PREFIX + f"/requests/{identifier}")
        assert receipt.status_code == 200 and receipt.json()["id"] == queued["id"]
        assert_private(receipt)


def test_application_lifespan_dispatches_and_stops_its_own_notification_worker(client):
    settings = save(client)
    entered = Event()
    closed = Event()

    class HeldTransport:
        async def send(self, *_):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

    app = client.app
    app.state.notification_transport = HeldTransport()
    with TestClient(app, base_url="https://testserver") as running:
        running.cookies.update(client.cookies)
        running.headers["X-CSRF-Token"] = client.headers["X-CSRF-Token"]
        response = running.post(
            PREFIX + "/test",
            json={"expected_revision": settings["revision"], "request_id": str(uuid4())},
        )
        assert response.status_code == 202
        queued = response.json()["delivery"]
        assert entered.wait(5)
        receipt = running.get(PREFIX + f"/deliveries/{queued['id']}")
        assert receipt.json()["delivery"]["state"] == "sending"
    assert closed.wait(1)
    stopped = client.get(PREFIX + f"/deliveries/{queued['id']}").json()["delivery"]
    assert stopped["state"] == "unknown"
    assert stopped["code"] == "notification_worker_interrupted"


def test_notification_key_directory_setting_requires_absolute_non_root_path(tmp_path):
    for invalid in ("relative", "/", str(tmp_path / ".." / "notifications")):
        with pytest.raises(ValidationError):
            Settings(notifications_state_dir=invalid)
    assert Settings(notifications_state_dir="").notifications_state_dir is None
    assert Settings(notifications_state_dir=tmp_path).notifications_state_dir == tmp_path
    for invalid in (0, -1, 61):
        with pytest.raises(ValidationError):
            Settings(notifications_poll_seconds=invalid)


@pytest.mark.parametrize("suffix", ["certificates", "certificates/child", ""])
def test_notification_key_directory_must_not_overlap_other_private_vaults(tmp_path, suffix):
    with pytest.raises(ValueError, match="separate, non-overlapping"):
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'data.db'}",
                certificate_state_dir=tmp_path / "certificates",
                notifications_state_dir=tmp_path / suffix,
            )
        )
    assert not (tmp_path / "telegram.key").exists()


def test_notification_key_directory_must_not_overlap_external_vault(tmp_path):
    with pytest.raises(ValueError, match="separate, non-overlapping"):
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'data.db'}",
                external_subscriptions_state_dir=tmp_path / "external",
                notifications_state_dir=tmp_path / "external",
            )
        )
