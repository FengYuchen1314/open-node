"""Private ACME state and encrypted material; the database never holds the vault key."""

import json
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from open_node.domain.certificates import dns_name


def private_path(root: Path, path: Path) -> Path:
    if not path.is_relative_to(root) or ".." in path.parts:
        raise ValueError("Certificate state path escapes its directory")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Certificate state cannot use symlinks")
    if path.exists() and (path.stat().st_mode & 0o077 or path.stat().st_nlink != 1):
        if path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError("Certificate state must be private and not hard-linked")
    return path


class CertificateVault:
    def __init__(self, root: Path, *, initialized=False):
        self.root = root.absolute()
        self.initialized = initialized

    def prepare(self):
        private_path(self.root, self.root)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)

    def cipher(self) -> Fernet:
        self.prepare()
        key_path = private_path(self.root, self.root / "vault.key")
        with self.lock("vault.lock"):
            marker = private_path(self.root, self.root / "vault.initialized")
            if not key_path.exists():
                if self.initialized or marker.exists():
                    raise InvalidToken()
                fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            cipher = Fernet(self.read(key_path, 128))
            self.initialized = True
            if not marker.exists():
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"Open Node certificate vault\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return cipher

    @contextmanager
    def lock(self, name, *, blocking=True):
        import fcntl

        self.prepare()
        path = private_path(self.root, self.root / name)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield fd
        finally:
            os.close(fd)

    def read(self, path: Path, limit=262144) -> bytes:
        private_path(self.root, path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Expected a private regular certificate file")
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Certificate file exceeds its size limit")
        return content

    def seal(self, value) -> str:
        return self.cipher().encrypt(json.dumps(value).encode()).decode()

    def open(self, value: str):
        if not (self.root / "vault.key").exists():
            raise InvalidToken()
        return json.loads(self.cipher().decrypt(value.encode()))


def material(cert_pem: str, key_pem: str, expected_domains=None) -> dict:
    try:
        chain = x509.load_pem_x509_certificates(cert_pem.encode())
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        cert = chain[0]
        encoding, fmt = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(encoding, fmt) != cert.public_key().public_bytes(
            encoding, fmt
        ):
            raise ValueError()
        names = [
            dns_name(name)
            for name in cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        ]
        if not names or (expected_domains and set(names) != set(expected_domains)):
            raise ValueError()
        now = datetime.now(UTC)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError()
        try:
            usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if ExtendedKeyUsageOID.SERVER_AUTH not in usage:
                raise ValueError()
        except x509.ExtensionNotFound:
            pass
    except (ValueError, TypeError, IndexError, x509.ExtensionNotFound):
        raise ValueError(
            "Invalid, expired, mismatched or unexpected certificate material"
        ) from None
    return {
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "domains": names,
        "not_before": cert.not_valid_before_utc.timestamp(),
        "expires_at": cert.not_valid_after_utc.timestamp(),
        "serial": str(cert.serial_number),
        "issuer": cert.issuer.rfc4514_string(),
    }


def covers(names: list[str], host: str) -> bool:
    return any(
        name == host
        or (
            name.startswith("*.")
            and host.split(".", 1)[-1] == name[2:]
            and host.count(".") == name.count(".")
        )
        for name in names
    )
