"""Create one bounded official pg_dump custom archive for a PostgreSQL backup."""

from __future__ import annotations

import hashlib
import os
import selectors
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.engine import make_url

from open_node.domain.backup import MAX_FILE_BYTES
from open_node.services.backup_dependencies import (
    PostgresDependencySnapshot,
    capture_postgres_dependency_snapshot,
)
from open_node.services.backup_state import _directory

POSTGRES_DUMP_SECONDS = 30 * 60
POSTGRES_LIST_SECONDS = 2 * 60
POSTGRES_LIST_BYTES = 16 * 1024 * 1024
_READ_BYTES = 64 * 1024
_SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})


class BackupPostgresError(RuntimeError):
    code = "backup_postgres_unavailable"

    def __init__(self) -> None:
        super().__init__("PostgreSQL backup snapshot is unavailable.")


@dataclass(frozen=True, slots=True)
class PostgresBackupSnapshot:
    stream: BinaryIO = field(repr=False)
    size: int
    sha256: str
    schema_fingerprint: str
    dependencies: PostgresDependencySnapshot = field(repr=False)
    engine: str = field(default="postgresql", init=False)


def _connection(database_url: str) -> tuple[list[str], dict[str, str]]:
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or not url.username
        or url.password is None
        or not url.host
        or not url.database
    ):
        raise BackupPostgresError()
    sslmode = url.query.get("sslmode", "prefer")
    if sslmode not in _SSL_MODES or set(url.query) - {"sslmode"}:
        raise BackupPostgresError()
    arguments = [
        "--host", url.host,
        "--port", str(url.port or 5432),
        "--username", url.username,
        "--dbname", url.database,
    ]
    environment = os.environ.copy()
    environment.update({"PGPASSWORD": url.password, "PGSSLMODE": sslmode})
    return arguments, environment


def _dump(arguments: list[str], environment: dict[str, str], output: BinaryIO) -> None:
    process = subprocess.Popen(
        [
            "pg_dump", "--format=custom", "--compress=6",
            "--no-owner", "--no-privileges", *arguments,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        close_fds=True,
    )
    selector = selectors.DefaultSelector()
    errors = bytearray()
    total = 0
    deadline = time.monotonic() + POSTGRES_DUMP_SECONDS
    try:
        assert process.stdout is not None and process.stderr is not None
        for stream, kind in ((process.stdout, "data"), (process.stderr, "error")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackupPostgresError()
            events = selector.select(min(remaining, 1.0))
            if not events and process.poll() is not None:
                break
            for key, _mask in events:
                block = os.read(key.fileobj.fileno(), _READ_BYTES)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "error":
                    if len(errors) < 8192:
                        errors.extend(block[: 8192 - len(errors)])
                    continue
                total += len(block)
                if total > MAX_FILE_BYTES:
                    raise BackupPostgresError()
                view = memoryview(block)
                while view:
                    written = output.write(view)
                    if type(written) is not int or written <= 0:
                        raise BackupPostgresError()
                    view = view[written:]
        if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            raise BackupPostgresError()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _fingerprint(stream: BinaryIO) -> str:
    result = subprocess.run(
        ["pg_restore", "--list", f"/proc/self/fd/{stream.fileno()}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(stream.fileno(),),
        timeout=POSTGRES_LIST_SECONDS,
        check=False,
    )
    if result.returncode != 0 or not 1 <= len(result.stdout) <= POSTGRES_LIST_BYTES:
        raise BackupPostgresError()
    entries = b"\n".join(
        line for line in result.stdout.splitlines() if line and not line.startswith(b";")
    )
    if not entries:
        raise BackupPostgresError()
    return hashlib.sha256(entries).hexdigest()


@contextmanager
def postgres_backup_snapshot(
    database_url: str, *, staging_directory: Path
) -> Iterator[PostgresBackupSnapshot]:
    try:
        arguments, environment = _connection(database_url)
        with _directory(staging_directory) as staging_fd:
            with tempfile.TemporaryFile(
                mode="w+b", buffering=0, dir=f"/proc/self/fd/{staging_fd}"
            ) as output:
                os.fchmod(output.fileno(), 0o600)
                _dump(arguments, environment, output)
                output.flush()
                os.fsync(output.fileno())
                size = output.seek(0, os.SEEK_END)
                if not 5 <= size <= MAX_FILE_BYTES or output.seek(0) != 0:
                    raise BackupPostgresError()
                if output.read(5) != b"PGDMP" or output.seek(0) != 0:
                    raise BackupPostgresError()
                digest = hashlib.file_digest(output, "sha256").hexdigest()
                if output.seek(0) != 0:
                    raise BackupPostgresError()
                fingerprint = _fingerprint(output)
                dependencies = capture_postgres_dependency_snapshot(database_url)
                descriptor = os.open(
                    f"/proc/self/fd/{output.fileno()}", os.O_RDONLY | os.O_CLOEXEC
                )
                with os.fdopen(descriptor, "rb", buffering=0) as readonly:
                    if os.fstat(readonly.fileno()).st_size != size:
                        raise BackupPostgresError()
                    yield PostgresBackupSnapshot(
                        readonly, size, digest, fingerprint, dependencies
                    )
    except BackupPostgresError:
        raise
    except Exception:
        raise BackupPostgresError() from None
