import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.exc import IntegrityError

from open_node.domain.server_management import (
    ServerRemovalPreview,
    ServerRemovalResponse,
    ServerSettingsResponse,
)
from open_node.services.inventory import (
    AgentChangeSetModel,
    AgentChangeSetStepModel,
    Base,
    ChangeSetServerLockModel,
    CommandModel,
    DuplicateServerNameError,
    ManagedNodeModel,
    ProductUserModel,
    ProductUserRemovalModel,
    ServerModel,
    ServerNotFoundError,
    SubscriptionArchivedTrafficModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
    SubscriptionTrafficLedgerModel,
    TelemetrySnapshotModel,
)
from open_node.services.user_limits import prune_node_overrides

PROFILE_FIELDS = ("name", "ip_address", "ip_address_v6", "domain", "domain_v6", "ipv6_enabled")
SETTLED_CHANGES = {"succeeded", "rolled_back", "cancelled", "accepted"}
FINISHED_COMMANDS = {"succeeded", "failed", "skipped"}


class ServerManagementConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class ServerManagement:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _server(session, identifier):
        server = session.get(ServerModel, str(identifier))
        if server is None:
            raise ServerNotFoundError(f"server not found: {identifier}")
        return server

    @staticmethod
    def _require_local(session, server):
        from open_node.services.server_sharing import FederatedServerModel

        if session.get(FederatedServerModel, server.id) is not None:
            raise ServerManagementConflict(
                "Shared servers must be changed or removed from server sharing"
            )

    def _settings(self, server, updated=()):
        return ServerSettingsResponse(
            server=self.store._public_server(server),
            revision=digest({key: getattr(server, key) for key in PROFILE_FIELDS}),
            updated_node_ids=list(updated),
        )

    def settings(self, identifier):
        with self.store._session() as session:
            return self._settings(self._server(session, identifier))

    def _managed_egress_references(self, session, server) -> list[str]:
        """Find managed egress identities owned by or targeting ``server``.

        A source server owns all identities carrying its stable UUID fragment.
        A target server owns identities carrying one of its managed-node UUID
        fragments, and any dedicated managed client installed in its runtime.
        Search every runtime's current snapshot because a partially applied or
        manually edited egress may leave only the remote half behind.
        """

        servers = list(session.scalars(select(ServerModel).order_by(ServerModel.id)))
        target_fragments = {
            node.id.replace("-", "")[:12]
            for node in session.scalars(
                select(ManagedNodeModel).where(ManagedNodeModel.server_id == server.id)
            )
        }
        source_fragment = server.id.replace("-", "")[:12]
        source_outbound_prefix = f"managed-egress:{source_fragment}:"
        source_rule_prefix = f"managed-egress-rule:{source_fragment}:"
        source_client_prefix = f"open_node_egress__{source_fragment}__"

        def target_identity(value, prefix, separator):
            return isinstance(value, str) and value.startswith(prefix) and any(
                value.endswith(f"{separator}{fragment}") for fragment in target_fragments
            )

        references = []
        for runtime_server in servers:
            snapshot = self.store._current_xray_config_snapshot(session, runtime_server.id)
            if snapshot is None:
                continue
            try:
                config = json.loads(snapshot.config)
            except (TypeError, ValueError):
                continue
            if not isinstance(config, dict):
                continue

            outbounds = config.get("outbounds", [])
            if not isinstance(outbounds, list):
                outbounds = []
            for outbound in outbounds:
                tag = outbound.get("tag") if isinstance(outbound, dict) else None
                if (
                    isinstance(tag, str)
                    and tag.startswith(source_outbound_prefix)
                    or target_identity(tag, "managed-egress:", ":")
                ):
                    references.append(f"{runtime_server.name}: {tag}")

            routing = config.get("routing") or {}
            rules = routing.get("rules", []) if isinstance(routing, dict) else []
            if not isinstance(rules, list):
                rules = []
            for rule in rules:
                marktag = rule.get("marktag") if isinstance(rule, dict) else None
                if (
                    isinstance(marktag, str)
                    and marktag.startswith(source_rule_prefix)
                    or target_identity(marktag, "managed-egress-rule:", ":")
                ):
                    references.append(f"{runtime_server.name}: {marktag}")

            inbounds = config.get("inbounds", [])
            if not isinstance(inbounds, list):
                inbounds = []
            for inbound in inbounds:
                if not isinstance(inbound, dict):
                    continue
                settings = inbound.get("settings") or {}
                if not isinstance(settings, dict):
                    continue
                for container in ("clients", "users", "accounts"):
                    clients = settings.get(container, [])
                    if not isinstance(clients, list):
                        continue
                    for client in clients:
                        email = client.get("email") if isinstance(client, dict) else None
                        if not isinstance(email, str):
                            continue
                        if (
                            email.startswith(source_client_prefix)
                            or target_identity(email, "open_node_egress__", "__")
                            or (
                                runtime_server.id == server.id
                                and email.startswith("open_node_egress__")
                            )
                        ):
                            inbound_tag = inbound.get("tag") or "unnamed inbound"
                            references.append(
                                f"{runtime_server.name}: {inbound_tag} / {email}"
                            )
        return list(dict.fromkeys(references))

    def update(self, identifier, payload):
        with self.store._coordinated_session() as session:
            server = self._server(session, identifier)
            self._require_local(session, server)
            if self._settings(server).revision != payload.expected_revision:
                raise ServerManagementConflict("Server settings changed; refresh before saving")
            if session.get(ChangeSetServerLockModel, server.id):
                raise ServerManagementConflict("A coordinated server change is in progress")
            if session.scalar(
                select(ServerModel.id).where(
                    ServerModel.name == payload.name,
                    ServerModel.id != server.id,
                )
            ):
                raise DuplicateServerNameError(f"server name already exists: {payload.name}")
            profile_changed = any(
                getattr(server, key) != getattr(payload, key) for key in PROFILE_FIELDS
            )
            if profile_changed and self._managed_egress_references(session, server):
                raise ServerManagementConflict(
                    "Disconnect managed server egress before changing server settings"
                )
            old_host = self.store._server_subscription_host(server)
            for key in PROFILE_FIELDS:
                setattr(server, key, getattr(payload, key))
            server.updated_at = now = datetime.now(UTC)
            new_host = self.store._server_subscription_host(server)
            updated = []
            if payload.sync_node_hosts and old_host != new_host:
                for node in session.scalars(
                    select(ManagedNodeModel).where(
                        ManagedNodeModel.server_id == server.id,
                    )
                ).all():
                    changed = False
                    for field in ("config", "client_template"):
                        content = getattr(node, field) or {}
                        if content.get("server") == old_host:
                            setattr(node, field, {**content, "server": new_host})
                            changed = True
                    if changed:
                        node.updated_at = now
                        updated.append(UUID(node.id))
            try:
                session.commit()
            except IntegrityError as exc:
                raise DuplicateServerNameError(
                    f"server name already exists: {payload.name}"
                ) from exc
            return self._settings(server, updated)

    @staticmethod
    def _certificates(session, server_id):
        from open_node.services.certificates import (
            CertificateHTTPLease,
            CertificateTarget,
            ManagedCertificate,
        )

        if not inspect(session.connection()).has_table("managed_certificates"):
            return [], [], []
        validations = session.scalars(
            select(ManagedCertificate).where(
                ManagedCertificate.validation_server_id == server_id,
            )
        ).all()
        targets = session.scalars(
            select(CertificateTarget).where(
                CertificateTarget.server_id == server_id,
            )
        ).all()
        leases = session.scalars(
            select(CertificateHTTPLease).where(
                CertificateHTTPLease.server_id == server_id,
                CertificateHTTPLease.released_at.is_(None),
            )
        ).all()
        return validations, targets, leases

    def _impact(self, session, server):
        nodes = session.scalars(
            select(ManagedNodeModel)
            .where(
                ManagedNodeModel.server_id == server.id,
            )
            .order_by(ManagedNodeModel.id)
        ).all()
        node_ids = {node.id for node in nodes}
        plans = [
            plan
            for plan in session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.id)
            ).all()
            if node_ids.intersection(plan.node_ids or [])
        ]
        changes = session.scalars(
            select(AgentChangeSetModel)
            .where(
                AgentChangeSetModel.id.in_(
                    select(AgentChangeSetStepModel.change_set_id).where(
                        AgentChangeSetStepModel.server_id == server.id,
                    )
                )
            )
            .order_by(AgentChangeSetModel.id)
        ).all()
        commands = session.scalars(
            select(CommandModel)
            .where(
                CommandModel.server_id == server.id,
            )
            .order_by(CommandModel.id)
        ).all()
        users = set(
            session.scalars(
                select(SubscriptionCredentialModel.username).where(
                    SubscriptionCredentialModel.server_id == server.id,
                )
            )
        ) | set(
            session.scalars(
                select(SubscriptionTrafficLedgerModel.username).where(
                    SubscriptionTrafficLedgerModel.server_id == server.id,
                )
            )
        )
        validations, targets, leases = self._certificates(session, server.id)
        blockers = [
            f"Resolve or cancel change set: {change.name}"
            for change in changes
            if change.status not in SETTLED_CHANGES
        ]
        pending_user_removals = [
            job.id
            for job in session.scalars(
                select(ProductUserRemovalModel).where(
                    ProductUserRemovalModel.completed_at.is_(None)
                )
            )
            if any(item["server_id"] == server.id for item in job.fingerprints)
        ]
        if pending_user_removals:
            blockers.append("Complete pending user removals before removing this server")
        if self.store._node_management().pending_for_server(session, server.id):
            blockers.append("Complete pending node removals before removing this server")
        if session.get(ChangeSetServerLockModel, server.id):
            blockers.append("A change set still holds this server")
        if self._managed_egress_references(session, server):
            blockers.append("Disconnect managed server egress before removing this server")
        blockers += [
            f"Certificate validation is active: {row.name}"
            for row in validations
            if row.active_job_id
        ]
        if leases:
            blockers.append("Remote certificate challenges still need cleanup")
        # A dependent on another server must never become runnable by losing its parent.
        seen = {command.id for command in commands}
        frontier = seen.copy()
        dependents = []
        while frontier:
            rows = session.scalars(
                select(CommandModel).where(
                    CommandModel.depends_on_command_id.in_(frontier),
                    CommandModel.id.not_in(seen),
                )
            ).all()
            frontier = {row.id for row in rows}
            seen.update(frontier)
            dependents.extend(rows)
        if any(row.status not in FINISHED_COMMANDS for row in dependents):
            blockers.append("Unfinished commands on another server depend on this server")
        from open_node.services.certificates import ManagedCertificate

        certificate_ids = {row.id for row in validations} | {row.certificate_id for row in targets}
        certificates = [
            session.get(ManagedCertificate, identifier) for identifier in sorted(certificate_ids)
        ]
        preview = ServerRemovalPreview(
            server_id=server.id,
            server_name=server.name,
            revision="",
            nodes=[{"id": node.id, "name": node.name} for node in nodes],
            plans=[{"id": plan.id, "name": plan.name} for plan in plans],
            change_sets=[{"id": change.id, "name": change.name} for change in changes],
            certificates=[{"id": row.id, "name": row.name} for row in certificates if row],
            command_count=len(commands),
            unfinished_command_count=sum(row.status not in FINISHED_COMMANDS for row in commands),
            telemetry_count=session.scalar(
                select(func.count())
                .select_from(TelemetrySnapshotModel)
                .where(TelemetrySnapshotModel.server_id == server.id)
            ),
            user_count=len(users),
            blockers=blockers,
        )
        preview.revision = digest(
            {
                "server": self._settings(server).revision,
                "user_removals": pending_user_removals,
                "nodes": [(row.id, row.updated_at) for row in nodes],
                "plans": [(row.id, row.node_ids, row.updated_at) for row in plans],
                "changes": [(row.id, row.status, row.updated_at) for row in changes],
                "commands": [(row.id, row.status, row.attempts) for row in commands],
                "dependents": sorted((row.id, row.status) for row in dependents),
                "users": sorted(users),
                "validations": sorted(
                    (row.id, row.active_job_id, row.auto_renew) for row in validations
                ),
                "targets": sorted((row.id, row.certificate_id) for row in targets),
                "leases": sorted(row.id for row in leases),
                "blockers": blockers,
            }
        )
        return preview, nodes, plans, changes, commands, users, validations, targets

    def preview(self, identifier):
        with self.store._session() as session:
            server = self._server(session, identifier)
            self._require_local(session, server)
            return self._impact(session, server)[0]

    def remove(self, identifier, payload):
        with self.store._coordinated_session() as session:
            server = self._server(session, identifier)
            self._require_local(session, server)
            preview, nodes, plans, changes, commands, users, validations, targets = self._impact(
                session, server
            )
            if preview.revision != payload.expected_revision or server.name != payload.confirm_name:
                raise ServerManagementConflict("Removal details changed; review a fresh preview")
            if preview.blockers:
                raise ServerManagementConflict("; ".join(preview.blockers))
            now = datetime.now(UTC)
            before = {
                name: (
                    self.store._subscription_user_traffic(session, name),
                    self.store._subscription_user_weighted_traffic(session, name),
                )
                for name in users
            }
            node_ids = {node.id for node in nodes}
            for plan in plans:
                plan.node_ids = [
                    identifier for identifier in plan.node_ids if identifier not in node_ids
                ]
                for field in (
                    "node_multipliers",
                    "node_speed_limits",
                    "node_device_limits",
                    "node_name_overrides",
                ):
                    setattr(
                        plan,
                        field,
                        {
                            key: value
                            for key, value in (getattr(plan, field) or {}).items()
                            if key not in node_ids
                        },
                    )
                plan.updated_at = now
            for user in session.scalars(select(ProductUserModel)):
                if prune_node_overrides(user, node_ids):
                    user.updated_at = now
            for change in changes:
                archived = self.store._change_set_read(session, change).steps
                for step in archived:
                    step.archived = True
                    target = session.get(ServerModel, str(step.server_id))
                    step.server_name = target.name if target else step.server_name
                change.archived_steps = [step.model_dump(mode="json") for step in archived]
                session.execute(
                    delete(AgentChangeSetStepModel).where(
                        AgentChangeSetStepModel.change_set_id == change.id,
                    )
                )
                change.updated_at = now
            for certificate in validations:
                certificate.auto_renew = False
                certificate.last_error = "Validation server removed; choose a new validation server"
            for target in targets:
                session.delete(target)
            session.execute(
                update(CommandModel)
                .where(
                    CommandModel.server_id != server.id,
                    CommandModel.depends_on_command_id.in_([row.id for row in commands]),
                )
                .values(depends_on_command_id=None)
            )
            server_id, server_name = server.id, server.name
            # Apply declared cascades even on SQLite connections without FK enforcement.
            for table in reversed(Base.metadata.sorted_tables):
                for reference in table.foreign_keys:
                    if reference.column.table.name == "servers" and reference.ondelete == "CASCADE":
                        session.execute(
                            delete(table).where(table.c[reference.parent.name] == server_id)
                        )
            session.delete(server)
            session.flush()
            # Keep already charged usage while the server-bound ledgers are removed.
            for name, ((up, down), (weighted_up, weighted_down)) in before.items():
                after_up, after_down = self.store._subscription_user_traffic(session, name)
                after_weighted_up, after_weighted_down = (
                    self.store._subscription_user_weighted_traffic(session, name)
                )
                session.add(
                    SubscriptionArchivedTrafficModel(
                        username=name,
                        server_id=server_id,
                        server_name=server_name,
                        upload=max(0, up - after_up),
                        download=max(0, down - after_down),
                        weighted_upload=max(0, weighted_up - after_weighted_up),
                        weighted_download=max(0, weighted_down - after_weighted_down),
                        updated_at=now,
                    )
                )
            session.commit()
            return ServerRemovalResponse(
                server_id=server_id, removed_node_count=len(nodes), updated_plan_count=len(plans)
            )
