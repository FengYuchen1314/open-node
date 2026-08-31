"""Create a private staging ZIP from completed, exclusively held local binary streams.

This is not an online snapshot, encrypted export, or publication operation. Stream
privacy, stability, and exclusive ownership are caller obligations; arbitrary
BinaryIO wrappers cannot prove that different handles refer to different files.
Streams may move but are never closed. A failure may leave partial destination
bytes: the caller must discard them, not publish or use them as a backup.

Generation and independent archive validation each have a 30-second between-I/O
deadline. Neither can interrupt an indefinitely blocking underlying operation;
network streams do not satisfy the contract. Success leaves cursor positions
unspecified (the current validator leaves the destination at EOF).
"""

import hashlib
import io
import json
import stat
import time
import zipfile
from collections.abc import Mapping
from dataclasses import asdict, fields
from typing import BinaryIO, NoReturn

from open_node.domain.backup import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_BYTES,
    MAX_TOTAL_FILE_BYTES,
    BackupCoverage,
    BackupDatabase,
    BackupFileEntry,
    BackupManifest,
    BackupSource,
    BackupValidationError,
    parse_backup_manifest,
)
from open_node.services.backup_validation import (
    MAX_ARCHIVE_BYTES,
    MAX_IO_OPERATIONS,
    MAX_VALIDATION_SECONDS,
    READ_CHUNK_BYTES,
    BackupArchiveReport,
    validate_backup_archive,
)

MAX_WRITE_BYTES = 2 * MAX_ARCHIVE_BYTES
MAX_SOURCE_READ_BYTES = MAX_TOTAL_FILE_BYTES + MAX_FILES
_DOS_TIME = (1980, 1, 1, 0, 0, 0)


def _invalid() -> NoReturn:
    raise BackupValidationError() from None


class _Budget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + MAX_VALIDATION_SECONDS
        self.operations = self.written = self.read = 0

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            _invalid()

    def call(self, method, *args):
        self.check()
        self.operations += 1
        if self.operations > MAX_IO_OPERATIONS:
            _invalid()
        try:
            result = method(*args)
        except Exception:
            # In particular, prevent zipfile from interpreting an underlying
            # seek/tell OSError as permission to silently use data descriptors.
            raise BackupValidationError() from None
        self.check()
        return result


def _position(value, expected: int) -> None:
    if type(value) is not int or value != expected:
        _invalid()


class _Destination:
    """Make every stdlib write complete, bounded, and position-checked."""

    def __init__(self, destination: BinaryIO, budget: _Budget):
        self.stream, self.budget = destination, budget
        self.position = self.high_water = 0
        if (
            budget.call(destination.readable) is not True
            or budget.call(destination.writable) is not True
            or budget.call(destination.seekable) is not True
        ):
            _invalid()
        _position(budget.call(destination.seek, 0, io.SEEK_END), 0)
        _position(budget.call(destination.tell), 0)

    def tell(self) -> int:
        self.budget.check()
        _position(self.budget.call(self.stream.tell), self.position)
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if type(offset) is not int or type(whence) is not int:
            _invalid()
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self.position + offset
        elif whence == io.SEEK_END:
            target = self.high_water + offset
        else:
            _invalid()
        # zipfile only needs to rewrite existing headers, never create holes.
        if not 0 <= target <= self.high_water:
            _invalid()
        _position(self.budget.call(self.stream.seek, target, io.SEEK_SET), target)
        _position(self.budget.call(self.stream.tell), target)
        self.position = target
        return target

    def write(self, data) -> int:
        if type(data) not in {bytes, bytearray, memoryview}:
            _invalid()
        view = memoryview(data).cast("B")
        size = len(view)
        if (
            self.position + size > MAX_ARCHIVE_BYTES
            or self.budget.written + size > MAX_WRITE_BYTES
        ):
            _invalid()
        written = 0
        while written < size:
            block = view[written:written + READ_CHUNK_BYTES]
            count = self.budget.call(self.stream.write, block)
            if type(count) is not int or not 0 < count <= len(block):
                _invalid()
            written += count
            self.position += count
            self.budget.written += count
            self.high_water = max(self.high_water, self.position)
            _position(self.budget.call(self.stream.tell), self.position)
        return size

    def flush(self) -> None:
        self.budget.call(self.stream.flush)

    def finish(self) -> None:
        _position(self.budget.call(self.stream.seek, 0, io.SEEK_END), self.high_water)
        _position(self.budget.call(self.stream.tell), self.high_water)
        self.position = self.high_water


def _primitive(value) -> None:
    if type(value) is str:
        if len(value) > MAX_PATH_BYTES:
            _invalid()
    elif value is None or type(value) is bool:
        return
    elif type(value) is int:
        if not 0 <= value <= MAX_FILE_BYTES:
            _invalid()
    else:
        _invalid()


def _model(value, model) -> None:
    if type(value) is not model:
        _invalid()
    for item in fields(model):
        _primitive(getattr(value, item.name))


def _manifest(value: BackupManifest) -> tuple[BackupManifest, bytes]:
    # dataclasses.asdict deep-copies arbitrary leaves. Check exact classes,
    # immutable tuples, primitive types, and lengths before allowing it to run.
    if type(value) is not BackupManifest:
        _invalid()
    for item in (value.format, value.version, value.created_at):
        _primitive(item)
    _model(value.source, BackupSource)
    _model(value.database, BackupDatabase)
    _model(value.coverage, BackupCoverage)
    if (
        type(value.files) is not tuple
        or not 1 <= len(value.files) <= MAX_FILES
        or type(value.required_configuration) is not tuple
        or not 1 <= len(value.required_configuration) <= 2
    ):
        _invalid()
    for item in value.required_configuration:
        _primitive(item)
    for item in value.files:
        _model(item, BackupFileEntry)
    raw = json.dumps(
        asdict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", errors="strict")
    return parse_backup_manifest(raw), raw


def _sources(sources, manifest: BackupManifest, destination, budget: _Budget) -> dict:
    if not isinstance(sources, Mapping):
        _invalid()
    expected = {entry.path: entry for entry in manifest.files}
    result = {}
    identities = {id(destination)}
    for path in sources:
        budget.check()
        if type(path) is not str or path not in expected or path in result:
            _invalid()
        stream = sources[path]
        if id(stream) in identities:
            _invalid()
        identities.add(id(stream))
        result[path] = stream
    if set(result) != set(expected):
        _invalid()
    # All source lengths are checked before any destination byte is written.
    for path, stream in result.items():
        if budget.call(stream.readable) is not True or budget.call(stream.seekable) is not True:
            _invalid()
        _position(budget.call(stream.seek, 0, io.SEEK_END), expected[path].size)
        _position(budget.call(stream.tell), expected[path].size)
        _position(budget.call(stream.seek, 0, io.SEEK_SET), 0)
        _position(budget.call(stream.tell), 0)
    return result


def _info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _DOS_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.file_size = size
    info.extra = info.comment = b""
    return info


def _copy(source: BinaryIO, target, entry: BackupFileEntry, budget: _Budget) -> None:
    digest = hashlib.sha256()
    size = 0
    while True:
        # The final one-byte read must prove EOF, even if a stream lied about
        # its initial length or grew after the preflight check.
        limit = min(READ_CHUNK_BYTES, entry.size - size) if size < entry.size else 1
        block = budget.call(source.read, limit)
        if type(block) is not bytes or len(block) > limit:
            _invalid()
        budget.read += len(block)
        if budget.read > MAX_SOURCE_READ_BYTES or size + len(block) > entry.size:
            _invalid()
        size += len(block)
        _position(budget.call(source.tell), size)
        if not block:
            break
        digest.update(block)
        target.write(block)
    if size != entry.size or digest.hexdigest() != entry.sha256:
        _invalid()
    # An empty EOF result need not advance the cursor. Recheck the held stream's
    # length too, including a zero-byte source that grew while returning EOF.
    # This detects observed changes, not future mutation or snapshot consistency.
    _position(budget.call(source.seek, 0, io.SEEK_END), entry.size)
    _position(budget.call(source.tell), entry.size)


def write_backup_archive(
    destination: BinaryIO, manifest: BackupManifest, sources: Mapping[str, BinaryIO],
) -> BackupArchiveReport:
    """Write and independently recheck a staging artifact; never publish or close it.

    Each source must be a distinct stream object, also distinct from destination.
    No source name is interpreted as a host path. Invalid handcrafted dataclasses
    are rejected before any output. After *any* failure, discard staged bytes.
    """
    try:
        budget = _Budget()
        checked, raw = _manifest(manifest)
        budget.check()
        inputs = _sources(sources, checked, destination, budget)
        output = _Destination(destination, budget)
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_STORED, allowZip64=False,
        ) as archive:
            archive.comment = b""
            archive.writestr(_info("manifest.json", len(raw)), raw)
            for entry in checked.files:
                with archive.open(_info(entry.path, entry.size), "w", force_zip64=False) as member:
                    _copy(inputs[entry.path], member, entry, budget)
        output.finish()
        # This is a new, independent validation budget, not an assertion that
        # generation produced a consistent snapshot or authenticated backup.
        return validate_backup_archive(destination)
    except BackupValidationError:
        raise
    except Exception:
        raise BackupValidationError() from None
