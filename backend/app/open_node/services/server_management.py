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
    ProductUserRemovalModel,
    ServerModel,
    ServerNotFoundError,
    SubscriptionArchivedTrafficModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
    SubscriptionTrafficLedgerModel,
    TelemetrySnapshotModel,
)

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

    def _settings(self, server, updated=()):
        return ServerSettingsResponse(
            server=self.store._public_server(server),
            revision=digest({key: getattr(server, key) for key in PROFILE_FIELDS}),
            updated_node_ids=list(updated),
        )

    def settings(self, identifier):
        with self.store._session() as session:
            return self._settings(self._server(session, identifier))

    def update(self, identifier, payload):
        with self.store._coordinated_session() as session:
            server = self._server(session, identifier)
            if self._settings(server).revision != payload.expected_revision:
                raise ServerManagementConflict("Server settings changed; refresh before saving")
            if session.scalar(
                select(ServerModel.id).where(
                    ServerModel.name == payload.name,
                    ServerModel.id != server.id,
                )
            ):
                raise DuplicateServerNameError(f"server name already exists: {payload.name}")
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
        if session.get(ChangeSetServerLockModel, server.id):
            blockers.append("A change set still holds this server")
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
            return self._impact(session, self._server(session, identifier))[0]

    def remove(self, identifier, payload):
        with self.store._coordinated_session() as session:
            server = self._server(session, identifier)
            preview, nodes, plans, changes, commands, users, validations, targets = self._impact(
                session, server
            )
            if preview.revision != payload.expected_revision or server.name != payload.confirm_name:
                raise ServerManagementConflict("Removal details changed; review a fresh preview")
            if preview.blockers:
                raise ServerManagementConflict("; ".join(preview.blockers))
            now = datetime.now(UTC)
            before = {name: self.store._subscription_user_traffic(session, name) for name in users}
            node_ids = {node.id for node in nodes}
            for plan in plans:
                plan.node_ids = [
                    identifier for identifier in plan.node_ids if identifier not in node_ids
                ]
                for field in ("node_multipliers", "node_speed_limits", "node_device_limits"):
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
            for name, (up, down) in before.items():
                after_up, after_down = self.store._subscription_user_traffic(session, name)
                session.add(
                    SubscriptionArchivedTrafficModel(
                        username=name,
                        server_id=server_id,
                        server_name=server_name,
                        upload=max(0, up - after_up),
                        download=max(0, down - after_down),
                        updated_at=now,
                    )
                )
            session.commit()
            return ServerRemovalResponse(
                server_id=server_id, removed_node_count=len(nodes), updated_plan_count=len(plans)
            )
