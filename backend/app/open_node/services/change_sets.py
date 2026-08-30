"""Persisted, ordered changes with exclusive node reservations and explicit compensation."""

from datetime import UTC, datetime

from sqlalchemy import delete, or_, select

from open_node.domain.changes import AgentChangeSetStatus as State
from open_node.services.inventory import (
    AgentCapabilityUnavailableError,
    AgentChangeSetModel,
    AgentChangeSetStepModel,
    ChangeSetServerLockModel,
    CommandModel,
    CommandNotReadyError,
    ServerModel,
    ServerNotFoundError,
)

TERMINAL = {"succeeded", "failed", "skipped"}
REVIEW_STATES = {State.FAILED, State.ROLLBACK_FAILED, State.ROLLBACK_INCOMPLETE, State.NEEDS_REVIEW}
READ_ONLY = {
    "/api/child/xray/config",
    "/api/child/nginx/config",
    "/api/child/system/info",
    "/api/child/traffic",
    "/api/child/speed",
}


class ChangeSetConflict(ValueError):
    pass


def in_flight(command):
    return command is not None and command.status not in TERMINAL and command.attempts > 0


def attempted(command):
    return command is not None and (
        command.attempts > 0 or command.status in {"succeeded", "failed"}
    )


class ChangeSetCoordinator:
    def __init__(self, store):
        self.store = store

    def steps(self, session, change):
        return self.store._change_set_steps(session, change.id)

    def capability_error(self, session, commands):
        for server_id, payload in commands:
            server = session.get(ServerModel, server_id)
            if server is None:
                raise ServerNotFoundError(f"server not found: {server_id}")
            try:
                self.store._validate_command_capabilities(session, server, payload)
            except AgentCapabilityUnavailableError as exc:
                return str(exc)
        return None

    def preflight_capabilities(self, session, commands):
        if error := self.capability_error(session, commands):
            raise ChangeSetConflict(error)

    def rollback_creations(self, session, steps, *, retry_failed=False):
        commands = []
        for step in reversed(steps):
            forward = (
                session.get(CommandModel, step.forward_command_id)
                if step.forward_command_id
                else None
            )
            if not attempted(forward):
                continue
            payload = self.store._step_rollback_command(step)
            if payload is None:
                continue
            rollback = (
                session.get(CommandModel, step.rollback_command_id)
                if step.rollback_command_id
                else None
            )
            if rollback is None or (
                retry_failed and rollback.status in {"failed", "skipped"}
            ):
                commands.append((step.server_id, payload))
        return commands

    @staticmethod
    def owner(session, command):
        step = session.scalar(
            select(AgentChangeSetStepModel).where(
                or_(
                    AgentChangeSetStepModel.forward_command_id == command.id,
                    AgentChangeSetStepModel.rollback_command_id == command.id,
                )
            )
        )
        return session.get(AgentChangeSetModel, step.change_set_id) if step else None

    @staticmethod
    def command_ids(steps):
        return {
            identifier
            for step in steps
            for identifier in [
                step.forward_command_id,
                step.rollback_command_id,
                *(step.rollback_history_ids or []),
            ]
            if identifier
        }

    @staticmethod
    def external_in_flight(session, steps):
        identifiers = ChangeSetCoordinator.command_ids(steps)
        candidates = list(
            session.scalars(
                select(CommandModel).where(
                    CommandModel.server_id.in_({step.server_id for step in steps}),
                    CommandModel.id.not_in(identifiers),
                    CommandModel.status.in_(["waiting", "pending", "leased"]),
                )
            )
        )
        return [
            command
            for command in candidates
            if ChangeSetCoordinator.draining(session, command, identifiers)
        ]

    @staticmethod
    def draining(session, command, owned):
        if in_flight(command):
            return True
        parent_id = command.depends_on_command_id
        seen = set(owned)
        # Finish an earlier recovery sequence before a new change can take over its nodes.
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = session.get(CommandModel, parent_id)
            if parent is None:
                break
            if attempted(parent):
                return True
            parent_id = parent.depends_on_command_id
        return False

    def reserve(self, session, change, steps):
        servers = {step.server_id for step in steps}
        legacy = session.scalar(
            select(AgentChangeSetStepModel.id)
            .join(
                AgentChangeSetModel,
                AgentChangeSetStepModel.change_set_id == AgentChangeSetModel.id,
            )
            .where(
                AgentChangeSetStepModel.server_id.in_(servers),
                AgentChangeSetModel.id != change.id,
                AgentChangeSetModel.status == State.NEEDS_REVIEW,
            )
            .limit(1)
        )
        if legacy:
            raise ChangeSetConflict("A target has a legacy change set awaiting operator review")
        for server_id in sorted(servers):
            if self.store._node_management().pending_for_server(session, server_id):
                raise ChangeSetConflict("A target server has a pending node removal")
            if session.get(ServerModel, server_id) is None:
                raise ServerNotFoundError(f"server not found: {server_id}")
            lock = session.get(ChangeSetServerLockModel, server_id)
            if lock and lock.change_set_id != change.id:
                raise ChangeSetConflict(
                    f"Server {server_id} is reserved by change set {lock.change_set_id}"
                )
            if not lock:
                session.add(ChangeSetServerLockModel(server_id=server_id, change_set_id=change.id))
        session.flush()

    @staticmethod
    def release(session, change):
        session.execute(
            delete(ChangeSetServerLockModel).where(
                ChangeSetServerLockModel.change_set_id == change.id,
            )
        )

    def can_lease(self, session, command):
        change = self.owner(session, command)
        if change and change.coordination_version == 0:
            return in_flight(command)
        legacy = list(
            session.scalars(
                select(AgentChangeSetStepModel)
                .join(
                    AgentChangeSetModel,
                    AgentChangeSetStepModel.change_set_id == AgentChangeSetModel.id,
                )
                .where(
                    AgentChangeSetStepModel.server_id == command.server_id,
                    AgentChangeSetModel.status == State.NEEDS_REVIEW,
                )
            )
        )
        if legacy:
            return self.draining(session, command, self.command_ids(legacy))
        lock = session.get(ChangeSetServerLockModel, command.server_id)
        if not lock:
            return change is None
        if not change or change.id != lock.change_set_id:
            owner = session.get(AgentChangeSetModel, lock.change_set_id)
            return self.draining(session, command, self.command_ids(self.steps(session, owner)))
        if change.status not in {State.DISPATCHED, State.ROLLBACK_QUEUED}:
            return False
        return in_flight(command) or not self.external_in_flight(
            session, self.steps(session, change)
        )

    def validate_result(self, session, command):
        if command.attempts == 0 and (
            self.owner(session, command)
            or session.get(ChangeSetServerLockModel, command.server_id)
            or not self.can_lease(session, command)
        ):
            raise CommandNotReadyError("Reserved commands require a lease before result submission")

    def read_state(self, session, change, steps):
        warnings = []
        if change.archived_steps:
            warnings.append("Server removed; this change set is archived and cannot be replayed")
        if change.coordination_version == 0:
            warnings.append("Legacy execution needs operator review; unsent commands were stopped")
        for step in steps:
            forward = (
                session.get(CommandModel, step.forward_command_id)
                if step.forward_command_id
                else None
            )
            if attempted(forward) and not step.rollback_path:
                warnings.append(f"step {step.sequence} has no rollback command")
        held = list(
            session.scalars(
                select(ChangeSetServerLockModel.server_id).where(
                    ChangeSetServerLockModel.change_set_id == change.id,
                )
            )
        )
        if change.status == State.NEEDS_REVIEW:
            held = sorted({step.server_id for step in steps})
        blocking = self.external_in_flight(session, steps) if held else []
        if change.status in {State.ROLLBACK_QUEUED, State.NEEDS_REVIEW}:
            blocking += [
                command
                for identifier in self.command_ids(steps)
                if in_flight(command := session.get(CommandModel, identifier))
            ]
        return {
            "held_server_ids": sorted(held),
            "blocking_command_ids": sorted({command.id for command in blocking}),
            "warnings": warnings,
        }

    def dispatch(self, identifier):
        with self.store._coordinated_session() as session:
            change = self.store._change_set_model(session, identifier)
            commands = self.dispatch_model(session, change)
            session.commit()
            return self.store._change_set_read(session, change), [
                self.store._command_read(c) for c in commands
            ]

    def dispatch_model(self, session, change):
        if change.archived_steps:
            raise ChangeSetConflict("An archived change set cannot be dispatched")
        commands = []
        if change.status == State.PLANNED:
            steps = self.steps(session, change)
            self.preflight_capabilities(
                session,
                [
                    (step.server_id, self.store._step_forward_command(step))
                    for step in steps
                ],
            )
            self.reserve(session, change, steps)
            previous = None
            for step in steps:
                previous = self.store._create_command_model(
                    session,
                    session.get(ServerModel, step.server_id),
                    self.store._step_forward_command(step),
                    depends_on=previous,
                )
                # Persist the referenced command before exposing its identifier through
                # the step FK.  The models intentionally do not have an ORM relationship,
                # so SQLAlchemy cannot otherwise infer this INSERT-before-UPDATE ordering.
                session.flush()
                step.forward_command_id = previous.id
                commands.append(previous)
            change.status = State.DISPATCHED
            change.updated_at = datetime.now(UTC)
        elif change.status != State.DISPATCHED:
            raise ChangeSetConflict("Only a planned change set can be dispatched")
        session.flush()
        return commands

    def stop_unsent(self, session, steps, now):
        for step in steps:
            command = (
                session.get(CommandModel, step.forward_command_id)
                if step.forward_command_id
                else None
            )
            if command and command.status in {"waiting", "pending"} and command.attempts == 0:
                command.status = "skipped"
                command.result_error = "Stopped before execution by change-set rollback or failure"
                command.completed_at = command.updated_at = now
                self.store._advance_command_dependents(session, command, now)
        session.flush()

    def guard_late_rollback(self, session, steps):
        forwards = [session.get(CommandModel, step.forward_command_id) for step in steps]
        ended = max(command.completed_at for command in forwards)
        later = list(
            session.scalars(
                select(CommandModel).where(
                    CommandModel.server_id.in_({step.server_id for step in steps}),
                    CommandModel.id.not_in(self.command_ids(steps)),
                    CommandModel.attempts > 0,
                    CommandModel.leased_at >= ended,
                )
            )
        )
        later.extend(self.external_in_flight(session, steps))
        if any(command.method != "GET" or command.path not in READ_ONLY for command in later):
            raise ChangeSetConflict(
                "A target received later work; create a new recovery plan from its current state"
            )

    def rollback(self, identifier, payload):
        with self.store._coordinated_session() as session:
            change = self.store._change_set_model(session, identifier)
            if change.archived_steps:
                raise ChangeSetConflict("A target server was removed; create a new recovery plan")
            steps = self.steps(session, change)
            before = self.command_ids(steps)
            now = datetime.now(UTC)
            if change.coordination_version == 0:
                raise ChangeSetConflict(
                    "Review legacy execution, accept its current state, then create a recovery plan"
                )
            if change.status == State.PLANNED:
                change.status = State.CANCELLED
            elif change.status in {State.CANCELLED, State.ROLLED_BACK}:
                pass
            elif change.status == State.ACCEPTED:
                raise ChangeSetConflict(
                    "An accepted change set cannot be rolled back; create a new plan"
                )
            else:
                self.preflight_capabilities(
                    session,
                    self.rollback_creations(
                        session,
                        steps,
                        retry_failed=change.status == State.ROLLBACK_FAILED,
                    ),
                )
                self.reserve(session, change, steps)
                if change.status == State.SUCCEEDED:
                    self.guard_late_rollback(session, steps)
                if change.status == State.ROLLBACK_FAILED:
                    for step in steps:
                        command = (
                            session.get(CommandModel, step.rollback_command_id)
                            if step.rollback_command_id
                            else None
                        )
                        if command and command.status in {"failed", "skipped"}:
                            step.rollback_history_ids = [
                                *(step.rollback_history_ids or []),
                                command.id,
                            ]
                            step.rollback_command_id = None
                change.status = State.ROLLBACK_QUEUED
                if not change.rollback_reason:
                    change.rollback_reason = payload.reason
                self.advance(session, change, now)
            change.updated_at = now
            session.commit()
            result = self.store._change_set_read(session, change)
            commands = [
                step.rollback_command
                for step in reversed(result.steps)
                if step.rollback_command and str(step.rollback_command.id) not in before
            ]
            return result, commands, result.warnings

    def accept(self, identifier, payload):
        with self.store._coordinated_session() as session:
            change = self.store._change_set_model(session, identifier)
            if change.status not in REVIEW_STATES:
                raise ChangeSetConflict("Only a stopped or incomplete change set can be accepted")
            steps = self.steps(session, change)
            if any(in_flight(session.get(CommandModel, cid)) for cid in self.command_ids(steps)):
                raise ChangeSetConflict(
                    "Wait for every attempted command to return before accepting state"
                )
            change.status = State.ACCEPTED
            change.resolution_reason = payload.reason
            change.updated_at = datetime.now(UTC)
            self.release(session, change)
            session.commit()
            return self.store._change_set_read(session, change)

    def advance_after_result(self, session, command, now):
        change = self.owner(session, command)
        if change and change.coordination_version:
            self.advance(session, change, now)

    def advance(self, session, change, now):
        steps = self.steps(session, change)
        forwards = [session.get(CommandModel, step.forward_command_id) for step in steps]
        if change.status == State.DISPATCHED:
            failed = next(
                (command for command in forwards if command.status in {"failed", "skipped"}), None
            )
            if failed:
                self.stop_unsent(session, steps, now)
                change.status = (
                    State.ROLLBACK_QUEUED if change.rollback_on_failure else State.FAILED
                )
                change.rollback_reason = f"Forward command {failed.id} failed"
            elif all(command.status == "succeeded" for command in forwards):
                change.status = State.SUCCEEDED
                self.release(session, change)
        if change.status == State.ROLLBACK_QUEUED:
            self.stop_unsent(session, steps, now)
            if any(in_flight(command) for command in forwards):
                change.updated_at = now
                return
            capability_error = self.capability_error(
                session,
                self.rollback_creations(session, steps),
            )
            automatic_skip_error = (
                f"Automatic rollback not queued: {capability_error}"
                if capability_error
                else None
            )
            previous = None
            rollbacks = []
            missing = False
            for step, forward in reversed(list(zip(steps, forwards, strict=True))):
                if not attempted(forward):
                    continue
                payload = self.store._step_rollback_command(step)
                if payload is None:
                    missing = True
                    continue
                command = (
                    session.get(CommandModel, step.rollback_command_id)
                    if step.rollback_command_id
                    else None
                )
                if command is None:
                    server = session.get(ServerModel, step.server_id)
                    if automatic_skip_error:
                        command = self.store._create_skipped_command_model(
                            session,
                            server,
                            payload,
                            now,
                            automatic_skip_error,
                            depends_on=previous,
                        )
                    else:
                        command = self.store._create_command_model(
                            session,
                            server,
                            payload,
                            now,
                            depends_on=previous,
                        )
                    # As with forward dispatch, make the command row durable inside the
                    # transaction before the step starts referencing it.
                    session.flush()
                    step.rollback_command_id = command.id
                elif (
                    automatic_skip_error
                    and command.status in {"waiting", "pending"}
                    and command.attempts == 0
                ):
                    command.status = "skipped"
                    command.result_status = 501
                    command.result_error = automatic_skip_error
                    command.completed_at = command.updated_at = now
                rollbacks.append(command)
                previous = command
            if any(command.status in {"failed", "skipped"} for command in rollbacks):
                change.status = State.ROLLBACK_FAILED
            elif all(command.status == "succeeded" for command in rollbacks):
                change.status = State.ROLLBACK_INCOMPLETE if missing else State.ROLLED_BACK
                if not missing:
                    self.release(session, change)
        change.updated_at = now
        session.flush()
        self.store._private_routed_nodes().after_change_set(session, change, now)

    def migrate_legacy(self):
        with self.store._coordinated_session() as session:
            changes = list(
                session.scalars(
                    select(AgentChangeSetModel).where(
                        AgentChangeSetModel.coordination_version == 0,
                    )
                )
            )
            now = datetime.now(UTC)
            for change in changes:
                if change.status == State.PLANNED:
                    change.coordination_version = 1
                    continue
                if change.status not in {State.DISPATCHED, State.ROLLBACK_QUEUED}:
                    continue
                steps = self.steps(session, change)
                for identifier in self.command_ids(steps):
                    command = session.get(CommandModel, identifier)
                    if (
                        command
                        and command.status in {"waiting", "pending"}
                        and command.attempts == 0
                    ):
                        command.status = "skipped"
                        command.result_error = (
                            "Paused legacy change set during coordination upgrade"
                        )
                        command.completed_at = command.updated_at = now
                change.status = State.NEEDS_REVIEW
                change.updated_at = now
            session.commit()
