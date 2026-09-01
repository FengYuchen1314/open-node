"""Manifest declarations are bounded and immutable, never proof of recovery readiness."""

import builtins
import copy
import hashlib
import json
import logging
import os
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest
from open_node.domain import backup
from open_node.domain.backup import (
    MAX_COMPONENT_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_MANIFEST_BYTES,
    MAX_PATH_BYTES,
    MAX_TOTAL_FILE_BYTES,
    BackupCoverage,
    BackupDatabase,
    BackupFileEntry,
    BackupManifest,
    BackupSource,
    BackupValidationError,
    parse_backup_manifest,
    validate_backup_path,
)

REVISION = "a" * 40
DIGEST = "b" * 64
ERROR = "Invalid backup package."
COMPONENTS = (
    ("certificates", "certificate_state", (
        "data/certificates/vault.key", "data/certificates/vault.initialized",
    )),
    ("external_subscriptions", "external_state", (
        "data/external-subscriptions/vault.key", "data/external-subscriptions/vault.initialized",
    )),
    ("federation", "federation_state", (
        "data/federation/vault.key", "data/federation/vault.initialized",
    )),
    ("notifications", "notification_state", (
        "data/notifications/telegram.key", "data/notifications/telegram.initialized",
    )),
    ("agent_identity", "agent_identity", ("secrets/agent-identity.seed",)),
)


def entry(path="data/open-node.db", role="database", size=0):
    return {"path": path, "role": role, "size": size, "sha256": DIGEST}


def manifest():
    return {
        "format": "open-node-control-plane-backup",
        "version": 1,
        "created_at": "2026-08-31T12:34:56Z",
        "source": {"git_revision": None, "image_id": None, "image_revision": None},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": {name: "unknown" for name, _role, _paths in COMPONENTS},
        "required_configuration": ["deployment_settings"],
        "files": [entry()],
    }


def include(value, name):
    value["coverage"][name] = "included"
    for component, role, paths in COMPONENTS:
        if component == name:
            value["files"].extend(entry(path, role) for path in paths)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def invalid(value):
    with pytest.raises(BackupValidationError) as caught:
        parse_backup_manifest(encoded(value))
    assert caught.value.args == (ERROR,)
    assert str(caught.value) == ERROR


def changed(path, replacement):
    value = manifest()
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    return value


def test_fixed_resource_contract():
    assert (MAX_MANIFEST_BYTES, MAX_FILES) == (1024 * 1024, 4096)
    assert (MAX_FILE_BYTES, MAX_TOTAL_FILE_BYTES) == (1024**3, 1024**3)
    assert (MAX_PATH_BYTES, MAX_COMPONENT_BYTES) == (1024, 255)
    assert (MAX_JSON_DEPTH, MAX_JSON_NODES) == (12, 80000)


def test_minimal_manifest_and_exact_public_fields():
    result = parse_backup_manifest(encoded(manifest()))
    assert isinstance(result, BackupManifest)
    assert result.format == "open-node-control-plane-backup"
    assert result.version == 1
    assert result.created_at == "2026-08-31T12:34:56Z"
    assert result.source == BackupSource(None, None, None)
    assert result.database == BackupDatabase("sqlite", "standalone", None)
    assert result.coverage == BackupCoverage("unknown", "unknown", "unknown", "unknown")
    assert result.required_configuration == ("deployment_settings",)
    assert result.files == (BackupFileEntry("data/open-node.db", "database", 0, DIGEST),)
    assert {field.name for field in fields(result)} == set(manifest())


def test_all_roles_and_unicode_names_preserve_declarations():
    value = manifest()
    for component, _role, _paths in COMPONENTS:
        include(value, component)
    value["source"] = {
        "git_revision": REVISION, "image_id": "sha256:" + DIGEST, "image_revision": REVISION,
    }
    value["database"]["schema_fingerprint"] = DIGEST
    value["required_configuration"] = ["subscriber_totp_key", "deployment_settings"]
    path = "data/certificates/证书📦/账户/café 资料.pem"
    value["files"].append(entry(path, "certificate_state", 12))
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    result = parse_backup_manifest(raw)
    assert result.source == BackupSource(REVISION, "sha256:" + DIGEST, REVISION)
    assert result.database.schema_fingerprint == DIGEST
    assert all(getattr(result.coverage, name) == "included" for name, _r, _p in COMPONENTS)
    assert result.files[-1] == BackupFileEntry(path, "certificate_state", 12, DIGEST)
    assert result.required_configuration == ("subscriber_totp_key", "deployment_settings")


@pytest.mark.parametrize("git", [None, REVISION])
@pytest.mark.parametrize("image", [None, "sha256:" + DIGEST])
@pytest.mark.parametrize("revision", [None, REVISION])
def test_nullable_source_fields(git, image, revision):
    value = manifest()
    value["source"] = {"git_revision": git, "image_id": image, "image_revision": revision}
    assert parse_backup_manifest(encoded(value)).source == BackupSource(git, image, revision)


def test_nonnull_source_revisions_must_match():
    value = manifest()
    value["source"].update(git_revision=REVISION, image_revision="c" * 40)
    invalid(value)


@pytest.mark.parametrize("created", ["0001-01-01T00:00:00Z", "2000-02-29T23:59:59Z",
                                    "2024-02-29T00:00:00Z", "9999-12-31T23:59:59Z"])
def test_canonical_real_utc_dates(created):
    assert parse_backup_manifest(encoded(changed(("created_at",), created))).created_at == created


@pytest.mark.parametrize("created", [
    "0000-01-01T00:00:00Z", "1900-02-29T00:00:00Z", "2026-02-29T00:00:00Z",
    "2026-04-31T00:00:00Z", "2026-13-01T00:00:00Z", "2026-00-01T00:00:00Z",
    "2026-01-00T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z",
    "2026-01-01T00:00:60Z", "2026-1-01T00:00:00Z", "2026-01-01t00:00:00Z",
    "2026-01-01T00:00:00z", "2026-01-01 00:00:00Z", "2026-01-01T00:00:00+00:00",
    "2026-01-01T00:00:00.000Z", "２０２６-01-01T00:00:00Z", "2026-01-01T00:00:00Z\n",
])
def test_reject_noncanonical_or_impossible_dates(created):
    invalid(changed(("created_at",), created))


@pytest.mark.parametrize("path", [
    ("format",), ("created_at",), ("source", "git_revision"), ("source", "image_id"),
    ("source", "image_revision"), ("database", "engine"), ("database", "layout"),
    ("database", "schema_fingerprint"), ("files", 0, "path"), ("files", 0, "role"),
    ("files", 0, "sha256"),
])
@pytest.mark.parametrize("replacement", [False, True, 0, 1, [], {}])
def test_string_fields_never_coerce_types(path, replacement):
    invalid(changed(path, replacement))


@pytest.mark.parametrize("path", [("version",), ("files", 0, "size")])
@pytest.mark.parametrize("replacement", [None, False, True, "0", "1", [], {}, -1, 1.0])
def test_integer_fields_are_strict_nonbool(path, replacement):
    invalid(changed(path, replacement))


@pytest.mark.parametrize("replacement", [0, 2, -1, 10**100])
def test_only_version_one(replacement):
    invalid(changed(("version",), replacement))


@pytest.mark.parametrize("path,replacement", [
    (("format",), "mmwx-database-backup-v1"), (("format",), None),
    (("created_at",), None), (("files", 0, "role"), None),
    (("database", "engine"), "postgres"), (("database", "layout"), "fileset"),
    (("database", "engine"), None), (("database", "layout"), None),
    (("source", "git_revision"), "A" * 40), (("source", "git_revision"), "a" * 39),
    (("source", "image_revision"), "a" * 41),
    (("source", "image_id"), "SHA256:" + DIGEST), (("source", "image_id"), DIGEST),
    (("source", "image_id"), "sha256:" + "B" * 64),
    (("database", "schema_fingerprint"), "b" * 63),
    (("database", "schema_fingerprint"), "b" * 65),
    (("files", 0, "sha256"), "b" * 63), (("files", 0, "sha256"), "b" * 65),
    (("files", 0, "sha256"), "B" * 64), (("files", 0, "sha256"), "g" * 64),
    (("files", 0, "sha256"), None), (("files", 0, "sha256"), DIGEST + "\n"),
    (("files", 0, "role"), "unknown"), (("files", 0, "path"), None),
])
def test_fixed_enums_and_digest_shapes(path, replacement):
    invalid(changed(path, replacement))


@pytest.mark.parametrize("path", [(), ("source",), ("database",), ("coverage",), ("files", 0)])
def test_every_object_has_exact_keys(path):
    value = manifest()
    target = value
    for part in path:
        target = target[part]
    for key in tuple(target):
        candidate = copy.deepcopy(value)
        parent = candidate
        for part in path:
            parent = parent[part]
        del parent[key]
        invalid(candidate)
    target["attacker-secret-field"] = "attacker-secret-value"
    invalid(value)


@pytest.mark.parametrize("path", [("source",), ("database",), ("coverage",), ("files", 0)])
@pytest.mark.parametrize("replacement", [None, False, 0, "", []])
def test_objects_do_not_coerce(path, replacement):
    invalid(changed(path, replacement))


@pytest.mark.parametrize("value", [None, False, 0, "", [], [manifest()]])
def test_root_requires_object(value):
    invalid(value)


@pytest.mark.parametrize("value", [None, "deployment_settings", {}, False, 0, [],
    ["subscriber_totp_key"], ["deployment_settings", "deployment_settings"],
    ["deployment_settings", "subscriber_totp_key", "subscriber_totp_key"],
    ["deployment_settings", "OPEN_NODE_SUBSCRIBER_TOTP_KEY"], ["deployment_settings", None],
    ["deployment_settings", {"path": "/etc/secret"}], ["deployment_settings", True],
])
def test_configuration_is_a_unique_closed_list(value):
    invalid(changed(("required_configuration",), value))


@pytest.mark.parametrize("component,role,paths", COMPONENTS)
@pytest.mark.parametrize("state", [None, True, 0, [], {}, "", "excluded", "Included"])
def test_coverage_states_are_strict(component, role, paths, state):
    invalid(changed(("coverage", component), state))


@pytest.mark.parametrize("component,role,paths", COMPONENTS)
@pytest.mark.parametrize("state", ["unknown", "not_configured"])
def test_nonincluded_coverage_forbids_files(component, role, paths, state):
    value = manifest()
    value["coverage"][component] = state
    assert getattr(parse_backup_manifest(encoded(value)).coverage, component) == state
    value["files"].append(entry(paths[0], role))
    invalid(value)


@pytest.mark.parametrize("component,role,paths", COMPONENTS)
def test_included_coverage_requires_every_fixed_file(component, role, paths):
    value = manifest()
    include(value, component)
    assert getattr(parse_backup_manifest(encoded(value)).coverage, component) == "included"
    for path in paths:
        candidate = copy.deepcopy(value)
        candidate["files"] = [item for item in candidate["files"] if item["path"] != path]
        invalid(candidate)


@pytest.mark.parametrize("role,path", [
    ("database", "manifest.json"), ("database", "data/open-node.db-wal"),
    ("database", "data/open-node.db-shm"), ("database", "data/open-node.db-journal"),
    ("database", "data/another.db"), ("database", "secrets/agent-identity.seed"),
    ("certificate_state", "data/certificates"), ("certificate_state", "data/open-node.db"),
    ("certificate_state", "data/certificates-other/vault.key"),
    ("external_state", "data/external-subscriptions/extra.key"),
    ("external_state", "data/notifications/telegram.key"),
    ("notification_state", "data/notifications/extra.key"),
    ("agent_identity", "secrets/other.seed"), ("agent_identity", "/etc/open-node/identity"),
])
def test_exact_role_to_path_mapping(role, path):
    value = manifest()
    for component, _role, _paths in COMPONENTS:
        include(value, component)
    value["files"].append(entry(path, role))
    invalid(value)


def test_reserved_names_are_not_blanket_banned_inside_certificate_state():
    value = manifest()
    include(value, "certificates")
    for name in ("manifest.json", "open-node.db-wal", "open-node.db-shm", "open-node.db-journal"):
        value["files"].append(entry("data/certificates/" + name, "certificate_state"))
    assert len(parse_backup_manifest(encoded(value)).files) == 7


def test_database_is_required_and_unique():
    value = manifest()
    include(value, "certificates")
    value["files"] = value["files"][1:]
    invalid(value)
    value = manifest()
    value["files"].append(entry())
    invalid(value)


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_or_parent_file_conflicts_in_both_orders(reverse):
    value = manifest()
    include(value, "certificates")
    base = "data/certificates/account"
    entries = [entry(base, "certificate_state"), entry(base + "-other", "certificate_state"),
               entry(base + "/key.pem", "certificate_state")]
    value["files"].extend(reversed(entries) if reverse else entries)
    invalid(value)
    value = manifest()
    include(value, "certificates")
    value["files"].append(copy.deepcopy(value["files"][-1]))
    invalid(value)


@pytest.mark.parametrize("value", [None, False, 1, "", [], {}, [None]])
def test_files_requires_nonempty_array(value):
    invalid(changed(("files",), value))


def test_file_count_at_and_over_limit_without_large_payload_files():
    value = manifest()
    include(value, "certificates")
    value["files"].extend(
        entry(f"data/certificates/file-{index}", "certificate_state")
        for index in range(MAX_FILES - 3)
    )
    raw = encoded(value)
    assert len(raw) < MAX_MANIFEST_BYTES
    assert len(parse_backup_manifest(raw).files) == MAX_FILES
    value["files"].append(entry("data/certificates/one-more", "certificate_state"))
    assert len(encoded(value)) < MAX_MANIFEST_BYTES
    invalid(value)


def test_declared_size_and_total_boundaries_do_not_allocate_payloads():
    value = manifest()
    value["files"][0]["size"] = MAX_FILE_BYTES
    assert parse_backup_manifest(encoded(value)).files[0].size == MAX_FILE_BYTES
    value["files"][0]["size"] += 1
    invalid(value)
    value = manifest()
    include(value, "certificates")
    value["files"][0]["size"] = MAX_TOTAL_FILE_BYTES - 2
    value["files"][1]["size"] = value["files"][2]["size"] = 1
    assert sum(item.size for item in parse_backup_manifest(encoded(value)).files) == MAX_FILE_BYTES
    value["files"][2]["size"] += 1
    invalid(value)


@pytest.mark.parametrize("path", [
    "data/open-node.db", "data/certificates/café/证书📦.pem", "a/a b", "a/中\u00a0文",
    "a/%2e%2e/%2F", "a/.hidden", "a/[{}]", "a/é", "a/case", "a/CASE",
])
def test_literal_valid_paths_are_returned_unchanged(path):
    assert validate_backup_path(path) is path


@pytest.mark.parametrize("path", [
    None, True, 1, b"a/b", [], {}, "", "/absolute", "//server/share", "C:/file",
    "a:b", "a\\b", "a/", "a//b", "./a", "a/./b", "..", "a/../b", "a/..",
    " a", "a ", "a/ b", "a/b ", "a/b.", ".", "a\u00a0/b", "a/\u00a0b",
    "a/e\u0301", "a/\u212b", "a/\x00b", "a/\nb", "a/\tb", "a/\x7fb",
    "a/\u0085b", "a/\u200bb", "a/\u200cb", "a/\u200db", "a/\ufeffb",
    "a/\u2028b", "a/\u2029b", "a/\ud800", "a/\udfff",
])
def test_reject_noncanonical_or_unsafe_paths(path):
    with pytest.raises(BackupValidationError, match=r"^Invalid backup package\.$"):
        validate_backup_path(path)


def test_path_component_and_utf8_byte_limits():
    assert validate_backup_path("a" * 255) == "a" * 255
    assert validate_backup_path("中" * 85) == "中" * 85
    for value in ("a" * 256, "中" * 85 + "a"):
        with pytest.raises(BackupValidationError):
            validate_backup_path(value)
    path = "/".join(["a" * 255, "b" * 255, "c" * 255, "d" * 254, "e"])
    assert len(path.encode()) == MAX_PATH_BYTES
    assert validate_backup_path(path) == path
    with pytest.raises(BackupValidationError):
        validate_backup_path(path + "f")


def test_manifest_raw_byte_limit_includes_formatting_whitespace(monkeypatch):
    raw = encoded(manifest())
    exact = raw + b" " * (MAX_MANIFEST_BYTES - len(raw))
    assert len(exact) == MAX_MANIFEST_BYTES
    assert parse_backup_manifest(exact).version == 1
    with monkeypatch.context() as scoped:
        scoped.setattr(backup.json, "loads", lambda *_a, **_k: pytest.fail("decoder called"))
        with pytest.raises(BackupValidationError):
            parse_backup_manifest(exact + b" ")


@pytest.mark.parametrize("raw", [None, False, 0, "{}", bytearray(b"{}"), memoryview(b"{}"), b""])
def test_raw_requires_exact_nonempty_bytes(raw):
    with pytest.raises(BackupValidationError):
        parse_backup_manifest(raw)


def test_input_subclasses_and_stringification_are_not_accepted():
    class Text(str):
        pass

    class Bytes(bytes):
        pass

    class Hostile:
        def __str__(self):
            pytest.fail("untrusted input was stringified")

    for value in (Text("a/b"), Hostile()):
        with pytest.raises(BackupValidationError):
            validate_backup_path(value)
    for value in (Bytes(encoded(manifest())), Hostile()):
        with pytest.raises(BackupValidationError):
            parse_backup_manifest(value)


@pytest.mark.parametrize("raw", [
    b"\xff", b"\xed\xa0\x80", b"\xef\xbb\xbf{}", "{}".encode("utf-16"),
    b'{"secret":', b'{"a":1,}',
    b'[{]}', b'{"a":"unterminated}', b'{"a":"bad\\escape"}', b'{}{}', b']', b' ',
    b'{"a":"\x00"}', b'{"a":"\\ud800"}', b'{"\\udfff":1}', b'{"a\\n":0}',
])
def test_malformed_or_non_utf8_json_has_only_safe_error(raw):
    with pytest.raises(BackupValidationError) as caught:
        parse_backup_manifest(raw)
    assert str(caught.value) == ERROR


@pytest.mark.parametrize("token", [b"0.0", b"1.0", b"1e0", b"1e400", b"-1e400",
                                   b"NaN", b"Infinity", b"-Infinity"])
def test_all_float_and_nonfinite_spellings_are_rejected(token):
    raw = encoded(manifest()).replace(b'"size":0', b'"size":' + token)
    with pytest.raises(BackupValidationError):
        parse_backup_manifest(raw)


@pytest.mark.parametrize("original,replacement", [
    (b'"version":1', b'"version":1,"version":1'),
    (b'"version":1', b'"version":1,"\\u0076ersion":1'),
    (b'"git_revision":null', b'"git_revision":null,"git_revision":null'),
    (b'"size":0', b'"size":0,"size":0'),
])
def test_duplicate_keys_at_every_depth_are_rejected(original, replacement):
    raw = encoded(manifest()).replace(original, replacement)
    with pytest.raises(BackupValidationError):
        parse_backup_manifest(raw)


def test_depth_limit_counts_root_container_before_decoding(monkeypatch):
    at_limit = "[" * MAX_JSON_DEPTH + "0" + "]" * MAX_JSON_DEPTH
    backup._check_json_depth(at_limit)
    over_limit = "[" + at_limit + "]"
    with monkeypatch.context() as scoped:
        scoped.setattr(backup.json, "loads", lambda *_a, **_k: pytest.fail("decoder called"))
        with pytest.raises(BackupValidationError):
            parse_backup_manifest(over_limit.encode())


def test_depth_scan_ignores_brackets_in_strings_and_handles_escaping():
    text = json.dumps({"key": '[{\\\"' * 30})
    backup._check_json_depth(text)
    value = manifest()
    include(value, "certificates")
    path = "data/certificates/" + "[{}]" * 30
    value["files"].append(entry(path, "certificate_state"))
    assert parse_backup_manifest(encoded(value)).files[-1].path == path


def test_node_budget_counts_containers_and_values_but_not_keys():
    # One root list plus 79,999 scalar values is exactly 80,000 nodes.
    backup._check_json_tree([None] * (MAX_JSON_NODES - 1))
    with pytest.raises(BackupValidationError):
        backup._check_json_tree([None] * MAX_JSON_NODES)
    # Keys are bounded by the raw JSON limit and exact schema, not the node budget.
    backup._check_json_tree({str(index): None for index in range(MAX_JSON_NODES - 1)})
    with pytest.raises(BackupValidationError):
        backup._check_json_tree({str(index): None for index in range(MAX_JSON_NODES)})
    with pytest.raises(BackupValidationError):
        parse_backup_manifest(encoded([None] * MAX_JSON_NODES))


def test_models_are_deeply_frozen_slotted_and_hashable():
    result = parse_backup_manifest(encoded(manifest()))
    for item in (result, result.source, result.database, result.coverage, result.files[0]):
        assert is_dataclass(item)
        assert not hasattr(item, "__dict__")
        name = fields(item)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(item, name, getattr(item, name))
        hash(item)
    assert type(result.files) is tuple
    assert type(result.required_configuration) is tuple
    with pytest.raises(AttributeError):
        result.files.append(entry())
    with pytest.raises(AttributeError):
        result.required_configuration.append("subscriber_totp_key")


def test_parsing_performs_no_io_logging_or_application_imports(monkeypatch):
    raw = encoded(manifest())
    real_import = builtins.__import__

    def fail(*_args, **_kwargs):
        pytest.fail("manifest parsing performed an external operation")

    def guarded_import(name, *args, **kwargs):
        if name == "open_node.main" or name.startswith("open_node.services"):
            fail()
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "open", fail)
        scoped.setattr(builtins, "__import__", guarded_import)
        scoped.setattr(os, "open", fail)
        scoped.setattr(os, "mkdir", fail)
        scoped.setattr(Path, "open", fail)
        scoped.setattr(Path, "mkdir", fail)
        scoped.setattr(logging.Logger, "_log", fail)
        assert parse_backup_manifest(raw).version == 1
        assert validate_backup_path("data/open-node.db") == "data/open-node.db"


def test_safe_errors_do_not_echo_attacker_text_or_decoder_details():
    secret = "SYNTHETIC-NOT-A-CREDENTIAL-DO-NOT-ECHO"
    for raw in (b'{"' + secret.encode() + b'":', encoded(changed(("files", 0, "path"), secret))):
        with pytest.raises(BackupValidationError) as caught:
            parse_backup_manifest(raw)
        assert str(caught.value) == ERROR
        assert repr(caught.value) == "BackupValidationError('Invalid backup package.')"
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)
        assert caught.value.args == (ERROR,)


def test_structure_acceptance_is_not_database_key_or_provenance_verification():
    value = manifest()
    include(value, "notifications")
    payloads = (b"not a SQLite database", b"not a Fernet key", b"not an initialized marker")
    for item, content in zip(value["files"], payloads, strict=True):
        item["size"] = len(content)
        item["sha256"] = hashlib.sha256(content).hexdigest()
    result = parse_backup_manifest(encoded(value))
    assert tuple(item.sha256 for item in result.files) == tuple(
        hashlib.sha256(content).hexdigest() for content in payloads
    )
    assert result.source == BackupSource(None, None, None)
    assert result.required_configuration == ("deployment_settings",)
    assert {field.name for field in fields(result)} == set(manifest())
