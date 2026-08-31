"""Owner-scoped external sources, encrypted snapshots and explicit atomic confirmation.

External proxies are not managed nodes: this service never provisions credentials,
queues Agent commands or changes the local traffic ledger.
"""

import hashlib
import hmac
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.external_subscriptions import (
    ExternalConfirmationRead,
    ExternalNodeRead,
    ExternalNodeUpdate,
    ExternalPreviewConfirm,
    ExternalPreviewNode,
    ExternalPreviewRead,
    ExternalRefreshRead,
    ExternalRefreshUpdate,
    ExternalSourceCreate,
    ExternalSourceDelete,
    ExternalSourceDetail,
    ExternalSourceRead,
    ExternalSourceUpdate,
)
from open_node.domain.subscriptions import SubscriptionFormatNode
from open_node.services.certificate_vault import CertificateVault
from open_node.services.external_fetch import ExternalFetchError, fetch_external_subscription
from open_node.services.external_subscription_parser import (
    ExternalSubscriptionParseError,
    parse_external_subscription,
)
from open_node.services.inventory import Base, ProductUserModel

DEFAULT_USER_AGENT = "clash-meta/2.4.0"
PREVIEW_LIFETIME = timedelta(minutes=15)
RECEIPT_LIFETIME = timedelta(days=7)
MAX_PENDING_PREVIEWS = 3
MAX_SOURCES_PER_OWNER = 100
MAX_SAVED_NODES = 2000


class ExternalSubscriptionError(ValueError):
    status_code = 422


class ExternalSubscriptionNotFound(ExternalSubscriptionError):
    status_code = 404


class ExternalSubscriptionConflict(ExternalSubscriptionError):
    status_code = 409


class ExternalSubscriptionUnavailable(ExternalSubscriptionError):
    status_code = 503


class ExternalSourceModel(Base):
    __tablename__ = "external_subscription_sources"
    __table_args__ = (
        UniqueConstraint("owner_username", "url_digest", name="uq_external_owner_url"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    url_digest: Mapped[str] = mapped_column(String(64))
    secret: Mapped[str] = mapped_column(Text)
    has_custom_user_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    upstream_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExternalNodeModel(Base):
    __tablename__ = "external_subscription_nodes"
    __table_args__ = (
        UniqueConstraint("source_id", "upstream_name", name="uq_external_source_node"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("external_subscription_sources.id", ondelete="CASCADE"), index=True
    )
    upstream_name: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str | None] = mapped_column(String(160))
    protocol: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean)
    present: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String(300))
    secret: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExternalPreviewModel(Base):
    __tablename__ = "external_subscription_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("external_subscription_sources.id", ondelete="CASCADE"), index=True
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    secret: Mapped[str | None] = mapped_column(Text)
    # Safe display information remains available for a retry after a lost response.
    display: Mapped[dict] = mapped_column(JSON)
    selection_digest: Mapped[str | None] = mapped_column(String(64))
    receipt: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExternalRefreshModel(Base):
    """One durable schedule per source; no network work is done in a DB transaction."""

    __tablename__ = "external_subscription_refresh"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("external_subscription_sources.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    scope: Mapped[str] = mapped_column(String(16), default="saved_only")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code: Mapped[str] = mapped_column(String(32), default="never")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _utc(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class ExternalSubscriptions:
    def __init__(self, store, *, fetcher=None, parser=None):
        self.store = store
        self.fetcher = fetcher or fetch_external_subscription
        self.parser = parser or parse_external_subscription

    @contextmanager
    def _write(self):
        # Only short database work occurs here. DNS, network I/O and parsing are
        # completed before taking this write transaction.
        with self.store._session() as session:
            if self.store._engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except IntegrityError:
                session.rollback()
                raise ExternalSubscriptionConflict(
                    "External source changed or already exists; reload before retrying"
                ) from None

    def _source(self, session, identifier, *, writable=False, owner_username=None):
        query = select(ExternalSourceModel).where(ExternalSourceModel.id == str(identifier))
        if owner_username is not None:
            query = query.where(ExternalSourceModel.owner_username == owner_username)
        if writable:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise ExternalSubscriptionNotFound("External source not found")
        self._owner(session, row.owner_username, writable=writable)
        return row

    @staticmethod
    def _owner(session, username, *, writable=False):
        query = select(ProductUserModel).where(ProductUserModel.username == username)
        if writable:
            query = query.with_for_update()
        owner = session.scalar(query)
        if owner is None:
            raise ExternalSubscriptionNotFound("Subscriber not found")
        if writable and owner.removal_id:
            raise ExternalSubscriptionConflict("Subscriber removal is in progress")
        return owner

    @staticmethod
    def _expected(source, expected):
        if source.revision != expected:
            raise ExternalSubscriptionConflict("External source changed; reload its preview")

    def _bump(self, session, source, expected, now):
        self._expected(source, expected)
        result = session.execute(
            update(ExternalSourceModel)
            .where(ExternalSourceModel.id == source.id, ExternalSourceModel.revision == expected)
            .values(revision=expected + 1, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ExternalSubscriptionConflict("External source changed; reload its preview")
        source.revision = expected + 1
        source.updated_at = now

    def _keys(self, session):
        root = self.store.external_subscriptions_state_dir
        if root is None:
            raise ExternalSubscriptionUnavailable(
                "Configure a private external subscription state directory before use"
            )
        first = session.scalar(
            select(ExternalSourceModel).order_by(ExternalSourceModel.id).limit(1)
        )
        vault = CertificateVault(Path(root), initialized=first is not None)
        try:
            vault.cipher()
            with vault.lock("vault.lock"):
                key = vault.read(vault.root / "vault.key", 128)
                cipher = Fernet(key)
            if first is not None:
                self._open(cipher, first, "source", first.secret)
            return cipher, key
        except (InvalidToken, OSError, ValueError, TypeError):
            raise ExternalSubscriptionUnavailable(
                "External subscription encryption key is unavailable; restore the original key"
            ) from None

    @staticmethod
    def _seal(cipher, source, purpose, value):
        return cipher.encrypt(
            _canonical(
                {
                    "version": 1,
                    "owner": source.owner_username,
                    "source": source.id,
                    "purpose": purpose,
                    "value": value,
                }
            )
        ).decode()

    @staticmethod
    def _open(cipher, source, purpose, secret):
        try:
            value = json.loads(cipher.decrypt(secret.encode()))
            if not isinstance(value, dict) or any(
                value.get(key) != expected
                for key, expected in (
                    ("version", 1),
                    ("owner", source.owner_username),
                    ("source", source.id),
                    ("purpose", purpose),
                )
            ):
                raise ValueError()
            return value["value"]
        except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
            raise ExternalSubscriptionUnavailable(
                "External subscription credentials are unavailable; restore their original state"
            ) from None

    @staticmethod
    def _url_digest(key, url):
        return hmac.new(
            key, b"open-node/external-url/v1\0" + url.encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _nodes(session, source_id):
        return list(
            session.scalars(
                select(ExternalNodeModel)
                .where(ExternalNodeModel.source_id == source_id)
                .order_by(ExternalNodeModel.created_at, ExternalNodeModel.id)
            )
        )

    @staticmethod
    def _node_read(source, row):
        reason = (
            "External source is disabled"
            if not source.enabled
            else "Node is disabled"
            if not row.enabled
            else "Node is missing from the latest confirmed refresh"
            if not row.present
            else row.reason or ("Node configuration is unavailable" if row.secret is None else None)
        )
        return ExternalNodeRead(
            id=row.id,
            source_id=source.id,
            upstream_name=row.upstream_name,
            name=row.display_name or row.upstream_name,
            protocol=row.protocol,
            enabled=row.enabled,
            present=row.present,
            available=reason is None,
            reason=reason,
        )

    def _read(self, session, source, nodes=None):
        nodes = self._nodes(session, source.id) if nodes is None else nodes
        return ExternalSourceRead(
            id=source.id,
            owner_username=source.owner_username,
            name=source.name,
            enabled=source.enabled,
            revision=source.revision,
            has_custom_user_agent=source.has_custom_user_agent,
            node_count=len(nodes),
            available_node_count=sum(self._node_read(source, node).available for node in nodes),
            metadata=source.upstream_metadata or {},
            last_synced_at=_utc(source.last_synced_at),
            created_at=_utc(source.created_at),
            updated_at=_utc(source.updated_at),
            refresh=self._refresh_read(session, source),
        )

    def _refresh_read(self, session, source):
        row = session.get(ExternalRefreshModel, source.id)
        if row is None:
            return ExternalRefreshRead()
        owner = self._owner(session, source.owner_username)
        paused = not source.enabled or not owner.is_active or bool(owner.removal_id)
        return ExternalRefreshRead(
            enabled=row.enabled, interval_minutes=row.interval_minutes, scope=row.scope,
            paused=paused,
            running=bool(row.lease_id and _utc(row.lease_until) > datetime.now(UTC)),
            next_run_at=_utc(row.next_run_at) if row.enabled and not paused else None,
            last_attempt_at=_utc(row.last_attempt_at),
            last_finished_at=_utc(row.last_finished_at),
            last_success_at=_utc(row.last_success_at), code=row.code,
            consecutive_failures=row.consecutive_failures, **(row.counts or {}),
        )

    def update_refresh(self, identifier, payload: ExternalRefreshUpdate, *, owner_username=None):
        now = datetime.now(UTC)
        with self._write() as session:
            source = self._source(
                session, identifier, writable=True, owner_username=owner_username
            )
            self._expected(source, payload.expected_revision)
            row = session.get(ExternalRefreshModel, source.id)
            if row is None:
                row = ExternalRefreshModel(source_id=source.id)
                session.add(row)
            row.enabled = payload.enabled
            row.interval_minutes = payload.interval_minutes
            row.scope = payload.scope
            # Saving does not fetch. First execution is due one interval later.
            row.next_run_at = now + timedelta(minutes=row.interval_minutes) if row.enabled else None
            row.lease_id = row.lease_until = None
            row.consecutive_failures = 0
            self._bump(session, source, payload.expected_revision, now)
            session.flush()
            return self._read(session, source)

    def list(self, *, owner_username=None):
        with self.store._session() as session:
            query = select(ExternalSourceModel).order_by(
                ExternalSourceModel.created_at, ExternalSourceModel.id
            )
            if owner_username is not None:
                self._owner(session, owner_username)
                query = query.where(ExternalSourceModel.owner_username == owner_username)
            sources = session.scalars(query)
            return [self._read(session, source) for source in sources]

    def detail(self, identifier, *, owner_username=None):
        with self.store._session() as session:
            source = self._source(session, identifier, owner_username=owner_username)
            nodes = self._nodes(session, source.id)
            return ExternalSourceDetail(
                source=self._read(session, source, nodes),
                nodes=[self._node_read(source, node) for node in nodes],
            )

    def create(self, payload: ExternalSourceCreate, *, owner_username=None):
        if owner_username is not None and payload.owner_username != owner_username:
            raise ExternalSubscriptionNotFound("External source not found")
        now = datetime.now(UTC)
        with self._write() as session:
            self._owner(session, payload.owner_username, writable=True)
            count = session.scalar(
                select(func.count())
                .select_from(ExternalSourceModel)
                .where(ExternalSourceModel.owner_username == payload.owner_username)
            )
            if count >= MAX_SOURCES_PER_OWNER:
                raise ExternalSubscriptionConflict("Subscriber external source limit reached")
            cipher, key = self._keys(session)
            url = payload.url.get_secret_value()
            user_agent = payload.user_agent.get_secret_value() or DEFAULT_USER_AGENT
            source = ExternalSourceModel(
                id=str(uuid4()),
                owner_username=payload.owner_username,
                name=payload.name,
                enabled=payload.enabled,
                revision=1,
                url_digest=self._url_digest(key, url),
                has_custom_user_agent=user_agent != DEFAULT_USER_AGENT,
                upstream_metadata={},
                created_at=now,
                updated_at=now,
            )
            source.secret = self._seal(
                cipher, source, "source", {"url": url, "user_agent": user_agent}
            )
            session.add(source)
            session.flush()
            return self._read(session, source, [])

    def update(self, identifier, payload: ExternalSourceUpdate, *, owner_username=None):
        now = datetime.now(UTC)
        with self._write() as session:
            source = self._source(
                session, identifier, writable=True, owner_username=owner_username
            )
            self._expected(source, payload.expected_revision)
            if payload.url is not None or payload.user_agent is not None:
                cipher, key = self._keys(session)
                secret = self._open(cipher, source, "source", source.secret)
                if payload.url is not None:
                    if secret["url"] != payload.url.get_secret_value():
                        schedule = session.get(ExternalRefreshModel, source.id)
                        if schedule:
                            schedule.enabled = False
                            schedule.next_run_at = schedule.lease_id = schedule.lease_until = None
                    secret["url"] = payload.url.get_secret_value()
                if payload.user_agent is not None:
                    secret["user_agent"] = (
                        payload.user_agent.get_secret_value() or DEFAULT_USER_AGENT
                    )
                source.secret = self._seal(cipher, source, "source", secret)
                source.url_digest = self._url_digest(key, secret["url"])
                source.has_custom_user_agent = secret["user_agent"] != DEFAULT_USER_AGENT
            source.name = payload.name
            source.enabled = payload.enabled
            self._bump(session, source, payload.expected_revision, now)
            session.flush()
            return self._read(session, source)

    def delete(self, identifier, payload: ExternalSourceDelete, *, owner_username=None):
        with self._write() as session:
            source = self._source(
                session, identifier, writable=True, owner_username=owner_username
            )
            self._expected(source, payload.expected_revision)
            session.delete(source)

    def update_node(self, source_id, node_id, payload: ExternalNodeUpdate, *, owner_username=None):
        now = datetime.now(UTC)
        with self._write() as session:
            source = self._source(
                session, source_id, writable=True, owner_username=owner_username
            )
            self._expected(source, payload.expected_revision)
            row = session.get(ExternalNodeModel, str(node_id))
            if row is None or row.source_id != source.id:
                raise ExternalSubscriptionNotFound("External node not found")
            row.display_name = payload.name if payload.name != row.upstream_name else None
            row.enabled = payload.enabled
            row.updated_at = now
            self._bump(session, source, payload.expected_revision, now)
            session.flush()
            nodes = self._nodes(session, source.id)
            return ExternalSourceDetail(
                source=self._read(session, source, nodes),
                nodes=[self._node_read(source, node) for node in nodes],
            )

    def _purge_expired(self, session, source_id, now):
        session.execute(
            delete(ExternalPreviewModel).where(
                ExternalPreviewModel.source_id == source_id,
                ExternalPreviewModel.applied_at.is_(None),
                ExternalPreviewModel.expires_at <= now,
            )
        )
        session.execute(
            delete(ExternalPreviewModel).where(
                ExternalPreviewModel.source_id == source_id,
                ExternalPreviewModel.applied_at < now - RECEIPT_LIFETIME,
            )
        )

    def prepare_preview(self, identifier, expected_revision, *, owner_username=None):
        # Snapshot the immutable source ID and revision, then close the session.
        # A late response cannot upsert a deleted source or transfer it to a
        # same-name subscriber recreated while this request was in flight.
        with self.store._session() as session:
            source = self._source(session, identifier, owner_username=owner_username)
            self._owner(session, source.owner_username, writable=True)
            self._expected(source, expected_revision)
            cipher, _key = self._keys(session)
            secret = self._open(cipher, source, "source", source.secret)
        try:
            fetched = self.fetcher(secret["url"], user_agent=secret["user_agent"])
            parsed = self.parser(fetched.body)
        except (ExternalFetchError, ExternalSubscriptionParseError) as exc:
            raise ExternalSubscriptionError(str(exc)) from None
        now = datetime.now(UTC)
        with self._write() as session:
            source = self._source(
                session, identifier, writable=True, owner_username=owner_username
            )
            self._expected(source, expected_revision)
            self._purge_expired(session, source.id, now)
            count = session.scalar(
                select(func.count())
                .select_from(ExternalPreviewModel)
                .where(
                    ExternalPreviewModel.source_id == source.id,
                    ExternalPreviewModel.applied_at.is_(None),
                )
            )
            if count >= MAX_PENDING_PREVIEWS:
                raise ExternalSubscriptionConflict(
                    "Cancel an existing preview before fetching again"
                )
            cipher, _key = self._keys(session)
            existing = {row.upstream_name: row for row in self._nodes(session, source.id)}
            candidates, display = [], []
            seen = set()
            for entry in parsed:
                seen.add(entry.name)
                old = existing.get(entry.name)
                node_id = old.id if old else str(uuid5(UUID(source.id), "node:" + entry.name))
                config = deepcopy(entry.config)
                candidate = {
                    "id": node_id,
                    "name": entry.name,
                    "protocol": entry.protocol,
                    "config": config,
                    "reason": entry.reason,
                }
                candidates.append(candidate)
                old_config = (
                    self._open(cipher, source, "node:" + old.id, old.secret)
                    if old and old.secret
                    else None
                )
                changed_fields = (
                    sorted(
                        key
                        for key in set(old_config or {}) | set(config or {})
                        if (old_config or {}).get(key) != (config or {}).get(key)
                    )
                    if old and config is not None
                    else []
                )
                changed = bool(
                    old and (old_config != config or old.reason != entry.reason or not old.present)
                )
                change = (
                    "unavailable"
                    if config is None or entry.reason
                    else "new"
                    if old is None
                    else "updated"
                    if changed
                    else "unchanged"
                )
                display.append(
                    ExternalPreviewNode(
                        node_id=node_id,
                        upstream_name=entry.name,
                        name=(old.display_name or entry.name) if old else entry.name,
                        protocol=entry.protocol,
                        change=change,
                        existing=old is not None,
                        selectable=old is None and config is not None and entry.reason is None,
                        reason=entry.reason,
                        changed_fields=changed_fields,
                    )
                )
            for name, old in existing.items():
                if name not in seen:
                    display.append(
                        ExternalPreviewNode(
                            node_id=old.id,
                            upstream_name=name,
                            name=old.display_name or name,
                            protocol=old.protocol,
                            change="missing",
                            existing=True,
                            selectable=False,
                            reason=(
                                "Node will be unavailable until a later confirmed "
                                "refresh restores it"
                            ),
                        )
                    )
            preview = ExternalPreviewModel(
                id=str(uuid4()),
                source_id=source.id,
                source_revision=source.revision,
                created_at=now,
                expires_at=now + PREVIEW_LIFETIME,
                display={
                    "metadata": fetched.metadata,
                    "nodes": [node.model_dump(mode="json") for node in display],
                },
            )
            preview.secret = self._seal(
                cipher,
                source,
                "preview:" + preview.id,
                {"nodes": candidates, "metadata": fetched.metadata},
            )
            session.add(preview)
            session.flush()
            return self._preview_read(preview)

    @staticmethod
    def _preview_read(preview):
        return ExternalPreviewRead(
            id=preview.id,
            source_id=preview.source_id,
            source_revision=preview.source_revision,
            created_at=_utc(preview.created_at),
            expires_at=_utc(preview.expires_at),
            metadata=preview.display["metadata"],
            nodes=preview.display["nodes"],
            receipt=preview.receipt,
        )

    def _preview(self, session, source, preview_id):
        row = session.get(ExternalPreviewModel, str(preview_id))
        if row is None or row.source_id != source.id:
            raise ExternalSubscriptionNotFound("External subscription preview not found")
        now = datetime.now(UTC)
        if row.applied_at is not None and (
            self.store._aware_datetime(row.applied_at) + RECEIPT_LIFETIME <= now
        ):
            raise ExternalSubscriptionNotFound("External subscription preview not found")
        if row.applied_at is None and self.store._aware_datetime(row.expires_at) <= now:
            raise ExternalSubscriptionConflict("External subscription preview expired; fetch again")
        return row

    def preview(self, source_id, preview_id, *, owner_username=None):
        with self.store._session() as session:
            source = self._source(session, source_id, owner_username=owner_username)
            return self._preview_read(self._preview(session, source, preview_id))

    def cancel_preview(self, source_id, preview_id, *, owner_username=None):
        with self._write() as session:
            source = self._source(
                session, source_id, writable=True, owner_username=owner_username
            )
            if owner_username is None:
                row = session.get(ExternalPreviewModel, str(preview_id))
            else:
                row = session.scalar(select(ExternalPreviewModel).where(
                    ExternalPreviewModel.id == str(preview_id),
                    ExternalPreviewModel.source_id == source.id,
                ))
            if row is None:
                if owner_username is not None:
                    raise ExternalSubscriptionNotFound("External subscription preview not found")
                return
            if row.source_id != source.id:
                raise ExternalSubscriptionNotFound("External subscription preview not found")
            if row.applied_at is not None:
                raise ExternalSubscriptionConflict(
                    "Preview is already confirmed; its receipt is retained"
                )
            session.delete(row)

    def confirm(
        self, source_id, preview_id, payload: ExternalPreviewConfirm, *, owner_username=None
    ):
        now = datetime.now(UTC)
        selected = {str(identifier) for identifier in payload.selected_node_ids}
        selection_digest = hashlib.sha256(
            _canonical(
                {
                    "revision": payload.expected_revision,
                    "selected": sorted(selected),
                    "accepted": payload.accept_changes,
                }
            )
        ).hexdigest()
        with self._write() as session:
            source = self._source(
                session, source_id, writable=True, owner_username=owner_username
            )
            preview = self._preview(session, source, preview_id)
            if preview.applied_at is not None:
                if preview.selection_digest != selection_digest:
                    raise ExternalSubscriptionConflict(
                        "Preview was confirmed with a different selection"
                    )
                return ExternalConfirmationRead.model_validate(preview.receipt)
            self._expected(source, payload.expected_revision)
            if preview.source_revision != source.revision:
                raise ExternalSubscriptionConflict("External source changed after this preview")
            selectable = {
                entry["node_id"] for entry in preview.display["nodes"] if entry["selectable"]
            }
            if not selected <= selectable:
                raise ExternalSubscriptionConflict(
                    "Selection contains a node not offered by this preview"
                )
            cipher, _key = self._keys(session)
            snapshot = self._open(cipher, source, "preview:" + preview.id, preview.secret)
            counts = self._apply_snapshot(session, source, cipher, snapshot["nodes"], selected, now)
            source.upstream_metadata = snapshot["metadata"]
            source.last_synced_at = preview.created_at
            self._bump(session, source, payload.expected_revision, now)
            receipt = ExternalConfirmationRead(
                source_id=source.id,
                preview_id=preview.id,
                revision=source.revision,
                **counts,
                applied_at=now,
            )
            preview.applied_at = now
            preview.selection_digest = selection_digest
            preview.receipt = receipt.model_dump(mode="json")
            preview.secret = None
            return receipt

    def _apply_snapshot(self, session, source, cipher, entries, selected, now):
        """Shared atomic merge for a confirmed preview or explicitly enabled schedule."""
        existing = {row.id: row for row in self._nodes(session, source.id)}
        if len(existing) + len(selected) > MAX_SAVED_NODES:
            raise ExternalSubscriptionConflict("External source saved-node limit reached")
        incoming = {entry["id"]: entry for entry in entries}
        imported = updated = missing = 0
        for node_id, old in existing.items():
            entry = incoming.get(node_id)
            if entry is None:
                if old.present:
                    missing += 1
                old.present = False
                old.updated_at = now
                continue
            old_config = (
                self._open(cipher, source, "node:" + old.id, old.secret) if old.secret else None
            )
            if old_config != entry["config"] or old.reason != entry["reason"] or not old.present:
                updated += 1
            old.protocol = entry["protocol"]
            old.present = True
            old.reason = entry["reason"]
            old.secret = (
                self._seal(cipher, source, "node:" + old.id, entry["config"])
                if entry["config"] is not None else None
            )
            old.updated_at = now
        for node_id in sorted(selected):
            entry = incoming[node_id]
            if entry["config"] is None or entry["reason"]:
                raise ExternalSubscriptionConflict("Selected external node is unavailable")
            session.add(ExternalNodeModel(
                id=node_id, source_id=source.id, upstream_name=entry["name"],
                protocol=entry["protocol"], enabled=True, present=True,
                secret=self._seal(cipher, source, "node:" + node_id, entry["config"]),
                created_at=now, updated_at=now,
            ))
            imported += 1
        return dict(imported_count=imported, updated_count=updated, missing_count=missing)

    def subscription_candidates(self, session, username):
        sources = session.scalars(
            select(ExternalSourceModel)
            .where(ExternalSourceModel.owner_username == username)
            .order_by(ExternalSourceModel.created_at, ExternalSourceModel.id)
        ).all()
        if not sources:
            return [], [], []
        candidates, unavailable = [], []
        cipher = None
        for source in sources:
            for node in self._nodes(session, source.id):
                state = self._node_read(source, node)
                if not state.available:
                    unavailable.append(
                        SubscriptionFormatNode(
                            node_id=node.id,
                            name=state.name,
                            protocol=node.protocol,
                            available=False,
                            reason=state.reason,
                        )
                    )
                    continue
                if cipher is None:
                    cipher, _key = self._keys(session)
                proxy = self._open(cipher, source, "node:" + node.id, node.secret)
                if not isinstance(proxy, dict):
                    raise ExternalSubscriptionUnavailable(
                        "External node configuration is unavailable"
                    )
                proxy["name"] = state.name
                candidates.append((node.id, proxy))
        warnings = (
            ["External source traffic is upstream metadata, not part of local usage accounting"]
            if candidates
            else []
        )
        return candidates, unavailable, warnings
