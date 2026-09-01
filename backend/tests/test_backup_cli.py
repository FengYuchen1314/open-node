"""Actual CLI subprocesses plus focused fault injection; no application is started."""

import errno
import hashlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from open_node import backup_cli as cli
from open_node.services.backup_validation import MAX_ARCHIVE_BYTES, validate_backup_archive

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
SECRET = "private-source-path-token-never-print"
DB_BYTES = b"This is deliberately not a SQLite database."
ERROR = "备份包检查失败：输入无效、不可读取或超出支持范围。未执行恢复。\n"
SUMMARY_FIELDS = {
    "archive_size", "payload_size", "file_count", "checked_archive_sha256", "manifest_sha256",
    "structure_verified", "content_hashes_verified", "source_authentication",
    "database_validation", "key_validation", "snapshot_validation", "restore_validation",
    "restoration_ready",
}
UNCHECKED = {
    "source_authentication", "database_validation", "key_validation", "snapshot_validation",
    "restore_validation",
}
IMPORT_GUARD = '''\
import sys

class NoApplicationImports:
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            "open_node.main", "open_node.core", "open_node.services.inventory",
            "open_node.services.certificates", "open_node.services.auth",
            "open_node.services.notifications", "sqlalchemy", "cryptography", "fastapi",
        )
        service = fullname.startswith("open_node.services.") and fullname != (
            "open_node.services.backup_validation"
        )
        domain = fullname.startswith("open_node.domain.") and fullname != "open_node.domain.backup"
        if service or domain or any(
            fullname == item or fullname.startswith(item + ".") for item in blocked
        ):
            raise RuntimeError("Application import is forbidden in this CLI test")

sys.meta_path.insert(0, NoApplicationImports())
'''


def package(body: bytes = DB_BYTES) -> tuple[bytes, bytes]:
    manifest = json.dumps({
        "format": "open-node-control-plane-backup", "version": 1,
        "created_at": "2026-08-31T09:00:00Z",
        "source": {"git_revision": "b" * 40, "image_id": None, "image_revision": "b" * 40},
        "database": {"engine": "sqlite", "layout": "standalone", "schema_fingerprint": None},
        "coverage": {
            "certificates": "unknown", "external_subscriptions": "not_configured",
            "notifications": "unknown", "agent_identity": "not_configured",
            "federation": "not_configured",
        },
        "required_configuration": ["deployment_settings", "subscriber_totp_key"],
        "files": [{
            "path": "data/open-node.db", "role": "database", "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }],
    }, ensure_ascii=False, separators=(",", ":")).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("data/open-node.db", body)
    return stream.getvalue(), manifest


def source_file(tmp_path: Path, body: bytes = DB_BYTES) -> tuple[Path, bytes, bytes]:
    raw, manifest = package(body)
    source = tmp_path / f"{SECRET}-备份.zip"
    source.write_bytes(raw)
    return source, raw, manifest


def signature(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
    )


def invoke(
    tmp_path: Path, *args: str, prelude: str = "", timeout: float = 10,
    child_encoding: str = "utf-8",
):
    runtime = tmp_path / "cli-runtime"
    runtime.mkdir(exist_ok=True)
    guard = tmp_path / "cli-guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text(IMPORT_GUARD + prelude, encoding="utf-8")
    # Loading Settings would fail or attempt to touch these missing application paths.
    (runtime / ".env").write_text(
        'OPEN_NODE_SUBSCRIBER_TOTP_KEY="invalid-application-configuration"\n', encoding="utf-8",
    )
    application_data = runtime / "application-must-not-exist"
    env = {
        "PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join((str(guard), str(APP_ROOT))),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": child_encoding,
        "OPEN_NODE_DATABASE_URL": f"sqlite:///{application_data}/open-node.db",
        "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
        "OPEN_NODE_CERTIFICATE_STATE_DIR": str(application_data / "certificates"),
        "OPEN_NODE_NOTIFICATIONS_STATE_DIR": str(application_data / "notifications"),
        "OPEN_NODE_AGENT_IDENTITY_FILE": str(application_data / "missing-identity.seed"),
        "OPEN_NODE_SUBSCRIBER_TOTP_KEY": "invalid-environment-key",
        "TMPDIR": str(runtime / "must-not-use-env-temp"),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-m", "open_node.backup_cli", *args],
        cwd=runtime, env=env, stdin=subprocess.DEVNULL, capture_output=True,
        encoding="utf-8", timeout=timeout, check=False,
    )
    assert not application_data.exists()
    assert not (runtime / "data").exists()
    assert not (runtime / "must-not-use-env-temp").exists()
    assert sorted(path.name for path in runtime.iterdir()) == [".env"]
    assert not list(guard.glob("__pycache__"))
    return result


def failed(result) -> None:
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ERROR
    assert SECRET not in result.stderr
    assert "Traceback" not in result.stderr


def test_real_module_json_is_projected_and_not_recovery_approval(tmp_path):
    source, raw, manifest = source_file(tmp_path)
    before = signature(source)
    result = invoke(tmp_path, "validate", str(source), "--json")
    assert result.returncode == 0
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert set(report) == SUMMARY_FIELDS
    assert report["archive_size"] == len(raw)
    assert report["payload_size"] == len(DB_BYTES)
    assert report["file_count"] == 1
    assert report["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    assert report["structure_verified"] is True
    assert report["content_hashes_verified"] is True
    assert all(report[field] == "not_checked" for field in UNCHECKED)
    assert report["restoration_ready"] is False
    for private in (SECRET, "data/open-node.db", "b" * 40, str(source), DB_BYTES.decode()):
        assert private not in result.stdout
    assert signature(source) == before
    assert source.read_bytes() == raw
    assert signature(source) == before


def test_real_module_human_output_is_chinese_and_limited(tmp_path):
    source, raw, _ = source_file(tmp_path)
    result = invoke(tmp_path, "validate", str(source))
    assert result.returncode == 0
    assert result.stderr == ""
    for phrase in ("结构与内容摘要检查通过", "数据库可用性", "密钥配对", "来源真实性",
                   "一致快照", "实际恢复", "恢复就绪：否", "匿名私有暂存副本", "未写入源文件"):
        assert phrase in result.stdout
    assert hashlib.sha256(raw).hexdigest() in result.stdout
    assert SECRET not in result.stdout
    assert "data/open-node.db" not in result.stdout


@pytest.mark.parametrize("args", [("--help",), ("validate", "--help"), ("-h",)])
def test_real_help_has_no_source_or_application_access(tmp_path, args):
    result = invoke(tmp_path, *args)
    assert result.returncode == 0
    assert result.stderr == ""
    displayed = " ".join(result.stdout.split())
    for phrase in ("用法：", "位置参数", "选项", "不解压", "不恢复", "不加载应用配置",
                   "暂存", "30 秒", "软期限", "阻塞 I/O", "数据库", "密钥", "来源", "一致快照"):
        assert phrase in displayed


@pytest.mark.parametrize("strict_stderr", [False, True])
@pytest.mark.parametrize("kind", ["human", "help", "usage", "bad_package"])
def test_real_ascii_terminal_failures_never_escape_with_traceback(
    tmp_path, kind, strict_stderr,
):
    source, raw, _ = source_file(tmp_path)
    if kind == "bad_package":
        raw = (SECRET * 8).encode()
        source.write_bytes(raw)
    before = signature(source)
    args = {
        "human": ("validate", str(source)),
        "help": ("--help",),
        "usage": ("restore", str(source)),
        "bad_package": ("validate", str(source), "--json"),
    }[kind]
    # CPython normally backslash-escapes stderr even under an ASCII environment.
    # The stricter case exercises the second encoding failure in the error path.
    prelude = (
        '\nimport sys\nsys.stderr.reconfigure(encoding="ascii", errors="strict")\n'
        if strict_stderr else ""
    )
    result = invoke(tmp_path, *args, prelude=prelude, child_encoding="ascii")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "" if strict_stderr else ERROR.encode("ascii", errors="backslashreplace").decode()
    )
    assert "Traceback" not in result.stderr
    assert SECRET not in result.stderr
    assert source.read_bytes() == raw
    assert signature(source) == before


@pytest.mark.parametrize("strict_stderr", [False, True])
def test_real_ascii_terminal_can_still_output_safe_json(tmp_path, strict_stderr):
    source, raw, _ = source_file(tmp_path)
    before = signature(source)
    prelude = (
        '\nimport sys\nsys.stderr.reconfigure(encoding="ascii", errors="strict")\n'
        if strict_stderr else ""
    )
    result = invoke(
        tmp_path, "validate", str(source), "--json", prelude=prelude, child_encoding="ascii",
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.isascii()
    report = json.loads(result.stdout)
    assert set(report) == SUMMARY_FIELDS
    assert report["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert all(report[field] == "not_checked" for field in UNCHECKED)
    assert report["restoration_ready"] is False
    assert source.read_bytes() == raw
    assert signature(source) == before


def test_closed_stderr_is_safe_exit_one(monkeypatch, capsys):
    closed = io.StringIO()
    closed.close()
    with monkeypatch.context() as context:
        context.setattr(cli.sys, "stderr", closed)
        result = cli.main(["restore", SECRET])
    assert result == 1
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("phase", ["write", "flush"])
@pytest.mark.parametrize("failure", ["os", "unicode", "closed"])
def test_error_sink_failures_are_contained(monkeypatch, capsys, phase, failure):
    written = []

    class Unwritable:
        def reject(self):
            if failure == "os":
                raise OSError(SECRET)
            if failure == "unicode":
                raise UnicodeEncodeError("ascii", "敏感", 0, 2, SECRET)
            raise ValueError(SECRET)

        def write(self, message):
            if phase == "write":
                self.reject()
            written.append(message)

        def flush(self):
            if phase == "flush":
                self.reject()

    with monkeypatch.context() as context:
        context.setattr(cli.sys, "stderr", Unwritable())
        result = cli.main(["restore", SECRET])
    assert result == 1
    assert written == ([] if phase == "write" else [ERROR])
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("args", [
    (), ("validate",), ("restore", SECRET), ("create", SECRET),
    ("download", SECRET), ("validate", SECRET, "--unknown=" + SECRET),
    ("validate", SECRET, SECRET), ("--password=" + SECRET,),
    ("validate", SECRET, "--j"),
])
def test_usage_errors_are_safe_exit_one_not_argparse_echo(tmp_path, args):
    failed(invoke(tmp_path, *args))


@pytest.mark.parametrize("path", ["-", "https://example.invalid/" + SECRET,
                                   "file:///" + SECRET, "stdin:", "data:" + SECRET])
def test_stdin_and_uris_are_not_inputs(tmp_path, path):
    failed(invoke(tmp_path, "validate", path, "--json"))


def test_explicit_relative_path_can_contain_colon_without_becoming_url(tmp_path):
    source, raw, _ = source_file(tmp_path)
    relative = tmp_path / "local:archive.zip"
    relative.write_bytes(raw)
    result = invoke(tmp_path, "validate", "../local:archive.zip", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["archive_size"] == source.stat().st_size


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "dangling", "fifo", "socket"])
def test_real_special_or_missing_sources_fail_without_blocking(tmp_path, monkeypatch, kind):
    source, raw, _ = source_file(tmp_path)
    path = tmp_path / (SECRET + "-" + kind)
    listener = None
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(source)
    elif kind == "dangling":
        path.symlink_to(tmp_path / "missing-target")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "socket":
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Linux's sockaddr_un is short; pytest's private absolute root need not be.
        with monkeypatch.context() as context:
            context.chdir(tmp_path)
            listener.bind(path.name)
    try:
        failed(invoke(tmp_path, "validate", str(path), "--json", timeout=3))
        assert source.read_bytes() == raw
    finally:
        if listener is not None:
            listener.close()


def test_symlink_parent_is_allowed_but_source_is_held_once(tmp_path):
    source, raw, _ = source_file(tmp_path)
    parent = tmp_path / "parent-link"
    parent.symlink_to(tmp_path, target_is_directory=True)
    result = invoke(tmp_path, "validate", str(parent / source.name), "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK"])
def test_missing_required_flag_fails_in_real_module_before_open(tmp_path, flag):
    source, _, _ = source_file(tmp_path)
    prelude = f'\nimport os\ndelattr(os, {flag!r})\n'
    failed(invoke(tmp_path, "validate", str(source), "--json", prelude=prelude))


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("kind", ["short", "invalid", "bad_crc", "trailing"])
def test_real_bad_packages_have_no_sensitive_diagnostics(tmp_path, kind, json_mode):
    source, raw, _ = source_file(tmp_path, (SECRET * 5).encode())
    if kind == "short":
        raw = b"secret"
    elif kind == "invalid":
        raw = (SECRET * 8).encode()
    elif kind == "bad_crc":
        damaged = bytearray(raw)
        damaged[raw.index((SECRET * 5).encode())] ^= 1
        raw = bytes(damaged)
    else:
        raw += SECRET.encode()
    source.write_bytes(raw)
    before = signature(source)
    args = ("validate", str(source), "--json") if json_mode else ("validate", str(source))
    failed(invoke(tmp_path, *args))
    assert signature(source) == before
    assert source.read_bytes() == raw


def test_real_oversized_sparse_file_is_rejected_before_any_read(tmp_path):
    source = tmp_path / (SECRET + ".zip")
    with source.open("wb") as stream:
        stream.truncate(MAX_ARCHIVE_BYTES + 1)
    before = signature(source)
    prelude = '''
import os
def forbid_read(*args, **kwargs):
    raise SystemExit(73)
os.read = forbid_read
'''
    failed(invoke(tmp_path, "validate", str(source), "--json", prelude=prelude))
    assert signature(source) == before


@pytest.mark.parametrize("size", [0, 65535, 65536, 65537, 196613])
def test_real_copy_and_validator_cover_chunk_boundaries(tmp_path, size):
    source, raw, _ = source_file(tmp_path, b"x" * size)
    result = invoke(tmp_path, "validate", str(source), "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()


def tracked_resources(monkeypatch, source):
    opened, staged = [], []
    original_open, original_temp = os.open, tempfile.TemporaryFile

    def open_source(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == str(source):
            opened.append((descriptor, flags))
        return descriptor

    def private_temp(*args, **kwargs):
        assert kwargs["dir"] == "/tmp"
        assert kwargs["mode"] == "w+b"
        stream = original_temp(*args, **kwargs)
        info = os.fstat(stream.fileno())
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_nlink == 0
        assert info.st_uid == os.geteuid()
        staged.append(stream)
        return stream

    monkeypatch.setattr(cli.os, "open", open_source)
    monkeypatch.setattr(cli.tempfile, "TemporaryFile", private_temp)
    return opened, staged


def assert_closed(opened, staged):
    assert len(opened) == 1
    descriptor, flags = opened[0]
    assert flags & os.O_ACCMODE == os.O_RDONLY
    assert flags & os.O_NOFOLLOW
    assert flags & os.O_NONBLOCK
    with pytest.raises(OSError) as caught:
        os.fstat(descriptor)
    assert caught.value.errno == errno.EBADF
    assert len(staged) == 1
    assert staged[0].closed


@pytest.mark.parametrize("valid", [True, False])
def test_temp_and_source_descriptors_are_closed_on_both_paths(tmp_path, monkeypatch, capsys, valid):
    source, raw, _ = source_file(tmp_path)
    if not valid:
        source.write_bytes(SECRET.encode() * 4)
    opened, staged = tracked_resources(monkeypatch, source)
    assert cli.main(["validate", str(source), "--json"]) == (0 if valid else 1)
    out = capsys.readouterr()
    if valid:
        assert json.loads(out.out)["archive_size"] == len(raw)
        assert out.err == ""
    else:
        assert out.out == ""
        assert out.err == ERROR
    assert_closed(opened, staged)


@pytest.mark.parametrize("short_read", [None, 7, 31])
def test_every_source_read_is_finite_and_short_reads_are_filled(
    tmp_path, monkeypatch, capsys, short_read,
):
    source, raw, _ = source_file(tmp_path, b"a" * 196613)
    original_read = os.read
    requests = []

    def read(descriptor, size):
        assert type(size) is int and 1 <= size <= 65536
        requests.append(size)
        return original_read(descriptor, min(size, short_read) if short_read else size)

    monkeypatch.setattr(cli.os, "read", read)
    assert cli.main(["validate", str(source), "--json"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert json.loads(out.out)["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert max(requests) == 65536
    assert requests[-1] == 1


@pytest.mark.parametrize("change", ["grow", "shrink", "same_size"])
def test_copy_rejects_observed_source_changes_and_cleans_temp(
    tmp_path, monkeypatch, capsys, change,
):
    source, raw, _ = source_file(tmp_path, b"a" * 100000)
    opened, staged = tracked_resources(monkeypatch, source)
    original_read = os.read
    modified = False
    before_mtime = source.stat().st_mtime_ns

    def read(descriptor, size):
        nonlocal modified
        block = original_read(descriptor, size)
        if not modified:
            modified = True
            with source.open("r+b") as writer:
                if change == "grow":
                    writer.seek(0, io.SEEK_END)
                    writer.write(b"x")
                elif change == "shrink":
                    writer.truncate(len(raw) - 1)
                else:
                    writer.seek(-1, io.SEEK_END)
                    writer.write(b"x")
            os.utime(source, ns=(source.stat().st_atime_ns, before_mtime + 1000000))
        return block

    monkeypatch.setattr(cli.os, "read", read)
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ERROR
    assert modified
    assert_closed(opened, staged)


def test_copy_soft_deadline_checked_after_read_and_temp_closed(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    opened, staged = tracked_resources(monkeypatch, source)
    original_read = os.read
    now = [0.0]
    validator_calls = []

    def read(descriptor, size):
        block = original_read(descriptor, size)
        now[0] = 31.0
        return block

    monkeypatch.setattr(cli.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cli.os, "read", read)
    monkeypatch.setattr(
        cli, "validate_backup_archive", lambda stream: validator_calls.append(stream),
    )
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert validator_calls == []
    assert_closed(opened, staged)


def test_copy_operation_budget_stops_degenerate_short_reads(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    opened, staged = tracked_resources(monkeypatch, source)
    original_read = os.read
    calls = []

    def read(descriptor, size):
        calls.append(size)
        return original_read(descriptor, 1)

    monkeypatch.setattr(cli, "MAX_COPY_READS", 3)
    monkeypatch.setattr(cli.os, "read", read)
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert len(calls) == 3
    assert_closed(opened, staged)


@pytest.mark.parametrize("failure", ["read", "validate", "interrupt"])
def test_sensitive_errors_do_not_escape_and_resources_close(
    tmp_path, monkeypatch, capsys, failure,
):
    source, _, _ = source_file(tmp_path)
    opened, staged = tracked_resources(monkeypatch, source)

    def reject(*args, **kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt(SECRET)
        raise OSError(SECRET)

    if failure == "read":
        monkeypatch.setattr(cli.os, "read", reject)
    else:
        monkeypatch.setattr(cli, "validate_backup_archive", reject)
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert_closed(opened, staged)


@pytest.mark.parametrize("field,value", [
    ("checked_archive_sha256", "0" * 64), ("archive_size", 22),
])
def test_copied_sha_and_size_must_match_validator_report(
    tmp_path, monkeypatch, capsys, field, value,
):
    source, _, _ = source_file(tmp_path)
    opened, staged = tracked_resources(monkeypatch, source)

    def wrong_report(stream):
        return replace(validate_backup_archive(stream), **{field: value})

    monkeypatch.setattr(cli, "validate_backup_archive", wrong_report)
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert_closed(opened, staged)


def test_named_or_nonprivate_staging_is_not_accepted(tmp_path, monkeypatch, capsys):
    source, _, _ = source_file(tmp_path)
    staging = (tmp_path / "not-anonymous.tmp").open("w+b")
    monkeypatch.setattr(cli.tempfile, "TemporaryFile", lambda **kwargs: staging)
    assert cli.main(["validate", str(source), "--json"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ERROR
    assert staging.closed


def test_snapshot_is_private_and_separate_from_mutable_original(tmp_path, monkeypatch, capsys):
    source, raw, _ = source_file(tmp_path)
    opened, staged = tracked_resources(monkeypatch, source)

    def inspect_snapshot(stream):
        source.write_bytes(b"another writer changed the original after copying")
        assert stream is staged[0]
        assert os.fstat(stream.fileno()).st_nlink == 0
        assert stream.read() == raw
        stream.seek(0)
        return validate_backup_archive(stream)

    monkeypatch.setattr(cli, "validate_backup_archive", inspect_snapshot)
    assert cli.main(["validate", str(source), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["checked_archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["source_authentication"] == report["snapshot_validation"] == "not_checked"
    assert report["restoration_ready"] is False
    assert_closed(opened, staged)
