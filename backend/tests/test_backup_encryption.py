"""Private staging/runner boundary tests, plus explicitly opt-in official-age tests.

The stub runner used by unit tests performs NO cryptography. Only tests named
real_age_* launch the pinned official binary; the test-only environment variable
selects its private location while retaining production SHA/ELF validation.
"""

import base64
import errno
import fcntl
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from open_node.domain.backup import BackupValidationError, parse_backup_manifest
from open_node.services import backup_encryption as encryption
from open_node.services.backup_archive import write_backup_archive
from open_node.services.backup_validation import validate_backup_archive

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux descriptor contract")

PUBLIC_SHAPE = "age1" + "q" * 58
IDENTITY_SHAPE = b"AGE-SECRET-KEY-1" + b"Q" * 58
PRIVATE_SENTINEL = "never-echo-private-input-or-path"
BODY = ("Synthetic garbage DB: 中文 🔑 <script>plain text</script>.\n" * 6).encode()
APP_ROOT = Path(__file__).resolve().parents[1] / "app"
UNCHECKED = (
    "source_authentication", "database_validation", "key_validation", "snapshot_validation",
    "restore_validation",
)


def require(condition: bool, message: str = "Controlled test assertion failed") -> None:
    """Keep actual generated identities/plaintexts out of assertion diagnostics."""
    if not condition:
        raise AssertionError(message)


def package(body: bytes = BODY) -> bytes:
    manifest = parse_backup_manifest(json.dumps({
        "format": "open-node-control-plane-backup", "version": 1,
        "created_at": "2026-08-31T00:00:00Z",
        "source": {"git_revision": None, "image_id": None, "image_revision": None},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": dict.fromkeys((
            "certificates", "external_subscriptions", "notifications", "agent_identity",
        ), "unknown"),
        "required_configuration": ["deployment_settings"],
        "files": [{
            "path": "data/open-node.db", "role": "database", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }],
    }).encode())
    destination = io.BytesIO()
    write_backup_archive(destination, manifest, {"data/open-node.db": io.BytesIO(body)})
    return destination.getvalue()


def fake_header() -> bytes:
    block = base64.b64encode(bytes(32)).rstrip(b"=")
    return b"age-encryption.org/v1\n-> X25519 " + block + b"\n" + block + b"\n--- " + block + b"\n"


def fake_ciphertext(plaintext: bytes) -> bytes:
    """A shape-only substitute, deliberately not authenticated encryption."""
    size = encryption._cipher_size(len(plaintext))
    return fake_header() + bytes(size - encryption.AGE_HEADER_BYTES)


def descriptor_inventory() -> dict[int, tuple[int, int, int]]:
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            info = os.fstat(fd)
            result[fd] = (info.st_dev, info.st_ino, fcntl.fcntl(fd, fcntl.F_GETFL))
        except OSError:
            continue
    return result


def signature(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
    )


def safe_failure(context) -> None:
    with pytest.raises(BackupValidationError) as error, context:
        pytest.fail("Rejected input must never be yielded")
    assert str(error.value) == "Invalid backup package."
    assert error.value.__cause__ is None
    assert PRIVATE_SENTINEL not in repr(error.value)


def stub_runner(monkeypatch, raw: bytes, *, failure=None, output=None):
    """Observe access/ownership; output is a test substitute, never a crypto proof."""
    calls = []

    def run(*, operation, stdin_fd, stdout_fd, key_fd, expected_output_size):
        descriptors = (stdin_fd, stdout_fd, key_fd)
        identities = []
        for fd, access in zip(descriptors, (os.O_RDONLY, os.O_WRONLY, os.O_RDONLY), strict=True):
            info = os.fstat(fd)
            assert fd >= 3
            assert stat.S_ISREG(info.st_mode)
            assert info.st_nlink == 0
            assert info.st_uid == os.geteuid()
            assert stat.S_IMODE(info.st_mode) == 0o600
            assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access
            assert os.lseek(fd, 0, os.SEEK_CUR) == 0
            identities.append((info.st_dev, info.st_ino))
        assert len(set(identities)) == 3
        for fd in (stdin_fd, key_fd):
            with pytest.raises(OSError):
                os.write(fd, b"x")
        with pytest.raises(OSError):
            os.read(stdout_fd, 1)
        key_bytes = os.read(key_fd, 4096)
        expected_key = PUBLIC_SHAPE.encode() + b"\n" if operation == "encrypt" else (
            IDENTITY_SHAPE + b"\n"
        )
        require(key_bytes == expected_key, "Only the canonical key line may reach the child")
        input_info = os.fstat(stdin_fd)
        input_data = os.read(stdin_fd, input_info.st_size + 1)
        expected_input = raw if operation == "encrypt" else fake_ciphertext(raw)
        require(input_data == expected_input, "The child must read the complete private copy")
        calls.append((operation, descriptors, expected_output_size))
        produced = fake_ciphertext(raw) if operation == "encrypt" else raw
        if output is not None:
            produced = output
        written = os.write(stdout_fd, produced)
        assert written == len(produced)
        if failure is not None:
            raise failure

    monkeypatch.setattr(encryption, "_run_age", run)
    return calls


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_stub_private_copy_readonly_output_and_precise_report(monkeypatch, tmp_path, operation):
    raw = package()
    calls = stub_runner(monkeypatch, raw)
    original = raw if operation == "encrypt" else fake_ciphertext(raw)
    source = io.BytesIO(original)
    source.seek(7)
    before = descriptor_inventory()
    make = encryption.encrypted_backup_archive if operation == "encrypt" else (
        encryption.decrypted_backup_archive
    )
    key = PUBLIC_SHAPE if operation == "encrypt" else (
        b"# ignored\r\n\r\n" + IDENTITY_SHAPE + b"\r\n"
    )
    with make(source, key, temporary_directory=tmp_path) as staged:
        stream = staged.stream
        assert isinstance(stream, io.FileIO)
        assert stream.readable() and stream.seekable() and not stream.writable()
        assert stream.tell() == os.lseek(stream.fileno(), 0, os.SEEK_CUR) == 0
        assert fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert os.fstat(stream.fileno()).st_nlink == 0
        with pytest.raises(OSError):
            os.write(stream.fileno(), b"x")
        with pytest.raises(io.UnsupportedOperation):
            stream.write(b"x")
        expected = fake_ciphertext(raw) if operation == "encrypt" else raw
        assert hashlib.sha256(stream.read()).hexdigest() == hashlib.sha256(expected).hexdigest()
        report = staged.report
        assert report.encryption == "age-v1-x25519"
        assert report.authenticated_decryption is (operation == "decrypt")
        assert report.encrypted_size == len(fake_ciphertext(raw))
        assert report.encrypted_sha256 == hashlib.sha256(fake_ciphertext(raw)).hexdigest()
        archive = report.archive_report
        assert archive.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
        assert archive.structure_verified and archive.content_hashes_verified
        assert all(getattr(archive, name) == "not_checked" for name in UNCHECKED)
        assert archive.restoration_ready is False
        for secret in ("data/open-node.db", "manifest=", "archive_report=", "stream="):
            assert secret not in repr(staged)
        assert list(tmp_path.iterdir()) == []
    assert stream.closed and not source.closed
    assert source.getvalue() == original
    assert len(calls) == 1
    assert descriptor_inventory() == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_stub_regular_source_hash_stat_and_open_ownership_unchanged(
    monkeypatch, tmp_path, operation,
):
    raw = package()
    stub_runner(monkeypatch, raw)
    source_path = tmp_path / PRIVATE_SENTINEL
    initial = raw if operation == "encrypt" else fake_ciphertext(raw)
    source_path.write_bytes(initial)
    before = signature(source_path)
    make = encryption.encrypted_backup_archive if operation == "encrypt" else (
        encryption.decrypted_backup_archive
    )
    with source_path.open("rb") as source:
        source.seek(19)
        key = PUBLIC_SHAPE if operation == "encrypt" else IDENTITY_SHAPE
        with make(source, key, temporary_directory=tmp_path):
            assert not source.closed
        assert not source.closed
        assert signature(source_path) == before
    assert hashlib.sha256(source_path.read_bytes()).digest() == hashlib.sha256(initial).digest()
    assert signature(source_path) == before
    assert list(tmp_path.iterdir()) == [source_path]


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("kind", ["oserror", "timeout", "value", "partial", "invalid_zip"])
def test_stub_errors_never_yield_or_leave_resources(monkeypatch, tmp_path, operation, kind):
    raw = package()
    failures = {
        "oserror": OSError(errno.ENOSPC, PRIVATE_SENTINEL),
        "timeout": subprocess.TimeoutExpired(PRIVATE_SENTINEL, 30),
        "value": ValueError(PRIVATE_SENTINEL),
    }
    output = None
    if kind == "partial":
        output = b"x"
    if kind == "invalid_zip":
        output = bytes(len(fake_ciphertext(raw)) if operation == "encrypt" else len(raw))
    stub_runner(monkeypatch, raw, failure=failures.get(kind), output=output)
    source = io.BytesIO(raw if operation == "encrypt" else fake_ciphertext(raw))
    make = encryption.encrypted_backup_archive if operation == "encrypt" else (
        encryption.decrypted_backup_archive
    )
    key = PUBLIC_SHAPE if operation == "encrypt" else IDENTITY_SHAPE
    before = descriptor_inventory()
    safe_failure(make(source, key, temporary_directory=tmp_path))
    assert not source.closed and list(tmp_path.iterdir()) == []
    assert descriptor_inventory() == before


@pytest.mark.parametrize("cancel_type", [KeyboardInterrupt, GeneratorExit])
@pytest.mark.parametrize("phase", ["runner", "consumer"])
def test_baseexception_propagates_after_private_cleanup(monkeypatch, tmp_path, cancel_type, phase):
    raw = package()
    sentinel = cancel_type()
    stub_runner(monkeypatch, raw, failure=sentinel if phase == "runner" else None)
    source = io.BytesIO(raw)
    before = descriptor_inventory()
    with pytest.raises(cancel_type) as error, encryption.encrypted_backup_archive(
        source, PUBLIC_SHAPE, temporary_directory=tmp_path,
    ):
        if phase == "consumer":
            raise sentinel
        pytest.fail("Runner cancellation must not yield")
    assert error.value is sentinel
    assert not source.closed and list(tmp_path.iterdir()) == []
    assert descriptor_inventory() == before


@pytest.mark.parametrize("value", [
    None, 1, b"age1", "", PUBLIC_SHAPE + "\n", " " + PUBLIC_SHAPE,
    PUBLIC_SHAPE + " ", PUBLIC_SHAPE.upper(), "age1" + "q" * 57,
    "age1" + "q" * 59, "age1" + "q" * 57 + "b", "age1" + "q" * 57 + "é",
    PUBLIC_SHAPE[:25] + "\x00" + PUBLIC_SHAPE[26:], "ssh-ed25519 " + "q" * 50,
])
def test_invalid_recipient_is_rejected_before_source_or_stage(monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed recipient reached file access")

    monkeypatch.setattr(encryption, "_directory", forbidden)
    safe_failure(encryption.encrypted_backup_archive(object(), value))


@pytest.mark.parametrize("value", [
    None, "secret", bytearray(IDENTITY_SHAPE), b"", b"# comments only\n", b"\n\n",
    b" " + IDENTITY_SHAPE, IDENTITY_SHAPE + b" ", b"\t" + IDENTITY_SHAPE,
    b"# comment\t\n" + IDENTITY_SHAPE, IDENTITY_SHAPE + b"\r", IDENTITY_SHAPE + b"\x7f",
    b"#\x00\n" + IDENTITY_SHAPE, IDENTITY_SHAPE.lower(), IDENTITY_SHAPE[:-1],
    IDENTITY_SHAPE + b"Q", IDENTITY_SHAPE + b"\n" + IDENTITY_SHAPE,
    b"#" + b"x" * 4096 + b"\n" + IDENTITY_SHAPE, b"ssh-ed25519 fake-key",
    b" AGE-PLUGIN-TEST-1QQQQ", IDENTITY_SHAPE[:-1] + b"B",
])
def test_invalid_identity_is_rejected_before_source_or_stage(monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed identity reached file access")

    monkeypatch.setattr(encryption, "_directory", forbidden)
    safe_failure(encryption.decrypted_backup_archive(object(), value))


@pytest.mark.parametrize("value", [
    IDENTITY_SHAPE, IDENTITY_SHAPE + b"\n", IDENTITY_SHAPE + b"\r\n",
    b"# created: fixture\n# public key: fixture\n" + IDENTITY_SHAPE + b"\n",
    b"\r\n# comment\r\n\r\n" + IDENTITY_SHAPE + b"\r\n\r\n",
    b"#" + b"x" * (4096 - len(IDENTITY_SHAPE) - 2) + b"\n" + IDENTITY_SHAPE,
])
def test_identity_canonicalization_only_emits_single_key_line(value):
    assert encryption._identity(value) == IDENTITY_SHAPE + b"\n"


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY"])
def test_missing_platform_flag_has_no_fallback(monkeypatch, flag):
    monkeypatch.delattr(os, flag)
    safe_failure(encryption.encrypted_backup_archive(object(), PUBLIC_SHAPE))


def test_missing_fd_exec_and_dup_cloexec_have_no_fallback(monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(os, "supports_fd", set())
        safe_failure(encryption.encrypted_backup_archive(object(), PUBLIC_SHAPE))
    monkeypatch.delattr(fcntl, "F_DUPFD_CLOEXEC")
    safe_failure(encryption.encrypted_backup_archive(object(), PUBLIC_SHAPE))


def test_unsupported_architecture_rejects_before_open(monkeypatch):
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(machine="unavailable"))
    safe_failure(encryption.encrypted_backup_archive(object(), PUBLIC_SHAPE))


@pytest.mark.parametrize("kind", ["symlink", "file", "missing", "nul"])
def test_temporary_directory_must_be_an_openable_real_directory(monkeypatch, tmp_path, kind):
    raw = package()
    target = tmp_path / "target"
    if kind == "symlink":
        target.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "file":
        target.write_bytes(b"fixture")
    elif kind == "nul":
        target = "\x00"
    before = descriptor_inventory()
    safe_failure(encryption.encrypted_backup_archive(io.BytesIO(raw), PUBLIC_SHAPE,
                                                    temporary_directory=target))
    assert descriptor_inventory() == before


class ShortSource(io.BytesIO):
    def __init__(self, value, chunk):
        super().__init__(value)
        self.chunk, self.read_calls = chunk, []

    def read(self, size=-1):
        assert 0 < size <= 65536
        self.read_calls.append(size)
        return super().read(min(size, self.chunk))


@pytest.mark.parametrize("chunk", [1, 7, 31, 65536])
def test_legal_short_reads_are_filled_without_unbounded_read(monkeypatch, tmp_path, chunk):
    raw = package()
    stub_runner(monkeypatch, raw)
    source = ShortSource(raw, chunk)
    with encryption.encrypted_backup_archive(source, PUBLIC_SHAPE, temporary_directory=tmp_path):
        pass
    assert source.read_calls and max(source.read_calls) <= 65536
    assert not source.closed


@pytest.mark.parametrize("kind", [
    "oversized", "undersized", "bool_size", "false_readable", "false_seekable",
    "int_readable", "wrong_tell", "empty_read", "overread", "nonbytes", "read_error",
    "growing", "shrinking", "changing_fileno",
])
def test_incoherent_or_excessive_source_is_fixed_error_before_child(monkeypatch, tmp_path, kind):
    raw = package()

    class BrokenSource(io.BytesIO):
        ended = False

        def readable(self):
            return False if kind == "false_readable" else 1 if kind == "int_readable" else True

        def seekable(self):
            return kind != "false_seekable"

        def seek(self, offset, whence=0):
            actual = super().seek(offset, whence)
            if whence == io.SEEK_END:
                if kind == "oversized":
                    return encryption.MAX_ARCHIVE_BYTES + 1
                if kind == "undersized":
                    return 21
                if kind == "bool_size":
                    return True
                if kind == "shrinking" and self.ended:
                    return actual - 1
                self.ended = True
            return actual

        def tell(self):
            return -1 if kind == "wrong_tell" else super().tell()

        def read(self, size=-1):
            assert 0 < size <= 65536
            if kind == "empty_read":
                return b""
            if kind == "overread":
                return bytes(size + 1)
            if kind == "nonbytes":
                return bytearray(b"x")
            if kind == "read_error":
                raise OSError(PRIVATE_SENTINEL)
            block = super().read(size)
            if not block and kind == "growing":
                return b"x"
            return block

        def fileno(self):
            if kind == "changing_fileno":
                return True
            return super().fileno()

    source = BrokenSource(raw)

    def forbidden(**kwargs):
        pytest.fail("Invalid source reached the child")

    monkeypatch.setattr(encryption, "_run_age", forbidden)
    before = descriptor_inventory()
    safe_failure(encryption.encrypted_backup_archive(source, PUBLIC_SHAPE,
                                                    temporary_directory=tmp_path))
    assert not source.closed and descriptor_inventory() == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["deadline", "operations", "disk_full"])
def test_copy_soft_budgets_and_enospc_cleanup(monkeypatch, tmp_path, failure):
    raw = package()
    before = descriptor_inventory()
    if failure == "deadline":
        counter = iter(range(0, 1000, 31))
        monkeypatch.setattr(encryption.time, "monotonic", lambda: next(counter))
    elif failure == "operations":
        monkeypatch.setattr(encryption, "MAX_IO_OPERATIONS", 2)
    else:
        def fail_write(*args, **kwargs):
            raise OSError(errno.ENOSPC, PRIVATE_SENTINEL)

        monkeypatch.setattr(encryption, "_write_all", fail_write)
    safe_failure(encryption.encrypted_backup_archive(io.BytesIO(raw), PUBLIC_SHAPE,
                                                    temporary_directory=tmp_path))
    assert descriptor_inventory() == before and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("count", [0, -1, None, True, "3", 1000])
def test_invalid_write_counts_rejected(count):
    class BrokenWriter(io.BytesIO):
        def write(self, value):
            return count

    with pytest.raises(BackupValidationError):
        encryption._write_all(BrokenWriter(), b"test", encryption._Budget(
            read_limit=0, write_limit=4,
        ), 0)


def test_short_writes_are_completed_with_bounded_calls():
    class ShortWriter(io.BytesIO):
        def write(self, value):
            return super().write(value[:3])

    destination = ShortWriter()
    data = bytes(range(256)) * 300
    budget = encryption._Budget(read_limit=0, write_limit=len(data))
    assert encryption._write_all(destination, data, budget, 0) == len(data)
    assert destination.getvalue() == data


@pytest.mark.parametrize("size", [22, 23, 65535, 65536, 65537, 131071, 131072, 131073,
                                      encryption.MAX_ARCHIVE_BYTES])
def test_ciphertext_size_inference_exact_chunk_edges(size):
    total = encryption._cipher_size(size)
    assert encryption._cipher_shape(io.BytesIO(fake_header()), total) == size
    if size == encryption.MAX_ARCHIVE_BYTES:
        assert total == encryption.MAX_ENCRYPTED_ARCHIVE_BYTES == 1090785464


@pytest.mark.parametrize("change", ["intro", "crlf", "armor", "multi", "scrypt", "ssh",
                                        "plugin", "pq", "padded", "pad_bits", "truncated"])
def test_header_preflight_is_exact_canonical_single_x25519(change):
    header = fake_header()
    if change == "intro":
        header = header.replace(b"v1\n", b"v2\n", 1)
    elif change == "crlf":
        header = header.replace(b"\n", b"\r\n")
    elif change == "armor":
        header = b"-----BEGIN AGE ENCRYPTED FILE-----\n" + header
    elif change == "multi":
        header = header.replace(b"--- ", b"-> X25519 " + b"A" * 43 + b"\n--- ")
    elif change in {"scrypt", "ssh", "plugin", "pq"}:
        value = {"scrypt": b"scrypt", "ssh": b"ssh-rsa", "plugin": b"plugin", "pq": b"MLKEM"}
        header = header.replace(b"X25519", value[change])
    elif change == "padded":
        header = header.replace(b"A\n", b"A=\n", 1)
    elif change == "pad_bits":
        header = header.replace(b"A\n", b"B\n", 1)
    elif change == "truncated":
        header = header[:-1]
    with pytest.raises(BackupValidationError):
        encryption._cipher_shape(io.BytesIO(header), 1000)


def dummy_elf() -> bytes:
    value = bytearray(256)
    value[:7] = b"\x7fELF\x02\x01\x01"
    value[18:20] = (62).to_bytes(2, "little")
    return bytes(value)


def metadata_binary(monkeypatch, tmp_path, raw=None):
    """Pin a non-executable test byte string only for metadata rejection tests."""
    raw = dummy_elf() if raw is None else raw
    binary = tmp_path / "controlled-metadata-fixture"
    binary.write_bytes(raw)
    binary.chmod(0o700)
    monkeypatch.setattr(encryption, "AGE_BINARY_PATH", str(binary))
    monkeypatch.setattr(encryption, "_AGE_BINARIES", {
        os.uname().machine: (62, len(raw), hashlib.sha256(raw).hexdigest()),
    })
    return binary


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777, 0o4700, 0o2700, 0o1700, 0o7700])
def test_binary_write_or_special_permissions_rejected(monkeypatch, tmp_path, mode):
    binary = metadata_binary(monkeypatch, tmp_path)
    binary.chmod(mode)
    before = descriptor_inventory()
    with pytest.raises(BackupValidationError), ExitStack() as stack:
        encryption._open_age_binary(stack)
    assert descriptor_inventory() == before


def test_binary_unrelated_owner_is_rejected_and_effective_uid_used(monkeypatch, tmp_path):
    binary = metadata_binary(monkeypatch, tmp_path)
    original_stat = os.fstat
    inode = binary.stat().st_ino
    euid = os.geteuid()

    def alien_stat(fd):
        info = original_stat(fd)
        if info.st_ino != inode:
            return info
        fields = {name: getattr(info, name) for name in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode",
            "st_uid", "st_gid", "st_nlink",
        )}
        return SimpleNamespace(**(fields | {"st_uid": euid + 12345}))

    monkeypatch.setattr(os, "fstat", alien_stat)
    with pytest.raises(BackupValidationError), ExitStack() as stack:
        encryption._open_age_binary(stack)
    monkeypatch.setattr(os, "fstat", original_stat)
    monkeypatch.setattr(os, "getuid", lambda: euid + 12345)
    with ExitStack() as stack:
        fd, _ = encryption._open_age_binary(stack)
        assert os.fstat(fd).st_uid in (0, euid)


@pytest.mark.parametrize("kind", ["hash", "size", "symlink", "fifo", "directory", "missing"])
def test_binary_path_and_bytes_are_fail_closed(monkeypatch, tmp_path, kind):
    binary = metadata_binary(monkeypatch, tmp_path)
    if kind == "hash":
        binary.write_bytes(dummy_elf()[:-1] + b"x")
    elif kind == "size":
        binary.write_bytes(dummy_elf()[:-1])
    elif kind == "symlink":
        link = tmp_path / "symlink"
        link.symlink_to(binary)
        monkeypatch.setattr(encryption, "AGE_BINARY_PATH", str(link))
    else:
        binary.unlink()
        if kind == "fifo":
            os.mkfifo(binary, 0o600)
        elif kind == "directory":
            binary.mkdir()
    before = descriptor_inventory()
    with pytest.raises((BackupValidationError, OSError)), ExitStack() as stack:
        encryption._open_age_binary(stack)
    assert descriptor_inventory() == before


@pytest.mark.parametrize("field_index,new_value", [(0, 0), (4, 1), (5, 2), (6, 0), (18, 183)])
def test_binary_elf_architecture_even_with_matching_controlled_hash(
    monkeypatch, tmp_path, field_index, new_value,
):
    raw = bytearray(dummy_elf())
    raw[field_index] = new_value
    metadata_binary(monkeypatch, tmp_path, bytes(raw))
    with pytest.raises(BackupValidationError), ExitStack() as stack:
        encryption._open_age_binary(stack)


def test_binary_short_reads_accumulate_full_elf_header(monkeypatch, tmp_path):
    binary = metadata_binary(monkeypatch, tmp_path)
    original_read = os.read
    inode = binary.stat().st_ino

    def short_read(fd, length):
        return original_read(fd, min(length, 7) if os.fstat(fd).st_ino == inode else length)

    monkeypatch.setattr(os, "read", short_read)
    before = descriptor_inventory()
    with ExitStack() as stack:
        fd, identity = encryption._open_age_binary(stack)
        assert identity[1] == inode and fd >= 3
    assert descriptor_inventory() == before


@pytest.mark.parametrize("fault", ["return", "timeout", "cancel", "spawn"])
def test_runner_no_secrets_env_or_shell_and_failure_kill_reap(monkeypatch, tmp_path, fault):
    raw = package()
    binary = metadata_binary(monkeypatch, tmp_path)
    process_events = []
    captured = {}

    class ControlledProcess:
        pid = 987654321
        running = True
        waited = 0

        def wait(self, timeout=None):
            self.waited += 1
            process_events.append(("wait", timeout))
            if self.waited == 1 and fault == "timeout":
                raise subprocess.TimeoutExpired(PRIVATE_SENTINEL, timeout)
            if self.waited == 1 and fault == "cancel":
                raise KeyboardInterrupt()
            self.running = False
            return 1 if fault == "return" else -9

        def poll(self):
            return None if self.running else 1

    def popen(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        if fault == "spawn":
            raise OSError(PRIVATE_SENTINEL)
        return ControlledProcess()

    monkeypatch.setattr(encryption.subprocess, "Popen", popen)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: process_events.append(("kill", pid, sig)))
    before = descriptor_inventory()
    context = encryption.encrypted_backup_archive(io.BytesIO(raw), PUBLIC_SHAPE,
                                                  temporary_directory=tmp_path)
    if fault == "cancel":
        with pytest.raises(KeyboardInterrupt), context:
            pytest.fail("Cancelled controlled child must not yield")
    else:
        safe_failure(context)
    assert captured["env"] == {} and captured["cwd"] == "/"
    assert captured["stderr"] == subprocess.DEVNULL
    assert captured["close_fds"] is True and captured["start_new_session"] is True
    assert "shell" not in captured and "preexec_fn" not in captured
    assert captured["argv"][:5] == [sys.executable, "-I", "-S", "-B", "-c"]
    assert len(captured["pass_fds"]) == 2
    assert PUBLIC_SHAPE not in repr(captured)
    assert IDENTITY_SHAPE.decode() not in repr(captured)
    assert PRIVATE_SENTINEL not in repr(captured)
    assert BODY.decode() not in repr(captured)
    if fault in {"timeout", "cancel"}:
        assert process_events == [("wait", 30.0), ("kill", 987654321, 9), ("wait", None)]
    assert descriptor_inventory() == before
    assert list(tmp_path.iterdir()) == [binary]


def test_validator_disagreement_is_not_published(monkeypatch, tmp_path):
    raw = package()
    actual = validate_backup_archive(io.BytesIO(raw))
    monkeypatch.setattr(encryption, "validate_backup_archive", lambda stream: replace(
        actual, checked_archive_sha256="0" * 64,
    ))
    calls = stub_runner(monkeypatch, raw)
    safe_failure(encryption.encrypted_backup_archive(io.BytesIO(raw), PUBLIC_SHAPE,
                                                    temporary_directory=tmp_path))
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_context_generator_explicit_close_cleans_yielded_stream(monkeypatch, tmp_path):
    raw = package()
    stub_runner(monkeypatch, raw)
    context = encryption.encrypted_backup_archive(io.BytesIO(raw), PUBLIC_SHAPE,
                                                  temporary_directory=tmp_path)
    before = descriptor_inventory()
    staged = context.__enter__()
    context.gen.close()
    assert staged.stream.closed
    assert descriptor_inventory() == before


def test_import_in_clean_subprocess_never_loads_application_or_crypto(tmp_path):
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("open_node.main", "open_node.core", "sqlalchemy", "cryptography", "fastapi")
        allowed = ("open_node.services.backup_encryption", "open_node.services.backup_validation")
        if (fullname.startswith("open_node.services.") and fullname not in allowed
                or any(fullname == name or fullname.startswith(name + ".") for name in blocked)):
            raise RuntimeError("Forbidden application import")
sys.meta_path.insert(0, Guard())
import open_node.services.backup_encryption as module
assert module.MAX_ENCRYPTED_ARCHIVE_BYTES == 1090785464
print("PURE_IMPORT_OK")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(APP_ROOT)],
        cwd=tmp_path, env={"OPEN_NODE_DATABASE_URL": "invalid", "TMPDIR": str(tmp_path / "bad")},
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0 and result.stdout == b"PURE_IMPORT_OK\n" and not result.stderr
    assert list(tmp_path.iterdir()) == []


@dataclass(frozen=True, repr=False)
class NativeKeys:
    binary: Path
    public: str = field(repr=False)
    identity: bytes = field(repr=False)
    other_identity: bytes = field(repr=False)


@pytest.fixture(scope="module")
def real_age_keys():
    configured = os.environ.get("OPEN_NODE_BACKUP_AGE_TEST_BINARY")
    if not configured:
        pytest.skip("Opt-in pinned official-age interoperability, not stub crypto")
    binary = Path(configured)
    keygen = binary.with_name("age-keygen")
    require(binary.is_file() and keygen.is_file(), "Opt-in official tools are missing")
    require(hashlib.sha256(keygen.read_bytes()).hexdigest() == (
        "0a0009db842259d6717f7eeb30acb6b90d2a2eb924c6acd0a0db0ca1f1537899"
    ), "Official test keygen digest mismatch")
    identities = []
    for _ in range(2):
        with tempfile.TemporaryFile("w+b", buffering=0) as key:
            result = subprocess.run(
                [str(keygen)], stdin=subprocess.DEVNULL, stdout=key, stderr=subprocess.DEVNULL,
                env={}, timeout=10, check=False, start_new_session=True,
            )
            require(result.returncode == 0, "Official key generation failed")
            key.seek(0)
            identities.append(key.read(4097))
    result = subprocess.run(
        [str(keygen), "-y"], input=identities[0], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={}, timeout=10, check=False, start_new_session=True,
    )
    require(result.returncode == 0, "Official public-key derivation failed")
    public = result.stdout.decode("ascii").removesuffix("\n")
    require(len(public) == 62, "Official public key has an unexpected format")
    return NativeKeys(binary, public, identities[0], identities[1])


@pytest.fixture
def official_age(monkeypatch, real_age_keys):
    monkeypatch.setattr(encryption, "AGE_BINARY_PATH", str(real_age_keys.binary))
    return real_age_keys


@pytest.mark.parametrize("body_size", [0, 1, 4096, 65536, 196608])
def test_real_age_roundtrip_retains_semantic_limits(official_age, tmp_path, body_size):
    raw = package((BODY * ((body_size + len(BODY) - 1) // len(BODY)))[:body_size])
    before = descriptor_inventory()
    with encryption.encrypted_backup_archive(
        io.BytesIO(raw), official_age.public, temporary_directory=tmp_path,
    ) as encrypted:
        cipher = encrypted.stream.read()
        assert len(cipher) == encryption._cipher_size(len(raw))
        assert encrypted.report.authenticated_decryption is False
        with encryption.decrypted_backup_archive(
            io.BytesIO(cipher), official_age.identity, temporary_directory=tmp_path,
        ) as restored:
            assert restored.report.authenticated_decryption is True
            report = restored.report.archive_report
            assert report.checked_archive_sha256 == hashlib.sha256(raw).hexdigest()
            require(hashlib.sha256(restored.stream.read()).digest() == hashlib.sha256(raw).digest())
            assert all(getattr(report, name) == "not_checked" for name in UNCHECKED)
            assert report.restoration_ready is False
            assert list(tmp_path.iterdir()) == []
    assert descriptor_inventory() == before and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", [
    "wrong_key", "bad_checksum", "header_cut", "nonce_cut", "middle_cut", "final_cut",
    "append", "concat", "header_flip", "body_flip", "final_flip",
])
def test_real_age_rejects_damage_without_yield_or_anonymous_leak(official_age, tmp_path, mutation):
    raw = package(BODY * 800)
    with encryption.encrypted_backup_archive(
        io.BytesIO(raw), official_age.public, temporary_directory=tmp_path,
    ) as encrypted:
        cipher = encrypted.stream.read()
    identity = official_age.identity
    if mutation == "wrong_key":
        identity = official_age.other_identity
    elif mutation == "bad_checksum":
        identity = IDENTITY_SHAPE
    elif mutation == "header_cut":
        cipher = cipher[:100]
    elif mutation == "nonce_cut":
        cipher = cipher[:175]
    elif mutation == "middle_cut":
        cipher = cipher[:len(cipher) // 2]
    elif mutation == "final_cut":
        cipher = cipher[:-1]
    elif mutation == "append":
        cipher += b"x"
    elif mutation == "concat":
        cipher += cipher
    else:
        position = {"header_flip": 100, "body_flip": 200, "final_flip": len(cipher) - 1}[mutation]
        cipher = cipher[:position] + bytes([cipher[position] ^ 1]) + cipher[position + 1:]
    before = descriptor_inventory()
    source = io.BytesIO(cipher)
    safe_failure(encryption.decrypted_backup_archive(
        source, identity, temporary_directory=tmp_path,
    ))
    assert not source.closed and list(tmp_path.iterdir()) == []
    assert descriptor_inventory() == before


def test_real_age_rejects_public_checksum_before_yield(official_age, tmp_path):
    before = descriptor_inventory()
    safe_failure(encryption.encrypted_backup_archive(io.BytesIO(package()), PUBLIC_SHAPE,
                                                    temporary_directory=tmp_path))
    assert descriptor_inventory() == before and list(tmp_path.iterdir()) == []


def test_real_age_runs_normally_on_non_main_thread(official_age, tmp_path):
    raw = package()

    def work():
        with encryption.encrypted_backup_archive(
            io.BytesIO(raw), official_age.public, temporary_directory=tmp_path,
        ) as encrypted, encryption.decrypted_backup_archive(
            encrypted.stream, official_age.identity, temporary_directory=tmp_path,
        ) as restored:
            return restored.report.archive_report.checked_archive_sha256

    before = descriptor_inventory()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(work).result(timeout=20) == hashlib.sha256(raw).hexdigest()
    assert descriptor_inventory() == before and list(tmp_path.iterdir()) == []


def test_real_age_all_standard_descriptors_closed_before_staging(official_age, tmp_path):
    """A real subprocess confines intentional fd 0/1/2 closure to its own process."""
    raw = package()
    script = '''
import base64
import hashlib
import io
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
from open_node.services import backup_encryption as encryption
values = json.loads(sys.stdin.buffer.read(32769))
source = io.BytesIO(base64.b64decode(values["source"]))
identity = values["identity"].encode("ascii")
encryption.AGE_BINARY_PATH = sys.argv[2]
for descriptor in (0, 1, 2):
    os.close(descriptor)
try:
    with encryption.encrypted_backup_archive(
        source, values["public"], temporary_directory=sys.argv[3],
    ) as encrypted, encryption.decrypted_backup_archive(
        encrypted.stream, identity, temporary_directory=sys.argv[3],
    ) as decrypted:
        assert encrypted.stream.fileno() >= 3 and decrypted.stream.fileno() >= 3
        assert decrypted.stream.tell() == os.lseek(decrypted.stream.fileno(), 0, os.SEEK_CUR) == 0
        assert hashlib.sha256(decrypted.stream.read()).hexdigest() == values["source_sha256"]
    assert encrypted.stream.closed and decrypted.stream.closed and not source.closed
    assert not os.listdir(sys.argv[3])
except BaseException:
    os._exit(2)
os._exit(0)
'''
    payload = json.dumps({
        "source": base64.b64encode(raw).decode("ascii"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "identity": official_age.identity.decode("ascii"), "public": official_age.public,
    }).encode()
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(APP_ROOT),
         str(official_age.binary), str(tmp_path)],
        input=payload, capture_output=True, env={},
        timeout=40, check=False, start_new_session=True,
    )
    require(result.returncode == 0, "Official roundtrip with closed standard descriptors failed")
    require(not result.stdout and not result.stderr, "Closed-stdio fixture must not emit secrets")
    assert list(tmp_path.iterdir()) == []
