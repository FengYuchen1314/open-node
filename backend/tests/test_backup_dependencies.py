"""Real SQLite/Fernet/PEM dependency checks; never use a live application Store.

Fixtures manufacture synthetic private state in their owned temporary directory.
No test contacts Telegram, ACME, an Agent, or production. Controlled budget/clock
tests are distinct from real SQLite row/ciphertext boundary tests.
"""

import hashlib
import hmac
import io
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID
from open_node.services import backup_dependencies as dependencies

SAFE_ERROR = "Backup dependency verification is unavailable."
SENTINEL = "synthetic-private-sentinel-must-not-appear-in-report"
APP = Path(__file__).resolve().parents[1] / "app"
CERT = "data/certificates/"
EXTERNAL = "data/external-subscriptions/"
FEDERATION = "data/federation/"
NOTIFICATIONS = "data/notifications/"
IDENTITY = "secrets/agent-identity.seed"
PURPOSE = "open-node.notifications.telegram.v1"
MARKER = b"Open Node certificate vault\n"
TOKEN = "123456:" + "A" * 32
OTP_SECRET = "JBSWY3DPEHPK3PXP"
PROFILE, PROVIDER, VERSION, JOB, SOURCE, NODE, PREVIEW, FEDERATED = (
    str(UUID(int=index)) for index in range(1, 9)
)

# Independently declared from actual model columns, not imported checker internals.
DDL = """
CREATE TABLE product_users (username TEXT PRIMARY KEY, quota INTEGER DEFAULT 1);
CREATE TABLE certificate_dns_providers (
 id TEXT PRIMARY KEY, provider TEXT, credentials TEXT, credential_fields JSON);
CREATE TABLE managed_certificates (
 id TEXT PRIMARY KEY, provider_id TEXT, version_id TEXT, active_job_id TEXT,
 eab TEXT, auto_renew INTEGER DEFAULT 0, expires_at REAL DEFAULT 0);
CREATE TABLE certificate_jobs (
 id TEXT PRIMARY KEY, certificate_id TEXT, parameters JSON, status TEXT DEFAULT 'failed');
CREATE TABLE certificate_versions (
 id TEXT PRIMARY KEY, certificate_id TEXT, encrypted_material TEXT,
 details JSON, fingerprint TEXT, expires_at REAL DEFAULT 0);
CREATE TABLE certificate_targets (certificate_id TEXT, version_id TEXT);
CREATE TABLE external_subscription_sources (
 id TEXT PRIMARY KEY, owner_username TEXT, secret TEXT, url_digest TEXT,
 enabled INTEGER DEFAULT 0);
CREATE TABLE external_subscription_nodes (
 id TEXT PRIMARY KEY, source_id TEXT, secret TEXT, enabled INTEGER DEFAULT 0);
CREATE TABLE external_subscription_previews (
 id TEXT PRIMARY KEY, source_id TEXT, secret TEXT, expires_at REAL DEFAULT 0);
CREATE TABLE federated_servers (
 id TEXT PRIMARY KEY, owner_url TEXT, token_secret TEXT, name TEXT DEFAULT 'remote');
CREATE TABLE notification_settings (
 id INTEGER PRIMARY KEY, token_ciphertext TEXT, key_fingerprint TEXT, enabled INTEGER DEFAULT 0);
CREATE TABLE administrator (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE administrator_factors (
 administrator_id INTEGER PRIMARY KEY, totp_secret TEXT, pending_secret TEXT,
 pending_expires_at REAL DEFAULT 0);
CREATE TABLE operator_challenges (
 administrator_id INTEGER, pending_secret TEXT, expires_at REAL DEFAULT 0);
CREATE TABLE subscriber_accounts (
 username TEXT PRIMARY KEY, totp_secret TEXT, pending_secret TEXT,
 pending_expires_at REAL DEFAULT 0);
CREATE TABLE unrelated_business_sentinel (id INTEGER PRIMARY KEY, value TEXT);
INSERT INTO unrelated_business_sentinel VALUES (1, 'unchanged-business-value');
"""


def require(condition, message="Controlled dependency assertion failed"):
    if not condition:
        raise AssertionError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def seal(key, value, *, raw=False):
    body = value if raw else json.dumps(value, ensure_ascii=False).encode()
    return Fernet(key).encrypt(body).decode("ascii")


def otp(key, username, secret=OTP_SECRET):
    bound = hashlib.sha256(username.encode()).hexdigest() + "\n" + secret
    return seal(key, bound.encode(), raw=True)


def external(key, source=SOURCE, owner="用户甲", purpose="source", value=None):
    if value is None:
        value = {"url": "https://example.invalid/" + SENTINEL, "user_agent": "test/1"}
    return seal(key, {"version": 1, "owner": owner, "source": source, "purpose": purpose,
                      "value": value})


def url_digest(key, url):
    return hmac.new(key, b"open-node/external-url/v1\0" + url.encode(), hashlib.sha256).hexdigest()


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def certificate_material(*, expired=False, future=False, key=None):
    key = key or ec.generate_private_key(ec.SECP256R1())
    start = datetime(2020, 1, 1, tzinfo=UTC) if expired else datetime(2026, 1, 1, tzinfo=UTC)
    if future:
        start = datetime(2090, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=30 if expired else 3650)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "backup.example.invalid")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1001)
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("backup.example.invalid")]),
                           critical=False).sign(key, hashes.SHA256()))
    return {
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "key_pem": pem(key).decode(), "domains": ["backup.example.invalid"],
        "not_before": start.timestamp(), "expires_at": end.timestamp(), "serial": "1001",
        "issuer": cert.issuer.rfc4514_string(),
    }


class Fixture:
    def __init__(self, directory, *, full=True):
        self.directory = directory
        self.path = directory / "snapshot.sqlite3"
        self.connection = sqlite3.connect(self.path)
        self.connection.executescript(DDL)
        self.keys = {name: Fernet.generate_key()
                     for name in ("certificate", "external", "federation", "notification", "totp")}
        self.sources = {}
        self.material = certificate_material(expired=True)
        self.seed = ed25519.Ed25519PrivateKey.generate().private_bytes_raw()
        self.public = (ed25519.Ed25519PrivateKey.from_private_bytes(self.seed)
                       .public_key().public_bytes_raw())
        if full:
            self.populate()
        self.connection.commit()
        self.connection.close()
        self.connection = self.readonly()

    def readonly(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def stream(self, path, value):
        self.sources[path] = io.BytesIO(value)

    def populate(self):
        db = self.connection
        db.execute("INSERT INTO product_users(username) VALUES (?)", ("用户甲",))
        db.execute("INSERT INTO administrator VALUES (1, ?)", ("管理员",))
        for prefix, keyname in (
            (CERT, "certificate"), (EXTERNAL, "external"), (FEDERATION, "federation"),
        ):
            self.stream(prefix + "vault.key", self.keys[keyname])
            self.stream(prefix + "vault.initialized", MARKER)
        key = self.keys["notification"]
        fingerprint = hashlib.sha256(PURPOSE.encode() + b"\x00" + key).hexdigest()
        self.stream(NOTIFICATIONS + "telegram.key", key)
        self.stream(NOTIFICATIONS + "telegram.initialized",
                    canonical({"purpose": PURPOSE, "key_fingerprint": fingerprint}))
        self.stream(IDENTITY, self.seed)
        key = self.keys["certificate"]
        db.execute("INSERT INTO certificate_dns_providers VALUES (?,?,?,?)",
                   (PROVIDER, "cloudflare", seal(key, {"CF_DNS_API_TOKEN": SENTINEL}),
                    '["CF_DNS_API_TOKEN"]'))
        db.execute("INSERT INTO managed_certificates(id,provider_id,version_id,active_job_id,eab) "
                   "VALUES (?,?,?,?,?)", (PROFILE, PROVIDER, VERSION, JOB,
                                          seal(key, {"kid": "key-id", "hmac": SENTINEL})))
        parameters = {"email": "operator@example.invalid", "eab_action": "replace",
                      "eab": seal(key, {"kid": "later-id", "hmac": SENTINEL})}
        db.execute("INSERT INTO certificate_jobs(id,certificate_id,parameters) VALUES (?,?,?)",
                   (JOB, PROFILE, json.dumps(parameters)))
        self.set_version(self.material)
        db.execute("INSERT INTO certificate_targets VALUES (?,?)", (PROFILE, VERSION))
        source_secret = {"url": "https://example.invalid/" + SENTINEL, "user_agent": "test/1"}
        db.execute("INSERT INTO external_subscription_sources(id,owner_username,secret,url_digest) "
                   "VALUES (?,?,?,?)", (SOURCE, "用户甲", external(self.keys["external"]),
                                        url_digest(self.keys["external"], source_secret["url"])))
        db.execute("INSERT INTO external_subscription_nodes(id,source_id,secret) VALUES (?,?,?)",
                   (NODE, SOURCE, external(self.keys["external"], purpose="node:" + NODE,
                                           value={"server": "example.invalid",
                                                  "password": SENTINEL})))
        db.execute("INSERT INTO external_subscription_previews(id,source_id,secret) VALUES (?,?,?)",
                   (PREVIEW, SOURCE, external(self.keys["external"], purpose="preview:" + PREVIEW,
                                              value={"nodes": [{"id": NODE}], "metadata": {}})))
        owner_url = "https://owner.example.invalid"
        db.execute(
            "INSERT INTO federated_servers(id,owner_url,token_secret) VALUES (?,?,?)",
            (FEDERATED, owner_url, seal(self.keys["federation"], {
                "version": 1, "server": FEDERATED, "owner_url": owner_url,
                "purpose": "federation-token", "token": "A" * 43,
            })),
        )
        db.execute("INSERT INTO notification_settings(id,token_ciphertext,key_fingerprint) "
                   "VALUES (1,?,?)",
                   (seal(self.keys["notification"], {"purpose": PURPOSE, "token": TOKEN}),
                    fingerprint))
        admin_secret = otp(self.keys["totp"], "管理员")
        subscriber_secret = otp(self.keys["totp"], "用户甲")
        db.execute("INSERT INTO administrator_factors(administrator_id,totp_secret,pending_secret) "
                   "VALUES (1,?,?)", (admin_secret, admin_secret))
        db.execute("INSERT INTO operator_challenges(administrator_id,pending_secret) VALUES (1,?)",
                   (admin_secret,))
        db.execute("INSERT INTO subscriber_accounts(username,totp_secret,pending_secret) "
                   "VALUES (?,?,?)", ("用户甲", subscriber_secret, subscriber_secret))

    def set_version(self, material, *, fingerprint=True):
        digest = (x509.load_pem_x509_certificate(material["cert_pem"].encode())
                  .fingerprint(hashes.SHA256()).hex()) if fingerprint else None
        details = {key: value for key, value in material.items()
                   if key not in {"cert_pem", "key_pem"}}
        self.connection.execute("INSERT OR REPLACE INTO certificate_versions "
                                "(id,certificate_id,encrypted_material,details,fingerprint) "
                                "VALUES (?,?,?,?,?)",
                                (VERSION, PROFILE, seal(self.keys["certificate"], material),
                                 json.dumps(details), digest))

    @contextmanager
    def write(self):
        self.connection.close()
        self.connection = sqlite3.connect(self.path)
        try:
            yield self.connection
            self.connection.commit()
        finally:
            self.connection.close()
            self.connection = self.readonly()

    def change(self, sql, parameters=()):
        with self.write() as db:
            db.execute(sql, parameters)

    def check(self, **kwargs):
        options = {"totp_key": self.keys["totp"], "agent_public_key": self.public}
        options.update(kwargs)
        return dependencies.check_backup_dependencies(self.connection, self.sources, **options)

    def close(self):
        self.connection.close()
        for stream in self.sources.values():
            stream.close()


@pytest.fixture
def fixture(tmp_path):
    value = Fixture(tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def empty(tmp_path):
    value = Fixture(tmp_path, full=False)
    try:
        yield value
    finally:
        value.close()


def fails(fixture, **kwargs):
    with pytest.raises(dependencies.BackupDependencyError) as caught:
        fixture.check(**kwargs)
    require(str(caught.value) == SAFE_ERROR)
    require(caught.value.code == "backup_dependencies_unavailable")
    require(caught.value.__cause__ is None)
    require(caught.value.__suppress_context__)


def snapshot(path):
    info = path.stat()
    return (hashlib.sha256(path.read_bytes()).hexdigest(), info.st_size, info.st_ino,
            info.st_dev, info.st_mtime_ns, info.st_ctime_ns)


def test_all_supported_dependencies_and_expired_disabled_rows_are_checked(fixture):
    before = snapshot(fixture.path)
    contents = {path: stream.getvalue() for path, stream in fixture.sources.items()}
    report = fixture.check()
    assert report.coverage == dependencies.BackupCoverage(*(["included"] * 5))
    assert report.checked_ciphertexts == 14
    assert report.ciphertext_counts == (("certificates", 4), ("external_subscriptions", 3),
                                        ("federation", 1), ("notifications", 1), ("totp", 5))
    assert report.database_dependencies == frozenset({
        "certificates", "external_subscriptions", "federation", "notifications", "totp",
    })
    assert report.database_modules_present == report.database_dependencies
    assert report.totp_status == "verified"
    assert report.agent_identity_matches_runtime is True
    assert report.required_configuration == ("deployment_settings", "subscriber_totp_key")
    assert report.restoration_ready is False and report.remote_agent_trust == "not_checked"
    assert "certificate_ciphertext_row_or_purpose_binding" in report.not_checked
    assert "totp_active_or_pending_purpose_binding" in report.not_checked
    assert {"source_authenticity", "snapshot_consistency"} <= set(report.not_checked)
    safe = repr(report) + json.dumps(asdict(report), default=sorted)
    private = (SENTINEL, TOKEN, OTP_SECRET, str(fixture.path),
               fixture.keys["totp"].decode(), "用户甲", "管理员")
    require(all(secret not in safe for secret in private))
    require(snapshot(fixture.path) == before)
    require(contents == {path: stream.getvalue() for path, stream in fixture.sources.items()})
    assert fixture.connection.in_transaction is False
    assert fixture.connection.execute("PRAGMA query_only").fetchone() == (1,)
    with pytest.raises(sqlite3.OperationalError):
        fixture.connection.execute("UPDATE unrelated_business_sentinel SET value='bad'")
    actual = fixture.connection.execute("SELECT value FROM unrelated_business_sentinel").fetchone()
    assert actual == ("unchanged-business-value",)
    with pytest.raises(FrozenInstanceError):
        report.restoration_ready = True


def test_empty_database_inventory_is_conservative_and_creates_no_keys(empty):
    before = set(empty.directory.iterdir())
    report = empty.check(agent_public_key=None)
    assert report.coverage == dependencies.BackupCoverage(*(["unknown"] * 5))
    assert report.database_dependencies == frozenset()
    assert report.database_modules_present == frozenset()
    assert report.totp_status == "not_configured"
    assert report.checked_ciphertexts == 0
    assert report.required_configuration == ("deployment_settings",)
    assert empty.sources == {}
    assert set(empty.directory.iterdir()) == before


@pytest.mark.parametrize("kind", ["profile", "queued_job", "target"])
def test_uninitialized_certificate_module_does_not_invent_key_dependency(empty, kind):
    with empty.write() as db:
        db.execute("INSERT INTO managed_certificates(id) VALUES (?)", (PROFILE,))
        if kind == "queued_job":
            db.execute("UPDATE managed_certificates SET active_job_id=?", (JOB,))
            db.execute("INSERT INTO certificate_jobs(id,certificate_id,parameters,status) "
                       "VALUES (?,?,?,?)", (JOB, PROFILE, "{}", "queued"))
        elif kind == "target":
            db.execute("INSERT INTO certificate_targets VALUES (?,NULL)", (PROFILE,))
    report = empty.check(agent_public_key=None)
    assert report.database_modules_present == frozenset({"certificates"})
    assert report.database_dependencies == frozenset()
    assert report.coverage.certificates == "unknown"
    assert dict(report.ciphertext_counts)["certificates"] == 0
    assert empty.sources == {}


def test_default_notification_row_without_key_fingerprint_is_not_key_dependency(empty):
    empty.change("INSERT INTO notification_settings(id,enabled) VALUES (1,0)")
    report = empty.check(agent_public_key=None)
    assert report.database_modules_present == frozenset({"notifications"})
    assert report.database_dependencies == frozenset()
    assert report.coverage.notifications == "unknown"


def test_subscriber_without_totp_ciphertext_is_not_external_configuration_dependency(empty):
    with empty.write() as db:
        db.execute("INSERT INTO product_users(username) VALUES ('plain-user')")
        db.execute("INSERT INTO subscriber_accounts(username) VALUES ('plain-user')")
    report = empty.check(agent_public_key=None, totp_key=None)
    assert report.database_modules_present == frozenset({"totp"})
    assert report.database_dependencies == frozenset()
    assert report.required_configuration == ("deployment_settings",)
    assert report.totp_status == "not_configured"


def test_unused_but_initialized_keys_are_included_without_inventing_database_dependency(empty):
    for prefix in (CERT, EXTERNAL, FEDERATION):
        empty.stream(prefix + "vault.key", Fernet.generate_key())
        empty.stream(prefix + "vault.initialized", MARKER)
    report = empty.check(agent_public_key=None)
    assert report.coverage.certificates == "included"
    assert report.coverage.external_subscriptions == "included"
    assert report.coverage.federation == "included"
    assert report.database_dependencies == frozenset()
    expected = {
        "certificate_vault_key_and_marker", "external_vault_key_and_marker",
        "federation_vault_key_and_marker",
    }
    assert expected <= set(report.checked)


def test_acme_only_state_without_v1_required_key_pair_is_explicitly_unsupported(empty):
    path = CERT + PROFILE + "/accounts/ca.example.invalid/a/keys/a.key"
    empty.stream(path, pem(ec.generate_private_key(ec.SECP256R1())))
    fails(empty, agent_public_key=None)
    assert list(empty.sources) == [path]


def test_totp_key_absence_is_not_checked_not_corruption(fixture):
    report = fixture.check(totp_key=None)
    assert report.checked_ciphertexts == 9
    assert dict(report.ciphertext_counts)["totp"] == 5
    assert report.totp_status == "not_checked"
    assert "subscriber_totp_key" in report.required_configuration
    assert "totp_fernet_and_username_binding" not in report.checked
    assert report.restoration_ready is False


def test_seed_without_running_public_key_does_not_claim_runtime_match(fixture):
    report = fixture.check(agent_public_key=None)
    assert report.coverage.agent_identity == "included"
    assert report.agent_identity_matches_runtime is None
    assert "agent_seed_to_supplied_runtime_public_key" not in report.checked


@pytest.mark.parametrize("missing", [CERT + "vault.key", CERT + "vault.initialized",
                                    EXTERNAL + "vault.key", EXTERNAL + "vault.initialized",
                                    FEDERATION + "vault.key", FEDERATION + "vault.initialized",
                                    NOTIFICATIONS + "telegram.key",
                                    NOTIFICATIONS + "telegram.initialized",
                                    IDENTITY])
def test_missing_required_staged_file_fails_without_recreation(fixture, missing):
    removed = fixture.sources.pop(missing)
    before = set(fixture.sources)
    fails(fixture)
    assert set(fixture.sources) == before
    removed.close()


@pytest.mark.parametrize("path", [CERT + "vault.key", EXTERNAL + "vault.key",
                                 FEDERATION + "vault.key",
                                 NOTIFICATIONS + "telegram.key"])
def test_wrong_vault_key_rejects_all_roles(fixture, path):
    fixture.stream(path, Fernet.generate_key())
    fails(fixture)


@pytest.mark.parametrize("path", [CERT + "vault.initialized", EXTERNAL + "vault.initialized",
                                 FEDERATION + "vault.initialized",
                                 NOTIFICATIONS + "telegram.initialized"])
@pytest.mark.parametrize("content", [b"", b"bad marker", b"Open Node certificate vault\r\n"])
def test_marker_corruption_is_not_repaired(fixture, path, content):
    fixture.stream(path, content)
    fails(fixture)
    require(fixture.sources[path].getvalue() == content)


@pytest.mark.parametrize("key", [b"", b"bad", b"x" * 129, "not-bytes", 1, True, bytearray(44)])
def test_invalid_optional_totp_key_never_echoes_input(fixture, key):
    fails(fixture, totp_key=key)


def test_wrong_totp_key_is_rejected(fixture):
    fails(fixture, totp_key=Fernet.generate_key())


@pytest.mark.parametrize("table,column", [("certificate_dns_providers", "credentials"),
                                        ("managed_certificates", "eab"),
                                        ("certificate_versions", "encrypted_material"),
                                        ("external_subscription_sources", "secret"),
                                        ("external_subscription_nodes", "secret"),
                                        ("external_subscription_previews", "secret"),
                                        ("federated_servers", "token_secret"),
                                        ("notification_settings", "token_ciphertext"),
                                        ("administrator_factors", "totp_secret"),
                                        ("administrator_factors", "pending_secret"),
                                        ("operator_challenges", "pending_secret"),
                                        ("subscriber_accounts", "totp_secret"),
                                        ("subscriber_accounts", "pending_secret")])
def test_every_disabled_or_expired_ciphertext_is_validated(fixture, table, column):
    fixture.change(f'UPDATE "{table}" SET "{column}"=?', (SENTINEL,))
    fails(fixture)


def test_expired_certificate_job_eab_is_validated(fixture):
    fixture.change("UPDATE certificate_jobs SET parameters=?", (json.dumps({"eab": SENTINEL}),))
    fails(fixture)


@pytest.mark.parametrize("table,column,owner", [
    ("administrator_factors", "totp_secret", "用户甲"),
    ("administrator_factors", "pending_secret", "用户甲"),
    ("operator_challenges", "pending_secret", "用户甲"),
    ("subscriber_accounts", "totp_secret", "管理员"),
    ("subscriber_accounts", "pending_secret", "管理员"),
])
def test_totp_bound_username_cannot_move_between_owners(fixture, table, column, owner):
    fixture.change(f'UPDATE "{table}" SET "{column}"=?', (otp(fixture.keys["totp"], owner),))
    fails(fixture)


def test_totp_same_owner_active_pending_binding_is_not_claimed(fixture):
    value = otp(fixture.keys["totp"], "用户甲", "KRSXG5DSNFXGOIDB")
    fixture.change("UPDATE subscriber_accounts SET pending_secret=?", (value,))
    fixture.change("UPDATE subscriber_accounts SET totp_secret=pending_secret")
    report = fixture.check()
    assert report.totp_status == "verified"
    assert "totp_active_or_pending_purpose_binding" in report.not_checked


def test_certificate_same_vault_row_identity_is_not_claimed(fixture):
    value = seal(fixture.keys["certificate"], {"CF_DNS_API_TOKEN": "another-synthetic-token"})
    fixture.change("UPDATE certificate_dns_providers SET credentials=?", (value,))
    report = fixture.check()
    assert "certificate_ciphertext_row_or_purpose_binding" in report.not_checked


@pytest.mark.parametrize("secret", ["", "bad!", "valid\nsecond-line", "含中文", "====", "A"])
def test_totp_plaintext_secret_structure_is_checked(fixture, secret):
    fixture.change("UPDATE subscriber_accounts SET pending_secret=?",
                   (otp(fixture.keys["totp"], "用户甲", secret),))
    fails(fixture)


@pytest.mark.parametrize("field,value", [("version", True), ("version", 2), ("owner", "用户乙"),
                                       ("source", str(UUID(int=40))), ("purpose", "node:" + NODE),
                                       ("extra", "unexpected")])
def test_external_authenticated_envelope_binding(fixture, field, value):
    body = {"version": 1, "owner": "用户甲", "source": SOURCE, "purpose": "source",
            "value": {"url": "https://example.invalid/" + SENTINEL, "user_agent": "test/1"}}
    body[field] = value
    fixture.change("UPDATE external_subscription_sources SET secret=?",
                   (seal(fixture.keys["external"], body),))
    fails(fixture)


@pytest.mark.parametrize("table,purpose", [("external_subscription_nodes", "node:" + PREVIEW),
                                         ("external_subscription_previews", "preview:" + NODE),
                                         ("external_subscription_nodes", "preview:" + NODE),
                                         ("external_subscription_previews", "node:" + PREVIEW)])
def test_external_node_preview_id_and_purpose_are_separate(fixture, table, purpose):
    fixture.change(f'UPDATE "{table}" SET secret=?',
                   (external(fixture.keys["external"], purpose=purpose, value={}),))
    fails(fixture)


def test_external_url_digest_uses_exact_staged_key(fixture):
    fixture.change("UPDATE external_subscription_sources SET url_digest=?", ("0" * 64,))
    fails(fixture)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("server", SOURCE),
    ("owner_url", "https://other.example.invalid"), ("purpose", "other"),
    ("token", "short"), ("extra", "unexpected"),
])
def test_federation_authenticated_envelope_binding(fixture, field, value):
    body = {
        "version": 1, "server": FEDERATED,
        "owner_url": "https://owner.example.invalid",
        "purpose": "federation-token", "token": "A" * 43,
    }
    body[field] = value
    fixture.change(
        "UPDATE federated_servers SET token_secret=?",
        (seal(fixture.keys["federation"], body),),
    )
    fails(fixture)


def test_nullable_applied_preview_and_unavailable_node_are_not_false_ciphertexts(fixture):
    fixture.change("UPDATE external_subscription_previews SET secret=NULL")
    fixture.change("UPDATE external_subscription_nodes SET secret=NULL")
    report = fixture.check()
    assert dict(report.ciphertext_counts)["external_subscriptions"] == 1
    assert "external_subscriptions" in report.database_dependencies


@pytest.mark.parametrize("target", ["database", "marker", "purpose", "token", "extra"])
def test_notification_database_marker_and_purpose_binding(fixture, target):
    if target == "database":
        fixture.change("UPDATE notification_settings SET key_fingerprint=?", ("f" * 64,))
    elif target == "marker":
        fixture.stream(NOTIFICATIONS + "telegram.initialized",
                       canonical({"purpose": "wrong", "key_fingerprint": "f" * 64}))
    else:
        value = {"purpose": PURPOSE, "token": TOKEN}
        value[target] = "bad"
        fixture.change("UPDATE notification_settings SET token_ciphertext=?",
                       (seal(fixture.keys["notification"], value),))
    fails(fixture)


def test_cleared_notification_retains_key_dependency(fixture):
    fixture.change("UPDATE notification_settings SET token_ciphertext=NULL,enabled=0")
    report = fixture.check()
    assert "notifications" in report.database_dependencies
    assert dict(report.ciphertext_counts)["notifications"] == 0
    fixture.sources.pop(NOTIFICATIONS + "telegram.key").close()
    fails(fixture)


def test_notification_ciphertext_without_stored_fingerprint_is_rejected(fixture):
    fixture.change("UPDATE notification_settings SET key_fingerprint=NULL")
    fails(fixture)


def test_all_notification_rows_are_inspected_not_only_singleton_one(fixture):
    fixture.change("INSERT INTO notification_settings(id,token_ciphertext,key_fingerprint) "
                   "VALUES (2,NULL,NULL)")
    fails(fixture)


@pytest.mark.parametrize("kind", ["expired", "future", "legacy_without_fingerprint"])
def test_certificate_lifetime_is_not_a_backup_key_corruption_test(fixture, kind):
    material = certificate_material(expired=kind == "expired", future=kind == "future")
    with fixture.write():
        fixture.set_version(material, fingerprint=kind != "legacy_without_fingerprint")
    report = fixture.check()
    assert "certificate_pem_key_pairs_and_available_fingerprints" in report.checked


@pytest.mark.parametrize("field,value", [("key_pem", "garbage"), ("cert_pem", "garbage"),
                                       ("domains", ["wrong.example.invalid"]), ("serial", "wrong"),
                                       ("issuer", "wrong"), ("not_before", 0),
                                       ("expires_at", True), ("extra", "bad")])
def test_certificate_material_metadata_and_keys_are_checked(fixture, field, value):
    material = dict(fixture.material)
    material[field] = value
    fixture.change("UPDATE certificate_versions SET encrypted_material=?",
                   (seal(fixture.keys["certificate"], material),))
    fails(fixture)


def test_certificate_private_key_mismatch(fixture):
    material = dict(fixture.material)
    material["key_pem"] = pem(ec.generate_private_key(ec.SECP256R1())).decode()
    fixture.change("UPDATE certificate_versions SET encrypted_material=?",
                   (seal(fixture.keys["certificate"], material),))
    fails(fixture)


@pytest.mark.parametrize("field,value", [("details", "{}"), ("fingerprint", "0" * 64)])
def test_certificate_public_metadata_agrees_with_ciphertext(fixture, field, value):
    fixture.change(f'UPDATE certificate_versions SET "{field}"=?', (value,))
    fails(fixture)


@pytest.mark.parametrize("sql", [
    "UPDATE managed_certificates SET provider_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE managed_certificates SET version_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE managed_certificates SET active_job_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE certificate_versions SET certificate_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE certificate_jobs SET certificate_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE certificate_targets SET certificate_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE certificate_targets SET version_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE external_subscription_sources SET owner_username='unknown-owner'",
    "UPDATE external_subscription_nodes SET source_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE external_subscription_previews SET source_id='00000000-0000-0000-0000-000000000099'",
    "UPDATE administrator_factors SET administrator_id=99",
    "UPDATE operator_challenges SET administrator_id=99",
    "UPDATE subscriber_accounts SET username='unknown-owner'",
])
def test_required_key_owner_and_certificate_references(fixture, sql):
    fixture.change(sql)
    fails(fixture)


def test_certificate_revoke_job_version_and_fingerprint_binding(fixture):
    fixture.change("UPDATE certificate_jobs SET parameters=?",
                   (json.dumps({"version_id": VERSION, "fingerprint": "0" * 64}),))
    fails(fixture)


@pytest.mark.parametrize("value", [b"", bytes(31), bytes(33), "text", bytearray(32), True])
def test_agent_public_metadata_shape(fixture, value):
    fails(fixture, agent_public_key=value)


@pytest.mark.parametrize("seed", [b"", bytes(31), bytes(33), b"x" * 32])
def test_agent_seed_length_and_runtime_public_match(fixture, seed):
    fixture.stream(IDENTITY, seed)
    fails(fixture)


def test_another_valid_ed25519_public_key_is_not_accepted(fixture):
    public = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    fails(fixture, agent_public_key=public)


def acme_files(fixture):
    key = ec.generate_private_key(ec.SECP256R1())
    account = CERT + PROFILE + "/accounts/ca.example.invalid/operator@example.invalid/"
    fixture.stream(account + "keys/operator@example.invalid.key", pem(key))
    fixture.stream(account + "account.json",
                   canonical({"registration": {"uri": "https://ca.example.invalid/acct/1"}}))
    directory = CERT + PROFILE + "/jobs/" + JOB + "/"
    csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([]))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName("backup.example.invalid")]),
                          critical=False)
           .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    fixture.stream(directory + "certificate.key", pem(key))
    fixture.stream(directory + "request.csr", csr)
    fixture.stream(directory + "order.json", canonical({
        "job_id": JOB, "csr_digest": hashlib.sha256(csr).hexdigest(),
        "uri": "https://ca.example.invalid/order/1",
    }))
    fixture.stream(directory + "result.json", canonical({
        "job_id": JOB, "status": "succeeded", "material": fixture.material,
    }))
    fixture.stream(CERT + PROFILE + "/certificates/backup.example.invalid.crt",
                   fixture.material["cert_pem"].encode())
    fixture.stream(CERT + PROFILE + "/certificates/backup.example.invalid.key",
                   fixture.material["key_pem"].encode())
    return account, directory


def test_acme_local_accounts_certificates_and_order_key_dependencies(fixture):
    acme_files(fixture)
    report = fixture.check()
    expected = {"acme_local_account_private_keys", "acme_local_account_metadata_key_presence",
                "acme_local_certificate_key_pairs", "acme_order_private_keys",
                "acme_order_csr_key_pairs", "acme_order_job_and_csr_digest_binding",
                "acme_job_material_key_pairs"}
    assert expected <= set(report.checked)
    assert "acme_registration_and_remote_state" in report.not_checked


@pytest.mark.parametrize("kind", ["account_key", "order_key", "csr", "csr_digest", "job_id",
                                 "certificate_key", "result_material", "account_metadata"])
def test_acme_missing_wrong_or_corrupt_dependencies(fixture, kind):
    account, directory = acme_files(fixture)
    if kind == "account_key":
        fixture.sources.pop(account + "keys/operator@example.invalid.key").close()
    elif kind == "order_key":
        fixture.stream(directory + "certificate.key", pem(ec.generate_private_key(ec.SECP256R1())))
    elif kind == "csr":
        fixture.stream(directory + "request.csr", b"bad CSR")
    elif kind in {"csr_digest", "job_id"}:
        value = json.loads(fixture.sources[directory + "order.json"].getvalue())
        value[kind] = "0" * 64 if kind == "csr_digest" else str(UUID(int=99))
        fixture.stream(directory + "order.json", canonical(value))
    elif kind == "certificate_key":
        fixture.sources.pop(CERT + PROFILE + "/certificates/backup.example.invalid.key").close()
    elif kind == "result_material":
        value = {"job_id": JOB, "material": dict(fixture.material, key_pem="bad")}
        fixture.stream(directory + "result.json", canonical(value))
    else:
        fixture.stream(account + "account.json", b"[]")
    fails(fixture)


def test_retained_deleted_profile_and_unconfirmed_account_key_are_not_lost(fixture):
    identifier = str(uuid4())
    key_path = (CERT + identifier
                + "/accounts/ca.example.invalid/old@example.invalid/keys/old@example.invalid.key")
    fixture.stream(key_path, pem(ec.generate_private_key(ec.SECP256R1())))
    fixture.stream(CERT + identifier + "/历史说明.txt", "历史数据，不执行".encode())
    report = fixture.check()
    assert "acme_local_account_private_keys" in report.checked
    assert "unrecognized_certificate_state_semantics" in report.not_checked


@pytest.mark.parametrize("key", [rsa.generate_private_key(public_exponent=65537, key_size=2048),
                                 ec.generate_private_key(ec.SECP384R1())])
def test_supported_acme_signing_key_types(fixture, key):
    path = (CERT + PROFILE
            + "/accounts/ca.example.invalid/a@example.invalid/keys/a@example.invalid.key")
    fixture.stream(path, pem(key))
    assert "acme_local_account_private_keys" in fixture.check().checked


@pytest.mark.parametrize("raw", [b'{"eab":null,"eab":null}', b'{"eab":NaN}', b'{"value":Infinity}',
                                 b'{"eab":1}', b'[]', b'{"eab":true}', b'{"eab":""}',
                                 b'{"a":1e9999}', b'{"a":"\xff"}', b'{"a":"\\ud800"}',
                                 b'{"a":' + b"9" * 33 + b'}',
                                 b'{"a":' + b'[' * 25 + b'0' + b']' * 25 + b'}'])
def test_nested_certificate_job_json_is_bounded_and_unambiguous(fixture, raw):
    fixture.change("UPDATE certificate_jobs SET parameters=?", (raw.decode("latin1"),))
    # The latin1 case is valid Unicode JSON, so force invalid bytes separately.
    if b"\xff" in raw:
        fixture.change("UPDATE certificate_jobs SET parameters=?", (raw,))
    fails(fixture)


@pytest.mark.parametrize("raw", [b'{"kid":"one","kid":"two","hmac":"x"}', b'[]',
                                 b'{"kid":true,"hmac":"x"}', b'{"kid":"x","hmac":"y","extra":1}'])
def test_authenticated_eab_payload_still_requires_correct_shape(fixture, raw):
    fixture.change("UPDATE managed_certificates SET eab=?",
                   (seal(fixture.keys["certificate"], raw, raw=True),))
    fails(fixture)


@pytest.mark.parametrize("raw", [b'{"version":1,"version":1}', b'[]', b'{"a":NaN}',
                                 b'{"a":"\\ud800"}'])
def test_authenticated_external_json_is_unambiguous(fixture, raw):
    fixture.change("UPDATE external_subscription_nodes SET secret=?",
                   (seal(fixture.keys["external"], raw, raw=True),))
    fails(fixture)


def test_json_brackets_and_quote_escapes_in_real_secret_are_not_depth(fixture):
    value = {"CF_DNS_API_TOKEN": '[[' * 100 + '\\"' + ']]' * 100}
    fixture.change("UPDATE certificate_dns_providers SET credentials=?",
                   (seal(fixture.keys["certificate"], value),))
    assert fixture.check().checked_ciphertexts == 14


class ShortStream(io.BytesIO):
    def __init__(self, value, maximum):
        super().__init__(value)
        self.maximum = maximum
        self.read_sizes = []

    def fileno(self):
        raise AssertionError("No fileno is allowed for snapshot slices")

    def read(self, size=-1):
        require(type(size) is int and 0 < size <= 65536)
        self.read_sizes.append(size)
        return super().read(min(size, self.maximum))


@pytest.mark.parametrize("maximum", [1, 7, 31, 65536])
def test_short_reads_and_nonzero_offsets_are_preserved_without_fileno(fixture, maximum):
    fixture.sources = {path: ShortStream(stream.getvalue(), maximum)
                       for path, stream in fixture.sources.items()}
    for stream in fixture.sources.values():
        stream.seek(5)
    assert fixture.check().checked_ciphertexts == 14
    assert all(stream.tell() == 5 and not stream.closed for stream in fixture.sources.values())


def test_stream_positions_restored_on_rejection(fixture):
    value = ShortStream(b"bad marker", 1)
    value.seek(4)
    fixture.sources[CERT + "vault.initialized"] = value
    fails(fixture)
    assert value.tell() == 4 and not value.closed


@pytest.mark.parametrize("path", ["/etc/secret", "../secret", "data/certificates/../vault.key",
                                 "data/certificates/a\\b", "data/external-subscriptions/extra",
                                 "data/notifications/other", "data/open-node.db",
                                 "secrets/agent-identity.seed/extra", "data/certificates/a\nfile",
                                 "data/certificates/e\u0301.txt"])
def test_only_fixed_logical_state_paths_are_accepted(fixture, path):
    fixture.stream(path, b"unread-synthetic-data")
    fails(fixture)


def test_literal_certificate_path_prefix_conflicts_are_rejected(fixture):
    fixture.stream(CERT + "parent", b"a")
    fixture.stream(CERT + "parent/child", b"b")
    fails(fixture)


def test_immutable_mapping_supported(fixture):
    fixture.sources = MappingProxyType(fixture.sources)
    assert fixture.check().checked_ciphertexts == 14


@pytest.mark.parametrize("fault", ["overread", "short", "none", "text", "grow", "seek", "tell"])
def test_inconsistent_staged_stream_never_yields_success(fixture, fault):
    class Fault(io.BytesIO):
        def read(self, size=-1):
            if fault == "overread":
                return b"x" * (size + 1)
            if fault == "short":
                return b""
            if fault == "none":
                return None
            if fault == "text":
                return "bad"
            if fault == "grow" and super().tell() == 0:
                position = super().tell()
                super().seek(0, io.SEEK_END)
                super().write(b"x")
                super().seek(position)
            return super().read(size)

        def seek(self, offset, whence=0):
            result = super().seek(offset, whence)
            return -1 if fault == "seek" else result

        def tell(self):
            return "not-an-offset" if fault == "tell" else super().tell()

    fixture.sources[CERT + "vault.key"] = Fault(fixture.keys["certificate"])
    fails(fixture)


@pytest.mark.parametrize("fault", ["query_only", "transaction", "attached",
                                  "text_factory", "closed"])
def test_connection_ownership_preconditions(fixture, fault):
    if fault == "query_only":
        fixture.connection.execute("PRAGMA query_only=OFF")
    elif fault == "transaction":
        fixture.connection.execute("BEGIN")
    elif fault == "attached":
        fixture.connection.execute("ATTACH ':memory:' AS other")
    elif fault == "text_factory":
        fixture.connection.text_factory = bytes
    else:
        fixture.connection.close()
    fails(fixture)
    if fault == "transaction":
        assert fixture.connection.in_transaction
        fixture.connection.rollback()
    elif fault == "query_only":
        assert fixture.connection.execute("PRAGMA query_only").fetchone() == (0,)


def test_checker_never_invokes_custom_row_factory(fixture):
    def factory(_cursor, _row):
        raise AssertionError("Caller row factory must not run")
    fixture.connection.row_factory = factory
    assert fixture.check().checked_ciphertexts == 14
    assert fixture.connection.row_factory is factory
    fixture.connection.row_factory = None


def test_caller_opened_completed_connection_remains_usable_after_unlink(fixture):
    # Only this fixture's synthetic DB is unlinked. This is not a /proc/fd reopen
    # or a claim that this test called backup_sqlite's independently tested API.
    fixture.path.unlink()
    assert not fixture.path.exists()
    report = fixture.check()
    assert report.checked_ciphertexts == 14
    assert fixture.connection.execute("SELECT 1").fetchone() == (1,)
    assert not fixture.connection.in_transaction


def test_completed_sqlite_integrity_check_empty_builtin_temp_schema_is_allowed(fixture):
    assert fixture.connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert fixture.connection.execute("SELECT 1 FROM temp.sqlite_schema").fetchall() == []
    names = fixture.connection.execute("PRAGMA database_list").fetchall()
    assert [(row[0], row[1]) for row in names] == [(0, "main"), (1, "temp")]
    assert names[1][2] == ""
    assert fixture.check().checked_ciphertexts == 14
    assert fixture.connection.execute("PRAGMA database_list").fetchall() == names


@pytest.mark.parametrize("kind", ["table", "view"])
def test_nonempty_builtin_temp_schema_is_not_confused_with_integrity_artifact(fixture, kind):
    fixture.connection.execute("PRAGMA query_only=OFF")
    if kind == "table":
        fixture.connection.execute("CREATE TEMP TABLE dependency_test(value TEXT)")
    else:
        fixture.connection.execute("CREATE TEMP VIEW dependency_test AS SELECT 'unused'")
    fixture.connection.commit()
    fixture.connection.execute("PRAGMA query_only=ON")
    fails(fixture)


@pytest.mark.parametrize("change", ["DROP TABLE certificate_jobs",
                                    "ALTER TABLE certificate_jobs DROP COLUMN parameters",
                                    "DROP TABLE subscriber_accounts",
                                    "ALTER TABLE notification_settings "
                                    "DROP COLUMN key_fingerprint"])
def test_missing_supported_dependency_schema_is_explicitly_unavailable(fixture, change):
    fixture.change(change)
    fails(fixture)


def test_malicious_view_is_rejected_before_function_evaluation(fixture):
    with fixture.write() as db:
        db.execute("DROP TABLE certificate_jobs")
        db.execute("CREATE VIEW certificate_jobs AS SELECT attack() AS id, "
                   "'' AS certificate_id, '{}' AS parameters")
    calls = []
    fixture.connection.create_function("attack", 0, lambda: calls.append(True))
    fails(fixture)
    assert calls == []


def test_generated_ciphertext_column_is_rejected_before_function_evaluation(fixture):
    with fixture.write() as db:
        db.create_function("attack", 1, lambda value: value, deterministic=True)
        db.execute("DROP TABLE certificate_jobs")
        db.execute("CREATE TABLE certificate_jobs (id TEXT,certificate_id TEXT,raw TEXT, "
                   "parameters TEXT GENERATED ALWAYS AS (attack(raw)) VIRTUAL)")
    calls = []
    fixture.connection.create_function("attack", 1, lambda value: calls.append(True),
                                       deterministic=True)
    fails(fixture)
    assert calls == []


def test_sql_trace_is_read_only_no_begin_commit_or_schema_writes(fixture):
    statements = []
    fixture.connection.set_trace_callback(statements.append)
    before_changes = fixture.connection.total_changes
    fixture.check()
    fixture.connection.set_trace_callback(None)
    assert statements
    assert all(value.lstrip().upper().startswith(("SELECT ", "PRAGMA ")) for value in statements)
    assert not any("=" in value for value in statements if value.upper().startswith("PRAGMA"))
    assert fixture.connection.total_changes == before_changes
    assert len(statements) <= dependencies.MAX_DEPENDENCY_QUERIES


def test_failure_does_not_leave_progress_handler_active(fixture, monkeypatch):
    original = dependencies._Budget.progress
    called = []

    def progress(self):
        called.append(True)
        return original(self)

    monkeypatch.setattr(dependencies._Budget, "progress", progress)
    fixture.change("UPDATE certificate_jobs SET parameters='bad'")
    fails(fixture)
    called.clear()
    fixture.connection.execute("WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL "
                               "SELECT n+1 FROM t WHERE n<5000) SELECT sum(n) FROM t").fetchone()
    assert called == []


def test_keyboard_interrupt_cleans_progress_and_preserves_stream(fixture):
    class Interrupted(io.BytesIO):
        def read(self, size=-1):
            raise KeyboardInterrupt()
    value = Interrupted(fixture.keys["certificate"])
    value.seek(3)
    fixture.sources[CERT + "vault.key"] = value
    with pytest.raises(KeyboardInterrupt):
        fixture.check()
    assert value.tell() == 3 and not value.closed
    assert fixture.connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize("field", ["MAX_DEPENDENCY_SECONDS", "MAX_DEPENDENCY_QUERIES",
                                  "MAX_DEPENDENCY_ROWS", "MAX_TOTAL_METADATA_BYTES",
                                  "MAX_TOTAL_CIPHERTEXT_BYTES", "MAX_TOTAL_PLAINTEXT_BYTES",
                                  "MAX_TOTAL_STATE_BYTES", "MAX_DEPENDENCY_IO_OPERATIONS"])
def test_controlled_budget_rejections_are_safe_and_readonly(fixture, monkeypatch, field):
    before = snapshot(fixture.path)
    monkeypatch.setattr(dependencies, field, 0)
    fails(fixture)
    require(snapshot(fixture.path) == before)


def test_sqlite_progress_handler_has_real_vm_instruction_bound(fixture, monkeypatch):
    monkeypatch.setattr(dependencies, "MAX_DEPENDENCY_SQL_STEPS", 0)
    with fixture.write() as db:
        db.executemany("INSERT INTO product_users(username) VALUES (?)",
                       (("extra-" + str(i),) for i in range(1000)))
    fails(fixture)


def test_actual_ciphertext_field_ceiling_is_checked_before_decrypt(fixture, monkeypatch):
    value = "X" * (dependencies.MAX_CIPHERTEXT_BYTES + 1)
    fixture.change("UPDATE certificate_dns_providers SET credentials=?", (value,))
    calls = []
    monkeypatch.setattr(Fernet, "decrypt", lambda *_args, **_kwargs: calls.append(True))
    fails(fixture)
    assert calls == []


def test_actual_row_ceiling_rejects_one_extra_row(empty):
    with empty.write() as db:
        db.executemany("INSERT INTO product_users(username) VALUES (?)",
                       ((f"user-{index}",)
                        for index in range(dependencies.MAX_DEPENDENCY_ROWS + 1)))
    fails(empty, agent_public_key=None)


def test_large_ordinary_database_below_actual_row_ceiling_is_supported(empty):
    with empty.write() as db:
        db.executemany("INSERT INTO product_users(username) VALUES (?)",
                       ((f"user-{index}",) for index in range(99_000)))
    assert empty.check(agent_public_key=None).checked_ciphertexts == 0


def test_actual_aggregate_64mib_ciphertext_boundary_is_not_silent_truncation(fixture):
    key = fixture.keys["external"]
    large_value = {"url": "https://example.invalid/synthetic-large",
                   "user_agent": "X" * (4 * 1024 * 1024)}
    with fixture.write() as db:
        for index in range(20, 33):
            identifier = str(UUID(int=index))
            db.execute("INSERT INTO external_subscription_sources"
                       "(id,owner_username,secret,url_digest) VALUES (?,?,?,?)",
                       (identifier, "用户甲", external(key, source=identifier, value=large_value),
                        url_digest(key, large_value["url"])))
    fails(fixture)


def test_actual_state_file_count_boundary(fixture):
    while len(fixture.sources) < 4096:
        fixture.stream(CERT + "opaque-" + str(len(fixture.sources)), b"")
    fails(fixture)


def test_actual_bounded_stream_io_count(fixture):
    path = CERT + PROFILE + "/accounts/ca.example.invalid/a/keys/a.key"
    value = ShortStream(b"X" * (dependencies.MAX_DEPENDENCY_IO_OPERATIONS + 1), 1)
    fixture.sources[path] = value
    fails(fixture)
    assert len(value.read_sizes) <= dependencies.MAX_DEPENDENCY_IO_OPERATIONS
    assert value.tell() == 0


def test_completed_schema_checked_without_app_import_or_configuration(empty, tmp_path):
    script = r'''
import importlib.abc, io, json, sqlite3, sys
blocked = ("sqlalchemy", "open_node.main", "open_node.core.config", "open_node.services.inventory",
           "open_node.services.auth", "open_node.services.subscriber_auth",
           "open_node.services.certificates", "open_node.services.certificate_vault",
           "open_node.services.notifications", "open_node.services.secure_channel")
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Unexpected application import")
sys.meta_path.insert(0, Guard())
from open_node.services.backup_dependencies import check_backup_dependencies
connection = sqlite3.connect(sys.argv[1], uri=True)
connection.execute("PRAGMA query_only=ON")
report = check_backup_dependencies(connection, {})
assert report.restoration_ready is False and report.checked_ciphertexts == 0
assert not any(name in sys.modules for name in blocked)
connection.close()
print(json.dumps({"checked": True, "restoration_ready": False, "blocked_imports": 0}))
'''
    poison = tmp_path / "not-allowed-app-data"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(APP), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1",
           "OPEN_NODE_DATABASE_URL": "sqlite:///" + str(poison / "db.sqlite3"),
           "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
           "OPEN_NODE_CERTIFICATE_STATE_DIR": str(poison / "certificates"),
           "OPEN_NODE_SUBSCRIBER_TOTP_KEY": "invalid-unused-environment-key", "HOME": str(tmp_path),
           "LANG": "C.UTF-8"}
    args = [sys.executable, "-B", "-c", script, empty.path.as_uri() + "?mode=ro&immutable=1"]
    result = subprocess.run(args, capture_output=True, env=env, cwd=tmp_path,
                            timeout=30, check=False)
    require(result.returncode == 0 and result.stderr == b"")
    assert json.loads(result.stdout) == {
        "checked": True, "restoration_ready": False, "blocked_imports": 0,
    }
    assert not poison.exists()


def test_current_application_model_schema_matches_checker_without_store_construction(tmp_path):
    # Schema setup is explicitly fixture-only. The checker is separately tested
    # in a subprocess that forbids every ORM/Store/Vault import.
    from open_node.services.auth import AuthBase
    from open_node.services.branding import BrandingBase
    from open_node.services.certificates import CertificateBase
    from open_node.services.external_subscriptions import ExternalSourceModel
    from open_node.services.inventory import Base
    from open_node.services.notifications import NotificationBase
    from open_node.services.server_sharing import FederatedServerModel
    from open_node.services.subscriber_auth import SubscriberAccount
    from open_node.services.subscription_templates import TemplateRecord
    from sqlalchemy import create_engine

    path = tmp_path / "models.sqlite3"
    engine = create_engine("sqlite:///" + str(path))
    try:
        assert all(model.metadata is Base.metadata for model in (
            ExternalSourceModel, FederatedServerModel, SubscriberAccount, TemplateRecord,
        ))
        for metadata in (Base.metadata, AuthBase.metadata, CertificateBase.metadata,
                         NotificationBase.metadata, BrandingBase.metadata):
            metadata.create_all(engine)
    finally:
        engine.dispose()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    try:
        assert dependencies.check_backup_dependencies(connection, {}).checked_ciphertexts == 0
    finally:
        connection.close()
