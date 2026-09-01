"""Local issuance and atomic SQLite first-run administrator/branding initialization."""

from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from time import time

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from open_node.domain.branding import BRANDING_MAX_REVISION, BrandingError
from open_node.domain.initial_setup import InitialSetupError, InitialSetupStatus
from open_node.services.auth import (
    Administrator,
    AdministratorBackupEpoch,
    AdministratorProfile,
    InitialSetupTicket,
    OperatorChallenge,
    OperatorSession,
    password_hash,
)
from open_node.services.branding import BrandingSettingsModel, BrandingStore

SETUP_LIFETIME_SECONDS = 1800


class InitialSetupStore:
    def __init__(self, auth, *, clock=time):
        self.auth = auth
        self.clock = clock

    @staticmethod
    def _digest(value):
        return sha256(b"open-node/initial-setup/v1\0" + value.encode()).hexdigest()

    @staticmethod
    def _completed(db):
        # A deleted administrator must not turn an old installation back into
        # a publicly claimable first-run instance. The permanent epoch remains.
        ticket = db.get(InitialSetupTicket, 1)
        return bool(
            db.scalar(select(Administrator.id).limit(1)) is not None
            or db.get(AdministratorBackupEpoch, 1) is not None
            or (ticket and ticket.completed_at is not None)
        )

    def _supported(self):
        if self.auth.engine.dialect.name not in {"sqlite", "postgresql"}:
            raise InitialSetupError(503, "setup_unavailable")

    @contextmanager
    def _session(self, *, write=False):
        self._supported()
        try:
            with (self.auth._coordinated_session() if write else self.auth.session()) as db:
                if not write:
                    yield db
                    return
                connection = db.connection()
                raw = connection.connection.dbapi_connection
                try:
                    yield db
                except BaseException:
                    try:
                        raw.rollback()
                    except Exception:
                        connection.invalidate()
                    raise
        except (SQLAlchemyError, BrandingError):
            raise InitialSetupError(503, "setup_unavailable") from None

    def status(self):
        with self._session() as db:
            configured = self._completed(db)
            ticket = db.get(InitialSetupTicket, 1)
            available = bool(
                not configured and ticket and ticket.token_hash and ticket.expires_at > self.clock()
            )
            return InitialSetupStatus(
                configured=configured, available=available,
                expires_at=datetime.fromtimestamp(ticket.expires_at, UTC) if available else None,
            )

    def issue(self):
        """Only a local CLI calls this; there is intentionally no HTTP issuance API."""
        token = token_urlsafe(32)
        with self._session(write=True) as db:
            if self._completed(db):
                raise InitialSetupError(409, "setup_already_completed")
            ticket = db.get(InitialSetupTicket, 1)
            if ticket is None:
                ticket = InitialSetupTicket(id=1)
                db.add(ticket)
            ticket.token_hash = self._digest(token)
            ticket.expires_at = self.clock() + SETUP_LIFETIME_SECONDS
            expires_at = datetime.fromtimestamp(ticket.expires_at, UTC)
            db.commit()
        return token, expires_at

    def authorize_restore(self, token: str) -> str:
        """Validate without consuming the one-use setup credential; bind an upload owner."""
        with self._session() as db:
            self._authorize(db, token)
        return self._digest(token)

    def _authorize(self, db, token):
        if self._completed(db):
            raise InitialSetupError(409, "setup_already_completed")
        ticket = db.get(InitialSetupTicket, 1)
        matched = compare_digest(
            self._digest(token), ticket.token_hash if ticket and ticket.token_hash else "0" * 64,
        )
        if not matched or ticket is None or ticket.expires_at <= self.clock():
            raise InitialSetupError(403, "setup_ticket_invalid")
        return ticket

    def complete(self, payload):
        token = payload.setup_token.get_secret_value()
        # Do not perform an expensive password hash for unproven remote input.
        with self._session() as db:
            self._authorize(db, token)
        hashed = password_hash.hash(payload.password.get_secret_value())
        with self._session(write=True) as db:
            ticket = self._authorize(db, token)
            saved = BrandingStore._settings(db)
            if saved.revision >= BRANDING_MAX_REVISION:
                raise InitialSetupError(503, "setup_unavailable")
            branding = db.get(BrandingSettingsModel, 1)
            branding.site_title = payload.site_title
            branding.brand_title = payload.brand_title
            branding.revision += 1
            db.add(Administrator(id=1, username=payload.username, password_hash=hashed))
            db.add(AdministratorProfile(
                administrator_id=1,
                email=payload.email,
                nickname=payload.nickname or payload.username,
                avatar_url=payload.avatar_url,
                revision=0,
            ))
            self.auth._advance_backup_epoch(db)
            db.execute(delete(OperatorSession))
            db.execute(delete(OperatorChallenge))
            ticket.token_hash = None
            ticket.expires_at = 0
            ticket.completed_at = self.clock()
            db.commit()
