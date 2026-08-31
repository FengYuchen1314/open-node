"""Atomic SQLite-only branding settings, independent of business data and secrets."""

from contextlib import contextmanager

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Integer, Text, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.branding import (
    BRANDING_MAX_REVISION,
    DEFAULT_BRAND_TITLE,
    DEFAULT_SITE_TITLE,
    BrandingError,
    BrandingPublicRead,
    BrandingSettingsRead,
    BrandingSettingsUpdate,
)
from open_node.services.inventory import InventoryStore


class BrandingBase(DeclarativeBase):
    """Only explicit branding initialization can create the independent table."""


class BrandingSettingsModel(BrandingBase):
    __tablename__ = "site_branding_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="branding_single_row"),
        CheckConstraint(
            "typeof(revision) = 'integer' AND revision >= 0 "
            f"AND revision <= {BRANDING_MAX_REVISION}",
            name="branding_safe_revision",
        ),
        CheckConstraint("typeof(site_title) = 'text'", name="branding_site_text"),
        CheckConstraint("typeof(brand_title) = 'text'", name="branding_brand_text"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    site_title: Mapped[str] = mapped_column(Text, nullable=False)
    brand_title: Mapped[str] = mapped_column(Text, nullable=False)


class BrandingStore:
    def __init__(self, inventory: InventoryStore):
        self.inventory = inventory

    def _supported(self) -> None:
        if self.inventory._engine.dialect.name != "sqlite":
            raise BrandingError(503, "branding_storage_unavailable")

    @contextmanager
    def _read(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                yield session
        except SQLAlchemyError:
            raise BrandingError(503, "branding_storage_unavailable") from None

    @contextmanager
    def _write(self):
        self._supported()
        try:
            with self.inventory._session() as session:
                connection = session.connection()
                dbapi_connection = connection.connection.dbapi_connection
                try:
                    session.execute(text("BEGIN IMMEDIATE"))
                    yield session
                    session.commit()
                except BaseException:
                    # A commit hook can fail before SQLite commits while the
                    # SQLAlchemy transaction is already marked inactive. Ensure
                    # no uncommitted write leaks into the next pooled borrower.
                    # This cannot undo a commit that actually reached SQLite;
                    # callers must GET to reconcile an uncertain save receipt.
                    try:
                        dbapi_connection.rollback()
                    except Exception:
                        connection.invalidate()
                        raise BrandingError(503, "branding_storage_unavailable") from None
                    raise
        except SQLAlchemyError:
            raise BrandingError(503, "branding_storage_unavailable") from None

    @staticmethod
    def _settings(session) -> BrandingSettingsRead:
        rows = session.scalars(select(BrandingSettingsModel).limit(2)).all()
        if (
            len(rows) != 1 or rows[0] is None
            or type(rows[0].id) is not int or rows[0].id != 1
        ):
            raise BrandingError(503, "branding_storage_unavailable")
        row = rows[0]
        try:
            result = BrandingSettingsRead(
                revision=row.revision, site_title=row.site_title, brand_title=row.brand_title,
            )
        except ValidationError:
            raise BrandingError(503, "branding_storage_unavailable") from None
        # Stored values must already be canonical. Reads never silently normalize
        # corrupt persisted data, rewrite it, or claim an implicit reset succeeded.
        if result.site_title != row.site_title or result.brand_title != row.brand_title:
            raise BrandingError(503, "branding_storage_unavailable")
        return result

    def create_schema(self) -> None:
        if self.inventory._engine.dialect.name != "sqlite":
            return
        with self._write() as session:
            connection = session.connection()
            existed = inspect(connection).has_table(BrandingSettingsModel.__tablename__)
            BrandingBase.metadata.create_all(connection)
            if not existed:
                session.add(BrandingSettingsModel(
                    id=1, revision=0, site_title=DEFAULT_SITE_TITLE,
                    brand_title=DEFAULT_BRAND_TITLE,
                ))
                session.flush()
            self._settings(session)

    def get_settings(self) -> BrandingSettingsRead:
        with self._read() as session:
            return self._settings(session)

    def get_public(self) -> BrandingPublicRead:
        saved = self.get_settings()
        # Deliberately project only these public fields, never a settings dump.
        return BrandingPublicRead(site_title=saved.site_title, brand_title=saved.brand_title)

    def update_settings(self, payload: BrandingSettingsUpdate) -> BrandingSettingsRead:
        self._supported()
        try:
            validated = BrandingSettingsUpdate.model_validate(payload)
        except ValidationError:
            raise BrandingError(422, "branding_invalid_request") from None
        with self._write() as session:
            saved = self._settings(session)
            if saved.revision != validated.expected_revision:
                raise BrandingError(409, "branding_revision_conflict")
            if saved.revision == BRANDING_MAX_REVISION:
                raise BrandingError(503, "branding_storage_unavailable")
            result = session.execute(
                update(BrandingSettingsModel)
                .where(
                    BrandingSettingsModel.id == 1,
                    BrandingSettingsModel.revision == validated.expected_revision,
                )
                .values(
                    revision=saved.revision + 1, site_title=validated.site_title,
                    brand_title=validated.brand_title,
                )
            )
            if result.rowcount != 1:
                raise BrandingError(409, "branding_revision_conflict")
            # Materialize the response in this transaction; __exit__ commits
            # before returning it, and a commit failure never becomes success.
            return self._settings(session)
