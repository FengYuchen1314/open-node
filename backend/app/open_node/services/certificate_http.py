"""Host-selected, exclusively owned HTTP-01 challenge directories."""

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

from open_node.services.certificate_vault import private_path

log = logging.getLogger(__name__)


def harden_work(root: Path):
    # lego creates account keys with os.Create. A webroot job needs umask 022,
    # but its entire working tree stays behind this private directory.
    private_path(root, root)
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                path.chmod(0o700)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                path.chmod(0o600)
            else:
                raise ValueError("Unexpected file in private ACME working directory")


class WebrootChallenges:
    def __init__(self, vault):
        self.vault = vault
        self.registry = vault.root / "http01-webroots"

    def _root(self, root: Path):
        if (
            not root.is_absolute()
            or root == Path(root.anchor)
            or ".." in root.parts
            or root.is_relative_to(self.vault.root)
            or self.vault.root.is_relative_to(root)
        ):
            raise ValueError("Webroot and private certificate state must be separate")
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("Webroots cannot use symlinks")
        self._directory(root)

    @staticmethod
    def _directory(path: Path, *, create=False):
        if create:
            try:
                path.mkdir(mode=0o755)
                path.chmod(0o755)
            except FileExistsError:
                pass
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("HTTP challenge directories must not be linked or publicly writable")
        return info

    def _record_path(self, root):
        name = hashlib.sha256(str(root).encode()).hexdigest() + ".json"
        return private_path(self.vault.root, self.registry / name)

    def _check_record(self, root, record):
        self._root(root)
        self._directory(root / ".well-known")
        info = self._directory(root / ".well-known/acme-challenge")
        if (
            record != {"path": str(root), "device": info.st_dev, "inode": info.st_ino}
            or info.st_uid != os.geteuid()
        ):
            raise ValueError("HTTP challenge directory ownership has changed")

    def prepare(self, root: Path):
        self._root(root)
        self.vault.prepare()
        private_path(self.vault.root, self.registry).mkdir(mode=0o700, exist_ok=True)
        self._directory(root / ".well-known", create=True)
        directory = root / ".well-known/acme-challenge"
        info = self._directory(directory, create=True)
        record_path = self._record_path(root)
        if not record_path.exists():
            if info.st_uid != os.geteuid() or any(directory.iterdir()):
                raise ValueError("An unowned HTTP challenge directory must be empty before use")
            record = {"path": str(root), "device": info.st_dev, "inode": info.st_ino}
            with tempfile.NamedTemporaryFile(dir=self.registry, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(json.dumps(record).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.replace(temporary, record_path)
                finally:
                    temporary.unlink(missing_ok=True)
            fd = os.open(self.registry, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.cleanup(root)

    def cleanup(self, root: Path):
        record = json.loads(self.vault.read(self._record_path(root), 4096))
        self._check_record(root, record)
        directory = root / ".well-known/acme-challenge"
        candidates = []
        # The registered directory belongs only to Open Node. Validate the
        # entire set before removing anything; never follow links or open FIFOs.
        for path in directory.iterdir():
            info = path.lstat()
            if (
                not re.fullmatch(r"[a-zA-Z0-9_-]{22,128}", path.name)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_size > 256
            ):
                raise ValueError("Unexpected content in owned HTTP challenge directory")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                body = stream.read(257)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino) or not re.fullmatch(
                re.escape(path.name.encode()) + rb"\.[a-zA-Z0-9_-]{43}", body
            ):
                raise ValueError("Unexpected HTTP challenge response")
            candidates.append((path, info))
        for path, info in candidates:
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("HTTP challenge response changed during cleanup")
            path.unlink()

    def recover(self):
        if not private_path(self.vault.root, self.registry).exists():
            return
        for record_path in self.registry.glob("*.json"):
            try:
                record = json.loads(self.vault.read(record_path, 4096))
                root = Path(record["path"])
                if record_path != self._record_path(root):
                    raise ValueError("Invalid HTTP challenge ownership record")
                self.cleanup(root)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                # An altered/removed site cannot prevent unrelated certificates
                # or node deployments. Reuse of this webroot still fails closed.
                log.warning("HTTP challenge cleanup needs host attention (%s)", type(exc).__name__)
