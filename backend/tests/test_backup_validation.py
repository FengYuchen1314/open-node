import hashlib
import io
import json
import stat
import zipfile
from dataclasses import FrozenInstanceError

import pytest
from open_node.domain.backup import BackupValidationError
from open_node.services import backup_validation as validation


def _package(
    files: dict[str, bytes] | None = None,
    *,
    manifest_first: bool = True,
    regular_mode: bool = False,
    database_hash: str | None = None,
) -> tuple[bytes, bytes]:
    contents = {"data/open-node.db": b"not a SQLite database"} if files is None else files
    names = set(contents)
    coverage = {
        "certificates": "included" if "data/certificates/vault.key" in names else "unknown",
        "external_subscriptions": (
            "included" if "data/external-subscriptions/vault.key" in names else "not_configured"
        ),
        "notifications": "included" if "data/notifications/telegram.key" in names else "unknown",
        "agent_identity": (
            "included" if "secrets/agent-identity.seed" in names else "not_configured"
        ),
    }
    roles = {
        "certificates": "certificate_state",
        "external-subscriptions": "external_state",
        "notifications": "notification_state",
    }
    entries = []
    for path, data in contents.items():
        if path == "data/open-node.db":
            role = "database"
        elif path == "secrets/agent-identity.seed":
            role = "agent_identity"
        else:
            role = roles[path.split("/")[1]]
        digest = hashlib.sha256(data).hexdigest()
        if path == "data/open-node.db" and database_hash is not None:
            digest = database_hash
        entries.append({"path": path, "role": role, "size": len(data), "sha256": digest})
    manifest = json.dumps(
        {
            "format": "open-node-control-plane-backup",
            "version": 1,
            "created_at": "2026-08-31T09:00:00Z",
            "source": {"git_revision": None, "image_id": None, "image_revision": None},
            "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
            "coverage": coverage,
            "required_configuration": ["deployment_settings", "subscriber_totp_key"],
            "files": entries,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    members = [("manifest.json", manifest), *contents.items()]
    if not manifest_first:
        members.reverse()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, data in members:
            info = zipfile.ZipInfo(path)
            if regular_mode:
                info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, data)
    return stream.getvalue(), manifest


@pytest.mark.parametrize("manifest_first", [True, False])
@pytest.mark.parametrize("regular_mode", [True, False])
def test_checks_exact_archive_bytes_without_claiming_semantic_validation(
    manifest_first: bool, regular_mode: bool,
) -> None:
    raw, manifest = _package(manifest_first=manifest_first, regular_mode=regular_mode)
    source = io.BytesIO(raw)
    source.seek(3)
    report = validation.validate_backup_archive(source)
    assert report.archive_size == len(raw)
    assert report.payload_size == len(b"not a SQLite database")
    assert report.file_count == 1
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert report.manifest_sha256 == hashlib.sha256(manifest).hexdigest()
    assert report.structure_verified is True
    assert report.content_hashes_verified is True
    for attribute in (
        "source_authentication", "database_validation", "key_validation", "snapshot_validation",
        "restore_validation",
    ):
        assert getattr(report, attribute) == "not_checked"
    assert report.restoration_ready is False
    assert "data/open-node.db" not in repr(report)
    assert not source.closed
    assert source.getvalue() == raw
    with pytest.raises(FrozenInstanceError):
        report.restoration_ready = True
    with pytest.raises(FrozenInstanceError):
        report.manifest.files = ()


def test_accepts_all_declared_roles_and_literal_unicode_names() -> None:
    files = {
        "data/open-node.db": b"database bytes are not inspected as SQLite",
        "data/certificates/vault.key": b"not a valid vault key",
        "data/certificates/vault.initialized": b"not a matching marker",
        "data/certificates/账户/证书.pem": b"not a certificate",
        "data/certificates/manifest.json": b"ordinary certificate-tree file",
        "data/certificates/retained.db-wal": b"ordinary certificate-tree file too",
        "data/external-subscriptions/vault.key": b"external key",
        "data/external-subscriptions/vault.initialized": b"external marker",
        "data/notifications/telegram.key": b"notification key",
        "data/notifications/telegram.initialized": b"notification marker",
        "secrets/agent-identity.seed": b"not a 32-byte seed",
    }
    raw, _ = _package(files)
    report = validation.validate_backup_archive(io.BytesIO(raw))
    assert report.payload_size == sum(map(len, files.values()))
    assert report.file_count == len(files)
    assert {entry.path for entry in report.manifest.files} == set(files)
    assert report.key_validation == report.database_validation == "not_checked"


@pytest.mark.parametrize("size", [0, 1, 65535, 65536, 65537, 196613])
def test_streams_payload_chunk_boundaries_and_empty_members(size: int) -> None:
    contents = bytes(index % 251 for index in range(size))
    raw, _ = _package({"data/open-node.db": contents})
    report = validation.validate_backup_archive(io.BytesIO(raw))
    assert report.payload_size == size
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()


def test_sha_mismatch_rejected_even_when_both_zip_crcs_are_valid() -> None:
    raw, _ = _package(database_hash="0" * 64)
    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$"):
        validation.validate_backup_archive(io.BytesIO(raw))


@pytest.mark.parametrize("raw", [b"", b"not a zip archive", b"\x00" * 22])
def test_non_archive_input_has_safe_error_and_remains_open(raw: bytes) -> None:
    source = io.BytesIO(raw)
    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$") as caught:
        validation.validate_backup_archive(source)
    assert caught.value.__cause__ is None
    assert not source.closed
    assert source.getvalue() == raw


def test_non_seekable_input_rejected_before_reading() -> None:
    class NonSeekable:
        def seekable(self):
            return False

        def read(self, _size):
            pytest.fail("non-seekable input must not be read")

    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(NonSeekable())


def test_size_cap_rejects_before_any_read_without_materializing_huge_input() -> None:
    class Oversized:
        def seekable(self):
            return True

        def seek(self, offset, whence):
            assert (offset, whence) == (0, io.SEEK_END)
            return validation.MAX_ARCHIVE_BYTES + 1

        def read(self, _size):
            pytest.fail("oversized input must not be read")

    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(Oversized())


@pytest.mark.parametrize("value", [None, "secret input path", bytearray(b"PK"), 123])
def test_non_binary_read_results_have_safe_error(value) -> None:
    raw, _ = _package()

    class WrongReader(io.BytesIO):
        def read(self, _size=-1):
            return value

    source = WrongReader(raw)
    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$"):
        validation.validate_backup_archive(source)
    assert not source.closed


def test_over_returning_reader_is_rejected() -> None:
    raw, _ = _package()

    class OverReader(io.BytesIO):
        def read(self, size=-1):
            return b"x" * (size + 1)

    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(OverReader(raw))


def test_wrong_seek_position_is_rejected() -> None:
    raw, _ = _package()

    class WrongSeek(io.BytesIO):
        def seek(self, offset, whence=io.SEEK_SET):
            actual = super().seek(offset, whence)
            return actual if whence == io.SEEK_END else actual + 1

    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(WrongSeek(raw))


def test_observed_growth_on_same_held_input_is_rejected() -> None:
    raw, _ = _package()

    class GrowingReader(io.BytesIO):
        grew = False

        def read(self, size=-1):
            data = super().read(size)
            if not self.grew:
                self.grew = True
                position = self.tell()
                super().seek(0, io.SEEK_END)
                super().write(b"unexpected suffix from another writer")
                super().seek(position)
            return data

    source = GrowingReader(raw)
    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$"):
        validation.validate_backup_archive(source)
    assert not source.closed


@pytest.mark.parametrize("failure", [OSError, EOFError, ValueError, RuntimeError])
def test_io_errors_do_not_leak_source_paths_or_payload(failure) -> None:
    raw, _ = _package()

    class FailingReader(io.BytesIO):
        def read(self, _size=-1):
            raise failure("private/path/and/secret contents")

    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$") as caught:
        validation.validate_backup_archive(FailingReader(raw))
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("budget", ["MAX_READ_BYTES", "MAX_IO_OPERATIONS"])
def test_resource_budgets_are_enforced(monkeypatch, budget: str) -> None:
    raw, _ = _package()
    monkeypatch.setattr(validation, budget, 1)
    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(io.BytesIO(raw))


def test_deadline_is_checked_after_underlying_read_without_sleep(monkeypatch) -> None:
    raw, _ = _package()
    now = [0.0]
    monkeypatch.setattr(validation.time, "monotonic", lambda: now[0])

    class SlowReader(io.BytesIO):
        def read(self, size=-1):
            block = super().read(size)
            now[0] += validation.MAX_VALIDATION_SECONDS + 1
            return block

    with pytest.raises(BackupValidationError):
        validation.validate_backup_archive(SlowReader(raw))


def test_all_underlying_reads_are_finite_and_at_most_one_chunk() -> None:
    raw, _ = _package({"data/open-node.db": b"x" * (3 * validation.READ_CHUNK_BYTES)})
    calls = []

    class RecordingReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= validation.READ_CHUNK_BYTES
            calls.append(size)
            return super().read(size)

    report = validation.validate_backup_archive(RecordingReader(raw))
    assert report.payload_size == 3 * validation.READ_CHUNK_BYTES
    assert calls
    assert max(calls) == validation.READ_CHUNK_BYTES


def test_real_private_file_is_not_modified_or_closed(tmp_path) -> None:
    raw, _ = _package()
    path = tmp_path / "private-backup.zip"
    path.write_bytes(raw)
    before = path.stat()
    with path.open("rb") as source:
        report = validation.validate_backup_archive(source)
        assert not source.closed
        assert source.readable()
        assert not source.writable()
    after = path.stat()
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns,
    )


def test_largest_declared_file_count_fits_operation_budget() -> None:
    files = {
        "data/open-node.db": b"db",
        "data/certificates/vault.key": b"key",
        "data/certificates/vault.initialized": b"marker",
    }
    files.update({f"data/certificates/fixture-{index}.pem": b"" for index in range(4093)})
    raw, _ = _package(files)
    report = validation.validate_backup_archive(io.BytesIO(raw))
    assert report.file_count == 4096
    assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
