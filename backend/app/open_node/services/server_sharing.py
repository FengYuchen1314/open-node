"""Owner and consumer storage for durable server federation."""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
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

from open_node.domain.inventory import AgentCommandCreate, AgentCommandStatus
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
from open_node.services.certificate_vault import CertificateVault
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
)


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


class ServerSharingStore:
    def __init__(self, inventory, *, transport=None):
        self.inventory = inventory
        self.transport = transport or FederationHTTPTransport()

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
            scan.message = "分享服务器状态来自拥有方联邦快照"
            scan.reported_at = _utc(info.last_heartbeat) or now
            scan.updated_at = now

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
        return self.transport.manage(
            owner_url, token, self._prefix_payload(prefix, value)
        )

    def federated_command(self, identifier, command_id):
        owner_url, token, _prefix, _revision = self._federated_secret(identifier)
        return self.transport.command(owner_url, token, command_id)

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
