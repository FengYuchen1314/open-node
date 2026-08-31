"""Durable, fenced expiry notifications; this module never performs network I/O.

The administrator's Telegram token has a separate purpose-bound vault.  A committed
``sending`` attempt is never replayed after recovery: the remote service offers no
idempotency key, so a missing receipt is an unknown result, not evidence of failure.
"""

import hashlib
import json
import os
import stat
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.notifications import (
    ClaimedNotification,
    NotificationAttemptRead,
    NotificationCandidate,
    NotificationDeliveriesResponse,
    NotificationDeliveryDetail,
    NotificationDeliveryRead,
    NotificationError,
    NotificationOutcome,
    NotificationPreviewRead,
    NotificationRetryRequest,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    NotificationTestRequest,
    validate_bot_token,
)
from open_node.services.inventory import InventoryStore, ProductUserModel, SubscriptionPlanModel

CHAT_INTERVAL = timedelta(seconds=3.1)
MAX_AUTOMATIC_ATTEMPTS = 3
TEST_MESSAGE = "Open Node 通知测试\n这是管理员主动发送的测试消息，不会修改套餐或订阅。"
VAULT_PURPOSE = "open-node.notifications.telegram.v1"

# Only fixed codes cross the transport/storage boundary. Unknown values never reach
# the database or a response, even when a third-party exception is accidentally used.
OUTCOME_CODES = frozenset(
    {
        "telegram_accepted",
        "telegram_invalid_token",
        "telegram_invalid_chat_id",
        "telegram_invalid_text",
        "telegram_tls_failed",
        "telegram_bad_request",
        "telegram_unauthorized",
        "telegram_forbidden",
        "telegram_rejected",
        "telegram_connect_timeout",
        "telegram_connect_failed",
        "telegram_rate_limited",
        "telegram_send_timeout",
        "telegram_response_timeout",
        "telegram_connection_lost",
        "telegram_redirect_blocked",
        "telegram_server_error",
        "telegram_invalid_response",
        "telegram_response_too_large",
        "telegram_transport_failure",
        "notification_claim_expired",
        "notification_worker_interrupted",
        "notification_transport_failure",
        "notification_attempt_expired",
        "notification_invalid_response",
    }
)
SAFE_RETRY_CODES = frozenset({
    "notification_claim_expired", "telegram_connect_timeout", "telegram_connect_failed",
    "telegram_rate_limited",
})
FAILED_CODES = SAFE_RETRY_CODES | frozenset({
    "telegram_invalid_token", "telegram_invalid_chat_id", "telegram_invalid_text",
    "telegram_tls_failed", "telegram_bad_request", "telegram_unauthorized",
    "telegram_forbidden", "telegram_rejected",
})


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now(value: datetime | None = None) -> datetime:
    return _utc(value) if value is not None else datetime.now(UTC)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class NotificationBase(DeclarativeBase):
    """Separate metadata prevents imports from silently changing inventory migrations."""


class NotificationSettingsModel(NotificationBase):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(20), default="")
    advance_days: Mapped[int] = mapped_column(Integer, default=7)
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Shanghai")
    destination_revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryModel(NotificationBase):
    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), index=True)
    config_revision: Mapped[int] = mapped_column(Integer)
    destination_revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(20))
    # These are historical snapshots, not cascading inventory foreign keys.
    username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_once: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NotificationAttemptModel(NotificationBase):
    __tablename__ = "notification_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("notification_deliveries.id"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    config_revision: Mapped[int] = mapped_column(Integer)
    destination_revision: Mapped[int] = mapped_column(Integer)
    chat_id: Mapped[str] = mapped_column(String(20), index=True)
    message_text: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    late_receipt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationRequestModel(NotificationBase):
    __tablename__ = "notification_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(16))
    payload_digest: Mapped[str] = mapped_column(String(64))
    delivery_id: Mapped[str] = mapped_column(ForeignKey("notification_deliveries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationChatModel(NotificationBase):
    __tablename__ = "notification_chat_throttles"

    chat_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    in_flight_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _NotificationVault:
    def __init__(self, root: Path | None):
        self.root = root.absolute() if root is not None else None

    def _path(self, name: str = "", *, regular=False) -> Path:
        if self.root is None:
            raise NotificationError(503, "notification_storage_unavailable")
        path = self.root / name if name else self.root
        if ".." in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
            raise NotificationError(503, "notification_storage_permissions")
        for item in (self.root, path):
            if not item.exists():
                continue
            mode = item.stat()
            if mode.st_uid != os.geteuid() or mode.st_mode & 0o077:
                raise NotificationError(503, "notification_storage_permissions")
            if item == self.root and not stat.S_ISDIR(mode.st_mode):
                raise NotificationError(503, "notification_storage_permissions")
        if regular and path.exists():
            mode = path.stat()
            if not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1:
                raise NotificationError(503, "notification_storage_permissions")
        return path

    def _read(self, name: str, limit: int) -> bytes:
        path = self._path(name, regular=True)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            mode = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(mode.st_mode)
                or mode.st_uid != os.geteuid()
                or mode.st_mode & 0o077
                or mode.st_nlink != 1
            ):
                raise NotificationError(503, "notification_storage_permissions")
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise NotificationError(503, "notification_storage_key_invalid")
        return value

    def _write_new(self, name: str, value: bytes) -> None:
        path = self._path(name, regular=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def cipher(self, fingerprint: str | None, *, create=False) -> tuple[Fernet, str]:
        try:
            root = self._path()
            key_path = self._path("telegram.key", regular=True)
            marker_path = self._path("telegram.initialized", regular=True)
            if not key_path.exists():
                if fingerprint is not None or marker_path.exists():
                    raise NotificationError(503, "notification_storage_key_missing")
                if not create:
                    raise NotificationError(503, "notification_not_configured")
                root.mkdir(parents=True, mode=0o700, exist_ok=True)
                self._path()
                self._write_new("telegram.key", Fernet.generate_key())
            key = self._read("telegram.key", 128)
            actual = hashlib.sha256(VAULT_PURPOSE.encode() + b"\x00" + key).hexdigest()
            if fingerprint is not None and actual != fingerprint:
                raise NotificationError(503, "notification_storage_key_invalid")
            cipher = Fernet(key)
            marker = _canonical({"purpose": VAULT_PURPOSE, "key_fingerprint": actual})
            if marker_path.exists():
                if self._read("telegram.initialized", 512) != marker:
                    raise NotificationError(503, "notification_storage_key_invalid")
            elif fingerprint is not None:
                raise NotificationError(503, "notification_storage_key_missing")
            elif create:
                self._write_new("telegram.initialized", marker)
            return cipher, actual
        except NotificationError:
            raise
        except (OSError, ValueError, InvalidToken):
            raise NotificationError(503, "notification_storage_unavailable") from None

    def check(self, settings: NotificationSettingsModel) -> str | None:
        try:
            self._path()
            if settings.key_fingerprint or (self.root / "telegram.key").exists():
                self.cipher(settings.key_fingerprint)
            elif (self.root / "telegram.initialized").exists():
                raise NotificationError(503, "notification_storage_key_missing")
            if settings.token_ciphertext:
                self.open(settings)
        except NotificationError as exc:
            return exc.code
        except OSError:
            return "notification_storage_unavailable"
        return None

    def seal(self, value: SecretStr, settings: NotificationSettingsModel) -> tuple[str, str]:
        cipher, fingerprint = self.cipher(settings.key_fingerprint, create=True)
        payload = _canonical({"purpose": VAULT_PURPOSE, "token": value.get_secret_value()})
        return cipher.encrypt(payload).decode("ascii"), fingerprint

    def open(self, settings: NotificationSettingsModel) -> SecretStr:
        if not settings.token_ciphertext:
            raise NotificationError(422, "notification_not_configured")
        cipher, _fingerprint = self.cipher(settings.key_fingerprint)
        try:
            value = json.loads(cipher.decrypt(settings.token_ciphertext.encode("ascii")))
            if set(value) != {"purpose", "token"} or value["purpose"] != VAULT_PURPOSE:
                raise ValueError()
            return SecretStr(validate_bot_token(value["token"]))
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
            raise NotificationError(503, "notification_storage_key_invalid") from None


class NotificationStore:
    def __init__(self, inventory: InventoryStore, state_dir: Path | None):
        self.inventory = inventory
        self.state_dir = state_dir
        self._vault = _NotificationVault(state_dir)

    def _supported(self) -> None:
        if self.inventory._engine.dialect.name != "sqlite":
            raise NotificationError(503, "notification_database_unavailable")

    def create_schema(self) -> None:
        if self.inventory._engine.dialect.name != "sqlite":
            return
        NotificationBase.metadata.create_all(self.inventory._engine)
        with self._write() as session:
            if session.get(NotificationSettingsModel, 1) is None:
                session.add(self._defaults())

    @staticmethod
    def _defaults() -> NotificationSettingsModel:
        return NotificationSettingsModel(
            id=1, revision=0, enabled=False, chat_id="", advance_days=7,
            timezone="Asia/Shanghai", destination_revision=0, updated_at=_now(),
        )

    @contextmanager
    def _read(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                yield session
        except SQLAlchemyError:
            raise NotificationError(503, "notification_database_unavailable") from None

    @contextmanager
    def _write(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
        except IntegrityError:
            raise NotificationError(409, "notification_request_conflict") from None
        except SQLAlchemyError:
            raise NotificationError(503, "notification_database_unavailable") from None

    @staticmethod
    def _settings(session) -> NotificationSettingsModel:
        row = session.get(NotificationSettingsModel, 1)
        if row is None:
            raise NotificationError(503, "notification_database_unavailable")
        return row

    @staticmethod
    def _expected(settings, revision: int) -> None:
        if type(revision) is not int or settings.revision != revision:
            raise NotificationError(409, "notification_revision_conflict")

    def _settings_read(self, row, *, storage_error=None) -> NotificationSettingsRead:
        error = storage_error or self._vault.check(row)
        return NotificationSettingsRead(
            revision=row.revision, enabled=row.enabled, has_token=bool(row.token_ciphertext),
            chat_id=row.chat_id, advance_days=row.advance_days, timezone=row.timezone,
            destination_revision=row.destination_revision,
            storage_ready=error is None, storage_error=error,
        )

    def get_settings(self) -> NotificationSettingsRead:
        if self.inventory._engine.dialect.name != "sqlite":
            return self._settings_read(
                self._defaults(), storage_error="notification_database_unavailable"
            )
        with self._read() as session:
            return self._settings_read(self._settings(session))

    def update_settings(
        self, payload: NotificationSettingsUpdate, *, now: datetime | None = None
    ) -> NotificationSettingsRead:
        current = _now(now)
        with self._write() as session:
            row = self._settings(session)
            self._expected(row, payload.expected_revision)
            secret, fingerprint = row.token_ciphertext, row.key_fingerprint
            token_changed = False
            if payload.token_action == "replace":
                old = self._vault.open(row) if row.token_ciphertext else None
                token_changed = old != payload.token
                secret, fingerprint = self._vault.seal(payload.token, row)
            elif payload.token_action == "clear":
                error = self._vault.check(row)
                if error is not None:
                    # A broken vault must not erase recoverable ciphertext.  This
                    # is a read-only check, including a never-initialized vault.
                    raise NotificationError(503, error)
                token_changed = secret is not None
                secret = None
            if payload.enabled:
                if not secret or not payload.chat_id:
                    raise NotificationError(422, "notification_not_configured")
                if payload.token_action != "replace":
                    self._vault.open(row)
            destination_changed = token_changed or row.chat_id != payload.chat_id
            next_revision = row.revision + 1
            result = session.execute(
                update(NotificationSettingsModel)
                .where(NotificationSettingsModel.id == 1,
                       NotificationSettingsModel.revision == payload.expected_revision)
                .values(
                    revision=next_revision, enabled=payload.enabled,
                    chat_id=payload.chat_id, advance_days=payload.advance_days,
                    timezone=payload.timezone, token_ciphertext=secret,
                    key_fingerprint=fingerprint, updated_at=current,
                    destination_revision=row.destination_revision + int(destination_changed),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise NotificationError(409, "notification_revision_conflict")
            session.expire(row)
            session.refresh(row)
            for delivery in session.scalars(
                select(NotificationDeliveryModel).where(NotificationDeliveryModel.state == "queued")
            ):
                if not row.chat_id or not row.token_ciphertext:
                    # Clearing configuration is not authorization to preserve a latent
                    # explicit test. Keep the last meaningful target in its history.
                    self._cancel(delivery, "notification_not_configured", current)
                    continue
                delivery.config_revision = row.revision
                delivery.destination_revision = row.destination_revision
                delivery.chat_id = row.chat_id
                delivery.updated_at = current
                if delivery.kind == "package_expiry" and not row.enabled:
                    self._cancel(delivery, "notification_disabled", current)
            return self._settings_read(row)

    @staticmethod
    def _eligible_query(settings, now):
        deadline = (
            now.astimezone(ZoneInfo(settings.timezone)) + timedelta(days=settings.advance_days)
        ).astimezone(UTC)
        return (
            select(ProductUserModel, SubscriptionPlanModel)
            .join(
                SubscriptionPlanModel, ProductUserModel.current_plan_id == SubscriptionPlanModel.id
            )
            .where(
                ProductUserModel.is_active.is_(True), ProductUserModel.removal_id.is_(None),
                ProductUserModel.plan_expires_at > now,
                ProductUserModel.plan_expires_at <= deadline,
            )
            .order_by(ProductUserModel.plan_expires_at, ProductUserModel.username)
        )

    @staticmethod
    def _candidate(user, plan) -> NotificationCandidate:
        return NotificationCandidate(
            username=user.username, plan_id=UUID(plan.id), plan_name=plan.name,
            expires_at=_utc(user.plan_expires_at),
        )

    @staticmethod
    def _label(value: str) -> str:
        return "".join(
            " " if unicodedata.category(char).startswith("C") else char for char in value
        )[:120]

    @classmethod
    def _message(cls, candidate, timezone: str) -> str:
        expiry = candidate.expires_at.astimezone(ZoneInfo(timezone)).strftime(
            "%Y-%m-%d %H:%M:%S %z"
        )
        return (
            "套餐即将到期提醒\n"
            f"用户：{cls._label(candidate.username)}\n"
            f"套餐：{cls._label(candidate.plan_name)}\n"
            f"到期时间：{expiry}（{timezone}）\n"
            "此消息仅作提醒，不会自动续费或修改套餐。"
        )

    def preview(
        self, expected_revision: int, *, now: datetime | None = None
    ) -> NotificationPreviewRead:
        current = _now(now)
        with self._read() as session:
            row = self._settings(session)
            self._expected(row, expected_revision)
            candidates = [
                self._candidate(user, plan)
                for user, plan in session.execute(self._eligible_query(row, current))
            ]
            sample = candidates[0] if candidates else NotificationCandidate(
                username="示例用户", plan_id=UUID(int=0), plan_name="示例套餐",
                expires_at=current + timedelta(days=row.advance_days),
            )
            message = self._message(sample, row.timezone)
            if not candidates:
                message = "暂无符合条件的用户。以下仅为消息格式示例：\n" + message
            return NotificationPreviewRead(
                revision=row.revision, as_of=current, timezone=row.timezone,
                enabled=row.enabled, chat_id=row.chat_id, total=len(candidates),
                candidates=candidates[:20], sample_message=message, is_sample=not candidates,
            )

    @staticmethod
    def _event_key(user, plan) -> str:
        return _digest([
            "package_expiry", user.username,
            _utc(user.created_at).isoformat(timespec="microseconds"),
            plan.id, _utc(user.plan_expires_at).isoformat(timespec="microseconds"),
        ])

    @staticmethod
    def _configured(settings) -> None:
        if not settings.chat_id or not settings.token_ciphertext:
            raise NotificationError(422, "notification_not_configured")

    @staticmethod
    def _row(session, identifier) -> NotificationDeliveryModel:
        row = session.get(NotificationDeliveryModel, str(identifier))
        if row is None:
            raise NotificationError(404, "notification_not_found")
        return row

    def _existing_request(self, session, payload, action, identifier=None):
        digest = _digest({
            "action": action, "delivery_id": str(identifier) if identifier else None,
            "payload": payload.model_dump(mode="json"),
        })
        request = session.get(NotificationRequestModel, str(payload.request_id))
        if request is not None:
            if request.action != action or request.payload_digest != digest:
                raise NotificationError(409, "notification_request_conflict")
            return self._row(session, request.delivery_id), digest
        return None, digest

    @staticmethod
    def _save_request(session, payload, action, digest, row, now) -> None:
        session.flush()
        session.add(NotificationRequestModel(
            id=str(payload.request_id), action=action, payload_digest=digest,
            delivery_id=row.id, created_at=now,
        ))

    def enqueue_test(
        self, payload: NotificationTestRequest, *, now: datetime | None = None
    ) -> NotificationDeliveryDetail:
        current = _now(now)
        with self._write() as session:
            existing, digest = self._existing_request(session, payload, "test")
            if existing is not None:
                result = self._detail(session, existing, current)
                result.delivery.request_id = payload.request_id
                return result
            settings = self._settings(session)
            self._expected(settings, payload.expected_revision)
            self._configured(settings)
            self._vault.open(settings)
            row = NotificationDeliveryModel(
                id=str(uuid4()), event_key=_digest(["test", str(payload.request_id)]),
                kind="test", state="queued", config_revision=settings.revision,
                destination_revision=settings.destination_revision, chat_id=settings.chat_id,
                request_id=str(payload.request_id), attempt_count=0, accepted_once=False,
                generation=0, created_at=current, updated_at=current, next_attempt_at=current,
            )
            session.add(row)
            self._save_request(session, payload, "test", digest, row, current)
            return self._detail(session, row, current)

    def request_delivery(self, request_id: UUID) -> NotificationDeliveryRead:
        with self._read() as session:
            request = session.get(NotificationRequestModel, str(request_id))
            if request is None:
                raise NotificationError(404, "notification_request_not_found")
            result = self._delivery_read(session, self._row(session, request.delivery_id), _now())
            result.request_id = UUID(request.id)
            return result

    def list_deliveries(self, limit: int = 50) -> NotificationDeliveriesResponse:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise NotificationError(422, "notification_invalid_request")
        with self._read() as session:
            rows = session.scalars(
                select(NotificationDeliveryModel)
                .order_by(NotificationDeliveryModel.created_at.desc(), NotificationDeliveryModel.id)
                .limit(limit)
            )
            now = _now()
            return NotificationDeliveriesResponse(
                deliveries=[self._delivery_read(session, row, now) for row in rows]
            )

    def delivery(self, identifier: UUID) -> NotificationDeliveryDetail:
        with self._read() as session:
            return self._detail(session, self._row(session, identifier), _now())

    def _retry_time(self, session, row) -> datetime | None:
        if row.state not in {"unknown", "failed"} or row.accepted_once:
            return None
        latest = session.get(NotificationAttemptModel, row.last_attempt_id)
        if latest is None:
            return None
        times = [_utc(latest.finished_at or latest.started_at)]
        if row.state == "unknown":
            times.append(_utc(latest.deadline_at))
        settings = self._settings(session)
        throttle = session.get(NotificationChatModel, settings.chat_id)
        if throttle:
            times.append(_utc(throttle.next_allowed_at))
            if throttle.in_flight_attempt_id and throttle.deadline_at:
                times.append(_utc(throttle.deadline_at))
        return max(times)

    def _delivery_read(self, session, row, now) -> NotificationDeliveryRead:
        available = self._retry_time(session, row)
        settings = self._settings(session)
        allowed = bool(
            available is not None and available <= now
            and (row.kind == "test" or settings.enabled)
            and settings.chat_id and settings.token_ciphertext
            and self._vault.check(settings) is None
            and (
                row.kind == "test"
                or self._current_candidate(session, row, settings, now) is not None
            )
        )
        return NotificationDeliveryRead(
            id=UUID(row.id), kind=row.kind, state=row.state,
            config_revision=row.config_revision, destination_revision=row.destination_revision,
            request_id=UUID(row.request_id) if row.request_id else None,
            chat_id=row.chat_id, username=row.username,
            plan_id=UUID(row.plan_id) if row.plan_id else None,
            plan_name=row.plan_name, expires_at=_utc(row.expires_at),
            last_attempt_id=UUID(row.last_attempt_id) if row.last_attempt_id else None,
            attempt_count=row.attempt_count, created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at), next_attempt_at=_utc(row.next_attempt_at),
            retry_available_at=available, manual_retry_allowed=allowed,
            code=row.code, message_id=row.message_id,
        )

    def _detail(self, session, row, now) -> NotificationDeliveryDetail:
        attempts = session.scalars(
            select(NotificationAttemptModel).where(NotificationAttemptModel.delivery_id == row.id)
            .order_by(NotificationAttemptModel.attempt_number)
        )
        return NotificationDeliveryDetail(
            delivery=self._delivery_read(session, row, now),
            attempts=[NotificationAttemptRead(
                id=UUID(item.id), delivery_id=UUID(item.delivery_id), state=item.state,
                attempt_number=item.attempt_number, config_revision=item.config_revision,
                destination_revision=item.destination_revision, chat_id=item.chat_id,
                started_at=_utc(item.started_at), deadline_at=_utc(item.deadline_at),
                finished_at=_utc(item.finished_at), code=item.code, message_id=item.message_id,
                retry_after=item.retry_after, retryable=item.retryable,
                late_receipt_at=_utc(item.late_receipt_at),
            ) for item in attempts],
        )

    @staticmethod
    def _cancel(row, code, now) -> None:
        row.state, row.code = "cancelled", code
        row.updated_at, row.next_attempt_at = now, None

    def _current_candidate(self, session, row, settings, now):
        """Read-only eligibility for both display and the write-side sending fence."""
        user = session.get(ProductUserModel, row.username)
        if user is None or user.current_plan_id != row.plan_id:
            return None
        plan = session.get(SubscriptionPlanModel, row.plan_id)
        if (
            plan is None or not user.is_active or user.removal_id
            or _utc(user.created_at) != _utc(row.user_created_at)
            or _utc(user.plan_expires_at) != _utc(row.expires_at)
            or user.plan_expires_at is None
        ):
            return None
        expiry = _utc(user.plan_expires_at)
        deadline = (
            now.astimezone(ZoneInfo(settings.timezone)) + timedelta(days=settings.advance_days)
        ).astimezone(UTC)
        if not now < expiry <= deadline:
            return None
        return self._candidate(user, plan)

    def _revalidate(self, session, row, settings, now):
        if row.kind == "test":
            return TEST_MESSAGE
        candidate = self._current_candidate(session, row, settings, now)
        if candidate is None:
            return None
        row.plan_name = candidate.plan_name
        return self._message(candidate, settings.timezone)

    def retry(
        self, identifier: UUID, payload: NotificationRetryRequest, *, now: datetime | None = None
    ) -> NotificationDeliveryDetail:
        current = _now(now)
        with self._write() as session:
            existing, digest = self._existing_request(session, payload, "retry", identifier)
            if existing is not None:
                result = self._detail(session, existing, current)
                result.delivery.request_id = payload.request_id
                return result
            self._recover(session, current)
            settings = self._settings(session)
            self._expected(settings, payload.expected_revision)
            row = self._row(session, identifier)
            if row.last_attempt_id != str(payload.expected_attempt_id):
                raise NotificationError(409, "notification_attempt_conflict")
            available = self._retry_time(session, row)
            if available is None:
                raise NotificationError(409, "notification_retry_not_allowed")
            if available > current:
                raise NotificationError(409, "notification_retry_too_early")
            if row.state == "unknown" and not payload.confirm_duplicate_risk:
                raise NotificationError(422, "notification_duplicate_risk_required")
            if row.kind == "package_expiry" and not settings.enabled:
                raise NotificationError(409, "notification_disabled")
            self._configured(settings)
            self._vault.open(settings)
            if self._revalidate(session, row, settings, current) is None:
                raise NotificationError(409, "notification_no_longer_eligible")
            row.state, row.next_attempt_at, row.updated_at = "queued", current, current
            row.code, row.message_id = None, None
            row.config_revision, row.destination_revision = (
                settings.revision, settings.destination_revision
            )
            row.chat_id, row.request_id = settings.chat_id, str(payload.request_id)
            row.generation += 1
            self._save_request(session, payload, "retry", digest, row, current)
            return self._detail(session, row, current)

    def scan(self, *, now: datetime | None = None) -> int:
        current = _now(now)
        with self._write() as session:
            settings = self._settings(session)
            if not settings.enabled or current.astimezone(ZoneInfo(settings.timezone)).hour < 9:
                return 0
            self._configured(settings)
            self._vault.open(settings)
            added = 0
            for user, plan in session.execute(self._eligible_query(settings, current)):
                event_key = self._event_key(user, plan)
                row = session.scalar(select(NotificationDeliveryModel).where(
                    NotificationDeliveryModel.event_key == event_key
                ))
                if row is not None:
                    if (
                        row.state == "cancelled" and row.attempt_count == 0
                        and not row.accepted_once
                    ):
                        row.state, row.code = "queued", None
                        row.next_attempt_at, row.updated_at = current, current
                        row.config_revision, row.destination_revision = (
                            settings.revision, settings.destination_revision
                        )
                        row.chat_id, row.plan_name = settings.chat_id, plan.name
                        added += 1
                    continue
                session.add(NotificationDeliveryModel(
                    id=str(uuid4()), event_key=event_key, kind="package_expiry", state="queued",
                    config_revision=settings.revision,
                    destination_revision=settings.destination_revision, chat_id=settings.chat_id,
                    username=user.username, user_created_at=_utc(user.created_at),
                    plan_id=plan.id, plan_name=plan.name, expires_at=_utc(user.plan_expires_at),
                    attempt_count=0, accepted_once=False, generation=0, created_at=current,
                    updated_at=current, next_attempt_at=current,
                ))
                added += 1
            return added

    def _recover(self, session, now) -> int:
        recovered = 0
        for attempt in session.scalars(select(NotificationAttemptModel).where(
            NotificationAttemptModel.state == "sending", NotificationAttemptModel.deadline_at <= now
        )):
            attempt.state, attempt.code = "unknown", "notification_attempt_expired"
            attempt.finished_at, attempt.recovered_at, attempt.retryable = now, now, False
            row = self._row(session, attempt.delivery_id)
            if row.last_attempt_id == attempt.id and row.generation == attempt.generation:
                row.state, row.code = "unknown", attempt.code
                row.updated_at, row.next_attempt_at = now, None
            recovered += 1
        for throttle in session.scalars(select(NotificationChatModel).where(
            NotificationChatModel.in_flight_attempt_id.is_not(None),
            NotificationChatModel.deadline_at <= now,
        )):
            throttle.in_flight_attempt_id, throttle.deadline_at = None, None
            throttle.next_allowed_at = max(_utc(throttle.next_allowed_at), now)
        session.flush()
        return recovered

    def recover(self, *, now: datetime | None = None) -> int:
        with self._write() as session:
            return self._recover(session, _now(now))

    def claim(
        self, *, now: datetime | None = None, lease_seconds: int = 40
    ) -> ClaimedNotification | None:
        if type(lease_seconds) is not int or not 20 <= lease_seconds <= 120:
            raise NotificationError(422, "notification_invalid_request")
        current = _now(now)
        with self._write() as session:
            self._recover(session, current)
            settings = self._settings(session)
            rows = session.scalars(select(NotificationDeliveryModel).where(
                NotificationDeliveryModel.state == "queued",
                NotificationDeliveryModel.next_attempt_at <= current,
            ).order_by(NotificationDeliveryModel.created_at, NotificationDeliveryModel.id))
            for row in rows:
                if row.accepted_once:
                    self._cancel(row, "notification_already_accepted", current)
                    continue
                if row.kind == "package_expiry" and not settings.enabled:
                    self._cancel(row, "notification_disabled", current)
                    continue
                message = self._revalidate(session, row, settings, current)
                if message is None:
                    self._cancel(row, "notification_no_longer_eligible", current)
                    continue
                self._configured(settings)
                token = self._vault.open(settings)
                throttle = session.get(NotificationChatModel, settings.chat_id)
                if throttle is not None and (
                    throttle.in_flight_attempt_id
                    or _utc(throttle.next_allowed_at) > current
                ):
                    continue
                deadline = current + timedelta(seconds=lease_seconds)
                attempt_id = str(uuid4())
                attempt = NotificationAttemptModel(
                    id=attempt_id, delivery_id=row.id, state="sending",
                    attempt_number=row.attempt_count + 1, generation=row.generation,
                    config_revision=settings.revision,
                    destination_revision=settings.destination_revision, chat_id=settings.chat_id,
                    message_text=message, started_at=current, deadline_at=deadline, retryable=False,
                )
                session.add(attempt)
                row.state, row.last_attempt_id = "sending", attempt_id
                row.attempt_count += 1
                row.updated_at, row.next_attempt_at, row.code = current, None, None
                row.config_revision, row.destination_revision = (
                    settings.revision, settings.destination_revision
                )
                row.chat_id = settings.chat_id
                if throttle is None:
                    throttle = NotificationChatModel(chat_id=settings.chat_id)
                    session.add(throttle)
                throttle.next_allowed_at = current + CHAT_INTERVAL
                throttle.in_flight_attempt_id, throttle.deadline_at = attempt_id, deadline
                # The context manager commits before the caller receives this token.
                return ClaimedNotification(
                    delivery_id=UUID(row.id), attempt_id=UUID(attempt_id), token=token,
                    chat_id=settings.chat_id, text=message, deadline_at=deadline,
                )
            return None

    @staticmethod
    def _outcome(value: NotificationOutcome):
        state = value.state if value.state in {"accepted", "failed", "unknown"} else "unknown"
        code = value.code if value.code in OUTCOME_CODES else "notification_transport_failure"
        message_id = (
            value.message_id
            if type(value.message_id) is int and 0 < value.message_id < 2**63
            else None
        )
        delay = (
            value.retry_after
            if type(value.retry_after) is int and 1 <= value.retry_after <= 86400
            else None
        )
        if (state == "accepted" and code != "telegram_accepted") or (
            state == "failed" and code not in FAILED_CODES
        ):
            state, code = "unknown", "notification_transport_failure"
        if state == "accepted" and message_id is None:
            state, code = "unknown", "notification_invalid_response"
        if code == "telegram_rate_limited" and delay is None:
            state, code = "unknown", "notification_invalid_response"
        retryable = state == "failed" and value.retryable is True and code in SAFE_RETRY_CODES
        return state, code, message_id if state == "accepted" else None, delay, retryable

    def finish(
        self, claim: ClaimedNotification, outcome: NotificationOutcome,
        *, now: datetime | None = None,
    ) -> NotificationDeliveryRead:
        current = _now(now)
        state, code, message_id, delay, retryable = self._outcome(outcome)
        with self._write() as session:
            attempt = session.get(NotificationAttemptModel, str(claim.attempt_id))
            if attempt is None or attempt.delivery_id != str(claim.delivery_id):
                raise NotificationError(409, "notification_attempt_conflict")
            row = self._row(session, claim.delivery_id)
            # A recorded real receipt is terminal. Recovery is the sole provisional
            # receipt which a late worker result may refine, on its own history row.
            if attempt.finished_at is not None and attempt.recovered_at is None:
                return self._delivery_read(session, row, current)
            if attempt.late_receipt_at is not None:
                return self._delivery_read(session, row, current)
            if attempt.recovered_at is not None or current > _utc(attempt.deadline_at):
                attempt.late_receipt_at = current
            attempt.state, attempt.code, attempt.message_id = state, code, message_id
            attempt.finished_at, attempt.retry_after, attempt.retryable = current, delay, retryable
            if state == "accepted":
                row.accepted_once = True
            throttle = session.get(NotificationChatModel, attempt.chat_id)
            if throttle is not None:
                throttle.next_allowed_at = max(
                    _utc(throttle.next_allowed_at), current + CHAT_INTERVAL,
                    current + timedelta(seconds=delay or 0),
                )
                if throttle.in_flight_attempt_id == attempt.id and (
                    state != "unknown" or current >= _utc(attempt.deadline_at)
                ):
                    throttle.in_flight_attempt_id, throttle.deadline_at = None, None
            if row.last_attempt_id != attempt.id or row.generation != attempt.generation:
                session.flush()
                return self._delivery_read(session, row, current)
            row.state, row.code, row.message_id = state, code, message_id
            row.updated_at, row.next_attempt_at = current, None
            if retryable and row.attempt_count < MAX_AUTOMATIC_ATTEMPTS and not row.accepted_once:
                settings = self._settings(session)
                if (row.kind == "test" or settings.enabled) and self._revalidate(
                    session, row, settings, current
                ) is not None:
                    row.state = "queued"
                    row.next_attempt_at = max(
                        current + timedelta(seconds=delay or 2 ** row.attempt_count),
                        _utc(throttle.next_allowed_at) if throttle else current,
                    )
            session.flush()
            return self._delivery_read(session, row, current)
