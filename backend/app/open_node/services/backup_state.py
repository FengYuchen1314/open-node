"""Copy configured private state while a real database snapshot permit is held.

This module does not open application stores, initialize missing keys, decide
coverage, or establish key/database compatibility. A later assembler must make
those checks against the same SQLite snapshot before publishing an archive.

Only ordinary local paths are supported. Every path component is opened without
following links; source trees must be private and non-overlapping. Copies share
one anonymous staging file so the v1 file-count limit does not require thousands
of simultaneously open descriptors. Before yielding, all writable handles are
closed. The caller may then release its permit while reading the stable copies.

The 30-second copy deadline is checked between local operations, not a promise
to interrupt a blocked kernel call. Advisory coordination covers participating
writers, not a hostile process running as the service account.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

from open_node.domain.backup import (
    MAX_FILES,
    MAX_TOTAL_FILE_BYTES,
    BackupFileEntry,
    BackupFileRole,
    validate_backup_path,
)
from open_node.services.backup_coordination import (
    BackupCoordinationError,
    BackupSnapshotPermit,
)
from open_node.services.backup_validation import READ_CHUNK_BYTES

MAX_STATE_SECONDS = 30
MAX_STATE_DEPTH = 32
MAX_STATE_ITEMS = 2 * MAX_FILES
_LOCK_NAME = ".open-node-backup.lock"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_ROOTS = (
    ("certificates", "certificate_state", "data/certificates"),
    ("external_subscriptions", "external_state", "data/external-subscriptions"),
    ("notifications", "notification_state", "data/notifications"),
)
_KEY_NAMES = {
    "external_state": frozenset({"vault.key", "vault.initialized", "vault.lock"}),
    "notification_state": frozenset({"telegram.key", "telegram.initialized"}),
}


class BackupStateError(RuntimeError):
    code = "backup_state_unavailable"

    def __init__(self) -> None:
        super().__init__("Backup state snapshot is unavailable.")


@dataclass(frozen=True, slots=True)
class BackupStateLayout:
    database: Path = field(repr=False)
    certificates: Path = field(repr=False)
    external_subscriptions: Path | None = field(repr=False)
    notifications: Path | None = field(repr=False)
    agent_identity: Path | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class StagedBackupState:
    entries: tuple[BackupFileEntry, ...]
    sources: Mapping[str, BinaryIO] = field(repr=False)
    present_roots: frozenset[str]


def _path(value: object) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or value == Path(value.anchor)
        or ".." in value.parts
    ):
        raise BackupStateError()
    return value


def _private(info: os.stat_result, *, directory: bool = False) -> None:
    expected = (0o500, 0o700) if directory else (0o400, 0o600)
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or stat.S_IMODE(info.st_mode) not in expected
        or info.st_uid != os.geteuid()
        or (not directory and info.st_nlink != 1)
    ):
        raise BackupStateError()


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


@contextmanager
def _directory(path: Path, *, private: bool = True) -> Iterator[int]:
    """Pin every component; only a root-owned sticky ancestor may be writable."""
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            info = os.fstat(fd)
            if info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            ):
                raise BackupStateError()
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if private:
            _private(info, directory=True)
        elif info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise BackupStateError()
        yield fd
    finally:
        os.close(fd)


class _Slice(io.RawIOBase):
    """A bounded read-only view; positions never modify the shared spool cursor."""

    def __init__(self, spool: BinaryIO, offset: int, size: int) -> None:
        super().__init__()
        self._spool, self._offset, self._size = spool, offset, size
        self._position = 0

    def _check(self) -> None:
        if self.closed or self._spool.closed:
            raise ValueError("I/O operation on closed snapshot.")

    def readable(self) -> bool:
        self._check()
        return True

    def writable(self) -> bool:
        self._check()
        return False

    def seekable(self) -> bool:
        self._check()
        return True

    def tell(self) -> int:
        self._check()
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._check()
        if type(offset) is not int or type(whence) is not int:
            raise ValueError("Invalid snapshot seek.")
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError("Invalid snapshot seek.")
        if position < 0:
            raise ValueError("Invalid snapshot seek.")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        self._check()
        if type(size) is not int or size < -1:
            raise ValueError("Invalid snapshot read.")
        remaining = max(0, self._size - self._position)
        count = min(READ_CHUNK_BYTES, remaining, remaining if size == -1 else size)
        if not count:
            return b""
        block = os.pread(self._spool.fileno(), count, self._offset + self._position)
        if len(block) != count:
            raise BackupStateError()
        self._position += len(block)
        return block


class _Copy:
    def __init__(self, permit: BackupSnapshotPermit, database: Path, budget: int) -> None:
        self.permit, self.database, self.budget = permit, database, budget
        self.deadline = time.monotonic() + MAX_STATE_SECONDS
        self.items = self.total = 0
        self.entries: list[BackupFileEntry] = []
        self.offsets: list[int] = []
        self.inodes: set[tuple[int, int]] = set()

    def check(self) -> None:
        self.permit.assert_for_lock(self.database.parent / _LOCK_NAME)
        if time.monotonic() >= self.deadline:
            raise BackupStateError()

    def item(self) -> None:
        self.check()
        self.items += 1
        if self.items > MAX_STATE_ITEMS:
            raise BackupStateError()

    def file(self, parent: int, name: str, path: str, role: BackupFileRole, output) -> None:
        self.check()
        validate_backup_path(path)
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _private(before)
        inode = (before.st_dev, before.st_ino)
        if inode in self.inodes:
            raise BackupStateError()
        self.inodes.add(inode)
        if len(self.entries) >= MAX_FILES - 1 or before.st_size > self.budget - self.total:
            raise BackupStateError()
        fd = os.open(name, _READ_FLAGS, dir_fd=parent)
        try:
            if _identity(os.fstat(fd)) != _identity(before):
                raise BackupStateError()
            offset, size = self.total, 0
            digest = hashlib.sha256()
            while True:
                self.check()
                block = os.read(fd, min(READ_CHUNK_BYTES, before.st_size - size + 1))
                if not block:
                    break
                if size + len(block) > before.st_size:
                    raise BackupStateError()
                digest.update(block)
                view = memoryview(block)
                while view:
                    self.check()
                    count = output.write(view)
                    if type(count) is not int or not 0 < count <= len(view):
                        raise BackupStateError()
                    view = view[count:]
                size += len(block)
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                size != before.st_size
                or _identity(after) != _identity(before)
                or _identity(os.fstat(fd)) != _identity(before)
            ):
                raise BackupStateError()
            self.check()
            self.offsets.append(offset)
            self.entries.append(BackupFileEntry(path, role, size, digest.hexdigest()))
            self.total += size
        finally:
            os.close(fd)

    def tree(self, fd: int, prefix: str, role: BackupFileRole, output, depth=0) -> None:
        self.check()
        if depth > MAX_STATE_DEPTH:
            raise BackupStateError()
        before = os.fstat(fd)
        _private(before, directory=True)
        # scandir is consumed incrementally: a huge directory cannot allocate an
        # unbounded list before the entry-count and time limits are checked.
        with os.scandir(fd) as children:
            for child in children:
                self.item()
                name = child.name
                path = validate_backup_path(prefix + "/" + name)
                if role in _KEY_NAMES and (depth or name not in _KEY_NAMES[role]):
                    raise BackupStateError()
                info = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if role != "certificate_state":
                        raise BackupStateError()
                    child_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    try:
                        if _identity(os.fstat(child_fd)) != _identity(info):
                            raise BackupStateError()
                        self.tree(child_fd, path, role, output, depth + 1)
                        if _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != (
                            _identity(info)
                        ):
                            raise BackupStateError()
                    finally:
                        os.close(child_fd)
                else:
                    _private(info)
                    # These root-level files only coordinate running processes;
                    # ACME jobs and their contents are deliberately not omitted.
                    if depth == 0 and name in {"worker.lock", "vault.lock"}:
                        if info.st_size:
                            raise BackupStateError()
                        continue
                    self.file(fd, name, path, role, output)
        if _identity(os.fstat(fd)) != _identity(before):
            raise BackupStateError()
        self.check()


def _layout(layout: BackupStateLayout, staging: Path) -> None:
    if type(layout) is not BackupStateLayout:
        raise BackupStateError()
    _path(layout.database)
    _path(layout.certificates)
    roots = [_path(getattr(layout, name)) for name, _, _ in _ROOTS
             if getattr(layout, name) is not None]
    paths = [layout.database, *roots, _path(staging)]
    if layout.agent_identity is not None:
        paths.append(_path(layout.agent_identity))
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            if path.is_relative_to(other) or other.is_relative_to(path):
                raise BackupStateError()


@contextmanager
def staged_backup_state(
    layout: BackupStateLayout, *, permit: BackupSnapshotPermit,
    staging_directory: Path, database_size: int,
) -> Iterator[StagedBackupState]:
    """Yield immutable private copies, never a claim of complete recoverability.

    ``database_size`` reserves space for the independent SQLite snapshot in the
    v1 aggregate 1-GiB/4096-file budget. Missing optional state directories are
    reported, not initialized or interpreted as proof that no DB depends on them.
    An explicitly configured Agent identity must exist.
    """
    yielded = False
    try:
        if type(permit) is not BackupSnapshotPermit:
            raise BackupCoordinationError()
        permit.assert_active()
        _layout(layout, staging_directory)
        if type(database_size) is not int or not 0 <= database_size <= MAX_TOTAL_FILE_BYTES:
            raise BackupStateError()
        copier = _Copy(permit, layout.database, MAX_TOTAL_FILE_BYTES - database_size)
        copier.check()
        with ExitStack() as stack:
            staging_fd = stack.enter_context(_directory(staging_directory))
            staging_info = os.fstat(staging_fd)
            root_inodes = {(staging_info.st_dev, staging_info.st_ino)}
            # /proc/self/fd intentionally addresses our held directory/file;
            # source paths never use this symlink-following reopening mechanism.
            output = stack.enter_context(tempfile.TemporaryFile(
                mode="w+b", buffering=0, dir=f"/proc/self/fd/{staging_fd}",
            ))
            os.fchmod(output.fileno(), 0o600)
            present = set()
            for name, role, prefix in _ROOTS:
                path = getattr(layout, name)
                if path is None:
                    continue
                with ExitStack() as root_stack:
                    try:
                        root_fd = root_stack.enter_context(_directory(path))
                    except FileNotFoundError:
                        continue
                    root_info = os.fstat(root_fd)
                    root_inode = (root_info.st_dev, root_info.st_ino)
                    if root_inode in root_inodes:
                        raise BackupStateError()
                    root_inodes.add(root_inode)
                    present.add(name)
                    copier.tree(root_fd, prefix, role, output)
                    with _directory(path) as current:
                        if _identity(os.fstat(current)) != _identity(os.fstat(root_fd)):
                            raise BackupStateError()
            if layout.agent_identity is not None:
                with _directory(layout.agent_identity.parent, private=False) as parent:
                    copier.item()
                    copier.file(parent, layout.agent_identity.name,
                                "secrets/agent-identity.seed", "agent_identity", output)
                    if copier.entries[-1].size != 32:
                        raise BackupStateError()
                    with _directory(layout.agent_identity.parent, private=False) as current:
                        if _identity(os.fstat(current)) != _identity(os.fstat(parent)):
                            raise BackupStateError()
                present.add("agent_identity")
            copier.check()
            output.flush()
            os.fsync(output.fileno())
            info = os.fstat(output.fileno())
            if info.st_nlink != 0 or info.st_size != copier.total:
                raise BackupStateError()
            fd = os.open(f"/proc/self/fd/{output.fileno()}", os.O_RDONLY | os.O_CLOEXEC)
            try:
                if _identity(os.fstat(fd)) != _identity(info):
                    raise BackupStateError()
                readonly = os.fdopen(fd, "rb", buffering=0)
            except BaseException:
                os.close(fd)
                raise
            stack.enter_context(readonly)
            output.close()
            copier.check()
            sources = {}
            for entry, offset in zip(copier.entries, copier.offsets, strict=True):
                sources[entry.path] = stack.enter_context(_Slice(readonly, offset, entry.size))
            copier.check()
            yielded = True
            yield StagedBackupState(tuple(copier.entries), MappingProxyType(sources),
                                    frozenset(present))
    except (BackupStateError, BackupCoordinationError):
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        if yielded:
            raise
        raise BackupStateError() from None
