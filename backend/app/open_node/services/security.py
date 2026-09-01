"""Persistent official-compatible security event and IP-ban management."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import ip_address
from threading import RLock
from time import time

from sqlalchemy import Boolean, Float, Index, Integer, String, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.security import (
    SecurityBanRead,
    SecurityBansRead,
    SecurityError,
    SecurityEventRead,
    SecurityEventsRead,
    SecuritySettingsRead,
)
from open_node.services.backup_coordination import BackupCoordinationError
from open_node.services.backup_runtime import backup_operation


class SecurityBase(DeclarativeBase):
    pass


class SecurityEventModel(SecurityBase):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_at", "at"),
        Index("ix_security_events_ip", "ip"),
        Index("ix_security_events_kind_at", "kind", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[float] = mapped_column(Float)
    ip: Mapped[str] = mapped_column(String(45))
    kind: Mapped[str] = mapped_column(String(24))
    path: Mapped[str] = mapped_column(String(160), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(String(120), default="")
    actor: Mapped[str] = mapped_column(String(64), default="")


class SecurityBanModel(SecurityBase):
    __tablename__ = "security_ip_bans"

    ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    reason: Mapped[str] = mapped_column(String(24))
    banned_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    permanent: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    released_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="")


class SecuritySettingsModel(SecurityBase):
    __tablename__ = "security_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    brute_force_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    brute_force_max_failures: Mapped[int] = mapped_column(Integer, default=5)
    brute_force_window_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    brute_force_block_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    skip_local_ip: Mapped[bool] = mapped_column(Boolean, default=True)


class SecurityStore:
    def __init__(self, inventory, backup_writes, *, clock=time):
        self.inventory = inventory
        self.backup_writes = backup_writes
        self.clock = clock
        self._attempts: dict[str, tuple[int, float]] = {}
        self._attempt_lock = RLock()

    def create_schema(self) -> None:
        with self.inventory._engine.begin() as connection:
            SecurityBase.metadata.create_all(connection)
        with backup_operation(self.backup_writes):
            with self.inventory._coordinated_session() as session:
                if session.get(SecuritySettingsModel, 1) is None:
                    session.add(SecuritySettingsModel(id=1))
                    session.commit()

    @staticmethod
    def _datetime(value: float | None):
        return datetime.fromtimestamp(value, UTC) if value is not None else None

    @classmethod
    def _event_read(cls, row) -> SecurityEventRead:
        return SecurityEventRead(
            id=row.id,
            at=cls._datetime(row.at),
            ip=row.ip,
            kind=row.kind,
            path=row.path,
            username=row.username,
            detail=row.detail,
            actor=row.actor,
        )

    @classmethod
    def _ban_read(cls, row) -> SecurityBanRead:
        return SecurityBanRead(
            ip=row.ip,
            reason=row.reason,
            banned_at=cls._datetime(row.banned_at),
            expires_at=cls._datetime(row.expires_at),
            permanent=row.permanent,
            fail_count=row.fail_count,
            actor=row.actor,
        )

    @staticmethod
    def _settings_read(row) -> SecuritySettingsRead:
        return SecuritySettingsRead(
            revision=row.revision,
            brute_force_enabled=row.brute_force_enabled,
            brute_force_max_failures=row.brute_force_max_failures,
            brute_force_window_minutes=row.brute_force_window_minutes,
            brute_force_block_minutes=row.brute_force_block_minutes,
            skip_local_ip=row.skip_local_ip,
        )

    def settings(self) -> SecuritySettingsRead:
        try:
            with self.inventory._session() as session:
                row = session.get(SecuritySettingsModel, 1)
                if row is None:
                    raise SecurityError("security_unavailable", 503)
                return self._settings_read(row)
        except SQLAlchemyError:
            raise SecurityError("security_unavailable", 503) from None

    def update_settings(self, payload) -> SecuritySettingsRead:
        try:
            with backup_operation(self.backup_writes):
                with self.inventory._coordinated_session() as session:
                    row = session.get(SecuritySettingsModel, 1)
                    if row is None:
                        raise SecurityError("security_unavailable", 503)
                    if row.revision != payload.expected_revision:
                        raise SecurityError("security_revision_conflict", 409)
                    for name in (
                        "brute_force_enabled",
                        "brute_force_max_failures",
                        "brute_force_window_minutes",
                        "brute_force_block_minutes",
                        "skip_local_ip",
                    ):
                        setattr(row, name, getattr(payload, name))
                    row.revision += 1
                    session.commit()
                    return self._settings_read(row)
        except SecurityError:
            raise
        except (BackupCoordinationError, SQLAlchemyError):
            raise SecurityError("security_unavailable", 503) from None

    def events(self, *, kind: str | None, ip: str | None, limit: int, offset: int):
        try:
            canonical = str(ip_address(ip)) if ip else None
        except ValueError:
            raise SecurityError("security_invalid_request", 422) from None
        try:
            with self.inventory._session() as session:
                query = select(SecurityEventModel)
                if kind:
                    query = query.where(SecurityEventModel.kind == kind)
                if canonical:
                    query = query.where(SecurityEventModel.ip == canonical)
                rows = session.scalars(
                    query.order_by(SecurityEventModel.at.desc(), SecurityEventModel.id.desc())
                    .offset(offset)
                    .limit(limit + 1)
                ).all()
                return SecurityEventsRead(
                    events=[self._event_read(row) for row in rows[:limit]],
                    offset=offset,
                    limit=limit,
                    has_more=len(rows) > limit,
                )
        except SQLAlchemyError:
            raise SecurityError("security_unavailable", 503) from None

    def bans(self) -> SecurityBansRead:
        now = self.clock()
        try:
            with self.inventory._session() as session:
                rows = session.scalars(
                    select(SecurityBanModel)
                    .where(
                        SecurityBanModel.released_at.is_(None),
                        SecurityBanModel.permanent.is_(True)
                        | (SecurityBanModel.expires_at > now),
                    )
                    .order_by(SecurityBanModel.banned_at.desc())
                ).all()
                return SecurityBansRead(bans=[self._ban_read(row) for row in rows])
        except SQLAlchemyError:
            raise SecurityError("security_unavailable", 503) from None

    @staticmethod
    def _canonical_public_candidate(value: str) -> str | None:
        try:
            return str(ip_address(value))
        except ValueError:
            return None

    @staticmethod
    def _local(value: str) -> bool:
        try:
            address = ip_address(value)
        except ValueError:
            return True
        return (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
        )

    def is_blocked(self, value: str) -> bool:
        ip = self._canonical_public_candidate(value)
        if ip is None:
            return False
        try:
            settings = self.settings()
            if not settings.brute_force_enabled or (settings.skip_local_ip and self._local(ip)):
                return False
            now = self.clock()
            with self.inventory._session() as session:
                row = session.get(SecurityBanModel, ip)
                return bool(
                    row
                    and row.released_at is None
                    and (row.permanent or (row.expires_at is not None and row.expires_at > now))
                )
        except (SecurityError, SQLAlchemyError):
            return False

    @staticmethod
    def _append_event(session, *, now, ip, kind, path="", username="", detail="", actor=""):
        session.add(SecurityEventModel(
            at=now,
            ip=ip[:45],
            kind=kind,
            path=path[:160],
            username=username[:64],
            detail=detail[:120],
            actor=actor[:64],
        ))

    def _write_event(self, **values) -> None:
        with backup_operation(self.backup_writes):
            with self.inventory._coordinated_session() as session:
                self._append_event(session, now=self.clock(), **values)
                session.commit()

    def record_login_failure(
        self,
        value: str,
        username: str,
        *,
        locked: bool = False,
        path: str = "/api/v1/auth/login",
    ) -> None:
        ip = self._canonical_public_candidate(value)
        if ip is None:
            return
        try:
            self._write_event(
                ip=ip,
                kind="login_locked" if locked else "login_fail",
                path=path,
                username=" ".join(username.split()),
            )
        except (BackupCoordinationError, SecurityError, SQLAlchemyError):
            return

    def record_probe_success(self, value: str) -> None:
        ip = self._canonical_public_candidate(value)
        if ip is not None:
            with self._attempt_lock:
                self._attempts.pop(ip, None)

    def record_probe_failure(self, value: str, path: str) -> None:
        ip = self._canonical_public_candidate(value)
        if ip is None:
            return
        try:
            settings = self.settings()
        except SecurityError:
            return
        if not settings.brute_force_enabled or (settings.skip_local_ip and self._local(ip)):
            return
        if self.is_blocked(ip):
            return
        now = self.clock()
        window = settings.brute_force_window_minutes * 60
        with self._attempt_lock:
            count, first = self._attempts.get(ip, (0, now))
            if now - first > window:
                count, first = 0, now
            count += 1
            if count >= settings.brute_force_max_failures:
                self._attempts.pop(ip, None)
                should_ban = True
            else:
                self._attempts[ip] = (count, first)
                should_ban = False
        try:
            if should_ban:
                self._upsert_ban(
                    ip,
                    permanent=False,
                    actor="",
                    reason="brute_force",
                    fail_count=count,
                    expires_at=now + settings.brute_force_block_minutes * 60,
                    kind="ban",
                    path=path,
                )
            else:
                self._write_event(
                    ip=ip,
                    kind="probe",
                    path=path,
                    detail=f"{count}/{settings.brute_force_max_failures}",
                )
        except (BackupCoordinationError, SecurityError, SQLAlchemyError):
            return

    def _upsert_ban(
        self,
        ip: str,
        *,
        permanent: bool,
        actor: str,
        reason: str,
        fail_count: int,
        expires_at: float | None,
        kind: str,
        path: str = "",
    ) -> SecurityBanRead:
        now = self.clock()
        try:
            with backup_operation(self.backup_writes):
                with self.inventory._coordinated_session() as session:
                    row = session.get(SecurityBanModel, ip)
                    if row is None:
                        row = SecurityBanModel(ip=ip)
                        session.add(row)
                    row.reason = reason
                    row.banned_at = now
                    row.expires_at = None if permanent else expires_at
                    row.permanent = permanent
                    row.fail_count = fail_count
                    row.released_at = None
                    row.actor = actor[:64]
                    self._append_event(
                        session,
                        now=now,
                        ip=ip,
                        kind=kind,
                        path=path,
                        detail=f"fail={fail_count}",
                        actor=actor,
                    )
                    session.commit()
                    return self._ban_read(row)
        except (BackupCoordinationError, SQLAlchemyError):
            raise SecurityError("security_unavailable", 503) from None

    def ban(self, value: str, *, permanent: bool, actor: str) -> SecurityBanRead:
        try:
            ip = str(ip_address(value))
        except ValueError:
            raise SecurityError("security_invalid_request", 422) from None
        settings = self.settings()
        return self._upsert_ban(
            ip,
            permanent=permanent,
            actor=actor,
            reason="manual",
            fail_count=0,
            expires_at=self.clock() + settings.brute_force_block_minutes * 60,
            kind="ban_manual",
        )

    def unban(self, value: str, *, actor: str) -> None:
        try:
            ip = str(ip_address(value))
        except ValueError:
            raise SecurityError("security_invalid_request", 422) from None
        now = self.clock()
        try:
            with backup_operation(self.backup_writes):
                with self.inventory._coordinated_session() as session:
                    row = session.get(SecurityBanModel, ip)
                    if row is None or row.released_at is not None or (
                        not row.permanent and (row.expires_at is None or row.expires_at <= now)
                    ):
                        raise SecurityError("security_ban_not_found", 404)
                    row.released_at = now
                    row.actor = actor[:64]
                    self._append_event(session, now=now, ip=ip, kind="unban", actor=actor)
                    session.commit()
            with self._attempt_lock:
                self._attempts.pop(ip, None)
        except SecurityError:
            raise
        except (BackupCoordinationError, SQLAlchemyError):
            raise SecurityError("security_unavailable", 503) from None
