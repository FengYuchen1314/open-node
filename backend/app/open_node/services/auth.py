from dataclasses import dataclass
from hashlib import sha256
from secrets import token_urlsafe
from time import time

from pwdlib import PasswordHash
from sqlalchemy import Float, Integer, String, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from open_node.services.inventory import create_inventory_engine

password_hash = PasswordHash.recommended()
dummy_hash = password_hash.hash(token_urlsafe(32))


class AuthBase(DeclarativeBase):
    pass


class Administrator(AuthBase):
    __tablename__ = "administrator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))


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


@dataclass(frozen=True)
class SessionIdentity:
    username: str
    csrf_token: str


class AuthStore:
    def __init__(self, database_url: str) -> None:
        self.engine = create_inventory_engine(database_url)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        AuthBase.metadata.create_all(self.engine)

    def configured(self) -> bool:
        with self.session() as db:
            return db.get(Administrator, 1) is not None

    def set_administrator(self, username: str, password: str, *, reset: bool = False) -> None:
        hashed = password_hash.hash(password)
        with self.session.begin() as db:
            administrator = db.get(Administrator, 1)
            if administrator:
                if not reset:
                    raise ValueError("Administrator already exists; use reset-password")
                administrator.username = username
                administrator.password_hash = hashed
                db.execute(delete(OperatorSession))
            else:
                db.add(Administrator(id=1, username=username, password_hash=hashed))

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

    def login(
        self, username: str, password: str, lifetime: int
    ) -> tuple[str, SessionIdentity] | None:
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
            now = time()
            token, csrf = token_urlsafe(32), token_urlsafe(32)
            db.execute(delete(OperatorSession).where(OperatorSession.expires_at <= now))
            db.add(
                OperatorSession(
                    token_hash=sha256(token.encode()).hexdigest(),
                    csrf_token=csrf,
                    expires_at=now + lifetime,
                    last_seen_at=now,
                )
            )
            db.commit()
            return token, SessionIdentity(username, csrf)

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
            db.commit()
            return True
