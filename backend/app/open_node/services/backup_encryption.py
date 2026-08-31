"""Bounded, private staging around a pinned official age executable.

Only native single-recipient X25519 age/v1 files containing the existing backup
v1 ZIP format are supported. This module does not implement cryptography, read
application configuration, take a snapshot, publish files, or restore data.

The caller owns a completed, exclusively held, seekable binary source. Its
cursor can move but it is never closed. Each copy/hash/check phase has a bounded
between-I/O deadline; an uncooperative blocking source cannot be interrupted by
that soft deadline. The separate child process deadline does kill and reap a
running age process. This synchronous API does not cancel an independently
running thread when an asyncio.to_thread caller is cancelled.

All staged files are anonymous, unbuffered and private. Plaintext is yielded
only after age exits successfully AND independent ZIP validation succeeds.
Key bytes may remain in Python's heap; secure memory erasure is not promised.
"""

import base64
import hashlib
import io
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from typing import BinaryIO, Literal, NoReturn

from open_node.domain.backup import BackupValidationError
from open_node.services.backup_validation import (
    MAX_ARCHIVE_BYTES,
    MAX_IO_OPERATIONS,
    MAX_VALIDATION_SECONDS,
    READ_CHUNK_BYTES,
    BackupArchiveReport,
    validate_backup_archive,
)

AGE_BINARY_PATH = "/usr/local/bin/age"
AGE_PROCESS_TIMEOUT_SECONDS = 30.0
AGE_PROCESS_CPU_SECONDS = 30
AGE_HEADER_BYTES = 168
AGE_NONCE_BYTES = 16
AGE_TAG_BYTES = 16
AGE_CHUNK_BYTES = 65536
MAX_IDENTITY_BYTES = 4096
MAX_ENCRYPTED_ARCHIVE_BYTES = (
    MAX_ARCHIVE_BYTES + AGE_HEADER_BYTES + AGE_NONCE_BYTES
    + AGE_TAG_BYTES * ((MAX_ARCHIVE_BYTES + AGE_CHUNK_BYTES - 1) // AGE_CHUNK_BYTES)
)
_INTRO = b"age-encryption.org/v1\n"
_BECH32_LOWER = frozenset("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
_BECH32_UPPER = frozenset("QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L")
_AGE_BINARIES = {
    "x86_64": (
        62, 6977014, "eb7dd1b518f0a307c99cd97782623c5321da049154b04acd2d98d21aa7bc9b2c",
    ),
    "aarch64": (
        183, 6540637, "41b072352f4561018949623c674d16ef704019b9108a9bbdbd21292efebfc94f",
    ),
}

# This fixed standard-library-only launcher avoids preexec_fn in a threaded
# server. Arguments contain only a mode, descriptor numbers, limits and file
# identities, never a recipient, secret key, payload, or caller's host path.
# -I -S -B also prevents config/user-site imports and bytecode writes.
_AGE_LAUNCHER = r"""
import fcntl
import os
import resource
import stat
import sys

try:
    mode = sys.argv[1]
    values = tuple(int(value) for value in sys.argv[2:])
    if mode not in ('encrypt', 'decrypt') or len(values) != 18:
        os._exit(125)
    binary, key, limit, cpu, *identities = values
    identities = tuple(identities)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    for fd, access, index in ((0, os.O_RDONLY, 0), (1, os.O_WRONLY, 2), (key, os.O_RDONLY, 4)):
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != identities[index:index + 2]:
            os._exit(125)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 0
                or current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o600
                or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access
                or os.lseek(fd, 0, os.SEEK_CUR) != 0):
            os._exit(125)
    current = os.fstat(binary)
    if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
            current.st_ctime_ns, current.st_mode, current.st_uid, current.st_gid) != identities[6:]:
        os._exit(125)
    if (not stat.S_ISREG(current.st_mode) or current.st_mode & 0o7022
            or current.st_uid not in (0, os.geteuid())
            or fcntl.fcntl(binary, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
        os._exit(125)
    os.set_inheritable(binary, False)
    key_flag = '--recipients-file' if mode == 'encrypt' else '--identity'
    os.execve(binary, ['/usr/local/bin/age', '--' + mode, key_flag,
                       '/proc/self/fd/' + str(key)], {})
except BaseException:
    os._exit(125)
"""


@dataclass(frozen=True, slots=True)
class BackupEncryptionReport:
    archive_report: BackupArchiveReport = field(repr=False)
    encrypted_size: int
    encrypted_sha256: str
    authenticated_decryption: bool
    encryption: Literal["age-v1-x25519"] = field(default="age-v1-x25519", init=False)


@dataclass(frozen=True, slots=True)
class StagedBackup:
    stream: BinaryIO = field(repr=False)
    report: BackupEncryptionReport


def _invalid() -> NoReturn:
    raise BackupValidationError() from None


class _Budget:
    def __init__(self, *, read_limit: int, write_limit: int = 0):
        self.deadline = time.monotonic() + MAX_VALIDATION_SECONDS
        self.operations = self.read = self.written = 0
        self.read_limit, self.write_limit = read_limit, write_limit

    def check(self) -> None:
        if time.monotonic() > self.deadline or self.operations > MAX_IO_OPERATIONS:
            _invalid()

    def call(self, method, *arguments):
        self.operations += 1
        self.check()
        result = method(*arguments)
        self.check()
        return result

    def read_from(self, source, size: int) -> bytes:
        if not 0 < size <= READ_CHUNK_BYTES:
            _invalid()
        result = self.call(source.read, size)
        if type(result) is not bytes or len(result) > size:
            _invalid()
        self.read += len(result)
        if self.read > self.read_limit:
            _invalid()
        return result


def _position(value, expected: int) -> None:
    if type(value) is not int or value != expected:
        _invalid()


def _platform() -> tuple[int, int, str]:
    if sys.platform != "linux" or os.execve not in os.supports_fd:
        _invalid()
    import fcntl

    if not getattr(fcntl, "F_DUPFD_CLOEXEC", 0):
        _invalid()
    for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_DIRECTORY"):
        if not getattr(os, name, 0):
            _invalid()
    selected = _AGE_BINARIES.get(os.uname().machine)
    if selected is None:
        _invalid()
    return selected


@contextmanager
def _resources() -> Iterator[ExitStack]:
    stack = ExitStack()
    try:
        yield stack
    except BaseException:
        # Preserve cancellation/the initial failure while still closing every
        # registered resource; ExitStack attempts all callbacks on close errors.
        with suppress(Exception):
            stack.close()
        raise
    else:
        stack.close()


def _owned_fd(stack: ExitStack, fd: int) -> int:
    import fcntl

    if type(fd) is not int or fd < 0:
        _invalid()
    # A caller may have closed a standard descriptor. Such a newly allocated
    # key/binary descriptor must not be overwritten by Popen's stdin/stdout dup2.
    if fd < 3:
        old = fd
        try:
            fd = fcntl.fcntl(old, fcntl.F_DUPFD_CLOEXEC, 3)
            stack.callback(os.close, fd)
        finally:
            os.close(old)
    else:
        stack.callback(os.close, fd)
    os.set_inheritable(fd, False)
    return fd


def _directory(stack: ExitStack, value) -> str:
    path = os.fspath(value)
    if type(path) is not str or not path or "\x00" in path:
        _invalid()
    fd = _owned_fd(stack, os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
    ))
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        _invalid()
    # Hold the chosen directory across rename/replacement, rather than resolving
    # its caller-provided path again for each temporary file.
    return f"/proc/self/fd/{fd}"


def _private_stat(fd: int):
    current = os.fstat(fd)
    if (
        not stat.S_ISREG(current.st_mode) or current.st_nlink != 0
        or current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o600
    ):
        _invalid()
    return current


def _stage(stack: ExitStack, directory: str) -> BinaryIO:
    stream = stack.enter_context(tempfile.TemporaryFile(mode="w+b", buffering=0, dir=directory))
    if not isinstance(stream, io.FileIO):
        _invalid()
    if stream.fileno() < 3:
        import fcntl

        with _resources() as transfer:
            fd = _owned_fd(transfer, fcntl.fcntl(stream.fileno(), fcntl.F_DUPFD_CLOEXEC, 3))
            replacement = os.fdopen(fd, "w+b", buffering=0)
            transfer.pop_all()
        replacement = stack.enter_context(replacement)
        stream.close()
        stream = replacement
    if _private_stat(stream.fileno()).st_size != 0:
        _invalid()
    return stream


def _stat_identity(value) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        value.st_mode, value.st_uid, value.st_gid, value.st_nlink,
    )


def _source_stat(source, budget: _Budget):
    try:
        fd = budget.call(source.fileno)
    except (AttributeError, io.UnsupportedOperation):
        return None
    if type(fd) is not int or fd < 0:
        _invalid()
    current = budget.call(os.fstat, fd)
    if not stat.S_ISREG(current.st_mode):
        _invalid()
    return fd, _stat_identity(current)


def _write_all(destination, data: bytes, budget: _Budget, position: int) -> int:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        block = view[offset:offset + READ_CHUNK_BYTES]
        count = budget.call(destination.write, block)
        if type(count) is not int or not 0 < count <= len(block):
            _invalid()
        offset += count
        position += count
        budget.written += count
        if budget.written > budget.write_limit:
            _invalid()
        _position(budget.call(destination.tell), position)
    return position


def _copy(source: BinaryIO, destination: BinaryIO, maximum: int, minimum: int) -> tuple[int, str]:
    budget = _Budget(read_limit=maximum + 1, write_limit=maximum)
    if budget.call(source.readable) is not True or budget.call(source.seekable) is not True:
        _invalid()
    size = budget.call(source.seek, 0, io.SEEK_END)
    if type(size) is not int or not minimum <= size <= maximum:
        _invalid()
    _position(budget.call(source.tell), size)
    original = _source_stat(source, budget)
    if original is not None and original[1][2] != size:
        _invalid()
    _position(budget.call(source.seek, 0, io.SEEK_SET), 0)
    _position(budget.call(source.tell), 0)
    _position(budget.call(destination.seek, 0, io.SEEK_END), 0)
    _position(budget.call(destination.tell), 0)
    total = 0
    digest = hashlib.sha256()
    while total < size:
        block = budget.read_from(source, min(READ_CHUNK_BYTES, size - total))
        if not block:
            _invalid()
        digest.update(block)
        total = _write_all(destination, block, budget, total)
        _position(budget.call(source.tell), total)
    if budget.read_from(source, 1):
        _invalid()
    _position(budget.call(source.tell), size)
    _position(budget.call(source.seek, 0, io.SEEK_END), size)
    _position(budget.call(source.tell), size)
    if original is not None:
        if _stat_identity(budget.call(os.fstat, original[0])) != original[1]:
            _invalid()
        if _source_stat(source, budget) != original:
            _invalid()
    budget.call(destination.flush)
    if budget.call(os.fstat, destination.fileno()).st_size != size:
        _invalid()
    return size, digest.hexdigest()


def _recipient(value: str) -> bytes:
    if (
        type(value) is not str or len(value) != 62 or not value.startswith("age1")
        or any(char not in _BECH32_LOWER for char in value[4:])
    ):
        _invalid()
    return value.encode("ascii") + b"\n"


def _identity(value: bytes) -> bytes:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_IDENTITY_BYTES:
        _invalid()
    canonical_lines = value.replace(b"\r\n", b"\n")
    if any(byte != 10 and not 32 <= byte <= 126 for byte in canonical_lines):
        _invalid()
    keys = []
    for line in canonical_lines.split(b"\n"):
        if not line or line.startswith(b"#"):
            continue
        if (
            len(line) != 74 or not line.startswith(b"AGE-SECRET-KEY-1")
            or any(chr(char) not in _BECH32_UPPER for char in line[16:])
        ):
            _invalid()
        keys.append(line)
        if len(keys) > 1:
            _invalid()
    if len(keys) != 1:
        _invalid()
    return keys[0] + b"\n"


def _key_stage(stack: ExitStack, directory: str, data: bytes) -> BinaryIO:
    key = _stage(stack, directory)
    budget = _Budget(read_limit=0, write_limit=75)
    _write_all(key, data, budget, 0)
    budget.call(key.flush)
    return key


def _rewind(stream: BinaryIO) -> None:
    stream.flush()
    _position(stream.seek(0), 0)
    _position(stream.tell(), 0)
    _position(os.lseek(stream.fileno(), 0, os.SEEK_CUR), 0)


def _stage_fd(stack: ExitStack, stream: BinaryIO, access: int) -> int:
    import fcntl

    _rewind(stream)
    original = _private_stat(stream.fileno())
    fd = _owned_fd(stack, os.open(f"/proc/self/fd/{stream.fileno()}", access | os.O_CLOEXEC))
    current = _private_stat(fd)
    if (
        _stat_identity(current) != _stat_identity(original)
        or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access
    ):
        _invalid()
    _position(os.lseek(fd, 0, os.SEEK_CUR), 0)
    return fd


def _read_exact(source, length: int, budget: _Budget) -> bytes:
    result = bytearray()
    while len(result) < length:
        block = budget.read_from(source, min(READ_CHUNK_BYTES, length - len(result)))
        if not block:
            _invalid()
        result.extend(block)
    return bytes(result)


def _b64(value: bytes) -> None:
    if len(value) != 43:
        _invalid()
    decoded = base64.b64decode(value + b"=", validate=True)
    if len(decoded) != 32 or base64.b64encode(decoded).rstrip(b"=") != value:
        _invalid()


def _cipher_shape(source: BinaryIO, size: int) -> int:
    if type(size) is not int or not AGE_HEADER_BYTES + 32 <= size <= MAX_ENCRYPTED_ARCHIVE_BYTES:
        _invalid()
    budget = _Budget(read_limit=AGE_HEADER_BYTES)
    _position(budget.call(source.seek, 0), 0)
    header = _read_exact(source, AGE_HEADER_BYTES, budget)
    lines = header.split(b"\n")
    if (
        len(lines) != 5 or lines[0] + b"\n" != _INTRO or lines[4]
        or not lines[1].startswith(b"-> X25519 ") or not lines[3].startswith(b"--- ")
    ):
        _invalid()
    _b64(lines[1][10:])
    _b64(lines[2])
    _b64(lines[3][4:])
    payload = size - AGE_HEADER_BYTES - AGE_NONCE_BYTES
    chunks = (payload + AGE_CHUNK_BYTES + AGE_TAG_BYTES - 1) // (AGE_CHUNK_BYTES + AGE_TAG_BYTES)
    plaintext = payload - chunks * AGE_TAG_BYTES
    expected_chunks = max(1, (plaintext + AGE_CHUNK_BYTES - 1) // AGE_CHUNK_BYTES)
    if chunks != expected_chunks or not 22 <= plaintext <= MAX_ARCHIVE_BYTES:
        _invalid()
    budget.check()
    return plaintext


def _cipher_size(plaintext_size: int) -> int:
    return (
        plaintext_size + AGE_HEADER_BYTES + AGE_NONCE_BYTES
        + AGE_TAG_BYTES * max(1, (plaintext_size + AGE_CHUNK_BYTES - 1) // AGE_CHUNK_BYTES)
    )


def _hash_stage(source: BinaryIO, expected_size: int) -> str:
    budget = _Budget(read_limit=expected_size + 1)
    original = _stat_identity(budget.call(os.fstat, source.fileno()))
    if original[2] != expected_size:
        _invalid()
    _position(budget.call(source.seek, 0), 0)
    digest = hashlib.sha256()
    count = 0
    while count < expected_size:
        block = budget.read_from(source, min(READ_CHUNK_BYTES, expected_size - count))
        if not block:
            _invalid()
        count += len(block)
        _position(budget.call(source.tell), count)
        digest.update(block)
    if budget.read_from(source, 1):
        _invalid()
    if _stat_identity(budget.call(os.fstat, source.fileno())) != original:
        _invalid()
    return digest.hexdigest()


def _open_age_binary(stack: ExitStack) -> tuple[int, tuple[int, ...]]:
    machine, size, expected_digest = _platform()
    fd = _owned_fd(stack, os.open(
        AGE_BINARY_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
    ))
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_mode & 0o7022
        or before.st_uid not in (0, os.geteuid()) or before.st_size != size
    ):
        _invalid()
    budget = _Budget(read_limit=size + 1)
    digest = hashlib.sha256()
    first = b""
    count = 0
    while count < size:
        block = budget.call(os.read, fd, min(READ_CHUNK_BYTES, size - count))
        if (
            type(block) is not bytes or not block
            or len(block) > min(READ_CHUNK_BYTES, size - count)
        ):
            _invalid()
        if len(first) < 64:
            first += block[:64 - len(first)]
        count += len(block)
        budget.read += len(block)
        if budget.read > budget.read_limit:
            _invalid()
        digest.update(block)
    if budget.call(os.read, fd, 1):
        _invalid()
    if (
        len(first) != 64 or first[:7] != b"\x7fELF\x02\x01\x01"
        or int.from_bytes(first[18:20], "little") != machine
        or digest.hexdigest() != expected_digest
        or _stat_identity(budget.call(os.fstat, fd)) != _stat_identity(before)
    ):
        _invalid()
    # The held, verified ELF is executed by descriptor, never by resolving the
    # pathname a second time. The launcher rechecks its identity after fork.
    return fd, (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
        before.st_ctime_ns, before.st_mode, before.st_uid, before.st_gid,
    )


def _run_age(*, operation: str, stdin_fd: int, stdout_fd: int, key_fd: int,
             expected_output_size: int) -> None:
    import fcntl

    if operation not in {"encrypt", "decrypt"} or type(expected_output_size) is not int:
        _invalid()
    if not 22 <= expected_output_size <= MAX_ENCRYPTED_ARCHIVE_BYTES:
        _invalid()
    descriptors = (stdin_fd, stdout_fd, key_fd)
    if any(type(fd) is not int or fd < 3 for fd in descriptors) or len(set(descriptors)) != 3:
        _invalid()
    identities = []
    for fd, access in zip(descriptors, (os.O_RDONLY, os.O_WRONLY, os.O_RDONLY), strict=True):
        current = _private_stat(fd)
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != access:
            _invalid()
        _position(os.lseek(fd, 0, os.SEEK_CUR), 0)
        identities.extend((current.st_dev, current.st_ino))
    if len(set(zip(identities[::2], identities[1::2], strict=True))) != 3:
        _invalid()
    with _resources() as stack:
        binary_fd, binary_identity = _open_age_binary(stack)
        arguments = (
            binary_fd, key_fd, expected_output_size, AGE_PROCESS_CPU_SECONDS,
            *identities, *binary_identity,
        )
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", _AGE_LAUNCHER, operation,
             *(str(value) for value in arguments)],
            stdin=stdin_fd, stdout=stdout_fd, stderr=subprocess.DEVNULL,
            env={}, cwd="/", close_fds=True, pass_fds=(binary_fd, key_fd), start_new_session=True,
        )
        try:
            if process.wait(timeout=AGE_PROCESS_TIMEOUT_SECONDS) != 0:
                _invalid()
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                # wait() after kill is required to reap the actual child, not a
                # promise of a hard deadline for an uninterruptible kernel I/O.
                process.wait()


def _invoke(operation: str, source: BinaryIO, output: BinaryIO,
            key: BinaryIO, expected_size: int) -> None:
    # These reopened descriptions are independent from the caller and parent
    # stage objects. Child stdin/key are read-only; child stdout is write-only.
    with _resources() as descriptors:
        input_fd = _stage_fd(descriptors, source, os.O_RDONLY)
        output_fd = _stage_fd(descriptors, output, os.O_WRONLY)
        key_fd = _stage_fd(descriptors, key, os.O_RDONLY)
        _run_age(operation=operation, stdin_fd=input_fd, stdout_fd=output_fd,
                 key_fd=key_fd, expected_output_size=expected_size)
    if _private_stat(output.fileno()).st_size != expected_size:
        _invalid()


def _read_only_stream(stack: ExitStack, source: BinaryIO) -> BinaryIO:
    # Transfer this fd's ownership to FileIO exactly once; no os.close callback
    # remains to accidentally close a later descriptor with the same number.
    with _resources() as transfer:
        fd = _stage_fd(transfer, source, os.O_RDONLY)
        stream = os.fdopen(fd, "rb", buffering=0)
        transfer.pop_all()
    return stack.enter_context(stream)


@contextmanager
def encrypted_backup_archive(
    source: BinaryIO, recipient: str, *, temporary_directory="/tmp",
) -> Iterator[StagedBackup]:
    """Yield only a complete ciphertext for an independently checked private ZIP."""
    try:
        _platform()
        key_bytes = _recipient(recipient)
        with _resources() as stack:
            directory = _directory(stack, temporary_directory)
            plaintext = _stage(stack, directory)
            size, digest = _copy(source, plaintext, MAX_ARCHIVE_BYTES, 22)
            archive_report = validate_backup_archive(plaintext)
            if (
                archive_report.archive_size != size
                or archive_report.checked_archive_sha256 != digest
            ):
                _invalid()
            key = _key_stage(stack, directory, key_bytes)
            ciphertext = _stage(stack, directory)
            expected = _cipher_size(size)
            _invoke("encrypt", plaintext, ciphertext, key, expected)
            if _cipher_shape(ciphertext, expected) != size:
                _invalid()
            encrypted_digest = _hash_stage(ciphertext, expected)
            report = BackupEncryptionReport(
                archive_report=archive_report, encrypted_size=expected,
                encrypted_sha256=encrypted_digest, authenticated_decryption=False,
            )
            yield StagedBackup(stream=_read_only_stream(stack, ciphertext), report=report)
    except BackupValidationError:
        raise
    except Exception:
        raise BackupValidationError() from None


@contextmanager
def decrypted_backup_archive(
    source: BinaryIO, identity: bytes, *, temporary_directory="/tmp",
) -> Iterator[StagedBackup]:
    """Authenticate all ciphertext and revalidate its ZIP before yielding plaintext.

    A successful age decryption verifies envelope integrity, not the sender's
    identity, database health, key pairing, snapshot consistency or restorability.
    """
    try:
        _platform()
        key_bytes = _identity(identity)
        with _resources() as stack:
            directory = _directory(stack, temporary_directory)
            ciphertext = _stage(stack, directory)
            size, digest = _copy(
                source, ciphertext, MAX_ENCRYPTED_ARCHIVE_BYTES, AGE_HEADER_BYTES + 32,
            )
            expected = _cipher_shape(ciphertext, size)
            key = _key_stage(stack, directory, key_bytes)
            plaintext = _stage(stack, directory)
            _invoke("decrypt", ciphertext, plaintext, key, expected)
            archive_report = validate_backup_archive(plaintext)
            if archive_report.archive_size != expected:
                _invalid()
            report = BackupEncryptionReport(
                archive_report=archive_report, encrypted_size=size,
                encrypted_sha256=digest, authenticated_decryption=True,
            )
            yield StagedBackup(stream=_read_only_stream(stack, plaintext), report=report)
    except BackupValidationError:
        raise
    except Exception:
        raise BackupValidationError() from None
