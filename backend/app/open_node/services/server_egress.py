from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select

from open_node.domain.changes import AgentChangeSetCreate, AgentChangeSetStepCreate
from open_node.domain.inventory import (
    AgentCommandCreate,
    AgentOutboundTLSPinProbeOperationRequest,
)
from open_node.domain.server_egress import (
    ServerEgressApplyRequest,
    ServerEgressApplyResponse,
    ServerEgressCandidateRead,
    ServerEgressCatalogRead,
    ServerEgressPreviewRead,
    ServerEgressPreviewRequest,
    ServerEgressRemovePreviewRequest,
    ServerEgressRemoveRequest,
    ServerEgressRoutingSelector,
    ServerEgressTLSProbeDescriptor,
    normalize_tls_certificate_pins,
)
from open_node.services import subscription_clients
from open_node.services.inventory import (
    AgentScanResultModel,
    ManagedNodeModel,
    ServerModel,
)


class ServerEgressConflict(ValueError):
    pass


class ServerEgressNotFoundError(ValueError):
    pass


class ServerEgress:
    """Plans an authenticated managed node as another server's Xray egress.

    Credentials and concrete Xray outbounds are generated only inside the
    control plane.  The public API exposes candidates and a non-secret preview;
    apply compiles a guarded atomic change set across the affected server or
    servers.

    Runtime-imported physical nodes use the same model and are supported.  A
    subscriber external node is intentionally not a candidate: it is an
    owner-scoped encrypted subscription record with neither a Server/Agent
    identity nor operator authority to reuse its credential on a server.
    """

    SUPPORTED_PROTOCOLS = {
        "vless",
        "vmess",
        "trojan",
        "shadowsocks",
        "anytls",
        "snell",
        "hysteria",
        "socks",
        "http",
    }
    CLIENT_CONTAINERS = {
        "vless": "clients",
        "vmess": "clients",
        "trojan": "clients",
        "shadowsocks": "clients",
        "hysteria": "clients",
        "anytls": "users",
        "snell": "users",
        "socks": "accounts",
        "http": "accounts",
    }
    # Xray's JSON loader ignores unknown fields, while the Agent preserves the
    # source document byte-for-byte through guarded mutations.  This sidecar is
    # deliberately non-secret and lets removal distinguish an exclude that this
    # workflow inserted from one that an operator configured beforehand.
    MANAGED_SNI_STATE_KEY = "_openNodeManagedEgressSniffing"
    TLS_PIN_CONFIG_KEYS = (
        "tls-fingerprint",
        "pinnedPeerCertSha256",
        "pcs",
        "pinSHA256",
    )

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _protocol(value: str) -> str:
        normalized = value.strip().lower()
        return {
            "ss": "shadowsocks",
            "hy2": "hysteria",
            "hysteria2": "hysteria",
            "socks5": "socks",
        }.get(normalized, normalized)

    @classmethod
    def _configured_tls_pin(cls, node_config: Any) -> str | None:
        """Read the certificate-pin aliases emitted by the official parser.

        Invalid aliases are deliberately treated as absent.  Catalog callers
        then receive a probe descriptor and preview remains fail-closed until a
        valid, explicit pin is supplied.
        """

        if not isinstance(node_config, dict):
            return None
        for key in cls.TLS_PIN_CONFIG_KEYS:
            value = node_config.get(key)
            if not isinstance(value, str):
                continue
            try:
                return normalize_tls_certificate_pins(value)
            except ValueError:
                continue
        return None

    @classmethod
    def _tls_probe_descriptor(
        cls,
        outbound: dict[str, Any],
    ) -> ServerEgressTLSProbeDescriptor | None:
        """Extract only public TLS peer coordinates from the generated outbound.

        Credentials remain inside ``settings`` and are never copied.  REALITY
        authenticates with its own public-key contract, while Hysteria uses
        QUIC/UDP, so neither is eligible for the TCP certificate probe.
        """

        stream = outbound.get("streamSettings")
        if not isinstance(stream, dict):
            return None
        security = str(stream.get("security") or "").strip().lower()
        if security == "reality":
            return None
        if security != "tls":
            return None
        network = str(stream.get("network") or "tcp").strip().lower()
        if network == "hysteria" or "hysteriaSettings" in stream:
            return None

        protocol = cls._protocol(str(outbound.get("protocol") or ""))
        settings = outbound.get("settings")
        if not isinstance(settings, dict):
            raise ServerEgressConflict("Generated TLS outbound settings are invalid")
        if protocol == "anytls":
            target = settings
        else:
            targets = settings.get("vnext")
            if targets is None:
                targets = settings.get("servers")
            if not isinstance(targets, list) or len(targets) != 1 or not isinstance(
                targets[0], dict
            ):
                raise ServerEgressConflict(
                    "Generated TLS outbound must contain exactly one probe target"
                )
            target = targets[0]

        tls_settings = stream.get("tlsSettings")
        if not isinstance(tls_settings, dict):
            raise ServerEgressConflict("Generated TLS outbound lacks tlsSettings")
        descriptor = {
            "protocol": protocol,
            "address": target.get("address"),
            "port": target.get("port"),
            "server_name": tls_settings.get("serverName") or None,
            "alpn": tls_settings.get("alpn", []),
        }
        try:
            # Reuse the public operation schema so every descriptor returned by
            # this endpoint is directly valid as a bounded Agent probe request.
            validated = AgentOutboundTLSPinProbeOperationRequest.model_validate(descriptor)
            return ServerEgressTLSProbeDescriptor.model_validate(
                validated.model_dump(exclude={"timeout_ms", "command_timeout_ms"})
            )
        except ValueError as exc:
            raise ServerEgressConflict(
                "Generated TLS outbound does not expose a valid public probe target"
            ) from exc

    @classmethod
    def _secure_generated_tls_outbound(
        cls,
        outbound: dict[str, Any],
        node_config: Any,
        requested_pin: str | None,
    ) -> tuple[ServerEgressTLSProbeDescriptor | None, str | None]:
        """Normalize a generated outbound to the Xray-core-mmwx TLS contract."""

        stream = outbound.get("streamSettings")
        if not isinstance(stream, dict):
            if requested_pin is not None:
                raise ServerEgressConflict(
                    "pinned_peer_cert_sha256 is only valid for a TCP TLS managed egress"
                )
            return None, None

        tls_settings = stream.get("tlsSettings")
        used_insecure_tls = (
            isinstance(tls_settings, dict) and tls_settings.get("allowInsecure") is True
        )
        if isinstance(tls_settings, dict):
            # The fork rejects this field after 2026-06-01 even when false.
            tls_settings.pop("allowInsecure", None)

        descriptor = cls._tls_probe_descriptor(outbound)
        if descriptor is None:
            security = str(stream.get("security") or "").strip().lower()
            network = str(stream.get("network") or "tcp").strip().lower()
            is_hysteria = network == "hysteria" or "hysteriaSettings" in stream
            if security == "tls" and is_hysteria:
                if not isinstance(tls_settings, dict):
                    raise ServerEgressConflict("Generated Hysteria outbound lacks tlsSettings")
                resolved_pin = requested_pin or cls._configured_tls_pin(node_config)
                if resolved_pin is not None:
                    resolved_pin = normalize_tls_certificate_pins(resolved_pin)
                    tls_settings["pinnedPeerCertSha256"] = resolved_pin
                    return None, resolved_pin
                if used_insecure_tls:
                    raise ServerEgressConflict(
                        "Hysteria managed egress cannot preserve skip-cert-verify; "
                        "configure a valid tls-fingerprint or pinnedPeerCertSha256"
                    )
                # QUIC cannot use the TCP probe.  With no insecure flag or pin,
                # Hysteria retains ordinary system-root/SNI verification.
                return None, None
            if requested_pin is not None:
                raise ServerEgressConflict(
                    "pinned_peer_cert_sha256 is not used by REALITY or non-TLS egress"
                )
            return None, None
        if not isinstance(tls_settings, dict):
            raise ServerEgressConflict("Generated TLS outbound lacks tlsSettings")

        resolved_pin = requested_pin or cls._configured_tls_pin(node_config)
        if resolved_pin is None:
            raise ServerEgressConflict(
                "TLS managed egress requires pinned_peer_cert_sha256; "
                "probe the candidate from the source server first"
            )
        # ``requested_pin`` was normalized by the request model; aliases from
        # node.config are normalized above.  Re-normalize at the injection
        # boundary to make this invariant explicit.
        resolved_pin = normalize_tls_certificate_pins(resolved_pin)
        tls_settings["pinnedPeerCertSha256"] = resolved_pin
        return descriptor, resolved_pin

    @staticmethod
    def _identity(source_id: str, target_id: str) -> tuple[str, str, str]:
        source = source_id.replace("-", "")[:12]
        target = target_id.replace("-", "")[:12]
        suffix = f"{source}:{target}"
        return (
            f"managed-egress:{suffix}",
            f"managed-egress-rule:{suffix}",
            f"open_node_egress__{source}__{target}",
        )

    @staticmethod
    def _config(snapshot) -> dict[str, Any]:
        try:
            value = json.loads(snapshot.config)
        except (TypeError, ValueError) as exc:
            raise ServerEgressConflict("The saved Xray configuration is invalid") from exc
        if not isinstance(value, dict):
            raise ServerEgressConflict("The saved Xray configuration is not an object")
        return value

    def _snapshot(self, session, server: ServerModel):
        if self.store._pending_xray_config_snapshot(session, server.id) is not None:
            raise ServerEgressConflict(
                f"Server {server.name} has an unreviewed Xray configuration recovery"
            )
        snapshot = self.store._current_xray_config_snapshot(session, server.id)
        if snapshot is None:
            raise ServerEgressConflict(
                f"Server {server.name} needs a current Xray configuration snapshot"
            )
        self._config(snapshot)
        return snapshot

    def _server(self, session, identifier: UUID | str) -> ServerModel:
        server = session.get(ServerModel, str(identifier))
        if server is None:
            raise ServerEgressNotFoundError(f"server not found: {identifier}")
        shared_reason = self._shared_server_reason(session, server.id)
        if shared_reason:
            raise ServerEgressConflict(shared_reason)
        return server

    @staticmethod
    def _shared_server_reason(session, server_id: str) -> str | None:
        from open_node.services.server_sharing import FederatedServerModel

        if session.get(FederatedServerModel, server_id) is not None:
            return "Federated servers cannot participate in managed egress changes"
        return None

    def _target(self, session, source: ServerModel, identifier: UUID) -> tuple[Any, Any, str]:
        node = session.get(ManagedNodeModel, str(identifier))
        if node is None:
            raise ServerEgressNotFoundError(f"managed node not found: {identifier}")
        target_server = session.get(ServerModel, node.server_id)
        reason = self._unavailable_reason(session, source, node, target_server)
        if reason:
            raise ServerEgressConflict(reason)
        return node, target_server, self._protocol(node.protocol)

    def _unavailable_reason(self, session, source, node, target_server) -> str | None:
        if node.node_type != "physical":
            return "Only physical managed nodes can be used as an egress"
        if not node.enabled or node.removal_id:
            return "The managed node is disabled or being removed"
        if target_server is None:
            return "The managed node points to a missing server"
        if reason := self._shared_server_reason(session, target_server.id):
            return reason
        if not node.inbound_tag or not node.config:
            return "The managed node lacks an authenticated inbound or proxy config"
        if self._protocol(node.protocol) not in self.SUPPORTED_PROTOCOLS:
            return "The managed node protocol cannot be converted to an Xray outbound"
        return None

    @staticmethod
    def _tagged(entries: Any, tag: str, kind: str) -> tuple[int | None, dict | None]:
        if not isinstance(entries, list):
            raise ServerEgressConflict(f"Xray {kind} must be an array")
        matches = [
            (index, item)
            for index, item in enumerate(entries)
            if isinstance(item, dict) and item.get("tag") == tag
        ]
        if len(matches) > 1:
            raise ServerEgressConflict(f"Xray {kind} tag {tag} is duplicated")
        return matches[0] if matches else (None, None)

    @staticmethod
    def _rule_index(rules: Any, marktag: str) -> tuple[int | None, dict | None]:
        if not isinstance(rules, list):
            raise ServerEgressConflict("Xray routing rules must be an array")
        matches = [
            (index, rule)
            for index, rule in enumerate(rules)
            if isinstance(rule, dict) and rule.get("marktag") == marktag
        ]
        if len(matches) > 1:
            raise ServerEgressConflict(f"Xray routing marktag {marktag} is duplicated")
        return matches[0] if matches else (None, None)

    def catalog(self, server_id: UUID) -> ServerEgressCatalogRead:
        with self.store._session() as session:
            source = self._server(session, server_id)
            source_snapshot = self.store._current_xray_config_snapshot(session, source.id)
            source_config = self._config(source_snapshot) if source_snapshot else None
            pending = self.store._pending_xray_config_snapshot(session, source.id)
            nodes = session.scalars(
                select(ManagedNodeModel).order_by(ManagedNodeModel.name, ManagedNodeModel.id)
            ).all()
            candidates = []
            for node in nodes:
                if node.node_type != "physical":
                    continue
                target_server = session.get(ServerModel, node.server_id)
                reason = self._unavailable_reason(session, source, node, target_server)
                if reason is None and pending is not None:
                    reason = "The source server has an unreviewed Xray configuration recovery"
                if reason is None and source_snapshot is None:
                    reason = "The source server needs a current Xray configuration snapshot"
                if reason is None and self.store._pending_xray_config_snapshot(
                    session, node.server_id
                ):
                    reason = "The target server has an unreviewed Xray configuration recovery"
                target_snapshot = self.store._current_xray_config_snapshot(session, node.server_id)
                if reason is None and target_snapshot is None:
                    reason = "The target server needs a current Xray configuration snapshot"
                outbound_tag, marktag, email = self._identity(source.id, node.id)
                has_outbound = is_default = has_rule = has_client = False
                tls_probe = None
                if reason is None:
                    try:
                        _, generated_outbound = self._material(
                            session,
                            node,
                            target_server,
                            self._protocol(node.protocol),
                            email,
                        )
                        descriptor = self._tls_probe_descriptor(generated_outbound)
                        if descriptor is not None:
                            if self._configured_tls_pin(node.config) is None:
                                tls_probe = descriptor
                            else:
                                self._secure_generated_tls_outbound(
                                    generated_outbound,
                                    node.config,
                                    None,
                                )
                        else:
                            # In particular, reject a Hysteria candidate that
                            # would lose its legacy insecure/self-signed mode.
                            self._secure_generated_tls_outbound(
                                generated_outbound,
                                node.config,
                                None,
                            )
                    except ServerEgressConflict as exc:
                        reason = str(exc)
                if source_config is not None:
                    outbounds = source_config.get("outbounds", [])
                    index, _ = self._tagged(outbounds, outbound_tag, "outbounds")
                    has_outbound = index is not None
                    is_default = index == 0
                    routing = source_config.get("routing") or {}
                    if not isinstance(routing, dict):
                        raise ServerEgressConflict("Xray routing must be an object")
                    _, rule = self._rule_index(routing.get("rules", []), marktag)
                    has_rule = rule is not None
                if (
                    target_snapshot is not None
                    and target_server is not None
                    and node.inbound_tag
                    and self._protocol(node.protocol) in self.SUPPORTED_PROTOCOLS
                ):
                    try:
                        has_client = (
                            self._existing_client(
                                self._config(target_snapshot),
                                node,
                                self._protocol(node.protocol),
                                email,
                            )
                            is not None
                        )
                    except ServerEgressConflict as exc:
                        if reason is None:
                            reason = str(exc)
                configured = has_outbound or has_rule or has_client
                candidates.append(
                    ServerEgressCandidateRead(
                        node_id=UUID(node.id),
                        node_name=node.name,
                        server_id=UUID(node.server_id),
                        server_name=target_server.name if target_server else "Missing server",
                        protocol=node.protocol,
                        available=reason is None,
                        unavailable_reason=reason,
                        configured=configured,
                        is_default=is_default,
                        has_routing_rule=has_rule,
                        has_target_client=has_client,
                        needs_repair=configured and (not has_outbound or not has_client),
                        tls_probe=tls_probe,
                    )
                )
            return ServerEgressCatalogRead(
                server_id=UUID(source.id),
                candidates=candidates,
                source_snapshot_id=UUID(source_snapshot.id) if source_snapshot else None,
                source_snapshot_revision=source_snapshot.config_hash if source_snapshot else None,
            )

    @staticmethod
    def _routing_rule(selector: ServerEgressRoutingSelector, tag: str, marktag: str) -> dict:
        rule: dict[str, Any] = {"type": "field", "marktag": marktag, "outboundTag": tag}
        for field, target in (
            ("domains", "domain"),
            ("ips", "ip"),
            ("inbound_tags", "inboundTag"),
            ("users", "user"),
            ("protocols", "protocol"),
        ):
            values = getattr(selector, field)
            if values:
                rule[target] = values
        if selector.port is not None:
            rule["port"] = selector.port
        if selector.network is not None:
            rule["network"] = selector.network
        return rule

    @staticmethod
    def _routing_priority(rule: dict[str, Any]) -> int:
        """Mirror MMWX rule ordering so selector rules precede catch-alls."""

        outbound_tag = str(rule.get("outboundTag") or "")
        if outbound_tag == "nginx":
            return -1
        if outbound_tag.startswith("tunnel-"):
            return 0
        marktag = str(rule.get("marktag") or "")
        if marktag.startswith("routed:"):
            return 1 if len(marktag.split(":")) == 4 else 2
        if marktag.startswith("managed-egress-rule:"):
            return 2
        if marktag in {"home_broadband_warp", "speedtest_warp"}:
            return 3
        return 4

    @classmethod
    def _managed_sni_state(cls, config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get(cls.MANAGED_SNI_STATE_KEY)
        if raw is None:
            return {"version": 1, "inbounds": {}}
        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or not isinstance(raw.get("inbounds"), dict)
        ):
            raise ServerEgressConflict("Managed egress sniffing ownership state is invalid")
        state = deepcopy(raw)
        for inbound_tag, entry in state["inbounds"].items():
            if (
                not isinstance(inbound_tag, str)
                or not isinstance(entry, dict)
                or not isinstance(entry.get("ownedDomains", []), list)
                or not isinstance(entry.get("references", {}), dict)
            ):
                raise ServerEgressConflict("Managed egress sniffing ownership state is invalid")
            if any(not isinstance(item, str) for item in entry.get("ownedDomains", [])):
                raise ServerEgressConflict("Managed egress sniffing ownership state is invalid")
            for outbound_tag, domains in entry.get("references", {}).items():
                if not isinstance(outbound_tag, str) or not isinstance(domains, list) or any(
                    not isinstance(item, str) for item in domains
                ):
                    raise ServerEgressConflict(
                        "Managed egress sniffing ownership state is invalid"
                    )
        return state

    @staticmethod
    def _canonical_domains(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise ServerEgressConflict("Xray sniffing domainsExcluded must be an array")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise ServerEgressConflict(
                    "Xray sniffing domainsExcluded entries must be strings"
                )
            domain = value.strip().lower()
            if domain and domain not in seen:
                result.append(domain)
                seen.add(domain)
        return result

    @staticmethod
    def _sniffing_domains(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise ServerEgressConflict("Xray sniffing domainsExcluded must be an array")
        if any(not isinstance(value, str) for value in values):
            raise ServerEgressConflict("Xray sniffing domainsExcluded entries must be strings")
        return deepcopy(values)

    @classmethod
    def _tagged_inbound(cls, config: dict[str, Any], tag: str) -> dict[str, Any]:
        _, inbound = cls._tagged(config.get("inbounds", []), tag, "inbounds")
        if inbound is None:
            raise ServerEgressConflict(f"Source inbound {tag} is absent from the current snapshot")
        return inbound

    @classmethod
    def _all_inbound_tags(cls, config: dict[str, Any]) -> list[str]:
        inbounds = config.get("inbounds", [])
        if not isinstance(inbounds, list):
            raise ServerEgressConflict("Xray inbounds must be an array")
        tags: list[str] = []
        for inbound in inbounds:
            if not isinstance(inbound, dict):
                continue
            tag = inbound.get("tag")
            if not isinstance(tag, str) or not tag:
                continue
            if tag in tags:
                raise ServerEgressConflict(f"Xray inbounds tag {tag} is duplicated")
            tags.append(tag)
        return tags

    @classmethod
    def _related_source_inbounds(
        cls,
        config: dict[str, Any],
        outbound_tag: str,
    ) -> list[str]:
        """Return inbounds whose traffic can reach this outbound.

        An explicit ``inboundTag`` selector is exact.  A default outbound or a
        rule without ``inboundTag`` can receive traffic from every tagged
        inbound, matching Xray's field-rule semantics.
        """

        all_tags = cls._all_inbound_tags(config)
        outbounds = config.get("outbounds", [])
        if not isinstance(outbounds, list):
            raise ServerEgressConflict("Xray outbounds must be an array")
        if outbounds and isinstance(outbounds[0], dict) and outbounds[0].get("tag") == outbound_tag:
            return all_tags

        routing = config.get("routing") or {}
        if not isinstance(routing, dict):
            raise ServerEgressConflict("Xray routing must be an object")
        rules = routing.get("rules", [])
        if not isinstance(rules, list):
            raise ServerEgressConflict("Xray routing rules must be an array")
        selected: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("outboundTag") != outbound_tag:
                continue
            inbound_tags = rule.get("inboundTag")
            if inbound_tags is None:
                return all_tags
            if not isinstance(inbound_tags, list) or any(
                not isinstance(tag, str) or not tag for tag in inbound_tags
            ):
                raise ServerEgressConflict("Xray routing inboundTag must be an array of tags")
            for tag in inbound_tags:
                cls._tagged_inbound(config, tag)
                if tag not in selected:
                    selected.append(tag)
        return selected

    @classmethod
    def _other_managed_reality_domains(
        cls,
        config: dict[str, Any],
        outbound_tag: str,
        inbound_tag: str,
        extractor,
    ) -> set[str]:
        outbounds = config.get("outbounds", [])
        if not isinstance(outbounds, list):
            raise ServerEgressConflict("Xray outbounds must be an array")
        result: set[str] = set()
        for outbound in outbounds:
            if (
                not isinstance(outbound, dict)
                or outbound.get("tag") == outbound_tag
                or not str(outbound.get("tag") or "").startswith("managed-egress:")
            ):
                continue
            other_tag = str(outbound.get("tag"))
            if inbound_tag not in cls._related_source_inbounds(config, other_tag):
                continue
            result.update(
                domain.strip().lower()
                for domain in extractor(outbound)
                if isinstance(domain, str) and domain.strip()
            )
        return result

    @classmethod
    def _remove_managed_sniffing_reference(
        cls,
        config: dict[str, Any],
        outbound_tag: str,
        extractor,
    ) -> dict[str, Any]:
        state = cls._managed_sni_state(config)
        inbounds_state = state["inbounds"]
        for inbound_tag in list(inbounds_state):
            entry = inbounds_state[inbound_tag]
            references = entry.setdefault("references", {})
            references.pop(outbound_tag, None)
            referenced = {
                domain.strip().lower()
                for domains in references.values()
                for domain in domains
                if domain.strip()
            }
            owned = cls._canonical_domains(entry.setdefault("ownedDomains", []))
            other_domains = cls._other_managed_reality_domains(
                config,
                outbound_tag,
                inbound_tag,
                extractor,
            )
            removable = {
                domain
                for domain in owned
                if domain not in referenced and domain not in other_domains
            }
            if removable:
                inbound = cls._tagged_inbound(config, inbound_tag)
                sniffing = inbound.get("sniffing") or {}
                if not isinstance(sniffing, dict):
                    raise ServerEgressConflict("Xray inbound sniffing must be an object")
                values = cls._sniffing_domains(sniffing.get("domainsExcluded", []))
                sniffing["domainsExcluded"] = [
                    value
                    for value in values
                    if value.strip().lower() not in removable
                ]
                inbound["sniffing"] = sniffing
            entry["ownedDomains"] = [domain for domain in owned if domain not in removable]
            if references or entry["ownedDomains"]:
                entry["references"] = references
            else:
                inbounds_state.pop(inbound_tag)
        if inbounds_state:
            config[cls.MANAGED_SNI_STATE_KEY] = state
        else:
            config.pop(cls.MANAGED_SNI_STATE_KEY, None)
        return config

    def _apply_managed_sniffing(
        self,
        config: dict[str, Any],
        outbound_tag: str,
        domains: list[str],
        inbound_tags: list[str],
    ) -> dict[str, Any]:
        extractor = self.store._extract_reality_sni_domains
        self._remove_managed_sniffing_reference(config, outbound_tag, extractor)
        normalized = self._canonical_domains(domains)
        if not normalized or not inbound_tags:
            return config

        state = self._managed_sni_state(config)
        for inbound_tag in inbound_tags:
            inbound = self._tagged_inbound(config, inbound_tag)
            sniffing = inbound.get("sniffing") or {}
            if not isinstance(sniffing, dict):
                raise ServerEgressConflict("Xray inbound sniffing must be an object")
            excluded = self._sniffing_domains(sniffing.get("domainsExcluded", []))
            excluded_keys = {value.strip().lower() for value in excluded}
            entry = state["inbounds"].setdefault(
                inbound_tag,
                {"ownedDomains": [], "references": {}},
            )
            owned = self._canonical_domains(entry.setdefault("ownedDomains", []))
            for domain in normalized:
                if domain not in excluded_keys:
                    excluded.append(domain)
                    excluded_keys.add(domain)
                    owned.append(domain)
            entry["ownedDomains"] = list(dict.fromkeys(owned))
            entry.setdefault("references", {})[outbound_tag] = normalized
            sniffing["domainsExcluded"] = excluded
            inbound["sniffing"] = sniffing
        config[self.MANAGED_SNI_STATE_KEY] = state
        return config

    @classmethod
    def _assert_same_server_route_is_safe(
        cls,
        config: dict[str, Any],
        outbound_tag: str,
        target_inbound_tag: str,
    ) -> None:
        outbounds = config.get("outbounds", [])
        if outbounds and isinstance(outbounds[0], dict) and outbounds[0].get("tag") == outbound_tag:
            raise ServerEgressConflict(
                "A same-server managed egress cannot be promoted to the default outbound"
            )
        routing = config.get("routing") or {}
        rules = routing.get("rules", []) if isinstance(routing, dict) else []
        if not isinstance(rules, list):
            raise ServerEgressConflict("Xray routing rules must be an array")
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("outboundTag") != outbound_tag:
                continue
            inbound_tags = rule.get("inboundTag")
            if not isinstance(inbound_tags, list) or not inbound_tags:
                raise ServerEgressConflict(
                    "A same-server managed egress rule must select explicit source inbounds"
                )
            if target_inbound_tag in inbound_tags:
                raise ServerEgressConflict(
                    "A same-server managed egress cannot route its target inbound back to itself"
                )

    @staticmethod
    def _balancer_selectors(value: Any) -> list[str]:
        if isinstance(value, str):
            selectors = [value]
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            selectors = value
        else:
            raise ServerEgressConflict("Xray balancer selector must be a string or array")
        if any(not selector for selector in selectors):
            raise ServerEgressConflict("Xray balancer selectors must not be empty")
        return selectors

    @classmethod
    def _assert_managed_outbound_not_balanced(
        cls,
        config: dict[str, Any],
        outbound_tag: str,
    ) -> None:
        """Keep managed egress edges statically attributable to direct rules.

        Xray balancer selectors are tag prefixes and can start selecting a new
        managed outbound without changing the rule that targets the balancer.
        A fallback has the same problem.  Neither is accepted for an outbound
        owned by this workflow, so the route graph below remains auditable.
        """

        routing = config.get("routing") or {}
        if not isinstance(routing, dict):
            raise ServerEgressConflict("Xray routing must be an object")
        balancers = routing.get("balancers", [])
        if not isinstance(balancers, list):
            raise ServerEgressConflict("Xray routing balancers must be an array")
        for balancer in balancers:
            if not isinstance(balancer, dict):
                raise ServerEgressConflict("Xray routing balancers must contain objects")
            fallback = balancer.get("fallbackTag")
            if fallback is not None and not isinstance(fallback, str):
                raise ServerEgressConflict("Xray balancer fallbackTag must be a string")
            selectors = cls._balancer_selectors(balancer.get("selector", []))
            if fallback == outbound_tag or any(
                outbound_tag.startswith(selector) for selector in selectors
            ):
                raise ServerEgressConflict(
                    "Managed egress outbounds cannot be selected by an Xray balancer or fallback"
                )

    def _managed_route_graph(
        self,
        session,
        source: ServerModel,
        source_candidate: dict[str, Any],
    ) -> dict[tuple[str, str], set[tuple[str, str]]]:
        """Build direct managed-egress edges from the current saved snapshots.

        Missing or malformed snapshots outside the server being changed cannot
        prove an edge and are ignored.  Every edge that *can* be attributed to
        a physical managed node is retained, including cross-server edges.
        """

        servers = session.scalars(select(ServerModel)).all()
        nodes = session.scalars(select(ManagedNodeModel)).all()
        configs: dict[str, dict[str, Any]] = {source.id: source_candidate}
        for server in servers:
            if server.id == source.id:
                continue
            snapshot = self.store._current_xray_config_snapshot(session, server.id)
            if snapshot is None:
                continue
            try:
                configs[server.id] = self._config(snapshot)
            except ServerEgressConflict:
                continue

        graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for source_server_id, config in configs.items():
            for node in nodes:
                if node.node_type != "physical" or not node.inbound_tag or not node.server_id:
                    continue
                outbound_tag, _, _ = self._identity(source_server_id, node.id)
                try:
                    _, outbound = self._tagged(
                        config.get("outbounds", []),
                        outbound_tag,
                        "outbounds",
                    )
                    if outbound is None:
                        continue
                    self._assert_managed_outbound_not_balanced(config, outbound_tag)
                    source_inbounds = self._related_source_inbounds(config, outbound_tag)
                except ServerEgressConflict:
                    if source_server_id == source.id:
                        raise
                    continue
                target_vertex = (node.server_id, node.inbound_tag)
                for inbound_tag in source_inbounds:
                    graph.setdefault((source_server_id, inbound_tag), set()).add(target_vertex)
        return graph

    @staticmethod
    def _route_reaches(
        graph: dict[tuple[str, str], set[tuple[str, str]]],
        start: tuple[str, str],
        destination: tuple[str, str],
    ) -> bool:
        pending = [start]
        seen: set[tuple[str, str]] = set()
        while pending:
            current = pending.pop()
            if current == destination:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        return False

    def _assert_managed_route_is_acyclic(
        self,
        session,
        source: ServerModel,
        source_candidate: dict[str, Any],
        source_inbounds: list[str],
        target_server: ServerModel,
        target_inbound_tag: str,
    ) -> None:
        graph = self._managed_route_graph(session, source, source_candidate)
        target = (target_server.id, target_inbound_tag)
        for inbound_tag in source_inbounds:
            origin = (source.id, inbound_tag)
            if self._route_reaches(graph, target, origin):
                raise ServerEgressConflict(
                    "The managed egress route would create a direct managed-egress cycle"
                )

    def _existing_client(self, target_config, node, protocol, email):
        _, inbound = self._tagged(target_config.get("inbounds", []), node.inbound_tag, "inbounds")
        if inbound is None:
            raise ServerEgressConflict(
                f"Target inbound {node.inbound_tag} is absent from the current snapshot"
            )
        inbound_protocol = self._protocol(str(inbound.get("protocol") or ""))
        if inbound_protocol != protocol:
            raise ServerEgressConflict("Target inbound protocol no longer matches the managed node")
        settings = inbound.get("settings")
        if not isinstance(settings, dict):
            raise ServerEgressConflict("Target inbound settings are invalid")
        container = self.CLIENT_CONTAINERS[protocol]
        clients = settings.get(container, [])
        if not isinstance(clients, list):
            raise ServerEgressConflict("Target inbound clients are invalid")
        matches = [
            item for item in clients if isinstance(item, dict) and item.get("email") == email
        ]
        if len(matches) > 1:
            raise ServerEgressConflict("Target egress client identity is duplicated")
        return deepcopy(matches[0]) if matches else None

    def _mutated_target_client(self, target_config, node, protocol, email, client):
        candidate = deepcopy(target_config)
        _, inbound = self._tagged(candidate.get("inbounds", []), node.inbound_tag, "inbounds")
        if inbound is None:
            raise ServerEgressConflict(
                f"Target inbound {node.inbound_tag} is absent from the current snapshot"
            )
        settings = inbound.get("settings")
        if not isinstance(settings, dict):
            raise ServerEgressConflict("Target inbound settings are invalid")
        container = self.CLIENT_CONTAINERS[protocol]
        clients = settings.get(container, [])
        if not isinstance(clients, list):
            raise ServerEgressConflict("Target inbound clients are invalid")
        matches = [
            index
            for index, item in enumerate(clients)
            if isinstance(item, dict) and item.get("email") == email
        ]
        if len(matches) > 1:
            raise ServerEgressConflict("Target egress client identity is duplicated")
        old_index = matches[0] if matches else None
        if old_index is not None:
            clients.pop(old_index)
        if client is not None:
            clients.insert(old_index if old_index is not None else len(clients), deepcopy(client))
        settings[container] = clients
        inbound["settings"] = settings
        return candidate

    def _material(self, session, node, target_server, protocol, email):
        credential = self.store._generate_subscription_credential(
            protocol=protocol,
            # SOCKS/HTTP authenticate by account name, so every source needs
            # its own stable identity on a shared target inbound.
            username=email,
            email=email,
            node_config=node.config or {},
        )
        context = {
            "username": "open-node-egress",
            "user_email": email,
            "display_name": "Open Node managed egress",
            "client_email": email,
            "plan_id": "",
            "plan_name": "",
            "node_id": node.id,
            "node_name": node.name,
            "protocol": node.protocol,
            "server_id": target_server.id,
            "server_name": target_server.name,
            "server_domain": self.store._server_subscription_host(target_server),
            "server_host": self.store._server_subscription_host(target_server),
        }
        for key, value in credential.items():
            if isinstance(value, str | int | float | bool):
                context[f"credential_{key}"] = str(value)
                if key in {"id", "password", "auth", "psk", "user", "pass"}:
                    context[key] = str(value)

        client: dict[str, Any] = {}
        if node.client_template:
            rendered_client = self.store._render_template(node.client_template, context)
            if not isinstance(rendered_client, dict):
                raise ServerEgressConflict("Target client template did not render to an object")
            client.update(rendered_client)
        client.update(credential)
        if protocol == "vless" and (node.config or {}).get("flow"):
            client.setdefault("flow", node.config["flow"])
        if protocol == "shadowsocks":
            method = str(
                (node.config or {}).get("cipher") or (node.config or {}).get("method") or ""
            )
            if method and not method.startswith("2022-"):
                client.setdefault("method", method)
        client["email"] = email

        rendered = self.store._render_template(node.config, context)
        if not isinstance(rendered, dict) or not rendered:
            raise ServerEgressConflict("Target proxy config did not render to an object")
        proxy = dict(rendered)
        proxy.setdefault("name", node.name)
        proxy.setdefault("type", self.store._proxy_type_for_protocol(node.protocol))
        runtime_key_required = proxy.pop("server-key-source", None) == "runtime"
        if runtime_key_required:
            scan = session.get(AgentScanResultModel, target_server.id)
            server_key = self.store._runtime_shadowsocks_server_key(scan, node)
            if not server_key:
                raise ServerEgressConflict(
                    "Target Shadowsocks node needs a current matching runtime server key"
                )
            proxy["password"] = server_key
        self.store._apply_credential_to_proxy(proxy, protocol, client)
        try:
            outbound = subscription_clients.xray_outbound(proxy)
        except (KeyError, TypeError, ValueError) as exc:
            raise ServerEgressConflict(
                "Target node is not compatible with an Xray outbound"
            ) from exc
        return client, outbound

    def _mutated_source(self, source_config, outbound, tag, marktag, payload):
        candidate = deepcopy(source_config)
        outbounds = candidate.setdefault("outbounds", [])
        old_index, existing = self._tagged(outbounds, tag, "outbounds")
        if old_index is not None:
            outbounds.pop(old_index)
        outbound = deepcopy(outbound)
        outbound["tag"] = tag
        if payload.promote_to_default:
            outbounds.insert(0, outbound)
        elif old_index is None:
            outbounds.append(outbound)
        else:
            outbounds.insert(min(old_index, len(outbounds)), outbound)

        routing_action = (
            "keep"
            if "routing" not in payload.model_fields_set
            else "remove"
            if payload.routing is None
            else "set"
        )
        routing = candidate.get("routing")
        if routing is None:
            routing = {}
        if not isinstance(routing, dict):
            raise ServerEgressConflict("Xray routing must be an object")
        rules = routing.get("rules", [])
        if not isinstance(rules, list):
            raise ServerEgressConflict("Xray routing rules must be an array")
        rule_index, old_rule = self._rule_index(rules, marktag)
        if routing_action != "keep" and rule_index is not None:
            rules.pop(rule_index)
        if routing_action == "set":
            rule = self._routing_rule(payload.routing, tag, marktag)
            if rule_index is not None:
                insert_at = min(rule_index, len(rules))
            else:
                priority = self._routing_priority(rule)
                insert_at = next(
                    (
                        index
                        for index, existing_rule in enumerate(rules)
                        if isinstance(existing_rule, dict)
                        and self._routing_priority(existing_rule) >= priority
                    ),
                    len(rules),
                )
            rules.insert(insert_at, rule)
        if routing_action == "set" or candidate.get("routing") is not None:
            routing["rules"] = rules
            candidate["routing"] = routing
        for field, key in (
            ("observatory", "observatory"),
            ("burst_observatory", "burstObservatory"),
        ):
            if field not in payload.model_fields_set:
                continue
            value = getattr(payload, field)
            if value is None:
                candidate.pop(key, None)
            else:
                candidate[key] = deepcopy(value)
        return candidate, existing, old_rule, outbounds[0].get("tag") == tag

    def _prepare(self, session, server_id, payload):
        source = self._server(session, server_id)
        target, target_server, protocol = self._target(session, source, payload.target_node_id)
        source_snapshot = self._snapshot(session, source)
        target_snapshot = self._snapshot(session, target_server)
        source_config = self._config(source_snapshot)
        target_config = self._config(target_snapshot)
        same_server = source.id == target_server.id
        tag, marktag, email = self._identity(source.id, target.id)
        old_client = self._existing_client(target_config, target, protocol, email)
        client, outbound = self._material(session, target, target_server, protocol, email)
        tls_probe, pinned_peer_cert_sha256 = self._secure_generated_tls_outbound(
            outbound,
            target.config,
            payload.pinned_peer_cert_sha256,
        )
        client_candidate = self._mutated_target_client(
            target_config, target, protocol, email, client
        )
        candidate, existing_outbound, existing_rule, will_be_default = self._mutated_source(
            client_candidate if same_server else source_config,
            outbound,
            tag,
            marktag,
            payload,
        )
        self._assert_managed_outbound_not_balanced(candidate, tag)
        if same_server:
            self._assert_same_server_route_is_safe(candidate, tag, target.inbound_tag)
        reality_domains = self.store._extract_reality_sni_domains(outbound)
        related_inbounds = self._related_source_inbounds(candidate, tag)
        self._assert_managed_route_is_acyclic(
            session,
            source,
            candidate,
            related_inbounds,
            target_server,
            target.inbound_tag,
        )
        candidate = self._apply_managed_sniffing(
            candidate,
            tag,
            reality_domains,
            related_inbounds,
        )
        target_candidate = candidate if same_server else client_candidate
        if existing_outbound is not None and old_client is not None:
            action = "update"
        elif existing_outbound is not None or old_client is not None or existing_rule is not None:
            action = "repair"
        else:
            action = "create"
        revision_payload = {
            "source_snapshot": source_snapshot.config_hash,
            "target_snapshot": target_snapshot.config_hash,
            "target_node_id": target.id,
            "target_node_updated_at": target.updated_at.isoformat(),
            "target_server_context": {
                "name": target_server.name,
                "subscription_host": self.store._server_subscription_host(target_server),
                "runtime_server_key_sha256": (
                    sha256(
                        str(
                            self.store._runtime_shadowsocks_server_key(
                                session.get(AgentScanResultModel, target_server.id), target
                            )
                            or ""
                        ).encode()
                    ).hexdigest()
                    if (target.config or {}).get("server-key-source") == "runtime"
                    else None
                ),
            },
            "promote_to_default": payload.promote_to_default,
            "routing_action": (
                "keep"
                if "routing" not in payload.model_fields_set
                else "remove"
                if payload.routing is None
                else "set"
            ),
            "routing": payload.routing.model_dump(mode="json") if payload.routing else None,
            "observatory": (
                payload.observatory if "observatory" in payload.model_fields_set else "__keep__"
            ),
            "burstObservatory": (
                payload.burst_observatory
                if "burst_observatory" in payload.model_fields_set
                else "__keep__"
            ),
            "pinned_peer_cert_sha256": pinned_peer_cert_sha256,
        }
        revision = sha256(
            json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        preview = ServerEgressPreviewRead(
            source_server_id=UUID(source.id),
            source_server_name=source.name,
            target_node_id=UUID(target.id),
            target_node_name=target.name,
            target_server_id=UUID(target_server.id),
            target_server_name=target_server.name,
            protocol=target.protocol,
            action=action,
            outbound_tag=tag,
            routing_marktag=marktag,
            promote_to_default=payload.promote_to_default,
            will_be_default=will_be_default,
            routing=payload.routing,
            routing_action=(
                "keep"
                if "routing" not in payload.model_fields_set
                else "remove"
                if payload.routing is None
                else "set"
            ),
            observatory_action=(
                "keep"
                if "observatory" not in payload.model_fields_set
                else "remove"
                if payload.observatory is None
                else "set"
            ),
            burst_observatory_action=(
                "keep"
                if "burst_observatory" not in payload.model_fields_set
                else "remove"
                if payload.burst_observatory is None
                else "set"
            ),
            source_snapshot_id=UUID(source_snapshot.id),
            target_snapshot_id=UUID(target_snapshot.id),
            preview_revision=revision,
            tls_probe=tls_probe,
            pinned_peer_cert_sha256=pinned_peer_cert_sha256,
        )
        return {
            "preview": preview,
            "source": source,
            "target": target,
            "target_server": target_server,
            "source_config": source_config,
            "candidate": candidate,
            "target_config": target_config,
            "target_candidate": target_candidate,
            "same_server": same_server,
            "client": client,
            "old_client": old_client,
            "existing_outbound": existing_outbound,
            "existing_rule": existing_rule,
            "email": email,
        }

    def preview(self, server_id: UUID, payload: ServerEgressPreviewRequest):
        with self.store._session() as session:
            return self._prepare(session, server_id, payload)["preview"]

    def _prepare_remove(self, session, server_id, payload):
        source = self._server(session, server_id)
        target = session.get(ManagedNodeModel, str(payload.target_node_id))
        if target is None:
            raise ServerEgressNotFoundError(f"managed node not found: {payload.target_node_id}")
        target_server = session.get(ServerModel, target.server_id)
        protocol = self._protocol(target.protocol)
        if (
            target_server is None
            or not target.inbound_tag
            or protocol not in self.SUPPORTED_PROTOCOLS
        ):
            raise ServerEgressConflict("The target runtime identity is incomplete")
        shared_reason = self._shared_server_reason(session, target_server.id)
        if shared_reason:
            raise ServerEgressConflict(shared_reason)
        source_snapshot = self._snapshot(session, source)
        target_snapshot = self._snapshot(session, target_server)
        source_config = self._config(source_snapshot)
        target_config = self._config(target_snapshot)
        same_server = source.id == target_server.id
        tag, marktag, email = self._identity(source.id, target.id)
        old_client = self._existing_client(target_config, target, protocol, email)

        candidate = deepcopy(source_config)
        outbounds = candidate.get("outbounds", [])
        outbound_index, existing_outbound = self._tagged(outbounds, tag, "outbounds")
        if outbound_index is not None:
            outbounds.pop(outbound_index)
        routing = candidate.get("routing") or {}
        if not isinstance(routing, dict):
            raise ServerEgressConflict("Xray routing must be an object")
        rules = routing.get("rules", [])
        rule_index, existing_rule = self._rule_index(rules, marktag)
        if rule_index is not None:
            rules.pop(rule_index)
        if existing_outbound is None and existing_rule is None and old_client is None:
            raise ServerEgressConflict("The selected managed egress is not configured")
        if existing_outbound is not None and not outbounds:
            raise ServerEgressConflict(
                "Disconnecting this egress would leave Xray without an outbound"
            )
        candidate = self._apply_managed_sniffing(candidate, tag, [], [])
        target_candidate = self._mutated_target_client(
            candidate if same_server else target_config,
            target,
            protocol,
            email,
            None,
        )
        if same_server:
            candidate = target_candidate

        revision_payload = {
            "operation": "remove",
            "source_snapshot": source_snapshot.config_hash,
            "target_snapshot": target_snapshot.config_hash,
            "target_node_id": target.id,
            "target_node_updated_at": target.updated_at.isoformat(),
        }
        revision = sha256(
            json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        preview = ServerEgressPreviewRead(
            source_server_id=UUID(source.id),
            source_server_name=source.name,
            target_node_id=UUID(target.id),
            target_node_name=target.name,
            target_server_id=UUID(target_server.id),
            target_server_name=target_server.name,
            protocol=target.protocol,
            action="remove",
            outbound_tag=tag,
            routing_marktag=marktag,
            promote_to_default=False,
            will_be_default=False,
            routing=None,
            observatory_action="keep",
            burst_observatory_action="keep",
            source_snapshot_id=UUID(source_snapshot.id),
            target_snapshot_id=UUID(target_snapshot.id),
            preview_revision=revision,
        )
        return {
            "preview": preview,
            "target": target,
            "source_config": source_config,
            "candidate": candidate,
            "target_config": target_config,
            "target_candidate": target_candidate,
            "same_server": same_server,
            "old_client": old_client,
            "existing_outbound": existing_outbound,
            "existing_rule": existing_rule,
            "email": email,
        }

    def preview_remove(self, server_id: UUID, payload: ServerEgressRemovePreviewRequest):
        with self.store._session() as session:
            return self._prepare_remove(session, server_id, payload)["preview"]

    def apply(self, server_id: UUID, payload: ServerEgressApplyRequest):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            prepared = self._prepare(session, server_id, payload)
            preview = prepared["preview"]
            if preview.preview_revision != payload.expected_preview_revision:
                raise ServerEgressConflict(
                    "The egress preview is stale; preview the current server state again"
                )
            target = prepared["target"]
            timeout = payload.command_timeout_ms
            if prepared["same_server"]:
                rollback_selector = {
                    "outbound_tag": preview.outbound_tag,
                    "routing_marktag": preview.routing_marktag,
                    "inbound_tag": target.inbound_tag,
                    "client_email": prepared["email"],
                }
                steps = [
                    AgentChangeSetStepCreate(
                        server_id=preview.source_server_id,
                        label=f"Apply same-server managed egress {preview.target_node_name}",
                        forward=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["source_config"],
                                "config": prepared["candidate"],
                            },
                            timeout_ms=timeout,
                        ),
                        rollback=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["candidate"],
                                "config": prepared["source_config"],
                                "allow_diverged_managed_state": rollback_selector,
                            },
                            timeout_ms=timeout,
                        ),
                    )
                ]
            else:
                target_rollback = AgentCommandCreate(
                    method="POST",
                    path="/api/child/egress/apply",
                    body={
                        "expected_config": prepared["target_candidate"],
                        "config": prepared["target_config"],
                        "allow_diverged_managed_state": {
                            "inbound_tag": target.inbound_tag,
                            "client_email": prepared["email"],
                        },
                    },
                    timeout_ms=timeout,
                )
                steps = [
                    AgentChangeSetStepCreate(
                        server_id=preview.target_server_id,
                        label=f"Provision egress client on {preview.target_node_name}",
                        forward=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["target_config"],
                                "config": prepared["target_candidate"],
                            },
                            timeout_ms=timeout,
                        ),
                        rollback=target_rollback,
                    ),
                    AgentChangeSetStepCreate(
                        server_id=preview.source_server_id,
                        label=f"Apply managed egress {preview.target_node_name}",
                        forward=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["source_config"],
                                "config": prepared["candidate"],
                            },
                            timeout_ms=timeout,
                        ),
                        rollback=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["candidate"],
                                "config": prepared["source_config"],
                                "allow_diverged_managed_state": {
                                    "outbound_tag": preview.outbound_tag,
                                    "routing_marktag": preview.routing_marktag,
                                },
                            },
                            timeout_ms=timeout,
                        ),
                    ),
                ]
            change = self.store._create_change_set_model(
                session,
                AgentChangeSetCreate(
                    name=f"{preview.action.title()} managed egress {preview.target_node_name}",
                    description=(
                        f"Provision an authenticated client on {preview.target_server_name} and "
                        f"atomically apply {preview.outbound_tag} on {preview.source_server_name}."
                    ),
                    rollback_on_failure=True,
                    dispatch=payload.dispatch,
                    steps=steps,
                ),
                now,
            )
            commands = (
                self.store._change_sets().dispatch_model(session, change)
                if payload.dispatch
                else []
            )
            result = ServerEgressApplyResponse(
                preview=preview,
                change_set_id=UUID(change.id),
                change_set_status=change.status,
                command_ids=[UUID(command.id) for command in commands],
            )
            session.commit()
            return result, [self.store._command_read(command) for command in commands]

    def remove(self, server_id: UUID, payload: ServerEgressRemoveRequest):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            prepared = self._prepare_remove(session, server_id, payload)
            preview = prepared["preview"]
            if preview.preview_revision != payload.expected_preview_revision:
                raise ServerEgressConflict("The egress removal preview is stale; preview it again")
            timeout = payload.command_timeout_ms
            rollback_selector = {
                "outbound_tag": preview.outbound_tag,
                "routing_marktag": preview.routing_marktag,
            }
            if prepared["same_server"]:
                rollback_selector.update(
                    inbound_tag=prepared["target"].inbound_tag,
                    client_email=prepared["email"],
                )
            steps = [
                AgentChangeSetStepCreate(
                    server_id=preview.source_server_id,
                    label=f"Disconnect managed egress {preview.target_node_name}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/egress/apply",
                        body={
                            "expected_config": prepared["source_config"],
                            "config": prepared["candidate"],
                        },
                        timeout_ms=timeout,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/egress/apply",
                        body={
                            "expected_config": prepared["candidate"],
                            "config": prepared["source_config"],
                            "allow_diverged_managed_state": rollback_selector,
                        },
                        timeout_ms=timeout,
                    ),
                )
            ]
            if prepared["old_client"] is not None and not prepared["same_server"]:
                steps.append(
                    AgentChangeSetStepCreate(
                        server_id=preview.target_server_id,
                        label=f"Revoke egress client on {preview.target_node_name}",
                        forward=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["target_config"],
                                "config": prepared["target_candidate"],
                            },
                            timeout_ms=timeout,
                        ),
                        rollback=AgentCommandCreate(
                            method="POST",
                            path="/api/child/egress/apply",
                            body={
                                "expected_config": prepared["target_candidate"],
                                "config": prepared["target_config"],
                                "allow_diverged_managed_state": {
                                    "inbound_tag": prepared["target"].inbound_tag,
                                    "client_email": prepared["email"],
                                },
                            },
                            timeout_ms=timeout,
                        ),
                    )
                )
            change = self.store._create_change_set_model(
                session,
                AgentChangeSetCreate(
                    name=f"Remove managed egress {preview.target_node_name}",
                    description=(
                        f"Atomically remove {preview.outbound_tag} from "
                        f"{preview.source_server_name}, then revoke its target credential."
                    ),
                    rollback_on_failure=True,
                    dispatch=payload.dispatch,
                    steps=steps,
                ),
                now,
            )
            commands = (
                self.store._change_sets().dispatch_model(session, change)
                if payload.dispatch
                else []
            )
            result = ServerEgressApplyResponse(
                preview=preview,
                change_set_id=UUID(change.id),
                change_set_status=change.status,
                command_ids=[UUID(command.id) for command in commands],
            )
            session.commit()
            return result, [self.store._command_read(command) for command in commands]
