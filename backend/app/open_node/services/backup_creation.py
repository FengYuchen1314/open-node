"""Create a private encrypted control-plane backup, not an HTTP job or restore.

The configured layout and existing runtime keys are trusted application inputs,
never host paths or private keys accepted from a browser. Call on one actual
owning worker thread, without a work lease. Only the initial database/state copy
holds the instance's exclusive permit. Subsequent dependency checks, ZIP writing
and pinned official-age encryption use the completed private copies.

Before yielding, close *all* plaintext resources. Retain just a read-only
descriptor for the anonymous ciphertext; the caller owns it only for this
context's lifetime. No output path is created or published, and neither recovery
nor remote Agent trust is established. Deployment settings and any TOTP key are
still external requirements. Key bytes are not promised to be erased from RAM.
"""

import fcntl
import io
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal

from open_node.domain.backup import (
    BackupCoverage,
    BackupDatabase,
    BackupFileEntry,
    BackupManifest,
    BackupSource,
)
from open_node.services.backup_archive import _manifest, write_backup_archive
from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_dependencies import BackupDependencyReport, check_backup_dependencies
from open_node.services.backup_encryption import (
    BackupEncryptionReport,
    _cipher_size,
    _recipient,
    encrypted_backup_archive,
)
from open_node.services.backup_snapshot import (
    CapturedControlPlaneSnapshot,
    capture_control_plane_snapshot,
)
from open_node.services.backup_state import BackupStateLayout, _directory
from open_node.services.backup_validation import MAX_ARCHIVE_BYTES

_DATABASE_PATH = "data/open-node.db"
_ROLES = (
    "certificates", "external_subscriptions", "notifications", "agent_identity",
)
_SPACE_RESERVE = 1024 * 1024


class BackupCreationError(RuntimeError):
    code = "backup_creation_unavailable"

    def __init__(self) -> None:
        super().__init__("Control-plane backup creation is unavailable.")


@dataclass(frozen=True, slots=True)
class CreatedControlPlaneBackup:
    stream: BinaryIO = field(repr=False)
    encryption: BackupEncryptionReport
    dependencies: BackupDependencyReport
    snapshot_consistency: Literal["cooperating_writers"] = field(
        default="cooperating_writers", init=False,
    )
    sqlite_integrity_check: Literal["passed"] = field(default="passed", init=False)
    foreign_key_check: Literal["passed"] = field(default="passed", init=False)
    restoration_ready: Literal[False] = field(default=False, init=False)


@contextmanager
def _resources() -> Iterator[ExitStack]:
    stack = ExitStack()
    try:
        yield stack
    except BaseException:
        with suppress(Exception):
            stack.close()
        raise
    else:
        stack.close()


def _checked_manifest(
    snapshot: CapturedControlPlaneSnapshot, dependencies: BackupDependencyReport,
    layout: BackupStateLayout, source: BackupSource,
) -> tuple[BackupManifest, int]:
    if dependencies.totp_status == "not_checked":
        raise BackupCreationError()
    if layout.agent_identity is not None and (
        dependencies.coverage.agent_identity != "included"
        or dependencies.agent_identity_matches_runtime is not True
    ):
        raise BackupCreationError()
    # This assembler, unlike the dependency checker alone, owns the complete
    # configured layout copied under the same permit. Unknown *absent* roles
    # with no persistent key dependency can therefore be called not configured.
    coverage = {}
    for role in _ROLES:
        state = getattr(dependencies.coverage, role)
        if state == "unknown":
            if role in dependencies.database_dependencies:
                raise BackupCreationError()
            state = "not_configured"
        coverage[role] = state
    database = snapshot.database
    manifest = BackupManifest(
        format="open-node-control-plane-backup", version=1, created_at=snapshot.created_at,
        source=source,
        database=BackupDatabase("sqlite", "standalone", database.schema_fingerprint),
        coverage=BackupCoverage(**coverage),
        required_configuration=dependencies.required_configuration,
        files=(BackupFileEntry(_DATABASE_PATH, "database", database.size, database.sha256),
               *snapshot.state.entries),
    )
    checked, raw = _manifest(manifest)
    # ZIP_STORED, no descriptors/ZIP64/comments/extras: 30-byte local + 46-byte
    # central headers, names in both, and a 22-byte end record. Include manifest.
    archive_size = 22 + 76 + 2 * len("manifest.json") + len(raw)
    archive_size += sum(76 + 2 * len(entry.path.encode("utf-8")) + entry.size
                        for entry in checked.files)
    if not 22 <= archive_size <= MAX_ARCHIVE_BYTES:
        raise BackupCreationError()
    return checked, archive_size


def _space_preflight(directory: int, archive_size: int) -> None:
    # Copies of the DB/state already consume space. Remaining peak allocation is
    # our ZIP, age's private ZIP copy and its ciphertext, plus a small block/key
    # reserve. Quotas or later concurrent allocations can still cause ENOSPC;
    # those failures must clean up, not produce a partially usable artifact.
    info = os.fstatvfs(directory)
    needed = 2 * archive_size + _cipher_size(archive_size) + _SPACE_RESERVE
    if info.f_bavail * info.f_frsize < needed:
        raise BackupCreationError()


def _retain_ciphertext(stack: ExitStack, source: BinaryIO, size: int) -> BinaryIO:
    """Duplicate one verified RO description, not the ciphertext or its contents."""
    if not isinstance(source, io.FileIO) or source.writable() or not source.readable():
        raise BackupCreationError()
    original = source.fileno()
    info = os.fstat(original)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_size != size
        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        or fcntl.fcntl(original, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
    ):
        raise BackupCreationError()
    descriptor = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 3)
    try:
        stream = io.FileIO(descriptor, "rb", closefd=True)
    except BaseException:
        os.close(descriptor)
        raise
    stack.enter_context(stream)
    if os.fstat(descriptor) != info or os.get_inheritable(descriptor):
        raise BackupCreationError()
    if stream.seek(0) != 0 or stream.tell() != 0:
        raise BackupCreationError()
    return stream


@contextmanager
def create_control_plane_backup(
    layout: BackupStateLayout, *, barrier: BackupWriteBarrier, recipient: str,
    staging_directory: Path, totp_key: bytes | None = None,
    agent_public_key: bytes | None = None, source: BackupSource | None = None,
) -> Iterator[CreatedControlPlaneBackup]:
    """Yield only complete ciphertext; do not hold plaintext for download TTL.

    ``source`` is optional deployment-provided metadata, not authenticated by
    this function. No Git checkout, Docker socket or environment dump is read.
    The native recipient checksum is checked by age before any result is yielded.
    Each component keeps its existing bounded supported envelope; this is not a
    hard end-to-end deadline for uninterruptible kernel I/O or thread cancellation.
    """
    with _resources() as artifact:
        try:
            _recipient(recipient)  # Cheap shape rejection before a snapshot.
            if source is None:
                source = BackupSource(None, None, None)
            with _resources() as plaintext:
                directory = plaintext.enter_context(_directory(staging_directory))
                snapshot = plaintext.enter_context(capture_control_plane_snapshot(
                    layout, barrier=barrier, staging_directory=staging_directory,
                ))
                dependencies = check_backup_dependencies(
                    snapshot.database.connection, snapshot.state.sources,
                    totp_key=totp_key, agent_public_key=agent_public_key,
                )
                manifest, archive_size = _checked_manifest(snapshot, dependencies, layout, source)
                _space_preflight(directory, archive_size)
                archive = plaintext.enter_context(tempfile.TemporaryFile(
                    mode="w+b", buffering=0, dir=f"/proc/self/fd/{directory}",
                ))
                inputs = {_DATABASE_PATH: snapshot.database.stream, **snapshot.state.sources}
                written = write_backup_archive(archive, manifest, inputs)
                if written.archive_size != archive_size:
                    raise BackupCreationError()
                archive.flush()
                os.fsync(archive.fileno())
                # A trailing dot lets age's NOFOLLOW directory open refer to
                # this held directory, even if its original pathname changes.
                with encrypted_backup_archive(
                    archive, recipient, temporary_directory=f"/proc/self/fd/{directory}/.",
                ) as encrypted:
                    if encrypted.report.archive_report != written:
                        raise BackupCreationError()
                    result = CreatedControlPlaneBackup(
                        stream=_retain_ciphertext(
                            artifact, encrypted.stream, encrypted.report.encrypted_size,
                        ),
                        encryption=encrypted.report, dependencies=dependencies,
                    )
            # SQLite, every state slice, ZIP, keys and age's staging handles
            # have now closed. Just artifact's anonymous read-only fd remains.
        except BackupBusyError:
            raise
        except Exception:
            raise BackupCreationError() from None
        yield result
