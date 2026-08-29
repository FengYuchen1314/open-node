"""Subscriber identities never authorize controller or Agent operations."""

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest, token_hex, token_urlsafe
from uuid import uuid4

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.subscriber_auth import (
    SubscriberAccountRead,
    SubscriberDeviceRead,
    SubscriberEnrollment,
    SubscriberProfile,
    SubscriberSecurityRead,
)
from open_node.services.auth import dummy_hash, password_hash
from open_node.services.inventory import (
    Base,
    ManagedNodeModel,
    ProductUserConflict,
    ProductUserModel,
    ProductUserNotFoundError,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
)
from open_node.services.user_limits import effective_limits, node_limits


class SubscriberAccount(Base):
    __tablename__ = "subscriber_accounts"

    username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(String(512))
    version: Mapped[str] = mapped_column(String(36))
    totp_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=-1)
    recovery_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pending_expires_at: Mapped[float] = mapped_column(Float, default=0)
    pending_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SubscriberSession(Base):
    __tablename__ = "subscriber_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(
        ForeignKey("subscriber_accounts.username", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    last_seen_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    peer: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str] = mapped_column(String(512))


class SubscriberChallenge(Base):
    __tablename__ = "subscriber_challenges"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        ForeignKey("subscriber_accounts.username", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class SubscriberAuthenticationError(ValueError):
    pass


class SubscriberSessionExpired(SubscriberAuthenticationError):
    pass


class SubscriberFactorUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class SubscriberIdentity:
    username: str
    session_id: str
    csrf_token: str
    version: str


def digest(value):
    return sha256(value.encode()).hexdigest()


def revoke_subscriber_sessions(session, username, *, keep=None):
    statement = delete(SubscriberSession).where(SubscriberSession.username == username)
    if keep:
        statement = statement.where(SubscriberSession.id != keep)
    session.execute(statement)
    session.execute(delete(SubscriberChallenge).where(SubscriberChallenge.username == username))
    account = session.get(SubscriberAccount, username)
    if account:
        account.version = str(uuid4())
        account.pending_secret = account.pending_session_id = None
        account.pending_expires_at = 0


class SubscriberAuthStore:
    def __init__(self, inventory, settings):
        self.inventory = inventory
        self.settings = settings
        self.cipher = (
            Fernet(settings.subscriber_totp_key.get_secret_value())
            if settings.subscriber_totp_key
            else None
        )

    def _account(self, session, username, *, active=True):
        user = session.scalar(
            select(ProductUserModel).where(ProductUserModel.username == username).with_for_update()
        )
        account = session.scalar(
            select(SubscriberAccount)
            .where(SubscriberAccount.username == username)
            .with_for_update()
        )
        if not user or not account or (active and (not user.is_active or user.removal_id)):
            raise SubscriberSessionExpired("Subscriber sign-in required")
        return user, account

    def _identity(self, session, identity):
        user, account = self._account(session, identity.username)
        device = session.get(SubscriberSession, identity.session_id)
        now = time.time()
        if (
            not device
            or device.username != identity.username
            or account.version != identity.version
            or device.expires_at <= now
            or device.last_seen_at <= now - self.settings.session_idle_seconds
        ):
            raise SubscriberSessionExpired("Subscriber sign-in required")
        return user, account

    def _verified_version(self, username, password):
        with self.inventory._session() as session:
            account = session.get(SubscriberAccount, username)
            hashed = account.password_hash if account else dummy_hash
            version = account.version if account else None
        valid, replacement = password_hash.verify_and_update(password, hashed)
        if not valid or version is None:
            raise SubscriberAuthenticationError("Invalid credentials")
        if replacement:
            with self.inventory._coordinated_session() as session:
                session.execute(
                    update(SubscriberAccount)
                    .where(
                        SubscriberAccount.username == username,
                        SubscriberAccount.password_hash == hashed,
                        SubscriberAccount.version == version,
                    )
                    .values(password_hash=replacement)
                )
                session.commit()
        return version

    def _check_proof(self, session, identity, proof, version):
        user, account = self._identity(session, identity)
        if version != account.version or not self._factor(account, proof.code.get_secret_value()):
            raise SubscriberAuthenticationError("Invalid credentials or verification code")
        return user, account

    @staticmethod
    def _session_identity(account, device):
        return SubscriberIdentity(account.username, device.id, device.csrf_token, account.version)

    def _new_session(self, session, account, peer, user_agent):
        now = time.time()
        session.execute(delete(SubscriberSession).where(SubscriberSession.expires_at <= now))
        session.execute(delete(SubscriberChallenge).where(SubscriberChallenge.expires_at <= now))
        rows = session.scalars(
            select(SubscriberSession)
            .where(SubscriberSession.username == account.username)
            .order_by(SubscriberSession.created_at.desc(), SubscriberSession.id)
        ).all()
        for row in rows[19:]:
            session.delete(row)
        token = token_urlsafe(32)
        device = SubscriberSession(
            id=str(uuid4()),
            token_hash=digest(token),
            username=account.username,
            csrf_token=token_urlsafe(32),
            created_at=now,
            last_seen_at=now,
            expires_at=now + self.settings.session_lifetime_seconds,
            peer=peer[:255],
            user_agent="".join(c for c in user_agent if ord(c) >= 32)[:512],
        )
        session.add(device)
        session.flush()
        return token, self._session_identity(account, device)

    def login(self, username, password, peer, user_agent):
        username = username.strip()
        version = self._verified_version(username, password)
        with self.inventory._coordinated_session() as session:
            _, account = self._account(session, username)
            if account.version != version:
                raise SubscriberAuthenticationError("Invalid credentials")
            if account.totp_secret:
                challenge = token_urlsafe(32)
                session.execute(
                    delete(SubscriberChallenge).where(
                        (SubscriberChallenge.username == username)
                        | (SubscriberChallenge.expires_at <= time.time())
                    )
                )
                session.add(
                    SubscriberChallenge(
                        token_hash=digest(challenge),
                        username=username,
                        version=version,
                        expires_at=time.time() + 300,
                        attempts=0,
                    )
                )
                session.commit()
                return None, None, challenge
            token, identity = self._new_session(session, account, peer, user_agent)
            session.commit()
            return token, identity, None

    def complete_login(self, token, code, peer, user_agent):
        hashed = digest(token)
        with self.inventory._session() as session:
            row = session.get(SubscriberChallenge, hashed)
            username = row.username if row else None
        if not username:
            raise SubscriberAuthenticationError("Invalid or expired verification challenge")
        with self.inventory._coordinated_session() as session:
            _, account = self._account(session, username)
            row = session.get(SubscriberChallenge, hashed)
            if (
                not row
                or row.expires_at <= time.time()
                or row.version != account.version
                or row.attempts >= 5
            ):
                raise SubscriberAuthenticationError("Invalid or expired verification challenge")
            row.attempts += 1
            valid = self._factor(account, code)
            if not valid:
                session.commit()
                raise SubscriberAuthenticationError("Invalid verification code")
            session.delete(row)
            issued = self._new_session(session, account, peer, user_agent)
            session.commit()
            return issued

    def authenticate(self, token):
        if not token or len(token) > 128:
            return None
        with self.inventory._session() as session:
            device = session.scalar(
                select(SubscriberSession).where(SubscriberSession.token_hash == digest(token))
            )
            username = device.username if device else None
        if not username:
            return None
        try:
            with self.inventory._coordinated_session() as session:
                _, account = self._account(session, username)
                device = session.scalar(
                    select(SubscriberSession).where(SubscriberSession.token_hash == digest(token))
                )
                now = time.time()
                if (
                    not device
                    or device.expires_at <= now
                    or device.last_seen_at <= now - self.settings.session_idle_seconds
                ):
                    return None
                device.last_seen_at = now
                identity = self._session_identity(account, device)
                session.commit()
                return identity
        except SubscriberAuthenticationError:
            return None

    def logout(self, token):
        if not token or len(token) > 128:
            return
        with self.inventory._session() as session:
            session.execute(
                delete(SubscriberSession).where(SubscriberSession.token_hash == digest(token))
            )
            session.commit()

    @staticmethod
    def _management_read(user, account):
        return SubscriberAccountRead(
            username=user.username,
            configured=bool(account),
            totp_enabled=bool(account and account.totp_secret),
            revision=account.version
            if account
            else digest(user.username + user.created_at.isoformat()),
        )

    def management(self, username):
        with self.inventory._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError("User not found")
            return self._management_read(user, session.get(SubscriberAccount, username))

    def set_password(self, username, payload):
        hashed = password_hash.hash(payload.new_password.get_secret_value())
        with self.inventory._coordinated_session() as session:
            user = session.scalar(
                select(ProductUserModel)
                .where(ProductUserModel.username == username)
                .with_for_update()
            )
            if not user:
                raise ProductUserNotFoundError("User not found")
            self.inventory._user_management().require_editable(user)
            account = session.scalar(
                select(SubscriberAccount)
                .where(SubscriberAccount.username == username)
                .with_for_update()
            )
            if self._management_read(user, account).revision != payload.expected_revision:
                raise ProductUserConflict("Login settings changed; reload before saving")
            if not account:
                account = SubscriberAccount(
                    username=username, password_hash=hashed, version=str(uuid4())
                )
                session.add(account)
                session.flush()
            account.password_hash = hashed
            if payload.reset_totp:
                account.totp_secret, account.last_totp_step, account.recovery_hashes = None, -1, []
            revoke_subscriber_sessions(session, username)
            session.flush()
            result = self._management_read(user, account)
            session.commit()
            return result

    def change_password(self, identity, payload):
        version = self._verified_version(identity.username, payload.password.get_secret_value())
        hashed = password_hash.hash(payload.new_password.get_secret_value())
        with self.inventory._coordinated_session() as session:
            _, account = self._check_proof(session, identity, payload, version)
            account.password_hash = hashed
            revoke_subscriber_sessions(session, identity.username)
            session.commit()

    def profile(self, identity):
        with self.inventory._coordinated_session() as session:
            user, _ = self._identity(session, identity)
            plan = (
                session.get(SubscriptionPlanModel, user.current_plan_id)
                if user.current_plan_id
                else None
            )
            limits = effective_limits(user, plan)
            nodes = session.scalars(
                select(ManagedNodeModel).where(
                    ManagedNodeModel.id.in_(plan.node_ids if plan else []),
                    ManagedNodeModel.enabled.is_(True),
                    ManagedNodeModel.removal_id.is_(None),
                )
            ).all()
            return SubscriberProfile(
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                quota=self.inventory._subscription_quota_status(
                    session, user, plan, datetime.now(UTC)
                ),
                speed_limit_mbps=limits.speed_limit_mbps,
                device_limit=limits.device_limit,
                node_limits=node_limits(
                    user,
                    plan,
                    nodes,
                    session.scalars(
                        select(SubscriptionCredentialModel).where(
                            SubscriptionCredentialModel.username == user.username
                        )
                    ).all(),
                ),
            )

    def subscription_token(self, identity, proof=None):
        version = (
            self._verified_version(identity.username, proof.password.get_secret_value())
            if proof
            else None
        )
        with self.inventory._coordinated_session() as session:
            if proof:
                self._check_proof(session, identity, proof, version)
            else:
                self._identity(session, identity)
            token = self.inventory._issue_subscription_token(
                session, identity.username, reset=proof is not None
            )
            session.commit()
            return token

    def set_short_code(self, identity, payload):
        version = self._verified_version(identity.username, payload.password.get_secret_value())
        with self.inventory._coordinated_session() as session:
            self._check_proof(session, identity, payload, version)
            token = self.inventory._set_subscription_short_code(session, identity.username, payload)
            session.commit()
            return token

    def devices(self, identity):
        now = time.time()
        with self.inventory._coordinated_session() as session:
            self._identity(session, identity)
            return [
                SubscriberDeviceRead(
                    id=row.id,
                    current=row.id == identity.session_id,
                    created_at=datetime.fromtimestamp(row.created_at, UTC),
                    last_seen_at=datetime.fromtimestamp(row.last_seen_at, UTC),
                    expires_at=datetime.fromtimestamp(row.expires_at, UTC),
                    peer=row.peer,
                    user_agent=row.user_agent,
                )
                for row in session.scalars(
                    select(SubscriberSession)
                    .where(
                        SubscriberSession.username == identity.username,
                        SubscriberSession.expires_at > now,
                        SubscriberSession.last_seen_at > now - self.settings.session_idle_seconds,
                    )
                    .order_by(SubscriberSession.created_at.desc(), SubscriberSession.id)
                ).all()
            ]

    def revoke_device(self, identity, identifier=None):
        with self.inventory._coordinated_session() as session:
            self._identity(session, identity)
            statement = delete(SubscriberSession).where(
                SubscriberSession.username == identity.username
            )
            statement = (
                statement.where(SubscriberSession.id == identifier)
                if identifier
                else statement.where(SubscriberSession.id != identity.session_id)
            )
            session.execute(statement)
            session.commit()

    def security(self, identity):
        with self.inventory._coordinated_session() as session:
            _, account = self._identity(session, identity)
            return SubscriberSecurityRead(
                totp_enabled=bool(account.totp_secret),
                totp_available=bool(self.cipher),
                recovery_codes_remaining=len(account.recovery_hashes),
            )

    def _open_secret(self, account, value):
        if not self.cipher:
            raise SubscriberFactorUnavailable(
                "Two-factor authentication is unavailable; contact the administrator"
            )
        try:
            prefix, secret = self.cipher.decrypt(value.encode()).decode().split("\n", 1)
            if not compare_digest(prefix, digest(account.username)):
                raise InvalidToken()
            return secret
        except (InvalidToken, ValueError, UnicodeError):
            raise SubscriberFactorUnavailable(
                "Two-factor authentication is unavailable; contact the administrator"
            ) from None

    def _totp_step(self, secret, code, last_step):
        if not re.fullmatch(r"[0-9]{6}", code):
            return None
        counter = int(time.time() // 30)
        totp = pyotp.TOTP(secret)
        for step in (counter + 1, counter, counter - 1):
            if step > last_step and pyotp.utils.strings_equal(totp.at(step * 30), code):
                return step
        return None

    def _factor(self, account, code):
        if not account.totp_secret:
            return True
        normalized = code.strip().replace("-", "").lower()
        if re.fullmatch(r"[a-f0-9]{8}", normalized):
            target = "legacy:" + digest(normalized)
            for item in account.recovery_hashes:
                if compare_digest(target, item):
                    account.recovery_hashes = [
                        entry for entry in account.recovery_hashes if entry != item
                    ]
                    return True
            return False
        if re.fullmatch(r"[a-f0-9]{20}", normalized):
            hashed = digest(account.username + ":" + normalized)
            for item in account.recovery_hashes:
                if compare_digest(hashed, item):
                    account.recovery_hashes = [
                        entry for entry in account.recovery_hashes if entry != item
                    ]
                    return True
            return False
        step = self._totp_step(
            self._open_secret(account, account.totp_secret), code.strip(), account.last_totp_step
        )
        if step is None:
            return False
        account.last_totp_step = step
        return True

    @staticmethod
    def _recovery_codes(account):
        codes = [token_hex(10) for _ in range(10)]
        account.recovery_hashes = [digest(account.username + ":" + code) for code in codes]
        return ["-".join(code[index : index + 5] for index in range(0, 20, 5)) for code in codes]

    def begin_totp(self, identity, payload):
        version = self._verified_version(identity.username, payload.password.get_secret_value())
        with self.inventory._coordinated_session() as session:
            _, account = self._identity(session, identity)
            if account.version != version:
                raise SubscriberAuthenticationError("Invalid credentials")
            if account.totp_secret:
                raise ProductUserConflict("Two-factor authentication is already enabled")
            if not self.cipher:
                raise SubscriberFactorUnavailable(
                    "Two-factor enrollment is unavailable; contact the administrator"
                )
            secret = pyotp.random_base32()
            account.pending_secret = self.cipher.encrypt(
                (digest(account.username) + "\n" + secret).encode()
            ).decode()
            account.pending_expires_at = time.time() + 600
            account.pending_session_id = identity.session_id
            result = SubscriberEnrollment(
                secret=secret,
                provisioning_uri=pyotp.TOTP(secret).provisioning_uri(
                    name=identity.username, issuer_name="Open Node"
                ),
                expires_at=datetime.fromtimestamp(account.pending_expires_at, UTC),
            )
            session.commit()
            return result

    def confirm_totp(self, identity, code):
        with self.inventory._coordinated_session() as session:
            _, account = self._identity(session, identity)
            if (
                account.totp_secret
                or not account.pending_secret
                or account.pending_expires_at <= time.time()
                or account.pending_session_id != identity.session_id
            ):
                raise ProductUserConflict("Two-factor enrollment expired; start again")
            step = self._totp_step(
                self._open_secret(account, account.pending_secret), code.strip(), -1
            )
            if step is None:
                raise SubscriberAuthenticationError("Invalid verification code")
            account.totp_secret, account.last_totp_step = account.pending_secret, step
            codes = self._recovery_codes(account)
            revoke_subscriber_sessions(session, identity.username, keep=identity.session_id)
            session.commit()
            return codes

    def update_totp(self, identity, proof, *, disable=False):
        version = self._verified_version(identity.username, proof.password.get_secret_value())
        with self.inventory._coordinated_session() as session:
            _, account = self._check_proof(session, identity, proof, version)
            if not account.totp_secret:
                raise ProductUserConflict("Two-factor authentication is not enabled")
            if disable:
                account.totp_secret, account.last_totp_step, account.recovery_hashes = None, -1, []
                codes = []
            else:
                codes = self._recovery_codes(account)
            revoke_subscriber_sessions(session, identity.username, keep=identity.session_id)
            session.commit()
            return codes
