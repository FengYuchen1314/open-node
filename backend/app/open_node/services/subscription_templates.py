"""Template ownership, defaults and revision-guarded editing without remote writes."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.subscription_templates import (
    CatalogTemplateSettings,
    TemplateList,
    TemplateRead,
    TemplateSettings,
    TemplateWrite,
)
from open_node.services.inventory import Base, ProductUserModel, SubscriptionPlanModel
from open_node.services.subscription_access import revision
from open_node.services.template_rendering import DEFAULT_CLASH, TemplateError

FIELDS = ("clash_template_id",)
DEFAULT_TEMPLATE_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_TEMPLATE_NAME = "default.yaml"


class TemplateNotFound(TemplateError):
    pass


class TemplateConflict(TemplateError):
    pass


class TemplateForbidden(TemplateError):
    pass


class TemplateRecord(Base):
    __tablename__ = "subscription_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    name_key: Mapped[str] = mapped_column(String(640), unique=True)
    format: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    owner_username: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TemplatePreference(Base):
    __tablename__ = "subscription_template_preferences"

    scope: Mapped[str] = mapped_column(String(100), primary_key=True)
    username: Mapped[str | None] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), nullable=True, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    clash_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscription_templates.id", ondelete="SET NULL"), nullable=True
    )


class TemplateStore:
    def __init__(self, inventory):
        self.inventory = inventory

    @staticmethod
    def scope(username):
        return "user:" + username if username is not None else "system"

    @staticmethod
    def user(session, username):
        user = session.get(ProductUserModel, username)
        if not user or user.removal_id:
            raise TemplateNotFound("Subscriber not found")
        return user

    def preference(self, session, username):
        if username is not None:
            self.user(session, username)
        return session.get(TemplatePreference, self.scope(username))

    def settings(self, session, username=None):
        row = self.preference(session, username)
        values = {field: getattr(row, field) if row else None for field in FIELDS}
        values["enabled"] = bool(row.enabled) if row else username is None
        return TemplateSettings(
            **values, revision=revision({"scope": self.scope(username), **values})
        )

    def allowed(self, session, actor):
        return actor is None

    def get(self, session, identifier, actor=None, *, write=False):
        row = session.get(TemplateRecord, str(identifier))
        if row is None or (actor is not None and row.owner_username is not None):
            raise TemplateNotFound("Template not found")
        if (
            write
            and actor is not None
            and (row.owner_username != actor or not self.allowed(session, actor))
        ):
            raise TemplateForbidden("Template editing is not permitted")
        return row

    @staticmethod
    def references(session, row):
        plans = session.scalars(
            select(SubscriptionPlanModel)
            .where(SubscriptionPlanModel.clash_template_id == row.id)
            .order_by(SubscriptionPlanModel.name)
        ).all()
        defaults = session.scalars(
            select(TemplatePreference)
            .where(TemplatePreference.clash_template_id == row.id)
            .order_by(TemplatePreference.scope)
        ).all()
        return plans, defaults

    def read(self, session, row, actor=None, *, content=False):
        plans, defaults = self.references(session, row)
        data = {
            key: getattr(row, key)
            for key in ("id", "name", "format", "content", "owner_username", "is_public")
        }
        fingerprint = revision(
            {
                **data,
                "plans": [(plan.id, plan.name) for plan in plans],
                "defaults": [pref.scope for pref in defaults],
            }
        )
        return TemplateRead(
            **{key: value for key, value in data.items() if key != "content"},
            content=row.content if content else None,
            revision=fingerprint,
            size_bytes=len(row.content.encode()),
            editable=actor is None
            or (row.owner_username == actor and self.allowed(session, actor)),
            plan_names=[plan.name for plan in plans] if actor is None else [],
            default_scopes=[
                pref.scope for pref in defaults if actor is None or pref.username == actor
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list(self, actor=None):
        with self.inventory._session() as session:
            statement = select(TemplateRecord).order_by(TemplateRecord.name_key)
            if actor is not None:
                self.user(session, actor)
                statement = statement.where(TemplateRecord.owner_username.is_(None))
            return TemplateList(
                templates=[self.read(session, row, actor) for row in session.scalars(statement)],
                settings=self.settings(session, None),
                can_manage=self.allowed(session, actor),
            )

    def detail(self, identifier, actor=None):
        with self.inventory._session() as session:
            return self.read(session, self.get(session, identifier, actor), actor, content=True)

    def write(self, payload, identifier=None, actor=None):
        with self.inventory._coordinated_session() as session:
            if actor is not None:
                raise TemplateForbidden("Only administrators can manage global templates")
            row = self.get(session, identifier, actor, write=True) if identifier else None
            if not self.allowed(session, actor):
                raise TemplateForbidden("Template editing is not permitted")
            if row and self.read(session, row, actor).revision != payload.expected_revision:
                raise TemplateConflict("Template or bindings changed; reload before saving")
            conflict = session.scalar(
                select(TemplateRecord.id).where(TemplateRecord.name_key == payload.name.casefold())
            )
            if conflict and (row is None or conflict != row.id):
                raise TemplateConflict("A template with this filename already exists")
            if row and row.format != payload.format and any(self.references(session, row)):
                raise TemplateConflict("An assigned template cannot change format")
            now = datetime.now(UTC)
            if row is None:
                if session.scalar(select(func.count()).select_from(TemplateRecord)) >= 200:
                    raise TemplateConflict("The template library is limited to 200 files")
                row = TemplateRecord(id=str(uuid4()), created_at=now)
                session.add(row)
            row.name, row.name_key = payload.name, payload.name.casefold()
            row.format, row.content, row.owner_username = "clash", payload.content, None
            row.is_public, row.updated_at = True, now
            session.flush()
            result = self.read(session, row, actor, content=True)
            session.commit()
            return result

    def remove(self, identifier, payload, actor=None):
        with self.inventory._coordinated_session() as session:
            row = self.get(session, identifier, actor, write=True)
            if (
                payload.expected_revision != self.read(session, row, actor).revision
                or payload.confirm_name != row.name
            ):
                raise TemplateConflict("Template changed; reload before removing")
            if any(self.references(session, row)):
                raise TemplateConflict(
                    "Template is assigned; remove its plan and default bindings first"
                )
            session.delete(row)
            session.commit()

    def validate_selection(self, session, payload, existing=None, *, username=None):
        result = {}
        for field in FIELDS:
            identifier = (
                getattr(payload, field)
                if field in payload.model_fields_set or existing is None
                else getattr(existing, field)
            )
            if identifier is not None:
                row = self.get(session, identifier)
                if row.format != field.split("_", 1)[0]:
                    raise TemplateError("Selected template has the wrong format")
            result[field] = str(identifier) if identifier is not None else None
        return result

    def get_settings(self, username=None):
        with self.inventory._session() as session:
            if username is not None:
                self.user(session, username)
            return self.settings(session, None)

    def save_settings(self, payload, username=None, actor=None):
        with self.inventory._coordinated_session() as session:
            if actor is not None or username is not None:
                raise TemplateForbidden(
                    "Only administrators can change the global default template"
                )
            before = self.settings(session, None)
            if before.revision != payload.expected_revision:
                raise TemplateConflict("Template settings changed; reload before saving")
            row = self.preference(session, None)
            values = self.validate_selection(session, payload, existing=row)
            if values["clash_template_id"] is None:
                default = session.scalar(
                    select(TemplateRecord).where(
                        TemplateRecord.name_key == DEFAULT_TEMPLATE_NAME.casefold()
                    )
                )
                if default is None:
                    raise TemplateConflict("The built-in default template is unavailable")
                values["clash_template_id"] = default.id
            if row is None:
                row = TemplatePreference(scope=self.scope(None), username=None, enabled=True)
                session.add(row)
            for field, value in values.items():
                setattr(row, field, value)
            row.enabled = True
            session.flush()
            result = self.settings(session, None)
            session.commit()
            return result

    def resolve(self, session, user, plan, format):
        if format != "clash":
            return None
        field = "clash_template_id"
        system = self.preference(session, None)
        for selection in (plan, system):
            if selection and getattr(selection, field):
                row = self.get(session, getattr(selection, field))
                if row.format != format:
                    raise TemplateError("Template binding has an incompatible format")
                return row
        return None

    def name_for(self, session, identifier):
        return self.get(session, identifier).name if identifier else None

    def id_for(self, session, name, format):
        if name is None:
            return None
        row = session.scalar(
            select(TemplateRecord).where(TemplateRecord.name_key == name.casefold())
        )
        if row is None or row.format != format:
            raise TemplateError("Catalog references a missing or incompatible template: " + name)
        return row.id

    def export_catalog(self, session):
        rows = session.scalars(select(TemplateRecord).order_by(TemplateRecord.name_key)).all()
        system = self.preference(session, None)
        defaults = CatalogTemplateSettings(
            clash_template_name=self.name_for(session, system.clash_template_id if system else None)
        )
        return {
            "templates": [
                TemplateWrite(**{key: getattr(row, key) for key in TemplateWrite.model_fields})
                for row in rows
            ],
            "template_defaults": defaults,
            "template_preferences": [],
        }

    def import_templates(self, session, entries):
        if entries is None:
            return
        if len({entry.name.casefold() for entry in entries}) != len(entries):
            raise TemplateConflict("Catalog template filenames must be distinct")
        if sum(len(entry.content.encode()) for entry in entries) > 16 * 1024 * 1024:
            raise TemplateError("Catalog template content exceeds 16 MiB")
        for entry in entries:
            row = session.scalar(
                select(TemplateRecord).where(TemplateRecord.name_key == entry.name.casefold())
            )
            if row is None:
                row = TemplateRecord(id=str(uuid4()), created_at=datetime.now(UTC))
                session.add(row)
            elif row.format != entry.format and any(self.references(session, row)):
                raise TemplateConflict("An assigned template cannot change format")
            for key in TemplateWrite.model_fields:
                setattr(row, key, getattr(entry, key))
            row.owner_username, row.is_public, row.format = None, True, "clash"
            row.name_key, row.updated_at = entry.name.casefold(), datetime.now(UTC)
        session.flush()
        if session.scalar(select(func.count()).select_from(TemplateRecord)) > 200:
            raise TemplateConflict("The template library is limited to 200 files")

    def import_preferences(self, session, defaults, preferences):
        if preferences:
            raise TemplateConflict("Personal template preferences are no longer supported")
        if defaults is None:
            return
        row = self.preference(session, None)
        if row is None:
            row = TemplatePreference(scope=self.scope(None), username=None)
            session.add(row)
        row.enabled = True
        if defaults.clash_template_name is not None:
            row.clash_template_id = self.id_for(session, defaults.clash_template_name, "clash")
        else:
            fallback = session.scalar(
                select(TemplateRecord).where(
                    TemplateRecord.name_key == DEFAULT_TEMPLATE_NAME.casefold()
                )
            )
            if fallback is None:
                raise TemplateConflict("The built-in default template is unavailable")
            row.clash_template_id = fallback.id

    def ensure_default(self):
        """Create the global all-traffic-through-proxy template exactly once."""
        with self.inventory._coordinated_session() as session:
            now = datetime.now(UTC)
            row = session.get(TemplateRecord, DEFAULT_TEMPLATE_ID)
            if row is None:
                row = session.scalar(
                    select(TemplateRecord).where(
                        TemplateRecord.name_key == DEFAULT_TEMPLATE_NAME.casefold()
                    )
                )
            if row is None:
                row = TemplateRecord(
                    id=DEFAULT_TEMPLATE_ID,
                    name=DEFAULT_TEMPLATE_NAME,
                    name_key=DEFAULT_TEMPLATE_NAME.casefold(),
                    format="clash",
                    content=DEFAULT_CLASH,
                    owner_username=None,
                    is_public=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            preference = self.preference(session, None)
            if preference is None:
                preference = TemplatePreference(
                    scope=self.scope(None),
                    username=None,
                    enabled=True,
                    clash_template_id=row.id,
                )
                session.add(preference)
            elif not preference.clash_template_id:
                preference.clash_template_id = row.id
                preference.enabled = True
            session.commit()
