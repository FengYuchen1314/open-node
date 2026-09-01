"""Actual SQLite/Fernet/official-age pipeline; isolated synthetic instance only."""

import asyncio
import hashlib
import io
import os
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from open_node.domain.backup import BackupSource
from open_node.services import backup_creation as module
from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_creation import BackupCreationError, create_control_plane_backup
from open_node.services.backup_runtime import backup_operation
from open_node.services.backup_state import BackupStateLayout
from open_node.services.backup_validation import validate_backup_archive
from test_backup_dependencies import Fixture
from test_backup_encryption import official_age as official_age
from test_backup_encryption import real_age_keys as real_age_keys

SAFE_ERROR = "Control-plane backup creation is unavailable."
AGE_INTRO = b"age-encryption.org/v1\n"


def make_instance(root, *, full=True):
    data = root / "instance"
    data.mkdir(mode=0o700)
    sample = Fixture(data, full=full)
    sample.connection.close()
    sample.path.chmod(0o600)
    staging = data / "staging"
    staging.mkdir(mode=0o700)
    certificates, external, federation, notifications = (data / name for name in (
        "certificates", "external", "federation", "notifications",
    ))
    identity = data / "agent" / "identity.seed" if full else None
    for logical, stream in sample.sources.items():
        if logical.startswith("data/certificates/"):
            destination = certificates / logical.removeprefix("data/certificates/")
        elif logical.startswith("data/external-subscriptions/"):
            destination = external / logical.removeprefix("data/external-subscriptions/")
        elif logical.startswith("data/federation/"):
            destination = federation / logical.removeprefix("data/federation/")
        elif logical.startswith("data/notifications/"):
            destination = notifications / logical.removeprefix("data/notifications/")
        else:
            assert logical == "secrets/agent-identity.seed"
            destination = identity
        # pathlib's parents=True uses default modes for intermediate parents.
        # Make every owned source component explicitly private for the copier.
        parents = []
        parent = destination.parent
        while parent != data:
            parents.append(parent)
            parent = parent.parent
        for parent in reversed(parents):
            parent.mkdir(mode=0o700, exist_ok=True)
        destination.write_bytes(stream.getvalue())
        destination.chmod(0o600)
    layout = BackupStateLayout(
        sample.path, certificates, external, notifications, identity, federation=federation,
    )
    return SimpleNamespace(
        sample=sample, layout=layout, staging=staging,
        barrier=BackupWriteBarrier(data / ".open-node-backup.lock"),
    )


@pytest.fixture
def instance(tmp_path):
    value = make_instance(tmp_path)
    try:
        yield value
    finally:
        value.barrier.close()


def create(instance, recipient, **overrides):
    options = {
        "barrier": instance.barrier, "recipient": recipient,
        "staging_directory": instance.staging,
        "totp_key": instance.sample.keys["totp"],
        "agent_public_key": instance.sample.public,
    }
    options.update(overrides)
    return create_control_plane_backup(instance.layout, **options)


def descriptors():
    # listdir's temporary descriptor is absent once the iteration starts.
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            result[int(name)] = os.readlink(f"/proc/self/fd/{name}")
        except FileNotFoundError:
            pass
    return result


def observe_snapshot(monkeypatch):
    original = module.capture_control_plane_snapshot
    copies = []

    @contextmanager
    def observed(*args, **kwargs):
        with original(*args, **kwargs) as result:
            copies.append(result)
            yield result

    monkeypatch.setattr(module, "capture_control_plane_snapshot", observed)
    return copies


def assert_plaintext_closed(copies):
    assert len(copies) == 1
    copy = copies[0]
    assert copy.database.stream.closed
    assert all(stream.closed for stream in copy.state.sources.values())
    with pytest.raises(sqlite3.ProgrammingError):
        copy.database.connection.execute("SELECT 1")


def independent_decrypt(created, keys, staging):
    # Invoke the actual pinned official tool directly, not the product's
    # decrypt helper or a test stub; neither keys nor payload appear in argv.
    with tempfile.TemporaryFile("w+b", buffering=0, dir=staging) as identity:
        identity.write(keys.identity)
        identity.seek(0)
        with tempfile.TemporaryFile("w+b", buffering=0, dir=staging) as output:
            completed = subprocess.run(
                [str(keys.binary), "--decrypt", "-i", f"/proc/self/fd/{identity.fileno()}"],
                stdin=created.stream, stdout=output, stderr=subprocess.DEVNULL,
                pass_fds=(identity.fileno(),), env={}, timeout=30, check=False,
            )
            assert completed.returncode == 0
            checked = validate_backup_archive(output)
            assert checked == created.encryption.archive_report
            with zipfile.ZipFile(output) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
    return checked, payloads


def test_native_pipeline_yields_only_ciphertext_after_all_plaintext_has_closed(
    instance, official_age, monkeypatch,
):
    copies = observe_snapshot(monkeypatch)
    before = descriptors()
    with create(instance, official_age.public) as created:
        assert_plaintext_closed(copies)
        assert list(instance.staging.iterdir()) == []
        assert created.stream.readable() and not created.stream.writable()
        info = os.fstat(created.stream.fileno())
        assert info.st_nlink == 0 and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_size == created.encryption.encrypted_size
        assert not os.get_inheritable(created.stream.fileno())
        assert created.encryption.authenticated_decryption is False
        assert created.restoration_ready is False
        assert created.snapshot_consistency == "cooperating_writers"
        assert created.dependencies.totp_status == "verified"
        assert created.dependencies.agent_identity_matches_runtime is True
        assert created.dependencies.remote_agent_trust == "not_checked"
        opened = set(descriptors()) - set(before)
        assert opened == {created.stream.fileno()}
        ciphertext = created.stream.read()
        assert hashlib.sha256(ciphertext).hexdigest() == created.encryption.encrypted_sha256
        assert not ciphertext.startswith(b"PK")
        created.stream.seek(0)
        report, payloads = independent_decrypt(created, official_age, instance.staging)
        assert set(payloads) == {"manifest.json", "data/open-node.db", *instance.sample.sources}
        for path, expected in instance.sample.sources.items():
            assert payloads[path] == expected.getvalue()
        assert report.manifest.source == BackupSource(None, None, None)
        assert set(asdict(report.manifest.coverage).values()) == {"included"}
        assert report.manifest.database.schema_fingerprint == copies[0].database.schema_fingerprint
        assert report.manifest.required_configuration == (
            "deployment_settings", "subscriber_totp_key",
        )
    assert created.stream.closed
    assert descriptors() == before
    assert list(instance.staging.iterdir()) == []


def test_empty_supported_instance_has_explicit_absence_not_unknown(tmp_path, official_age):
    instance = make_instance(tmp_path, full=False)
    try:
        with create(instance, official_age.public, totp_key=None, agent_public_key=None) as created:
            coverage = created.encryption.archive_report.manifest.coverage
            assert set(asdict(coverage).values()) == {"not_configured"}
            assert created.dependencies.checked_ciphertexts == 0
            assert created.encryption.archive_report.manifest.required_configuration == (
                "deployment_settings",
            )
            independent_decrypt(created, official_age, instance.staging)
        assert not instance.layout.certificates.exists()
        assert not instance.layout.external_subscriptions.exists()
        assert not instance.layout.federation.exists()
        assert not instance.layout.notifications.exists()
    finally:
        instance.barrier.close()


@pytest.mark.parametrize("missing", ["totp", "agent", "wrong_totp", "wrong_agent"])
def test_present_dependencies_must_be_verified_before_zip_or_encryption(
    instance, official_age, monkeypatch, missing,
):
    copies = observe_snapshot(monkeypatch)

    def unexpected(*args, **kwargs):
        pytest.fail("Dependency failure must precede ZIP generation")

    monkeypatch.setattr(module, "write_backup_archive", unexpected)
    overrides = {}
    if missing in {"totp", "wrong_totp"}:
        overrides["totp_key"] = None if missing == "totp" else instance.sample.keys["external"]
    else:
        overrides["agent_public_key"] = None if missing == "agent" else (
            ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        )
    with pytest.raises(BackupCreationError) as caught:
        with create(instance, official_age.public, **overrides):
            pytest.fail("Unverified dependency yielded")
    assert str(caught.value) == SAFE_ERROR
    assert_plaintext_closed(copies)
    assert list(instance.staging.iterdir()) == []


@pytest.mark.parametrize("recipient", [None, "", "ssh-ed25519 x", "a" * 63])
def test_recipient_shape_rejection_precedes_snapshot(instance, monkeypatch, recipient):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid recipient shape must not start a snapshot")

    monkeypatch.setattr(module, "capture_control_plane_snapshot", unexpected)
    with pytest.raises(BackupCreationError, match="^" + SAFE_ERROR.replace(".", r"\.") + "$"):
        with create(instance, recipient):
            pytest.fail("Invalid recipient yielded")
    assert list(instance.staging.iterdir()) == []


def test_actual_age_rejects_checksum_without_partial_artifact(instance, official_age):
    before = descriptors()
    with pytest.raises(BackupCreationError):
        with create(instance, "age1" + "q" * 58):
            pytest.fail("Invalid native checksum yielded")
    assert descriptors() == before
    assert list(instance.staging.iterdir()) == []


def test_space_preflight_cleans_completed_snapshot_before_any_zip(
    instance, official_age, monkeypatch,
):
    copies = observe_snapshot(monkeypatch)
    monkeypatch.setattr(module.os, "fstatvfs", lambda _fd: SimpleNamespace(f_bavail=1, f_frsize=1))

    def unexpected(*args, **kwargs):
        pytest.fail("Insufficient staging space must precede ZIP generation")

    monkeypatch.setattr(module, "write_backup_archive", unexpected)
    with pytest.raises(BackupCreationError):
        with create(instance, official_age.public):
            pytest.fail("Insufficient-space artifact yielded")
    assert_plaintext_closed(copies)
    assert list(instance.staging.iterdir()) == []


@pytest.mark.parametrize("phase", [
    "check_backup_dependencies", "write_backup_archive", "encrypted_backup_archive",
    "_retain_ciphertext",
])
@pytest.mark.parametrize("failure", [
    OSError("synthetic secret path must be hidden"), KeyboardInterrupt(), asyncio.CancelledError(),
])
def test_real_resource_cleanup_on_phase_failure(
    instance, official_age, monkeypatch, phase, failure,
):
    copies = observe_snapshot(monkeypatch)
    before = descriptors()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(module, phase, fail)
    expected = BackupCreationError if isinstance(failure, Exception) else type(failure)
    with pytest.raises(expected) as caught:
        with create(instance, official_age.public):
            pytest.fail("Failed producer yielded")
    if expected is BackupCreationError:
        assert str(caught.value) == SAFE_ERROR
    else:
        assert caught.value is failure
    assert_plaintext_closed(copies)
    assert descriptors() == before
    assert list(instance.staging.iterdir()) == []


@pytest.mark.parametrize("failure", [RuntimeError("consumer"), asyncio.CancelledError()])
def test_consumer_exceptions_are_not_rewritten_and_close_the_ciphertext(
    instance, official_age, failure,
):
    before = descriptors()
    with pytest.raises(type(failure)) as caught:
        with create(instance, official_age.public) as created:
            raise failure
    assert caught.value is failure
    assert created.stream.closed
    assert descriptors() == before


def test_work_lease_self_conflict_remains_busy_not_a_generic_creation_error(instance, official_age):
    with backup_operation(instance.barrier):
        with pytest.raises(BackupBusyError):
            with create(instance, official_age.public):
                pytest.fail("Creation accepted its own active work lease")
    assert list(instance.staging.iterdir()) == []


def test_dependency_check_runs_after_writes_resume_against_stable_private_database(
    instance, official_age, monkeypatch,
):
    original = module.check_backup_dependencies

    def check(connection, sources, **kwargs):
        # Would raise Busy if EX were still held. This committed live write must
        # not change the connection borrowed from the completed private copy.
        with backup_operation(instance.barrier):
            with sqlite3.connect(instance.layout.database) as live:
                live.execute("UPDATE unrelated_business_sentinel SET value='after-copy'")
        assert connection.execute("SELECT value FROM unrelated_business_sentinel").fetchone() == (
            "unchanged-business-value",
        )
        return original(connection, sources, **kwargs)

    monkeypatch.setattr(module, "check_backup_dependencies", check)
    with create(instance, official_age.public) as created:
        assert created.restoration_ready is False


def test_staging_directory_is_pinned_through_path_replacement(instance, official_age, monkeypatch):
    original_check = module.check_backup_dependencies
    original_encrypt = module.encrypted_backup_archive
    held = instance.staging.stat()
    displaced = instance.staging.with_name("displaced-staging")

    def check(*args, **kwargs):
        report = original_check(*args, **kwargs)
        instance.staging.rename(displaced)
        instance.staging.mkdir(mode=0o700)
        (instance.staging / "sentinel").write_bytes(b"unchanged")
        return report

    @contextmanager
    def encrypt(*args, **kwargs):
        actual = os.stat(kwargs["temporary_directory"])
        assert (actual.st_dev, actual.st_ino) == (held.st_dev, held.st_ino)
        with original_encrypt(*args, **kwargs) as encrypted:
            yield encrypted

    monkeypatch.setattr(module, "check_backup_dependencies", check)
    monkeypatch.setattr(module, "encrypted_backup_archive", encrypt)
    with create(instance, official_age.public) as created:
        assert created.stream.read(len(AGE_INTRO)) == AGE_INTRO
    assert list(displaced.iterdir()) == []
    assert {path.name for path in instance.staging.iterdir()} == {"sentinel"}
    assert (instance.staging / "sentinel").read_bytes() == b"unchanged"


def test_actual_pipeline_can_run_in_its_own_worker_thread(instance, official_age):
    def run():
        with create(instance, official_age.public) as created:
            return created.stream.read(len(AGE_INTRO)), created.dependencies.totp_status

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(run).result(timeout=15) == (AGE_INTRO, "verified")


def test_source_metadata_is_only_an_explicit_claim(instance, official_age):
    source = BackupSource("a" * 40, "sha256:" + "b" * 64, "a" * 40)
    with create(instance, official_age.public, source=source) as created:
        report = created.encryption.archive_report
        assert report.manifest.source == source
        assert report.source_authentication == "not_checked"


def test_contradictory_source_revisions_cannot_be_published(instance, official_age):
    source = BackupSource("a" * 40, "sha256:" + "b" * 64, "c" * 40)
    with pytest.raises(BackupCreationError):
        with create(instance, official_age.public, source=source):
            pytest.fail("Contradictory source metadata yielded")
    assert list(instance.staging.iterdir()) == []


def test_plaintext_cleanup_failure_after_dup_also_discards_the_ciphertext(
    instance, official_age, monkeypatch,
):
    original_snapshot = module.capture_control_plane_snapshot
    original_retain = module._retain_ciphertext
    copies, retained = [], []
    before = descriptors()

    @contextmanager
    def snapshot(*args, **kwargs):
        with original_snapshot(*args, **kwargs) as copied:
            copies.append(copied)
            yield copied
        raise OSError("synthetic cleanup failure must not reveal host paths")

    def retain(*args, **kwargs):
        stream = original_retain(*args, **kwargs)
        retained.append(stream)
        return stream

    monkeypatch.setattr(module, "capture_control_plane_snapshot", snapshot)
    monkeypatch.setattr(module, "_retain_ciphertext", retain)
    with pytest.raises(BackupCreationError) as caught:
        with create(instance, official_age.public):
            pytest.fail("Plaintext cleanup failed after retaining ciphertext")
    assert str(caught.value) == SAFE_ERROR
    assert len(retained) == 1 and retained[0].closed
    assert_plaintext_closed(copies)
    assert descriptors() == before
    assert list(instance.staging.iterdir()) == []


def test_retention_rejects_plaintext_or_caller_owned_writable_stream(tmp_path):
    path = tmp_path / "not-an-artifact"
    path.write_bytes(b"ordinary caller file")
    with module._resources() as resources, path.open("rb") as stream:
        with pytest.raises(BackupCreationError):
            module._retain_ciphertext(resources, stream, path.stat().st_size)
    with module._resources() as resources, tempfile.TemporaryFile("w+b", buffering=0) as stream:
        with pytest.raises(BackupCreationError):
            module._retain_ciphertext(resources, stream, 0)
    with module._resources() as resources:
        with pytest.raises(BackupCreationError):
            module._retain_ciphertext(resources, io.BytesIO(b"not a private artifact"), 22)
    assert path.read_bytes() == b"ordinary caller file"
