"""Private file updates with a durable undo record for interrupted host operations."""

import base64
import json
import os
import stat
from pathlib import Path

from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, atomic_write


def guarded_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if ".." in path.parts or not path.is_relative_to(root) or path == root:
        raise RuntimeFailure("Path must stay inside the configured directory")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeFailure("Symlink paths are not allowed")
    if path.exists() and not path.is_file():
        raise RuntimeFailure("Expected a regular file")
    if path.exists() and path.stat().st_nlink != 1:
        raise RuntimeFailure("Hard-linked files are not allowed")
    return path


def read_private(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeFailure("Expected one regular non-hard-linked file")
        data = stream.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise RuntimeFailure("Host file exceeds 2 MiB")
    return data


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.exists():
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class FileTransaction:
    def __init__(self, record: Path, check_path, restore_intents=None):
        self.record = record
        self.check_path = check_path
        self.restore_intents = restore_intents

    def check_intents(self, intents):
        if intents is not None and (
            not isinstance(intents, dict)
            or set(intents) != {"xray", "nginx"}
            or any(type(value) is not bool for value in intents.values())
            or self.restore_intents is None
        ):
            raise RuntimeFailure("Invalid runtime intent undo record")

    def recover(self) -> None:
        guarded_path(self.record.parent, self.record)
        if not self.record.exists():
            return
        data = json.loads(read_private(self.record))
        intents = None
        if isinstance(data, dict) and data.get("schema") == 1:
            intents = data.get("intents")
            if set(data) != {"schema", "files", "intents"} or intents is None:
                raise RuntimeFailure("Invalid runtime intent undo record")
            self.check_intents(intents)
            data = data.get("files")
        if not isinstance(data, dict) or len(data) > 128:
            raise RuntimeFailure("Invalid host transaction record")
        # Validate the complete undo record before restoring any file.
        originals = {
            self.check_path(Path(name)): base64.b64decode(value, validate=True)
            if value is not None
            else None
            for name, value in data.items()
        }
        for path, value in originals.items():
            if value is None:
                remove_file(path)
            else:
                atomic_write(path, value)
                path.chmod(0o600)
        if intents is not None:
            self.restore_intents(intents)
        self.commit()

    def begin(self, changes: dict[Path, bytes | None], *, intents=None) -> None:
        self.check_intents(intents)
        guarded_path(self.record.parent, self.record)
        if self.record.exists():
            raise RuntimeFailure("Unresolved host transaction; recovery is required")
        if len(changes) > 128:
            raise RuntimeFailure("Too many files in one host transaction")
        originals = {}
        for path, value in changes.items():
            self.check_path(path)
            if value is not None and len(value) > MAX_CONFIG_BYTES:
                raise RuntimeFailure("Host file exceeds 2 MiB")
            originals[str(path)] = (
                base64.b64encode(read_private(path)).decode() if path.exists() else None
            )
        record = json.dumps(
            {"schema": 1, "files": originals, "intents": intents}
            if intents is not None
            else originals
        ).encode()
        if len(record) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Host transaction undo record exceeds 2 MiB")
        atomic_write(self.record, record)
        try:
            for path, value in changes.items():
                if value is None:
                    remove_file(path)
                else:
                    atomic_write(path, value)
                    path.chmod(0o600)
        except BaseException:
            self.recover()
            raise

    def commit(self) -> None:
        remove_file(self.record)
