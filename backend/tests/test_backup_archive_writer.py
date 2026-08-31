"""Only completed private staging streams are supported; no snapshot/publication claims."""

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import asdict, replace
from types import MappingProxyType

import pytest
from open_node.domain.backup import MAX_FILES, MAX_PATH_BYTES, BackupValidationError
from open_node.domain.backup import parse_backup_manifest as parse_manifest
from open_node.services import backup_archive as writer
from open_node.services.backup_archive import write_backup_archive
from open_node.services.backup_validation import validate_backup_archive


def fixture(payload=b"opaque database bytes", *, full=False):
    payloads = {"data/open-node.db": payload}
    coverage = dict.fromkeys(
        ("certificates", "external_subscriptions", "notifications", "agent_identity"), "unknown"
    )
    if full:
        coverage = dict.fromkeys(coverage, "included")
        payloads.update({
            "data/certificates/vault.key": b"opaque certificate key",
            "data/certificates/vault.initialized": b"opaque certificate fence",
            "data/certificates/账户📦/café.pem": b"opaque certificate",
            "data/external-subscriptions/vault.key": b"opaque external key",
            "data/external-subscriptions/vault.initialized": b"opaque external fence",
            "data/notifications/telegram.key": b"opaque notification key",
            "data/notifications/telegram.initialized": b"opaque notification fence",
            "secrets/agent-identity.seed": b"not an actual identity",
        })
    roles = {
        "data/certificates/": "certificate_state",
        "data/external-subscriptions/": "external_state",
        "data/notifications/": "notification_state", "secrets/": "agent_identity",
    }
    entries = []
    for path, content in payloads.items():
        role = next((role for prefix, role in roles.items() if path.startswith(prefix)), "database")
        entries.append({"path": path, "role": role, "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()})
    manifest = parse_manifest(json.dumps({
        "format": "open-node-control-plane-backup", "version": 1,
        "created_at": "0001-01-01T00:00:00Z",
        "source": {"git_revision": None, "image_id": None, "image_revision": None},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": coverage, "required_configuration": ["deployment_settings"], "files": entries,
    }).encode())
    return manifest, payloads


def streams(payloads, factory=io.BytesIO):
    return {path: factory(content) for path, content in payloads.items()}


def rejected(destination, manifest, sources):
    with pytest.raises(BackupValidationError) as caught:
        write_backup_archive(destination, manifest, sources)
    assert str(caught.value) == "Invalid backup package."
    assert repr(caught.value) == "BackupValidationError('Invalid backup package.')"
    assert not destination.closed
    for source in sources.values() if isinstance(sources, dict) else ():
        if isinstance(source, io.IOBase):
            assert not source.closed


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("payload", [b"", b"not a database", b"abc\x00" * 40000])
def test_roundtrip_all_roles_bytes_metadata_and_not_checked(full, payload):
    manifest, payloads = fixture(payload, full=full)
    inputs, destination = streams(payloads), io.BytesIO()
    for source in inputs.values():
        source.seek(len(source.getvalue()))
    report = write_backup_archive(destination, manifest, MappingProxyType(inputs))
    data = destination.getvalue()
    assert report.manifest == manifest
    assert report.archive_size == len(data)
    assert report.payload_size == sum(map(len, payloads.values()))
    assert report.file_count == len(payloads)
    assert report.checked_archive_sha256 == hashlib.sha256(data).hexdigest()
    assert report.structure_verified and report.content_hashes_verified
    assert not report.restoration_ready
    for name in ("source_authentication", "database_validation", "key_validation",
                 "snapshot_validation", "restore_validation"):
        assert getattr(report, name) == "not_checked"
    canonical = json.dumps(
        asdict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["manifest.json", *payloads]
        assert archive.comment == b""
        assert archive.read("manifest.json") == canonical
        for info in archive.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.compress_size == info.file_size
            assert info.extra == info.comment == b""
            assert info.flag_bits in {0, 0x800}
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            assert info.external_attr >> 16 == stat.S_IFREG | 0o600
        for path, content in payloads.items():
            assert archive.read(path) == content
    assert report.manifest_sha256 == hashlib.sha256(canonical).hexdigest()
    assert not destination.closed and all(not stream.closed for stream in inputs.values())


def test_fixed_dos_time_is_independent_of_manifest_year_and_deterministic():
    manifest, payloads = fixture()
    manifest = replace(manifest, created_at="9999-12-31T23:59:59Z")
    first, second = io.BytesIO(), io.BytesIO()
    write_backup_archive(first, manifest, streams(payloads))
    write_backup_archive(second, manifest, streams(payloads))
    assert first.getvalue() == second.getvalue()
    with zipfile.ZipFile(first) as archive:
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())


def test_full_4096_entry_boundary_uses_only_empty_payload_streams():
    manifest, payloads = fixture(b"", full=True)
    empty_digest = hashlib.sha256(b"").hexdigest()
    payloads = dict.fromkeys(payloads, b"")
    entries = [replace(entry, size=0, sha256=empty_digest) for entry in manifest.files]
    for index in range(MAX_FILES - len(payloads)):
        path = f"data/certificates/empty-{index}"
        payloads[path] = b""
        entries.append(replace(entries[1], path=path))
    manifest = replace(manifest, files=tuple(entries))
    destination = io.BytesIO()
    report = write_backup_archive(destination, manifest, streams(payloads))
    assert report.file_count == MAX_FILES
    assert report.payload_size == sum(map(len, payloads.values())) == 0
    checked = validate_backup_archive(destination)
    assert checked.checked_archive_sha256 == report.checked_archive_sha256


class ShortIO(io.BytesIO):
    def __init__(self, value=b"", *, chunk=7):
        super().__init__(value)
        self.chunk = chunk
        self.read_sizes = []
        self.write_sizes = []

    def read(self, size=-1):
        assert 0 <= size <= 64 * 1024
        self.read_sizes.append(size)
        return super().read(min(size, self.chunk))

    def write(self, value):
        assert len(value) <= 64 * 1024
        self.write_sizes.append(len(value))
        return super().write(value[:self.chunk])


@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_legal_partial_writes_and_short_reads_are_completed(chunk):
    manifest, payloads = fixture(b"short IO" * 80)
    destination = ShortIO(chunk=chunk)
    inputs = streams(payloads, lambda value: ShortIO(value, chunk=chunk))
    report = write_backup_archive(destination, manifest, inputs)
    assert report.payload_size == 640
    assert destination.write_sizes and destination.read_sizes
    assert all(source.read_sizes for source in inputs.values())
    assert max(destination.write_sizes) <= 65536
    assert all(max(source.read_sizes) <= 65536 for source in inputs.values())


def test_real_private_local_files_remain_open_and_source_unchanged(tmp_path):
    manifest, payloads = fixture()
    source_path, target_path = tmp_path / "source.bin", tmp_path / "staging.bin"
    source_path.write_bytes(payloads["data/open-node.db"])
    source_path.chmod(0o600)
    with source_path.open("rb") as source, target_path.open("w+b") as destination:
        target_path.chmod(0o600)
        report = write_backup_archive(destination, manifest, {"data/open-node.db": source})
        assert not source.closed and not destination.closed
        assert report.archive_size == destination.tell()
        assert source_path.read_bytes() == payloads["data/open-node.db"]


@pytest.mark.parametrize("kind", ["missing", "extra", "not_mapping", "destination_alias",
                                  "source_alias"])
def test_exact_source_names_and_distinct_objects_before_writing(kind):
    manifest, payloads = fixture(full=True)
    destination, inputs = io.BytesIO(), streams(payloads)
    if kind == "missing":
        inputs.pop("data/open-node.db")
    elif kind == "extra":
        inputs["/etc/never-open-this"] = io.BytesIO()
    elif kind == "not_mapping":
        inputs = list(inputs.items())
    elif kind == "destination_alias":
        inputs["data/open-node.db"] = destination
    else:
        paths = list(inputs)
        inputs[paths[1]] = inputs[paths[0]]
    rejected(destination, manifest, inputs)
    assert destination.getvalue() == b""


@pytest.mark.parametrize("content", [b"", b"x", b"too much data" * 10])
def test_all_actual_source_sizes_checked_before_output(content):
    manifest, payloads = fixture(full=True)
    inputs = streams(payloads)
    inputs["secrets/agent-identity.seed"] = io.BytesIO(content)
    destination = io.BytesIO()
    rejected(destination, manifest, inputs)
    assert destination.getvalue() == b""


def test_digest_mismatch_leaves_only_unpublishable_staging_bytes(monkeypatch):
    manifest, payloads = fixture()
    inputs = streams(payloads)
    bad = replace(manifest.files[0], sha256="0" * 64)
    manifest = replace(manifest, files=(bad,))
    monkeypatch.setattr(
        writer, "validate_backup_archive", lambda _s: pytest.fail("validated error"),
    )
    destination = io.BytesIO()
    rejected(destination, manifest, inputs)
    assert destination.getvalue()


@pytest.mark.parametrize("kind", ["nonempty", "not_readable", "not_writable", "not_seekable",
                                  "bad_tell", "bad_seek", "integer_capability"])
def test_destination_contract_is_checked_without_truncating(kind):
    manifest, payloads = fixture()
    destination = io.BytesIO(b"preserve me" if kind == "nonempty" else b"")
    before = destination.getvalue()
    for name in ("readable", "writable", "seekable"):
        if kind == "not_" + name:
            setattr(destination, name, lambda: False)
    if kind == "bad_tell":
        destination.tell = lambda: False
    elif kind == "bad_seek":
        destination.seek = lambda *_args: None
    elif kind == "integer_capability":
        destination.seekable = lambda: 1
    rejected(destination, manifest, streams(payloads))
    assert destination.getvalue() == before


@pytest.mark.parametrize("kind", ["not_readable", "not_seekable", "bad_tell", "bad_seek"])
def test_source_capabilities_and_position_checks(kind):
    manifest, payloads = fixture()
    inputs = streams(payloads)
    source = inputs["data/open-node.db"]
    if kind == "not_readable":
        source.readable = lambda: False
    elif kind == "not_seekable":
        source.seekable = lambda: False
    elif kind == "bad_tell":
        source.tell = lambda: None
    else:
        source.seek = lambda *_args: True
    destination = io.BytesIO()
    rejected(destination, manifest, inputs)
    assert destination.getvalue() == b""


@pytest.mark.parametrize("result", [None, "data", bytearray(b"a"), memoryview(b"a"), True, b""])
def test_invalid_read_results_or_early_eof(result):
    manifest, payloads = fixture()
    inputs = streams(payloads)
    inputs["data/open-node.db"].read = lambda _size: result
    rejected(io.BytesIO(), manifest, inputs)


def test_read_overreturn_and_incorrect_cursor_are_rejected():
    manifest, payloads = fixture()
    for read in (lambda size: b"x" * (size + 1), lambda _size: b"x"):
        inputs = streams(payloads)
        inputs["data/open-node.db"].read = read
        rejected(io.BytesIO(), manifest, inputs)


@pytest.mark.parametrize("result", [None, "1", 0, -1, True, 10**9])
def test_invalid_write_counts_are_never_ignored(result):
    manifest, payloads = fixture()
    destination = io.BytesIO()
    destination.write = lambda _data: result
    rejected(destination, manifest, streams(payloads))


def test_reported_write_count_must_match_actual_cursor():
    manifest, payloads = fixture()
    destination = io.BytesIO()
    destination.write = lambda data: len(data)
    rejected(destination, manifest, streams(payloads))


def test_late_seek_failure_cannot_enable_zip_data_descriptors(monkeypatch):
    class FailingSeek(io.BytesIO):
        calls = 0

        def seek(self, offset, whence=io.SEEK_SET):
            self.calls += 1
            if self.calls == 2:
                raise OSError("do not silently switch to a streaming ZIP")
            return super().seek(offset, whence)

    manifest, payloads = fixture()
    monkeypatch.setattr(writer, "validate_backup_archive", lambda _s: pytest.fail("accepted seek"))
    rejected(FailingSeek(), manifest, streams(payloads))


@pytest.mark.parametrize("operation", ["write", "flush", "seek", "tell", "read"])
def test_io_errors_have_one_safe_error_and_do_not_close_handles(operation):
    manifest, payloads = fixture()
    destination, inputs = io.BytesIO(), streams(payloads)

    def fail(*_args):
        raise OSError("SYNTHETIC-SECRET-HOST-PATH-MUST-NOT-LEAK")

    target = inputs["data/open-node.db"] if operation == "read" else destination
    setattr(target, operation, fail)
    rejected(destination, manifest, inputs)


@pytest.mark.parametrize("payload", [b"", b"opaque database bytes"])
@pytest.mark.parametrize("timing", ["before_read", "after_eof"])
def test_growth_after_size_preflight_is_not_accepted(payload, timing):
    class Growing(io.BytesIO):
        def append_without_moving_cursor(self):
            position = self.tell()
            self.seek(0, io.SEEK_END)
            self.write(b"x")
            self.seek(position)

        def read(self, size=-1):
            if timing == "before_read" and self.tell() == 0:
                self.append_without_moving_cursor()
            result = super().read(size)
            if timing == "after_eof" and result == b"":
                self.append_without_moving_cursor()
            return result

    manifest, payloads = fixture(payload)
    rejected(io.BytesIO(), manifest, streams(payloads, Growing))


def test_reader_runs_after_zip_close_and_its_actual_report_is_returned(monkeypatch):
    manifest, payloads = fixture(full=True)
    destination = io.BytesIO()
    reports = []

    def verify(stream):
        assert stream is destination and not stream.closed
        assert stream.getvalue()[-22:-18] == b"PK\x05\x06"
        report = validate_backup_archive(stream)
        reports.append(report)
        return report

    monkeypatch.setattr(writer, "validate_backup_archive", verify)
    assert write_backup_archive(destination, manifest, streams(payloads)) is reports[0]
    assert len(reports) == 1


def test_corruption_observed_by_independent_reader_never_returns_success():
    class Corrupted(io.BytesIO):
        def read(self, size=-1):
            self.getbuffer()[0] = 0
            return super().read(size)

    manifest, payloads = fixture()
    rejected(Corrupted(), manifest, streams(payloads))


@pytest.mark.parametrize("constant,value", [
    ("MAX_ARCHIVE_BYTES", 100), ("MAX_WRITE_BYTES", 100),
    ("MAX_IO_OPERATIONS", 10), ("MAX_SOURCE_READ_BYTES", 0),
])
def test_each_generation_resource_bound_is_enforced(monkeypatch, constant, value):
    manifest, payloads = fixture()
    monkeypatch.setattr(writer, constant, value)
    rejected(io.BytesIO(), manifest, streams(payloads))


def test_deadline_is_checked_after_an_underlying_operation_without_sleep(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(writer.time, "monotonic", lambda: clock[0])

    class Delayed(io.BytesIO):
        def write(self, data):
            result = super().write(data)
            clock[0] = writer.MAX_VALIDATION_SECONDS + 1
            return result

    manifest, payloads = fixture()
    destination = Delayed()
    rejected(destination, manifest, streams(payloads))
    assert destination.getvalue()


@pytest.mark.parametrize("kind", ["root", "files_list", "configuration_list", "too_many_files",
                                  "long_string", "custom_leaf", "subclass_leaf", "nested_model"])
def test_handcrafted_shapes_rejected_before_asdict(monkeypatch, kind):
    manifest, payloads = fixture()

    class Hostile:
        def __deepcopy__(self, _memo):
            pytest.fail("unsafe deepcopy")

    class Text(str):
        def __deepcopy__(self, _memo):
            pytest.fail("unsafe string subclass deepcopy")

    changes = {
        "files_list": {"files": list(manifest.files)},
        "configuration_list": {"required_configuration": ["deployment_settings"]},
        "too_many_files": {"files": manifest.files * (MAX_FILES + 1)},
        "long_string": {"created_at": "x" * (MAX_PATH_BYTES + 1)},
        "custom_leaf": {"created_at": Hostile()},
        "subclass_leaf": {"created_at": Text(manifest.created_at)},
        "nested_model": {"source": Hostile()},
    }
    invalid_manifest = {} if kind == "root" else replace(manifest, **changes[kind])
    monkeypatch.setattr(
        writer, "asdict", lambda _m: pytest.fail("asdict called before shape check"),
    )
    destination = io.BytesIO()
    rejected(destination, invalid_manifest, streams(payloads))
    assert destination.getvalue() == b""


@pytest.mark.parametrize("kind", ["version_bool", "unknown_format", "bad_role", "coverage",
                                  "bad_digest", "non_nfc", "negative_size", "surrogate"])
def test_handcrafted_semantics_must_still_pass_authoritative_manifest_parser(kind):
    manifest, payloads = fixture()
    changes = {
        "version_bool": {"version": True}, "unknown_format": {"format": "other"},
        "bad_role": {"files": (replace(manifest.files[0], role="other"),)},
        "coverage": {"coverage": replace(manifest.coverage, notifications="included")},
        "bad_digest": {"files": (replace(manifest.files[0], sha256="x"),)},
        "non_nfc": {"files": (replace(manifest.files[0], path="data/e\u0301.db"),)},
        "negative_size": {"files": (replace(manifest.files[0], size=-1),)},
        "surrogate": {"created_at": "\ud800"},
    }
    destination = io.BytesIO()
    rejected(destination, replace(manifest, **changes[kind]), streams(payloads))
    assert destination.getvalue() == b""
