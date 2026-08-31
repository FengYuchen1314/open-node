"""Notification persistence tests use only owned SQLite files and local fake receipts."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from open_node.domain.notifications import (
    NotificationError,
    NotificationRetryRequest,
    NotificationSettingsUpdate,
    NotificationTestRequest,
)
from open_node.services.inventory import (
    InventoryStore,
    ProductUserModel,
    ProductUserRemovalModel,
    SubscriptionArchivedTrafficModel,
    SubscriptionPlanModel,
)
from open_node.services.notifications import (
    CHAT_INTERVAL,
    TEST_MESSAGE,
    NotificationAttemptModel,
    NotificationChatModel,
    NotificationDeliveryModel,
    NotificationRequestModel,
    NotificationSettingsModel,
    NotificationStore,
)
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

NOW = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
TOKEN = SecretStr("123456:" + "notification-test-token-ONLY_" * 2)
OTHER_TOKEN = SecretStr("123456:" + "replacement-test-token-ONLY_" * 2)
CHAT = "-1001234567890"
OTHER_CHAT = "-1001234567891"


@dataclass(frozen=True)
class Receipt:
    state: str
    code: str
    message_id: int | None = None
    retry_after: int | None = None
    retryable: bool = False


@pytest.fixture
def store(tmp_path):
    inventory = InventoryStore("sqlite:///" + str(tmp_path / "notifications-test.db"))
    inventory.create_schema()
    value = NotificationStore(inventory, tmp_path / "notifications")
    value.create_schema()
    yield value
    inventory._engine.dispose()


def configure(store, *, now=NOW, **changes):
    current = store.get_settings()
    payload = {
        "expected_revision": current.revision, "enabled": True,
        "chat_id": current.chat_id or CHAT, "advance_days": current.advance_days,
        "timezone": current.timezone, "token_action": "keep",
    }
    if not current.has_token:
        payload.update(token_action="replace", token=TOKEN)
    payload.update(changes)
    return store.update_settings(NotificationSettingsUpdate(**payload), now=now)


def enqueue(store, *, now=NOW, request_id=None):
    payload = NotificationTestRequest(
        expected_revision=store.get_settings().revision, request_id=request_id or uuid4()
    )
    return store.enqueue_test(payload, now=now), payload


def seed(store, username="alice", *, expires_at=None, created_at=None, active=True):
    plan_id = str(uuid4())
    with store.inventory._session() as session:
        session.add(SubscriptionPlanModel(
            id=plan_id, name="Plan " + username, description="",
            traffic_limit_bytes=1, cycle_days=30, created_at=NOW - timedelta(days=40),
            updated_at=NOW,
        ))
        session.flush()
        session.add(ProductUserModel(
            username=username, display_name=username, role="user", is_active=active,
            current_plan_id=plan_id, plan_started_at=NOW - timedelta(days=28),
            plan_expires_at=expires_at or NOW + timedelta(days=2),
            created_at=created_at or NOW - timedelta(days=40), updated_at=NOW,
        ))
        session.commit()
    return plan_id


def change_user(store, username="alice", **changes):
    with store.inventory._session() as session:
        user = session.get(ProductUserModel, username)
        for key, value in changes.items():
            setattr(user, key, value)
        session.commit()


def second_store(store):
    inventory = InventoryStore(str(store.inventory._engine.url))
    value = NotificationStore(inventory, store.state_dir)
    value.create_schema()
    return value


def expect_code(code, callback):
    with pytest.raises(NotificationError) as captured:
        callback()
    assert captured.value.code == code
    assert TOKEN.get_secret_value() not in str(captured.value)
    return captured.value


def unknown_test(store, *, now=NOW):
    configure(store, enabled=False, now=now)
    result, request = enqueue(store, now=now)
    claim = store.claim(now=now)
    store.finish(
        claim, Receipt("unknown", "telegram_response_timeout"), now=now + timedelta(seconds=1)
    )
    return result.delivery.id, request, claim


def retry_payload(store, claim, **changes):
    payload = {
        "expected_revision": store.get_settings().revision, "request_id": uuid4(),
        "expected_attempt_id": claim.attempt_id, "confirm_duplicate_risk": True,
    }
    payload.update(changes)
    return NotificationRetryRequest(**payload)


def test_default_and_read_only_preview_create_no_vault_or_delivery(store):
    value = store.get_settings()
    assert value.revision == value.destination_revision == 0
    assert not value.enabled and not value.has_token
    assert value.storage_ready and value.storage_error is None
    assert value.advance_days == 7 and value.timezone == "Asia/Shanghai"
    preview = store.preview(0, now=NOW)
    assert preview.total == 0 and preview.is_sample
    assert "示例" in preview.sample_message and preview.as_of == NOW
    assert not store.state_dir.exists()
    assert store.scan(now=NOW) == 0 and store.claim(now=NOW) is None
    assert store.list_deliveries().deliveries == []
    assert not store.state_dir.exists()


def test_none_vault_allows_disabled_configuration_and_preview_but_no_send(store):
    value = NotificationStore(store.inventory, None)
    settings = value.get_settings()
    assert not settings.storage_ready
    assert settings.storage_error == "notification_storage_unavailable"
    saved = value.update_settings(NotificationSettingsUpdate(
        expected_revision=0, enabled=False, chat_id="", advance_days=2
    ), now=NOW)
    assert saved.revision == 1 and not saved.has_token
    assert value.preview(1, now=NOW).is_sample
    expect_code("notification_storage_unavailable", lambda: configure(value))
    assert not store.state_dir.exists()


@pytest.mark.parametrize("changes", [
    {"enabled": "false"}, {"enabled": 1}, {"expected_revision": True},
    {"expected_revision": 0.0}, {"expected_revision": -1}, {"advance_days": True},
    {"advance_days": "7"}, {"advance_days": 7.0}, {"advance_days": 0},
    {"advance_days": 366}, {"chat_id": -1234}, {"chat_id": -1234.0},
    {"chat_id": "+1234"}, {"chat_id": "001234"}, {"chat_id": "0"},
    {"chat_id": "1e5"}, {"chat_id": "1234.0"}, {"chat_id": "١٢٣٤"},
    {"chat_id": "4503599627370496"}, {"chat_id": "-4503599627370496"},
    {"chat_id": "1234\n"}, {"timezone": "../UTC"}, {"timezone": "Does/NotExist"},
    {"timezone": 8}, {"local_time": "10:00"}, {"token_action": "ignore"},
    {"token_action": "keep", "token": TOKEN}, {"token_action": "clear", "token": TOKEN},
    {"token_action": "replace"}, {"unexpected": TOKEN.get_secret_value()},
])
def test_settings_validation_is_strict_and_error_strings_never_echo_input(changes):
    payload = {"expected_revision": 0, "enabled": False, "chat_id": CHAT}
    payload.update(changes)
    with pytest.raises(ValidationError) as captured:
        NotificationSettingsUpdate(**payload)
    assert TOKEN.get_secret_value() not in str(captured.value)


@pytest.mark.parametrize("value", [
    "1:" + "s" * 20, "12345678901234567890:" + "s" * 128,
])
def test_token_accepts_bounded_official_shape_without_invented_id_length(value):
    payload = NotificationSettingsUpdate(
        expected_revision=0, enabled=False, chat_id=CHAT,
        token_action="replace", token=SecretStr(value),
    )
    assert payload.token.get_secret_value() == value
    assert value not in repr(payload)


@pytest.mark.parametrize("value", [
    "1:" + "s" * 19, "1:" + "s" * 129, "01:" + "s" * 20,
    "1:" + "é" * 20, "1:" + "s" * 20 + "\n", "1:" + "s" * 19 + "/",
])
def test_bad_token_cannot_echo_in_validation(value):
    with pytest.raises(ValidationError) as captured:
        NotificationSettingsUpdate(
            expected_revision=0, enabled=False, chat_id=CHAT,
            token_action="replace", token=SecretStr(value),
        )
    assert value not in str(captured.value)


def test_saved_token_is_encrypted_separate_private_and_never_in_responses(store):
    settings = configure(store, enabled=False)
    assert settings.has_token and settings.storage_ready and not settings.enabled
    assert settings.revision == settings.destination_revision == 1
    root = store.state_dir
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "telegram.key").stat().st_mode & 0o777 == 0o600
    assert (root / "telegram.initialized").stat().st_mode & 0o777 == 0o600
    snapshot = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
    for _ in range(2):
        assert store.get_settings() == settings
        store.preview(settings.revision, now=NOW)
    assert snapshot == {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()
    }
    result, _request = enqueue(store)
    assert TOKEN.get_secret_value() not in result.model_dump_json()
    assert TOKEN.get_secret_value() not in settings.model_dump_json()
    database = Path(store.inventory._engine.url.database).read_bytes()
    assert TOKEN.get_secret_value().encode() not in database
    assert (root / "telegram.key").read_bytes() not in database
    assert store.delivery(result.delivery.id).attempts == []


def test_settings_cas_is_cross_connection_and_failed_writer_does_not_rotate_token(store):
    configure(store)
    other = second_store(store)
    payload = NotificationSettingsUpdate(
        expected_revision=1, enabled=False, chat_id=CHAT, advance_days=6
    )

    def save(value):
        try:
            return value.update_settings(payload, now=NOW).revision
        except NotificationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [store, other]))
    assert sorted(str(item) for item in results) == ["2", "notification_revision_conflict"]
    expect_code("notification_revision_conflict", lambda: store.update_settings(
        NotificationSettingsUpdate(
            expected_revision=1, enabled=False, chat_id=CHAT,
            token_action="replace", token=OTHER_TOKEN,
        ), now=NOW
    ))
    enqueue(store)
    assert store.claim(now=NOW).token == TOKEN
    other.inventory._engine.dispose()


@pytest.mark.parametrize("problem,code", [
    ("missing", "notification_storage_key_missing"),
    ("wrong", "notification_storage_key_invalid"),
    ("permissions", "notification_storage_permissions"),
    ("marker_missing", "notification_storage_key_missing"),
    ("marker_wrong", "notification_storage_key_invalid"),
])
def test_initialized_vault_fails_closed_and_restored_backup_recovers(store, problem, code):
    configure(store)
    key = store.state_dir / "telegram.key"
    marker = store.state_dir / "telegram.initialized"
    original_key, original_marker = key.read_bytes(), marker.read_bytes()
    if problem == "missing":
        key.unlink()
    elif problem == "wrong":
        key.write_bytes(Fernet.generate_key())
    elif problem == "permissions":
        key.chmod(0o644)
    elif problem == "marker_missing":
        marker.unlink()
    else:
        marker.write_bytes(b"wrong purpose")
    snapshot = (key.read_bytes() if key.exists() else None, marker.exists())
    read = store.get_settings()
    assert read.has_token and not read.storage_ready and read.storage_error == code
    expect_code(code, lambda: configure(store, token_action="replace", token=OTHER_TOKEN))
    assert snapshot == (key.read_bytes() if key.exists() else None, marker.exists())
    # Keep + disabled remains available for emergency shutdown. Clearing must not
    # discard recoverable ciphertext when the original vault cannot be verified.
    with store.inventory._session() as session:
        original_ciphertext = session.get(NotificationSettingsModel, 1).token_ciphertext
    disabled = configure(store, enabled=False, token_action="keep")
    assert disabled.has_token and not disabled.enabled
    expect_code(code, lambda: configure(store, enabled=False, token_action="clear"))
    with store.inventory._session() as session:
        row = session.get(NotificationSettingsModel, 1)
        assert row.token_ciphertext == original_ciphertext and row.revision == disabled.revision
    expect_code(code, lambda: configure(store, token_action="replace", token=OTHER_TOKEN))
    key.write_bytes(original_key)
    key.chmod(0o600)
    marker.write_bytes(original_marker)
    marker.chmod(0o600)
    assert store.get_settings().storage_ready
    configure(store, token_action="replace", token=OTHER_TOKEN)
    enqueue(store)
    assert store.claim(now=NOW).token == OTHER_TOKEN


def test_encrypted_token_is_purpose_bound(store):
    configure(store)
    key = (store.state_dir / "telegram.key").read_bytes()
    invalid = Fernet(key).encrypt(json.dumps({
        "purpose": "open-node.certificates", "token": TOKEN.get_secret_value()
    }).encode()).decode()
    with store.inventory._session() as session:
        session.get(NotificationSettingsModel, 1).token_ciphertext = invalid
        session.commit()
    assert store.get_settings().storage_error == "notification_storage_key_invalid"
    expect_code("notification_storage_key_invalid", lambda: enqueue(store))


@pytest.mark.parametrize("kind", ["root_symlink", "key_symlink", "key_hardlink"])
def test_vault_rejects_symlinks_and_hardlinked_key(store, tmp_path, kind):
    if kind == "root_symlink":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        store.state_dir.symlink_to(other, target_is_directory=True)
        expect_code("notification_storage_permissions", lambda: configure(store))
        assert list(other.iterdir()) == []
        return
    configure(store)
    key = store.state_dir / "telegram.key"
    other = tmp_path / "other-key"
    if kind == "key_symlink":
        key.rename(other)
        key.symlink_to(other)
    else:
        os.link(key, other)
    assert store.get_settings().storage_error == "notification_storage_permissions"
    expect_code("notification_storage_permissions", lambda: enqueue(store))


def test_preview_strict_eligibility_and_truncation_not_quota_available(store):
    configure(store)
    for number in range(23):
        seed(store, f"active-{number:02}")
    seed(store, "expired", expires_at=NOW)
    seed(store, "later", expires_at=NOW + timedelta(days=7, microseconds=1))
    seed(store, "inactive", active=False)
    seed(store, "no-expiry")
    change_user(store, "no-expiry", plan_expires_at=None)
    seed(store, "no-plan")
    change_user(store, "no-plan", current_plan_id=None)
    seed(store, "removing")
    removal_id = str(uuid4())
    with store.inventory._session() as session:
        session.add(ProductUserRemovalModel(
            id=removal_id, username="removing", requested_at=NOW
        ))
        session.commit()
    change_user(store, "removing", removal_id=removal_id)
    # Real charged usage above the one-byte plan limit must not suppress an expiry reminder.
    with store.inventory._session() as session:
        session.add(SubscriptionArchivedTrafficModel(
            username="active-00", server_id=str(uuid4()), server_name="Fixture archived server",
            upload=1, download=2, weighted_upload=1, weighted_download=2, updated_at=NOW,
        ))
        session.commit()
    quota = store.inventory.subscription_user_quota("active-00", now=NOW)
    assert quota.over_quota and not quota.available and quota.charged_usage_bytes == 3
    preview = store.preview(1, now=NOW)
    assert preview.total == 23 and len(preview.candidates) == 20 and not preview.is_sample
    assert preview.candidates[0].username == "active-00"
    assert all(item.expires_at.tzinfo is UTC for item in preview.candidates)
    assert store.list_deliveries().deliveries == []
    assert store.scan(now=NOW) == 23
    expect_code("notification_revision_conflict", lambda: store.preview(0, now=NOW))


def test_scan_runs_at_local_0900_and_startup_later_deduplicates(store):
    configure(store)
    seed(store)
    assert store.scan(now=NOW - timedelta(microseconds=1)) == 0
    assert store.scan(now=NOW) == 1
    other = second_store(store)
    assert other.scan(now=NOW + timedelta(hours=5)) == 0
    assert len(other.list_deliveries().deliveries) == 1
    other.inventory._engine.dispose()


@pytest.mark.parametrize("current,inside,outside", [
    (datetime(2026, 3, 7, 14, tzinfo=UTC), datetime(2026, 3, 8, 13, tzinfo=UTC),
     datetime(2026, 3, 8, 13, 0, 1, tzinfo=UTC)),
    (datetime(2026, 10, 31, 13, tzinfo=UTC), datetime(2026, 11, 1, 14, tzinfo=UTC),
     datetime(2026, 11, 1, 14, 0, 1, tzinfo=UTC)),
])
def test_eligibility_uses_local_calendar_days_across_dst(store, current, inside, outside):
    configure(store, timezone="America/New_York", advance_days=1)
    seed(store, "boundary", expires_at=inside)
    seed(store, "outside", expires_at=outside)
    preview = store.preview(1, now=current)
    assert [item.username for item in preview.candidates] == ["boundary"]
    assert store.scan(now=current) == 1


def test_accepted_event_is_not_repeated_by_restart_rename_timezone_token_or_toggle(store):
    configure(store)
    plan_id = seed(store)
    assert store.scan(now=NOW) == 1
    claim = store.claim(now=NOW)
    assert "alice" in claim.text and "Plan alice" in claim.text and "Asia/Shanghai" in claim.text
    store.finish(claim, Receipt("accepted", "telegram_accepted", message_id=71), now=NOW)
    with store.inventory._session() as session:
        session.get(SubscriptionPlanModel, plan_id).name = "Renamed plan"
        session.get(ProductUserModel, "alice").display_name = "Renamed display"
        session.commit()
    configure(store, enabled=False)
    configure(store, enabled=True, token_action="replace", token=OTHER_TOKEN, timezone="UTC")
    later = NOW + timedelta(hours=10)
    restarted = second_store(store)
    assert restarted.scan(now=later) == 0
    assert restarted.claim(now=later) is None
    row = restarted.delivery(claim.delivery_id).delivery
    assert row.state == "accepted" and row.attempt_count == 1
    restarted.inventory._engine.dispose()


def test_event_identity_uses_full_expiry_and_user_incarnation(store):
    configure(store)
    plan_id = seed(store)
    assert store.scan(now=NOW) == 1
    claim = store.claim(now=NOW)
    store.finish(claim, Receipt("accepted", "telegram_accepted", message_id=1), now=NOW)
    change_user(store, plan_expires_at=NOW + timedelta(days=2, microseconds=1))
    assert store.scan(now=NOW) == 1
    next_claim = store.claim(now=NOW + timedelta(seconds=4))
    assert next_claim.delivery_id != claim.delivery_id
    store.finish(next_claim, Receipt("accepted", "telegram_accepted", message_id=2), now=NOW)
    with store.inventory._session() as session:
        session.delete(session.get(ProductUserModel, "alice"))
        session.commit()
        session.add(ProductUserModel(
            username="alice", display_name="alice", role="user", is_active=True,
            current_plan_id=plan_id, plan_expires_at=NOW + timedelta(days=2, microseconds=1),
            created_at=NOW, updated_at=NOW,
        ))
        session.commit()
    assert store.scan(now=NOW) == 1
    assert len(store.list_deliveries().deliveries) == 3


@pytest.mark.parametrize(
    "change", ["deleted", "inactive", "expired", "plan", "renewed", "incarnation"]
)
def test_claim_revalidates_queued_expiry_against_current_inventory(store, change):
    configure(store)
    seed(store)
    assert store.scan(now=NOW) == 1
    row = store.list_deliveries().deliveries[0]
    if change == "deleted":
        with store.inventory._session() as session:
            session.delete(session.get(ProductUserModel, "alice"))
            session.commit()
    else:
        changes = {
            "inactive": {"is_active": False}, "expired": {"plan_expires_at": NOW},
            "plan": {"current_plan_id": None},
            "renewed": {"plan_expires_at": NOW + timedelta(days=3)},
            "incarnation": {"created_at": NOW},
        }
        change_user(store, **changes[change])
    assert store.claim(now=NOW) is None
    result = store.delivery(row.id)
    assert result.delivery.state == "cancelled" and result.delivery.attempt_count == 0
    assert result.delivery.code == "notification_no_longer_eligible"
    assert result.attempts == []


def test_test_delivery_is_explicit_allowed_when_disabled_and_never_sent_by_save_or_preview(store):
    configure(store, enabled=False)
    store.preview(1, now=NOW)
    assert store.scan(now=NOW) == 0
    assert store.list_deliveries().deliveries == []
    result, request = enqueue(store)
    assert result.delivery.state == "queued" and result.attempts == []
    claim = store.claim(now=NOW)
    assert claim.delivery_id == result.delivery.id and claim.text == TEST_MESSAGE
    assert TOKEN.get_secret_value() not in repr(claim)
    with store.inventory._session() as session:
        assert session.get(NotificationDeliveryModel, str(claim.delivery_id)).state == "sending"
        assert session.get(NotificationAttemptModel, str(claim.attempt_id)).state == "sending"
    assert store.request_delivery(request.request_id).last_attempt_id == claim.attempt_id


def test_test_requests_are_idempotent_even_after_config_changes_and_have_exact_lookup(store):
    configure(store, enabled=False)
    original, request = enqueue(store)
    configure(store, chat_id=OTHER_CHAT, enabled=False)
    assert store.enqueue_test(request, now=NOW).delivery.id == original.delivery.id
    expect_code("notification_request_conflict", lambda: store.enqueue_test(
        NotificationTestRequest(expected_revision=2, request_id=request.request_id), now=NOW
    ))
    for _ in range(51):
        enqueue(store, now=NOW + timedelta(seconds=1))
    assert len(store.list_deliveries().deliveries) == 50
    assert original.delivery.id not in {row.id for row in store.list_deliveries().deliveries}
    assert store.request_delivery(request.request_id).id == original.delivery.id
    expect_code("notification_request_not_found", lambda: store.request_delivery(uuid4()))
    expect_code("notification_not_found", lambda: store.delivery(uuid4()))


def test_same_uuid_concurrent_enqueue_commits_only_one_delivery(store):
    configure(store)
    other = second_store(store)
    payload = NotificationTestRequest(expected_revision=1, request_id=uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: item.enqueue_test(payload, now=NOW), [store, other]))
    assert results[0].delivery.id == results[1].delivery.id
    with store.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryModel)) == 1
        assert session.scalar(select(func.count()).select_from(NotificationRequestModel)) == 1
    other.inventory._engine.dispose()


def test_concurrent_claims_across_store_connections_have_single_committed_inflight(store):
    configure(store)
    for _ in range(3):
        enqueue(store)
    other = second_store(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: item.claim(now=NOW), [store, other]))
    claims = [item for item in results if item is not None]
    assert len(claims) == 1
    with store.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationAttemptModel)) == 1
        throttle = session.get(NotificationChatModel, CHAT)
        assert throttle.in_flight_attempt_id == str(claims[0].attempt_id)
    assert store.claim(now=NOW + timedelta(seconds=10)) is None
    other.inventory._engine.dispose()


def test_persistent_throttle_spaces_finished_sends_and_rate_limit_applies_whole_chat(store):
    configure(store)
    enqueue(store)
    enqueue(store)
    claim = store.claim(now=NOW)
    store.finish(claim, Receipt("accepted", "telegram_accepted", message_id=1), now=NOW)
    other = second_store(store)
    assert other.claim(now=NOW + CHAT_INTERVAL - timedelta(microseconds=1)) is None
    second = other.claim(now=NOW + CHAT_INTERVAL)
    assert second is not None
    outcome = Receipt("failed", "telegram_rate_limited", retry_after=17, retryable=True)
    assert other.finish(second, outcome, now=NOW + CHAT_INTERVAL).state == "queued"
    enqueue(store, now=NOW + CHAT_INTERVAL)
    assert store.claim(now=NOW + CHAT_INTERVAL + timedelta(seconds=16.999)) is None
    assert store.claim(now=NOW + CHAT_INTERVAL + timedelta(seconds=17)) is not None
    other.inventory._engine.dispose()


def test_changed_destination_reroutes_only_unsent_attempts(store):
    configure(store)
    first, _ = enqueue(store)
    first_claim = store.claim(now=NOW)
    second, _ = enqueue(store)
    configure(store, chat_id=OTHER_CHAT, token_action="replace", token=OTHER_TOKEN)
    assert store.delivery(first.delivery.id).attempts[0].chat_id == CHAT
    assert store.delivery(first.delivery.id).delivery.chat_id == CHAT
    assert store.delivery(second.delivery.id).delivery.chat_id == OTHER_CHAT
    claim = store.claim(now=NOW)
    assert claim.delivery_id == second.delivery.id
    assert claim.chat_id == OTHER_CHAT and claim.token == OTHER_TOKEN
    assert first_claim.chat_id == CHAT and first_claim.token == TOKEN
    assert store.delivery(first.delivery.id).attempts[0].destination_revision == 1
    assert store.delivery(second.delivery.id).attempts[0].destination_revision == 2


def test_unknown_waits_for_deadline_requires_risk_ack_config_cas_and_attempt_fence(store):
    identifier, _request, claim = unknown_test(store)
    expect_code("notification_retry_too_early", lambda: store.retry(
        identifier, retry_payload(store, claim), now=NOW + timedelta(seconds=39)
    ))
    expect_code("notification_duplicate_risk_required", lambda: store.retry(
        identifier, retry_payload(store, claim, confirm_duplicate_risk=False),
        now=NOW + timedelta(seconds=41),
    ))
    stale = retry_payload(store, claim)
    configure(store, enabled=False, chat_id=OTHER_CHAT)
    expect_code("notification_revision_conflict", lambda: store.retry(
        identifier, stale, now=NOW + timedelta(seconds=41)
    ))
    expect_code("notification_attempt_conflict", lambda: store.retry(
        identifier, retry_payload(store, claim, expected_attempt_id=uuid4()),
        now=NOW + timedelta(seconds=41),
    ))
    payload = retry_payload(store, claim)
    result = store.retry(identifier, payload, now=NOW + timedelta(seconds=41))
    assert result.delivery.state == "queued" and result.delivery.chat_id == OTHER_CHAT
    assert result.attempts[0].chat_id == CHAT
    retried = store.claim(now=NOW + timedelta(seconds=41))
    assert retried.attempt_id != claim.attempt_id and retried.chat_id == OTHER_CHAT
    expect_code("notification_attempt_conflict", lambda: store.retry(
        identifier, retry_payload(store, claim), now=NOW + timedelta(seconds=100)
    ))


def test_recovery_of_crash_before_send_or_missing_receipt_never_automatically_replays(store):
    configure(store)
    seed(store)
    assert store.scan(now=NOW) == 1
    claim = store.claim(now=NOW)
    other = second_store(store)
    assert other.recover(now=NOW + timedelta(seconds=39)) == 0
    assert other.recover(now=claim.deadline_at) == 1
    assert other.recover(now=claim.deadline_at) == 0
    result = other.delivery(claim.delivery_id)
    assert result.delivery.state == "unknown"
    assert result.delivery.code == "notification_attempt_expired"
    assert result.attempts[0].state == "unknown"
    assert other.scan(now=NOW + timedelta(hours=1)) == 0
    assert other.claim(now=NOW + timedelta(hours=1)) is None
    other.inventory._engine.dispose()


def test_late_receipt_updates_only_old_attempt_never_overwrites_newer_attempt(store):
    configure(store, enabled=False)
    identifier = enqueue(store)[0].delivery.id
    old = store.claim(now=NOW)
    store.recover(now=old.deadline_at)
    payload = retry_payload(store, old)
    store.retry(identifier, payload, now=NOW + timedelta(seconds=41))
    newer = store.claim(now=NOW + timedelta(seconds=41))
    latest = store.finish(
        old, Receipt("accepted", "telegram_accepted", message_id=91),
        now=NOW + timedelta(seconds=42),
    )
    assert latest.state == "sending" and latest.last_attempt_id == newer.attempt_id
    detail = store.delivery(identifier)
    assert detail.attempts[0].state == "accepted" and detail.attempts[0].message_id == 91
    assert detail.attempts[0].late_receipt_at == NOW + timedelta(seconds=42)
    assert detail.attempts[1].state == "sending" and detail.attempts[1].message_id is None
    result = store.finish(
        newer, Receipt("unknown", "telegram_connection_lost"), now=NOW + timedelta(seconds=43)
    )
    assert result.state == "unknown" and not result.manual_retry_allowed
    expect_code("notification_retry_not_allowed", lambda: store.retry(
        identifier, retry_payload(store, newer), now=NOW + timedelta(seconds=100)
    ))


def test_late_accept_before_retry_claim_cancels_duplicate_queued_work(store):
    configure(store)
    identifier = enqueue(store)[0].delivery.id
    old = store.claim(now=NOW)
    store.recover(now=old.deadline_at)
    store.retry(identifier, retry_payload(store, old), now=NOW + timedelta(seconds=41))
    store.finish(
        old, Receipt("accepted", "telegram_accepted", message_id=17),
        now=NOW + timedelta(seconds=42),
    )
    assert store.claim(now=NOW + timedelta(seconds=46)) is None
    detail = store.delivery(identifier)
    assert detail.delivery.state == "cancelled"
    assert detail.delivery.code == "notification_already_accepted"
    assert len(detail.attempts) == 1 and detail.attempts[0].state == "accepted"


def test_retry_uuid_is_idempotent_and_original_request_remains_queryable(store):
    identifier, request, claim = unknown_test(store)
    payload = retry_payload(store, claim)
    first = store.retry(identifier, payload, now=NOW + timedelta(seconds=41))
    second = store.retry(identifier, payload, now=NOW + timedelta(seconds=42))
    assert first.delivery.id == second.delivery.id == identifier
    assert store.request_delivery(request.request_id).request_id == request.request_id
    assert store.request_delivery(payload.request_id).request_id == payload.request_id
    assert store.enqueue_test(request, now=NOW).delivery.request_id == request.request_id
    expect_code("notification_request_conflict", lambda: store.retry(
        identifier, payload.model_copy(update={"confirm_duplicate_risk": False}),
        now=NOW + timedelta(seconds=42),
    ))
    with store.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationRequestModel)) == 2
        assert session.scalar(select(func.count()).select_from(NotificationAttemptModel)) == 1


@pytest.mark.parametrize("code", [
    "telegram_connect_timeout", "telegram_connect_failed", "telegram_rate_limited",
    "notification_claim_expired",
])
def test_only_proven_safe_failures_retry_and_automatic_attempts_are_bounded(store, code):
    configure(store)
    identifier = enqueue(store)[0].delivery.id
    for index in range(3):
        now = NOW + timedelta(seconds=20 * index)
        claim = store.claim(now=now)
        assert claim is not None
        result = store.finish(claim, Receipt(
            "failed", code, retryable=True,
            retry_after=5 if code == "telegram_rate_limited" else None
        ), now=now)
        assert result.attempt_count == index + 1
        assert result.state == ("queued" if index < 2 else "failed")
    assert store.claim(now=NOW + timedelta(minutes=10)) is None
    assert store.delivery(identifier).delivery.attempt_count == 3


@pytest.mark.parametrize("state,code", [
    ("failed", "telegram_bad_request"), ("failed", "telegram_unauthorized"),
    ("failed", "telegram_forbidden"), ("failed", "telegram_tls_failed"),
    ("unknown", "telegram_server_error"), ("unknown", "telegram_response_timeout"),
    ("unknown", "telegram_invalid_response"), ("unknown", "notification_worker_interrupted"),
    ("unknown", "notification_transport_failure"),
])
def test_nonretryable_or_unknown_result_is_not_requeued_by_minute_scans(store, state, code):
    configure(store)
    seed(store)
    store.scan(now=NOW)
    claim = store.claim(now=NOW)
    result = store.finish(claim, Receipt(state, code, retryable=True), now=NOW)
    assert result.state == state and result.next_attempt_at is None
    for index in range(1, 4):
        assert store.scan(now=NOW + timedelta(minutes=index)) == 0
        assert store.claim(now=NOW + timedelta(minutes=index)) is None
    assert len(store.delivery(claim.delivery_id).attempts) == 1


def test_unknown_holds_chat_until_deadline_and_is_never_auto_retried(store):
    _identifier, _request, claim = unknown_test(store)
    another = enqueue(store)[0].delivery.id
    assert store.claim(now=NOW + timedelta(seconds=39)) is None
    next_claim = store.claim(now=claim.deadline_at)
    assert next_claim.delivery_id == another
    assert next_claim.delivery_id != claim.delivery_id


@pytest.mark.parametrize("outcome", [
    Receipt("accepted", "telegram_accepted", message_id=True),
    Receipt("accepted", "telegram_accepted", message_id=2**100),
    Receipt("accepted", "provider-secret-token-DO-NOT-STORE", message_id=1),
    Receipt("failed", "provider-secret-token-DO-NOT-STORE", retryable=True),
    Receipt("failed", "telegram_rate_limited", retry_after=True, retryable=True),
    Receipt("failed", "telegram_rate_limited", retry_after=86401, retryable=True),
])
def test_invalid_receipts_fail_closed_without_storing_raw_descriptions(store, outcome):
    configure(store)
    enqueue(store)
    claim = store.claim(now=NOW)
    result = store.finish(claim, outcome, now=NOW)
    assert result.state == "unknown" and result.next_attempt_at is None
    assert "provider-secret-token" not in store.delivery(claim.delivery_id).model_dump_json()
    assert b"provider-secret-token" not in Path(store.inventory._engine.url.database).read_bytes()


def test_real_receipt_is_terminal_even_if_finish_is_replayed_with_another_result(store):
    configure(store)
    enqueue(store)
    claim = store.claim(now=NOW)
    accepted = store.finish(claim, Receipt("accepted", "telegram_accepted", message_id=87), now=NOW)
    repeated = store.finish(
        claim, Receipt("unknown", "telegram_response_timeout"), now=NOW + timedelta(seconds=1)
    )
    assert accepted.state == repeated.state == "accepted" and repeated.message_id == 87
    assert len(store.delivery(claim.delivery_id).attempts) == 1


def test_notification_flow_never_modifies_inventory_or_queues_agent_commands(store, monkeypatch):
    configure(store)
    seed(store)

    def forbidden(*args, **kwargs):
        raise AssertionError("Notifications must never change a subscriber plan")

    monkeypatch.setattr(store.inventory, "assign_subscription_plan", forbidden)

    def snapshot():
        with store.inventory._engine.connect() as connection:
            names = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )).scalars()
            return {
                name: connection.execute(text('SELECT * FROM "' + name + '"')).all()
                for name in names if not name.startswith("notification_")
            }

    before = snapshot()
    store.preview(1, now=NOW)
    store.scan(now=NOW)
    claim = store.claim(now=NOW)
    store.finish(claim, Receipt("accepted", "telegram_accepted", message_id=1), now=NOW)
    assert snapshot() == before


@pytest.mark.parametrize("changes", [
    {"enabled": False, "chat_id": ""},
    {"enabled": False, "token_action": "clear"},
    {"enabled": False, "chat_id": "", "token_action": "clear"},
])
def test_cleared_destination_cancels_all_unsent_work_and_never_replays_explicit_test(
    store, changes
):
    configure(store)
    seed(store)
    store.scan(now=NOW)
    test, _request = enqueue(store)
    configure(store, **changes)
    rows = store.list_deliveries().deliveries
    assert len(rows) == 2
    assert all(row.state == "cancelled" and row.chat_id == CHAT for row in rows)
    assert all(
        row.code == "notification_not_configured" and row.attempt_count == 0 for row in rows
    )
    assert store.claim(now=NOW) is None
    configure(store)
    assert store.scan(now=NOW) == 1  # Only a fresh eligible package scan may restore unsent work.
    claim = store.claim(now=NOW)
    assert claim.delivery_id != test.delivery.id
    assert store.delivery(test.delivery.id).delivery.state == "cancelled"


def test_disabling_reminders_preserves_explicit_test_with_complete_destination(store):
    configure(store)
    seed(store)
    store.scan(now=NOW)
    test, _request = enqueue(store)
    configure(store, enabled=False)
    claim = store.claim(now=NOW)
    assert claim.delivery_id == test.delivery.id
    rows = store.list_deliveries().deliveries
    assert [row.state for row in rows if row.kind == "package_expiry"] == ["cancelled"]


def test_manual_retry_eligibility_is_current_and_get_does_not_update_saved_plan_label(store):
    configure(store)
    past = (datetime.now(UTC) - timedelta(days=1)).replace(hour=10, minute=0, second=0)
    plan_id = seed(store, expires_at=datetime.now(UTC) + timedelta(days=2))
    store.scan(now=past)
    claim = store.claim(now=past)
    store.finish(claim, Receipt("failed", "telegram_bad_request"), now=past)
    with store.inventory._session() as session:
        session.get(SubscriptionPlanModel, plan_id).name = "New plan label"
        session.commit()
    current = store.delivery(claim.delivery_id).delivery
    assert current.manual_retry_allowed
    assert current.plan_name == "Plan alice"
    with store.inventory._session() as session:
        saved = session.get(NotificationDeliveryModel, str(claim.delivery_id))
        assert saved.plan_name == "Plan alice"
    change_user(store, is_active=False)
    assert not store.delivery(claim.delivery_id).delivery.manual_retry_allowed
    expect_code("notification_no_longer_eligible", lambda: store.retry(
        claim.delivery_id, retry_payload(store, claim), now=datetime.now(UTC)
    ))


def test_concurrent_manual_retries_with_different_request_ids_queue_only_once(store):
    identifier, _request, claim = unknown_test(store)
    other = second_store(store)
    payloads = [retry_payload(store, claim), retry_payload(store, claim)]

    def attempt(pair):
        target, payload = pair
        try:
            return target.retry(identifier, payload, now=NOW + timedelta(seconds=41)).delivery.state
        except NotificationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, zip([store, other], payloads, strict=True)))
    assert sorted(results) == ["notification_retry_not_allowed", "queued"]
    with store.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationRequestModel)) == 2
    other.inventory._engine.dispose()


def fail_next_commits(store, monkeypatch):
    original = store.inventory._session

    def broken_session():
        session = original()

        def commit():
            raise OperationalError(None, None, RuntimeError("private-failure-never-echo"))

        session.commit = commit
        return session

    monkeypatch.setattr(store.inventory, "_session", broken_session)


def test_claim_is_not_returned_if_sending_commit_fails(store, monkeypatch):
    configure(store)
    identifier = enqueue(store)[0].delivery.id
    with monkeypatch.context() as patch:
        fail_next_commits(store, patch)
        error = expect_code("notification_database_unavailable", lambda: store.claim(now=NOW))
        assert "private-failure" not in str(error)
    saved = store.delivery(identifier)
    assert saved.delivery.state == "queued" and saved.delivery.attempt_count == 0
    assert saved.attempts == []
    assert store.claim(now=NOW).delivery_id == identifier


def test_missing_commit_after_remote_acceptance_recovers_unknown_without_replay(store, monkeypatch):
    configure(store)
    enqueue(store)
    claim = store.claim(now=NOW)
    remote_receipt = Receipt("accepted", "telegram_accepted", message_id=909)
    with monkeypatch.context() as patch:
        fail_next_commits(store, patch)
        expect_code("notification_database_unavailable", lambda: store.finish(
            claim, remote_receipt, now=NOW + timedelta(seconds=1)
        ))
    assert store.delivery(claim.delivery_id).delivery.state == "sending"
    other = second_store(store)
    assert other.recover(now=claim.deadline_at) == 1
    assert other.claim(now=claim.deadline_at + timedelta(seconds=1)) is None
    detail = other.delivery(claim.delivery_id)
    assert detail.delivery.state == "unknown" and detail.delivery.message_id is None
    assert len(detail.attempts) == 1
    other.inventory._engine.dispose()


def test_request_and_delivery_are_atomic_when_enqueue_commit_fails(store, monkeypatch):
    configure(store)
    payload = NotificationTestRequest(expected_revision=1, request_id=uuid4())
    with monkeypatch.context() as patch:
        fail_next_commits(store, patch)
        expect_code(
            "notification_database_unavailable", lambda: store.enqueue_test(payload, now=NOW)
        )
    assert store.list_deliveries().deliveries == []
    expect_code(
        "notification_request_not_found", lambda: store.request_delivery(payload.request_id)
    )
    assert store.enqueue_test(payload, now=NOW).delivery.state == "queued"


def test_partial_initialization_keeps_key_marker_and_never_silently_rekeys(store, monkeypatch):
    with monkeypatch.context() as patch:
        fail_next_commits(store, patch)
        expect_code("notification_database_unavailable", lambda: configure(store))
    assert store.get_settings().revision == 0 and not store.get_settings().has_token
    key = store.state_dir / "telegram.key"
    original = key.read_bytes()
    assert (store.state_dir / "telegram.initialized").exists()
    key.write_bytes(Fernet.generate_key())
    expect_code("notification_storage_key_invalid", lambda: configure(store))
    key.write_bytes(original)
    assert configure(store).revision == 1
    assert key.read_bytes() == original


def test_first_clear_is_read_only_for_vault_and_creates_no_key_or_marker(store):
    result = store.update_settings(NotificationSettingsUpdate(
        expected_revision=0, enabled=False, chat_id="", token_action="clear"
    ), now=NOW)
    assert result.revision == 1 and not result.has_token and result.storage_ready
    assert not store.state_dir.exists()


def test_clear_requires_initialized_key_even_when_no_ciphertext_remains(store):
    configure(store)
    cleared = configure(store, enabled=False, token_action="clear")
    assert not cleared.has_token
    key = store.state_dir / "telegram.key"
    key.unlink()
    payload = NotificationSettingsUpdate(
        expected_revision=cleared.revision, enabled=False, chat_id=CHAT, token_action="clear"
    )
    expect_code("notification_storage_key_missing", lambda: store.update_settings(payload, now=NOW))
    assert not key.exists()
    assert store.get_settings().revision == cleared.revision
