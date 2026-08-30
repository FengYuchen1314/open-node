import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from ipaddress import IPv6Address
from secrets import compare_digest, token_urlsafe
from time import time
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Float, ForeignKey, String, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from open_node.domain.agent_bootstrap import (
    AgentBootstrapConfig,
    AgentBootstrapIssued,
    AgentBootstrapState,
    AgentBootstrapStatus,
    AgentBootstrapTransport,
)
from open_node.domain.inventory import ServerStatus
from open_node.services.inventory import (
    AgentModel,
    Base,
    InventoryStore,
    ServerModel,
    ServerNotFoundError,
)

TICKET_LIFETIME_SECONDS = 600
CLAIM_RETRY_SECONDS = 120
_REDEMPTION_ERROR = "Invalid or expired installation ticket"
_URL_ERROR = "control_url must be a valid HTTPS base URL without credentials, query, or fragment"


class AgentBootstrapUnavailableError(ValueError):
    pass


class AgentBootstrapRedemptionError(ValueError):
    pass


class AgentBootstrapTicketModel(Base):
    __tablename__ = "agent_bootstrap_tickets"

    server_id: Mapped[str] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    credential_hash: Mapped[str] = mapped_column(String(64))
    control_url: Mapped[str] = mapped_column(String(2048))
    transport: Mapped[str] = mapped_column(String(16))
    issued_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)
    claim_nonce_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


def normalize_control_url(value: str) -> str:
    """Validate the explicitly configured origin/base path without resolving or fetching it."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or any(character in value for character in ("\\", "?", "#"))
    ):
        raise ValueError(_URL_ERROR)
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.netloc.endswith(":")
            or (port is not None and not 1 <= port <= 65535)
            or "%" in host
        ):
            raise ValueError(_URL_ERROR)
        if ":" in host:
            if not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", parts.netloc):
                raise ValueError(_URL_ERROR)
            authority = f"[{IPv6Address(host).compressed}]"
        else:
            if parts.netloc.startswith("["):
                raise ValueError(_URL_ERROR)
            normalized_host = host.encode("idna").decode("ascii").lower()
            if normalized_host.endswith("."):
                normalized_host = normalized_host[:-1]
            if len(normalized_host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in normalized_host.split(".")
            ):
                raise ValueError(_URL_ERROR)
            authority = normalized_host
        if port is not None and port != 443:
            authority += f":{port}"
        path = parts.path.rstrip("/")
        if (
            not re.fullmatch(r"[/a-zA-Z0-9._~!$&'()*+,;=:@-]*", path)
            or "//" in path
            or any(piece in {".", ".."} for piece in path.split("/"))
        ):
            raise ValueError(_URL_ERROR)
        normalized = urlunsplit(("https", authority, path, "", ""))
        if len(normalized) > 2048:
            raise ValueError(_URL_ERROR)
        return normalized
    except ValueError:
        # Parser exceptions can contain the original input, including userinfo.
        raise ValueError(_URL_ERROR) from None


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _opaque_secret(value: str | SecretStr) -> str:
    """Require canonical unpadded base64url for 32 random bytes, for both secrets."""
    secret = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not isinstance(secret, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
        raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR)
    decoded = urlsafe_b64decode(secret + "=")
    if urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != secret:
        raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR)
    return secret


def _timestamp(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class AgentBootstrapStore:
    def __init__(self, inventory: InventoryStore, *, clock: Callable[[], float] = time) -> None:
        self.inventory = inventory
        self._clock = clock
        AgentBootstrapTicketModel.__table__.create(inventory._engine, checkfirst=True)

    @contextmanager
    def _coordinated_session(self) -> Iterator[Session]:
        with self.inventory._session() as session:
            if self.inventory._engine.dialect.name == "sqlite":
                # A process-local lock does not protect two workers or independent stores.
                # Acquire the write reservation before reading ticket or registration state.
                session.execute(text("BEGIN IMMEDIATE"))
            yield session

    @staticmethod
    def _server(session: Session, server_id: UUID | str, *, lock: bool = False) -> ServerModel:
        statement = select(ServerModel).where(ServerModel.id == str(server_id))
        if lock:
            statement = statement.with_for_update()
        server = session.scalar(statement)
        if server is None:
            raise ServerNotFoundError(f"server not found: {server_id}")
        return server

    @staticmethod
    def _agent(session: Session, server_id: str) -> AgentModel | None:
        return session.scalar(select(AgentModel).where(AgentModel.server_id == server_id))

    @staticmethod
    def _eligible(server: ServerModel, agent: AgentModel | None) -> bool:
        return (
            agent is None
            and server.last_heartbeat is None
            and server.status == ServerStatus.PENDING.value
            and bool(server.agent_token)
        )

    def _status(
        self,
        server: ServerModel,
        row: AgentBootstrapTicketModel | None,
        agent: AgentModel | None,
        now: float,
    ) -> AgentBootstrapStatus:
        status: AgentBootstrapState = "not_issued"
        if row is not None:
            if (
                row.revoked_at is not None
                or not self._eligible(server, agent)
                or not compare_digest(row.credential_hash, _digest(server.agent_token))
                or (row.claimed_at is None) != (row.claim_nonce_hash is None)
            ):
                status = "revoked"
            elif row.expires_at <= now:
                status = "expired"
            elif row.claimed_at is not None:
                status = "claimed"
            else:
                status = "issued"
        return AgentBootstrapStatus(
            server_id=server.id,
            server_name=server.name,
            status=status,
            issued_at=_timestamp(row.issued_at) if row is not None else None,
            expires_at=_timestamp(row.expires_at) if row is not None else None,
            claimed_at=_timestamp(row.claimed_at) if row is not None else None,
            agent_registered=agent is not None,
            agent_registered_at=_aware(agent.registered_at) if agent is not None else None,
            agent_last_seen_at=_aware(agent.last_seen_at) if agent is not None else None,
            agent_version=agent.agent_version if agent is not None else None,
            server_last_heartbeat=_aware(server.last_heartbeat),
        )

    def issue(
        self,
        server_id: UUID | str,
        control_url: str,
        transport: AgentBootstrapTransport = "auto",
    ) -> AgentBootstrapIssued:
        control_url = normalize_control_url(control_url)
        if transport not in ("auto", "websocket", "http"):
            raise ValueError("transport must be auto, websocket, or http")
        with self._coordinated_session() as session:
            server = self._server(session, server_id, lock=True)
            if not self._eligible(server, self._agent(session, server.id)):
                raise AgentBootstrapUnavailableError(
                    "Installation tickets are only available for servers that have never connected"
                )
            row = session.get(AgentBootstrapTicketModel, server.id)
            if row is not None and (row.claimed_at is not None or row.claim_nonce_hash is not None):
                # Claiming has already disclosed this server's long-lived credential. Until
                # there is a credential-rotation protocol, another installation must get a
                # new server identity, even after expiry, revocation, or a credential change.
                raise AgentBootstrapUnavailableError(
                    "This server has already claimed an installation ticket; resume the original "
                    "private installation job or create a new server"
                )
            # Read the clock after waiting for the database lock, not before it.
            now = self._clock()
            secret = token_urlsafe(32)
            if row is None:
                row = AgentBootstrapTicketModel(server_id=server.id)
                session.add(row)
            row.ticket_hash = _digest(secret)
            # This binds to the existing plaintext credential without copying it into this table.
            row.credential_hash = _digest(server.agent_token)
            row.control_url = control_url
            row.transport = transport
            row.issued_at = now
            row.expires_at = now + TICKET_LIFETIME_SECONDS
            row.claim_nonce_hash = None
            row.claimed_at = None
            row.revoked_at = None
            result = AgentBootstrapIssued(
                server_id=server.id,
                server_name=server.name,
                ticket=SecretStr(secret),
                control_url=control_url,
                transport=transport,
                issued_at=_timestamp(now),
                expires_at=_timestamp(row.expires_at),
            )
            session.commit()
            return result

    def read(self, server_id: UUID | str) -> AgentBootstrapStatus:
        with self.inventory._session() as session:
            # One statement gives the read-only status a single coherent database snapshot.
            result = session.execute(
                select(ServerModel, AgentBootstrapTicketModel, AgentModel)
                .select_from(ServerModel)
                .outerjoin(
                    AgentBootstrapTicketModel,
                    AgentBootstrapTicketModel.server_id == ServerModel.id,
                )
                .outerjoin(AgentModel, AgentModel.server_id == ServerModel.id)
                .where(ServerModel.id == str(server_id))
            ).one_or_none()
            if result is None:
                raise ServerNotFoundError(f"server not found: {server_id}")
            server, row, agent = result
            return self._status(server, row, agent, self._clock())

    def revoke(self, server_id: UUID | str) -> AgentBootstrapStatus:
        with self._coordinated_session() as session:
            server = self._server(session, server_id, lock=True)
            row = session.get(AgentBootstrapTicketModel, server.id)
            now = self._clock()
            if row is not None and row.revoked_at is None:
                row.revoked_at = now
            result = self._status(server, row, self._agent(session, server.id), now)
            # Never rotate/revoke ServerModel.agent_token or alter an installed Agent here.
            session.commit()
            return result

    def redeem(self, ticket: str | SecretStr, claim_nonce: str | SecretStr) -> AgentBootstrapConfig:
        ticket_hash = _digest(_opaque_secret(ticket))
        nonce_hash = _digest(_opaque_secret(claim_nonce))
        with self._coordinated_session() as session:
            server_id = session.scalar(
                select(AgentBootstrapTicketModel.server_id).where(
                    AgentBootstrapTicketModel.ticket_hash == ticket_hash
                )
            )
            if server_id is None:
                raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR)
            try:
                server = self._server(session, server_id, lock=True)
            except ServerNotFoundError:
                raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR) from None
            # Re-read after the server lock: another worker could have reissued/revoked the
            # ticket between the lookup and this lock on a non-SQLite database.
            row = session.get(AgentBootstrapTicketModel, server_id)
            now = self._clock()
            if (
                row is None
                or not compare_digest(row.ticket_hash, ticket_hash)
                or row.revoked_at is not None
                or row.expires_at <= now
                or not self._eligible(server, self._agent(session, server.id))
                or not compare_digest(row.credential_hash, _digest(server.agent_token))
                or (row.claimed_at is None) != (row.claim_nonce_hash is None)
            ):
                raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR)
            if row.claim_nonce_hash is None:
                row.claim_nonce_hash = nonce_hash
                row.claimed_at = now
                row.expires_at = min(row.expires_at, now + CLAIM_RETRY_SECONDS)
            elif not compare_digest(row.claim_nonce_hash, nonce_hash):
                raise AgentBootstrapRedemptionError(_REDEMPTION_ERROR)
            result = AgentBootstrapConfig(
                server_id=server.id,
                server_name=server.name,
                control_url=row.control_url,
                agent_token=SecretStr(server.agent_token),
                transport=row.transport,
                expires_at=_timestamp(row.expires_at),
            )
            session.commit()
            return result
