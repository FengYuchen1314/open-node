"""Stage browser uploads and activate restored SQLite/PostgreSQL state after restart."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from signal import SIGTERM
from time import time
from uuid import UUID, uuid4

from open_node.core.config import Settings
from open_node.domain.restore import (
    BrowserRestoreError,
    RestorePreparedRead,
    RestoreUploadRead,
)
from open_node.services.backup_encryption import (
    MAX_ENCRYPTED_ARCHIVE_BYTES,
    decrypted_backup_archive,
)
from open_node.services.backup_postgres_restore import (
    drop_postgres_database,
    postgres_url,
    require_drop_postgres_database,
)
from open_node.services.backup_restore import (
    POSTGRES_RESTORE_METADATA,
    BackupRestoreError,
    restore_backup_archive,
)
from open_node.services.backup_snapshot import BackupSnapshotError, configured_backup_layout
from open_node.services.backup_sqlite import BackupSQLiteError, _directory
from open_node.services.backup_validation import BackupValidationError

UPLOAD_DIRECTORY = ".open-node-restore-uploads"
ACTIVATION_MARKER = ".open-node-browser-restore.json"
PENDING_PREFIX = ".open-node-restore-pending-"
ROLLBACK_PREFIX = ".open-node-restore-rollback-"
UPLOAD_TTL_SECONDS = 1800
MAX_UPLOADS = 2
MAX_TOP_LEVEL_ENTRIES = 256
_IDENTIFIER = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_TEMPORARY_RESTORE = re.compile(r"^\.open-node-restore-[0-9a-f]{32}$")
_UPLOAD_KEYS = {"schema_version", "id", "owner", "size", "sha256", "expires_at"}
_MARKER_KEYS = {
    "schema_version", "request_id", "restore_id", "pending_dir", "phase",
    "old_entries", "new_entries", "database_engine", "stage_database",
}


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        count = os.write(descriptor, view)
        if count <= 0:
            raise OSError("short write")
        view = view[count:]


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _json(value: bytes, maximum: int) -> dict:
    if not 1 < len(value) <= maximum:
        raise ValueError("invalid JSON size")
    result = json.loads(
        value.decode("ascii"), object_pairs_hook=_unique,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(result, dict):
        raise ValueError("object required")
    return result


def _read_file(directory: int, name: str, maximum: int, *, owner: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= maximum
        ):
            raise OSError("unsafe file")
        value = b""
        while len(value) <= maximum:
            block = os.read(descriptor, min(65_536, maximum + 1 - len(value)))
            if not block:
                break
            value += block
        after = os.fstat(descriptor)
        if (
            len(value) != info.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        ):
            raise OSError("file changed")
        return value
    finally:
        os.close(descriptor)


def _atomic_json(directory: int, name: str, value: dict, *, replace: bool) -> None:
    temporary = ".restore-json-" + uuid4().hex
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600, dir_fd=directory,
    )
    try:
        content = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        if replace:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        else:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
            os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _root(settings: Settings) -> Path:
    try:
        layout = configured_backup_layout(settings)
        root = layout.state_root or layout.database.parent
        expected_database = (
            root / "open-node.db"
            if layout.database_engine == "sqlite"
            else root / ".postgresql-database"
        )
        if (
            layout.database != expected_database
            or layout.certificates != root / "certificates"
            or layout.external_subscriptions != root / "external-subscriptions"
            or layout.federation != root / "federation"
            or layout.notifications != root / "notifications"
            or layout.agent_identity not in (None, root / "agent-identity.seed")
        ):
            raise BrowserRestoreError("restore_upload_unavailable", 503)
        if "\n" in str(root) or "\r" in str(root):
            raise BrowserRestoreError("restore_upload_unavailable", 503)
        return root
    except BrowserRestoreError:
        raise
    except (BackupSnapshotError, ValueError, OSError):
        raise BrowserRestoreError("restore_upload_unavailable", 503) from None


class RestoreUploadWriter:
    def __init__(self, store: BrowserRestoreStore, owner: str, expected: int):
        self.store, self.owner, self.expected = store, owner, expected
        self.identifier = uuid4()
        self.directory = -1
        self.descriptor = -1
        self.total = 0
        self.digest = hashlib.sha256()
        self.published = False

    def __enter__(self):
        with self.store.upload_lock:
            self.directory = self.store._uploads(clean=True)
            try:
                records = {
                    name.rsplit(".", 1)[0] for name in os.listdir(self.directory)
                    if name.endswith((".json", ".bin"))
                    and _IDENTIFIER.fullmatch(name.rsplit(".", 1)[0])
                }
                if len(records) >= MAX_UPLOADS:
                    raise BrowserRestoreError("restore_upload_busy", 409)
                self.descriptor = os.open(
                    str(self.identifier) + ".bin",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=self.directory,
                )
            except BaseException:
                os.close(self.directory)
                self.directory = -1
                raise
        return self

    def write(self, block: bytes) -> None:
        if type(block) is not bytes or not block:
            return
        if self.total + len(block) > self.expected:
            raise BrowserRestoreError("restore_upload_invalid", 413)
        _write_all(self.descriptor, block)
        self.total += len(block)
        self.digest.update(block)

    def finish(self) -> RestoreUploadRead:
        if self.total != self.expected:
            raise BrowserRestoreError("restore_upload_invalid", 422)
        os.fsync(self.descriptor)
        expires = self.store.clock() + UPLOAD_TTL_SECONDS
        value = {
            "schema_version": 1,
            "id": str(self.identifier),
            "owner": self.owner,
            "size": self.total,
            "sha256": self.digest.hexdigest(),
            "expires_at": expires,
        }
        _atomic_json(self.directory, str(self.identifier) + ".json", value, replace=False)
        self.published = True
        return RestoreUploadRead(
            id=self.identifier, size=self.total, sha256=value["sha256"],
            expires_at=datetime.fromtimestamp(expires, UTC),
        )

    def __exit__(self, _kind, _error, _traceback):
        if self.descriptor >= 0:
            os.close(self.descriptor)
        if not self.published and self.directory >= 0:
            try:
                os.unlink(str(self.identifier) + ".bin", dir_fd=self.directory)
            except FileNotFoundError:
                pass
        if self.directory >= 0:
            os.close(self.directory)


class BrowserRestoreStore:
    def __init__(self, settings: Settings, *, clock=time):
        self.settings = settings
        self.clock = clock
        try:
            self.root: Path | None = _root(settings)
        except BrowserRestoreError:
            self.root = None
        self.automatic_restart = settings.browser_restore_auto_restart
        self.upload_lock = threading.Lock()

    @property
    def available(self) -> bool:
        if self.root is None:
            return False
        try:
            descriptor = self._root_fd()
            os.close(descriptor)
            return True
        except BrowserRestoreError:
            return False

    def _root_fd(self) -> int:
        if self.root is None:
            raise BrowserRestoreError("restore_upload_unavailable", 503)
        try:
            return _directory(self.root, private=True)
        except (BackupSQLiteError, OSError):
            raise BrowserRestoreError("restore_upload_unavailable", 503) from None

    def _uploads(self, *, clean: bool) -> int:
        root = self._root_fd()
        try:
            try:
                os.mkdir(UPLOAD_DIRECTORY, 0o700, dir_fd=root)
                os.fsync(root)
            except FileExistsError:
                pass
            directory = os.open(
                UPLOAD_DIRECTORY,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root,
            )
        finally:
            os.close(root)
        info = os.fstat(directory)
        if (
            not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(directory)
            raise BrowserRestoreError("restore_upload_unavailable", 503)
        if clean:
            self._clean(directory)
        return directory

    def _clean(self, directory: int) -> None:
        names = set(os.listdir(directory))
        for name in names:
            if not name.endswith(".json") or not _IDENTIFIER.fullmatch(name[:-5]):
                continue
            try:
                value = _json(_read_file(directory, name, 4096, owner=os.geteuid()), 4096)
                expired = set(value) != _UPLOAD_KEYS or float(value["expires_at"]) <= self.clock()
            except Exception:
                expired = True
            if expired:
                identifier = name[:-5]
                for suffix in (".json", ".bin"):
                    try:
                        os.unlink(identifier + suffix, dir_fd=directory)
                    except FileNotFoundError:
                        pass
        # A killed request can leave only its unpublished body. Do not touch a
        # recent body because another in-process upload may still own it.
        for name in names:
            if (
                not name.endswith(".bin") or not _IDENTIFIER.fullmatch(name[:-4])
                or name[:-4] + ".json" in names
            ):
                continue
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                stale = (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_mtime <= self.clock() - UPLOAD_TTL_SECONDS
                )
            except OSError:
                stale = True
            if stale:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
        os.fsync(directory)

    def writer(self, owner: str, expected: int) -> RestoreUploadWriter:
        if (
            not re.fullmatch(r"[a-f0-9]{64}", owner)
            or type(expected) is not int or not 22 <= expected <= MAX_ENCRYPTED_ARCHIVE_BYTES
        ):
            raise BrowserRestoreError("restore_upload_invalid", 422)
        root = self._root_fd()
        try:
            try:
                os.stat(ACTIVATION_MARKER, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BrowserRestoreError("restore_upload_busy", 409)
        finally:
            os.close(root)
        return RestoreUploadWriter(self, owner, expected)

    @contextmanager
    def _upload(self, identifier: UUID, owner: str):
        directory = self._uploads(clean=True)
        descriptor = -1
        try:
            name = str(identifier)
            try:
                value = _json(
                    _read_file(directory, name + ".json", 4096, owner=os.geteuid()), 4096,
                )
                if (
                    set(value) != _UPLOAD_KEYS or value["schema_version"] != 1
                    or value["id"] != name or value["owner"] != owner
                    or type(value["size"]) is not int
                    or not 22 <= value["size"] <= MAX_ENCRYPTED_ARCHIVE_BYTES
                    or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
                    or type(value["expires_at"]) not in (int, float)
                    or value["expires_at"] <= self.clock()
                ):
                    raise ValueError("invalid upload")
                descriptor = os.open(
                    name + ".bin", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory,
                )
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size != value["size"]
                ):
                    raise ValueError("invalid upload file")
            except Exception:
                raise BrowserRestoreError("restore_upload_not_found", 404) from None
            with os.fdopen(descriptor, "rb", buffering=0) as source:
                descriptor = -1
                yield source, value, directory
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    def prepare(self, identifier: UUID, owner: str, payload) -> RestorePreparedRead:
        identity = payload.identity.get_secret_value()
        if (payload.format == "age") != bool(identity):
            raise BrowserRestoreError("restore_upload_invalid", 422)
        totp = payload.subscriber_totp_key.get_secret_value()
        root = self._root_fd()
        try:
            try:
                os.stat(ACTIVATION_MARKER, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BrowserRestoreError("restore_upload_busy", 409)
        finally:
            os.close(root)
        pending = PENDING_PREFIX + str(identifier)
        stage = ""
        marker_published = False
        try:
            with self._upload(identifier, owner) as (source, metadata, uploads):
                if payload.format == "age":
                    with decrypted_backup_archive(
                        source, identity.encode("utf-8"),
                        temporary_directory=str(self.root / UPLOAD_DIRECTORY),
                    ) as staged:
                        if (
                            staged.report.encrypted_size != metadata["size"]
                            or staged.report.encrypted_sha256 != metadata["sha256"]
                        ):
                            raise BackupValidationError()
                        record = restore_backup_archive(
                            staged.stream, str(self.root / pending),
                            totp_key=totp.encode("ascii") if totp else None,
                            database_url=self.settings.database_url,
                        )
                else:
                    record = restore_backup_archive(
                        source, str(self.root / pending),
                        totp_key=totp.encode("ascii") if totp else None,
                        database_url=self.settings.database_url,
                    )
                    if record.archive_sha256 != metadata["sha256"]:
                        raise BackupRestoreError()
                engine, stage = _pending_database(self.root / pending)
                marker = {
                    "schema_version": 1,
                    "request_id": str(identifier),
                    "restore_id": str(record.id),
                    "pending_dir": pending,
                    "phase": "prepared",
                    "old_entries": [],
                    "new_entries": [],
                    "database_engine": engine,
                    "stage_database": stage,
                }
                root = self._root_fd()
                try:
                    _atomic_json(root, ACTIVATION_MARKER, marker, replace=False)
                    marker_published = True
                finally:
                    os.close(root)
                for suffix in (".json", ".bin"):
                    with suppress(FileNotFoundError):
                        os.unlink(str(identifier) + suffix, dir_fd=uploads)
                with suppress(OSError):
                    os.fsync(uploads)
            return RestorePreparedRead(
                id=record.id, automatic_restart=self.automatic_restart,
            )
        except BrowserRestoreError:
            if not marker_published:
                _discard_pending_database(self.settings.database_url, self.root, pending, stage)
            raise
        except (BackupRestoreError, BackupValidationError, OSError, UnicodeError, ValueError):
            if not marker_published:
                _discard_pending_database(self.settings.database_url, self.root, pending, stage)
            raise BrowserRestoreError("restore_prepare_failed", 422) from None

    def request_restart(self) -> None:
        if not self.automatic_restart:
            return
        timer = threading.Timer(0.75, os.kill, args=(os.getpid(), SIGTERM))
        timer.daemon = True
        timer.start()


def _private_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BrowserRestoreError("restore_prepare_failed", 503)


def _postgres_stage_metadata(directory: int) -> str:
    value = _json(
        _read_file(
            directory,
            POSTGRES_RESTORE_METADATA,
            4096,
            owner=os.geteuid(),
        ),
        4096,
    )
    stage = value.get("stage_database")
    if (
        set(value) != {"schema_version", "stage_database"}
        or value["schema_version"] != 1
        or type(stage) is not str
        or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", stage)
    ):
        raise BrowserRestoreError("restore_prepare_failed", 503)
    return stage


def _pending_database(path: Path) -> tuple[str, str]:
    directory = _directory(path, private=True)
    try:
        sqlite_present = _exists(directory, "open-node.db")
        postgres_present = _exists(directory, POSTGRES_RESTORE_METADATA)
        if sqlite_present == postgres_present:
            raise BrowserRestoreError("restore_prepare_failed", 503)
        if sqlite_present:
            return "sqlite", ""
        return "postgresql", _postgres_stage_metadata(directory)
    finally:
        os.close(directory)


def _discard_pending_database(
    database_url: str, root: Path | None, pending: str, stage: str
) -> None:
    if (
        root is None
        or pending != PENDING_PREFIX + pending.removeprefix(PENDING_PREFIX)
        or not _IDENTIFIER.fullmatch(pending.removeprefix(PENDING_PREFIX))
    ):
        return
    if not stage:
        try:
            directory = _directory(root / pending, private=True)
        except (BackupSQLiteError, OSError):
            return
        try:
            if _exists(directory, POSTGRES_RESTORE_METADATA):
                stage = _postgres_stage_metadata(directory)
        except (BrowserRestoreError, OSError, ValueError):
            return
        finally:
            os.close(directory)
    if stage and not drop_postgres_database(database_url, stage):
        return
    with suppress(OSError):
        shutil.rmtree(root / pending)


def _orphan_restore_name(name: str) -> bool:
    return bool(
        _TEMPORARY_RESTORE.fullmatch(name)
        or (
            name.startswith(PENDING_PREFIX)
            and _IDENTIFIER.fullmatch(name.removeprefix(PENDING_PREFIX))
        )
    )


def _cleanup_orphan_restores(
    settings: Settings,
    root_path: Path,
    root: int,
    *,
    preserve: set[str],
) -> None:
    for name in sorted(os.listdir(root)):
        if name in preserve or not _orphan_restore_name(name):
            continue
        directory = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root,
        )
        try:
            _private_directory(directory)
            if _exists(directory, POSTGRES_RESTORE_METADATA):
                stage = _postgres_stage_metadata(directory)
                require_drop_postgres_database(settings.database_url, stage)
        finally:
            os.close(directory)
        shutil.rmtree(root_path / name)
        os.fsync(root)


def _entry_name(name: str) -> bool:
    return bool(
        name and name not in {".", ".."} and "/" not in name and "\x00" not in name
        and not name.startswith(ROLLBACK_PREFIX)
    )


def _safe_entries(
    directory: int, excluded: set[str], *, preserve_rollbacks: bool = False,
) -> list[str]:
    names = sorted(os.listdir(directory))
    if len(names) > MAX_TOP_LEVEL_ENTRIES:
        raise BrowserRestoreError("restore_prepare_failed", 503)
    result = []
    for name in names:
        if name in excluded:
            continue
        if preserve_rollbacks and name.startswith(ROLLBACK_PREFIX):
            if not _IDENTIFIER.fullmatch(name.removeprefix(ROLLBACK_PREFIX)):
                raise BrowserRestoreError("restore_prepare_failed", 503)
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            try:
                _private_directory(descriptor)
            finally:
                os.close(descriptor)
            continue
        if not _entry_name(name):
            raise BrowserRestoreError("restore_prepare_failed", 503)
        result.append(name)
    return result


def _marker(root: int) -> dict | None:
    try:
        value = _json(_read_file(root, ACTIVATION_MARKER, 65_536, owner=os.geteuid()), 65_536)
    except FileNotFoundError:
        return None
    if (
        set(value) != _MARKER_KEYS or value["schema_version"] != 1
        or not _IDENTIFIER.fullmatch(str(value["request_id"]))
        or not _IDENTIFIER.fullmatch(str(value["restore_id"]))
        or value["pending_dir"] != PENDING_PREFIX + value["request_id"]
        or value["phase"] not in {
            "prepared", "database_old_renamed", "database_activated",
            "moving_old", "moving_new", "activated",
        }
        or not isinstance(value["old_entries"], list)
        or not isinstance(value["new_entries"], list)
        or any(type(name) is not str for name in value["old_entries"] + value["new_entries"])
        or len(value["old_entries"]) > MAX_TOP_LEVEL_ENTRIES
        or len(value["new_entries"]) > MAX_TOP_LEVEL_ENTRIES
        or len(set(value["old_entries"])) != len(value["old_entries"])
        or len(set(value["new_entries"])) != len(value["new_entries"])
        or any(not _entry_name(name) for name in value["old_entries"] + value["new_entries"])
        or value["database_engine"] not in {"sqlite", "postgresql"}
        or type(value["stage_database"]) is not str
        or (
            value["database_engine"] == "sqlite"
            and (value["stage_database"] or value["phase"].startswith("database_"))
        )
        or (
            value["database_engine"] == "postgresql"
            and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value["stage_database"])
        )
    ):
        raise BrowserRestoreError("restore_prepare_failed", 503)
    return value


def _activate_postgres_database(settings: Settings, root: int, value: dict) -> None:
    try:
        import psycopg
        from psycopg import sql

        configured = postgres_url(settings.database_url)
        current = configured.database
        stage = value["stage_database"]
        if current == "postgres" or stage == current:
            raise ValueError("unsafe database name")
        suffix = "_rollback_" + value["request_id"].replace("-", "")[:16]
        rollback = current[: 63 - len(suffix)] + suffix
        admin = postgres_url(settings.database_url, "postgres")
        admin_url = admin.set(drivername="postgresql").render_as_string(
            hide_password=False
        )
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                    ([current, stage, rollback],),
                )
            }
            phase = value["phase"]
            if phase == "prepared":
                if names == {stage, rollback}:
                    phase = "database_old_renamed"
                elif names == {current, rollback}:
                    phase = "database_activated"
                elif names != {current, stage}:
                    raise ValueError("unexpected restore databases")
                else:
                    connection.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=%s AND pid<>pg_backend_pid()",
                        (current,),
                    )
                    connection.execute(
                        sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                            sql.Identifier(current), sql.Identifier(rollback)
                        )
                    )
                    phase = "database_old_renamed"
                value["phase"] = phase
                _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
            if phase == "database_old_renamed":
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                        ([current, stage, rollback],),
                    )
                }
                if names == {current, rollback}:
                    phase = "database_activated"
                elif names != {stage, rollback}:
                    raise ValueError("unexpected staging database")
                else:
                    connection.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=%s AND pid<>pg_backend_pid()",
                        (stage,),
                    )
                    connection.execute(
                        sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                            sql.Identifier(stage), sql.Identifier(current)
                        )
                    )
                    phase = "database_activated"
                value["phase"] = phase
                _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
            if phase != "database_activated":
                raise ValueError("database activation incomplete")
    except BrowserRestoreError:
        raise
    except Exception:
        raise BrowserRestoreError("restore_prepare_failed", 503) from None


def activate_pending_restore(settings: Settings) -> Path:
    """Finish a prepared directory switch before the application opens SQLite."""
    root_path = _root(settings)
    root = _directory(root_path, private=True)
    pending = rollback = -1
    try:
        value = _marker(root)
        _cleanup_orphan_restores(
            settings,
            root_path,
            root,
            preserve={value["pending_dir"]} if value is not None else set(),
        )
        if value is None:
            return root_path
        pending_name = value["pending_dir"]
        rollback_name = ROLLBACK_PREFIX + value["request_id"]
        try:
            pending = os.open(
                pending_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root,
            )
            _private_directory(pending)
        except FileNotFoundError:
            if value["phase"] not in {"moving_new", "activated"}:
                raise BrowserRestoreError("restore_prepare_failed", 503) from None
        if (
            pending >= 0
            and value["database_engine"] == "postgresql"
            and value["phase"] in {
                "prepared", "database_old_renamed", "database_activated",
            }
            and _exists(pending, POSTGRES_RESTORE_METADATA)
        ):
            if _postgres_stage_metadata(pending) != value["stage_database"]:
                raise BrowserRestoreError("restore_prepare_failed", 503)
            os.unlink(POSTGRES_RESTORE_METADATA, dir_fd=pending)
            os.fsync(pending)
        try:
            os.mkdir(rollback_name, 0o700, dir_fd=root)
            os.fsync(root)
        except FileExistsError:
            pass
        rollback = os.open(
            rollback_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root,
        )
        _private_directory(rollback)
        if value["phase"] in {
            "prepared", "database_old_renamed", "database_activated",
        }:
            # Validate the complete filesystem switch before PostgreSQL is renamed.
            # Persist the exact lists into the same journal so a crash in either
            # database rename resumes with the already-reviewed state transition.
            value["old_entries"] = _safe_entries(
                root, {ACTIVATION_MARKER, pending_name, rollback_name},
                preserve_rollbacks=True,
            )
            if pending < 0:
                raise BrowserRestoreError("restore_prepare_failed", 503)
            value["new_entries"] = _safe_entries(pending, set())
            required = {".open-node-restore.json", "restore.env"}
            if value["database_engine"] == "sqlite":
                required.add("open-node.db")
            if not required <= set(value["new_entries"]):
                raise BrowserRestoreError("restore_prepare_failed", 503)
            _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
        if value["database_engine"] == "postgresql" and value["phase"] in {
            "prepared", "database_old_renamed", "database_activated",
        }:
            _activate_postgres_database(settings, root, value)
        if value["phase"] in {"prepared", "database_activated"}:
            value["phase"] = "moving_old"
            _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
        if value["phase"] == "moving_old":
            for name in value["old_entries"]:
                root_exists = _exists(root, name)
                rollback_exists = _exists(rollback, name)
                if root_exists and rollback_exists:
                    raise BrowserRestoreError("restore_prepare_failed", 503)
                if root_exists:
                    os.rename(name, name, src_dir_fd=root, dst_dir_fd=rollback)
                elif not rollback_exists:
                    raise BrowserRestoreError("restore_prepare_failed", 503)
            os.fsync(rollback)
            os.fsync(root)
            value["phase"] = "moving_new"
            _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
        if value["phase"] == "moving_new":
            for name in value["new_entries"]:
                root_exists = _exists(root, name)
                pending_exists = pending >= 0 and _exists(pending, name)
                if root_exists and pending_exists:
                    raise BrowserRestoreError("restore_prepare_failed", 503)
                if pending_exists:
                    os.rename(name, name, src_dir_fd=pending, dst_dir_fd=root)
                elif not root_exists:
                    raise BrowserRestoreError("restore_prepare_failed", 503)
            if pending >= 0:
                os.fsync(pending)
                os.close(pending)
                pending = -1
                os.rmdir(pending_name, dir_fd=root)
            os.fsync(root)
            value["phase"] = "activated"
            _atomic_json(root, ACTIVATION_MARKER, value, replace=True)
        if value["phase"] == "activated":
            os.unlink(ACTIVATION_MARKER, dir_fd=root)
            os.fsync(root)
        return root_path
    except BrowserRestoreError:
        raise
    except Exception:
        raise BrowserRestoreError("restore_prepare_failed", 503) from None
    finally:
        if pending >= 0:
            os.close(pending)
        if rollback >= 0:
            os.close(rollback)
        os.close(root)


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
