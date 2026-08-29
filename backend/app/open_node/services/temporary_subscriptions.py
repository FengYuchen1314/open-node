"""Durable, access-limited subscription shares backed by an existing subscriber."""

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import delete, select

from open_node.domain.temporary_subscriptions import TemporarySubscriptionRead
from open_node.services.inventory import (
    ProductUserModel,
    SubscriptionUnavailableError,
    TemporarySubscriptionModel,
)


class TemporarySubscriptionNotFoundError(ValueError):
    pass


class TemporarySubscriptionConflict(ValueError):
    pass


class TemporarySubscriptions:
    def __init__(self, store):
        self.store = store

    def _read(self, row, url_for, now=None):
        active_now = now or datetime.now(UTC)
        status = (
            "expired"
            if active_now >= self.store._aware_datetime(row.expires_at)
            else "exhausted"
            if row.access_count >= row.max_access
            else "active"
        )
        return TemporarySubscriptionRead(
            id=row.id,
            username=row.username,
            label=row.label,
            node_ids=row.node_ids or [],
            max_access=row.max_access,
            access_count=row.access_count,
            expires_at=row.expires_at,
            status=status,
            subscription_url=str(url_for("render_temporary_subscription", code=row.code)),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list(self, url_for):
        now = datetime.now(UTC)
        with self.store._session() as session:
            rows = session.scalars(
                select(TemporarySubscriptionModel).order_by(
                    TemporarySubscriptionModel.created_at.desc(),
                    TemporarySubscriptionModel.id,
                )
            ).all()
            return [self._read(row, url_for, now) for row in rows]

    def create(self, payload, url_for):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user = session.get(ProductUserModel, payload.username)
            try:
                plan = self.store._available_subscription_plan(session, user)
            except SubscriptionUnavailableError as exc:
                raise TemporarySubscriptionConflict(str(exc)) from exc
            node_ids = [str(node_id) for node_id in payload.node_ids]
            self.store._ensure_managed_nodes_exist(session, payload.node_ids)
            if not set(node_ids).issubset(plan.node_ids or []):
                raise TemporarySubscriptionConflict(
                    "temporary subscription nodes must belong to the subscriber's current plan"
                )
            code = ""
            for _ in range(16):
                candidate = token_urlsafe(24)
                if (
                    session.scalar(
                        select(TemporarySubscriptionModel.id).where(
                            TemporarySubscriptionModel.code == candidate
                        )
                    )
                    is None
                ):
                    code = candidate
                    break
            if not code:
                raise TemporarySubscriptionConflict("unable to allocate temporary subscription")
            row = TemporarySubscriptionModel(
                id=str(uuid4()),
                code=code,
                username=user.username,
                label=payload.label,
                node_ids=node_ids,
                max_access=payload.max_access,
                access_count=0,
                expires_at=now + timedelta(seconds=payload.expires_in_seconds),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            result = self._read(row, url_for, now)
            session.commit()
            return result

    def delete(self, identifier):
        with self.store._coordinated_session() as session:
            row = session.get(TemporarySubscriptionModel, str(identifier))
            if row is None:
                raise TemporarySubscriptionNotFoundError("temporary subscription not found")
            session.execute(
                delete(TemporarySubscriptionModel).where(
                    TemporarySubscriptionModel.id == str(identifier)
                )
            )
            session.commit()

    def render(self, code, client_format, node_id=None):
        key = code.strip()
        if not key:
            raise TemporarySubscriptionNotFoundError("temporary subscription not found")
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            row = session.scalar(
                select(TemporarySubscriptionModel).where(TemporarySubscriptionModel.code == key)
            )
            if (
                row is None
                or now >= self.store._aware_datetime(row.expires_at)
                or row.access_count >= row.max_access
            ):
                raise TemporarySubscriptionNotFoundError("temporary subscription not found")
            user = session.get(ProductUserModel, row.username)
            plan = self.store._available_subscription_plan(session, user)
            selected = set(row.node_ids or [])
            if not selected or not selected.issubset(plan.node_ids or []):
                raise SubscriptionUnavailableError(
                    "temporary subscription nodes are outside the current plan"
                )
            rendered = self.store._render_user_subscription(
                session,
                user,
                plan,
                client_format,
                node_id=node_id,
                selected_node_ids=selected,
                title=row.label,
                include_userinfo=False,
            )
            row.access_count += 1
            row.updated_at = now
            session.commit()
            return rendered
