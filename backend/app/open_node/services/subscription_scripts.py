"""Durable override scripts and ordered execution through the isolated worker."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.subscription_scripts import (
    OverrideScriptCreate,
    OverrideScriptRead,
    OverrideScriptUpdate,
)
from open_node.services.inventory import Base, ProductUserModel
from open_node.services.script_runtime import ScriptRuntimeError, lint_script, run_script

MAX_SCRIPT_VALUE_BYTES = 8 * 1024 * 1024


class SubscriptionScriptError(ValueError):
    status_code = 422


class SubscriptionScriptNotFound(SubscriptionScriptError):
    status_code = 404


class SubscriptionScriptConflict(SubscriptionScriptError):
    status_code = 409


class OverrideScriptModel(Base):
    __tablename__ = "subscription_override_scripts"
    __table_args__ = (
        UniqueConstraint("owner_username", "name_key", name="uq_override_script_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    hook: Mapped[str] = mapped_column(String(24), index=True)
    content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _name(value: str):
    result = value.strip()
    if not result or any(ord(char) < 32 for char in result):
        raise SubscriptionScriptError("Override script name is required")
    return result


def _lint(content: str):
    try:
        lint_script(content)
    except ScriptRuntimeError as exc:
        raise SubscriptionScriptError(str(exc)) from None


def _bounded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ScriptRuntimeError("Override script returned a non-JSON value") from None
    if len(raw.encode()) > MAX_SCRIPT_VALUE_BYTES:
        raise ScriptRuntimeError("Override script returned more than 8 MiB")
    return value


class SubscriptionScripts:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(row):
        return OverrideScriptRead(
            id=row.id,
            owner_username=row.owner_username,
            name=row.name,
            hook=row.hook,
            content=row.content,
            enabled=row.enabled,
            sort_order=row.sort_order,
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _owner(session, username):
        owner = session.get(ProductUserModel, username)
        if owner is None or owner.removal_id:
            raise SubscriptionScriptNotFound("Subscriber not found")

    def list(self, *, owner_username=None):
        with self.store._session() as session:
            query = select(OverrideScriptModel)
            if owner_username is not None:
                query = query.where(OverrideScriptModel.owner_username == owner_username)
            rows = session.scalars(
                query.order_by(
                    OverrideScriptModel.owner_username,
                    OverrideScriptModel.sort_order,
                    OverrideScriptModel.created_at,
                    OverrideScriptModel.id,
                )
            ).all()
            return [self._read(row) for row in rows]

    def create(self, payload: OverrideScriptCreate, *, owner_username=None):
        owner = owner_username or payload.owner_username
        if owner_username is not None and payload.owner_username != owner_username:
            raise SubscriptionScriptNotFound("Subscriber not found")
        _lint(payload.content)
        now = datetime.now(UTC)
        name = _name(payload.name)
        row = OverrideScriptModel(
            id=str(uuid4()),
            owner_username=owner,
            name=name,
            name_key=name.casefold(),
            hook=payload.hook,
            content=payload.content.strip(),
            enabled=payload.enabled,
            sort_order=payload.sort_order,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        with self.store._coordinated_session() as session:
            self._owner(session, owner)
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionScriptConflict(
                    "An override script with this name already exists for the subscriber"
                ) from None
            result = self._read(row)
            session.commit()
            return result

    def update(self, identifier, payload: OverrideScriptUpdate, *, owner_username=None):
        _lint(payload.content)
        with self.store._coordinated_session() as session:
            row = session.get(OverrideScriptModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionScriptNotFound("Override script not found")
            if row.revision != payload.expected_revision:
                raise SubscriptionScriptConflict("Override script changed; reload before saving")
            row.name = _name(payload.name)
            row.name_key = row.name.casefold()
            row.hook = payload.hook
            row.content = payload.content.strip()
            row.enabled = payload.enabled
            row.sort_order = payload.sort_order
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionScriptConflict(
                    "An override script with this name already exists for the subscriber"
                ) from None
            result = self._read(row)
            session.commit()
            return result

    def delete(self, identifier, expected_revision, *, owner_username=None):
        with self.store._coordinated_session() as session:
            row = session.get(OverrideScriptModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionScriptNotFound("Override script not found")
            if row.revision != expected_revision:
                raise SubscriptionScriptConflict("Override script changed; reload before deleting")
            self._remove_profile_reference(session, row.id)
            session.delete(row)
            session.commit()

    @staticmethod
    def _remove_profile_reference(session, identifier):
        from open_node.services.inventory import SubscriptionProfileModel

        now = datetime.now(UTC)
        for profile in session.scalars(select(SubscriptionProfileModel)).all():
            values = list(profile.selected_override_script_ids or [])
            filtered = [value for value in values if value != identifier]
            if values != filtered:
                profile.selected_override_script_ids = filtered
                profile.updated_at = now

    @staticmethod
    def owned(session, identifiers, owner_username):
        if not identifiers:
            return
        rows = session.scalars(
            select(OverrideScriptModel).where(OverrideScriptModel.id.in_(identifiers))
        ).all()
        if len(rows) != len(identifiers) or any(
            row.owner_username != owner_username for row in rows
        ):
            raise SubscriptionScriptConflict(
                "Selected override script is missing or belongs to another subscriber"
            )

    @staticmethod
    def _selected(session, owner_username, identifiers, hook):
        rows = session.scalars(
            select(OverrideScriptModel)
            .where(
                OverrideScriptModel.owner_username == owner_username,
                OverrideScriptModel.enabled.is_(True),
                OverrideScriptModel.hook == hook,
            )
            .order_by(
                OverrideScriptModel.sort_order,
                OverrideScriptModel.created_at,
                OverrideScriptModel.id,
            )
        ).all()
        if identifiers:
            selected = set(identifiers)
            rows = [row for row in rows if row.id in selected]
        return rows

    def apply(self, session, owner_username, identifiers, hook, value, *, validator=None):
        warnings = []
        current = value
        applied = False
        for row in self._selected(session, owner_username, identifiers, hook):
            try:
                candidate = _bounded(run_script(hook, row.content, current))
                current = validator(candidate) if validator else candidate
                applied = True
            except (ScriptRuntimeError, ValueError, TypeError, KeyError, OverflowError):
                warnings.append("An enabled override script failed and was skipped")
        return current, warnings, applied
