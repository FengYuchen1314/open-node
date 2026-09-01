"""SQLite-backed subscriber permissions, enforced independently of the UI."""

import json
from contextlib import contextmanager

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Integer, Text, func, inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from open_node.domain.subscriber_permissions import (
    FEATURES,
    MAX_QUOTA,
    MAX_REVISION,
    SubscriberFeature,
    SubscriberPermissionsAccount,
    SubscriberPermissionsError,
    SubscriberPermissionsSettings,
    SubscriberPermissionsUpdate,
    SubscriberQuotaUsage,
)


class SubscriberPermissionsBase(DeclarativeBase):
    pass


class SubscriberPermissionsModel(SubscriberPermissionsBase):
    __tablename__ = "subscriber_permissions_policy"
    __table_args__ = (
        CheckConstraint("id=1"),
        CheckConstraint(
            f"typeof(revision)='integer' AND revision>=0 AND revision<={MAX_REVISION}"
        ),
        CheckConstraint(
            f"typeof(template_quota)='integer' AND template_quota>=0 "
            f"AND template_quota<={MAX_QUOTA}"
        ),
        CheckConstraint(
            f"typeof(external_source_quota)='integer' AND external_source_quota>=0 "
            f"AND external_source_quota<={MAX_QUOTA}"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[str] = mapped_column(Text, nullable=False)
    template_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    external_source_quota: Mapped[int] = mapped_column(Integer, nullable=False)


class SubscriberPermissionsStore:
    def __init__(self, inventory):
        self.inventory = inventory

    def _supported(self):
        if self.inventory._engine.dialect.name != "sqlite":
            raise SubscriberPermissionsError(
                503, "subscriber_permissions_storage_unavailable"
            )

    @contextmanager
    def _session(self, *, write=False):
        self._supported()
        try:
            with self.inventory._session() as session:
                if not write:
                    yield session
                    return
                connection = session.connection()
                raw = connection.connection.dbapi_connection
                try:
                    session.execute(text("BEGIN IMMEDIATE"))
                    yield session
                    session.commit()
                except BaseException:
                    try:
                        raw.rollback()
                    except Exception:
                        connection.invalidate()
                    raise
        except SubscriberPermissionsError:
            raise
        except (SQLAlchemyError, ValidationError, json.JSONDecodeError, TypeError, ValueError):
            raise SubscriberPermissionsError(
                503, "subscriber_permissions_storage_unavailable"
            ) from None

    @staticmethod
    def _read(session):
        try:
            rows = session.scalars(select(SubscriberPermissionsModel).limit(2)).all()
            if len(rows) != 1 or rows[0].id != 1:
                raise SubscriberPermissionsError(
                    503, "subscriber_permissions_storage_unavailable"
                )
            row = rows[0]
            pages = json.loads(row.pages)
            result = SubscriberPermissionsSettings(
                revision=row.revision,
                pages=pages,
                template_quota=row.template_quota,
                external_source_quota=row.external_source_quota,
                license_required=False,
            )
            canonical = json.dumps(result.pages, ensure_ascii=True, separators=(",", ":"))
            if row.pages != canonical:
                raise SubscriberPermissionsError(
                    503, "subscriber_permissions_storage_unavailable"
                )
            return result
        except SubscriberPermissionsError:
            raise
        except (SQLAlchemyError, ValidationError, json.JSONDecodeError, TypeError, ValueError):
            raise SubscriberPermissionsError(
                503, "subscriber_permissions_storage_unavailable"
            ) from None

    def create_schema(self):
        if self.inventory._engine.dialect.name != "sqlite":
            return
        with self._session(write=True) as session:
            connection = session.connection()
            existed = inspect(connection).has_table(SubscriberPermissionsModel.__tablename__)
            SubscriberPermissionsBase.metadata.create_all(connection)
            if not existed:
                session.add(SubscriberPermissionsModel(
                    id=1,
                    revision=0,
                    pages=json.dumps(list(FEATURES), separators=(",", ":")),
                    template_quota=0,
                    external_source_quota=0,
                ))
                session.flush()
            self._read(session)

    def settings(self):
        with self._session() as session:
            return self._read(session)

    def update(self, payload):
        try:
            value = SubscriberPermissionsUpdate.model_validate(payload)
        except ValidationError:
            raise SubscriberPermissionsError(
                422, "subscriber_permissions_invalid_request"
            ) from None
        with self._session(write=True) as session:
            current = self._read(session)
            if current.revision != value.expected_revision:
                raise SubscriberPermissionsError(
                    409, "subscriber_permissions_revision_conflict"
                )
            if current.revision == MAX_REVISION:
                raise SubscriberPermissionsError(
                    503, "subscriber_permissions_storage_unavailable"
                )
            changed = session.execute(
                update(SubscriberPermissionsModel)
                .where(
                    SubscriberPermissionsModel.id == 1,
                    SubscriberPermissionsModel.revision == value.expected_revision,
                )
                .values(
                    revision=value.expected_revision + 1,
                    pages=json.dumps(value.pages, separators=(",", ":")),
                    template_quota=value.template_quota,
                    external_source_quota=value.external_source_quota,
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise SubscriberPermissionsError(
                    409, "subscriber_permissions_revision_conflict"
                )
            session.expire_all()
            return self._read(session)

    @staticmethod
    def _policy(session):
        return SubscriberPermissionsStore._read(session)

    def require_page(self, _username: str, page: SubscriberFeature):
        with self._session() as session:
            if page not in self._policy(session).pages:
                raise SubscriberPermissionsError(403, "subscriber_feature_disabled")

    def enforce_create(self, session, username: str, feature: SubscriberFeature):
        policy = self._policy(session)
        if feature not in policy.pages:
            raise SubscriberPermissionsError(403, "subscriber_feature_disabled")
        if feature == "templates":
            from open_node.services.subscription_templates import TemplateRecord

            maximum = policy.template_quota
            used = session.scalar(
                select(func.count()).select_from(TemplateRecord).where(
                    TemplateRecord.owner_username == username
                )
            )
        elif feature == "external_subscriptions":
            from open_node.services.external_subscriptions import ExternalSourceModel

            maximum = policy.external_source_quota
            used = session.scalar(
                select(func.count()).select_from(ExternalSourceModel).where(
                    ExternalSourceModel.owner_username == username
                )
            )
        else:
            return
        if maximum and used >= maximum:
            raise SubscriberPermissionsError(409, "subscriber_quota_exceeded")

    def account(self, username: str):
        from open_node.services.external_subscriptions import ExternalSourceModel
        from open_node.services.inventory import ProductUserModel
        from open_node.services.subscription_templates import TemplateRecord

        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if user is None or user.removal_id:
                raise SubscriberPermissionsError(403, "subscriber_feature_disabled")
            policy = self._policy(session)
            templates = session.scalar(
                select(func.count()).select_from(TemplateRecord).where(
                    TemplateRecord.owner_username == username
                )
            )
            sources = session.scalar(
                select(func.count()).select_from(ExternalSourceModel).where(
                    ExternalSourceModel.owner_username == username
                )
            )
            return SubscriberPermissionsAccount(
                pages=policy.pages,
                templates=SubscriberQuotaUsage(
                    used=templates, maximum=policy.template_quota
                ),
                external_sources=SubscriberQuotaUsage(
                    used=sources, maximum=policy.external_source_quota
                ),
                license_required=False,
            )
