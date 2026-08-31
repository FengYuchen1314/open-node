"""Offline v1 restore into a new private directory; never opens a live Store."""

import ctypes
import hashlib
import os
import secrets
import shutil
import sqlite3
import time
import zipfile
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from open_node.domain.restore import RestoreRecord
from open_node.services.backup_dependencies import check_backup_dependencies
from open_node.services.backup_sqlite import _directory
from open_node.services.backup_validation import validate_backup_archive
from open_node.services.restore_state import RESTORE_MARKER

RESTORE_ERROR = (
    "恢复未能完成：请检查备份、所需密钥、版本兼容性、空间和新目录权限。"
    "未覆盖原实例；若新目录已出现，请检查其中的恢复记录，不要重复导入。"
)
REASON = "备份恢复后暂停，需管理员核对远端状态。"


class BackupRestoreError(ValueError):
    def __init__(self):
        super().__init__(RESTORE_ERROR)


def _publish(parent: int, temporary: str, target: str) -> None:
    # Linux atomic directory publication. rename() alone could replace a racing
    # empty destination. Unsupported filesystems/platforms fail without fallback.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(temporary), parent, os.fsencode(target), 1) != 0:
        raise BackupRestoreError()


def _destination(logical: str) -> tuple[str, bool]:
    if logical == "secrets/agent-identity.seed":
        return "agent-identity.seed", False
    if logical.startswith(("data/certificates/jobs/", "data/certificates/http01-webroots/")):
        return ".restore-quarantine/" + logical.removeprefix("data/"), True
    if not logical.startswith("data/"):
        raise BackupRestoreError()
    return logical.removeprefix("data/"), False


def _write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _database(connection: sqlite3.Connection) -> None:
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_schema LIMIT 4097"
        ).fetchall()
        if len(schema) > 4096 or sum(len(row[2] or "") for row in schema) > 1_048_576:
            raise BackupRestoreError()
        for kind, name, sql in schema:
            if kind not in {"table", "index"} or (sql and "VIRTUAL TABLE" in sql.upper()):
                raise BackupRestoreError()
            if kind == "table":
                # Bound ordinary columns; reject generated/hidden columns before
                # any application write. Parameters, not archive-derived SQL.
                columns = connection.execute("SELECT hidden FROM pragma_table_xinfo(?)", (name,))
                rows = columns.fetchmany(1025)
                if len(rows) > 1024 or any(row[0] for row in rows):
                    raise BackupRestoreError()
        if connection.execute("PRAGMA integrity_check(1)").fetchmany(2) != [("ok",)]:
            raise BackupRestoreError()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupRestoreError()
        if connection.execute("SELECT count(*) FROM administrator").fetchone() != (1,):
            raise BackupRestoreError()
    finally:
        connection.set_progress_handler(None, 0)


def _quiesce(connection: sqlite3.Connection) -> dict[str, int]:
    """Preserve history, but invalidate authority and do not replay old work."""
    now = datetime.now(UTC)
    stamp = now.replace(tzinfo=None).isoformat(" ")
    connection.execute("PRAGMA query_only=OFF")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        with connection:
            sessions = 0
            for table in ("operator_sessions", "subscriber_sessions"):
                sessions += connection.execute(f"DELETE FROM {table}").rowcount
            for table in ("operator_challenges", "subscriber_challenges"):
                connection.execute(f"DELETE FROM {table}")
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='initial_setup_tickets'"
            ).fetchone():
                connection.execute(
                    "UPDATE initial_setup_tickets SET token_hash=NULL, expires_at=0, "
                    "completed_at=?",
                    (now.timestamp(),),
                )
            connection.execute(
                "UPDATE agent_bootstrap_tickets SET revoked_at=? WHERE revoked_at IS NULL",
                (now.timestamp(),),
            )
            connection.execute(
                "UPDATE administrator_factors SET pending_secret=NULL, pending_expires_at=0, "
                "pending_session_hash=NULL"
            )
            connection.execute(
                "UPDATE subscriber_accounts SET pending_secret=NULL, pending_expires_at=0, "
                "pending_session_id=NULL"
            )
            connection.execute(
                "UPDATE administrator_backup_epoch SET value=?", (secrets.token_hex(32),),
            )
            commands = connection.execute(
                "UPDATE agent_commands SET status='failed', result_error=?, leased_at=NULL, "
                "completed_at=?, updated_at=? WHERE status NOT IN ('succeeded','failed','skipped')",
                (REASON, stamp, stamp),
            ).rowcount
            connection.execute(
                "UPDATE agent_change_sets SET status='needs_review', resolution_reason=?, "
                "updated_at=? WHERE status IN ('dispatched','rollback_queued')", (REASON, stamp),
            )
            certificates = connection.execute(
                "UPDATE certificate_jobs SET status='failed', message=?, finished_at=? "
                "WHERE status IN ('queued','running')", (REASON, now.timestamp()),
            ).rowcount
            connection.execute(
                "UPDATE managed_certificates SET auto_renew=0, active_job_id=NULL, "
                "status=CASE WHEN status IN ('queued','running') THEN 'failed' ELSE status END, "
                "last_error=?", (REASON,),
            )
            connection.execute("UPDATE certificate_targets SET auto_deploy=0")
            # Lease history remains available; old host webroots are never cleaned
            # or re-presented by the new instance.
            connection.execute(
                "UPDATE certificate_http_leases SET released_at=? WHERE released_at IS NULL",
                (now.timestamp(),),
            )
            connection.execute("UPDATE notification_settings SET enabled=0")
            # Older v1 archives have no refresh table. New restores must not
            # start fetching provider URLs merely because first-boot review ends.
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='external_subscription_refresh'"
            ).fetchone():
                connection.execute(
                    "UPDATE external_subscription_refresh SET enabled=0, next_run_at=NULL, "
                    "lease_id=NULL, lease_until=NULL, code='restore_paused', "
                    "consecutive_failures=0"
                )
            connection.execute(
                "UPDATE notification_deliveries SET state=CASE WHEN state='sending' "
                "THEN 'unknown' ELSE 'cancelled' END, next_attempt_at=NULL, "
                "code='restore_paused', updated_at=? WHERE state IN ('queued','sending')", (stamp,),
            )
            connection.execute(
                "UPDATE notification_attempts SET state='unknown', recovered_at=?, "
                "code='restore_paused', retryable=0 WHERE state='sending'", (stamp,),
            )
            connection.execute(
                "UPDATE notification_chat_throttles SET in_flight_attempt_id=NULL, deadline_at=NULL"
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupRestoreError()
    finally:
        connection.set_progress_handler(None, 0)
    return dict(invalidated_sessions=sessions, cancelled_agent_commands=commands,
                cancelled_certificate_jobs=certificates)


def restore_backup_archive(source, output: str, *, totp_key: bytes | None = None) -> RestoreRecord:
    """Caller owns an immutable, validated/decrypted private copy, never a live file.

    Output parent must already exist, owned by the caller with mode 0700.
    Only the random staging directory created here is cleaned up on failure.
    """
    parent = None
    temporary = None
    try:
        target = Path(output)
        if not output or output.endswith("/") or target.name in {"", ".", ".."}:
            raise BackupRestoreError()
        parent = _directory(target.parent, private=True)
        try:
            os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BackupRestoreError()
        if totp_key is not None:
            if len(totp_key) != 44:
                raise BackupRestoreError()
            Fernet(totp_key)
        report = validate_backup_archive(source)
        if "unknown" in asdict(report.manifest.coverage).values():
            raise BackupRestoreError()
        if "subscriber_totp_key" in report.manifest.required_configuration and totp_key is None:
            raise BackupRestoreError()
        temporary = ".open-node-restore-" + secrets.token_hex(16)
        os.mkdir(temporary, 0o700, dir_fd=parent)
        root = Path(f"/proc/self/fd/{parent}") / temporary
        quarantined = 0
        deadline = time.monotonic() + 120
        with zipfile.ZipFile(source) as archive, ExitStack() as stack:
            states = {}
            for entry in report.manifest.files:
                relative, quarantine = _destination(entry.path)
                quarantined += int(quarantine)
                destination = root / relative
                current = root
                for part in Path(relative).parts[:-1]:
                    current /= part
                    current.mkdir(mode=0o700, exist_ok=True)
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as file, archive.open(entry.path) as member:
                    os.fchmod(file.fileno(), 0o600)
                    digest, total = hashlib.sha256(), 0
                    while block := member.read(65536):
                        total += len(block)
                        if total > entry.size or time.monotonic() > deadline:
                            raise BackupRestoreError()
                        file.write(block)
                        digest.update(block)
                    if total != entry.size or digest.hexdigest() != entry.sha256:
                        raise BackupRestoreError()
                    file.flush()
                    os.fsync(file.fileno())
                if entry.role != "database":
                    states[entry.path] = stack.enter_context(archive.open(entry.path))
            connection = sqlite3.connect(root / "open-node.db")
            try:
                _database(connection)
                dependencies = check_backup_dependencies(connection, states, totp_key=totp_key)
                if dependencies.totp_status == "not_checked":
                    raise BackupRestoreError()
                counts = _quiesce(connection)
            finally:
                connection.close()
        record = RestoreRecord(
            id=uuid4(), created_at=datetime.now(UTC),
            archive_sha256=report.checked_archive_sha256, quarantined_files=quarantined, **counts,
        )
        settings = [
            "OPEN_NODE_DATABASE_URL=sqlite:////var/lib/open-node/open-node.db",
            "OPEN_NODE_CERTIFICATE_STATE_DIR=/var/lib/open-node/certificates",
            "OPEN_NODE_EXTERNAL_SUBSCRIPTIONS_STATE_DIR=/var/lib/open-node/external-subscriptions",
            "OPEN_NODE_NOTIFICATIONS_STATE_DIR=/var/lib/open-node/notifications",
        ]
        if (root / "agent-identity.seed").exists():
            settings.append("OPEN_NODE_AGENT_IDENTITY_FILE=/var/lib/open-node/agent-identity.seed")
        if totp_key:
            settings.append("OPEN_NODE_SUBSCRIBER_TOTP_KEY=" + totp_key.decode("ascii"))
        _write(root / "restore.env", ("\n".join(settings) + "\n").encode())
        _write(root / RESTORE_MARKER, record.model_dump_json().encode())
        # SQLite writes happened after extraction; sync the final database too.
        with (root / "open-node.db").open("rb") as database:
            os.fsync(database.fileno())
        for directory, _, _ in os.walk(root, topdown=False):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _publish(parent, temporary, target.name)
        temporary = None  # Never remove a published destination, even if fsync fails.
        os.fsync(parent)
        return record
    except Exception:
        raise BackupRestoreError() from None
    finally:
        if parent is not None:
            try:
                if temporary is not None:
                    shutil.rmtree(temporary, dir_fd=parent)
            finally:
                os.close(parent)
