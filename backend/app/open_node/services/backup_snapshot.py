"""Coordinate private database/state copies; no publication or restoration.

The short-lived exclusive permit covers both the SQLite online copy and all
configured state-file copies. Only completed private read-only resources survive
that scope. Validation, ZIP generation and encryption can subsequently run while
ordinary application operations have resumed. Call this synchronous context from
the actual owning worker thread, never through a work-lease thread wrapper.
"""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from open_node.core.config import Settings
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_postgres import PostgresBackupSnapshot, postgres_backup_snapshot
from open_node.services.backup_sqlite import SQLiteBackupSnapshot, sqlite_backup_snapshot
from open_node.services.backup_state import (
    BackupStateLayout,
    StagedBackupState,
    _layout,
    staged_backup_state,
)


class BackupSnapshotError(RuntimeError):
    code = "backup_snapshot_unavailable"

    def __init__(self) -> None:
        super().__init__("Control-plane backup snapshot is unavailable.")


@dataclass(frozen=True, slots=True)
class CapturedControlPlaneSnapshot:
    created_at: str
    database: SQLiteBackupSnapshot | PostgresBackupSnapshot = field(repr=False)
    state: StagedBackupState = field(repr=False)
    restoration_ready: bool = False


def configured_backup_layout(settings: Settings) -> BackupStateLayout:
    """Map configured host paths, never paths supplied by an archive or request.

    Defaults deliberately match InventoryStore and create_app without importing
    either of them, constructing a pool, or creating any directory/key/database.
    """
    try:
        if not isinstance(settings, Settings):
            raise BackupSnapshotError()
        url = make_url(settings.database_url)
        if url.drivername in {"sqlite", "sqlite+pysqlite"}:
            if (
                not url.database
                or url.database == ":memory:"
                or url.database.startswith("file:")
                or "uri" in url.query
                or any(
                    value is not None for value in (url.username, url.password, url.host, url.port)
                )
            ):
                raise BackupSnapshotError()
            database = Path(url.database).absolute()
            state_root = database.parent
            database_engine = "sqlite"
            database_url = None
        elif url.drivername == "postgresql+psycopg":
            if (
                settings.control_state_dir is None
                or not url.username
                or url.password is None
                or not url.host
                or not url.database
                or set(url.query) - {"sslmode"}
            ):
                raise BackupSnapshotError()
            state_root = settings.control_state_dir.absolute()
            database = state_root / ".postgresql-database"
            database_engine = "postgresql"
            database_url = settings.database_url
        else:
            raise BackupSnapshotError()
        return BackupStateLayout(
            database=database,
            certificates=settings.certificate_state_dir.absolute(),
            external_subscriptions=(
                settings.external_subscriptions_state_dir.absolute()
                if settings.external_subscriptions_state_dir is not None
                else database.parent / "external-subscriptions"
            ),
            notifications=(
                settings.notifications_state_dir.absolute()
                if settings.notifications_state_dir is not None
                else database.parent / "notifications"
            ),
            federation=(
                settings.federation_state_dir.absolute()
                if settings.federation_state_dir is not None
                else database.parent / "federation"
            ),
            agent_identity=(
                settings.agent_identity_file.absolute()
                if settings.agent_identity_file is not None else None
            ),
            database_url=database_url,
            database_engine=database_engine,
            state_root=state_root,
        )
    except Exception:
        raise BackupSnapshotError() from None


@contextmanager
def capture_control_plane_snapshot(
    layout: BackupStateLayout, *, barrier: BackupWriteBarrier, staging_directory: Path,
) -> Iterator[CapturedControlPlaneSnapshot]:
    """Copy a cooperating instance without holding EX during result consumption.

    The barrier must be the running application's shared instance. Other
    cooperating processes use its same pinned lock. Acquisition has the existing
    15s admission budget; each copy substep has its own 30s between-I/O budget.
    This function never cancels active work to force a snapshot to succeed.
    """
    if type(barrier) is not BackupWriteBarrier:
        raise BackupSnapshotError()
    # Reject overlap before SQLite is allowed to create even a private temporary
    # child: a staging directory must not be one of the source state trees.
    _layout(layout, staging_directory)
    with ExitStack() as resources:
        with barrier.snapshot() as permit:
            captured_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if layout.database_engine == "sqlite":
                database = resources.enter_context(sqlite_backup_snapshot(
                    layout.database, permit=permit, staging_directory=staging_directory,
                ))
            elif layout.database_engine == "postgresql" and layout.database_url is not None:
                database = resources.enter_context(postgres_backup_snapshot(
                    layout.database_url, staging_directory=staging_directory,
                ))
            else:
                raise BackupSnapshotError()
            state = resources.enter_context(staged_backup_state(
                layout, permit=permit, staging_directory=staging_directory,
                database_size=database.size,
            ))
            permit.assert_for_lock(
                (layout.state_root or layout.database.parent) / ".open-node-backup.lock"
            )
            captured = CapturedControlPlaneSnapshot(captured_at, database, state)
        yield captured
