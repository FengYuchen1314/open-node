from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import func, select

from open_node.domain.registration_invitations import (
    RegistrationClaim,
    RegistrationClaimResponse,
    RegistrationInvitationCreate,
    RegistrationInvitationRead,
    RegistrationInvitationsResponse,
    RegistrationInvitationStatus,
)
from open_node.services.auth import password_hash
from open_node.services.inventory import (
    ProductUserModel,
    RegistrationInvitationModel,
    SubscriptionPlanModel,
    SubscriptionPlanNotFoundError,
)
from open_node.services.subscriber_auth import SubscriberAccount


class RegistrationInvitationConflict(ValueError):
    pass


class RegistrationInvitationUnavailable(ValueError):
    pass


@dataclass
class IssuedRegistrationInvitation:
    invitation: RegistrationInvitationRead
    token: str


class RegistrationInvitations:
    def __init__(self, store):
        self.store = store

    def _status(self, row, now):
        if row.used_at is not None:
            return RegistrationInvitationStatus.USED
        if row.revoked_at is not None:
            return RegistrationInvitationStatus.REVOKED
        if self.store._aware_datetime(row.expires_at) <= now:
            return RegistrationInvitationStatus.EXPIRED
        return RegistrationInvitationStatus.ACTIVE

    def _read(self, row, plan, now):
        return RegistrationInvitationRead(
            id=row.id,
            token_hint=row.token_hint,
            plan_id=row.plan_id,
            plan_name=plan.name,
            status=self._status(row, now),
            used_by=row.used_by,
            expires_at=row.expires_at,
            used_at=row.used_at,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
        )

    def list(self):
        now = datetime.now(UTC)
        with self.store._session() as session:
            rows = session.execute(
                select(RegistrationInvitationModel, SubscriptionPlanModel)
                .join(
                    SubscriptionPlanModel,
                    RegistrationInvitationModel.plan_id == SubscriptionPlanModel.id,
                )
                .order_by(RegistrationInvitationModel.created_at.desc())
            ).all()
            return RegistrationInvitationsResponse(
                invitations=[self._read(row, plan, now) for row, plan in rows]
            )

    def create(self, payload: RegistrationInvitationCreate):
        now = datetime.now(UTC)
        token = token_urlsafe(32)
        with self.store._coordinated_session() as session:
            plan = session.get(SubscriptionPlanModel, str(payload.plan_id))
            if plan is None:
                raise SubscriptionPlanNotFoundError(
                    f"subscription plan not found: {payload.plan_id}"
                )
            if self.store._plan_topology_owners(session, plan.node_ids or []):
                raise RegistrationInvitationConflict(
                    "Plans with subscriber-owned topology nodes cannot be invited"
                )
            row = RegistrationInvitationModel(
                id=str(uuid4()),
                token_hash=sha256(token.encode()).hexdigest(),
                token_hint=token[:8],
                plan_id=plan.id,
                used_by=None,
                expires_at=now + timedelta(minutes=payload.expires_minutes),
                used_at=None,
                revoked_at=None,
                created_at=now,
            )
            session.add(row)
            session.flush()
            result = self._read(row, plan, now)
            session.commit()
            return IssuedRegistrationInvitation(result, token)

    def revoke(self, identifier):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            row = session.scalar(
                select(RegistrationInvitationModel)
                .where(RegistrationInvitationModel.id == str(identifier))
                .with_for_update()
            )
            if row is None:
                raise RegistrationInvitationUnavailable("Invitation not found")
            if self._status(row, now) != RegistrationInvitationStatus.ACTIVE:
                raise RegistrationInvitationConflict("Only active invitations can be revoked")
            plan = session.get(SubscriptionPlanModel, row.plan_id)
            row.revoked_at = now
            session.flush()
            result = self._read(row, plan, now)
            session.commit()
            return result

    def claim(self, payload: RegistrationClaim, command_timeout_ms=60_000):
        now = datetime.now(UTC)
        secret = payload.token.get_secret_value()
        token_hash = sha256(secret.encode()).hexdigest()
        hashed_password = password_hash.hash(payload.password.get_secret_value())
        with self.store._coordinated_session() as session:
            invitation = session.scalar(
                select(RegistrationInvitationModel)
                .where(RegistrationInvitationModel.token_hash == token_hash)
                .with_for_update()
            )
            if (
                invitation is None
                or self._status(invitation, now) != RegistrationInvitationStatus.ACTIVE
            ):
                raise RegistrationInvitationUnavailable("Invitation unavailable")
            existing = session.scalar(
                select(ProductUserModel.username).where(
                    func.lower(ProductUserModel.username) == payload.username.lower()
                )
            )
            if existing is not None:
                raise RegistrationInvitationConflict("Username is unavailable")
            plan = session.get(SubscriptionPlanModel, invitation.plan_id)
            if plan is None:
                raise RegistrationInvitationUnavailable("Invitation unavailable")
            if self.store._plan_topology_owners(session, plan.node_ids or []):
                raise RegistrationInvitationUnavailable("Invitation unavailable")
            reset_day = plan.reset_day
            if plan.is_reset and reset_day == 0:
                reset_day = min(now.day, 28)
            user = ProductUserModel(
                username=payload.username,
                email=payload.email,
                display_name=payload.display_name or payload.username,
                remark="",
                traffic_limit_override_bytes=None,
                speed_limit_override_mbps=None,
                device_limit_override=None,
                node_speed_limit_overrides={},
                node_device_limit_overrides={},
                removal_id=None,
                role="user",
                is_active=True,
                current_plan_id=plan.id,
                plan_started_at=now,
                plan_expires_at=now + timedelta(days=plan.cycle_days),
                is_reset=plan.is_reset,
                reset_day=reset_day,
                last_traffic_reset_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()
            session.add(
                SubscriberAccount(
                    username=user.username,
                    password_hash=hashed_password,
                    version=str(uuid4()),
                    totp_secret=None,
                    last_totp_step=-1,
                    recovery_hashes=[],
                    pending_secret=None,
                    pending_expires_at=0,
                    pending_session_id=None,
                )
            )
            batches, warnings = self.store._subscription_provision_batches(
                session, user, plan, no_restart=False
            )
            access = self.store._subscription_access()
            access.authorize(session, user, plan, batches, now)
            commands = access.reconcile(
                session, now, username=user.username, timeout_ms=command_timeout_ms
            )
            invitation.used_by = user.username
            invitation.used_at = now
            session.flush()
            result = RegistrationClaimResponse(
                user=self.store._product_user_read(user),
                plan=self.store._subscription_plan_read(plan),
                commands=[self.store._command_read(command) for command in commands],
                warnings=warnings,
            )
            session.commit()
            return result
