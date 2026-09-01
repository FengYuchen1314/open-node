"""Restore an official custom dump into a new PostgreSQL staging database."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from open_node.services.backup_dependencies import (
    capture_postgres_dependency_snapshot,
    check_postgres_backup_dependencies,
)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
POSTGRES_LIST_SECONDS = 2 * 60
POSTGRES_RESTORE_SECONDS = 30 * 60
REASON = "备份恢复后暂停，需管理员核对远端状态。"


class PostgresRestoreError(RuntimeError):
    pass


def postgres_url(database_url: str, database: str | None = None):
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or not url.username
        or url.password is None
        or not url.host
        or not url.database
        or not _IDENTIFIER.fullmatch(url.database)
        or set(url.query) - {"sslmode"}
        or url.query.get("sslmode", "prefer") not in _SSL_MODES
    ):
        raise PostgresRestoreError()
    if database is not None and not _IDENTIFIER.fullmatch(database):
        raise PostgresRestoreError()
    return url.set(database=database or url.database)


def _connection_arguments(url) -> tuple[list[str], dict[str, str]]:
    environment = os.environ.copy()
    environment.update(
        {"PGPASSWORD": url.password, "PGSSLMODE": url.query.get("sslmode", "prefer")}
    )
    return [
        "--host", url.host,
        "--port", str(url.port or 5432),
        "--username", url.username,
        "--dbname", url.database,
    ], environment


def _url_text(url) -> str:
    # psycopg accepts the PostgreSQL URI scheme, not SQLAlchemy's dialect-qualified
    # ``postgresql+psycopg`` scheme used by the running application.
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _stage_name(current: str) -> str:
    suffix = "_restore_" + secrets.token_hex(8)
    return current[: 63 - len(suffix)] + suffix


def _database_command(database_url: str, statement: str, name: str) -> None:
    import psycopg
    from psycopg import sql

    admin = postgres_url(database_url, "postgres")
    with psycopg.connect(_url_text(admin), autocommit=True, connect_timeout=10) as connection:
        connection.execute(sql.SQL(statement).format(sql.Identifier(name)))


def drop_postgres_database(database_url: str, name: str) -> bool:
    try:
        require_drop_postgres_database(database_url, name)
        return True
    except Exception:
        return False


def require_drop_postgres_database(database_url: str, name: str) -> None:
    _database_command(database_url, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)


def _restore_dump(dump: Path, database_url: str) -> None:
    url = postgres_url(database_url)
    arguments, environment = _connection_arguments(url)
    descriptor = os.open(dump, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if info.st_nlink != 1 or not 5 <= info.st_size <= 1024 * 1024 * 1024:
            raise PostgresRestoreError()
        checked = subprocess.run(
            ["pg_restore", "--list", f"/proc/self/fd/{descriptor}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(descriptor,),
            timeout=POSTGRES_LIST_SECONDS,
            check=False,
        )
        if checked.returncode != 0:
            raise PostgresRestoreError()
        restored = subprocess.run(
            [
                "pg_restore", "--exit-on-error", "--single-transaction",
                "--no-owner", "--no-privileges", *arguments,
                f"/proc/self/fd/{descriptor}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            pass_fds=(descriptor,),
            timeout=POSTGRES_RESTORE_SECONDS,
            check=False,
        )
        if restored.returncode != 0:
            raise PostgresRestoreError()
    finally:
        os.close(descriptor)


def _quiesce(database_url: str) -> dict[str, int]:
    import psycopg

    now = datetime.now(UTC)
    stamp = now
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='10s'")
            cursor.execute("SET LOCAL statement_timeout='30s'")
            cursor.execute("SELECT count(*) FROM administrator")
            if cursor.fetchone() != (1,):
                raise PostgresRestoreError()
            sessions = 0
            for table in ("operator_sessions", "subscriber_sessions"):
                cursor.execute(f'DELETE FROM "{table}"')
                sessions += cursor.rowcount
            for table in ("operator_challenges", "subscriber_challenges"):
                cursor.execute(f'DELETE FROM "{table}"')
            cursor.execute(
                "UPDATE initial_setup_tickets SET token_hash=NULL, expires_at=0, "
                "completed_at=%s",
                (now.timestamp(),),
            )
            cursor.execute(
                "UPDATE agent_bootstrap_tickets SET revoked_at=%s WHERE revoked_at IS NULL",
                (now.timestamp(),),
            )
            cursor.execute(
                "UPDATE administrator_factors SET pending_secret=NULL, pending_expires_at=0, "
                "pending_session_hash=NULL"
            )
            cursor.execute(
                "UPDATE subscriber_accounts SET pending_secret=NULL, pending_expires_at=0, "
                "pending_session_id=NULL"
            )
            cursor.execute(
                "UPDATE administrator_backup_epoch SET value=%s", (secrets.token_hex(32),)
            )
            cursor.execute(
                "UPDATE agent_commands SET status='failed', result_error=%s, leased_at=NULL, "
                "completed_at=%s, updated_at=%s "
                "WHERE status NOT IN ('succeeded','failed','skipped')",
                (REASON, stamp, stamp),
            )
            commands = cursor.rowcount
            cursor.execute(
                "UPDATE agent_change_sets SET status='needs_review', resolution_reason=%s, "
                "updated_at=%s WHERE status IN ('dispatched','rollback_queued')",
                (REASON, stamp),
            )
            cursor.execute(
                "UPDATE certificate_jobs SET status='failed', message=%s, finished_at=%s "
                "WHERE status IN ('queued','running')",
                (REASON, now.timestamp()),
            )
            certificates = cursor.rowcount
            cursor.execute(
                "UPDATE managed_certificates SET auto_renew=false, active_job_id=NULL, "
                "status=CASE WHEN status IN ('queued','running') THEN 'failed' ELSE status END, "
                "last_error=%s",
                (REASON,),
            )
            cursor.execute("UPDATE certificate_targets SET auto_deploy=false")
            cursor.execute(
                "UPDATE certificate_http_leases SET released_at=%s WHERE released_at IS NULL",
                (now.timestamp(),),
            )
            cursor.execute("UPDATE notification_settings SET enabled=false")
            cursor.execute(
                "UPDATE external_subscription_refresh SET enabled=false, next_run_at=NULL, "
                "lease_id=NULL, lease_until=NULL, code='restore_paused', "
                "consecutive_failures=0"
            )
            cursor.execute(
                "UPDATE notification_deliveries SET state=CASE WHEN state='sending' "
                "THEN 'unknown' ELSE 'cancelled' END, next_attempt_at=NULL, "
                "code='restore_paused', updated_at=%s WHERE state IN ('queued','sending')",
                (stamp,),
            )
            cursor.execute(
                "UPDATE notification_attempts SET state='unknown', recovered_at=%s, "
                "code='restore_paused', retryable=false WHERE state='sending'",
                (stamp,),
            )
            cursor.execute(
                "UPDATE notification_chat_throttles "
                "SET in_flight_attempt_id=NULL, deadline_at=NULL"
            )
        connection.commit()
    return {
        "invalidated_sessions": sessions,
        "cancelled_agent_commands": commands,
        "cancelled_certificate_jobs": certificates,
    }


def restore_postgres_to_staging(
    dump: Path,
    database_url: str,
    states,
    *,
    totp_key: bytes | None,
    stage_journal: Callable[[str], None],
) -> tuple[str, dict[str, int]]:
    current = postgres_url(database_url)
    if current.database == "postgres":
        raise PostgresRestoreError()
    stage = _stage_name(current.database)
    stage_url = postgres_url(database_url, stage)
    stage_application_url = stage_url.render_as_string(hide_password=False)
    try:
        stage_journal(stage)
        _database_command(database_url, "CREATE DATABASE {} TEMPLATE template0", stage)
        _restore_dump(dump, stage_application_url)
        dependencies = check_postgres_backup_dependencies(
            capture_postgres_dependency_snapshot(stage_application_url),
            states,
            totp_key=totp_key,
        )
        if dependencies.totp_status == "not_checked":
            raise PostgresRestoreError()
        return stage, _quiesce(_url_text(stage_url))
    except Exception:
        drop_postgres_database(database_url, stage)
        raise PostgresRestoreError() from None
