"""CLI/file publication tests; service stubs here are NOT cryptographic evidence."""

import errno
import fcntl
import hashlib
import io
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from open_node import backup_cli as cli
from open_node.services import backup_encryption as encryption
from open_node.services.backup_validation import validate_backup_archive
from test_backup_cli import SECRET, SUMMARY_FIELDS, invoke, package, signature, source_file

STUB_CIPHERTEXT = b"TEST-ONLY: service output stub, not age ciphertext\x00" * 1700
KEY_TEXT = b"TEST-ONLY identity bytes; native key parsing is tested in the service"
RECIPIENT = "TEST-ONLY public recipient; not a cryptographic fixture"
ENCRYPT_ERROR = cli.ENCRYPT_ERROR_MESSAGE + "\n"
CHECK_ERROR = cli.ERROR_MESSAGE + "\n"
ENCRYPTED_FIELDS = SUMMARY_FIELDS | {
    "encryption", "encrypted_size", "encrypted_sha256", "authenticated_decryption",
}


@contextmanager
def staged_output(raw: bytes, report, tmp_path):
    with tempfile.TemporaryFile(mode="w+b", buffering=0, dir=tmp_path) as private:
        private.write(raw)
        private.seek(0)
        descriptor = os.open(f"/proc/self/fd/{private.fileno()}", os.O_RDONLY)
        with os.fdopen(descriptor, "rb", buffering=0) as readonly:
            yield SimpleNamespace(stream=readonly, report=report)


def install_service_stub(monkeypatch, tmp_path, *, before_yield=None, failure=None):
    """Exercise real CLI path/descriptor/publication code, but replace only age service."""
    calls, streams = [], []

    def report(raw: bytes, decrypt: bool):
        return SimpleNamespace(
            archive_report=validate_backup_archive(io.BytesIO(raw)),
            encrypted_size=len(STUB_CIPHERTEXT),
            encrypted_sha256=hashlib.sha256(STUB_CIPHERTEXT).hexdigest(),
            encryption="age-v1-x25519", authenticated_decryption=decrypt,
        )

    def check_source(source):
        assert isinstance(source, io.FileIO)
        assert fcntl.fcntl(source.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert source.tell() == os.lseek(source.fileno(), 0, os.SEEK_CUR) == 0
        streams.append(source)

    @contextmanager
    def encrypt(source, recipient, **kwargs):
        check_source(source)
        calls.append(("encrypt", recipient, kwargs))
        raw = source.read(os.fstat(source.fileno()).st_size)
        if failure is not None:
            raise failure
        if before_yield is not None:
            before_yield()
        with staged_output(STUB_CIPHERTEXT, report(raw, False), tmp_path) as staged:
            streams.append(staged.stream)
            yield staged

    @contextmanager
    def decrypt(source, identity, **kwargs):
        check_source(source)
        calls.append(("decrypt", identity, kwargs))
        assert source.read(len(STUB_CIPHERTEXT) + 1) == STUB_CIPHERTEXT
        if failure is not None:
            raise failure
        if before_yield is not None:
            before_yield()
        raw, _ = package()
        with staged_output(raw, report(raw, True), tmp_path) as staged:
            streams.append(staged.stream)
            yield staged

    monkeypatch.setattr(encryption, "encrypted_backup_archive", encrypt)
    monkeypatch.setattr(encryption, "decrypted_backup_archive", decrypt)
    return calls, streams


def output_path(tmp_path):
    parent = tmp_path / "private-output"
    parent.mkdir(mode=0o700)
    return parent / (SECRET + ".zip.age")


def identity_file(tmp_path, mode=0o600):
    path = tmp_path / (SECRET + "-identity.txt")
    path.write_bytes(KEY_TEXT)
    path.chmod(mode)
    return path


def encrypt_args(source, output, *, json_mode=True):
    args = ["encrypt", str(source), "--recipient", RECIPIENT, "--output", str(output)]
    return args + ["--json"] if json_mode else args


def assert_failure(result, captured, *, encrypt=True):
    assert result == 1
    assert captured.out == ""
    assert captured.err == (ENCRYPT_ERROR if encrypt else CHECK_ERROR)
    assert SECRET not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("json_mode", [True, False])
def test_stub_service_output_published_privately_without_overwrite(
    tmp_path, monkeypatch, capsys, json_mode,
):
    source, raw, _ = source_file(tmp_path)
    before = signature(source)
    output = output_path(tmp_path)
    calls, streams = install_service_stub(monkeypatch, tmp_path)
    assert cli.main(encrypt_args(source, output, json_mode=json_mode)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_mode:
        result = json.loads(captured.out)
        assert result.keys() == ENCRYPTED_FIELDS
        assert result["encrypted_size"] == len(STUB_CIPHERTEXT)
        assert result["encrypted_sha256"] == hashlib.sha256(STUB_CIPHERTEXT).hexdigest()
        assert result["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()
        assert result["authenticated_decryption"] is False
        assert result["restoration_ready"] is False
        assert result["source_authentication"] == "not_checked"
    else:
        for phrase in ("已创建私有加密文件", "未覆盖已有文件", "未执行私钥解密验证",
                       "不证明发送者身份", "恢复就绪：否"):
            assert phrase in captured.out
    assert SECRET not in captured.out
    assert RECIPIENT not in captured.out
    assert output.read_bytes() == STUB_CIPHERTEXT
    info = output.stat()
    assert info.st_uid == os.geteuid()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_nlink == 1
    assert list(output.parent.iterdir()) == [output]
    assert source.read_bytes() == raw
    assert signature(source) == before
    assert calls == [("encrypt", RECIPIENT, {})]
    assert len(streams) == 2 and all(stream.closed for stream in streams)


@pytest.mark.parametrize("json_mode", [True, False])
@pytest.mark.parametrize("key_mode", [0o400, 0o600])
def test_stub_authenticated_validation_reports_without_plaintext_publication(
    tmp_path, monkeypatch, capsys, json_mode, key_mode,
):
    source = tmp_path / (SECRET + ".age")
    source.write_bytes(STUB_CIPHERTEXT)
    identity = identity_file(tmp_path, key_mode)
    before = {path: signature(path) for path in (source, identity)}
    calls, streams = install_service_stub(monkeypatch, tmp_path)
    args = ["validate", str(source), "--identity", str(identity)]
    assert cli.main(args + (["--json"] if json_mode else [])) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_mode:
        result = json.loads(captured.out)
        assert result.keys() == ENCRYPTED_FIELDS
        assert result["authenticated_decryption"] is True
        assert result["source_authentication"] == "not_checked"
        assert result["restoration_ready"] is False
    else:
        assert "完整解密认证通过" in captured.out
        assert "未发布明文文件" in captured.out
        assert "恢复就绪：否" in captured.out
    assert SECRET not in captured.out
    assert KEY_TEXT.decode() not in captured.out
    assert calls == [("decrypt", KEY_TEXT, {})]
    assert len(streams) == 2 and all(stream.closed for stream in streams)
    assert set(tmp_path.iterdir()) == {source, identity}
    assert {path: signature(path) for path in (source, identity)} == before


@pytest.mark.parametrize("args,encrypt_error", [
    (["encrypt", SECRET], True),
    (["encrypt", SECRET, "--recipient", SECRET], True),
    (["encrypt", SECRET, "--output", SECRET], True),
    (["encrypt", SECRET, "--recipient", SECRET, "--output", SECRET,
      "--identity", SECRET], True),
    (["validate", SECRET, "--output", SECRET], False),
    (["validate", SECRET, "--recipient", SECRET], False),
    (["decrypt", SECRET], False),
    (["encrypt", SECRET, "--password", SECRET], False),
    (["encrypt", SECRET, "--recipient", SECRET, "--recipient", "second-key"], False),
    (["encrypt", SECRET, "--output", SECRET, "--output", "second-file"], False),
    (["validate", SECRET, "--identity", SECRET, "--identity", "second-keyfile"], False),
])
def test_real_module_rejects_cross_command_options_and_echoes_no_inputs(
    tmp_path, args, encrypt_error,
):
    result = invoke(tmp_path, *args)
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr == (ENCRYPT_ERROR if encrypt_error else CHECK_ERROR)
    assert SECRET not in result.stderr


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling", "fifo", "hardlink"])
def test_existing_output_is_rejected_before_service_and_never_modified(
    tmp_path, monkeypatch, capsys, kind,
):
    source, raw, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    if kind == "file":
        output.write_bytes(b"existing-output-canary")
    elif kind == "directory":
        output.mkdir()
    elif kind == "symlink":
        output.symlink_to(source)
    elif kind == "dangling":
        output.symlink_to(output.parent / "missing-target")
    elif kind == "fifo":
        os.mkfifo(output, 0o600)
    else:
        os.link(source, output)
    before = output.lstat()
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert calls == []
    assert output.lstat() == before
    assert list(output.parent.iterdir()) == [output]
    assert source.read_bytes() == raw


@pytest.mark.parametrize("path", ["-", ".", "..", "output/.", "output/..", "output/",
                                   "https://example.invalid/" + SECRET])
def test_invalid_output_paths_fail_before_source_or_service(
    tmp_path, monkeypatch, capsys, path,
):
    monkeypatch.chdir(tmp_path)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(encrypt_args(SECRET, path))
    assert_failure(result, capsys.readouterr())
    assert calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["missing", "symlink", "group_writable", "world_writable"])
def test_output_parent_must_be_owned_non_writable_by_others_directory(
    tmp_path, monkeypatch, capsys, kind,
):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    if kind == "missing":
        output = tmp_path / "missing" / "encrypted.age"
    elif kind == "symlink":
        link = tmp_path / "parent-link"
        link.symlink_to(output.parent, target_is_directory=True)
        output = link / output.name
    else:
        output.parent.chmod(0o770 if kind == "group_writable" else 0o777)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert calls == [] and not output.exists()


def test_output_created_between_preflight_and_link_is_not_overwritten(
    tmp_path, monkeypatch, capsys,
):
    source, raw, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    existing = b"another-owner-created-this-output"
    install_service_stub(monkeypatch, tmp_path, before_yield=lambda: output.write_bytes(existing))
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert output.read_bytes() == existing
    assert list(output.parent.iterdir()) == [output]
    assert source.read_bytes() == raw


def test_same_input_output_is_rejected_before_service(tmp_path, monkeypatch, capsys):
    source, raw, _ = source_file(tmp_path)
    before = signature(source)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(encrypt_args(source, source))
    assert_failure(result, capsys.readouterr())
    assert source.read_bytes() == raw and signature(source) == before
    assert calls == []


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "dangling", "fifo"])
def test_encrypt_source_special_files_do_not_reach_service(tmp_path, monkeypatch, capsys, kind):
    source, raw, _ = source_file(tmp_path)
    selected = tmp_path / (SECRET + "-" + kind)
    if kind == "directory":
        selected.mkdir()
    elif kind == "symlink":
        selected.symlink_to(source)
    elif kind == "dangling":
        selected.symlink_to(tmp_path / "missing")
    elif kind == "fifo":
        os.mkfifo(selected, 0o600)
    output = output_path(tmp_path)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(encrypt_args(selected, output))
    assert_failure(result, capsys.readouterr())
    assert calls == [] and list(output.parent.iterdir()) == []
    assert source.read_bytes() == raw


@pytest.mark.parametrize("change", ["grow", "shrink", "rewrite"])
def test_observed_source_mutation_prevents_publication(tmp_path, monkeypatch, capsys, change):
    source, raw, _ = source_file(tmp_path)
    before = source.stat()
    output = output_path(tmp_path)

    def mutate():
        source.write_bytes(raw + b"x" if change == "grow" else raw[:-1] if change == "shrink"
                           else b"x" + raw[1:])
        # A rapid same-size write can share the filesystem's timestamp tick.
        # This test promises an observable change, not a snapshot lock against
        # uncooperative writers; make its timestamp change deterministic.
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    calls, streams = install_service_stub(monkeypatch, tmp_path, before_yield=mutate)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert len(calls) == 1 and all(stream.closed for stream in streams)
    assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("failure", [
    OSError(SECRET), ValueError(SECRET), KeyboardInterrupt(SECRET),
])
def test_service_errors_and_interrupts_are_safe_and_close_source(
    tmp_path, monkeypatch, capsys, failure,
):
    source, raw, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    calls, streams = install_service_stub(monkeypatch, tmp_path, failure=failure)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert len(calls) == 1 and all(stream.closed for stream in streams)
    assert source.read_bytes() == raw
    assert list(output.parent.iterdir()) == []


@pytest.mark.parametrize("fault", ["write_error", "write_zero", "write_bool", "fsync_file",
                                    "fsync_parent", "fsync_final", "link"])
def test_failed_publication_discards_only_owned_partial_files(tmp_path, monkeypatch, capsys, fault):
    source, raw, _ = source_file(tmp_path)
    before = signature(source)
    output = output_path(tmp_path)
    sentinel = output.parent / "older-backup.age"
    sentinel.write_bytes(b"previous-complete-output")
    install_service_stub(monkeypatch, tmp_path)
    original_write, original_fsync = os.write, os.fsync
    writes = syncs = 0

    def write(descriptor, data):
        nonlocal writes
        writes += 1
        if writes == 2:
            if fault == "write_error":
                raise OSError(errno.ENOSPC, SECRET)
            if fault == "write_zero":
                return 0
            if fault == "write_bool":
                return True
        return original_write(descriptor, data)

    def sync(descriptor):
        nonlocal syncs
        syncs += 1
        if {"fsync_file": 1, "fsync_parent": 2, "fsync_final": 3}.get(fault) == syncs:
            raise OSError(SECRET)
        original_fsync(descriptor)

    def link(*args, **kwargs):
        raise OSError(errno.EXDEV, SECRET)

    monkeypatch.setattr(cli.os, "write", write)
    monkeypatch.setattr(cli.os, "fsync", sync)
    if fault == "link":
        monkeypatch.setattr(cli.os, "link", link)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    if fault in ("fsync_parent", "fsync_final"):
        # The complete ciphertext was atomically linked before this durability
        # failure. It is retained, never removed by a racy public-name rollback.
        assert set(output.parent.iterdir()) == {sentinel, output}
        assert output.read_bytes() == STUB_CIPHERTEXT
    else:
        assert list(output.parent.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"previous-complete-output"
    assert source.read_bytes() == raw and signature(source) == before


def test_short_publication_writes_are_filled_and_rehashed(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    install_service_stub(monkeypatch, tmp_path)
    original_write = os.write
    calls = []

    def short(descriptor, data):
        calls.append(len(data))
        return original_write(descriptor, data[:17])

    monkeypatch.setattr(cli.os, "write", short)
    assert cli.main(encrypt_args(source, output)) == 0
    assert capsys.readouterr().err == ""
    assert len(calls) > 2 and max(calls) == cli.COPY_CHUNK_BYTES
    assert output.read_bytes() == STUB_CIPHERTEXT
    assert list(output.parent.iterdir()) == [output]


def test_rename_of_output_parent_does_not_redirect_publication(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    original_parent = output.parent
    moved = tmp_path / "operator-moved-output"

    def move_parent():
        original_parent.rename(moved)
        original_parent.mkdir(mode=0o700)

    install_service_stub(monkeypatch, tmp_path, before_yield=move_parent)
    assert cli.main(encrypt_args(source, output)) == 0
    assert capsys.readouterr().err == ""
    assert list(original_parent.iterdir()) == []
    assert (moved / output.name).read_bytes() == STUB_CIPHERTEXT


@pytest.mark.parametrize("mode", [0o000, 0o500, 0o640, 0o644, 0o700, 0o660])
def test_private_identity_rejects_unapproved_modes_before_service(
    tmp_path, monkeypatch, capsys, mode,
):
    identity = identity_file(tmp_path, mode)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(["validate", SECRET, "--identity", str(identity), "--json"])
    assert_failure(result, capsys.readouterr(), encrypt=False)
    assert calls == []


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo", "missing"])
def test_private_identity_leaf_must_be_single_link_regular_file(
    tmp_path, monkeypatch, capsys, kind,
):
    original = identity_file(tmp_path)
    selected = tmp_path / (SECRET + "-selected")
    if kind == "symlink":
        selected.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, selected)
    elif kind == "directory":
        selected.mkdir()
    elif kind == "fifo":
        os.mkfifo(selected, 0o600)
    calls, _ = install_service_stub(monkeypatch, tmp_path)
    result = cli.main(["validate", SECRET, "--identity", str(selected), "--json"])
    assert_failure(result, capsys.readouterr(), encrypt=False)
    assert calls == [] and original.read_bytes() == KEY_TEXT


@pytest.mark.parametrize("size", [0, 4097])
def test_empty_and_oversized_identity_rejected_before_read(tmp_path, monkeypatch, size):
    identity = identity_file(tmp_path)
    identity.write_bytes(b"x" * size)

    def forbidden(*args, **kwargs):
        raise AssertionError("must reject from fstat before fdopen")

    monkeypatch.setattr(cli.os, "fdopen", forbidden)
    with pytest.raises(cli._CLIError):
        cli._identity_bytes(str(identity))


def test_private_identity_must_belong_to_caller(tmp_path, monkeypatch):
    identity = identity_file(tmp_path)
    caller = os.geteuid()
    monkeypatch.setattr(cli.os, "geteuid", lambda: caller + 1)
    with pytest.raises(cli._CLIError):
        cli._identity_bytes(str(identity))


def test_stdout_failure_after_publication_keeps_complete_output(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    install_service_stub(monkeypatch, tmp_path)

    class BrokenOutput:
        def write(self, data):
            raise BrokenPipeError(SECRET)

    with monkeypatch.context() as context:
        context.setattr(cli.sys, "stdout", BrokenOutput())
        result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert output.read_bytes() == STUB_CIPHERTEXT
    assert list(output.parent.iterdir()) == [output]


def test_error_path_does_not_delete_unrelated_replacement_of_temporary_name(
    tmp_path, monkeypatch, capsys,
):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    install_service_stub(monkeypatch, tmp_path)
    original = cli._write_encrypted_file
    replacement = []

    def replace_name(*args, **kwargs):
        original(*args, **kwargs)
        temporary, = output.parent.iterdir()
        temporary.unlink()
        temporary.write_bytes(b"unrelated-replacement-canary")
        replacement.append(temporary)

    monkeypatch.setattr(cli, "_write_encrypted_file", replace_name)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert list(output.parent.iterdir()) == replacement
    assert replacement[0].read_bytes() == b"unrelated-replacement-canary"


def test_opened_cli_descriptors_are_closed_after_service_failure(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    install_service_stub(monkeypatch, tmp_path, failure=OSError(SECRET))
    original_open = os.open
    descriptors = []

    def tracked(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path) in (source, output.parent):
            descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(cli.os, "open", tracked)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF


def test_post_publication_failure_never_removes_replaced_public_name(
    tmp_path, monkeypatch, capsys,
):
    source, raw, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    install_service_stub(monkeypatch, tmp_path)
    original_fsync = os.fsync
    syncs = 0
    replacement = b"other-process-complete-output-canary"

    def sync(descriptor):
        nonlocal syncs
        syncs += 1
        if syncs == 2:
            assert output.read_bytes() == STUB_CIPHERTEXT
            output.unlink()
            output.write_bytes(replacement)
            raise OSError(errno.EIO, SECRET)
        original_fsync(descriptor)

    monkeypatch.setattr(cli.os, "fsync", sync)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert syncs == 2
    assert output.read_bytes() == replacement
    assert list(output.parent.iterdir()) == [output]
    assert source.read_bytes() == raw


@pytest.mark.parametrize("fault", ["write", "fsync"])
def test_created_ciphertext_descriptor_is_closed_after_publication_failure(
    tmp_path, monkeypatch, capsys, fault,
):
    source, _, _ = source_file(tmp_path)
    output = output_path(tmp_path)
    _, streams = install_service_stub(monkeypatch, tmp_path)
    original_open, original_write, original_fsync = os.open, os.write, os.fsync
    descriptors = {}

    def tracked(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        selected = Path(path)
        if selected == source:
            descriptors["source"] = descriptor
        elif selected == output.parent:
            descriptors["parent"] = descriptor
        elif selected.name.startswith(".open-node-encrypted-"):
            assert kwargs.get("dir_fd") == descriptors["parent"]
            descriptors["ciphertext"] = descriptor
        return descriptor

    def write(descriptor, data):
        if fault == "write" and descriptor == descriptors.get("ciphertext"):
            raise OSError(errno.ENOSPC, SECRET)
        return original_write(descriptor, data)

    def sync(descriptor):
        if fault == "fsync" and descriptor == descriptors.get("ciphertext"):
            assert os.fstat(descriptor).st_size == len(STUB_CIPHERTEXT)
            raise OSError(errno.EIO, SECRET)
        return original_fsync(descriptor)

    monkeypatch.setattr(cli.os, "open", tracked)
    monkeypatch.setattr(cli.os, "write", write)
    monkeypatch.setattr(cli.os, "fsync", sync)
    result = cli.main(encrypt_args(source, output))
    assert_failure(result, capsys.readouterr())
    assert descriptors.keys() == {"source", "parent", "ciphertext"}
    assert len(set(descriptors.values())) == 3
    for descriptor in descriptors.values():
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF
    assert all(stream.closed for stream in streams)
    assert list(output.parent.iterdir()) == []


def test_private_identity_descriptor_is_closed_after_read_failure(tmp_path, monkeypatch, capsys):
    identity = identity_file(tmp_path)
    original_fdopen = os.fdopen
    opened = []
    calls, _ = install_service_stub(monkeypatch, tmp_path)

    def fail_read(size):
        raise OSError(errno.EIO, SECRET)

    @contextmanager
    def fdopen(descriptor, *args, **kwargs):
        with original_fdopen(descriptor, *args, **kwargs) as source:
            opened.append((descriptor, source))
            yield SimpleNamespace(read=fail_read)

    monkeypatch.setattr(cli.os, "fdopen", fdopen)
    result = cli.main(["validate", SECRET, "--identity", str(identity), "--json"])
    assert_failure(result, capsys.readouterr(), encrypt=False)
    assert calls == [] and len(opened) == 1
    descriptor, stream = opened[0]
    assert stream.closed
    with pytest.raises(OSError) as caught:
        os.fstat(descriptor)
    assert caught.value.errno == errno.EBADF
    assert identity.read_bytes() == KEY_TEXT
