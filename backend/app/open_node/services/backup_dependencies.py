"""Read-only checks of a completed SQLite snapshot and its private state copies.

The caller exclusively owns a completed, standalone snapshot connection with
``query_only=ON``, no transaction, attachments, custom SQL functions, or installed
progress handler. SQLite's empty built-in temp schema is permitted. This module
temporarily owns the progress handler, not the connection.
It never opens an application Store, a pathname, or a Vault; it cannot establish
that an arbitrary connection was made from a consistent snapshot. State streams
are completed private copies, need not have ``fileno()``, and remain caller-owned.

These checks authenticate every present ciphertext, including disabled/expired
records, when its key is available. Existing certificate ciphertext has no row
identity/purpose binding; TOTP binds a username, not active/pending purpose. An
ACME account key parsing successfully does not authenticate its registration at
the CA. Remote Agent trust, deployment configuration, and restoration remain
unchecked. Missing external TOTP configuration is reported, never guessed.

The limits are an explicit supported verification envelope, not corruption
criteria. The 30-second deadline is soft between local I/O/crypto operations;
SQLite also has a bounded VM progress handler. Neither is a hard kernel deadline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import BinaryIO, Literal
from uuid import UUID

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from open_node.domain.backup import (
    MAX_FILES,
    BackupConfiguration,
    BackupCoverage,
    validate_backup_path,
)
from open_node.domain.certificates import DNS_FIELDS, DNS_REQUIRED, dns_name
from open_node.domain.notifications import validate_bot_token

MAX_DEPENDENCY_SECONDS = 30.0
MAX_DEPENDENCY_QUERIES = 128
MAX_DEPENDENCY_ROWS = 100_000
MAX_DEPENDENCY_SQL_STEPS = 20_000_000
MAX_DEPENDENCY_IO_OPERATIONS = 131_072
MAX_CIPHERTEXT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_CIPHERTEXT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_PLAINTEXT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_METADATA_BYTES = 64 * 1024 * 1024
MAX_TOTAL_STATE_BYTES = 64 * 1024 * 1024
MAX_STATE_ITEM_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 200_000
READ_CHUNK_BYTES = 64 * 1024

_CERTIFICATES = "data/certificates/"
_EXTERNAL = "data/external-subscriptions/"
_NOTIFICATIONS = "data/notifications/"
_IDENTITY = "secrets/agent-identity.seed"
_VAULT_MARKER = b"Open Node certificate vault\n"
_NOTIFICATION_PURPOSE = "open-node.notifications.telegram.v1"
_ROLE_NAMES = ("certificates", "external_subscriptions", "notifications", "totp")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_NOT_CHECKED = (
    "certificate_ciphertext_row_or_purpose_binding",
    "totp_active_or_pending_purpose_binding",
    "acme_registration_and_remote_state",
    "unrecognized_certificate_state_semantics",
    "deployment_configuration",
    "snapshot_consistency",
    "source_authenticity",
    "restoration",
)

# Only these physical tables/ordinary columns may be read. Integer columns have
# limit None; every text value is bounded in SQL before it crosses into Python.
_TABLES: dict[str, dict[str, int | None]] = {
    "product_users": {"username": 1024},
    "certificate_dns_providers": {
        "id": 36, "provider": 64, "credentials": MAX_CIPHERTEXT_BYTES,
        "credential_fields": 4096,
    },
    "managed_certificates": {
        "id": 36, "provider_id": 36, "version_id": 36, "active_job_id": 36,
        "eab": MAX_CIPHERTEXT_BYTES,
    },
    "certificate_jobs": {
        "id": 36, "certificate_id": 36, "parameters": MAX_CIPHERTEXT_BYTES,
    },
    "certificate_versions": {
        "id": 36, "certificate_id": 36, "encrypted_material": MAX_CIPHERTEXT_BYTES,
        "details": MAX_CIPHERTEXT_BYTES, "fingerprint": 64,
    },
    "certificate_targets": {"certificate_id": 36, "version_id": 36},
    "external_subscription_sources": {
        "id": 36, "owner_username": 1024, "secret": MAX_CIPHERTEXT_BYTES,
        "url_digest": 64,
    },
    "external_subscription_nodes": {
        "id": 36, "source_id": 36, "secret": MAX_CIPHERTEXT_BYTES,
    },
    "external_subscription_previews": {
        "id": 36, "source_id": 36, "secret": MAX_CIPHERTEXT_BYTES,
    },
    "notification_settings": {
        "id": None, "token_ciphertext": MAX_CIPHERTEXT_BYTES, "key_fingerprint": 64,
    },
    "administrator": {"id": None, "username": 1024},
    "administrator_factors": {
        "administrator_id": None, "totp_secret": 512, "pending_secret": 512,
    },
    "operator_challenges": {"administrator_id": None, "pending_secret": 512},
    "subscriber_accounts": {"username": 1024, "totp_secret": 512, "pending_secret": 512},
}


class BackupDependencyError(RuntimeError):
    """One safe failure for invalid, unsupported, unavailable, or over-budget input."""

    code = "backup_dependencies_unavailable"

    def __init__(self) -> None:
        super().__init__("Backup dependency verification is unavailable.")


@dataclass(frozen=True, slots=True)
class BackupDependencyReport:
    coverage: BackupCoverage
    required_configuration: tuple[BackupConfiguration, ...]
    checked: tuple[str, ...]
    checked_ciphertexts: int
    ciphertext_counts: tuple[tuple[str, int], ...]
    # Existing business rows do not by themselves require an initialized Vault.
    # dependencies means ciphertext or a persisted notification key fingerprint;
    # modules_present is inventory only and must not imply required state files.
    database_dependencies: frozenset[str]
    database_modules_present: frozenset[str]
    totp_status: Literal["verified", "not_checked", "not_configured"]
    agent_identity_matches_runtime: bool | None
    not_checked: tuple[str, ...] = _NOT_CHECKED
    remote_agent_trust: Literal["not_checked"] = "not_checked"
    restoration_ready: Literal[False] = False


def _fail() -> None:
    raise BackupDependencyError() from None


def _text(value: object, *, maximum: int = MAX_CIPHERTEXT_BYTES) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        _fail()
    return value


def _uuid(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _text(value, maximum=36)
    if str(UUID(text)) != text:
        _fail()
    return text


def _integer(value: object) -> int:
    if type(value) is not int or value < 1:
        _fail()
    return value


def _hex(value: object) -> str:
    text = _text(value, maximum=64)
    if _HEX64.fullmatch(text) is None:
        _fail()
    return text


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class _Budget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + MAX_DEPENDENCY_SECONDS
        self.queries = self.rows = self.steps = self.operations = 0
        self.ciphertext = self.plaintext = self.metadata = self.state = 0

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            _fail()

    def progress(self) -> int:
        self.steps += 1000
        return int(self.steps > MAX_DEPENDENCY_SQL_STEPS or time.monotonic() >= self.deadline)

    def operation(self) -> None:
        self.check()
        self.operations += 1
        if self.operations > MAX_DEPENDENCY_IO_OPERATIONS:
            _fail()

    def add(self, name: str, count: int, maximum: int) -> None:
        self.check()
        total = getattr(self, name) + count
        if total > maximum:
            _fail()
        setattr(self, name, total)


def _query(connection: sqlite3.Connection, budget: _Budget, sql: str, parameters=()) -> Iterator:
    budget.check()
    budget.queries += 1
    if budget.queries > MAX_DEPENDENCY_QUERIES:
        _fail()
    cursor = connection.cursor()
    try:
        # Do not execute a caller-provided row factory, even for PRAGMAs.
        cursor.row_factory = None
        cursor.execute(sql, parameters)
        while True:
            budget.check()
            row = cursor.fetchone()
            if row is None:
                break
            budget.rows += 1
            if budget.rows > MAX_DEPENDENCY_ROWS:
                _fail()
            for value in row:
                if isinstance(value, (str, bytes)):
                    size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
                    budget.add("metadata", size, MAX_TOTAL_METADATA_BYTES)
            yield row
    finally:
        cursor.close()
    budget.check()


def _schema(connection: sqlite3.Connection, budget: _Budget) -> None:
    if (
        not isinstance(connection, sqlite3.Connection)
        or connection.in_transaction
        or connection.text_factory is not str
    ):
        _fail()
    if list(_query(connection, budget, "PRAGMA query_only")) != [(1,)]:
        _fail()
    databases = list(_query(connection, budget, "PRAGMA database_list"))
    if not databases or databases[0][:2] != (0, "main"):
        _fail()
    if len(databases) == 2:
        # SQLite integrity/FK checks can initialize its empty built-in temp
        # schema. It is not ATTACH and must not reject the completed snapshot.
        if databases[1] != (1, "temp", "") or list(_query(
            connection, budget, "SELECT 1 FROM temp.sqlite_schema LIMIT 1",
        )):
            _fail()
    elif len(databases) != 1:
        _fail()
    placeholders = ",".join("?" for _ in _TABLES)
    rows = list(_query(
        connection, budget,
        "SELECT name, type, rootpage, "
        "CASE WHEN length(CAST(sql AS BLOB)) <= 131072 THEN sql ELSE NULL END "
        f"FROM main.sqlite_schema WHERE name IN ({placeholders})",
        tuple(_TABLES),
    ))
    if len(rows) != len(_TABLES):
        _fail()
    for name, kind, rootpage, declaration in rows:
        if (
            kind != "table" or type(rootpage) is not int or rootpage < 1
            or type(declaration) is not str
            or not declaration.upper().startswith("CREATE TABLE")
        ):
            _fail()
        # Fixed names only; do not interpolate any SQLite-returned identifier.
        expected = _TABLES.get(name)
        if expected is None:
            _fail()
        columns = list(_query(connection, budget, f'PRAGMA main.table_xinfo("{name}")'))
        found = {row[1]: row for row in columns}
        if not expected.keys() <= found.keys() or any(found[column][6] != 0 for column in expected):
            _fail()


def _rows(connection: sqlite3.Connection, budget: _Budget, table: str) -> Iterator[tuple]:
    columns = _TABLES[table]
    expressions = []
    for name, limit in columns.items():
        column = f'"{name}"'
        condition = f"typeof({column})='integer'" if limit is None else (
            f"typeof({column})='text' AND length(CAST({column} AS BLOB)) <= {limit}"
        )
        # A fixed tiny BLOB is an invalid typed value, never a truncated success.
        expressions.append(f"CASE WHEN {column} IS NULL THEN NULL WHEN {condition} "
                           f"THEN {column} ELSE X'00' END")
    yield from _query(connection, budget, f'SELECT {",".join(expressions)} FROM main."{table}"')


def _json(raw: bytes | str, budget: _Budget) -> object:
    if type(raw) not in (bytes, str):
        _fail()
    encoded = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    if not encoded or len(encoded) > MAX_CIPHERTEXT_BYTES:
        _fail()
    text = encoded.decode("utf-8")
    depth = 0
    quoted = escaped = False
    for index, character in enumerate(text):
        if index % 4096 == 0:
            budget.check()
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _fail()
        elif character in "}]":
            depth -= 1
            if depth < 0:
                _fail()
    if depth or quoted:
        _fail()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail()
            result[key] = value
        return result

    def integer(value):
        if len(value) > 32:
            _fail()
        return int(value)

    def constant(_value):
        _fail()

    result = json.loads(text, object_pairs_hook=unique, parse_int=integer, parse_constant=constant)
    stack = [iter((result,))]
    nodes = 0
    while stack:
        budget.check()
        try:
            value = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES:
            _fail()
        if type(value) is dict:
            for key in value:
                key.encode("utf-8")
            stack.append(iter(value.values()))
        elif type(value) is list:
            stack.append(iter(value))
        elif type(value) is str:
            value.encode("utf-8")
        elif type(value) is float and not math.isfinite(value):
            _fail()
    return result


def _object(value: object, fields: set[str] | None = None) -> dict:
    if type(value) is not dict or (fields is not None and set(value) != fields):
        _fail()
    return value


class _State:
    def __init__(self, sources: Mapping[str, BinaryIO], budget: _Budget) -> None:
        if not isinstance(sources, Mapping) or len(sources) > MAX_FILES - 1:
            _fail()
        self.sources, self.budget = sources, budget
        self.paths: set[str] = set()
        for index, path in enumerate(sources):
            budget.check()
            if index >= MAX_FILES - 1:
                _fail()
            validate_backup_path(path)
            if not (
                path.startswith(_CERTIFICATES)
                or path in {_EXTERNAL + "vault.key", _EXTERNAL + "vault.initialized",
                            _NOTIFICATIONS + "telegram.key",
                            _NOTIFICATIONS + "telegram.initialized", _IDENTITY}
            ):
                _fail()
            self.paths.add(path)
        components = sorted(path.split("/") for path in self.paths)
        for previous, current in zip(components, components[1:], strict=False):
            if current[:len(previous)] == previous:
                _fail()

    def read(self, path: str, maximum: int = MAX_STATE_ITEM_BYTES) -> bytes:
        stream = self.sources.get(path)
        if stream is None or not stream.readable() or not stream.seekable():
            _fail()
        self.budget.operation()
        original = stream.tell()
        if type(original) is not int or original < 0:
            _fail()
        try:
            self.budget.operation()
            size = stream.seek(0, io.SEEK_END)
            if type(size) is not int or not 0 <= size <= maximum or stream.tell() != size:
                _fail()
            self.budget.operation()
            if stream.seek(0) != 0 or stream.tell() != 0:
                _fail()
            data = bytearray()
            while True:
                self.budget.operation()
                wanted = min(READ_CHUNK_BYTES, size - len(data) + 1)
                block = stream.read(wanted)
                if type(block) is not bytes or len(block) > wanted:
                    _fail()
                if not block:
                    break
                self.budget.add("state", len(block), MAX_TOTAL_STATE_BYTES)
                data.extend(block)
                if len(data) > size:
                    _fail()
            self.budget.operation()
            if (
                len(data) != size or stream.tell() != size
                or stream.seek(0, io.SEEK_END) != size or stream.tell() != size
            ):
                _fail()
            return bytes(data)
        finally:
            # Restoring caller position is cleanup and must still run on timeout.
            if stream.seek(original) != original or stream.tell() != original:
                _fail()

    def vault(self, prefix: str) -> tuple[Fernet, bytes] | None:
        key, marker = prefix + "vault.key", prefix + "vault.initialized"
        if not ({key, marker} & self.paths):
            if any(path.startswith(prefix) for path in self.paths):
                _fail()
            return None
        if not {key, marker} <= self.paths or self.read(marker, 128) != _VAULT_MARKER:
            _fail()
        raw = self.read(key, 128)
        return Fernet(raw), raw


class _Check:
    def __init__(self, connection, state: _State, budget: _Budget) -> None:
        self.connection, self.state, self.budget = connection, state, budget
        self.counts = dict.fromkeys(_ROLE_NAMES, 0)
        self.checked_ciphertexts = 0
        self.checked = {"supported_dependency_schema_and_columns"}
        self.dependencies: set[str] = set()
        self.modules_present: set[str] = set()

    def rows(self, table: str):
        module = None
        if table.startswith("certificate_") or table == "managed_certificates":
            module = "certificates"
        elif table.startswith("external_subscription_"):
            module = "external_subscriptions"
        elif table == "notification_settings":
            module = "notifications"
        elif table in {"administrator_factors", "operator_challenges", "subscriber_accounts"}:
            module = "totp"
        for row in _rows(self.connection, self.budget, table):
            if module is not None:
                self.modules_present.add(module)
            yield row

    def open(self, cipher: Fernet | None, secret: object, role: str) -> bytes:
        encoded = _text(secret).encode("ascii")
        self.budget.add("ciphertext", len(encoded), MAX_TOTAL_CIPHERTEXT_BYTES)
        self.counts[role] += 1
        self.dependencies.add(role)
        if cipher is None:
            _fail()
        self.budget.check()
        plaintext = cipher.decrypt(encoded)  # Deliberately no age/expiry TTL.
        self.budget.add("plaintext", len(plaintext), MAX_TOTAL_PLAINTEXT_BYTES)
        self.checked_ciphertexts += 1
        return plaintext

    def json_secret(self, cipher, secret, role):
        return _object(_json(self.open(cipher, secret, role), self.budget))

    def eab(self, cipher, secret) -> None:
        value = _object(self.json_secret(cipher, secret, "certificates"), {"kid", "hmac"})
        _text(value["kid"], maximum=32768)
        _text(value["hmac"], maximum=32768)

    def certificates(self, pair) -> None:
        cipher = pair[0] if pair else None
        providers = set()
        for identifier, provider, secret, fields in self.rows("certificate_dns_providers"):
            identifier = _uuid(identifier)
            if identifier in providers or provider not in DNS_FIELDS:
                _fail()
            providers.add(identifier)
            credentials = self.json_secret(cipher, secret, "certificates")
            if not set(DNS_REQUIRED[provider]) <= credentials.keys() <= set(DNS_FIELDS[provider]):
                _fail()
            for value in credentials.values():
                _text(value)
            public_fields = _json(fields, self.budget)
            if type(public_fields) is not list or public_fields != sorted(credentials):
                _fail()

        profiles = {}
        for identifier, provider, version, job, eab in self.rows("managed_certificates"):
            identifier = _uuid(identifier)
            if identifier in profiles:
                _fail()
            profiles[identifier] = (_uuid(provider, nullable=True), _uuid(version, nullable=True),
                                    _uuid(job, nullable=True))
            if eab is not None:
                self.eab(cipher, eab)
        versions = {}
        for identifier, owner, secret, details, fingerprint in self.rows("certificate_versions"):
            identifier, owner = _uuid(identifier), _uuid(owner)
            if identifier in versions or owner not in profiles:
                _fail()
            material = self.json_secret(cipher, secret, "certificates")
            digest = self.material(material)
            expected = {key: value for key, value in material.items()
                        if key not in {"cert_pem", "key_pem"}}
            if _canonical(_json(details, self.budget)) != _canonical(expected):
                _fail()
            if fingerprint is not None and _hex(fingerprint) != digest:
                _fail()
            versions[identifier] = (owner, digest)
        jobs = {}
        for identifier, owner, parameters in self.rows("certificate_jobs"):
            identifier, owner = _uuid(identifier), _uuid(owner)
            if identifier in jobs or owner not in profiles:
                _fail()
            jobs[identifier] = owner
            parameters = _object(_json(parameters, self.budget))
            if parameters.get("eab") is not None:
                self.eab(cipher, parameters["eab"])
            if parameters.get("version_id") is not None:
                version = versions.get(_uuid(parameters["version_id"]))
                if version is None or version[0] != owner:
                    _fail()
                if parameters.get("fingerprint") is not None and (
                    _hex(parameters["fingerprint"]) != version[1]
                ):
                    _fail()
        for identifier, (provider, version, job) in profiles.items():
            if (
                (provider is not None and provider not in providers)
                or (version is not None and (
                    version not in versions or versions[version][0] != identifier
                ))
                or (job is not None and jobs.get(job) != identifier)
            ):
                _fail()
        for owner, version in self.rows("certificate_targets"):
            owner, version = _uuid(owner), _uuid(version, nullable=True)
            if owner not in profiles or (version is not None and (
                version not in versions or versions[version][0] != owner
            )):
                _fail()
        self.checked.add("certificate_database_key_dependencies")
        if self.counts["certificates"]:
            self.checked.add("certificate_fernet_authentication_and_payload_shapes")
        if versions:
            self.checked.add("certificate_pem_key_pairs_and_available_fingerprints")

    def material(self, value: object) -> str:
        value = _object(value, {"cert_pem", "key_pem", "domains", "not_before", "expires_at",
                                "serial", "issuer"})
        certificate = self.pem_pair(_text(value["cert_pem"]).encode(),
                                    _text(value["key_pem"]).encode())
        names = [dns_name(name) for name in certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName,
        ).value.get_values_for_type(x509.DNSName)]
        if not names or type(value["domains"]) is not list or value["domains"] != names:
            _fail()
        for field, actual in (
            ("not_before", certificate.not_valid_before_utc.timestamp()),
            ("expires_at", certificate.not_valid_after_utc.timestamp()),
        ):
            if type(value[field]) not in (int, float) or value[field] != actual:
                _fail()
        if value["serial"] != str(certificate.serial_number) or (
            value["issuer"] != certificate.issuer.rfc4514_string()
        ):
            _fail()
        return certificate.fingerprint(hashes.SHA256()).hex()

    def pem_pair(self, certificate: bytes, private: bytes):
        self.budget.check()
        chain = x509.load_pem_x509_certificates(certificate)
        key = serialization.load_pem_private_key(private, password=None)
        if not chain or self.public(key.public_key()) != self.public(chain[0].public_key()):
            _fail()
        self.budget.check()
        return chain[0]

    @staticmethod
    def public(key) -> bytes:
        return key.public_bytes(serialization.Encoding.DER,
                                serialization.PublicFormat.SubjectPublicKeyInfo)

    def acme_state(self) -> None:
        paths = self.state.paths
        verified = set()
        for path in sorted(paths):
            self.budget.check()
            if not path.startswith(_CERTIFICATES):
                continue
            pieces = path.split("/")
            # A deleted profile's retained files need not still have a DB row.
            if len(pieces) >= 4 and pieces[3] == "accounts":
                if len(pieces) == 8 and pieces[6] == "keys" and path.endswith(".key"):
                    key = serialization.load_pem_private_key(self.state.read(path), password=None)
                    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
                        _fail()
                    verified.add("acme_local_account_private_keys")
                elif len(pieces) == 7 and pieces[6] == "account.json":
                    account = _object(_json(self.state.read(path), self.budget))
                    key_path = "/".join(pieces[:-1]) + "/keys/" + pieces[5] + ".key"
                    if key_path not in paths:
                        _fail()
                    registration = _object(account.get("registration"))
                    _text(registration.get("uri"), maximum=16384)
                    verified.add("acme_local_account_metadata_key_presence")
            elif len(pieces) == 5 and pieces[3] == "certificates":
                if path.endswith(".crt"):
                    self.pem_pair(self.state.read(path), self.state.read(path[:-4] + ".key"))
                    verified.add("acme_local_certificate_key_pairs")
                elif path.endswith(".key"):
                    serialization.load_pem_private_key(self.state.read(path), password=None)
                    verified.add("acme_local_certificate_private_keys")
            elif len(pieces) == 6 and pieces[3] == "jobs":
                directory = "/".join(pieces[:-1]) + "/"
                if pieces[-1] == "certificate.key":
                    serialization.load_pem_private_key(self.state.read(path), password=None)
                    verified.add("acme_order_private_keys")
                elif pieces[-1] == "request.csr":
                    csr = x509.load_pem_x509_csr(self.state.read(path))
                    key = serialization.load_pem_private_key(
                        self.state.read(directory + "certificate.key"), password=None,
                    )
                    if not csr.is_signature_valid or self.public(csr.public_key()) != (
                        self.public(key.public_key())
                    ):
                        _fail()
                    verified.add("acme_order_csr_key_pairs")
                elif pieces[-1] == "order.json":
                    order = _object(_json(self.state.read(path), self.budget))
                    if _uuid(order.get("job_id")) != pieces[4] or (
                        _hex(order.get("csr_digest"))
                        != hashlib.sha256(self.state.read(directory + "request.csr")).hexdigest()
                    ):
                        _fail()
                    _text(order.get("uri"), maximum=16384)
                    verified.add("acme_order_job_and_csr_digest_binding")
                elif pieces[-1] in {"request.json", "result.json"}:
                    value = _object(_json(self.state.read(path), self.budget))
                    if value.get("job_id") is not None and _uuid(value["job_id"]) != pieces[4]:
                        _fail()
                    if value.get("material") is not None:
                        self.material(value["material"])
                        verified.add("acme_job_material_key_pairs")
        self.checked.update(verified)

    def external(self, pair, users: set[str]) -> None:
        cipher, key = pair if pair else (None, None)
        sources = {}
        for identifier, owner, secret, digest in self.rows("external_subscription_sources"):
            identifier, owner = _uuid(identifier), _text(owner, maximum=1024)
            if identifier in sources or owner not in users:
                _fail()
            sources[identifier] = owner
            value = self.external_secret(cipher, secret, identifier, owner, "source")
            value = _object(value, {"url", "user_agent"})
            url = _text(value["url"])
            _text(value["user_agent"])
            actual = hmac.new(key, b"open-node/external-url/v1\0" + url.encode(), hashlib.sha256)
            if not hmac.compare_digest(_hex(digest), actual.hexdigest()):
                _fail()
        for table, purpose in (("external_subscription_nodes", "node:"),
                               ("external_subscription_previews", "preview:")):
            identifiers = set()
            for identifier, source, secret in self.rows(table):
                identifier, source = _uuid(identifier), _uuid(source)
                if identifier in identifiers or source not in sources:
                    _fail()
                identifiers.add(identifier)
                if secret is not None:
                    value = self.external_secret(cipher, secret, source, sources[source],
                                                 purpose + identifier)
                    _object(value)
                    if purpose == "preview:":
                        _object(value, {"nodes", "metadata"})
                        if type(value["nodes"]) is not list or type(value["metadata"]) is not dict:
                            _fail()
                        seen = set()
                        for candidate in value["nodes"]:
                            candidate = _object(candidate)
                            node_id = _uuid(candidate.get("id"))
                            if node_id in seen:
                                _fail()
                            seen.add(node_id)
        self.checked.add("external_database_key_dependencies")
        if self.counts["external_subscriptions"]:
            self.checked.add("external_owner_source_purpose_id_binding_and_url_digest")

    def external_secret(self, cipher, secret, source, owner, purpose):
        value = _object(self.json_secret(cipher, secret, "external_subscriptions"),
                        {"version", "owner", "source", "purpose", "value"})
        if (
            type(value["version"]) is not int or value["version"] != 1
            or value["owner"] != owner or value["source"] != source or value["purpose"] != purpose
        ):
            _fail()
        return value["value"]

    def notifications(self) -> bool:
        key_path, marker_path = (_NOTIFICATIONS + "telegram.key",
                                 _NOTIFICATIONS + "telegram.initialized")
        present = {key_path, marker_path} & self.state.paths
        cipher = fingerprint = None
        if present:
            if present != {key_path, marker_path}:
                _fail()
            key = self.state.read(key_path, 128)
            cipher = Fernet(key)
            fingerprint = hashlib.sha256(
                _NOTIFICATION_PURPOSE.encode() + b"\x00" + key,
            ).hexdigest()
            expected = _canonical({"purpose": _NOTIFICATION_PURPOSE,
                                   "key_fingerprint": fingerprint})
            if self.state.read(marker_path, 512) != expected:
                _fail()
        seen = False
        for identifier, secret, recorded in self.rows("notification_settings"):
            if type(identifier) is not int or identifier != 1 or seen:
                _fail()
            seen = True
            if recorded is not None:
                self.dependencies.add("notifications")
                if _hex(recorded) != fingerprint:
                    _fail()
            if secret is not None:
                if recorded is None:
                    _fail()
                value = _object(self.json_secret(cipher, secret, "notifications"),
                                {"purpose", "token"})
                if value["purpose"] != _NOTIFICATION_PURPOSE:
                    _fail()
                validate_bot_token(value["token"])
        self.checked.add("notification_database_key_dependencies")
        if present:
            self.checked.add("notification_marker_and_available_database_fingerprint")
        if self.counts["notifications"]:
            self.checked.add("notification_fernet_and_telegram_purpose_binding")
        return bool(present)

    def totp(self, key: bytes | None, users: set[str]) -> str:
        if key is not None and (type(key) is not bytes or len(key) > 128):
            _fail()
        cipher = Fernet(key) if key is not None else None
        administrators = {}
        for identifier, username in self.rows("administrator"):
            identifier, username = _integer(identifier), _text(username, maximum=1024)
            if identifier in administrators:
                _fail()
            administrators[identifier] = username
        for table in ("administrator_factors", "operator_challenges", "subscriber_accounts"):
            for owner, *values in self.rows(table):
                if table == "subscriber_accounts":
                    username = _text(owner, maximum=1024)
                    if username not in users:
                        _fail()
                else:
                    username = administrators.get(_integer(owner))
                    if username is None:
                        _fail()
                for value in values:
                    if value is None:
                        continue
                    if cipher is None:
                        encoded = _text(value, maximum=512).encode("ascii")
                        self.budget.add("ciphertext", len(encoded), MAX_TOTAL_CIPHERTEXT_BYTES)
                        self.counts["totp"] += 1
                        self.dependencies.add("totp")
                        continue
                    plain = self.open(cipher, value, "totp").decode("ascii")
                    prefix, secret = plain.split("\n", 1)
                    expected = hashlib.sha256(username.encode()).hexdigest()
                    if not hmac.compare_digest(prefix, expected):
                        _fail()
                    decoded = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
                    if not decoded:
                        _fail()
        self.checked.add("totp_database_key_dependency_inventory")
        if not self.counts["totp"]:
            return "not_configured"
        if cipher is None:
            return "not_checked"
        self.checked.add("totp_fernet_and_username_binding")
        return "verified"

    def identity(self, public: bytes | None) -> tuple[bool, bool | None]:
        if public is not None and (type(public) is not bytes or len(public) != 32):
            _fail()
        if _IDENTITY not in self.state.paths:
            if public is not None:
                _fail()
            return False, None
        seed = self.state.read(_IDENTITY, 32)
        if len(seed) != 32:
            _fail()
        derived = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        self.checked.add("agent_seed_ed25519_structure")
        if public is not None:
            if not hmac.compare_digest(public, derived):
                _fail()
            self.checked.add("agent_seed_to_supplied_runtime_public_key")
            return True, True
        return True, None


def check_backup_dependencies(
    connection: sqlite3.Connection,
    sources: Mapping[str, BinaryIO],
    *,
    totp_key: bytes | None = None,
    agent_public_key: bytes | None = None,
) -> BackupDependencyReport:
    """Check supported dependencies; never mutate/close input or claim recovery.

    ``totp_key`` is the existing ASCII Fernet key, not decoded key bytes.
    ``agent_public_key`` is the running identity's raw 32-byte Ed25519 public key.
    No key is generated or returned. All report strings are fixed vocabulary.
    Optional roles without state remain ``unknown``: absence cannot prove that
    the caller copied the deployment's complete configured layout.
    """
    installed = False
    try:
        if not isinstance(connection, sqlite3.Connection) or connection.in_transaction:
            _fail()
        budget = _Budget()
        connection.set_progress_handler(budget.progress, 1000)
        installed = True
        _schema(connection, budget)
        state = _State(sources, budget)
        checker = _Check(connection, state, budget)
        users = set()
        for (username,) in checker.rows("product_users"):
            username = _text(username, maximum=1024)
            if username in users:
                _fail()
            users.add(username)
        certificate_pair = state.vault(_CERTIFICATES)
        external_pair = state.vault(_EXTERNAL)
        if certificate_pair:
            checker.checked.add("certificate_vault_key_and_marker")
        if external_pair:
            checker.checked.add("external_vault_key_and_marker")
        checker.certificates(certificate_pair)
        checker.acme_state()
        checker.external(external_pair, users)
        notifications = checker.notifications()
        totp = checker.totp(totp_key, users)
        identity, matched = checker.identity(agent_public_key)
        budget.check()
        if connection.in_transaction:
            _fail()
        required: tuple[BackupConfiguration, ...] = ("deployment_settings",)
        if checker.counts["totp"]:
            required += ("subscriber_totp_key",)
        return BackupDependencyReport(
            coverage=BackupCoverage(
                "included" if certificate_pair else "unknown",
                "included" if external_pair else "unknown",
                "included" if notifications else "unknown",
                "included" if identity else "unknown",
            ),
            required_configuration=required,
            checked=tuple(sorted(checker.checked)),
            checked_ciphertexts=checker.checked_ciphertexts,
            ciphertext_counts=tuple((name, checker.counts[name]) for name in _ROLE_NAMES),
            database_dependencies=frozenset(checker.dependencies),
            database_modules_present=frozenset(checker.modules_present),
            totp_status=totp,
            agent_identity_matches_runtime=matched,
        )
    except Exception:
        raise BackupDependencyError() from None
    finally:
        if installed:
            try:
                connection.set_progress_handler(None, 0)
            except Exception:
                raise BackupDependencyError() from None
