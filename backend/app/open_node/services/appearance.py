"""Two owned image slots live inside SQLite, so normal snapshots include them."""

import re
from contextlib import contextmanager
from hashlib import sha256

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Integer, LargeBinary, String, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.appearance import (
    ASSET_BASE,
    ASSET_LIMITS,
    MAX_REVISION,
    AppearanceError,
    AppearancePublic,
    AppearanceSettings,
    AppearanceUpdate,
)
from open_node.services.appearance_images import IMAGE_TYPES, validate_image
from open_node.services.inventory import begin_serialized_write


class AppearanceBase(DeclarativeBase):
    pass


class AppearanceRow(AppearanceBase):
    __tablename__ = "site_appearance"
    __table_args__ = (CheckConstraint("id=1"), CheckConstraint(
        f"revision>=0 AND revision<={MAX_REVISION}"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    default_theme: Mapped[str] = mapped_column(String(10), default="light")
    logo_url: Mapped[str] = mapped_column(String(2000), default="")
    wallpaper_url: Mapped[str] = mapped_column(String(2000), default="")


class AppearanceAsset(AppearanceBase):
    __tablename__ = "site_appearance_assets"
    __table_args__ = (CheckConstraint("slot IN ('logo','wallpaper')"), CheckConstraint(
        "length(content)>0 AND "
        "length(content)<=CASE WHEN slot='logo' THEN 2097152 ELSE 10485760 END"),)
    slot: Mapped[str] = mapped_column(String(16), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class AppearanceStore:
    def __init__(self, inventory):
        self.inventory = inventory

    @contextmanager
    def _session(self, *, write=False):
        if self.inventory._engine.dialect.name not in {"sqlite", "postgresql"}:
            raise AppearanceError(503, "appearance_storage_unavailable")
        try:
            with self.inventory._session() as db:
                if not write:
                    yield db
                    return
                connection = db.connection()
                raw = connection.connection.dbapi_connection
                try:
                    begin_serialized_write(
                        db, self.inventory._engine, "appearance-write"
                    )
                    yield db
                    db.commit()
                except BaseException:
                    try:
                        raw.rollback()
                    except Exception:
                        connection.invalidate()
                    raise
        except (SQLAlchemyError, ValidationError):
            raise AppearanceError(503, "appearance_storage_unavailable") from None

    @staticmethod
    def _read(db):
        rows = db.scalars(select(AppearanceRow).limit(2)).all()
        if len(rows) != 1 or rows[0].id != 1:
            raise AppearanceError(503, "appearance_storage_unavailable")
        item = rows[0]
        return AppearanceSettings(revision=item.revision, default_theme=item.default_theme,
                                  logo_url=item.logo_url, wallpaper_url=item.wallpaper_url)

    @staticmethod
    def _revision(db, expected):
        current = AppearanceStore._read(db)
        if current.revision != expected:
            raise AppearanceError(409, "appearance_revision_conflict")
        if current.revision == MAX_REVISION:
            raise AppearanceError(503, "appearance_storage_unavailable")
        return current

    def create_schema(self):
        with self._session(write=True) as db:
            exists = inspect(db.connection()).has_table(AppearanceRow.__tablename__)
            AppearanceBase.metadata.create_all(db.connection())
            if not exists:
                db.add(AppearanceRow(id=1))
                db.flush()
            self._read(db)

    def get_settings(self):
        with self._session() as db:
            return self._read(db)

    def get_public(self):
        saved = self.get_settings()
        return AppearancePublic(**saved.model_dump(exclude={"revision"}))

    def update(self, value):
        try:
            payload = AppearanceUpdate.model_validate(value)
        except ValidationError:
            raise AppearanceError(422, "appearance_invalid_request") from None
        with self._session(write=True) as db:
            self._revision(db, payload.expected_revision)
            row = db.get(AppearanceRow, 1)
            for slot in ASSET_LIMITS:
                url = getattr(payload, slot + "_url")
                asset = db.get(AppearanceAsset, slot)
                if url.startswith("/"):
                    if not asset or url != f"{ASSET_BASE}/{slot}/{asset.digest}":
                        raise AppearanceError(422, "appearance_asset_missing")
                elif asset:
                    db.delete(asset)
                setattr(row, slot + "_url", url)
            row.default_theme = payload.default_theme
            row.revision += 1
            db.flush()
            return self._read(db)

    def upload(self, slot, expected_revision, content):
        if slot not in ASSET_LIMITS or type(expected_revision) is not int:
            raise AppearanceError(422, "appearance_invalid_request")
        with self._session() as db:
            self._revision(db, expected_revision)
        media = validate_image(slot, content)
        digest = sha256(content).hexdigest()
        with self._session(write=True) as db:
            self._revision(db, expected_revision)
            asset = db.get(AppearanceAsset, slot)
            if asset is None:
                asset = AppearanceAsset(slot=slot)
                db.add(asset)
            asset.digest, asset.media_type, asset.content = digest, media, content
            row = db.get(AppearanceRow, 1)
            setattr(row, slot + "_url", f"{ASSET_BASE}/{slot}/{digest}")
            row.revision += 1
            db.flush()
            return self._read(db)

    def image(self, slot, digest):
        if slot not in ASSET_LIMITS or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise AppearanceError(404, "appearance_asset_missing")
        with self._session() as db:
            settings = self._read(db)
            if getattr(settings, slot + "_url") != f"{ASSET_BASE}/{slot}/{digest}":
                raise AppearanceError(404, "appearance_asset_missing")
            asset = db.get(AppearanceAsset, slot)
            if (asset is None or asset.media_type not in IMAGE_TYPES or asset.digest != digest
                    or not 0 < len(asset.content) <= ASSET_LIMITS[slot]
                    or sha256(asset.content).hexdigest() != digest):
                raise AppearanceError(404, "appearance_asset_missing")
            return asset.content, asset.media_type
