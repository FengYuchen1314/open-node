"""Independent hostile-input tests; stubbed orchestration is not crypto evidence.

The test design predates reading backup_encryption.py. Native tests require an
explicit OPEN_NODE_BACKUP_AGE_TEST_BINARY pointing to a private, pinned official
age binary. Without it, those tests are visibly skipped, never simulated.

Format/key examples: https://c2sp.org/age (native X25519, unpadded canonical base64).
All keys and payloads below are public synthetic test material, not user secrets.
"""

import asyncio
import base64
import errno
import fcntl
import hashlib
import io
import json
import os
import platform
import resource
import shutil
import signal
import stat
import subprocess
import threading
import time
import traceback
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from open_node.domain.backup import BackupValidationError
from open_node.services import backup_encryption as encryption

CANARY = "backup-encryption-private-canary-do-not-disclose"
ERROR = "Invalid backup package."
RECIPIENT = "age1zvkyg2lqzraa2lnjvqej32nkuu0ues2s82hzrye869xeexvn73equnujwj"
IDENTITY = b"AGE-SECRET-KEY-1GFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPQ4EGAEX"
# This header is framing-only in stub tests. It is NOT an authenticated file.
FRAMING_HEADER = (
    b"age-encryption.org/v1\n"
    b"-> X25519 XEl0dJ6y3C7KZkgmgWUicg63EyXJiwBJW8PdYJ/cYBE\n"
    b"qRS0AMjdjPvZ/WT08U2KL4G+PIooA3hy38SvLpvaC1E\n"
    b"--- HK2NmOBN9Dpq0Gw6xMCuhFcQlQLvZ/wQUi/2scLG75s\n"
)
OFFICIAL_HASHES = {
    "x86_64": "eb7dd1b518f0a307c99cd97782623c5321da049154b04acd2d98d21aa7bc9b2c",
    "aarch64": "41b072352f4561018949623c674d16ef704019b9108a9bbdbd21292efebfc94f",
}
UNCHECKED = (
    "source_authentication", "database_validation", "key_validation",
    "snapshot_validation", "restore_validation",
)


def package(payload: bytes = CANARY.encode(), *, declared_sha: str | None = None) -> bytes:
    manifest = json.dumps({
        "format": "open-node-control-plane-backup", "version": 1,
        "created_at": "2026-08-31T00:00:00Z",
        "source": {"git_revision": None, "image_id": None, "image_revision": None},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": dict.fromkeys(
            ("certificates", "external_subscriptions", "federation", "notifications",
             "agent_identity"),
            "unknown",
        ),
        "required_configuration": ["deployment_settings"],
        "files": [{
            "path": "data/open-node.db", "role": "database", "size": len(payload),
            "sha256": declared_sha or hashlib.sha256(payload).hexdigest(),
        }],
    }, separators=(",", ":"), ensure_ascii=False).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in (("manifest.json", manifest), ("data/open-node.db", payload)):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, content)
    return output.getvalue()


def exact_archive_size(size: int) -> bytes:
    count = size - len(package(b""))
    for _ in range(5):
        raw = package(b"Z" * count)
        if len(raw) == size:
            return raw
        count += size - len(raw)
    raise AssertionError("Synthetic ZIP length did not converge")


def framed_only(plaintext_size: int) -> bytes:
    assert len(FRAMING_HEADER) == 168
    chunks = max(1, (plaintext_size + 65535) // 65536)
    return FRAMING_HEADER + b"\x00" * 16 + b"\x93" * (plaintext_size + 16 * chunks)


class CallerSource(io.BytesIO):
    def __init__(self, raw: bytes, *, chunk: int | None = None):
        super().__init__(raw)
        self.chunk = chunk
        self.read_requests = []
        self.close_calls = 0
        self.write_calls = 0

    def read(self, size=-1):
        assert type(size) is int and 0 <= size <= 65536
        self.read_requests.append(size)
        return super().read(min(size, self.chunk) if self.chunk is not None else size)

    def write(self, _value):
        self.write_calls += 1
        raise AssertionError("Caller source must not be written")

    def truncate(self, _size=None):
        self.write_calls += 1
        raise AssertionError("Caller source must not be truncated")

    def close(self):
        self.close_calls += 1
        super().close()


def read_fd(fd: int) -> bytes:
    blocks = []
    while block := os.read(fd, 65536):
        blocks.append(block)
    return b"".join(blocks)


def write_fd(fd: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        count = os.write(fd, view[:65536])
        assert count > 0
        view = view[count:]


def live_staging_inodes(directory: Path) -> set[tuple[int, int]]:
    result = set()
    for fd in Path("/proc/self/fd").iterdir():
        try:
            target, info = os.readlink(fd), fd.stat()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if target.startswith(str(directory) + "/") and stat.S_ISREG(info.st_mode):
            result.add((info.st_dev, info.st_ino))
    return result


@pytest.fixture
def staging(tmp_path):
    path = tmp_path / CANARY
    path.mkdir(mode=0o700)
    yield path
    assert list(path.iterdir()) == []
    assert live_staging_inodes(path) == set()


def error_is_safe(caught, caplog, capsys):
    assert type(caught.value) is BackupValidationError
    assert str(caught.value) == ERROR
    assert repr(caught.value) == "BackupValidationError('Invalid backup package.')"
    rendered = "".join(traceback.format_exception(caught.value))
    assert CANARY not in rendered
    assert CANARY not in caplog.text
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


def no_child(monkeypatch):
    calls = []

    def forbidden(**kwargs):
        calls.append(kwargs["operation"])
        raise AssertionError("Invalid framing/keys must not launch age")

    monkeypatch.setattr(encryption, "_run_age", forbidden)
    return calls


def stub_runner(monkeypatch, plaintext: bytes, *, failure: BaseException | None = None):
    """Simulate only the runner boundary, explicitly NOT authentication."""
    observations = []

    def run(*, operation, stdin_fd, stdout_fd, key_fd, expected_output_size):
        assert operation in {"encrypt", "decrypt"}
        current = {"operation": operation, "owned_inodes": set()}
        for fd in (stdin_fd, stdout_fd, key_fd):
            info = os.fstat(fd)
            assert stat.S_ISREG(info.st_mode)
            assert stat.S_IMODE(info.st_mode) == 0o600
            assert info.st_uid == os.geteuid() and info.st_nlink == 0
            current["owned_inodes"].add((info.st_dev, info.st_ino))
        assert os.lseek(stdout_fd, 0, os.SEEK_CUR) == 0
        incoming, key = read_fd(stdin_fd), read_fd(key_fd)
        current.update(incoming=incoming, key=key, output_limit=expected_output_size)
        observations.append(current)
        if failure is not None:
            write_fd(stdout_fd, b"partially-written-" + CANARY.encode())
            raise failure
        outgoing = framed_only(len(plaintext)) if operation == "encrypt" else plaintext
        assert expected_output_size >= len(outgoing)
        write_fd(stdout_fd, outgoing)

    monkeypatch.setattr(encryption, "_run_age", run)
    return observations


@pytest.mark.parametrize("recipient", [
    None, b"not-a-string", 17, True, "", " ", RECIPIENT.upper(),
    " " + RECIPIENT, RECIPIENT + " ", RECIPIENT + "\n", RECIPIENT + "\r",
    RECIPIENT + "\t", RECIPIENT + "\x00", "\ufeff" + RECIPIENT,
    RECIPIENT[:-1], RECIPIENT + "q",
    "ssh-ed25519 AAAA", "ssh-rsa AAAA", "age-plugin-test",
    "age1pq1" + "q" * 2000, "x" * 100000,
], ids=lambda value: "invalid-" + type(value).__name__)
def test_stub_invalid_recipients_never_launch_age(
    recipient, monkeypatch, staging, caplog, capsys,
):
    calls = no_child(monkeypatch)
    source = CallerSource(package())
    with pytest.raises(BackupValidationError) as caught:
        with encryption.encrypted_backup_archive(
            source, recipient, temporary_directory=str(staging),
        ):
            pytest.fail("Invalid recipient was accepted")
    assert calls == []
    assert not source.closed and source.close_calls == source.write_calls == 0
    error_is_safe(caught, caplog, capsys)


def maximum_identity() -> bytes:
    return b"#" + b"x" * (4096 - len(IDENTITY) - 3) + b"\n" + IDENTITY + b"\n"


@pytest.mark.parametrize("identity", [
    None, "", bytearray(IDENTITY), memoryview(IDENTITY), 42, b"", b"\n\r\n",
    b"# comment only\n", IDENTITY.lower(), b" " + IDENTITY, IDENTITY + b" ",
    IDENTITY + b"\r", b"\t" + IDENTITY, IDENTITY + b"\x00", IDENTITY + b"\x7f",
    b"# tab\tcomment\n" + IDENTITY, b"# lone\rcarriage\n" + IDENTITY,
    b"# non-ascii \xc3\xa9\n" + IDENTITY, b"\xef\xbb\xbf" + IDENTITY,
    IDENTITY + b"\n" + IDENTITY, IDENTITY + b"\n# note\n" + IDENTITY,
    b" \n" + IDENTITY, IDENTITY[:-1], IDENTITY + b"Q",
    b"AGE-SECRET-KEY-PQ-1" + b"Q" * 58,
    b"-----BEGIN AGE ENCRYPTED FILE-----\n" + IDENTITY,
    maximum_identity() + b"\n", b"#" + b"x" * 100000 + b"\n" + IDENTITY,
], ids=lambda value: "invalid-" + type(value).__name__)
def test_stub_invalid_identities_never_launch_age(
    identity, monkeypatch, staging, caplog, capsys,
):
    calls = no_child(monkeypatch)
    source = CallerSource(framed_only(len(package())))
    with pytest.raises(BackupValidationError) as caught:
        with encryption.decrypted_backup_archive(
            source, identity, temporary_directory=str(staging),
        ):
            pytest.fail("Invalid identity was accepted")
    assert calls == []
    assert not source.closed and source.close_calls == source.write_calls == 0
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("identity", [
    IDENTITY, IDENTITY + b"\n", IDENTITY + b"\r\n",
    b"\n\r\n# created: synthetic test only\r\n# public key: "
    + RECIPIENT.encode() + b"\r\n\r\n" + IDENTITY + b"\r\n\n",
    maximum_identity(),
], ids=["bare", "lf", "crlf", "keygen-comments", "maximum-4096"])
def test_stub_identity_comments_are_removed_before_key_fd(
    identity, monkeypatch, staging,
):
    plaintext = package()
    observations = stub_runner(monkeypatch, plaintext)
    with encryption.decrypted_backup_archive(
        CallerSource(framed_only(len(plaintext))), identity,
        temporary_directory=str(staging),
    ) as staged:
        assert staged.stream.read() == plaintext
    assert observations[0]["key"].splitlines() == [IDENTITY]


def malformed_headers():
    lines = FRAMING_HEADER.splitlines(keepends=True)
    yield "armor", b"-----BEGIN AGE ENCRYPTED FILE-----\n" + FRAMING_HEADER
    yield "leading-nul", b"\0" + FRAMING_HEADER
    yield "leading-space", b" " + FRAMING_HEADER
    yield "version-two", FRAMING_HEADER.replace(b"/v1\n", b"/v2\n", 1)
    yield "crlf", FRAMING_HEADER.replace(b"\n", b"\r\n")
    yield "lower-type", FRAMING_HEADER.replace(b"X25519", b"x25519", 1)
    yield "tab-separator", FRAMING_HEADER.replace(b"-> ", b"->\t", 1)
    yield "extra-argument", b"".join((lines[0], lines[1][:-1] + b" extra\n", *lines[2:]))
    yield "no-stanza", lines[0] + lines[3]
    yield "two-stanzas", b"".join((lines[0], lines[1], lines[2], lines[1], *lines[2:]))
    yield "empty-wrap", b"".join((lines[0], lines[1], b"\n", lines[3]))
    yield "extra-empty-line", b"".join((lines[0], lines[1], lines[2], b"\n", lines[3]))
    for kind in (b"scrypt", b"ssh-ed25519", b"ssh-rsa", b"mlkem768x25519", b"plugin"):
        yield "unsupported-" + kind.decode(), FRAMING_HEADER.replace(b"X25519", kind, 1)
    for index in (1, 2, 3):
        line = lines[index]
        part = line.rstrip(b"\n").split(b" ")[-1]
        for label, changed in (
            ("padding", part + b"="),
            ("url-safe", b"_" + part[1:]),
            ("short", part[:-1]),
            ("long", part + b"A"),
        ):
            altered = list(lines)
            altered[index] = line.replace(part, changed)
            yield f"field-{index}-{label}", b"".join(altered)
        alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        last = alphabet.index(part[-1])
        assert last % 4 == 0
        for low_bits in (1, 2, 3):
            alias = part[:-1] + bytes([alphabet[last + low_bits]])
            assert base64.b64decode(alias + b"=") == base64.b64decode(part + b"=")
            altered = list(lines)
            altered[index] = line.replace(part, alias)
            yield f"field-{index}-noncanonical-padbits-{low_bits}", b"".join(altered)


@pytest.mark.parametrize(("label", "header"), list(malformed_headers()), ids=lambda value: (
    value if isinstance(value, str) else "header"
))
def test_stub_hostile_headers_fail_before_runner(
    label, header, monkeypatch, staging, caplog, capsys,
):
    calls = no_child(monkeypatch)
    source = CallerSource(header + b"\x00" * 1024, chunk=7)
    with pytest.raises(BackupValidationError) as caught:
        with encryption.decrypted_backup_archive(
            source, IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail(label)
    assert calls == []
    assert not source.closed and source.close_calls == source.write_calls == 0
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("deleted_offset", range(168))
def test_stub_each_header_byte_deletion_is_rejected_before_runner(
    deleted_offset, monkeypatch, staging,
):
    calls = no_child(monkeypatch)
    raw = FRAMING_HEADER[:deleted_offset] + FRAMING_HEADER[deleted_offset + 1:] + b"\0" * 1024
    with pytest.raises(BackupValidationError):
        with encryption.decrypted_backup_archive(
            CallerSource(raw), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("Truncated header was accepted")
    assert calls == []


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("chunk", [1, 7, 31, 65536])
def test_stub_staging_is_raw_kernel_readonly_and_caller_offset_is_not_input_start(
    operation, chunk, monkeypatch, staging,
):
    plaintext = package()
    raw = plaintext if operation == "encrypt" else framed_only(len(plaintext))
    source = CallerSource(raw, chunk=chunk)
    source.seek(len(raw) // 2)
    observations = stub_runner(monkeypatch, plaintext)
    context = (
        encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
        if operation == "encrypt" else
        encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
    )
    with context as staged:
        stream = staged.stream
        assert isinstance(stream, io.FileIO)
        assert not stream.writable() and stream.readable() and stream.seekable()
        assert fcntl.fcntl(stream.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        info = os.fstat(stream.fileno())
        assert info.st_nlink == 0 and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid()
        with pytest.raises((io.UnsupportedOperation, OSError)):
            stream.write(b"must-not-write")
        with pytest.raises(OSError) as caught:
            os.write(stream.fileno(), b"must-not-write-through-raw-fd")
        assert caught.value.errno == errno.EBADF
        expected = framed_only(len(plaintext)) if operation == "encrypt" else plaintext
        stream.seek(0)
        assert stream.read() == expected
        stream.seek(7)
        assert stream.tell() == os.lseek(stream.fileno(), 0, os.SEEK_CUR) == 7
        report = staged.report
        assert report.encryption == "age-v1-x25519"
        assert report.authenticated_decryption is (operation == "decrypt")
        assert report.encrypted_size == len(framed_only(len(plaintext)))
        assert report.encrypted_sha256 == hashlib.sha256(framed_only(len(plaintext))).hexdigest()
        assert report.archive_report.checked_archive_sha256 == hashlib.sha256(plaintext).hexdigest()
        assert report.archive_report.restoration_ready is False
        assert all(getattr(report.archive_report, name) == "not_checked" for name in UNCHECKED)
        assert CANARY not in repr(staged) + repr(report)
    assert stream.closed
    assert observations[0]["incoming"] == raw
    assert not source.closed and source.close_calls == source.write_calls == 0
    assert source.getvalue() == raw and source.read_requests


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("failure_type", [OSError, ValueError, RuntimeError, TimeoutError])
def test_stub_partial_output_is_never_yielded_and_all_owned_fds_close(
    operation, failure_type, monkeypatch, staging, caplog, capsys,
):
    plaintext = package()
    source = CallerSource(plaintext if operation == "encrypt" else framed_only(len(plaintext)))
    observations = stub_runner(monkeypatch, plaintext, failure=failure_type(CANARY))
    with pytest.raises(BackupValidationError) as caught:
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        with context:
            pytest.fail("Failed runner exposed a partial output")
    assert len(observations) == 1
    assert live_staging_inodes(staging) == set()
    assert not source.closed and source.close_calls == source.write_calls == 0
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, GeneratorExit, asyncio.CancelledError])
def test_stub_cancellation_is_propagated_after_cleanup(
    operation, failure_type, monkeypatch, staging,
):
    plaintext = package()
    source = CallerSource(plaintext if operation == "encrypt" else framed_only(len(plaintext)))
    failure = failure_type()
    stub_runner(monkeypatch, plaintext, failure=failure)
    with pytest.raises(failure_type) as caught:
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        with context:
            pytest.fail("Cancellation exposed partial output")
    assert caught.value is failure
    assert live_staging_inodes(staging) == set()
    assert not source.closed and source.close_calls == source.write_calls == 0


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_stub_consumer_exception_closes_yielded_view(monkeypatch, staging, operation):
    plaintext = package()
    source = CallerSource(plaintext if operation == "encrypt" else framed_only(len(plaintext)))
    stub_runner(monkeypatch, plaintext)
    with pytest.raises(KeyboardInterrupt):
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        with context as staged:
            stream = staged.stream
            raise KeyboardInterrupt()
    assert stream.closed and live_staging_inodes(staging) == set()
    assert not source.closed


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_stub_opaque_body_may_contain_header_like_signatures(monkeypatch, staging, operation):
    marker = b"age-encryption.org/v1\n-> scrypt \n-----BEGIN AGE ENCRYPTED FILE-----"
    plaintext = package(marker * 4 + b"PK\x03\x04PK\x05\x06PK\x06\x06")
    ciphertext = bytearray(framed_only(len(plaintext)))
    ciphertext[184:184 + len(marker)] = marker
    raw = plaintext if operation == "encrypt" else bytes(ciphertext)
    observations = stub_runner(monkeypatch, plaintext)
    context = (
        encryption.encrypted_backup_archive(
            CallerSource(raw), RECIPIENT, temporary_directory=str(staging),
        ) if operation == "encrypt" else
        encryption.decrypted_backup_archive(
            CallerSource(raw), IDENTITY, temporary_directory=str(staging),
        )
    )
    with context as staged:
        assert staged.report.archive_report.content_hashes_verified is True
    assert observations[0]["incoming"] == raw


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("fault", [
    "none", "text", "bytearray", "memoryview", "too-long", "early-eof",
    "wrong-position", "io-error", "not-readable", "not-seekable",
])
def test_stub_invalid_binary_source_results_fail_closed(
    operation, fault, monkeypatch, staging, caplog, capsys,
):
    class InvalidSource(CallerSource):
        def readable(self):
            return 1 if fault == "not-readable" else True

        def seekable(self):
            return False if fault == "not-seekable" else True

        def read(self, size=-1):
            if fault == "io-error":
                raise OSError(CANARY)
            if fault in {"none", "text", "bytearray", "memoryview", "too-long", "early-eof"}:
                return {
                    "none": None, "text": CANARY, "bytearray": bytearray(b"x"),
                    "memoryview": memoryview(b"x"), "too-long": b"x" * (size + 1),
                    "early-eof": b"",
                }[fault]
            block = super().read(size)
            if fault == "wrong-position" and block:
                super().seek(-1, os.SEEK_CUR)
            return block

    raw = package() if operation == "encrypt" else framed_only(len(package()))
    source = InvalidSource(raw)
    calls = no_child(monkeypatch)
    with pytest.raises(BackupValidationError) as caught:
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        with context:
            pytest.fail("An invalid source read/seek contract was accepted")
    assert calls == [] and not source.closed
    assert source.close_calls == source.write_calls == 0
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
@pytest.mark.parametrize("advertised_size", [-1, True, 22.0, 1090785465, 2**63])
def test_stub_invalid_source_size_rejected_without_reading_or_launching_age(
    operation, advertised_size, monkeypatch, staging,
):
    class AdvertisedSource(CallerSource):
        def seek(self, offset, whence=os.SEEK_SET):
            actual = super().seek(offset, whence)
            return advertised_size if whence == os.SEEK_END else actual

    calls = no_child(monkeypatch)
    source = AdvertisedSource(package())
    with pytest.raises(BackupValidationError):
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        with context:
            pytest.fail("An unbounded/invalid declared source size was accepted")
    assert calls == [] and source.read_requests == []
    assert not source.closed and source.close_calls == source.write_calls == 0


@pytest.fixture
def official_age(monkeypatch):
    configured = os.environ.get("OPEN_NODE_BACKUP_AGE_TEST_BINARY")
    if not configured:
        pytest.skip("Real official age opt-in is absent; stub tests are not crypto evidence")
    binary = Path(configured)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    architecture = platform.machine()
    assert architecture in OFFICIAL_HASHES
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == OFFICIAL_HASHES[architecture]
    monkeypatch.setattr(encryption, "AGE_BINARY_PATH", str(binary))
    return binary


def reference_encrypt(binary: Path, plaintext: bytes, *, recipients=(RECIPIENT,), armor=False):
    args = [str(binary), "--encrypt"]
    if armor:
        args.append("--armor")
    for recipient in recipients:
        args.extend(("--recipient", recipient))
    result = subprocess.run(
        args, input=plaintext, capture_output=True,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        timeout=15, check=False,
    )
    assert result.returncode == 0
    return result.stdout


def native_spy(monkeypatch):
    original = encryption._run_age
    calls = []

    def observed(**kwargs):
        record = {"operation": kwargs["operation"], "returned": False}
        calls.append(record)
        try:
            original(**kwargs)
            record["returned"] = True
        finally:
            record["output_size"] = os.fstat(kwargs["stdout_fd"]).st_size

    monkeypatch.setattr(encryption, "_run_age", observed)
    return calls


@pytest.mark.parametrize("archive_size", [65535, 65536, 65537, 131071, 131072, 131073])
def test_real_official_age_roundtrip_on_exact_stream_chunk_boundaries(
    archive_size, official_age, staging,
):
    plaintext = exact_archive_size(archive_size)
    source = CallerSource(plaintext, chunk=31)
    source.seek(9)
    with encryption.encrypted_backup_archive(
        source, RECIPIENT, temporary_directory=str(staging),
    ) as encrypted:
        assert isinstance(encrypted.stream, io.FileIO) and not encrypted.stream.writable()
        encrypted.stream.seek(0)
        ciphertext = encrypted.stream.read()
        assert len(ciphertext) == 168 + 16 + archive_size + 16 * (
            (archive_size + 65535) // 65536
        )
        assert encrypted.report.authenticated_decryption is False
        assert encrypted.report.encrypted_sha256 == hashlib.sha256(ciphertext).hexdigest()
    assert not source.closed and source.close_calls == source.write_calls == 0
    source = CallerSource(ciphertext, chunk=7)
    source.seek(len(ciphertext))
    with encryption.decrypted_backup_archive(
        source, IDENTITY, temporary_directory=str(staging),
    ) as decrypted:
        decrypted.stream.seek(0)
        assert decrypted.stream.read() == plaintext
        assert decrypted.report.authenticated_decryption is True
        assert decrypted.report.archive_report.restoration_ready is False
        assert all(getattr(decrypted.report.archive_report, name) == "not_checked"
                   for name in UNCHECKED)
        assert decrypted.report.encrypted_sha256 == hashlib.sha256(ciphertext).hexdigest()
    assert not source.closed and source.close_calls == source.write_calls == 0


@pytest.mark.parametrize("where", ["ephemeral-share", "wrapped-key", "header-mac", "nonce",
                                    "first-body-byte", "first-chunk-tag", "final-tag"])
def test_real_authenticated_tampering_never_exposes_staged_plaintext(
    where, official_age, monkeypatch, staging, caplog, capsys,
):
    plaintext = exact_archive_size(131072)
    original = reference_encrypt(official_age, plaintext)
    changed = bytearray(original)
    offset = {
        "ephemeral-share": 32, "wrapped-key": 76, "header-mac": 124,
        "nonce": 168, "first-body-byte": 184, "first-chunk-tag": 184 + 65536,
        "final-tag": len(original) - 1,
    }[where]
    changed[offset] = (ord("A") if changed[offset] != ord("A") else ord("B")) if (
        offset < 168
    ) else changed[offset] ^ 1
    calls = native_spy(monkeypatch)
    with pytest.raises(BackupValidationError) as caught:
        with encryption.decrypted_backup_archive(
            CallerSource(bytes(changed)), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("Unauthenticated ciphertext yielded plaintext")
    assert len(calls) == 1 and not calls[0]["returned"]
    if where == "final-tag":
        assert calls[0]["output_size"] > 0
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("drop", [1, 15, 16, 17, 65552])
def test_real_truncation_including_loss_of_complete_final_chunk_is_rejected(
    drop, official_age, monkeypatch, staging,
):
    ciphertext = reference_encrypt(official_age, exact_archive_size(131072))
    calls = native_spy(monkeypatch)
    with pytest.raises(BackupValidationError):
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext[:-drop]), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("A file without its authenticated final chunk was accepted")
    if drop == 65552:
        assert len(calls) == 1 and not calls[0]["returned"]


@pytest.mark.parametrize("suffix", ["byte", "newline", "tag-sized", "same-file", "second-file"])
def test_real_append_and_concatenation_are_not_accepted_as_a_valid_prefix(
    suffix, official_age, staging,
):
    ciphertext = reference_encrypt(official_age, package())
    trailing = {
        "byte": b"\0", "newline": b"\n", "tag-sized": b"\x80" * 16,
        "same-file": ciphertext,
        "second-file": reference_encrypt(official_age, package(b"different synthetic database")),
    }[suffix]
    with pytest.raises(BackupValidationError):
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext + trailing), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("Trailing or concatenated ciphertext was ignored")


@pytest.mark.parametrize("kind", ["not-zip", "bad-manifest-hash", "zip-prefix", "zip-trailer"])
def test_real_age_success_is_not_enough_without_complete_zip_validation(
    kind, official_age, monkeypatch, staging, caplog, capsys,
):
    plaintext = {
        "not-zip": b"not a ZIP; " + CANARY.encode(),
        "bad-manifest-hash": package(declared_sha="0" * 64),
        "zip-prefix": b"x" + package(),
        "zip-trailer": package() + b"x",
    }[kind]
    ciphertext = reference_encrypt(official_age, plaintext)
    calls = native_spy(monkeypatch)
    with pytest.raises(BackupValidationError) as caught:
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("Authenticated but invalid ZIP content was exposed")
    assert len(calls) == 1 and calls[0]["returned"]
    assert calls[0]["output_size"] == len(plaintext)
    error_is_safe(caught, caplog, capsys)


def bech32(hrp: str, data: bytes) -> str:
    """Encode synthetic native keys; actual encryption is still done by official age."""
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    values, acc, bits = [], 0, 0
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            values.append((acc >> bits) & 31)
    if bits:
        values.append((acc << (5 - bits)) & 31)
    expanded = [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]
    checksum = 1
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    for value in expanded + values + [0] * 6:
        high = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for bit, generator in enumerate(generators):
            if (high >> bit) & 1:
                checksum ^= generator
    checksum ^= 1
    encoded = values + [(checksum >> (5 * (5 - index))) & 31 for index in range(6)]
    return hrp + "1" + "".join(alphabet[value] for value in encoded)


def alternate_keys():
    secret = b"\x23" * 32
    public = X25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return bech32("age", public), bech32("age-secret-key-", secret).upper().encode()


def test_real_canonical_but_wrong_identity_is_rejected(
    official_age, monkeypatch, staging, caplog, capsys,
):
    ciphertext = reference_encrypt(official_age, package())
    _, wrong_identity = alternate_keys()
    calls = native_spy(monkeypatch)
    with pytest.raises(BackupValidationError) as caught:
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext), wrong_identity, temporary_directory=str(staging),
        ):
            pytest.fail("A different native identity decrypted the input")
    assert len(calls) == 1 and not calls[0]["returned"]
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize(("operation", "key"), [
    ("encrypt", RECIPIENT[:-1] + "q"),
    ("encrypt", "age1" + "q" * 58),
    ("decrypt", IDENTITY[:-1] + b"Q"),
    ("decrypt", b"AGE-SECRET-KEY-1" + b"Q" * 58),
], ids=["recipient-checksum", "recipient-all-q", "identity-checksum", "identity-all-q"])
def test_real_official_age_not_a_handwritten_parser_rejects_invalid_key_checksum(
    operation, key, official_age, monkeypatch, staging, caplog, capsys,
):
    plaintext = package()
    raw = plaintext if operation == "encrypt" else reference_encrypt(official_age, plaintext)
    calls = native_spy(monkeypatch)
    with pytest.raises(BackupValidationError) as caught:
        context = (
            encryption.encrypted_backup_archive(
                CallerSource(raw), key, temporary_directory=str(staging),
            ) if operation == "encrypt" else
            encryption.decrypted_backup_archive(
                CallerSource(raw), key, temporary_directory=str(staging),
            )
        )
        with context:
            pytest.fail("A key with an invalid checksum was accepted")
    assert len(calls) == 1 and not calls[0]["returned"]
    error_is_safe(caught, caplog, capsys)


@pytest.mark.parametrize("kind", ["armor", "multiple-recipients"])
def test_real_unsupported_age_forms_are_rejected_without_decryption_child(
    kind, official_age, monkeypatch, staging,
):
    other_recipient, _ = alternate_keys()
    ciphertext = reference_encrypt(
        official_age, package(),
        recipients=(RECIPIENT, other_recipient) if kind == "multiple-recipients" else (RECIPIENT,),
        armor=kind == "armor",
    )
    calls = no_child(monkeypatch)
    with pytest.raises(BackupValidationError):
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("Unsupported genuine age form was accepted")
    assert calls == []


@pytest.mark.parametrize(("name", "digest", "prefix"), [
    ("cctv-scrypt.vector",
     "4eedf64d8e648634bfe3345bc45ee71b1cee5dab32b90211fb1c3a61a1eda15e",
     b"age-encryption.org/v1\n-> scrypt "),
    ("cctv-armor-scrypt.vector",
     "b747417cda8ce1ff0b980d7b60e8171d41236df941cbbb0d39d1aecc7f941083",
     b"-----BEGIN AGE ENCRYPTED FILE-----"),
])
def test_real_official_scrypt_vectors_are_rejected_before_a_decryption_child(
    name, digest, prefix, official_age, monkeypatch, staging,
):
    # c2sp.org/CCTV/age v0.0.0-20260829155415-4448f2097b2d, pinned by
    # official age v1.3.2's go.mod. This is a framing rejection using authentic
    # published vectors, NOT evidence of a new scrypt encryption/decryption.
    configured = os.environ.get("OPEN_NODE_BACKUP_AGE_VECTOR_DIRECTORY")
    assert configured, "Native opt-in also requires the separately pinned official vectors"
    path = Path(configured) / name
    assert path.is_absolute() and path.is_file() and not path.is_symlink()
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    preamble, separator, ciphertext = raw.partition(b"\n\n")
    assert separator and preamble.startswith(b"expect: success\n")
    assert ciphertext.startswith(prefix)
    calls = no_child(monkeypatch)
    with pytest.raises(BackupValidationError):
        with encryption.decrypted_backup_archive(
            CallerSource(ciphertext), IDENTITY, temporary_directory=str(staging),
        ):
            pytest.fail("A genuine passphrase-encrypted input reached native decryption")
    assert calls == []


@pytest.mark.parametrize("kind", [
    "symlink", "fifo", "directory", "world-writable", "setuid", "same-size-tamper", "truncated",
])
def test_real_pinned_binary_checks_reject_changed_or_nonregular_files_before_spawn(
    kind, official_age, monkeypatch, tmp_path, staging, caplog, capsys,
):
    path = tmp_path / "untrusted-age"
    if kind == "symlink":
        path.symlink_to(official_age)
    elif kind == "fifo":
        os.mkfifo(path, mode=0o600)
    elif kind == "directory":
        path.mkdir()
    else:
        shutil.copyfile(official_age, path)
        path.chmod(0o755)
        if kind == "world-writable":
            path.chmod(0o777)
        elif kind == "setuid":
            path.chmod(0o4755)
        elif kind == "same-size-tamper":
            with path.open("r+b", buffering=0) as changed:
                changed.seek(-1, os.SEEK_END)
                final = changed.read(1)
                changed.seek(-1, os.SEEK_END)
                changed.write(bytes([final[0] ^ 1]))
        elif kind == "truncated":
            with path.open("r+b", buffering=0) as changed:
                changed.truncate(path.stat().st_size - 1)
    monkeypatch.setattr(encryption, "AGE_BINARY_PATH", str(path))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Unverified executables must never launch")

    monkeypatch.setattr(encryption.subprocess, "Popen", forbidden)
    with pytest.raises(BackupValidationError) as caught:
        with encryption.encrypted_backup_archive(
            CallerSource(package()), RECIPIENT, temporary_directory=str(staging),
        ):
            pytest.fail("An unverified executable was accepted")
    assert calls == []
    error_is_safe(caught, caplog, capsys)


def stopped_native_child_probe(monkeypatch, binary: Path, inherited_fd: int, failure=None):
    """Run the real launcher and pinned age, then SIGSTOP only that owned group.

    Stopping a genuine child is controlled fault injection, not a fake timeout
    result. The public wrapper must still perform its actual wait/kill/reap.
    """
    original = subprocess.Popen
    children, observations = [], []

    def observed(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None, "Fixture missed the short-lived official age child"
                try:
                    executable = os.readlink(f"/proc/{process.pid}/exe")
                except FileNotFoundError:
                    executable = ""
                if executable == str(binary):
                    os.killpg(process.pid, signal.SIGSTOP)
                    break
                assert time.monotonic() < deadline, (
                    "Official age did not exec in the fixture window"
                )
                time.sleep(0.0005)
            for _ in range(1000):
                status = Path(f"/proc/{process.pid}/status").read_text()
                if "\nState:\tT" in status:
                    break
                time.sleep(0.001)
            assert "\nState:\tT" in status, "Owned age child did not stop"
            marker = os.fstat(inherited_fd)
            descriptor_inodes = set()
            for path in Path(f"/proc/{process.pid}/fd").iterdir():
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue
                descriptor_inodes.add((info.st_dev, info.st_ino))
            observations.append({
                "executable": executable,
                "environment": Path(f"/proc/{process.pid}/environ").read_bytes(),
                "core": resource.prlimit(process.pid, resource.RLIMIT_CORE),
                "file_size": resource.prlimit(process.pid, resource.RLIMIT_FSIZE),
                "cpu": resource.prlimit(process.pid, resource.RLIMIT_CPU),
                "inherited_marker": (marker.st_dev, marker.st_ino) in descriptor_inodes,
                "launch_environment": kwargs.get("env"),
                "start_new_session": kwargs.get("start_new_session"),
                "argv": args[0],
                "wait_timeout": None,
            })
            original_wait = process.wait
            interrupted = False

            def wait(*wait_args, **wait_kwargs):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    observations[-1]["wait_timeout"] = wait_kwargs.get("timeout")
                    if failure is not None:
                        raise failure
                return original_wait(*wait_args, **wait_kwargs)

            process.wait = wait
            return process
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise

    monkeypatch.setattr(encryption.subprocess, "Popen", observed)
    return children, observations


@pytest.mark.parametrize("cancellation", ["hard-deadline", "keyboard", "async-cancelled"])
def test_real_stopped_native_child_deadline_or_injected_cancellation_kills_and_reaps(
    cancellation, official_age, monkeypatch, tmp_path, staging, caplog, capsys,
):
    # No reduced timeout or substituted binary: the timeout case really waits 30s.
    failure = {
        "hard-deadline": None, "keyboard": KeyboardInterrupt(),
        "async-cancelled": asyncio.CancelledError(),
    }[cancellation]
    plaintext = package(b"independent child observation\n" * 65536)
    ciphertext_size = 184 + len(plaintext) + 16 * ((len(plaintext) + 65535) // 65536)
    monkeypatch.setenv("OPEN_NODE_ENCRYPTION_SECRET_CANARY", CANARY)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "must-not-be-used"))
    marker_path = tmp_path / "must-not-be-inherited"
    with marker_path.open("w+b", buffering=0) as marker:
        os.set_inheritable(marker.fileno(), True)
        children, observations = stopped_native_child_probe(
            monkeypatch, official_age, marker.fileno(), failure,
        )
        started = time.monotonic()
        try:
            expected_error = BackupValidationError if failure is None else type(failure)
            with pytest.raises(expected_error) as caught:
                with encryption.encrypted_backup_archive(
                    CallerSource(plaintext), RECIPIENT, temporary_directory=str(staging),
                ):
                    pytest.fail("Stopped/cancelled native execution yielded ciphertext")
            elapsed = time.monotonic() - started
            assert len(children) == len(observations) == 1
            child, observation = children[0], observations[0]
            assert child.returncode == -signal.SIGKILL
            assert not Path(f"/proc/{child.pid}").exists()
            assert observation["executable"] == str(official_age)
            assert observation["environment"] == b"" and observation["launch_environment"] == {}
            assert observation["core"] == (0, 0)
            assert observation["file_size"] == (ciphertext_size, ciphertext_size)
            assert observation["cpu"] == (30, 30)
            assert observation["wait_timeout"] == 30.0
            assert observation["start_new_session"] is True
            assert observation["inherited_marker"] is False
            assert CANARY not in repr(observation["argv"])
            assert RECIPIENT not in repr(observation["argv"])
            assert IDENTITY.decode() not in repr(observation["argv"])
            if failure is None:
                assert 29.5 <= elapsed < 45
                error_is_safe(caught, caplog, capsys)
            else:
                assert caught.value is failure
            assert not (tmp_path / "must-not-be-used").exists()
            assert live_staging_inodes(staging) == set()
        finally:
            # A failed assertion must not strand our deliberately stopped child.
            for child in children:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_real_caller_disk_stream_and_closed_view_fd_reuse_remain_owned_by_caller(
    operation, official_age, tmp_path, staging,
):
    plaintext = package()
    raw = plaintext if operation == "encrypt" else reference_encrypt(official_age, plaintext)
    path = tmp_path / "caller-owned-source"
    path.write_bytes(raw)
    before = path.stat()
    reused_fd = None
    with path.open("rb", buffering=0) as source:
        source.seek(17)
        context = (
            encryption.encrypted_backup_archive(source, RECIPIENT, temporary_directory=str(staging))
            if operation == "encrypt" else
            encryption.decrypted_backup_archive(source, IDENTITY, temporary_directory=str(staging))
        )
        try:
            with context as staged:
                closed_number = staged.stream.fileno()
                staged.stream.close()
                reused_fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
                assert reused_fd == closed_number, "Fixture did not reuse the just-closed view fd"
                assert stat.S_ISCHR(os.fstat(reused_fd).st_mode)
            assert stat.S_ISCHR(os.fstat(reused_fd).st_mode)
            assert not source.closed
            source.seek(0)
            assert source.read() == raw
        finally:
            if reused_fd is not None:
                os.close(reused_fd)
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    )


def test_real_roundtrip_in_a_non_main_thread(official_age, staging):
    results, failures = [], []

    def worker():
        try:
            plaintext = package()
            with encryption.encrypted_backup_archive(
                CallerSource(plaintext), RECIPIENT, temporary_directory=str(staging),
            ) as encrypted:
                encrypted.stream.seek(0)
                ciphertext = encrypted.stream.read()
            with encryption.decrypted_backup_archive(
                CallerSource(ciphertext), IDENTITY, temporary_directory=str(staging),
            ) as decrypted:
                decrypted.stream.seek(0)
                results.append(decrypted.stream.read() == plaintext)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=15)
    completed_promptly = not thread.is_alive()
    if not completed_promptly:
        # Synchronous worker cancellation is not promised; let its child deadline
        # clean it up before reporting a fixture/product failure.
        thread.join(timeout=35)
    assert completed_promptly, "Pinned age execution must not deadlock after a threaded fork"
    assert failures == [] and results == [True]
