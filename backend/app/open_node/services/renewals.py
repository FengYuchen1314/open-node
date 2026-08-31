"""Manual renewal review, committed atomically with durable access reconciliation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.renewals import (
    AccountRenewalsResponse,
    RenewalCreate,
    RenewalDecision,
    RenewalDecisionResponse,
    RenewalError,
    RenewalRead,
    RenewalsResponse,
)
from open_node.services.auth import password_hash
from open_node.services.inventory import Base, ProductUserModel, SubscriptionPlanModel
from open_node.services.subscription_access import SubscriptionAccessConflict


class RenewalRequestModel(Base):
    __tablename__ = "renewal_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    # NULL for terminal requests. A portable unique constraint also protects
    # the single-pending-request invariant if callers race across processes.
    pending_username: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    plan_id: Mapped[str] = mapped_column(String(36))
    plan_name: Mapped[str] = mapped_column(String(120))
    previous_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renew_days: Mapped[int] = mapped_column(Integer)
    passphrase_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    new_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RenewalStore:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(row):
        return RenewalRead(**{
            name: (value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None
                   else value)
            for name in RenewalRead.model_fields for value in [getattr(row, name)]
        })

    @staticmethod
    def _get(session, identifier, username=None):
        query = select(RenewalRequestModel).where(RenewalRequestModel.id == str(identifier))
        if username is not None:
            query = query.where(RenewalRequestModel.username == username)
        row = session.scalar(query.with_for_update())
        if row is None:
            raise RenewalError("renewal_not_found", 404)
        return row

    @staticmethod
    def _user_plan(session, username):
        user = session.scalar(
            select(ProductUserModel).where(ProductUserModel.username == username).with_for_update()
        )
        if user is None:
            raise RenewalError("renewal_not_found", 404)
        plan = (
            session.get(SubscriptionPlanModel, user.current_plan_id)
            if user.current_plan_id else None
        )
        eligible = bool(user.is_active and not user.removal_id and plan and plan.cycle_days > 0)
        return user, plan, eligible

    @staticmethod
    def _page(session, *, username=None, status=None, limit=50, offset=0):
        query = select(RenewalRequestModel)
        if username is not None:
            query = query.where(RenewalRequestModel.username == username)
        if status is not None:
            query = query.where(RenewalRequestModel.status == status)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.scalars(
            query.order_by(RenewalRequestModel.created_at.desc(), RenewalRequestModel.id.desc())
            .limit(limit).offset(offset)
        ).all()
        return RenewalsResponse(
            requests=[RenewalStore._read(row) for row in rows],
            total=total, limit=limit, offset=offset,
        )

    def list(self, *, username=None, status=None, limit=50, offset=0):
        with self.store._session() as session:
            return self._page(session, username=username, status=status, limit=limit, offset=offset)

    def account(self, username, *, limit=20, offset=0):
        with self.store._session() as session:
            user, plan, eligible = self._user_plan(session, username)
            pending = session.scalar(select(RenewalRequestModel.id).where(
                RenewalRequestModel.pending_username == username
            ))
            code = "renewal_unavailable" if not eligible else "renewal_pending" if pending else None
            page = self._page(session, username=username, limit=limit, offset=offset)
            return AccountRenewalsResponse(
                **page.model_dump(), eligible=eligible and not pending, unavailable_code=code,
                plan_id=plan.id if plan else None, plan_name=plan.name if plan else None,
                renew_days=plan.cycle_days if plan else None,
                plan_expires_at=(self.store._aware_datetime(user.plan_expires_at)
                                 if user.plan_expires_at else None),
            )

    def get(self, identifier, *, username=None):
        with self.store._session() as session:
            return self._read(self._get(session, identifier, username))

    def submit(self, username, payload: RenewalCreate):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user, plan, eligible = self._user_plan(session, username)
            existing = session.get(RenewalRequestModel, str(payload.request_id))
            if existing is not None:
                if existing.username != username:
                    raise RenewalError("renewal_not_found", 404)
                # A repeated request ID always refers to the original request,
                # including after approval. It never extends the package again.
                return self._read(existing)
            if not eligible:
                raise RenewalError("renewal_unavailable")
            if session.scalar(select(RenewalRequestModel.id).where(
                RenewalRequestModel.pending_username == username
            )):
                raise RenewalError("renewal_pending")
            recent = session.scalar(select(func.count()).select_from(RenewalRequestModel).where(
                RenewalRequestModel.username == username,
                RenewalRequestModel.created_at >= now - timedelta(days=1),
            ))
            if recent >= 20:
                raise RenewalError("renewal_rate_limited", 429)
            row = RenewalRequestModel(
                id=str(payload.request_id), username=username, pending_username=username,
                plan_id=plan.id, plan_name=plan.name, previous_end_date=user.plan_expires_at,
                renew_days=plan.cycle_days,
                passphrase_hash=password_hash.hash(
                    "renewal:" + payload.passphrase.get_secret_value()
                ),
                status="pending", created_at=now, reviewed_at=None, reviewed_by=None,
                new_end_date=None,
            )
            session.add(row)
            session.flush()
            result = self._read(row)
            session.commit()
            return result

    @staticmethod
    def _finish(row, status, now, reviewer=None):
        row.status, row.reviewed_at, row.reviewed_by = status, now, reviewer
        row.pending_username = row.passphrase_hash = None

    def cancel(self, identifier, username):
        with self.store._coordinated_session() as session:
            row = self._get(session, identifier, username)
            if row.status == "cancelled":
                return self._read(row)
            if row.status != "pending":
                raise RenewalError("renewal_conflict")
            self._finish(row, "cancelled", datetime.now(UTC))
            result = self._read(row)
            session.commit()
            return result

    def review(self, identifier, payload: RenewalDecision, reviewer):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            row = self._get(session, identifier)
            target = "approved" if payload.decision == "approve" else "rejected"
            if row.status == target:
                return RenewalDecisionResponse(request=self._read(row), processed=False)
            if row.status != "pending":
                raise RenewalError("renewal_conflict")
            commands, warnings = [], []
            if target == "approved":
                if not row.passphrase_hash or not password_hash.verify(
                    "renewal:" + payload.passphrase.get_secret_value(), row.passphrase_hash
                ):
                    raise RenewalError("renewal_wrong_passphrase", 400)
                user, plan, eligible = self._user_plan(session, row.username)
                if not eligible or user.current_plan_id != row.plan_id:
                    raise RenewalError("renewal_conflict")
                base = max([now] + [self.store._aware_datetime(value) for value in (
                    user.plan_expires_at, row.previous_end_date
                ) if value is not None])
                try:
                    user.plan_expires_at = base + timedelta(days=row.renew_days)
                except OverflowError:
                    raise RenewalError("renewal_conflict") from None
                # Keep existing traffic counters, reset policy, manual disable
                # state and credentials. Renewal grants time, not extra quota.
                user.plan_started_at = user.plan_started_at or now
                user.updated_at = now
                try:
                    batches, warnings = self.store._subscription_provision_batches(
                        session, user, plan, no_restart=False
                    )
                    access = self.store._subscription_access()
                    access.authorize(session, user, plan, batches, now)
                    commands = access.reconcile(session, now, username=user.username)
                except SubscriptionAccessConflict:
                    raise RenewalError("renewal_access_conflict") from None
                row.new_end_date = user.plan_expires_at
            self._finish(row, target, now, reviewer)
            session.flush()
            result = RenewalDecisionResponse(
                request=self._read(row), processed=True,
                commands=[self.store._command_read(command) for command in commands],
                warnings=warnings,
            )
            session.commit()
            return result
