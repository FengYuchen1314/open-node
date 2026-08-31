"""Fresh administrator proof and read-only checks for short-lived backup jobs."""

from dataclasses import dataclass, field
from hashlib import sha256
from secrets import compare_digest
from time import time

from open_node.services.auth import (
    Administrator,
    AdministratorBackupEpoch,
    AuthStore,
    OperatorSession,
    password_hash,
)

BACKUP_AUTHORIZATION_SECONDS = 900


class BackupAuthorizationError(ValueError):
    def __init__(self, code: str = "backup_authorization_expired", status_code: int = 403):
        super().__init__("Backup authorization is unavailable.")
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class BackupAuthorization:
    session_hash: str = field(repr=False)
    security_epoch: str = field(repr=False)
    expires_at: float


def backup_session_hash(token: str | None) -> str:
    if not isinstance(token, str) or not 1 <= len(token) <= 128:
        raise BackupAuthorizationError()
    return sha256(token.encode()).hexdigest()


class BackupAuthorizer:
    def __init__(self, store: AuthStore, idle_seconds: int) -> None:
        self.store = store
        self.idle_seconds = idle_seconds

    def issue(self, token: str, password: str, code: str = "") -> BackupAuthorization:
        """Called under the ordinary request write lease, never under snapshot EX.

        The persistent account-wide rate budget cannot be reset by switching
        session or peer. Only the returned hashes/expiry reach the job queue.
        Existing TOTP/recovery codes are verified and consumed transactionally.
        """
        owner = backup_session_hash(token)
        if not self.store.allow_login_attempt("administrator:backup", max_attempts=5):
            raise BackupAuthorizationError("backup_rate_limited", 429)
        try:
            with self.store._coordinated_session() as db:
                now = time()
                administrator = db.get(Administrator, 1)
                session = db.get(OperatorSession, owner)
                factor = self.store._factor_row(db)
                policy = self.store._policy(db)
                if (
                    not administrator or not session
                    or session.expires_at <= now
                    or session.last_seen_at <= now - self.idle_seconds
                    or not isinstance(password, str) or not 1 <= len(password) <= 1024
                    or not isinstance(code, str) or len(code) > 64
                    or not password_hash.verify(password, administrator.password_hash)
                    or (policy and policy.require_totp and not (factor and factor.totp_secret))
                    or (factor and factor.totp_secret
                        and not self.store._verify_factor(administrator, factor, code))
                ):
                    raise BackupAuthorizationError()
                epoch = db.get(AdministratorBackupEpoch, 1)
                if epoch is None:
                    self.store._advance_backup_epoch(db)
                    db.flush()
                    epoch = db.get(AdministratorBackupEpoch, 1)
                result = BackupAuthorization(
                    session_hash=owner,
                    security_epoch=epoch.value,
                    expires_at=min(session.expires_at, now + BACKUP_AUTHORIZATION_SECONDS),
                )
                db.commit()
                return result
        except BackupAuthorizationError:
            raise
        except Exception:
            raise BackupAuthorizationError() from None

    def is_authorized(self, authorization: BackupAuthorization) -> bool:
        """No last-seen update, key initialization, or other database mutation.

        Check again before creation/publication and each download chunk. Once
        sent, bytes cannot be recalled; revocation stops subsequent reads.
        """
        if type(authorization) is not BackupAuthorization or authorization.expires_at <= time():
            return False
        try:
            with self.store.session() as db:
                now = time()
                session = db.get(OperatorSession, authorization.session_hash)
                epoch = db.get(AdministratorBackupEpoch, 1)
                administrator = db.get(Administrator, 1)
                factor = self.store._factor_row(db)
                policy = self.store._policy(db)
                return bool(
                    session and epoch and administrator
                    and session.expires_at > now
                    and session.last_seen_at > now - self.idle_seconds
                    and compare_digest(epoch.value, authorization.security_epoch)
                    and not (policy and policy.require_totp and not (factor and factor.totp_secret))
                )
        except Exception:
            return False
