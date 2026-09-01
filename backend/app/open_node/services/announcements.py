"""SQLite announcement instances with active-plan subscriber filtering."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, DateTime, String, Text, delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.announcements import (
    AnnouncementCreate,
    AnnouncementError,
    AnnouncementRead,
    AnnouncementsResponse,
)
from open_node.services.inventory import (
    InventoryStore,
    ProductUserModel,
    SubscriptionPlanModel,
    begin_serialized_write,
)


class AnnouncementBase(DeclarativeBase):
    pass


class AnnouncementModel(AnnouncementBase):
    __tablename__ = "web_announcements"
    __table_args__ = (
        CheckConstraint("length(id) = 36", name="announcement_uuid_length"),
        CheckConstraint(
            "type IN ('general','maintenance','sub_update')",
            name="announcement_known_type",
        ),
        CheckConstraint("length(title) BETWEEN 1 AND 100", name="announcement_title_length"),
        CheckConstraint("length(body) BETWEEN 1 AND 2000", name="announcement_body_length"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AnnouncementStore:
    def __init__(self, inventory: InventoryStore, *, clock=None):
        self.inventory = inventory
        self.clock = clock or (lambda: datetime.now(UTC))

    def _supported(self):
        if self.inventory._engine.dialect.name not in {"sqlite", "postgresql"}:
            raise AnnouncementError("announcement_storage_unavailable", 503)

    @contextmanager
    def _read(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                yield session
        except SQLAlchemyError:
            raise AnnouncementError("announcement_storage_unavailable", 503) from None

    @contextmanager
    def _write(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                connection = session.connection()
                dbapi_connection = connection.connection.dbapi_connection
                try:
                    begin_serialized_write(
                        session, self.inventory._engine, "announcement-write"
                    )
                    yield session
                    session.commit()
                except BaseException:
                    try:
                        dbapi_connection.rollback()
                    except Exception:
                        connection.invalidate()
                        raise AnnouncementError(
                            "announcement_storage_unavailable", 503
                        ) from None
                    raise
        except SQLAlchemyError:
            raise AnnouncementError("announcement_storage_unavailable", 503) from None

    def create_schema(self):
        self._supported()
        try:
            with self.inventory._engine.begin() as connection:
                AnnouncementBase.metadata.create_all(connection)
        except SQLAlchemyError:
            raise AnnouncementError("announcement_storage_unavailable", 503) from None

    @staticmethod
    def _read_row(row: AnnouncementModel) -> AnnouncementRead:
        try:
            return AnnouncementRead(
                id=UUID(row.id),
                type=row.type,
                title=row.title,
                body=row.body,
                created_at=_utc(row.created_at),
                expires_at=_utc(row.expires_at) if row.expires_at else None,
            )
        except (ValidationError, ValueError, TypeError):
            raise AnnouncementError("announcement_storage_unavailable", 503) from None

    def create(self, payload: AnnouncementCreate) -> AnnouncementRead:
        try:
            value = AnnouncementCreate.model_validate(payload)
        except ValidationError:
            raise AnnouncementError("announcement_invalid_request", 422) from None
        now = _utc(self.clock())
        expires_at = (
            now + timedelta(minutes=value.expires_minutes)
            if value.expires_minutes > 0
            else None
        )
        with self._write() as session:
            row = AnnouncementModel(
                id=str(uuid4()),
                type=value.type,
                title=value.title,
                body=value.body,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(row)
            session.flush()
            return self._read_row(row)

    def active(self, *, username: str | None = None) -> AnnouncementsResponse:
        now = _utc(self.clock())
        with self._read() as session:
            if username is not None:
                user = session.get(ProductUserModel, username)
                if (
                    user is None
                    or not user.is_active
                    or user.removal_id is not None
                    or user.current_plan_id is None
                    or session.get(SubscriptionPlanModel, user.current_plan_id) is None
                    or (
                        user.plan_expires_at is not None
                        and _utc(user.plan_expires_at) <= now
                    )
                ):
                    return AnnouncementsResponse(announcements=[])
            rows = session.scalars(
                select(AnnouncementModel)
                .where(
                    or_(
                        AnnouncementModel.expires_at.is_(None),
                        AnnouncementModel.expires_at > now,
                    )
                )
                .order_by(AnnouncementModel.created_at.desc(), AnnouncementModel.id.desc())
                .limit(100)
            ).all()
            return AnnouncementsResponse(
                announcements=[self._read_row(row) for row in rows]
            )

    def delete(self, identifier: UUID):
        with self._write() as session:
            result = session.execute(
                delete(AnnouncementModel).where(AnnouncementModel.id == str(identifier))
            )
            if result.rowcount != 1:
                raise AnnouncementError("announcement_not_found", 404)
