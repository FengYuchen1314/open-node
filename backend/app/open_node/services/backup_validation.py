"""Read-only validation of the deliberately small, stored-ZIP backup v1 format.

The caller owns a completed, private, exclusively held seekable binary input.
This module never extracts files, opens a database, or starts application services.
It may move the input cursor, but never writes to or closes the caller's stream.
Its between-operation deadline cannot interrupt a blocking underlying read; network
streams and concurrently mutable files do not satisfy the input contract.
"""

import hashlib
import io
import stat
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import BinaryIO, Literal

from open_node.domain.backup import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_MANIFEST_BYTES,
    MAX_PATH_BYTES,
    MAX_TOTAL_FILE_BYTES,
    BackupManifest,
    BackupValidationError,
    parse_backup_manifest,
    validate_backup_path,
)

MAX_ARCHIVE_BYTES = MAX_TOTAL_FILE_BYTES + 16 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = (MAX_FILES + 1) * (46 + MAX_PATH_BYTES)
READ_CHUNK_BYTES = 64 * 1024
MAX_READ_BYTES = 2 * MAX_ARCHIVE_BYTES
# Enough for the maximum payload at normal chunk sizes plus all 4097 headers;
# still bound hostile streams that return one byte per underlying operation.
MAX_IO_OPERATIONS = 524288
MAX_VALIDATION_SECONDS = 30.0

_EOCD = struct.Struct("<4s4H2IH")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_LOCAL = struct.Struct("<4s5H3I2H")
_MANIFEST_NAME = "manifest.json"
_VERSIONS = frozenset({10, 20})


@dataclass(frozen=True, slots=True)
class BackupArchiveReport:
    """Byte checks are not proof of source identity, consistency, or recoverability.

    The digest describes the bytes checked during this call. Exclusive ownership
    is required; no claim is made about an independently modified input later.
    """

    manifest: BackupManifest = field(repr=False)
    archive_size: int
    payload_size: int
    file_count: int
    checked_archive_sha256: str
    manifest_sha256: str
    structure_verified: Literal[True] = field(default=True, init=False)
    content_hashes_verified: Literal[True] = field(default=True, init=False)
    source_authentication: Literal["not_checked"] = field(default="not_checked", init=False)
    database_validation: Literal["not_checked"] = field(default="not_checked", init=False)
    key_validation: Literal["not_checked"] = field(default="not_checked", init=False)
    snapshot_validation: Literal["not_checked"] = field(default="not_checked", init=False)
    restore_validation: Literal["not_checked"] = field(default="not_checked", init=False)
    restoration_ready: Literal[False] = field(default=False, init=False)


def _invalid() -> None:
    raise BackupValidationError() from None


class _BoundedReader:
    """Bound allocations and actual underlying I/O even inside stdlib zipfile.

    Filling legal short reads here also supplies the buffered-file semantics
    zipfile expects. Every underlying read receives an explicit finite bound.
    """

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._deadline = time.monotonic() + MAX_VALIDATION_SECONDS
        self._operations = 0
        self._read_bytes = 0
        self._check()
        if source.seekable() is not True:
            _invalid()
        end = source.seek(0, io.SEEK_END)
        if type(end) is not int or not _EOCD.size <= end <= MAX_ARCHIVE_BYTES:
            _invalid()
        self.size = end
        self._position = end
        if source.tell() != end:
            _invalid()
        self._check()

    def _check(self) -> None:
        self._operations += 1
        if self._operations > MAX_IO_OPERATIONS or time.monotonic() > self._deadline:
            _invalid()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._check()
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._check()
        if type(offset) is not int:
            _invalid()
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._position + offset
        elif whence == io.SEEK_END:
            target = self.size + offset
        else:
            _invalid()
        if not 0 <= target <= self.size:
            _invalid()
        actual = self._source.seek(target, io.SEEK_SET)
        if type(actual) is not int or actual != target or self._source.tell() != target:
            _invalid()
        self._position = target
        self._check()
        return target

    def read(self, size: int = -1) -> bytes:
        self._check()
        if type(size) is not int or size < -1:
            _invalid()
        remaining = self.size - self._position
        requested = remaining if size == -1 else min(size, remaining)
        if requested > MAX_CENTRAL_DIRECTORY_BYTES:
            _invalid()
        result = bytearray()
        while len(result) < requested:
            self._check()
            limit = min(READ_CHUNK_BYTES, requested - len(result))
            if self._read_bytes + limit > MAX_READ_BYTES:
                _invalid()
            block = self._source.read(limit)
            self._check()
            if type(block) is not bytes or len(block) > limit:
                _invalid()
            if not block:
                break
            self._read_bytes += len(block)
            self._position += len(block)
            if self._source.tell() != self._position:
                _invalid()
            result.extend(block)
        return bytes(result)

    def read_exact(self, offset: int, size: int) -> bytes:
        self.seek(offset)
        data = self.read(size)
        if len(data) != size:
            _invalid()
        return data

    def finish(self) -> None:
        # Detect observed length changes on the same held object. This is not a
        # lock or proof against a writer changing previously checked bytes.
        self._check()
        end = self._source.seek(0, io.SEEK_END)
        if type(end) is not int or end != self.size or self._source.tell() != end:
            _invalid()
        self._position = end
        self._check()


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    raw_name: bytes
    made_by: int
    version: int
    flags: int
    method: int
    dos_time: int
    dos_date: int
    crc: int
    size: int
    internal_attributes: int
    external_attributes: int
    offset: int

    @property
    def date_time(self) -> tuple[int, int, int, int, int, int]:
        return (
            (self.dos_date >> 9) + 1980,
            (self.dos_date >> 5) & 15,
            self.dos_date & 31,
            self.dos_time >> 11,
            (self.dos_time >> 5) & 63,
            (self.dos_time & 31) * 2,
        )


def _check_attributes(made_by: int, internal: int, external: int) -> None:
    if made_by >> 8 not in {0, 3} or made_by & 255 not in _VERSIONS or internal != 0:
        _invalid()
    mode = external >> 16
    # Python's ordinary writestr defaults contain permission bits but no S_IFREG.
    # Accept that representation, not links, special types, or privileged modes.
    if stat.S_IFMT(mode) not in {0, stat.S_IFREG} or mode & (
        stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    ):
        _invalid()
    if external & 0xFFFF not in {0, 0x20}:
        _invalid()


def _directory(reader: _BoundedReader) -> tuple[tuple[_Entry, ...], int, bytes, bytes]:
    end_offset = reader.size - _EOCD.size
    end = reader.read_exact(end_offset, _EOCD.size)
    magic, disk, start_disk, disk_count, count, length, offset, comment = _EOCD.unpack(end)
    if (
        magic != b"PK\x05\x06"
        or disk != 0
        or start_disk != 0
        or disk_count != count
        or not 2 <= count <= MAX_FILES + 1
        or comment != 0
        or not _CENTRAL.size * count <= length <= MAX_CENTRAL_DIRECTORY_BYTES
        or offset == 0xFFFFFFFF
        or offset + length != end_offset
    ):
        _invalid()
    # CPython probes this location even without ZIP64 sentinels in the EOCD.
    if end_offset >= 20 and reader.read_exact(end_offset - 20, 4) == b"PK\x06\x07":
        _invalid()
    raw = reader.read_exact(offset, length)
    entries: list[_Entry] = []
    names: set[str] = set()
    cursor = 0
    payload_size = 0
    while cursor < len(raw):
        if len(entries) >= count or len(raw) - cursor < _CENTRAL.size:
            _invalid()
        (
            signature, made_by, version, flags, method, dos_time, dos_date, crc,
            compressed, size, name_length, extra_length, comment_length, entry_disk,
            internal, external, header_offset,
        ) = _CENTRAL.unpack_from(raw, cursor)
        cursor += _CENTRAL.size
        if (
            signature != b"PK\x01\x02"
            or version not in _VERSIONS
            or flags not in {0, 0x800}
            or method != zipfile.ZIP_STORED
            or compressed != size
            or size > MAX_FILE_BYTES
            or not 1 <= name_length <= MAX_PATH_BYTES
            or extra_length != 0
            or comment_length != 0
            or entry_disk != 0
            or cursor + name_length > len(raw)
            or header_offset >= offset
        ):
            _invalid()
        _check_attributes(made_by, internal, external)
        raw_name = raw[cursor:cursor + name_length]
        cursor += name_length
        name = validate_backup_path(raw_name.decode("utf-8" if flags & 0x800 else "ascii"))
        if name in names:
            _invalid()
        names.add(name)
        if name == _MANIFEST_NAME:
            if not 1 <= size <= MAX_MANIFEST_BYTES:
                _invalid()
        else:
            payload_size += size
            if payload_size > MAX_TOTAL_FILE_BYTES:
                _invalid()
        entry = _Entry(
            name, raw_name, made_by, version, flags, method, dos_time, dos_date, crc, size,
            internal, external, header_offset,
        )
        datetime(*entry.date_time)  # Reject impossible DOS dates, not just inconsistent copies.
        entries.append(entry)
    if len(entries) != count or _MANIFEST_NAME not in names:
        _invalid()
    return tuple(entries), offset, raw, end


def _local_headers(
    reader: _BoundedReader, entries: tuple[_Entry, ...], central_offset: int,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    cursor = 0
    for entry in sorted(entries, key=lambda item: item.offset):
        if entry.offset != cursor:
            _invalid()
        raw = reader.read_exact(cursor, _LOCAL.size + len(entry.raw_name))
        (
            signature, version, flags, method, dos_time, dos_date, crc, compressed, size,
            name_length, extra_length,
        ) = _LOCAL.unpack_from(raw)
        if (
            signature != b"PK\x03\x04"
            or (version, flags, method, dos_time, dos_date, crc, compressed, size,
                name_length, extra_length) != (
                    entry.version, entry.flags, entry.method, entry.dos_time, entry.dos_date,
                    entry.crc, entry.size, entry.size, len(entry.raw_name), 0,
                )
            or raw[_LOCAL.size:] != entry.raw_name
        ):
            _invalid()
        cursor += len(raw) + entry.size
        if cursor > central_offset:
            _invalid()
        result[entry.name] = raw
    if cursor != central_offset:
        _invalid()
    return result


def _zip_infos(archive: zipfile.ZipFile, entries: tuple[_Entry, ...]) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) != len(entries) or archive.comment:
        _invalid()
    result = {}
    for info, entry in zip(infos, entries, strict=True):
        if (
            info.filename != entry.name
            or info.orig_filename != entry.name
            or info.header_offset != entry.offset
            or info.file_size != entry.size
            or info.compress_size != entry.size
            or info.CRC != entry.crc
            or info.flag_bits != entry.flags
            or info.compress_type != entry.method
            or info.create_system != entry.made_by >> 8
            or info.create_version != entry.made_by & 255
            or info.extract_version != entry.version
            or info.reserved != 0
            or info.volume != 0
            or info.internal_attr != entry.internal_attributes
            or info.external_attr != entry.external_attributes
            or info.date_time != entry.date_time
            or info.extra
            or info.comment
            or info.is_dir()
        ):
            _invalid()
        result[entry.name] = info
    return result


def _manifest_bytes(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    data = bytearray()
    with archive.open(info, "r") as member:
        while block := member.read(READ_CHUNK_BYTES):
            data.extend(block)
            if len(data) > info.file_size or len(data) > MAX_MANIFEST_BYTES:
                _invalid()
    if len(data) != info.file_size:
        _invalid()
    return bytes(data)


def _validate(source: BinaryIO) -> BackupArchiveReport:
    reader = _BoundedReader(source)
    entries, central_offset, central, end = _directory(reader)
    headers = _local_headers(reader, entries, central_offset)
    with zipfile.ZipFile(reader, "r", allowZip64=False) as archive:
        infos = _zip_infos(archive, entries)
        raw_manifest = _manifest_bytes(archive, infos[_MANIFEST_NAME])
        manifest = parse_backup_manifest(raw_manifest)
        manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
        expected = {entry.path: entry for entry in manifest.files}
        if set(expected) != set(infos) - {_MANIFEST_NAME}:
            _invalid()
        if any(infos[path].file_size != entry.size for path, entry in expected.items()):
            _invalid()
        archive_hash = hashlib.sha256()
        payload_size = 0
        for entry in sorted(entries, key=lambda item: item.offset):
            header = reader.read_exact(entry.offset, len(headers[entry.name]))
            if header != headers[entry.name]:
                _invalid()
            archive_hash.update(header)
            file_hash = hashlib.sha256()
            size = crc = 0
            with archive.open(infos[entry.name], "r") as member:
                while block := member.read(READ_CHUNK_BYTES):
                    size += len(block)
                    if size > entry.size:
                        _invalid()
                    file_hash.update(block)
                    archive_hash.update(block)
                    crc = zlib.crc32(block, crc)
            digest = (
                manifest_digest if entry.name == _MANIFEST_NAME else expected[entry.name].sha256
            )
            if size != entry.size or crc != entry.crc or file_hash.hexdigest() != digest:
                _invalid()
            if entry.name != _MANIFEST_NAME:
                payload_size += size
    if reader.read_exact(central_offset, len(central)) != central:
        _invalid()
    if reader.read_exact(reader.size - len(end), len(end)) != end:
        _invalid()
    archive_hash.update(central)
    archive_hash.update(end)
    reader.finish()
    return BackupArchiveReport(
        manifest=manifest,
        archive_size=reader.size,
        payload_size=payload_size,
        file_count=len(expected),
        checked_archive_sha256=archive_hash.hexdigest(),
        manifest_sha256=manifest_digest,
    )


def validate_backup_archive(source: BinaryIO) -> BackupArchiveReport:
    """Check the whole v1 stored ZIP, without trusting zipfile's permissive parser.

    All rejected-input errors have one safe message. No file content, member name,
    host path, decoder diagnostic, or secrets are logged or included in errors.
    This is not a restoration approval, schema migration, or authenticated import.
    """
    try:
        return _validate(source)
    except BackupValidationError:
        raise
    except (
        OSError, EOFError, ValueError, TypeError, AttributeError, OverflowError,
        RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile, struct.error,
    ):
        raise BackupValidationError() from None
