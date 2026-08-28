"""Keep user removal pending until its runtime credentials are withdrawn."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select

from open_node.domain.subscriptions import SubscriptionAccessResponse
from open_node.domain.user_management import (
    UserManagementRead,
    UserManagementResult,
    UserRemovalRead,
)
from open_node.services.inventory import (
    CommandModel,
    ProductUserConflict,
    ProductUserModel,
    ProductUserNotFoundError,
    ProductUserRemovalModel,
    ProductUserSubscriptionTokenModel,
    SubscriptionAccessModel,
    SubscriptionArchivedTrafficModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
    SubscriptionTrafficLedgerModel,
)
from open_node.services.subscription_access import ENDPOINT, TERMINAL, revision

AUTH_FIELDS = {"id", "password", "psk", "auth", "user", "pass", "username"}


class UserRemovalNotFoundError(ValueError):
    pass


def fingerprint(server_id, tag, client):
    fields = sorted(AUTH_FIELDS.intersection(client)) or ["email"]
    return {
        "server_id": server_id,
        "tag": tag,
        "email": client.get("email", ""),
        "fields": fields,
        "digest": revision({field: client.get(field) for field in fields}),
    }


def matches(identity, client):
    return all(field in client for field in identity["fields"]) and identity["digest"] == revision(
        {field: client[field] for field in identity["fields"]}
    )


class UserManagement:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def user(session, username):
        user = session.get(ProductUserModel, username)
        if user is None:
            raise ProductUserNotFoundError(f"user not found: {username}")
        return user

    @staticmethod
    def require_editable(user):
        if user.removal_id:
            raise ProductUserConflict("User removal is in progress")

    @staticmethod
    def check_active(user, active):
        if user.role == "admin" and not active:
            raise ProductUserConflict("Administrator product users cannot be disabled")

    @staticmethod
    def credentials(session, username):
        return session.scalars(
            select(SubscriptionCredentialModel)
            .where(SubscriptionCredentialModel.username == username)
            .order_by(SubscriptionCredentialModel.id)
        ).all()

    def _view(self, session, user):
        credentials = self.credentials(session, user.username)
        rows = self.store._subscription_access().rows(session, user.username)
        public = self.store._product_user_read(user)
        values = public.model_dump(mode="json")
        for key, value in public:
            if isinstance(value, datetime):
                values[key] = self.store._aware_datetime(value).isoformat()
        blockers = []
        if user.role == "admin":
            blockers.append("Administrator product users cannot be removed")
        if user.removal_id:
            blockers.append("User removal is already in progress")
        warnings = []
        for credential in credentials:
            if not credential.inbound_tag:
                warnings.append(
                    f"Credential {credential.email} has no managed inbound; "
                    "remote cleanup is manual"
                )
            if session.scalar(
                select(SubscriptionCredentialModel.id)
                .where(
                    SubscriptionCredentialModel.username != user.username,
                    SubscriptionCredentialModel.server_id == credential.server_id,
                    SubscriptionCredentialModel.inbound_tag == credential.inbound_tag,
                    SubscriptionCredentialModel.email == credential.email,
                )
                .limit(1)
            ):
                blockers.append("A credential label is shared with another subscriber")
        return UserManagementRead(
            user=public,
            revision=revision(
                {
                    "user": values,
                    "credentials": [
                        (row.id, self.store._aware_datetime(row.updated_at).isoformat())
                        for row in credentials
                    ],
                    "bindings": [(row.id, row.bindings) for row in rows],
                    "blockers": blockers,
                }
            ),
            credential_count=len(credentials),
            blockers=list(dict.fromkeys(blockers)),
            warnings=warnings,
            access=self.store._subscription_access().read_in_session(session, user.username),
        )

    def read(self, username):
        with self.store._session() as session:
            return self._view(session, self.user(session, username))

    def update(self, username, payload):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user = self.user(session, username)
            self.require_editable(user)
            self.check_active(user, payload.is_active)
            before = self._view(session, user)
            if before.revision != payload.expected_revision:
                raise ProductUserConflict("User or credentials changed; reload before saving")
            if before.blockers and user.is_active != payload.is_active:
                raise ProductUserConflict("; ".join(before.blockers))
            if not payload.is_active:
                plan = (
                    session.get(SubscriptionPlanModel, user.current_plan_id)
                    if user.current_plan_id
                    else None
                )
                self.store._plan_management()._track_revocations(session, user, plan, now)
            for field in ("display_name", "email", "remark", "is_active"):
                setattr(user, field, getattr(payload, field))
            user.updated_at = now
            commands = self.store._subscription_access().reconcile(session, now, username=username)
            session.flush()
            result = UserManagementResult(
                **self._view(session, user).model_dump(),
                commands=[self.store._command_read(command) for command in commands],
            )
            session.commit()
            return result

    def _removal(self, session, identifier):
        job = session.get(ProductUserRemovalModel, str(identifier))
        if job is None:
            raise UserRemovalNotFoundError("User removal not found")
        return job

    def _removal_read(self, session, job, commands=()):
        if job.completed_at:
            servers = job.servers
        else:
            user = self.user(session, job.username)
            if user.removal_id != job.id:
                raise ProductUserConflict("User removal identity no longer matches")
            servers = self.store._subscription_access().read_in_session(session, job.username)[
                "servers"
            ]
        return UserRemovalRead(
            id=job.id,
            username=job.username,
            requested_at=job.requested_at,
            completed_at=job.completed_at,
            status="completed"
            if job.completed_at
            else "failed"
            if any(server["status"] == "failed" for server in servers)
            else "pending",
            servers=servers,
            warnings=job.warnings,
            commands=[self.store._command_read(command) for command in commands],
        )

    def read_removal(self, identifier):
        with self.store._session() as session:
            return self._removal_read(session, self._removal(session, identifier))

    def remove(self, username, payload):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user = self.user(session, username)
            if payload.confirm_name != username:
                raise ProductUserConflict("Confirm the exact username")
            if user.removal_id:
                return self._removal_read(session, self._removal(session, user.removal_id))
            view = self._view(session, user)
            if view.revision != payload.expected_revision:
                raise ProductUserConflict("User or credentials changed; reload before removing")
            if view.blockers:
                raise ProductUserConflict("; ".join(view.blockers))
            if view.warnings and not payload.acknowledge_unmanaged_credentials:
                raise ProductUserConflict("Confirm responsibility for unmanaged credential cleanup")
            plan = (
                session.get(SubscriptionPlanModel, user.current_plan_id)
                if user.current_plan_id
                else None
            )
            warnings = self.store._plan_management()._track_revocations(session, user, plan, now)
            identities = [
                fingerprint(row.server_id, binding["tag"], binding["client"])
                for row in self.store._subscription_access().rows(session, username)
                for binding in row.bindings
            ]
            identities.extend(
                fingerprint(row.server_id, row.inbound_tag, {**row.credential, "email": row.email})
                for row in self.credentials(session, username)
                if not row.inbound_tag
            )
            job = ProductUserRemovalModel(
                id=str(uuid4()),
                username=username,
                requested_at=now,
                fingerprints=identities,
                warnings=list(dict.fromkeys(view.warnings + warnings)),
                servers=[],
            )
            session.add(job)
            session.flush()
            user.removal_id, user.is_active, user.updated_at = job.id, False, now
            user.current_plan_id = user.plan_started_at = user.plan_expires_at = None
            user.is_reset, user.reset_day = False, 0
            session.execute(
                delete(ProductUserSubscriptionTokenModel).where(
                    ProductUserSubscriptionTokenModel.username == username
                )
            )
            commands = self.store._subscription_access().reconcile(session, now, username=username)
            self.finalize_ready(session, now, identifier=job.id)
            result = self._removal_read(session, job, commands)
            session.commit()
            return result

    def retry(self, identifier):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            job = self._removal(session, identifier)
            commands = []
            if not job.completed_at:
                commands = self.store._subscription_access().reconcile(
                    session, now, username=job.username, force=True
                )
                self.finalize_ready(session, now, identifier=job.id)
            result = self._removal_read(session, job, commands)
            session.commit()
            return result

    def finalize_ready(self, session, now, identifier=None):
        query = select(ProductUserRemovalModel).where(
            ProductUserRemovalModel.completed_at.is_(None)
        )
        if identifier:
            query = query.where(ProductUserRemovalModel.id == identifier)
        access = self.store._subscription_access()
        for job in session.scalars(query).all():
            user = session.get(ProductUserModel, job.username)
            if user is None or user.removal_id != job.id:
                continue
            rows = access.rows(session, job.username)
            in_flight = False
            for command in session.scalars(
                select(CommandModel).where(CommandModel.status.not_in(TERMINAL))
            ).all():
                if not self.restores(command, job.fingerprints):
                    if self.store._should_refresh_xray_snapshot_after(command.method, command.path):
                        in_flight |= any(
                            row.server_id == command.server_id
                            and access.affects_credentials(row, command)
                            for row in rows
                        )
                    continue
                if command.attempts:
                    in_flight = True
                else:
                    access.skip(
                        session, command, now, "Not sent: user removal retired these credentials"
                    )
            if in_flight:
                continue
            states = access.read_in_session(session, job.username)["servers"]
            if any(
                server["status"] != "applied"
                or any(entry["enabled"] for entry in server["entries"])
                for server in states
            ):
                continue
            job.servers = SubscriptionAccessResponse(
                username=job.username, managed=bool(states), servers=states
            ).model_dump(mode="json")["servers"]
            job.completed_at = now
            for model in (
                ProductUserSubscriptionTokenModel,
                SubscriptionCredentialModel,
                SubscriptionAccessModel,
                SubscriptionTrafficLedgerModel,
                SubscriptionArchivedTrafficModel,
            ):
                session.execute(delete(model).where(model.username == user.username))
            session.delete(user)
            session.flush()

    @staticmethod
    def restores(command, fingerprints):
        if command.method not in {"POST", "PUT", "PATCH"} or not isinstance(command.body, dict):
            return False
        identities = [item for item in fingerprints if item["server_id"] == command.server_id]
        if not identities:
            return False
        if command.path == ENDPOINT:
            pending = [
                entry.get("client")
                for entry in command.body.get("entries", [])
                if isinstance(entry, dict) and entry.get("enabled") is True
            ]
        elif str(command.body.get("action", "")).startswith(("remove", "delete")):
            return False
        else:
            pending = [command.body]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if any(matches(item, value) for item in identities):
                    return True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except (ValueError, RecursionError):
                    continue
                if isinstance(parsed, (dict, list)):
                    pending.append(parsed)
        return False

    def retired_restore(self, session, command):
        if command.method not in {"POST", "PUT", "PATCH"} or not isinstance(command.body, dict):
            return False
        return any(
            self.restores(command, job.fingerprints)
            for job in session.scalars(select(ProductUserRemovalModel))
        )

    @staticmethod
    def check_imported_credential(session, server_id, entry):
        for job in session.scalars(select(ProductUserRemovalModel)):
            if any(
                item["server_id"] == server_id
                and (item["email"] == entry.email or matches(item, entry.credential))
                for item in job.fingerprints
            ):
                raise ProductUserConflict(
                    "Removed-user credentials or traffic labels cannot be reimported"
                )
