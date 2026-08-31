import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest, token_hex, token_urlsafe
from time import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from open_node.services.inventory import create_inventory_engine

password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))
dummy_hash = password_hash.hash(token_urlsafe(32))


class AuthBase(DeclarativeBase):
    pass


class Administrator(AuthBase):
    __tablename__ = "administrator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))


class AdministratorFactor(AuthBase):
    __tablename__ = "administrator_factors"

    administrator_id: Mapped[int] = mapped_column(
        ForeignKey("administrator.id", ondelete="CASCADE"), primary_key=True
    )
    totp_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=-1)
    recovery_hashes: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pending_expires_at: Mapped[float] = mapped_column(Float, default=0)
    pending_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AdministratorSecurityPolicy(AuthBase):
    __tablename__ = "administrator_security_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    require_totp: Mapped[bool] = mapped_column(Boolean, default=False)


class AdministratorBackupEpoch(AuthBase):
    __tablename__ = "administrator_backup_epoch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(64))


class InitialSetupTicket(AuthBase):
    __tablename__ = "initial_setup_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float, default=0)
    completed_at: Mapped[float | None] = mapped_column(Float)


class OperatorSession(AuthBase):
    __tablename__ = "operator_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    last_seen_at: Mapped[float] = mapped_column(Float)


class LoginWindow(AuthBase):
    __tablename__ = "operator_login_windows"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class OperatorChallenge(AuthBase):
    __tablename__ = "operator_challenges"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    administrator_id: Mapped[int] = mapped_column(
        ForeignKey("administrator.id", ondelete="CASCADE"), index=True
    )
    credential_hash: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pending_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)


@dataclass(frozen=True)
class SessionIdentity:
    username: str
    csrf_token: str


@dataclass(frozen=True)
class TotpEnrollmentResult:
    secret: str
    provisioning_uri: str
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticationResult:
    token: str | None = None
    identity: SessionIdentity | None = None
    challenge: str | None = None
    enrollment: TotpEnrollmentResult | None = None
    recovery_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdministratorSecurityState:
    totp_enabled: bool
    totp_available: bool
    recovery_codes_remaining: int
    require_totp: bool


class AdministratorAuthenticationError(ValueError):
    pass


class AdministratorFactorUnavailable(ValueError):
    pass


class AdministratorSecurityConflict(ValueError):
    pass


class AdministratorRateLimited(ValueError):
    pass


class AuthStore:
    def __init__(self, database_url: str, totp_key=None, app_name: str = "Open Node") -> None:
        self.engine = create_inventory_engine(database_url)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        secret = totp_key.get_secret_value() if hasattr(totp_key, "get_secret_value") else totp_key
        self.cipher = Fernet(secret) if secret else None
        self.app_name = app_name
        AuthBase.metadata.create_all(self.engine)

    @contextmanager
    def _coordinated_session(self):
        with self.session() as db:
            if self.engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            else:
                db.execute(
                    select(Administrator.id).where(Administrator.id == 1).with_for_update()
                ).all()
            yield db

    def configured(self) -> bool:
        with self.session() as db:
            return db.get(Administrator, 1) is not None

    @staticmethod
    def _advance_backup_epoch(db) -> None:
        # Retaining the current browser session after a security change must not
        # retain its already-approved backup downloads. An epoch also prevents
        # change-then-revert from reviving an earlier authorization.
        epoch = db.get(AdministratorBackupEpoch, 1)
        if epoch is None:
            db.add(AdministratorBackupEpoch(id=1, value=token_hex(32)))
        else:
            epoch.value = token_hex(32)

    def set_administrator(self, username: str, password: str, *, reset: bool = False) -> None:
        hashed = password_hash.hash(password)
        with self._coordinated_session() as db:
            administrator = db.get(Administrator, 1)
            if administrator:
                if not reset:
                    raise ValueError("Administrator already exists; use reset-password")
                administrator.username = username
                administrator.password_hash = hashed
                db.execute(delete(OperatorSession))
                db.execute(delete(OperatorChallenge))
                db.execute(delete(AdministratorFactor))
                db.execute(delete(LoginWindow))
                policy = db.get(AdministratorSecurityPolicy, 1)
                if policy:
                    policy.require_totp = False
            else:
                db.add(Administrator(id=1, username=username, password_hash=hashed))
            self._advance_backup_epoch(db)
            ticket = db.get(InitialSetupTicket, 1)
            if ticket is not None:
                ticket.token_hash = None
                ticket.expires_at = 0
                ticket.completed_at = time()
            db.commit()

    def allow_login_attempt(self, peer: str, *, max_attempts: int = 10) -> bool:
        key = sha256(peer.encode()).hexdigest()
        now = time()
        # A conditional increment enforces the same limit across workers and restarts.
        with self.session() as db:
            db.execute(delete(LoginWindow).where(LoginWindow.expires_at <= now))
            try:
                db.add(LoginWindow(key=key, attempts=0, expires_at=now + 60))
                db.commit()
            except IntegrityError:
                db.rollback()
            result = db.execute(
                update(LoginWindow)
                .where(LoginWindow.key == key, LoginWindow.attempts < max_attempts)
                .values(attempts=LoginWindow.attempts + 1)
            )
            db.commit()
            return result.rowcount == 1

    @staticmethod
    def _digest(value: str) -> str:
        return sha256(value.encode()).hexdigest()

    @staticmethod
    def _policy(db) -> AdministratorSecurityPolicy | None:
        return db.get(AdministratorSecurityPolicy, 1)

    @staticmethod
    def _factor_row(db, *, create: bool = False) -> AdministratorFactor | None:
        factor = db.get(AdministratorFactor, 1)
        if factor is None and create:
            factor = AdministratorFactor(administrator_id=1)
            db.add(factor)
            db.flush()
        return factor

    def _seal_secret(self, administrator: Administrator, secret: str) -> str:
        if not self.cipher:
            raise AdministratorFactorUnavailable(
                "Administrator two-factor authentication is unavailable"
            )
        bound = self._digest(administrator.username) + "\n" + secret
        return self.cipher.encrypt(bound.encode()).decode()

    def _open_secret(self, administrator: Administrator, value: str) -> str:
        if not self.cipher:
            raise AdministratorFactorUnavailable(
                "Administrator two-factor authentication is unavailable"
            )
        try:
            prefix, secret = self.cipher.decrypt(value.encode()).decode().split("\n", 1)
            if not compare_digest(prefix, self._digest(administrator.username)):
                raise InvalidToken()
            return secret
        except (InvalidToken, ValueError, UnicodeError):
            raise AdministratorFactorUnavailable(
                "Administrator two-factor authentication is unavailable"
            ) from None

    @staticmethod
    def _totp_step(secret: str, code: str, last_step: int) -> int | None:
        if not re.fullmatch(r"[0-9]{6}", code):
            return None
        counter = int(time() // 30)
        totp = pyotp.TOTP(secret)
        for step in (counter + 1, counter, counter - 1):
            if step > last_step and pyotp.utils.strings_equal(totp.at(step * 30), code):
                return step
        return None

    def _verify_factor(
        self, administrator: Administrator, factor: AdministratorFactor, code: str
    ) -> bool:
        if not factor.totp_secret:
            return False
        normalized = code.strip().replace("-", "").lower()
        if re.fullmatch(r"[a-f0-9]{20}", normalized):
            target = self._digest(administrator.username + ":" + normalized)
            for item in factor.recovery_hashes:
                if compare_digest(target, item):
                    factor.recovery_hashes = [
                        candidate for candidate in factor.recovery_hashes if candidate != item
                    ]
                    return True
            return False
        step = self._totp_step(
            self._open_secret(administrator, factor.totp_secret),
            code.strip(),
            factor.last_totp_step,
        )
        if step is None:
            return False
        factor.last_totp_step = step
        return True

    def _new_recovery_codes(
        self, administrator: Administrator, factor: AdministratorFactor
    ) -> tuple[str, ...]:
        codes = [token_hex(10) for _ in range(10)]
        factor.recovery_hashes = [
            self._digest(administrator.username + ":" + code) for code in codes
        ]
        return tuple(
            "-".join(code[index : index + 5] for index in range(0, 20, 5)) for code in codes
        )

    def _new_session(
        self, db, administrator: Administrator, lifetime: int
    ) -> AuthenticationResult:
        now = time()
        token, csrf = token_urlsafe(32), token_urlsafe(32)
        db.execute(delete(OperatorSession).where(OperatorSession.expires_at <= now))
        db.add(
            OperatorSession(
                token_hash=self._digest(token),
                csrf_token=csrf,
                expires_at=now + lifetime,
                last_seen_at=now,
            )
        )
        return AuthenticationResult(
            token=token, identity=SessionIdentity(administrator.username, csrf)
        )

    def _new_challenge(
        self,
        db,
        administrator: Administrator,
        kind: str,
        *,
        pending_secret: str | None = None,
    ) -> tuple[str, float]:
        now = time()
        db.execute(
            delete(OperatorChallenge).where(
                (OperatorChallenge.administrator_id == administrator.id)
                | (OperatorChallenge.expires_at <= now)
            )
        )
        token = token_urlsafe(32)
        expires_at = now + 300
        db.add(
            OperatorChallenge(
                token_hash=self._digest(token),
                administrator_id=administrator.id,
                credential_hash=administrator.password_hash,
                kind=kind,
                expires_at=expires_at,
                attempts=0,
                pending_secret=pending_secret,
            )
        )
        return token, expires_at

    def login(
        self, username: str, password: str, lifetime: int
    ) -> AuthenticationResult | None:
        with self.session() as db:
            administrator = db.get(Administrator, 1)
            matches = administrator is not None and administrator.username == username
            hashed = administrator.password_hash if matches else dummy_hash
            if not password_hash.verify(password, hashed) or not matches:
                return None
            # Lock the credential version before issuing a session, so a concurrent
            # password reset cannot be followed by a login with the old password.
            result = db.execute(
                update(Administrator)
                .where(Administrator.id == 1, Administrator.password_hash == hashed)
                .values(password_hash=hashed)
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            factor = self._factor_row(db)
            policy = self._policy(db)
            if factor and factor.totp_secret:
                challenge, _ = self._new_challenge(db, administrator, "verify")
                db.commit()
                return AuthenticationResult(challenge=challenge)
            if policy and policy.require_totp:
                secret = pyotp.random_base32()
                sealed = self._seal_secret(administrator, secret)
                challenge, expires_at = self._new_challenge(
                    db, administrator, "enroll", pending_secret=sealed
                )
                enrollment = TotpEnrollmentResult(
                    secret=secret,
                    provisioning_uri=pyotp.TOTP(secret).provisioning_uri(
                        name=administrator.username, issuer_name=self.app_name
                    ),
                    expires_at=datetime.fromtimestamp(expires_at, UTC),
                )
                db.commit()
                return AuthenticationResult(challenge=challenge, enrollment=enrollment)
            issued = self._new_session(db, administrator, lifetime)
            db.commit()
            return issued

    def complete_login(self, challenge: str, code: str, lifetime: int) -> AuthenticationResult:
        if not challenge or len(challenge) > 128:
            raise AdministratorAuthenticationError("Invalid or expired verification challenge")
        with self._coordinated_session() as db:
            row = db.get(OperatorChallenge, self._digest(challenge))
            administrator = db.get(Administrator, 1)
            if (
                not row
                or not administrator
                or row.administrator_id != administrator.id
                or row.credential_hash != administrator.password_hash
                or row.expires_at <= time()
                or row.attempts >= 5
            ):
                if row:
                    db.delete(row)
                    db.commit()
                raise AdministratorAuthenticationError(
                    "Invalid or expired verification challenge"
                )
            self._consume_factor_attempt(db, administrator.id)
            row.attempts += 1
            recovery_codes: tuple[str, ...] = ()
            factor = self._factor_row(db, create=row.kind == "enroll")
            if row.kind == "verify":
                valid = bool(factor and self._verify_factor(administrator, factor, code))
            elif row.kind == "enroll" and row.pending_secret and factor and not factor.totp_secret:
                step = self._totp_step(
                    self._open_secret(administrator, row.pending_secret), code.strip(), -1
                )
                valid = step is not None
                if valid:
                    factor.totp_secret = row.pending_secret
                    factor.last_totp_step = step
                    recovery_codes = self._new_recovery_codes(administrator, factor)
            else:
                valid = False
            if not valid:
                db.commit()
                raise AdministratorAuthenticationError("Invalid verification code")
            if row.kind == "enroll":
                db.execute(delete(OperatorSession))
                self._advance_backup_epoch(db)
            db.execute(delete(OperatorChallenge).where(OperatorChallenge.administrator_id == 1))
            issued = self._new_session(db, administrator, lifetime)
            db.commit()
            return AuthenticationResult(
                token=issued.token,
                identity=issued.identity,
                recovery_codes=recovery_codes,
            )

    def _consume_factor_attempt(self, db, administrator_id: int) -> None:
        # The caller holds the administrator lock. Only a live, password-proven
        # challenge can consume this budget; changing IP or challenge cannot reset it.
        key = self._digest(f"administrator:second-factor:{administrator_id}")
        now = time()
        window = db.scalar(select(LoginWindow).where(LoginWindow.key == key).with_for_update())
        if window is None:
            db.add(LoginWindow(key=key, attempts=1, expires_at=now + 60))
        elif window.expires_at <= now:
            window.attempts, window.expires_at = 1, now + 60
        elif window.attempts >= 10:
            raise AdministratorRateLimited("Too many verification attempts; try again shortly")
        else:
            window.attempts += 1

    def authenticate(self, token: str | None, idle_seconds: int) -> SessionIdentity | None:
        if not token or len(token) > 128:
            return None
        now = time()
        with self.session() as db:
            token_hash = sha256(token.encode()).hexdigest()
            result = db.execute(
                update(OperatorSession)
                .where(
                    OperatorSession.token_hash == token_hash,
                    OperatorSession.expires_at > now,
                    OperatorSession.last_seen_at > now - idle_seconds,
                )
                .values(last_seen_at=now)
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            record = db.get(OperatorSession, token_hash)
            administrator = db.get(Administrator, 1)
            policy = self._policy(db)
            factor = self._factor_row(db)
            if not administrator or (
                policy and policy.require_totp and not (factor and factor.totp_secret)
            ):
                db.delete(record)
                db.commit()
                return None
            identity = SessionIdentity(administrator.username, record.csrf_token)
            db.commit()
            return identity

    def logout(self, token: str) -> None:
        with self.session.begin() as db:
            db.execute(
                delete(OperatorSession).where(
                    OperatorSession.token_hash == sha256(token.encode()).hexdigest(),
                )
            )

    def security(self) -> AdministratorSecurityState:
        with self.session() as db:
            factor = self._factor_row(db)
            policy = self._policy(db)
            return AdministratorSecurityState(
                totp_enabled=bool(factor and factor.totp_secret),
                totp_available=bool(self.cipher),
                recovery_codes_remaining=len(factor.recovery_hashes) if factor else 0,
                require_totp=bool(policy and policy.require_totp),
            )

    def begin_totp(self, session_token: str, password: str) -> TotpEnrollmentResult:
        token_hash = self._digest(session_token)
        with self._coordinated_session() as db:
            administrator = db.get(Administrator, 1)
            active_session = db.get(OperatorSession, token_hash)
            if (
                not administrator
                or not active_session
                or active_session.expires_at <= time()
                or not password_hash.verify(password, administrator.password_hash)
            ):
                raise AdministratorAuthenticationError("Invalid administrator credentials")
            factor = self._factor_row(db, create=True)
            if factor.totp_secret:
                raise AdministratorSecurityConflict(
                    "Administrator two-factor authentication is already enabled"
                )
            secret = pyotp.random_base32()
            factor.pending_secret = self._seal_secret(administrator, secret)
            factor.pending_expires_at = time() + 600
            factor.pending_session_hash = token_hash
            result = TotpEnrollmentResult(
                secret=secret,
                provisioning_uri=pyotp.TOTP(secret).provisioning_uri(
                    name=administrator.username, issuer_name=self.app_name
                ),
                expires_at=datetime.fromtimestamp(factor.pending_expires_at, UTC),
            )
            self._advance_backup_epoch(db)
            db.commit()
            return result

    def confirm_totp(self, session_token: str, code: str) -> tuple[str, ...]:
        token_hash = self._digest(session_token)
        with self._coordinated_session() as db:
            administrator = db.get(Administrator, 1)
            active_session = db.get(OperatorSession, token_hash)
            factor = self._factor_row(db)
            if (
                not administrator
                or not active_session
                or active_session.expires_at <= time()
                or not factor
                or factor.totp_secret
                or not factor.pending_secret
                or factor.pending_expires_at <= time()
                or factor.pending_session_hash != token_hash
            ):
                raise AdministratorSecurityConflict(
                    "Two-factor enrollment expired; start again"
                )
            step = self._totp_step(
                self._open_secret(administrator, factor.pending_secret), code.strip(), -1
            )
            if step is None:
                raise AdministratorAuthenticationError("Invalid verification code")
            factor.totp_secret = factor.pending_secret
            factor.last_totp_step = step
            factor.pending_secret = factor.pending_session_hash = None
            factor.pending_expires_at = 0
            codes = self._new_recovery_codes(administrator, factor)
            db.execute(delete(OperatorSession).where(OperatorSession.token_hash != token_hash))
            db.execute(delete(OperatorChallenge))
            self._advance_backup_epoch(db)
            db.commit()
            return codes

    def _security_proof(self, db, session_token: str, password: str, code: str):
        token_hash = self._digest(session_token)
        administrator = db.get(Administrator, 1)
        active_session = db.get(OperatorSession, token_hash)
        factor = self._factor_row(db)
        if (
            not administrator
            or not active_session
            or active_session.expires_at <= time()
            or not password_hash.verify(password, administrator.password_hash)
            or not factor
            or not factor.totp_secret
            or not self._verify_factor(administrator, factor, code)
        ):
            raise AdministratorAuthenticationError(
                "Invalid administrator credentials or verification code"
            )
        return token_hash, administrator, factor

    def update_totp(
        self, session_token: str, password: str, code: str, *, disable: bool = False
    ) -> tuple[str, ...]:
        with self._coordinated_session() as db:
            token_hash, administrator, factor = self._security_proof(
                db, session_token, password, code
            )
            policy = self._policy(db)
            if disable and policy and policy.require_totp:
                db.rollback()
                raise AdministratorSecurityConflict(
                    "Disable the mandatory administrator 2FA policy first"
                )
            if disable:
                factor.totp_secret = factor.pending_secret = factor.pending_session_hash = None
                factor.last_totp_step = -1
                factor.recovery_hashes = []
                factor.pending_expires_at = 0
                codes: tuple[str, ...] = ()
            else:
                codes = self._new_recovery_codes(administrator, factor)
            db.execute(delete(OperatorSession).where(OperatorSession.token_hash != token_hash))
            db.execute(delete(OperatorChallenge))
            self._advance_backup_epoch(db)
            db.commit()
            return codes

    def update_policy(
        self, session_token: str, password: str, code: str, required: bool
    ) -> AdministratorSecurityState:
        with self._coordinated_session() as db:
            token_hash = self._digest(session_token)
            administrator = db.get(Administrator, 1)
            active_session = db.get(OperatorSession, token_hash)
            factor = self._factor_row(db)
            if (
                not administrator
                or not active_session
                or active_session.expires_at <= time()
                or not password_hash.verify(password, administrator.password_hash)
            ):
                raise AdministratorAuthenticationError("Invalid administrator credentials")
            if required and (not factor or not factor.totp_secret):
                raise AdministratorSecurityConflict(
                    "Enable administrator two-factor authentication before requiring it"
                )
            if factor and factor.totp_secret and not self._verify_factor(
                administrator, factor, code
            ):
                raise AdministratorAuthenticationError(
                    "Invalid administrator credentials or verification code"
                )
            policy = self._policy(db)
            if not policy:
                policy = AdministratorSecurityPolicy(id=1, require_totp=required)
                db.add(policy)
            else:
                policy.require_totp = required
            db.execute(delete(OperatorSession).where(OperatorSession.token_hash != token_hash))
            db.execute(delete(OperatorChallenge))
            self._advance_backup_epoch(db)
            db.commit()
            return AdministratorSecurityState(
                totp_enabled=bool(factor and factor.totp_secret),
                totp_available=bool(self.cipher),
                recovery_codes_remaining=len(factor.recovery_hashes) if factor else 0,
                require_totp=required,
            )

    def change_password(self, current_password: str, new_password: str) -> bool:
        with self.session() as db:
            administrator = db.get(Administrator, 1)
            if not administrator or not password_hash.verify(
                current_password, administrator.password_hash
            ):
                return False
            result = db.execute(
                update(Administrator)
                .where(
                    Administrator.id == 1,
                    Administrator.password_hash == administrator.password_hash,
                )
                .values(password_hash=password_hash.hash(new_password))
            )
            if result.rowcount != 1:
                db.rollback()
                return False
            db.execute(delete(OperatorSession))
            db.execute(delete(OperatorChallenge))
            factor = self._factor_row(db)
            if factor:
                factor.pending_secret = factor.pending_session_hash = None
                factor.pending_expires_at = 0
            self._advance_backup_epoch(db)
            db.commit()
            return True
