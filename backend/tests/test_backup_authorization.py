from dataclasses import replace

import pyotp
import pytest
from cryptography.fernet import Fernet
from open_node.services import backup_authorization as module
from open_node.services.auth import (
    AdministratorBackupEpoch,
    AdministratorFactor,
    AuthStore,
    OperatorSession,
)
from open_node.services.backup_authorization import BackupAuthorizationError, BackupAuthorizer
from sqlalchemy import event

PASSWORD = "backup-test-password-only"


@pytest.fixture
def instance(tmp_path):
    store = AuthStore(f"sqlite:///{tmp_path / 'auth.db'}", Fernet.generate_key())
    store.set_administrator("admin", PASSWORD)
    token = store.login("admin", PASSWORD, 3600).token
    try:
        yield store, token, BackupAuthorizer(store, idle_seconds=1800)
    finally:
        store.engine.dispose()


def test_fresh_password_returns_only_bound_short_lived_authorization(instance):
    store, token, authorizer = instance
    grant = authorizer.issue(token, PASSWORD)
    assert authorizer.is_authorized(grant)
    assert grant.session_hash == store._digest(token)
    assert 890 < grant.expires_at - module.time() <= 900
    assert token not in repr(grant) and grant.security_epoch not in repr(grant)
    assert not hasattr(grant, "password") and not hasattr(grant, "code")


@pytest.mark.parametrize("kind", ["password", "session", "idle", "expiry"])
def test_invalid_or_stale_proof_is_rejected(instance, kind):
    store, token, authorizer = instance
    password = "incorrect" if kind == "password" else PASSWORD
    if kind == "session":
        token = "not-the-current-session"
    elif kind in {"idle", "expiry"}:
        with store.session.begin() as db:
            session = db.get(OperatorSession, store._digest(token))
            if kind == "idle":
                session.last_seen_at = module.time() - 1900
            else:
                session.expires_at = module.time() - 1
    with pytest.raises(BackupAuthorizationError) as error:
        authorizer.issue(token, password)
    assert str(error.value) == "Backup authorization is unavailable."
    assert error.value.status_code == 403


def test_download_checks_execute_only_reads_and_do_not_refresh_idle_time(instance):
    store, token, authorizer = instance
    grant = authorizer.issue(token, PASSWORD)
    with store.session() as db:
        before = db.get(OperatorSession, store._digest(token)).last_seen_at
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", observed)
    try:
        assert authorizer.is_authorized(grant)
    finally:
        event.remove(store.engine, "before_cursor_execute", observed)
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    with store.session() as db:
        assert db.get(OperatorSession, store._digest(token)).last_seen_at == before


@pytest.mark.parametrize("change", ["logout", "password", "reset", "totp-setup", "policy-revert"])
def test_security_changes_revoke_existing_grants_including_change_then_revert(instance, change):
    store, token, authorizer = instance
    grant = authorizer.issue(token, PASSWORD)
    if change == "logout":
        store.logout(token)
    elif change == "password":
        assert store.change_password(PASSWORD, PASSWORD + "-new")
    elif change == "reset":
        store.set_administrator("admin", PASSWORD, reset=True)
    elif change == "totp-setup":
        store.begin_totp(token, PASSWORD)
    else:
        # Even saving the same policy must revoke a previously approved grant.
        store.update_policy(token, PASSWORD, "", False)
        store.update_policy(token, PASSWORD, "", False)
        assert store.authenticate(token, 1800) is not None
    assert not authorizer.is_authorized(grant)


def test_enabled_totp_requires_a_fresh_non_replayed_code_and_accepts_one_recovery_code(instance):
    store, token, authorizer = instance
    enrollment = store.begin_totp(token, PASSWORD)
    code = pyotp.TOTP(enrollment.secret).now()
    recovery = store.confirm_totp(token, code)
    with pytest.raises(BackupAuthorizationError):
        authorizer.issue(token, PASSWORD)
    with pytest.raises(BackupAuthorizationError):
        authorizer.issue(token, PASSWORD, code)
    grant = authorizer.issue(token, PASSWORD, recovery[0])
    assert authorizer.is_authorized(grant)
    with pytest.raises(BackupAuthorizationError):
        authorizer.issue(token, PASSWORD, recovery[0])
    with store.session() as db:
        assert len(db.get(AdministratorFactor, 1).recovery_hashes) == 9


def test_expiry_wrong_epoch_and_deleted_epoch_fail_closed(instance, monkeypatch):
    store, _token, authorizer = instance
    grant = authorizer.issue(_token, PASSWORD)
    assert not authorizer.is_authorized(replace(grant, security_epoch="0" * 64))
    assert not authorizer.is_authorized(replace(grant, expires_at=0))
    assert not authorizer.is_authorized(None)
    with store.session.begin() as db:
        db.delete(db.get(AdministratorBackupEpoch, 1))
    assert not authorizer.is_authorized(grant)
    monkeypatch.setattr(store, "session", lambda: (_ for _ in ()).throw(RuntimeError("private")))
    assert not authorizer.is_authorized(grant)


def test_account_wide_proof_rate_limit_survives_a_new_authorizer_and_session(instance):
    store, token, authorizer = instance
    for _ in range(5):
        with pytest.raises(BackupAuthorizationError) as error:
            authorizer.issue(token, "wrong")
        assert error.value.status_code == 403
    other = store.login("admin", PASSWORD, 3600).token
    with pytest.raises(BackupAuthorizationError) as error:
        BackupAuthorizer(store, 1800).issue(other, PASSWORD)
    assert (error.value.code, error.value.status_code) == ("backup_rate_limited", 429)
