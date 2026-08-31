"""Independent byte-level ZIP cases; no extraction or recovery is exercised."""

import hashlib
import io
import json
import stat
import struct
import sys
import traceback
import zipfile
import zlib
from dataclasses import dataclass

import pytest
from open_node.domain.backup import MAX_FILES, BackupValidationError
from open_node.services.backup_validation import validate_backup_archive

_DATABASE = "data/open-node.db"
_CANARY = "archive-secret-canary-not-for-errors"
_PAYLOADS = ((_DATABASE, "database", b"opaque bytes, deliberately not a SQLite database"),)
_EOCD = struct.Struct("<4s4H2IH")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_LOCAL = struct.Struct("<4s5H3I2H")
_LOCAL_FIELDS = {
    "version": (4, "H"),
    "flags": (6, "H"),
    "method": (8, "H"),
    "time": (10, "H"),
    "date": (12, "H"),
    "crc": (14, "I"),
    "compressed": (18, "I"),
    "size": (22, "I"),
    "name_length": (26, "H"),
    "extra_length": (28, "H"),
}
_CENTRAL_FIELDS = {
    "made_by": (4, "H"),
    "version": (6, "H"),
    "flags": (8, "H"),
    "method": (10, "H"),
    "time": (12, "H"),
    "date": (14, "H"),
    "crc": (16, "I"),
    "compressed": (20, "I"),
    "size": (24, "I"),
    "name_length": (28, "H"),
    "extra_length": (30, "H"),
    "comment_length": (32, "H"),
    "disk": (34, "H"),
    "internal": (36, "H"),
    "external": (38, "I"),
    "offset": (42, "I"),
}
_SIGNATURES = (
    pytest.param(b"PK\x03\x04", id="local"),
    pytest.param(b"PK\x01\x02", id="central"),
    pytest.param(b"PK\x05\x06", id="eocd"),
    pytest.param(b"PK\x06\x06", id="zip64-eocd"),
    pytest.param(b"PK\x06\x07", id="zip64-locator"),
    pytest.param(b"PK\x07\x08", id="descriptor-spanning"),
    pytest.param(b"PK\x05\x05", id="digital-signature"),
    pytest.param(b"PK\x06\x08", id="archive-extra"),
    pytest.param(b"PK00", id="temporary-spanning"),
)


@dataclass(frozen=True)
class _Location:
    local: int
    central: int
    name: bytes
    data: int
    size: int


class _ReadOnlySource(io.BytesIO):
    """Keep the caller's ownership and every underlying read observable."""

    def __init__(self, raw: bytes, short_read: int | None = None) -> None:
        super().__init__(raw)
        self.short_read = short_read
        self.requests: list[int] = []
        self.close_calls = 0
        self.write_calls = 0

    def read(self, size: int = -1) -> bytes:
        assert type(size) is int and 0 <= size <= 64 * 1024
        self.requests.append(size)
        if self.short_read is not None:
            size = min(size, self.short_read)
        return super().read(size)

    def write(self, _data: bytes) -> int:
        self.write_calls += 1
        raise AssertionError("The validator must not write to the source")

    def truncate(self, _size: int | None = None) -> int:
        self.write_calls += 1
        raise AssertionError("The validator must not truncate the source")

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class _UnseekableOutput(io.BytesIO):
    def seekable(self) -> bool:
        return False

    def seek(self, _offset: int, _whence: int = io.SEEK_SET) -> int:
        raise io.UnsupportedOperation("write genuine ZIP data descriptors")


def _manifest(payloads: tuple[tuple[str, str, bytes], ...]) -> bytes:
    roles = {role for _, role, _ in payloads}
    value = {
        "format": "open-node-control-plane-backup",
        "version": 1,
        "created_at": "2026-08-31T00:00:00Z",
        "source": {"git_revision": None, "image_id": None, "image_revision": None},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": {
            key: "included" if role in roles else "not_configured"
            for key, role in (
                ("certificates", "certificate_state"),
                ("external_subscriptions", "external_state"),
                ("notifications", "notification_state"),
                ("agent_identity", "agent_identity"),
            )
        },
        "required_configuration": ["deployment_settings", "subscriber_totp_key"],
        "files": [
            {"path": name, "role": role, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, role, data in payloads
        ],
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _package(
    payloads: tuple[tuple[str, str, bytes], ...] = _PAYLOADS,
    *,
    manifest: bytes | None = None,
    extra: bytes = b"",
    comment: bytes = b"",
    archive_comment: bytes = b"",
    compression: int = zipfile.ZIP_STORED,
    descriptors: bool = False,
) -> tuple[bytes, bytes]:
    manifest = _manifest(payloads) if manifest is None else manifest
    output = _UnseekableOutput() if descriptors else io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.comment = archive_comment
        for name, data in [("manifest.json", manifest), *[(p, d) for p, _, d in payloads]]:
            info = zipfile.ZipInfo(name, (2026, 8, 31, 9, 0, 0))
            info.create_system = 3
            info.create_version = info.extract_version = 20
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.extra = extra
            info.comment = comment
            info.compress_type = compression
            archive.writestr(info, data)
    return output.getvalue(), manifest


def _certificate_payloads(name: str, data: bytes = b"opaque certificate bytes"):
    return (
        *_PAYLOADS,
        ("data/certificates/vault.key", "certificate_state", b"not-a-real-key"),
        ("data/certificates/vault.initialized", "certificate_state", b"not-a-real-marker"),
        (name, "certificate_state", data),
    )


def _locations(raw: bytes) -> tuple[_Location, ...]:
    end = _EOCD.unpack_from(raw, len(raw) - _EOCD.size)
    cursor = end[6]
    result = []
    for _ in range(end[4]):
        central = _CENTRAL.unpack_from(raw, cursor)
        local = _LOCAL.unpack_from(raw, central[16])
        name = raw[cursor + _CENTRAL.size:cursor + _CENTRAL.size + central[10]]
        result.append(_Location(
            central[16], cursor, name,
            central[16] + _LOCAL.size + local[9] + local[10], central[9],
        ))
        cursor += _CENTRAL.size + central[10] + central[11] + central[12]
    assert cursor == len(raw) - _EOCD.size
    return tuple(result)


def _location(raw: bytes, name: str = _DATABASE) -> _Location:
    return next(item for item in _locations(raw) if item.name == name.encode("utf-8"))


def _patch_fields(
    raw: bytes,
    *,
    name: str = _DATABASE,
    local: dict[str, int] | None = None,
    central: dict[str, int] | None = None,
) -> bytes:
    entry = _location(raw, name)
    changed = bytearray(raw)
    for values, fields, offset in (
        (local or {}, _LOCAL_FIELDS, entry.local),
        (central or {}, _CENTRAL_FIELDS, entry.central),
    ):
        for key, value in values.items():
            relative, kind = fields[key]
            struct.pack_into("<" + kind, changed, offset + relative, value)
    return bytes(changed)


def _patch_eocd(raw: bytes, relative: int, kind: str, value: int) -> bytes:
    changed = bytearray(raw)
    struct.pack_into("<" + kind, changed, len(raw) - _EOCD.size + relative, value)
    return bytes(changed)


def _rename_same_length(raw: bytes, old: str, new: bytes, *, local_only: bool = False) -> bytes:
    entry = _location(raw, old)
    assert len(new) == len(entry.name)
    changed = bytearray(raw)
    changed[entry.local + _LOCAL.size:entry.data] = new
    if not local_only:
        start = entry.central + _CENTRAL.size
        changed[start:start + len(new)] = new
    return bytes(changed)


def _insert_gap(raw: bytes, offset: int, gap: bytes) -> bytes:
    """Relocate every header correctly, leaving only unreferenced physical bytes."""
    entries = _locations(raw)
    central_offset = _EOCD.unpack_from(raw, len(raw) - _EOCD.size)[6]
    assert offset <= central_offset
    changed = bytearray(raw[:offset] + gap + raw[offset:])
    for entry in entries:
        struct.pack_into(
            "<I", changed, entry.central + len(gap) + 42,
            entry.local + (len(gap) if entry.local >= offset else 0),
        )
    struct.pack_into("<I", changed, len(changed) - _EOCD.size + 16,
                     central_offset + len(gap))
    return bytes(changed)


def _invalid(raw: bytes) -> None:
    source = _ReadOnlySource(raw)
    with pytest.raises(BackupValidationError) as caught:
        validate_backup_archive(source)
    assert str(caught.value) == "Invalid backup package."
    assert caught.value.args == ("Invalid backup package.",)
    assert not source.closed and source.close_calls == source.write_calls == 0
    assert source.getvalue() == raw


def _verified(raw: bytes, manifest: bytes, payloads=_PAYLOADS) -> None:
    source = _ReadOnlySource(raw)
    source.seek(min(17, len(raw)))
    report = validate_backup_archive(source)
    assert report.archive_size == len(raw)
    assert report.payload_size == sum(len(data) for _, _, data in payloads)
    assert report.file_count == len(payloads)
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert report.manifest_sha256 == hashlib.sha256(manifest).hexdigest()
    actual_files = tuple(
        (item.path, item.role, item.size, item.sha256) for item in report.manifest.files
    )
    expected_files = tuple((name, role, len(data), hashlib.sha256(data).hexdigest())
                           for name, role, data in payloads)
    assert actual_files == expected_files
    assert report.structure_verified is True and report.content_hashes_verified is True
    for field in (
        "source_authentication", "database_validation", "key_validation",
        "snapshot_validation", "restore_validation",
    ):
        assert getattr(report, field) == "not_checked"
    assert report.restoration_ready is False
    assert not source.closed and source.close_calls == source.write_calls == 0
    assert source.getvalue() == raw
    assert source.requests and max(source.requests) <= 64 * 1024


def test_report_is_byte_validation_not_database_key_or_restore_approval():
    payloads = (
        *_certificate_payloads(f"data/certificates/{_CANARY}.pem"),
        ("data/external-subscriptions/vault.key", "external_state", b"not-a-key"),
        ("data/external-subscriptions/vault.initialized", "external_state", b"not-a-marker"),
        ("data/notifications/telegram.key", "notification_state", b"not-a-key"),
        ("data/notifications/telegram.initialized", "notification_state", b"not-a-marker"),
        ("secrets/agent-identity.seed", "agent_identity", b"not-a-seed"),
    )
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)
    report = validate_backup_archive(_ReadOnlySource(raw))
    assert _CANARY not in repr(report)


@pytest.mark.parametrize("short_read", [1, 7, 31, 4096])
def test_completed_seekable_input_supports_short_reads_without_taking_ownership(short_read):
    raw, _ = _package()
    source = _ReadOnlySource(raw, short_read)
    report = validate_backup_archive(source)
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.getvalue() == raw
    assert not source.closed and source.close_calls == source.write_calls == 0
    assert source.requests and all(0 <= size <= 64 * 1024 for size in source.requests)


def test_payload_is_read_in_bounded_chunks_not_as_an_entire_member():
    payloads = ((_DATABASE, "database", bytes(range(256)) * 1025),)
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


@pytest.mark.parametrize("signature", _SIGNATURES)
@pytest.mark.parametrize("placement", ["start", "middle", "end"])
def test_zip_signatures_are_ordinary_payload_bytes(signature, placement):
    parts = {
        "start": signature + b"\x00payload-end",
        "middle": b"payload-start\x00" + signature + b"\xffpayload-end",
        "end": b"payload-start\x00" + signature,
    }
    payloads = ((_DATABASE, "database", parts[placement]),)
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


def test_a_complete_nested_zip_is_opaque_payload_and_not_recursively_inspected():
    nested, _ = _package()
    payloads = ((_DATABASE, "database", nested),)
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


@pytest.mark.parametrize("name", [
    "data/certificates/证书-🚀.pem",
    "data/certificates/" + "界" * 85,
    "data/certificates/" + "/".join(["a" * 255, "b" * 255, "c" * 255, "d" * 238]),
])
def test_canonical_utf8_and_exact_path_byte_boundaries_are_accepted(name):
    payloads = _certificate_payloads(name)
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


@pytest.mark.parametrize("name", [
    "data/certificates/" + "界" * 86,
    "data/certificates/" + "x" * 256,
    "data/certificates/" + "/".join(["a" * 255, "b" * 255, "c" * 255, "d" * 239]),
    "data/certificates/e\u0301.pem",
    "data/certificates/\ufeffhidden.pem",
    "data/certificates/../escape.pem",
    "data/certificates//alias.pem",
    "data/certificates/./alias.pem",
    "data/certificates/space .",
    "data/certificates/trailing ",
    "data/certificates/back\\slash.pem",
    "data/certificates/drive:alias.pem",
])
def test_noncanonical_paths_are_rejected_not_normalized(name):
    raw, _ = _package(_certificate_payloads(name))
    _invalid(raw)


@pytest.mark.parametrize("creator", [0, 3])
@pytest.mark.parametrize("made_version", [10, 20])
@pytest.mark.parametrize("extract_version", [10, 20])
@pytest.mark.parametrize("kind", [0, stat.S_IFREG])
@pytest.mark.parametrize("dos_archive", [0, 0x20])
def test_allowed_creator_versions_and_regular_file_representations(
    creator, made_version, extract_version, kind, dos_archive,
):
    raw, manifest = _package()
    raw = _patch_fields(
        raw, local={"version": extract_version},
        central={"made_by": (creator << 8) | made_version, "version": extract_version,
                 "external": ((kind | 0o600) << 16) | dos_archive},
    )
    _verified(raw, manifest)


def test_ascii_names_with_utf8_flag_and_zero_external_attributes_are_accepted():
    raw, manifest = _package()
    raw = _patch_fields(raw, local={"flags": 0x800},
                        central={"flags": 0x800, "external": 0})
    _verified(raw, manifest)


def test_empty_payload_has_exact_zero_length_and_crc_but_no_database_approval():
    payloads = ((_DATABASE, "database", b""),)
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


def test_full_4096_payload_entry_boundary_is_accepted():
    payloads = (
        *_PAYLOADS,
        ("data/certificates/vault.key", "certificate_state", b"key"),
        ("data/certificates/vault.initialized", "certificate_state", b"marker"),
        *((f"data/certificates/item-{index:04d}.bin", "certificate_state", b"x")
          for index in range(MAX_FILES - 3)),
    )
    raw, manifest = _package(payloads)
    _verified(raw, manifest, payloads)


@pytest.mark.parametrize("raw", [b"", b"PK", b"PK\x03\x04" + b"\x00" * 26,
                                     _EOCD.pack(b"PK\x05\x06", 0, 0, 0, 0, 0, 0, 0)])
def test_empty_truncated_or_header_only_packages_are_rejected(raw):
    _invalid(raw)


@pytest.mark.parametrize("remove", [1, 4, 21, 22, 23, 60])
def test_archive_truncation_is_not_accepted_by_an_eocd_search_fallback(remove):
    raw, _ = _package()
    _invalid(raw[:-remove])


@pytest.mark.parametrize(("offset", "kind", "value"), [
    (4, "H", 1), (6, "H", 1), (8, "H", 0), (10, "H", 0),
    (8, "H", 0xFFFF), (10, "H", 0xFFFF),
    (12, "I", 0), (12, "I", 0xFFFFFFFF), (16, "I", 0xFFFFFFFF), (20, "H", 1),
])
def test_eocd_disk_count_size_offset_and_comment_fields_are_not_trusted(offset, kind, value):
    raw, _ = _package()
    _invalid(_patch_eocd(raw, offset, kind, value))


@pytest.mark.parametrize("count", [0, 1, 2, 6, MAX_FILES + 2, 0xFFFF])
def test_actual_central_entry_count_must_equal_both_eocd_counts(count):
    raw, _ = _package(_certificate_payloads("data/certificates/leaf.pem"))
    raw = _patch_eocd(_patch_eocd(raw, 8, "H", count), 10, "H", count)
    _invalid(raw)


@pytest.mark.parametrize("where", ["prefix", "between-local-entries", "before-central"])
def test_correctly_relocated_headers_cannot_hide_unreferenced_garbage(where):
    raw, _ = _package()
    offsets = {"prefix": 0, "between-local-entries": _location(raw).local,
               "before-central": _EOCD.unpack_from(raw, len(raw) - _EOCD.size)[6]}
    _invalid(_insert_gap(raw, offsets[where], b"unreferenced-" + _CANARY.encode()))


@pytest.mark.parametrize("suffix", [b"trailing-junk", b"PK\x05\x06" + b"\x00" * 18])
def test_bytes_after_the_real_eocd_are_rejected(suffix):
    raw, _ = _package()
    _invalid(raw + suffix)


def test_appending_a_second_copy_of_the_valid_eocd_does_not_hide_the_first():
    raw, _ = _package()
    _invalid(raw + raw[-_EOCD.size:])


@pytest.mark.parametrize("record", [b"arbitrary", b"PK\x05\x05\x00\x00",
                                    b"PK\x06\x08\x00\x00\x00\x00"])
def test_non_entry_records_inside_the_declared_central_directory_are_rejected(record):
    raw, _ = _package()
    old_length = _EOCD.unpack_from(raw, len(raw) - _EOCD.size)[5]
    raw = raw[:-_EOCD.size] + record + raw[-_EOCD.size:]
    _invalid(_patch_eocd(raw, 12, "I", old_length + len(record)))


def test_small_zip64_eocd_and_locator_are_forbidden_even_without_legacy_sentinels():
    raw, _ = _package()
    end_offset = len(raw) - _EOCD.size
    end = _EOCD.unpack_from(raw, end_offset)
    record = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45,
                         0, 0, end[3], end[4], end[5], end[6])
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end_offset, 1)
    _invalid(raw[:end_offset] + record + locator + raw[end_offset:])


def test_zip64_locator_probe_cannot_override_a_small_eocd_allocation_budget():
    raw, _ = _package()
    changed = bytearray(raw)
    start = len(raw) - _EOCD.size - 20
    changed[start:start + 20] = struct.pack("<4sIQI", b"PK\x06\x07", 0, 0, 1)
    _invalid(bytes(changed))


@pytest.mark.parametrize("options", [
    {"extra": b"\xff\xff\x00\x00"},
    {"comment": _CANARY.encode()},
    {"archive_comment": _CANARY.encode()},
    {"compression": zipfile.ZIP_DEFLATED},
    {"descriptors": True},
])
def test_genuine_zip_features_outside_the_v1_profile_are_rejected(options):
    raw, _ = _package(**options)
    _invalid(raw)


def test_genuine_small_zip64_local_headers_are_not_enabled_by_allowzip64_false_on_read():
    manifest = _manifest(_PAYLOADS)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in (("manifest.json", manifest), (_DATABASE, _PAYLOADS[0][2])):
            info = zipfile.ZipInfo(name, (2026, 8, 31, 9, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            with archive.open(info, "w", force_zip64=True) as member:
                member.write(data)
    _invalid(output.getvalue())


@pytest.mark.parametrize("corruption", ["eocd", "central", "local", "nul", "gap", "zip64"])
def test_raw_preflight_rejects_before_the_real_zipfile_constructor(corruption):
    raw, _ = _package()
    malformed = {
        "eocd": lambda: _patch_eocd(raw, 12, "I", 0xFFFFFFFF),
        "central": lambda: _patch_fields(raw, central={"comment_length": 1}),
        "local": lambda: _patch_fields(raw, local={"flags": 0x800}),
        "nul": lambda: _rename_same_length(raw, _DATABASE, b"data\x00open-node.db"),
        "gap": lambda: _insert_gap(raw, 0, b"prefix"),
        "zip64": lambda: _patch_fields(raw, central={"version": 45}),
    }[corruption]()
    constructor = zipfile.ZipFile.__init__.__code__
    previous_profile = sys.getprofile()
    calls = []

    def observe(frame, event, argument):
        if event == "call" and frame.f_code is constructor:
            calls.append(event)
        if previous_profile is not None:
            previous_profile(frame, event, argument)

    # Observe Python calls, without replacing ZipFile or its implementation.
    sys.setprofile(observe)
    try:
        report = validate_backup_archive(_ReadOnlySource(raw))
        assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
        assert calls, "The positive control must actually instantiate stdlib ZipFile"
        calls.clear()
        _invalid(malformed)
        assert not calls, "Untrusted structural metadata reached the ZipFile constructor"
    finally:
        sys.setprofile(previous_profile)


@pytest.mark.parametrize(("field", "value"), [
    ("version", 10), ("version", 0x114), ("flags", 0x800), ("method", 8),
    ("time", 10 << 11), ("date", ((2026 - 1980) << 9) | (8 << 5) | 30),
    ("crc", 0), ("compressed", 1), ("size", 1),
    ("name_length", 1), ("extra_length", 1),
])
@pytest.mark.parametrize("header", ["local", "central"])
def test_each_shared_local_central_field_must_match(field, value, header):
    raw, _ = _package()
    options = {header: {field: value}}
    _invalid(_patch_fields(raw, **options))


@pytest.mark.parametrize("header", ["local", "central"])
def test_each_header_requires_its_own_exact_magic(header):
    raw, _ = _package()
    entry = _location(raw)
    changed = bytearray(raw)
    offset = entry.local if header == "local" else entry.central
    changed[offset:offset + 4] = b"PK\x00\x00"
    _invalid(bytes(changed))


@pytest.mark.parametrize("flag", [1 << bit for bit in range(16) if bit != 11])
def test_even_matching_reserved_encryption_and_descriptor_flags_are_forbidden(flag):
    raw, _ = _package()
    _invalid(_patch_fields(raw, local={"flags": flag}, central={"flags": flag}))


@pytest.mark.parametrize("method", [1, 8, 9, 12, 14, 93, 99, 65535])
def test_matching_nonstored_methods_are_rejected_before_payload_decoding(method):
    raw, _ = _package()
    _invalid(_patch_fields(raw, local={"method": method}, central={"method": method}))


@pytest.mark.parametrize(("field", "value"), [
    ("made_by", (10 << 8) | 20), ("made_by", (3 << 8) | 45),
    ("made_by", (3 << 8)), ("version", 45), ("version", 0x114),
    ("name_length", 0), ("name_length", 65535),
    ("extra_length", 65535), ("comment_length", 65535), ("disk", 1), ("disk", 65535),
    ("compressed", 0xFFFFFFFF), ("size", 0xFFFFFFFF), ("offset", 0xFFFFFFFF),
    ("internal", 1), ("internal", 0x8000),
])
def test_central_metadata_cannot_hide_truncation_zip64_or_unknown_semantics(field, value):
    raw, _ = _package()
    _invalid(_patch_fields(raw, central={field: value}))


@pytest.mark.parametrize("mode", [
    stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o700, stat.S_IFIFO | 0o600,
    stat.S_IFCHR | 0o600, stat.S_IFBLK | 0o600, stat.S_IFSOCK | 0o600,
    stat.S_IFREG | stat.S_ISUID | 0o700,
    stat.S_IFREG | stat.S_ISGID | 0o700,
    stat.S_IFREG | stat.S_ISVTX | 0o700,
])
def test_special_or_privileged_unix_types_are_rejected_even_without_a_slash_suffix(mode):
    raw, _ = _package()
    _invalid(_patch_fields(raw, central={"external": mode << 16}))


@pytest.mark.parametrize("attributes", [0x08, 0x10, 0x8000, 0x21])
def test_dos_volume_directory_and_unknown_attributes_are_rejected(attributes):
    raw, _ = _package()
    _invalid(_patch_fields(raw, central={"external": (stat.S_IFREG << 16) | attributes}))


@pytest.mark.parametrize(("field", "value"), [
    ("date", 0), ("date", (46 << 9) | (13 << 5) | 1),
    ("date", (46 << 9) | (2 << 5) | 30),
    ("date", (45 << 9) | (2 << 5) | 29),
    ("time", 24 << 11), ("time", 60 << 5), ("time", 31),
])
def test_matching_but_impossible_dos_timestamps_are_rejected(field, value):
    raw, _ = _package()
    _invalid(_patch_fields(raw, local={field: value}, central={field: value}))


@pytest.mark.parametrize("new_name", [
    b"data\x00open-node.db", b"data/\xffpen-node.db", b"data/\x01pen-node.db",
    b"data\\open-node.db", b"/ata/open-node.db",
])
def test_raw_names_are_rejected_before_stdlib_nul_aliasing_or_fallback_decoding(new_name):
    raw, _ = _package()
    _invalid(_rename_same_length(raw, _DATABASE, new_name))


def test_utf8_flag_does_not_make_invalid_utf8_or_surrogate_sequences_acceptable():
    raw, _ = _package()
    raw = _patch_fields(raw, local={"flags": 0x800}, central={"flags": 0x800})
    for prefix in (b"\xff", b"\xc0\xaf", b"\xed\xa0\x80"):
        name = prefix + _DATABASE.encode()[len(prefix):]
        _invalid(_rename_same_length(raw, _DATABASE, name))


def test_local_name_bytes_cannot_differ_even_when_lengths_match():
    raw, _ = _package()
    _invalid(_rename_same_length(raw, _DATABASE, b"data/open-node.dB", local_only=True))


@pytest.mark.parametrize("duplicate", ["payload", "manifest"])
def test_real_duplicate_zip_entries_cannot_be_collapsed_by_name_lookup(duplicate):
    raw, manifest = _package()
    output = io.BytesIO(raw)
    with zipfile.ZipFile(output, "a", allowZip64=False) as archive:
        name = _DATABASE if duplicate == "payload" else "manifest.json"
        data = _PAYLOADS[0][2] if duplicate == "payload" else manifest
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(name, data)
    _invalid(output.getvalue())


def test_nul_suffix_cannot_alias_a_distinct_canonical_member():
    first = "data/certificates/a"
    second = first + "X" + _CANARY
    payloads = (*_certificate_payloads(first), (second, "certificate_state", b"other bytes"))
    raw, _ = _package(payloads)
    _invalid(_rename_same_length(raw, second, (first + "\x00" + _CANARY).encode()))


@pytest.mark.parametrize("offset", [0, 1])
def test_duplicate_or_overlapping_local_header_offsets_are_rejected(offset):
    raw, _ = _package()
    _invalid(_patch_fields(raw, central={"offset": offset}))


@pytest.mark.parametrize("keep_compressed_size", [False, True])
def test_stored_prefix_crc_and_manifest_hash_cannot_hide_extra_physical_bytes(keep_compressed_size):
    visible = b"visible prefix"
    actual = visible + b"hidden suffix " + _CANARY.encode()
    declarations = ((_DATABASE, "database", visible),)
    raw, _ = _package(((_DATABASE, "database", actual),), manifest=_manifest(declarations))
    fields = {"crc": zlib.crc32(visible), "size": len(visible)}
    if not keep_compressed_size:
        fields["compressed"] = len(visible)
    _invalid(_patch_fields(raw, local=fields, central=fields))


def test_stored_eof_must_not_satisfy_a_larger_declared_uncompressed_size():
    raw, _ = _package()
    size = len(_PAYLOADS[0][2]) + 1
    _invalid(_patch_fields(raw, local={"size": size}, central={"size": size}))


@pytest.mark.parametrize("repair_crc", [False, True])
def test_actual_payload_crc_and_manifest_sha_are_both_verified(repair_crc):
    raw, _ = _package()
    entry = _location(raw)
    changed = bytearray(raw)
    changed[entry.data] ^= 1
    raw = bytes(changed)
    if repair_crc:
        crc = zlib.crc32(raw[entry.data:entry.data + entry.size])
        raw = _patch_fields(raw, local={"crc": crc}, central={"crc": crc})
    _invalid(raw)


def test_zero_length_member_still_requires_the_crc_of_empty_bytes():
    raw, _ = _package(((_DATABASE, "database", b""),))
    _invalid(_patch_fields(raw, local={"crc": 1}, central={"crc": 1}))


def test_unlisted_payloads_and_declared_but_missing_payloads_are_rejected():
    complete = _certificate_payloads("data/certificates/leaf.pem")
    extra, _ = _package(complete, manifest=_manifest(_PAYLOADS))
    missing, _ = _package(_PAYLOADS, manifest=_manifest(complete))
    _invalid(extra)
    _invalid(missing)


def test_central_directory_order_may_differ_from_contiguous_physical_member_order():
    raw, manifest = _package()
    entries = _locations(raw)
    central = b"".join(raw[item.central:item.central + _CENTRAL.size + len(item.name)]
                       for item in reversed(entries))
    offset = _EOCD.unpack_from(raw, len(raw) - _EOCD.size)[6]
    reordered = raw[:offset] + central + raw[-_EOCD.size:]
    _verified(reordered, manifest)


def test_input_io_errors_have_only_the_fixed_safe_message_and_no_logs(caplog, capsys):
    class FailingRead(_ReadOnlySource):
        def read(self, _size: int = -1) -> bytes:
            raise OSError(_CANARY)

    raw, _ = _package()
    source = FailingRead(raw)
    with pytest.raises(BackupValidationError) as caught:
        validate_backup_archive(source)
    assert str(caught.value) == "Invalid backup package."
    assert _CANARY not in "".join(traceback.format_exception(caught.value))
    assert _CANARY not in caplog.text
    captured = capsys.readouterr()
    assert _CANARY not in captured.out + captured.err
    assert not source.closed and source.close_calls == source.write_calls == 0
