"""Owner and consumer storage for durable server federation."""

import asyncio
import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from time import monotonic
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.inventory import (
    AgentCommandCreate,
    AgentCommandResultRequest,
    AgentCommandStatus,
    ProbeSysMetrics,
)
from open_node.domain.server_sharing import (
    TOKEN_PATTERN,
    FederatedServerCreate,
    FederatedServerRead,
    FederatedServersResponse,
    FederationCommandCreate,
    FederationCommandRead,
    FederationServerInfo,
    ServerShareCreate,
    ServerShareCreated,
    ServerShareRead,
    ServerShareRevoke,
    ServerShareRevoked,
    ServerSharesResponse,
    ServerSharingError,
)
from open_node.services.backup_coordination import BackupBusyError
from open_node.services.backup_runtime import backup_operation, run_in_backup_thread
from open_node.services.certificate_vault import CertificateVault
from open_node.services.federation_crypto import FederationSessionCache
from open_node.services.federation_transport import (
    FederationHTTPTransport,
    normalize_owner_url,
)
from open_node.services.inventory import (
    AgentCapabilityUnavailableError,
    AgentScanResultModel,
    Base,
    CommandModel,
    ServerModel,
    ServerNotFoundError,
    ServerTrafficModel,
    TelemetrySnapshotModel,
)

log = logging.getLogger(__name__)
FEDERATION_REFRESH_SECONDS = 5
FEDERATION_REFRESH_FAILURE_SECONDS = 30
FEDERATION_REFRESH_BATCH = 64


@dataclass(frozen=True, repr=False)
class FederationRefreshJob:
    identifier: str
    owner_url: str
    token: str = dataclass_field(repr=False)
    token_secret: str = dataclass_field(repr=False)
    revision: int


def _utc(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class ServerShareModel(Base):
    __tablename__ = "server_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80), default="")
    allow_manage_xray: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServerShareInboundModel(Base):
    __tablename__ = "server_share_inbounds"
    __table_args__ = (UniqueConstraint("share_id", "tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("server_shares.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServerShareCommandModel(Base):
    __tablename__ = "server_share_commands"

    command_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_commands.id", ondelete="CASCADE"), primary_key=True
    )
    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("server_shares.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(12))
    path: Mapped[str] = mapped_column(String(255))
    tag_action: Mapped[str] = mapped_column(String(16), default="")
    tag: Mapped[str] = mapped_column(String(255), default="")
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederatedServerModel(Base):
    __tablename__ = "federated_servers"

    id: Mapped[str] = mapped_column(
        String(36), ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(120), unique=True)
    owner_url: Mapped[str] = mapped_column(String(2048))
    token_secret: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(String(40), default="")
    snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederatedCommandRelayModel(Base):
    __tablename__ = "federated_command_relays"

    local_command_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_commands.id", ondelete="CASCADE"), primary_key=True
    )
    remote_command_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServerSharingStore:
    def __init__(self, inventory, *, transport=None):
        self.inventory = inventory
        self.transport = transport or FederationHTTPTransport()
        self.legacy_sessions = FederationSessionCache()
        self.inventory.federation_relay = self

    @contextmanager
    def _write(self):
        try:
            with self.inventory._coordinated_session() as session:
                try:
                    yield session
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    raise ServerSharingError(409, "server_share_conflict") from None
        except ServerSharingError:
            raise
        except SQLAlchemyError:
            raise ServerSharingError(
                503, "server_share_storage_unavailable"
            ) from None

    @staticmethod
    def _share_read(row):
        return ServerShareRead(
            id=UUID(row.id), server_id=UUID(row.server_id), label=row.label,
            allow_manage_xray=row.allow_manage_xray, revision=row.revision,
            created_at=_utc(row.created_at), license_required=False,
        )

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _active_share(session, identifier):
        row = session.get(ServerShareModel, str(identifier))
        if row is None or row.revoked_at is not None:
            raise ServerSharingError(404, "server_share_not_found")
        return row

    def _share_token(self, session, token):
        if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
            raise ServerSharingError(401, "server_share_token_invalid")
        row = session.scalar(select(ServerShareModel).where(
            ServerShareModel.token_hash == self._token_hash(token),
            ServerShareModel.revoked_at.is_(None),
        ))
        if row is None:
            raise ServerSharingError(401, "server_share_token_invalid")
        return row

    def create_share(self, payload):
        try:
            value = ServerShareCreate.model_validate(payload)
        except ValidationError:
            raise ServerSharingError(422, "server_share_invalid_request") from None
        token = token_urlsafe(32)
        now = datetime.now(UTC)
        with self._write() as session:
            if session.get(ServerModel, str(value.server_id)) is None:
                raise ServerSharingError(404, "server_share_not_found")
            if session.get(FederatedServerModel, str(value.server_id)) is not None:
                raise ServerSharingError(403, "server_share_forbidden")
            count = session.scalar(select(func.count()).select_from(ServerShareModel).where(
                ServerShareModel.server_id == str(value.server_id),
                ServerShareModel.revoked_at.is_(None),
            ))
            if count >= 20:
                raise ServerSharingError(409, "server_share_conflict")
            row = ServerShareModel(
                id=str(uuid4()), server_id=str(value.server_id),
                token_hash=self._token_hash(token), label=value.label,
                allow_manage_xray=value.allow_manage_xray, revision=0,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return ServerShareCreated(
                share=self._share_read(row), share_token=token, license_required=False
            )

    def list_shares(self, server_id):
        with self.inventory._session() as session:
            if session.get(ServerModel, str(server_id)) is None:
                raise ServerSharingError(404, "server_share_not_found")
            rows = session.scalars(select(ServerShareModel).where(
                ServerShareModel.server_id == str(server_id),
                ServerShareModel.revoked_at.is_(None),
            ).order_by(ServerShareModel.created_at.desc())).all()
            return ServerSharesResponse(
                shares=[self._share_read(row) for row in rows], license_required=False
            )

    @staticmethod
    def _tag_operation(payload):
        if payload.path != "/api/child/inbounds" or payload.method != "POST":
            return "", ""
        body = payload.body or {}
        action = str(body.get("action") or "add").lower()
        inbound = body.get("inbound") if isinstance(body.get("inbound"), dict) else {}
        tag = body.get("tag") or inbound.get("tag") or ""
        return action, tag if isinstance(tag, str) else ""

    @staticmethod
    def _owned_tags(session, share_id):
        return set(session.scalars(select(ServerShareInboundModel.tag).where(
            ServerShareInboundModel.share_id == share_id
        )))

    def _scope(self, session, share, payload):
        if share.allow_manage_xray:
            return
        if payload.path == "/api/child/subscription-access" and payload.method == "POST":
            body = payload.body or {}
            entries = body.get("entries") if isinstance(body, dict) else None
            owned = self._owned_tags(session, share.id)
            if (
                isinstance(entries, list)
                and entries
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("tag"), str)
                    and item["tag"] in owned
                    for item in entries
                )
            ):
                return
            raise ServerSharingError(403, "server_share_forbidden")
        if payload.path != "/api/child/inbounds":
            raise ServerSharingError(403, "server_share_forbidden")
        if payload.method == "GET":
            return
        action, tag = self._tag_operation(payload)
        if action == "add" and tag:
            return
        if not tag or tag not in self._owned_tags(session, share.id):
            raise ServerSharingError(403, "server_share_forbidden")

    def create_shared_command(self, token, payload):
        try:
            value = FederationCommandCreate.model_validate(payload)
            command_payload = AgentCommandCreate(
                method=value.method, path=value.path, body=value.body,
                timeout_ms=value.timeout_ms, stream=False,
            ).validate_wire_payload()
        except (ValidationError, ValueError):
            raise ServerSharingError(422, "server_share_invalid_request") from None
        now = datetime.now(UTC)
        try:
            with self._write() as session:
                share = self._share_token(session, token)
                self._scope(session, share, value)
                server = session.get(ServerModel, share.server_id)
                if server is None:
                    raise ServerSharingError(404, "server_share_not_found")
                command = self.inventory._create_command_model(
                    session, server, command_payload
                )
                session.flush()
                action, tag = self._tag_operation(value)
                session.add(ServerShareCommandModel(
                    command_id=command.id, share_id=share.id, method=value.method,
                    path=value.path, tag_action=action, tag=tag, created_at=now,
                ))
                if action == "add" and tag and tag not in self._owned_tags(session, share.id):
                    session.add(ServerShareInboundModel(
                        id=str(uuid4()), share_id=share.id, tag=tag, created_at=now,
                    ))
                session.flush()
                return self.inventory._command_read(command)
        except AgentCapabilityUnavailableError:
            raise ServerSharingError(409, "server_share_conflict") from None

    @staticmethod
    def _filter_inbounds(result, tags):
        empty = {"success": True, "inbounds": []}
        if not isinstance(result, dict) or not isinstance(result.get("inbounds"), list):
            return empty
        return {
            **{key: value for key, value in result.items() if key != "inbounds"},
            "inbounds": [item for item in result["inbounds"]
                         if isinstance(item, dict) and item.get("tag") in tags],
        }

    def _command_read(self, session, share, link, command):
        terminal = command.status in {
            AgentCommandStatus.SUCCEEDED.value,
            AgentCommandStatus.FAILED.value,
            AgentCommandStatus.SKIPPED.value,
        }
        succeeded = command.status == AgentCommandStatus.SUCCEEDED.value
        if terminal and not link.reconciled:
            if link.tag_action == "add" and link.tag and not succeeded:
                session.query(ServerShareInboundModel).filter_by(
                    share_id=share.id, tag=link.tag
                ).delete()
            elif link.tag_action in {"remove", "delete"} and link.tag and succeeded:
                session.query(ServerShareInboundModel).filter_by(
                    share_id=share.id, tag=link.tag
                ).delete()
            link.reconciled = True
        failed = command.status in {
            AgentCommandStatus.FAILED.value, AgentCommandStatus.SKIPPED.value
        } or bool(command.result_status and command.result_status >= 400)
        body = None if failed else command.result_body
        if (
            not share.allow_manage_xray and link.method == "GET"
            and link.path == "/api/child/inbounds"
        ):
            body = self._filter_inbounds(body, self._owned_tags(session, share.id))
        return FederationCommandRead(
            id=UUID(command.id), method=link.method, path=link.path,
            status=command.status, result_status=command.result_status,
            result_body=body, failed=failed, created_at=_utc(command.created_at),
            completed_at=_utc(command.completed_at), license_required=False,
        )

    def shared_command(self, token, command_id):
        with self._write() as session:
            share = self._share_token(session, token)
            link = session.get(ServerShareCommandModel, str(command_id))
            command = session.get(CommandModel, str(command_id))
            if link is None or command is None or link.share_id != share.id:
                raise ServerSharingError(404, "server_share_not_found")
            return self._command_read(session, share, link, command)

    def server_info(self, token):
        with self.inventory._session() as session:
            share = self._share_token(session, token)
            server = session.get(ServerModel, share.server_id)
            if server is None:
                raise ServerSharingError(404, "server_share_not_found")
            scan = session.get(AgentScanResultModel, server.id)
            telemetry = session.scalar(
                select(TelemetrySnapshotModel)
                .where(TelemetrySnapshotModel.server_id == server.id)
                .order_by(TelemetrySnapshotModel.received_at.desc())
                .limit(1)
            )
            probe_sys = None
            if telemetry is not None and telemetry.sysmetrics:
                metrics = ProbeSysMetrics.model_validate(telemetry.sysmetrics)
                probe_sys = {
                    **metrics.model_dump(mode="json"),
                    "upload_speed": server.current_upload_speed,
                    "download_speed": server.current_download_speed,
                    "cumulative_up": telemetry.system_tx_total or 0,
                    "cumulative_down": telemetry.system_rx_total or 0,
                    "has_network": (
                        telemetry.system_tx_total is not None
                        or telemetry.system_rx_total is not None
                    ),
                    "at": int(_utc(telemetry.received_at).timestamp()),
                }
            identifier = UUID(server.id)
            data = dict(
                name=server.name, status=server.status, ip_address=server.ip_address,
                ip_address_v6=server.ip_address_v6, domain=server.domain,
                domain_v6=server.domain_v6, ipv6_enabled=server.ipv6_enabled,
                xray_mode=server.xray_mode, traffic_limit=server.traffic_limit,
                traffic_reset_day=server.traffic_reset_day,
                current_upload_speed=server.current_upload_speed,
                current_download_speed=server.current_download_speed,
                xray_running=scan.xray_running if scan else None,
                xray_version=scan.xray_version if scan else None,
                nginx=scan.nginx if scan else None,
                probe_sys=probe_sys,
                last_heartbeat=_utc(server.last_heartbeat), license_required=False,
                allow_manage_xray=share.allow_manage_xray,
            )
        try:
            traffic_used = self.inventory._server_traffic().read(identifier).used
        except (ServerNotFoundError, ValueError):
            traffic_used = 0
        return FederationServerInfo(**data, traffic_used=traffic_used)

    def revoke(self, identifier, payload):
        try:
            value = ServerShareRevoke.model_validate(payload)
        except ValidationError:
            raise ServerSharingError(422, "server_share_invalid_request") from None
        now = datetime.now(UTC)
        commands = []
        try:
            with self._write() as session:
                share = self._active_share(session, identifier)
                if share.revision != value.expected_revision:
                    raise ServerSharingError(409, "server_share_conflict")
                tags = self._owned_tags(session, share.id) if value.delete_inbounds else set()
                server = session.get(ServerModel, share.server_id)
                if server is None:
                    raise ServerSharingError(404, "server_share_not_found")
                for tag in sorted(tags):
                    command = self.inventory._create_command_model(
                        session, server, AgentCommandCreate(
                            method="POST", path="/api/child/inbounds",
                            body={"action": "remove", "tag": tag}, timeout_ms=30_000,
                        )
                    )
                    commands.append(command)
                share.revoked_at, share.revision = now, share.revision + 1
                session.query(ServerShareInboundModel).filter_by(share_id=share.id).delete()
                session.flush()
                reads = [self.inventory._command_read(command) for command in commands]
        except AgentCapabilityUnavailableError:
            raise ServerSharingError(409, "server_share_conflict") from None
        return reads

    def revoked_response(self, commands):
        return ServerShareRevoked(
            cleanup_commands=[FederationCommandRead(
                id=item.id, method=item.method, path=item.path, status=item.status.value,
                result_status=item.result_status, result_body=None, failed=False,
                created_at=item.created_at, completed_at=item.completed_at,
                license_required=False,
            ) for item in commands],
            license_required=False,
        )

    def _cipher(self, session):
        root = self.inventory.federation_state_dir
        if root is None:
            raise ServerSharingError(503, "server_share_storage_unavailable")
        initialized = session.scalar(
            select(func.count()).select_from(FederatedServerModel)
        ) > 0
        try:
            return CertificateVault(Path(root), initialized=initialized).cipher()
        except (InvalidToken, OSError, ValueError, TypeError):
            raise ServerSharingError(
                503, "server_share_storage_unavailable"
            ) from None

    def _seal(self, session, identifier, owner_url, token):
        payload = json.dumps({
            "version": 1, "server": identifier, "owner_url": owner_url,
            "purpose": "federation-token", "token": token,
        }, sort_keys=True, separators=(",", ":")).encode()
        return self._cipher(session).encrypt(payload).decode()

    def _open(self, session, row):
        try:
            value = json.loads(self._cipher(session).decrypt(row.token_secret.encode()))
            if value != {
                "version": 1, "server": row.id, "owner_url": row.owner_url,
                "purpose": "federation-token", "token": value.get("token"),
            } or TOKEN_PATTERN.fullmatch(value["token"]) is None:
                raise ValueError()
            return value["token"]
        except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
            raise ServerSharingError(
                503, "server_share_storage_unavailable"
            ) from None

    @staticmethod
    def _federated_read(row):
        try:
            return FederatedServerRead(
                id=UUID(row.id), name=row.name, owner_url=row.owner_url, prefix=row.prefix,
                revision=row.revision, info=FederationServerInfo.model_validate(row.snapshot),
                last_synced_at=_utc(row.last_synced_at), created_at=_utc(row.created_at),
                license_required=False,
            )
        except (ValidationError, ValueError, TypeError, AttributeError):
            raise ServerSharingError(
                503, "server_share_storage_unavailable"
            ) from None

    @staticmethod
    def _projection_name(session, preferred, identifier):
        base = preferred.strip() or "共享服务器"
        existing = session.scalar(select(ServerModel.id).where(ServerModel.name == base))
        if existing in {None, identifier}:
            return base
        suffix = f" · 分享 {identifier[:8]}"
        candidate = base[: max(1, 120 - len(suffix))] + suffix
        if session.scalar(select(ServerModel.id).where(ServerModel.name == candidate)):
            raise ServerSharingError(409, "server_share_conflict")
        return candidate

    @staticmethod
    def _new_projection(identifier, name, info, now):
        return ServerModel(
            id=identifier,
            name=name,
            agent_token=token_urlsafe(32),
            status=info.status,
            ip_address=info.ip_address,
            ip_address_v6=info.ip_address_v6,
            domain=info.domain,
            domain_v6=info.domain_v6,
            connection_mode="auto",
            listen_port=0,
            pull_address=None,
            pull_address_v6=None,
            pull_port=0,
            ipv6_enabled=info.ipv6_enabled,
            traffic_limit=info.traffic_limit,
            traffic_reset_day=info.traffic_reset_day,
            last_traffic_reset_at=now,
            traffic_stats_mode="both",
            traffic_source="xray",
            xray_mode=info.xray_mode,
            current_upload_speed=info.current_upload_speed,
            current_download_speed=info.current_download_speed,
            last_heartbeat=_utc(info.last_heartbeat),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _sync_projection(session, server, name, info, now):
        server.name = name
        for field in (
            "status", "ip_address", "ip_address_v6", "domain", "domain_v6",
            "ipv6_enabled", "traffic_limit", "traffic_reset_day", "xray_mode",
            "current_upload_speed", "current_download_speed", "last_heartbeat",
        ):
            value = getattr(info, field)
            if field == "last_heartbeat":
                value = _utc(value)
            setattr(server, field, value)
        server.updated_at = now

        traffic = session.get(ServerTrafficModel, (server.id, "xray"))
        if traffic is None:
            traffic = ServerTrafficModel(
                server_id=server.id,
                source="xray",
                counters={},
                upload=0,
                download=0,
                baseline_upload=0,
                baseline_download=0,
            )
            session.add(traffic)
        traffic.counters = {"federation_snapshot": [info.traffic_used, 0]}
        traffic.upload = info.traffic_used
        traffic.download = 0
        traffic.baseline_upload = 0
        traffic.baseline_download = 0
        traffic.last_reported_at = now

        scan = session.get(AgentScanResultModel, server.id)
        if scan is None:
            scan = AgentScanResultModel(
                server_id=server.id,
                xray_running=bool(info.xray_running),
                nginx=info.nginx.model_dump(mode="json") if info.nginx else None,
                xray_version=info.xray_version,
                xray_capabilities={},
                inbounds=[],
                device_kicks={},
                config_modified=False,
                config_added_sections=[],
                message="分享服务器状态来自拥有方联邦快照",
                reported_at=_utc(info.last_heartbeat) or now,
                updated_at=now,
            )
            session.add(scan)
        else:
            scan.xray_running = bool(info.xray_running)
            scan.xray_version = info.xray_version
            if info.nginx is not None:
                scan.nginx = info.nginx.model_dump(mode="json")
            scan.message = "分享服务器状态来自拥有方联邦快照"
            scan.reported_at = _utc(info.last_heartbeat) or now
            scan.updated_at = now

        if info.probe_sys is not None:
            metrics = ProbeSysMetrics.model_validate(info.probe_sys.model_dump(mode="json"))
            latest = session.scalar(
                select(TelemetrySnapshotModel)
                .where(TelemetrySnapshotModel.server_id == server.id)
                .order_by(TelemetrySnapshotModel.received_at.desc())
                .limit(1)
            )
            if latest is None or now - _utc(latest.received_at) >= timedelta(seconds=30):
                latest = TelemetrySnapshotModel(
                    id=str(uuid4()), server_id=server.id, reported_at=now, received_at=now,
                    stats=None, online_users={}, online_collection=None,
                    user_speeds={}, conn_counts={}, latency=[],
                )
                session.add(latest)
            latest.reported_at = now
            latest.received_at = now
            latest.system_rx_total = info.probe_sys.cumulative_down
            latest.system_tx_total = info.probe_sys.cumulative_up
            latest.system_boot_time_unix = max(0, int(now.timestamp()) - metrics.uptime)
            latest.sysmetrics = metrics.model_dump(mode="json")

    def ensure_projections(self):
        with self._write() as session:
            rows = session.scalars(
                select(FederatedServerModel).order_by(FederatedServerModel.created_at)
            ).all()
            for row in rows:
                info = FederationServerInfo.model_validate(row.snapshot)
                server = session.get(ServerModel, row.id)
                if server is None:
                    name = self._projection_name(session, row.name, row.id)
                    row.name = name
                    server = self._new_projection(row.id, name, info, _utc(row.created_at))
                    session.add(server)
                    session.flush()
                self._sync_projection(
                    session, server, row.name, info, _utc(row.last_synced_at)
                )

    def add_federated(self, payload):
        try:
            value = FederatedServerCreate.model_validate(payload)
            owner_url = normalize_owner_url(value.owner_url)
        except ValidationError:
            raise ServerSharingError(422, "server_share_invalid_request") from None
        token = value.share_token.get_secret_value()
        info = self.transport.server_info(owner_url, token)
        now, identifier = datetime.now(UTC), str(uuid4())
        with self._write() as session:
            name = value.name or info.name
            if session.scalar(select(FederatedServerModel.id).where(
                FederatedServerModel.name == name
            )) or session.scalar(select(ServerModel.id).where(ServerModel.name == name)):
                raise ServerSharingError(409, "server_share_conflict")
            row = FederatedServerModel(
                id=identifier, name=name, owner_url=owner_url,
                token_secret=self._seal(session, identifier, owner_url, token),
                prefix=value.prefix, snapshot=info.model_dump(mode="json"), revision=0,
                last_synced_at=now, created_at=now,
            )
            server = self._new_projection(identifier, name, info, now)
            session.add_all([server, row])
            session.flush()
            self._sync_projection(session, server, name, info, now)
            session.flush()
            return self._federated_read(row)

    def list_federated(self):
        with self.inventory._session() as session:
            rows = session.scalars(select(FederatedServerModel).order_by(
                FederatedServerModel.created_at
            )).all()
            return FederatedServersResponse(
                servers=[self._federated_read(row) for row in rows],
                license_required=False,
            )

    def _federated_secret(self, identifier):
        with self.inventory._session() as session:
            row = session.get(FederatedServerModel, str(identifier))
            if row is None:
                raise ServerSharingError(404, "server_share_not_found")
            return row.owner_url, self._open(session, row), row.prefix, row.revision

    def refresh_federated(self, identifier, expected_revision):
        owner_url, token, _prefix, revision = self._federated_secret(identifier)
        if revision != expected_revision:
            raise ServerSharingError(409, "server_share_conflict")
        info = self.transport.server_info(owner_url, token)
        now = datetime.now(UTC)
        with self._write() as session:
            row = session.get(FederatedServerModel, str(identifier))
            if row is None:
                raise ServerSharingError(404, "server_share_not_found")
            changed = session.execute(update(FederatedServerModel).where(
                FederatedServerModel.id == row.id,
                FederatedServerModel.revision == expected_revision,
            ).values(
                snapshot=info.model_dump(mode="json"), revision=expected_revision + 1,
                last_synced_at=now,
            ).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise ServerSharingError(409, "server_share_conflict")
            session.expire_all()
            row = session.get(FederatedServerModel, row.id)
            server = session.get(ServerModel, row.id)
            if server is None:
                server = self._new_projection(row.id, row.name, info, now)
                session.add(server)
                session.flush()
            self._sync_projection(session, server, row.name, info, now)
            session.flush()
            return self._federated_read(row)

    def automatic_refresh_jobs(self, *, now=None):
        now = now or datetime.now(UTC)
        due = now - timedelta(seconds=FEDERATION_REFRESH_SECONDS)
        jobs = []
        with self.inventory._session() as session:
            rows = session.scalars(
                select(FederatedServerModel)
                .where(FederatedServerModel.last_synced_at <= due)
                .order_by(FederatedServerModel.last_synced_at, FederatedServerModel.id)
                .limit(FEDERATION_REFRESH_BATCH)
            ).all()
            for row in rows:
                try:
                    token = self._open(session, row)
                except ServerSharingError:
                    continue
                jobs.append(FederationRefreshJob(
                    identifier=row.id,
                    owner_url=row.owner_url,
                    token=token,
                    token_secret=row.token_secret,
                    revision=row.revision,
                ))
        return jobs

    def apply_automatic_refresh(self, job, info, *, now=None):
        now = now or datetime.now(UTC)
        with self._write() as session:
            row = session.get(FederatedServerModel, job.identifier)
            if (
                row is None
                or row.owner_url != job.owner_url
                or row.token_secret != job.token_secret
                or row.revision != job.revision
            ):
                return False
            row.snapshot = info.model_dump(mode="json")
            row.last_synced_at = now
            server = session.get(ServerModel, row.id)
            if server is None:
                server = self._new_projection(row.id, row.name, info, now)
                session.add(server)
                session.flush()
            self._sync_projection(session, server, row.name, info, now)
            session.flush()
            return True

    @staticmethod
    def _prefix_payload(prefix, payload):
        if not prefix or payload.path != "/api/child/inbounds" or payload.method != "POST":
            return payload
        body = dict(payload.body or {})
        action = str(body.get("action") or "add").lower()
        inbound = dict(body.get("inbound") or {}) if isinstance(body.get("inbound"), dict) else {}
        if action == "add":
            tag = inbound.get("tag")
            if not isinstance(tag, str) or not tag:
                raise ServerSharingError(422, "server_share_invalid_request")
            inbound["tag"] = prefix + tag
            body["inbound"] = inbound
        else:
            tag = body.get("tag") or inbound.get("tag")
            if not isinstance(tag, str) or not tag.startswith(prefix):
                raise ServerSharingError(403, "server_share_forbidden")
        return payload.model_copy(update={"body": body})

    def manage_federated(self, identifier, payload):
        try:
            value = FederationCommandCreate.model_validate(payload)
        except ValidationError:
            raise ServerSharingError(422, "server_share_invalid_request") from None
        owner_url, token, prefix, _revision = self._federated_secret(identifier)
        result = self.transport.manage(
            owner_url, token, self._prefix_payload(prefix, value)
        )
        self._sync_federated_inbounds(identifier, result)
        return result

    def federated_command(self, identifier, command_id):
        owner_url, token, _prefix, _revision = self._federated_secret(identifier)
        result = self.transport.command(owner_url, token, command_id)
        self._sync_federated_inbounds(identifier, result)
        return result

    def _sync_federated_inbounds(self, identifier, command):
        if (
            command.method != "GET"
            or command.path != "/api/child/inbounds"
            or command.status != AgentCommandStatus.SUCCEEDED.value
            or command.failed
            or (command.result_status is not None and command.result_status >= 400)
        ):
            return
        body = command.result_body
        if not isinstance(body, dict) or not isinstance(body.get("inbounds"), list):
            return
        inbounds = [item for item in body["inbounds"] if isinstance(item, dict)][:512]
        now = datetime.now(UTC)
        with self._write() as session:
            row = session.get(FederatedServerModel, str(identifier))
            if row is None:
                raise ServerSharingError(404, "server_share_not_found")
            scan = session.get(AgentScanResultModel, row.id)
            if scan is None:
                scan = AgentScanResultModel(
                    server_id=row.id,
                    xray_running=bool((row.snapshot or {}).get("xray_running")),
                    xray_version=(row.snapshot or {}).get("xray_version"),
                    xray_capabilities={},
                    inbounds=inbounds,
                    device_kicks={},
                    config_modified=False,
                    config_added_sections=[],
                    message="分享服务器入站已从拥有方同步",
                    reported_at=now,
                    updated_at=now,
                )
                session.add(scan)
            else:
                scan.inbounds = inbounds
                scan.message = "分享服务器入站已从拥有方同步"
                scan.reported_at = now
                scan.updated_at = now

    @staticmethod
    def _relay_allowed(command):
        return command.method == "POST" and command.path in {
            "/api/child/inbounds",
            "/api/child/subscription-access",
        } and not command.query and not command.stream

    def is_federated(self, identifier):
        with self.inventory._session() as session:
            return session.get(FederatedServerModel, str(identifier)) is not None

    def dispatch_agent_command(self, command_read):
        now = datetime.now(UTC)
        relay_created = False
        with self._write() as session:
            command = session.get(CommandModel, str(command_read.id))
            row = session.get(FederatedServerModel, str(command_read.server_id))
            if command is None or row is None:
                return command_read
            if command.status in {
                AgentCommandStatus.SUCCEEDED.value,
                AgentCommandStatus.FAILED.value,
                AgentCommandStatus.SKIPPED.value,
            }:
                return self.inventory._command_read(command)
            if not self._relay_allowed(command):
                self.inventory._terminalize_unleaseable_command(
                    session, command, now,
                    error="Not sent: shared server operation is outside the federation scope",
                    result_status=403,
                )
                session.flush()
                return self.inventory._command_read(command)
            relay = session.get(FederatedCommandRelayModel, command.id)
            if relay is None:
                if not self.inventory._claim_command_lease(session, command, now):
                    session.flush()
                    return self.inventory._command_read(command)
                session.expire(command)
                command = session.get(CommandModel, command.id)
                relay = FederatedCommandRelayModel(
                    local_command_id=command.id,
                    remote_command_id=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(relay)
                session.flush()
                relay_created = True
            owner_url = row.owner_url
            token = self._open(session, row)
            prefix = row.prefix
            remote_id = relay.remote_command_id
            payload = FederationCommandCreate(
                method=command.method,
                path=command.path,
                body=command.body,
                timeout_ms=command.timeout_ms,
            )

        # A durable relay with no remote id means the previous process may have
        # died after dispatching the owner command.  Re-sending a credential
        # mutation would be unsafe, so keep the outcome explicitly unknown.
        if remote_id is None and not relay_created:
            return self._finish_relay(
                command_read.id,
                status=502,
                body=None,
                error="Federation dispatch outcome is unknown; command was not retried",
            )

        try:
            remote = (
                self.transport.command(owner_url, token, UUID(remote_id))
                if remote_id
                else self.transport.manage(
                    owner_url, token, self._prefix_payload(prefix, payload)
                )
            )
        except ServerSharingError:
            return self._finish_relay(
                command_read.id, status=502, body=None,
                error="Federation dispatch failed or its outcome is unknown",
            )

        with self._write() as session:
            relay = session.get(FederatedCommandRelayModel, str(command_read.id))
            if relay is None:
                raise ServerSharingError(404, "server_share_not_found")
            if relay.remote_command_id is None:
                relay.remote_command_id = str(remote.id)
            relay.updated_at = datetime.now(UTC)
        if remote.status in {
            AgentCommandStatus.SUCCEEDED.value,
            AgentCommandStatus.FAILED.value,
            AgentCommandStatus.SKIPPED.value,
        }:
            return self._finish_relay(
                command_read.id,
                status=remote.result_status or (502 if remote.failed else 200),
                body=remote.result_body,
                error="Federated owner rejected the command" if remote.failed else None,
            )
        with self.inventory._session() as session:
            command = session.get(CommandModel, str(command_read.id))
            return self.inventory._command_read(command)

    def _finish_relay(self, identifier, *, status, body, error):
        with self._write() as session:
            command = session.get(CommandModel, str(identifier))
            if command is None:
                raise ServerSharingError(404, "server_share_not_found")
            server = session.get(ServerModel, command.server_id)
            if server is None:
                raise ServerSharingError(404, "server_share_not_found")
            self.inventory._apply_command_result(
                session,
                server,
                command,
                AgentCommandResultRequest(
                    token="federation", status=status, body=body, error=error
                ),
            )
            session.flush()
            return self.inventory._command_read(command)

    def delete_federated(self, identifier, expected_revision):
        with self._write() as session:
            row = session.get(FederatedServerModel, str(identifier))
            if row is None:
                raise ServerSharingError(404, "server_share_not_found")
            if row.revision != expected_revision:
                raise ServerSharingError(409, "server_share_conflict")
            server = session.get(ServerModel, row.id)
            session.delete(row)
            session.flush()
            if server is not None:
                session.delete(server)


class FederationRefreshWorker:
    def __init__(self, store, *, backup_writes, interval=FEDERATION_REFRESH_SECONDS):
        self.store = store
        self.backup_writes = backup_writes
        self.interval = interval
        self.failure_until = {}

    async def tick(self):
        with backup_operation(self.backup_writes):
            jobs = await run_in_backup_thread(self.store.automatic_refresh_jobs)
        if not jobs:
            return False

        attempted = False
        for job in jobs:
            if monotonic() < self.failure_until.get(job.identifier, 0):
                continue
            attempted = True
            try:
                info = await asyncio.to_thread(
                    self.store.transport.server_info, job.owner_url, job.token
                )
            except ServerSharingError:
                self.failure_until[job.identifier] = (
                    monotonic() + FEDERATION_REFRESH_FAILURE_SECONDS
                )
                continue
            with backup_operation(self.backup_writes):
                applied = await run_in_backup_thread(
                    self.store.apply_automatic_refresh, job, info
                )
            if applied:
                self.failure_until.pop(job.identifier, None)
        return attempted

    async def run(self):
        while True:
            try:
                worked = await self.tick()
            except asyncio.CancelledError:
                raise
            except BackupBusyError:
                worked = False
            except Exception as exc:
                log.warning(
                    "Federation refresh cycle failed (%s); saved state is retained",
                    type(exc).__name__,
                )
                await asyncio.sleep(self.interval)
                continue
            await asyncio.sleep(1 if worked else self.interval)
