"""Change plan membership without losing runtime revocation intent."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from open_node.domain.plan_management import PlanManagementRead, PlanManagementResult
from open_node.domain.subscriptions import SubscriptionPlanCreate
from open_node.services.inventory import (
    DuplicateSubscriptionPlanNameError,
    ManagedNodeModel,
    ProductUserModel,
    ProductUserNotFoundError,
    ServerModel,
    SubscriptionAccessModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
    SubscriptionPlanNotFoundError,
)
from open_node.services.subscription_access import SubscriptionAccessConflict, protocol, revision


class PlanManagementConflict(ValueError):
    pass


class PlanManagement:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _plan(session, identifier):
        plan = session.get(SubscriptionPlanModel, str(identifier))
        if plan is None:
            raise SubscriptionPlanNotFoundError(f"plan not found: {identifier}")
        return plan

    @staticmethod
    def _user(session, username):
        user = session.get(ProductUserModel, username)
        if user is None:
            raise ProductUserNotFoundError(f"user not found: {username}")
        if not user.current_plan_id:
            raise PlanManagementConflict("User no longer has an assigned plan")
        return user

    @staticmethod
    def _members(session, plan):
        return list(
            session.scalars(
                select(ProductUserModel)
                .where(
                    ProductUserModel.current_plan_id == plan.id,
                )
                .order_by(ProductUserModel.username)
            )
        )

    def _revision_record(self, model):
        values = model.model_dump(mode="json")
        for key, value in model:
            if isinstance(value, datetime):
                values[key] = self.store._aware_datetime(value).isoformat()
        return values

    def _view(self, session, plan, users):
        names = [user.username for user in users]
        rows = session.scalars(
            select(SubscriptionAccessModel)
            .where(
                SubscriptionAccessModel.username.in_(names),
            )
            .order_by(SubscriptionAccessModel.id)
        ).all()
        credentials = session.scalars(
            select(SubscriptionCredentialModel)
            .where(
                SubscriptionCredentialModel.username.in_(names),
            )
            .order_by(SubscriptionCredentialModel.id)
        ).all()
        managed = {row.username for row in rows}
        public = self.store._subscription_plan_read(plan)
        fingerprint = revision(
            {
                "plan": self._revision_record(public),
                "users": [
                    self._revision_record(self.store._product_user_read(user)) for user in users
                ],
                "bindings": [(row.id, row.bindings) for row in rows],
                "credentials": [
                    (row.id, self.store._aware_datetime(row.updated_at).isoformat())
                    for row in credentials
                ],
            }
        )
        manual = [name for name in names if name not in managed]
        return PlanManagementRead(
            plan=public,
            revision=fingerprint,
            users=[
                {
                    "username": user.username,
                    "display_name": user.display_name,
                    "is_active": user.is_active,
                    "managed": user.username in managed,
                }
                for user in users
            ],
            warnings=["Plan edits do not enroll preview-only subscribers: " + ", ".join(manual)]
            if manual
            else [],
        )

    def read(self, identifier):
        with self.store._session() as session:
            plan = self._plan(session, identifier)
            return self._view(session, plan, self._members(session, plan))

    def assignment(self, username):
        with self.store._session() as session:
            user = self._user(session, username)
            return self._view(session, self._plan(session, user.current_plan_id), [user])

    @staticmethod
    def _check(view, payload, name=None):
        if view.revision != payload.expected_revision or (
            name is not None and payload.confirm_name != name
        ):
            raise PlanManagementConflict("Plan or subscribers changed; reload before continuing")

    def update(self, identifier, payload):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            plan = self._plan(session, identifier)
            users = self._members(session, plan)
            before = self._view(session, plan, users)
            self._check(before, payload)
            if session.scalar(
                select(SubscriptionPlanModel.id).where(
                    SubscriptionPlanModel.name == payload.name,
                    SubscriptionPlanModel.id != plan.id,
                )
            ):
                raise DuplicateSubscriptionPlanNameError("A plan with this name already exists")
            self.store._ensure_managed_nodes_exist(session, payload.node_ids)
            aliases = (
                {str(key): value for key, value in payload.node_name_overrides.items()}
                if "node_name_overrides" in payload.model_fields_set
                else {
                    key: value
                    for key, value in (plan.node_name_overrides or {}).items()
                    if key in {str(identifier) for identifier in payload.node_ids}
                }
            )
            aliases_enabled = (
                payload.node_name_override_enabled
                if "node_name_override_enabled" in payload.model_fields_set
                else plan.node_name_override_enabled
            )
            templates = self.store.subscription_templates().validate_selection(
                session, payload, plan
            )
            subscription_only = (
                aliases != plan.node_name_overrides
                or aliases_enabled != plan.node_name_override_enabled
                or any(value != getattr(plan, key) for key, value in templates.items())
            ) and all(
                getattr(payload, key) == getattr(before.plan, key)
                for key in SubscriptionPlanCreate.model_fields
                if key not in {"node_name_overrides", "node_name_override_enabled", *templates}
                and (key != "auto_speed_rules" or key in payload.model_fields_set)
            )
            plan.node_name_overrides, plan.node_name_override_enabled = aliases, aliases_enabled
            for key, value in templates.items():
                setattr(plan, key, value)
            if "auto_speed_rules" in payload.model_fields_set:
                plan.auto_speed_rules = [rule.model_dump() for rule in payload.auto_speed_rules]
            for key in (
                "name",
                "description",
                "cycle_days",
                "is_reset",
                "reset_day",
                "speed_limit_mbps",
                "device_limit",
                "traffic_mode",
            ):
                setattr(plan, key, getattr(payload, key))
            plan.traffic_limit_bytes = int(payload.traffic_limit_gb * 1024**3)
            plan.node_ids = [str(identifier) for identifier in payload.node_ids]
            for key in ("node_multipliers", "node_speed_limits", "node_device_limits"):
                setattr(
                    plan,
                    key,
                    {str(identifier): value for identifier, value in getattr(payload, key).items()},
                )
            plan.updated_at = now
            managed = {user.username for user in before.users if user.managed}
            warnings, commands = list(before.warnings), []
            access = self.store._subscription_access()
            for user in users:
                if subscription_only:
                    continue
                batches, notices = self.store._subscription_provision_batches(
                    session, user, plan, no_restart=False
                )
                warnings.extend(notices)
                if user.username not in managed:
                    continue
                access.authorize(session, user, plan, batches, now)
                commands.extend(access.reconcile(session, now, username=user.username))
            session.flush()
            view = self._view(session, plan, users)
            result = PlanManagementResult(
                plan=view.plan,
                revision=view.revision,
                affected_users=[user.username for user in users],
                commands=[self.store._command_read(command) for command in commands],
                warnings=list(dict.fromkeys(warnings)),
            )
            session.commit()
            return result

    def _track_revocations(self, session, user, plan, now, node_ids=None):
        access = self.store._subscription_access()
        rows = {row.server_id: row for row in access.rows(session, user.username)}
        warnings = []
        for credential in session.scalars(
            select(SubscriptionCredentialModel)
            .where(
                SubscriptionCredentialModel.username == user.username,
            )
            .order_by(SubscriptionCredentialModel.id)
        ).all():
            if node_ids is not None and credential.node_id not in node_ids:
                continue
            if not credential.inbound_tag:
                warnings.append(
                    f"{user.username}: credential {credential.id} has no inbound; "
                    "remote cleanup needs review"
                )
                continue
            server = session.get(ServerModel, credential.server_id)
            if server is None:
                raise PlanManagementConflict(
                    "A credential refers to a missing server; repair its inventory first"
                )
            node = session.get(ManagedNodeModel, credential.node_id)
            client = (
                self.store._provisioning_client_from_credential(
                    user, plan, node, server, credential
                )
                if node
                else {
                    **credential.credential,
                    "email": credential.email,
                }
            )
            row = rows.get(server.id)
            if row is None:
                row = SubscriptionAccessModel(
                    id=str(uuid4()),
                    username=user.username,
                    server_id=server.id,
                    bindings=[],
                    updated_at=now,
                )
                session.add(row)
                rows[server.id] = row
            bindings = deepcopy(row.bindings)
            previous = next(
                (
                    item
                    for item in bindings
                    if item["tag"] == credential.inbound_tag
                    and item["client"]["email"] == credential.email
                ),
                None,
            )
            if previous:
                if any(
                    previous["client"].get(key) != value
                    for key, value in credential.credential.items()
                ) or previous["protocol"] != protocol(credential.protocol):
                    raise SubscriptionAccessConflict(
                        "A tracked credential changed; review its runtime identity first"
                    )
                if credential.node_id not in previous["node_ids"]:
                    previous["node_ids"].append(credential.node_id)
            else:
                bindings.append(
                    {
                        "tag": credential.inbound_tag,
                        "protocol": protocol(credential.protocol),
                        "client": deepcopy(client),
                        "node_ids": [credential.node_id],
                    }
                )
            row.bindings = sorted(bindings, key=lambda item: (item["tag"], item["client"]["email"]))
            row.updated_at, row.retry_at = now, None
        session.flush()
        return warnings

    def _unbind(self, session, plan, users, now):
        commands, warnings = [], []
        for user in users:
            warnings.extend(self._track_revocations(session, user, plan, now))
            user.current_plan_id = None
            user.plan_started_at = user.plan_expires_at = None
            user.is_reset, user.reset_day, user.updated_at = False, 0, now
            commands.extend(
                self.store._subscription_access().reconcile(session, now, username=user.username)
            )
        return PlanManagementResult(
            affected_users=[user.username for user in users],
            commands=[self.store._command_read(command) for command in commands],
            warnings=warnings,
        )

    def remove(self, identifier, payload):
        with self.store._coordinated_session() as session:
            plan = self._plan(session, identifier)
            users = self._members(session, plan)
            self._check(self._view(session, plan, users), payload, plan.name)
            result = self._unbind(session, plan, users, datetime.now(UTC))
            session.delete(plan)
            session.commit()
            return result

    def unassign(self, username, payload):
        with self.store._coordinated_session() as session:
            user = self._user(session, username)
            plan = self._plan(session, user.current_plan_id)
            self._check(self._view(session, plan, [user]), payload, username)
            result = self._unbind(session, plan, [user], datetime.now(UTC))
            session.commit()
            return result
