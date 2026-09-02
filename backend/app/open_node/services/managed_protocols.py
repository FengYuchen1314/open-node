"""Prepare and compile the five official Mihomo-backed managed listeners."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from secrets import token_hex, token_urlsafe
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.managed_protocols import (
    ManagedProtocolListener,
    ManagedProtocolSnapshot,
    ManagedProtocolUser,
    ManagedProtocolWireProfile,
)
from open_node.domain.subscriptions import ManagedProtocolProfile
from open_node.services.inventory import (
    ManagedNodeConflict,
    ManagedNodeModel,
    ServerModel,
    SubscriptionAccessModel,
)

ENDPOINT = "/api/child/managed-protocols"
SHARED_PROFILES = frozenset(
    {
        ManagedProtocolProfile.VLESS_REALITY_VISION,
        ManagedProtocolProfile.VLESS_XHTTP_REALITY_XMUX,
        ManagedProtocolProfile.ANYTLS_SHADOWTLS,
    }
)
_DYNAMIC_START = 49_152
_DYNAMIC_END = 65_535
_RESERVED_PORTS = frozenset({58_090, 62_031})
_WIRE_PROFILE = {
    ManagedProtocolProfile.VLESS_REALITY_VISION: ManagedProtocolWireProfile.VLESS_REALITY_VISION,
    ManagedProtocolProfile.VLESS_XHTTP_REALITY_XMUX: (
        ManagedProtocolWireProfile.VLESS_XHTTP_REALITY_XMUX
    ),
    ManagedProtocolProfile.ANYTLS_SHADOWTLS: ManagedProtocolWireProfile.ANYTLS_SHADOWTLS,
    ManagedProtocolProfile.MIERU: ManagedProtocolWireProfile.MIERU,
    ManagedProtocolProfile.SOCKS5: ManagedProtocolWireProfile.SOCKS5,
}


def _raw_b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _host(server: ServerModel) -> str:
    return (
        server.domain
        or server.ip_address
        or server.domain_v6
        or server.ip_address_v6
        or server.name
    )


class ManagedProtocols:
    def __init__(self, inventory) -> None:
        self.inventory = inventory

    @staticmethod
    def _allocate_port(session: Session, node: ManagedNodeModel) -> int:
        occupied = set(
            session.scalars(
                select(ManagedNodeModel.runtime_port).where(
                    ManagedNodeModel.server_id == node.server_id,
                    ManagedNodeModel.id != node.id,
                    ManagedNodeModel.runtime_port.is_not(None),
                )
            ).all()
        ) | set(_RESERVED_PORTS)
        width = _DYNAMIC_END - _DYNAMIC_START + 1
        seed = int(hashlib.sha256(node.id.encode()).hexdigest()[:8], 16) % width
        for offset in range(width):
            candidate = _DYNAMIC_START + (seed + offset) % width
            if candidate not in occupied:
                return candidate
        raise ManagedNodeConflict("No free managed runtime port remains on this server")

    @staticmethod
    def _reality_material() -> tuple[str, str, str]:
        private = X25519PrivateKey.generate()
        private_bytes = private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public_bytes = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return _raw_b64(private_bytes), _raw_b64(public_bytes), token_hex(8)

    def prepare(self, session: Session, server: ServerModel, node: ManagedNodeModel) -> None:
        """Generate secrets, stable tags, ports, and client-facing config once."""

        if not node.protocol_profile:
            return
        try:
            profile = ManagedProtocolProfile(node.protocol_profile)
        except ValueError:
            raise ManagedNodeConflict("Unknown managed protocol profile") from None

        host = _host(server)
        node.inbound_tag = f"open-node-{node.id}"
        runtime = deepcopy(node.runtime_config or {})
        config = deepcopy(node.config or {})
        node.runtime_port = (
            node.ix_port
            if profile == ManagedProtocolProfile.MIERU
            else node.runtime_port or self._allocate_port(session, node)
        )
        if node.runtime_port is None:
            raise ManagedNodeConflict("Managed protocol runtime port is unavailable")
        if node.runtime_port < 1_024:
            raise ManagedNodeConflict("Managed protocol runtime ports must be at least 1024")

        if profile in SHARED_PROFILES:
            sni = node.camouflage_sni
            if not sni:
                raise ManagedNodeConflict("Shared 443 profiles require a camouflage SNI")
            config.update(
                {
                    "server": host,
                    "port": 443,
                    "tls": True,
                    "servername": sni,
                    "sni": sni,
                    "client-fingerprint": "chrome",
                }
            )
            runtime["sni"] = sni

        if profile in {
            ManagedProtocolProfile.VLESS_REALITY_VISION,
            ManagedProtocolProfile.VLESS_XHTTP_REALITY_XMUX,
        }:
            if not all(
                isinstance(runtime.get(key), str)
                for key in ("reality_private_key", "reality_public_key", "reality_short_id")
            ):
                private_key, public_key, short_id = self._reality_material()
                runtime.update(
                    reality_private_key=private_key,
                    reality_public_key=public_key,
                    reality_short_id=short_id,
                )
            config.update(
                {
                    "type": "vless",
                    "encryption": "",
                    "udp": True,
                    "reality-opts": {
                        "public-key": runtime["reality_public_key"],
                        "short-id": runtime["reality_short_id"],
                    },
                }
            )
            if profile == ManagedProtocolProfile.VLESS_REALITY_VISION:
                config.update(network="tcp", flow="xtls-rprx-vision")
            else:
                runtime.setdefault("xhttp_path", "/" + token_urlsafe(18))
                runtime.setdefault("xhttp_host", sni)
                config.update(
                    network="xhttp",
                    alpn=["h2"],
                    **{
                        "xhttp-opts": {
                            "path": runtime["xhttp_path"],
                            "host": runtime["xhttp_host"],
                            "mode": "auto",
                            "reuse-settings": {
                                "max-concurrency": "16-32",
                                "h-max-reusable-secs": "1800-3000",
                                "h-keep-alive-period": 0,
                            },
                        }
                    },
                )
        elif profile == ManagedProtocolProfile.ANYTLS_SHADOWTLS:
            config.update(
                {
                    "type": "anytls",
                    "udp": True,
                    "shadow-tls-opts": {"version": 3},
                    "idle-session-check-interval": 30,
                    "idle-session-timeout": 30,
                    "min-idle-session": 0,
                }
            )
            config.pop("shadow-tls", None)
        elif profile == ManagedProtocolProfile.MIERU:
            config.update(
                {
                    "type": "mieru",
                    "server": node.domestic_entry_ip,
                    "port": node.domestic_entry_port,
                    "transport": "TCP",
                    "udp": False,
                    "ix-port": node.ix_port,
                    "ix-port-mapping": node.mieru_port_mapping_mode,
                }
            )
            runtime.setdefault("transport", "TCP")
        elif profile == ManagedProtocolProfile.SOCKS5:
            config.update(
                {
                    "type": "socks5",
                    "server": host,
                    "port": node.runtime_port,
                    "udp": True,
                }
            )

        node.runtime_config = runtime
        node.config = config
        node.updated_at = datetime.now(UTC)

    def _users_by_tag(self, session: Session, server_id: str) -> dict[str, list[dict[str, Any]]]:
        by_tag: dict[str, dict[str, dict[str, Any]]] = {}
        now = datetime.now(UTC)
        coordinator = self.inventory._subscription_access()
        rows = session.scalars(
            select(SubscriptionAccessModel).where(
                SubscriptionAccessModel.server_id == server_id
            )
        ).all()
        for row in rows:
            body, _ = coordinator.desired(session, row, now)
            for entry in body["entries"]:
                if not entry["enabled"]:
                    continue
                client = entry["client"]
                tag = entry["tag"]
                user: dict[str, Any] = {"name": str(client.get("email") or row.username)}
                if client.get("id"):
                    user["uuid"] = client["id"]
                elif client.get("password"):
                    user["password"] = client["password"]
                    user["name"] = str(client.get("username") or user["name"])
                elif client.get("pass"):
                    user["password"] = client["pass"]
                    user["name"] = str(client.get("user") or user["name"])
                by_tag.setdefault(tag, {})[user["name"]] = user
        return {tag: list(users.values()) for tag, users in by_tag.items()}

    def snapshot(self, session: Session, server: ServerModel) -> ManagedProtocolSnapshot:
        users = self._users_by_tag(session, server.id)
        nodes = session.scalars(
            select(ManagedNodeModel)
            .where(
                ManagedNodeModel.server_id == server.id,
                ManagedNodeModel.protocol_profile.is_not(None),
                ManagedNodeModel.node_type == "physical",
                ManagedNodeModel.removal_id.is_(None),
            )
            .order_by(ManagedNodeModel.created_at, ManagedNodeModel.id)
        ).all()
        listeners: list[ManagedProtocolListener] = []
        for node in nodes:
            profile = ManagedProtocolProfile(node.protocol_profile)
            runtime = node.runtime_config or {}
            if not node.runtime_port or not node.inbound_tag:
                raise ManagedNodeConflict(f"Managed node {node.name} has no runtime declaration")
            server_config: dict[str, Any]
            if profile in {
                ManagedProtocolProfile.VLESS_REALITY_VISION,
                ManagedProtocolProfile.VLESS_XHTTP_REALITY_XMUX,
            }:
                server_config = {
                    "sni": runtime["sni"],
                    "reality_private_key": runtime["reality_private_key"],
                    "reality_short_id": runtime["reality_short_id"],
                }
                if profile == ManagedProtocolProfile.VLESS_XHTTP_REALITY_XMUX:
                    server_config.update(
                        xhttp_path=runtime["xhttp_path"],
                        xhttp_host=runtime["xhttp_host"],
                    )
            elif profile == ManagedProtocolProfile.ANYTLS_SHADOWTLS:
                server_config = {"sni": runtime["sni"]}
            elif profile == ManagedProtocolProfile.MIERU:
                server_config = {"transport": runtime.get("transport", "TCP")}
            else:
                server_config = {"udp": True}
            listener_users = [
                ManagedProtocolUser.model_validate(user)
                for user in users.get(node.inbound_tag, [])
            ]
            listeners.append(
                ManagedProtocolListener(
                    tag=node.inbound_tag,
                    node_id=node.id,
                    profile=_WIRE_PROFILE[profile],
                    listen="127.0.0.1" if profile in SHARED_PROFILES else "0.0.0.0",
                    port=node.runtime_port,
                    enabled=node.enabled,
                    client_config={
                        "server": (node.config or {}).get("server"),
                        "port": (node.config or {}).get("port"),
                    },
                    server_config=server_config,
                    users=listener_users,
                )
            )
        canonical = [listener.model_dump(mode="json") for listener in listeners]
        revision = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ManagedProtocolSnapshot(revision=revision, listeners=listeners)

    def command(self, session: Session, server: ServerModel) -> AgentCommandCreate:
        snapshot = self.snapshot(session, server)
        return AgentCommandCreate(
            method="PUT",
            path=ENDPOINT,
            body=snapshot.model_dump(mode="json"),
            timeout_ms=60_000,
        )

    def reconcile_shared_ingress(self, server_id) -> AgentCommandCreate | None:
        """Persist the SNI map derived from managed nodes while preserving website settings."""

        from open_node.domain.shared_ingress import (
            SharedIngressConfiguration,
            SharedIngressRoute,
        )
        from open_node.services.shared_ingress import SharedIngressStore

        service = SharedIngressStore(self.inventory)
        current = service.get(server_id)
        with self.inventory._session() as session:
            nodes = session.scalars(
                select(ManagedNodeModel)
                .where(
                    ManagedNodeModel.server_id == str(server_id),
                    ManagedNodeModel.protocol_profile.in_(
                        [profile.value for profile in SHARED_PROFILES]
                    ),
                    ManagedNodeModel.node_type == "physical",
                    ManagedNodeModel.enabled.is_(True),
                    ManagedNodeModel.removal_id.is_(None),
                )
                .order_by(ManagedNodeModel.created_at, ManagedNodeModel.id)
            ).all()
        routes = [
            SharedIngressRoute(
                node_id=node.id,
                profile=node.protocol_profile,
                sni=node.camouflage_sni,
                upstream_address="127.0.0.1",
                upstream_port=node.runtime_port,
            )
            for node in nodes
        ]
        website = current.configuration.website if current.configuration else None
        if not routes and website is None:
            if current.configuration is not None:
                state = service.disable(server_id, expected_revision=current.revision)
                return AgentCommandCreate(
                    method="DELETE",
                    path="/api/child/nginx/shared-ingress",
                    body={"revision": state.revision},
                    timeout_ms=60_000,
                )
            return None
        configuration = SharedIngressConfiguration(
            listen_port=443,
            listen_ipv6=(
                current.configuration.listen_ipv6 if current.configuration else True
            ),
            routes=routes,
            website=website,
        )
        if current.configuration == configuration:
            return None
        state = service.save(
            server_id,
            configuration,
            expected_revision=current.revision,
        )
        if state.configuration is None:
            return None
        return AgentCommandCreate(
            method="PUT",
            path="/api/child/nginx/shared-ingress",
            body={
                "revision": state.revision,
                "configuration": state.configuration.model_dump(mode="json"),
            },
            timeout_ms=60_000,
        )
