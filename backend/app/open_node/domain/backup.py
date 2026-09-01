"""Pure, bounded validation of backup declarations, not recoverability or authenticity."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Literal, NoReturn, cast

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILES = 4096
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 1024 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_COMPONENT_BYTES = 255
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 80000

BackupFileRole = Literal[
    "database", "certificate_state", "external_state", "federation_state",
    "notification_state", "agent_identity"
]
BackupCoverageStatus = Literal["included", "not_configured", "unknown"]
BackupConfiguration = Literal["deployment_settings", "subscriber_totp_key"]

_FORMAT = "open-node-control-plane-backup"
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_UTC_SECONDS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ROLES = frozenset(
    {"database", "certificate_state", "external_state", "federation_state",
     "notification_state", "agent_identity"}
)
_COVERAGE_STATES = frozenset({"included", "not_configured", "unknown"})
_CONFIGURATION = frozenset({"deployment_settings", "subscriber_totp_key"})
_CERTIFICATE_KEYS = frozenset(
    {"data/certificates/vault.key", "data/certificates/vault.initialized"}
)
_EXTERNAL_KEYS = frozenset(
    {"data/external-subscriptions/vault.key", "data/external-subscriptions/vault.initialized"}
)
_FEDERATION_KEYS = frozenset(
    {"data/federation/vault.key", "data/federation/vault.initialized"}
)
_NOTIFICATION_KEYS = frozenset(
    {"data/notifications/telegram.key", "data/notifications/telegram.initialized"}
)
_IDENTITY_FILES = frozenset({"secrets/agent-identity.seed"})
_PATH_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class BackupValidationError(ValueError):
    """A single safe error; never interpolate input, paths, or decoder details."""

    def __init__(self) -> None:
        super().__init__("Invalid backup package.")


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    path: str
    role: BackupFileRole
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupSource:
    git_revision: str | None
    image_id: str | None
    image_revision: str | None


@dataclass(frozen=True, slots=True)
class BackupDatabase:
    engine: Literal["sqlite", "postgresql"]
    layout: Literal["standalone", "custom_dump"]
    schema_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class BackupCoverage:
    certificates: BackupCoverageStatus
    external_subscriptions: BackupCoverageStatus
    notifications: BackupCoverageStatus
    agent_identity: BackupCoverageStatus
    federation: BackupCoverageStatus = "unknown"


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format: Literal["open-node-control-plane-backup"]
    version: int
    created_at: str
    source: BackupSource
    database: BackupDatabase
    coverage: BackupCoverage
    required_configuration: tuple[BackupConfiguration, ...]
    files: tuple[BackupFileEntry, ...]


def _invalid() -> NoReturn:
    raise BackupValidationError() from None


def validate_backup_path(value: object) -> str:
    """Validate a literal POSIX name without resolving, normalizing, or reading it."""
    if type(value) is not str or not value or any(char in value for char in "\\:"):
        _invalid()
    path = cast(str, value)
    if any(unicodedata.category(char) in _PATH_CATEGORIES for char in path):
        _invalid()
    if len(path.encode("utf-8")) > MAX_PATH_BYTES or unicodedata.normalize("NFC", path) != path:
        _invalid()
    for component in path.split("/"):
        if (
            not component
            or component in {".", ".."}
            or component.strip() != component
            or component.endswith(".")
            or len(component.encode("utf-8")) > MAX_COMPONENT_BYTES
        ):
            _invalid()
    return path


def _check_json_depth(text: str) -> None:
    # Depth counts containers only: the root object/array has depth one. Brackets
    # in strings (including escaped quotes/backslashes) never increase depth.
    depth = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                _invalid()
        elif char in "]}":
            depth -= 1
            if depth < 0:
                _invalid()
    if in_string or depth != 0:
        _invalid()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_number(_value: str) -> NoReturn:
    _invalid()


def _check_json_tree(value: object) -> None:
    # Nodes are containers and values, not object keys. Key text is checked too;
    # its resource bounds come from raw bytes and the later exact-field schema.
    nodes = 0
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES:
            _invalid()
        if isinstance(item, dict):
            for key in item:
                if any(unicodedata.category(char) in {"Cc", "Cs"} for char in key):
                    _invalid()
            stack.append(iter(item.values()))
        elif isinstance(item, list):
            stack.append(iter(item))
        elif isinstance(item, str):
            if any(unicodedata.category(char) in {"Cc", "Cs"} for char in item):
                _invalid()


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _invalid()
    return cast(dict[str, object], value)


def _choice(value: object, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        _invalid()
    return cast(str, value)


def _hex(value: object, pattern: re.Pattern[str], *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid()
    return cast(str, value)


def _source(value: object) -> BackupSource:
    row = _object(value, {"git_revision", "image_id", "image_revision"})
    git = _hex(row["git_revision"], _HEX40, nullable=True)
    image = _hex(row["image_id"], _IMAGE_ID, nullable=True)
    revision = _hex(row["image_revision"], _HEX40, nullable=True)
    if git is not None and revision is not None and git != revision:
        _invalid()
    return BackupSource(git, image, revision)


def _database(value: object) -> BackupDatabase:
    row = _object(value, {"engine", "layout", "schema_fingerprint"})
    if (row["engine"], row["layout"]) not in {
        ("sqlite", "standalone"),
        ("postgresql", "custom_dump"),
    }:
        _invalid()
    return BackupDatabase(
        cast(Literal["sqlite", "postgresql"], row["engine"]),
        cast(Literal["standalone", "custom_dump"], row["layout"]),
        _hex(row["schema_fingerprint"], _HEX64, nullable=True),
    )


def _coverage(value: object) -> BackupCoverage:
    names = (
        "certificates", "external_subscriptions", "notifications", "agent_identity", "federation",
    )
    row = _object(value, set(names))
    states = [cast(BackupCoverageStatus, _choice(row[name], _COVERAGE_STATES)) for name in names]
    return BackupCoverage(
        certificates=states[0], external_subscriptions=states[1], notifications=states[2],
        agent_identity=states[3], federation=states[4],
    )


def _configuration(value: object) -> tuple[BackupConfiguration, ...]:
    if type(value) is not list:
        _invalid()
    result = tuple(cast(BackupConfiguration, _choice(item, _CONFIGURATION)) for item in value)
    if len(result) != len(set(result)) or "deployment_settings" not in result:
        _invalid()
    return result


def _files(
    value: object, coverage: BackupCoverage, database: BackupDatabase
) -> tuple[BackupFileEntry, ...]:
    if type(value) is not list or not 1 <= len(value) <= MAX_FILES:
        _invalid()
    result = []
    paths: set[str] = set()
    by_role: dict[str, set[str]] = {role: set() for role in _ROLES}
    total = 0
    for item in value:
        row = _object(item, {"path", "role", "size", "sha256"})
        path = validate_backup_path(row["path"])
        role = cast(BackupFileRole, _choice(row["role"], _ROLES))
        size = row["size"]
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or path in paths:
            _invalid()
        total += size
        if total > MAX_TOTAL_FILE_BYTES:
            _invalid()
        if (
            (role == "database" and path != (
                "data/open-node.db"
                if database.engine == "sqlite"
                else "database/postgres.dump"
            ))
            or (role == "certificate_state" and not path.startswith("data/certificates/"))
            or (role == "external_state" and path not in _EXTERNAL_KEYS)
            or (role == "federation_state" and path not in _FEDERATION_KEYS)
            or (role == "notification_state" and path not in _NOTIFICATION_KEYS)
            or (role == "agent_identity" and path not in _IDENTITY_FILES)
        ):
            _invalid()
        paths.add(path)
        by_role[role].add(path)
        result.append(BackupFileEntry(path, role, size, cast(str, _hex(row["sha256"], _HEX64))))
    # Sort components, not whole strings: "a-other" must not conceal a parent
    # conflict between "a" and "a/child" in ordinary lexicographic ordering.
    for previous, current in pairwise(sorted(path.split("/") for path in paths)):
        if current[:len(previous)] == previous:
            _invalid()
    expected_database = (
        "data/open-node.db"
        if database.engine == "sqlite"
        else "database/postgres.dump"
    )
    if by_role["database"] != {expected_database}:
        _invalid()
    for name, role, required in (
        ("certificates", "certificate_state", _CERTIFICATE_KEYS),
        ("external_subscriptions", "external_state", _EXTERNAL_KEYS),
        ("federation", "federation_state", _FEDERATION_KEYS),
        ("notifications", "notification_state", _NOTIFICATION_KEYS),
        ("agent_identity", "agent_identity", _IDENTITY_FILES),
    ):
        present = by_role[role]
        if getattr(coverage, name) != "included":
            if present:
                _invalid()
        elif not required <= present or (role != "certificate_state" and present != required):
            _invalid()
    return tuple(result)


def parse_backup_manifest(raw: bytes) -> BackupManifest:
    """Check declarations only; no I/O, application state, decryption, or DB access."""
    try:
        if type(raw) is not bytes or not raw or len(raw) > MAX_MANIFEST_BYTES:
            _invalid()
        text = raw.decode("utf-8", errors="strict")
        _check_json_depth(text)
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
        )
        _check_json_tree(value)
        row = _object(
            value,
            {"format", "version", "created_at", "source", "database", "coverage",
             "required_configuration", "files"},
        )
        if row["format"] != _FORMAT or type(row["version"]) is not int or row["version"] != 1:
            _invalid()
        created = row["created_at"]
        if type(created) is not str or _UTC_SECONDS.fullmatch(created) is None:
            _invalid()
        # A datetime constructor validates actual dates without strptime's lazy
        # locale/timezone initialization; preserve the original canonical text.
        datetime(
            int(created[0:4]), int(created[5:7]), int(created[8:10]),
            int(created[11:13]), int(created[14:16]), int(created[17:19]),
        )
        coverage = _coverage(row["coverage"])
        database = _database(row["database"])
        return BackupManifest(
            _FORMAT, 1, created, _source(row["source"]), database,
            coverage, _configuration(row["required_configuration"]),
            _files(row["files"], coverage, database),
        )
    except BackupValidationError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise BackupValidationError() from None
