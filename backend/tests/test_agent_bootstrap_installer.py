import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import urllib.error
import zipfile
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from open_node.resources import agent_installer as installer

VERSION = "0.3.0a0"
TICKET = "T" * 42 + "Q"
SECRET = "private-agent-token-which-must-never-be-printed"
CONTROL = "https://control.example.test/panel"
SERVER = "8a829d3b-65bc-471a-97a7-ec36c0e7477d"
DEFAULT_INSTALL_BASE = installer.INSTALL_BASE


@pytest.fixture(autouse=True)
def private_fixture_owner(monkeypatch, tmp_path):
    # Production is strictly root-only. Hosted backend CI is deliberately
    # unprivileged; fixtures own only their temporary files and never chown.
    # A hosted runner's /opt is not a production installation directory and
    # may be writable by other accounts. Exercise the same parent checks in
    # our own private tree instead of depending on host directory permissions.
    monkeypatch.setattr(installer, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(installer, "JOB_BASE", tmp_path / "jobs")
    install_base = tmp_path / "managed"
    install_base.mkdir(mode=0o700)
    monkeypatch.setattr(installer, "INSTALL_BASE", install_base)


def test_bootstrap_default_installation_base_remains_opt():
    assert DEFAULT_INSTALL_BASE == Path("/opt")


@pytest.mark.parametrize("mode", [0o775, 0o777])
def test_bootstrap_rejects_writable_installation_parent(mode):
    installer.INSTALL_BASE.chmod(mode)
    with pytest.raises(installer.BootstrapError, match="Unsafe directory component"):
        installer.prepare_job(control_url=CONTROL, ticket=TICKET, server_id=SERVER)
    assert not installer.JOB_BASE.exists()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest():
    tag = "agent-v" + VERSION

    def artifact(filename):
        return {
            "filename": filename,
            "path": f"{installer.API_PATH}/artifacts/{filename}",
            "sha256": "a" * 64,
            "bytes": 128,
        }

    return {
        "schema_version": 2,
        "agent": {
            "version": VERSION,
            "source_commit": "6ca84e2" + "0" * 33,
            "tag": tag,
            "wheel": artifact(f"open_node_agent-{VERSION}-py3-none-any.whl"),
            "bootstrap": artifact(f"open-node-agent-bootstrap-{VERSION}.tar.gz"),
            "build": artifact("BUILD.json"),
        },
        "xray": {
            "version": installer.XRAY_VERSION,
            "architecture": "x86_64",
            "archive": {
                "filename": "Xray-linux-64.zip",
                "path": installer.API_PATH + "/artifacts/Xray-linux-64.zip",
                "sha256": installer.XRAY_SHA256,
                "bytes": installer.XRAY_BYTES,
            },
        },
        "mihomo": {
            "version": installer.MIHOMO_VERSION,
            "assets": {
                platform_name: {
                    "filename": expected["filename"],
                    "path": installer.API_PATH + "/artifacts/" + expected["filename"],
                    "sha256": expected["sha256"],
                    "bytes": expected["bytes"],
                }
                for platform_name, expected in installer.MIHOMO_ASSETS.items()
            },
        },
        "license_required": False,
    }


def claim(**changes):
    return {
        "configuration": {
            "server_id": SERVER,
            "server_name": "Private bootstrap fixture",
            "control_url": CONTROL,
            "agent_token": SECRET,
            "transport": "auto",
            "expires_at": "2026-08-31T12:00:00+00:00",
            **changes,
        },
        "license_required": False,
    }


@pytest.fixture
def job():
    return installer.prepare_job(control_url=CONTROL, ticket=TICKET, server_id=SERVER)


class Response(io.BytesIO):
    def __init__(self, data=b"", *, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class Client:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def private_file(path, content):
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def bootstrap_tar(members=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, kind, data in members or [
            (name, tarfile.REGTYPE, b"# fixture only\n") for name in installer.BOOTSTRAP_FILES
        ]:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data) if kind in {tarfile.REGTYPE, tarfile.AREGTYPE} else 0
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                info.linkname = "/etc/shadow"
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def xray_zip(extra=None):
    output = io.BytesIO()
    binary = b"\x7fELF\x02\x01" + b"\0" * 12 + (62).to_bytes(2, "little") + b"fixture"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xray", binary)
        archive.writestr("LICENSE", b"fixture license")
        archive.writestr("geoip.dat", b"not installed")
        archive.writestr("geosite.dat", b"not installed")
        for name, data, mode in extra or []:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return output.getvalue()


def agent_wheel(version=VERSION, package="open-node-agent"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            f"open_node_agent-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {package}\nVersion: {version}\n",
        )
    return output.getvalue()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Example.COM/", "https://example.com"),
        ("https://example.com:443/panel/", "https://example.com/panel"),
        ("https://127.0.0.1:8443/prefix", "https://127.0.0.1:8443/prefix"),
        ("https://[::1]:9443/panel", "https://[::1]:9443/panel"),
    ],
)
def test_bootstrap_control_url_canonicalizes_safe_https(value, expected):
    assert installer.validate_control_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com", "https://admin:password@example.com", "https://@example.com",
        "https://example.com?token=" + SECRET, "https://example.com#fragment",
        "https://example.com:0", "https://example.com:65536", "https://example.com:bad",
        "https://example.com:", "https://[fe80::1%eth0]",
        "https://example.com/../panel", "https://example.com/%2e%2e/panel",
        "https://example.com/panel//nested", "https://example.com\\@other.test",
        "https://example.com/\nsecret", " https://example.com", "https://example.com/ ",
        "https://[::1", "https://example.com/%0d%0a", "https://example.com./panel",
        "https://bad_host.test", "https://example.com\x7f", None, {"token": SECRET},
    ],
)
def test_bootstrap_rejects_unsafe_urls_without_echoing_credentials(value):
    with pytest.raises(installer.BootstrapError) as caught:
        installer.validate_control_url(value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "ticket", ["", "x" * 42, "x" * 44, "x" * 42 + "+", "T" * 43, [TICKET]]
)
def test_bootstrap_ticket_shape_is_strict(ticket):
    with pytest.raises(installer.BootstrapError):
        installer.validate_ticket(ticket)


def test_bootstrap_requires_explicit_pinned_manifest_and_exact_build_identity():
    pinned = installer.validate_manifest(manifest())
    agent = pinned["agent"]
    build = {
        "source_commit": agent["source_commit"], "version": VERSION,
        "python": "3.11.2", "platform": "Linux-x86_64",
        "artifacts": {
            agent[key]["filename"]: agent[key]["sha256"] for key in ("wheel", "bootstrap")
        },
    }
    assert installer.validate_build(build, pinned) == build
    for changes in [
        {"source_commit": "b" * 40}, {"version": "0.3.0"}, {"artifacts": {}},
        {"python": ["3.11"]}, {"platform": SECRET + "\n"},
    ]:
        with pytest.raises(installer.BootstrapError):
            installer.validate_build({**build, **changes}, pinned)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(schema_version=True),
        lambda value: value.update(license_required=0),
        lambda value: value["agent"].update(tag="latest"),
        lambda value: value["agent"].update(source_commit="6ca84e2"),
        lambda value: value["agent"].update(version=[VERSION]),
        lambda value: value["agent"]["wheel"].update(path="/untrusted/agent.whl"),
        lambda value: value["agent"]["wheel"].update(sha256="A" * 64),
        lambda value: value["agent"]["bootstrap"].update(filename="../service.py"),
        lambda value: value["xray"].update(architecture="arm64"),
        lambda value: value["xray"]["archive"].update(sha256="b" * 64),
        lambda value: value["mihomo"]["assets"]["linux-amd64"].update(sha256="b" * 64),
        lambda value: value.update(extra="ignored is unsafe"),
    ],
)
def test_bootstrap_rejects_manifest_mismatches(change):
    value = manifest()
    change(value)
    with pytest.raises(installer.BootstrapError):
        installer.validate_manifest(value)


@pytest.mark.parametrize(
    "data",
    [b'{"token":"one","token":"two"}', b'{"secret":"' + SECRET.encode(),
     b'{"value":NaN}', b"\xff", b"[" * 1500, b" " * (installer.JSON_LIMIT + 1)],
)
def test_bootstrap_json_boundary_never_reflects_secret_payload(data):
    with pytest.raises(installer.BootstrapError) as caught:
        installer.parse_json(data)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"server_id": str(uuid4())}, {"control_url": "https://other.example.test"},
        {"agent_token": {"secret": SECRET}}, {"transport": ["http"]},
        {"transport": "tcp"}, {"expires_at": "2026-08-31T12:00:00"},
        {"expires_at": SECRET}, {"server_name": "unsafe\n"},
    ],
)
def test_bootstrap_redeemed_configuration_is_strict_and_redacted(changes):
    with pytest.raises(installer.BootstrapError) as caught:
        installer.validate_claim(claim(**changes), server_id=SERVER, control_url=CONTROL)
    assert SECRET not in str(caught.value)


def test_bootstrap_job_persists_original_nonce_without_raw_ticket(job):
    saved = installer.read_owned(job.directory / "request.json")
    assert TICKET.encode() not in saved
    assert job.nonce.encode() in saved
    assert json.loads(saved)["ticket_sha256"] == digest(TICKET.encode())
    repeated = installer.prepare_job(control_url=CONTROL, ticket=TICKET, server_id=SERVER)
    assert repeated.nonce == job.nonce
    assert repeated.directory == job.directory
    assert stat.S_IMODE(job.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((job.directory / "request.json").stat().st_mode) == 0o600
    assert job.nonce not in repr(job)
    assert job.root == installer.INSTALL_BASE / "open-node-agent-8a829d3b65bc"
    assert job.unit == "open-node-agent-8a829d3b65bc.service"


def test_bootstrap_existing_job_cannot_change_control_or_ca(job):
    original = (job.directory / "request.json").read_bytes()
    for changes in [{"control_url": "https://other.example.test"}, {"ca_data": b"CA"}]:
        with pytest.raises(installer.BootstrapError):
            installer.prepare_job(
                **{"control_url": CONTROL, "ticket": TICKET, "server_id": SERVER, **changes}
            )
        assert (job.directory / "request.json").read_bytes() == original


def test_bootstrap_lost_claim_response_retries_the_same_persisted_nonce(job, monkeypatch):
    monkeypatch.setattr(installer.time, "sleep", lambda delay: None)
    client = Client(
        urllib.error.URLError("lost-response-" + SECRET), Response(json.dumps(claim()).encode())
    )
    result = installer.redeem_claim(job, TICKET, client)
    assert result["configuration"]["agent_token"] == SECRET
    assert len(client.requests) == 2
    for request in client.requests:
        assert request.full_url == CONTROL + installer.API_PATH + "/redeem"
        assert TICKET not in request.full_url
        assert SECRET not in request.full_url
        assert "Authorization" not in request.headers
        assert json.loads(request.data) == {"ticket": TICKET, "claim_nonce": job.nonce}
    assert stat.S_IMODE((job.directory / "claim.json").stat().st_mode) == 0o600
    assert not (job.directory / "success.json").exists()


def test_bootstrap_persisted_claim_can_resume_after_claim_retry_deadline(job):
    first = claim(expires_at="2000-01-01T00:00:00+00:00")
    installer.write_new(job.directory / "claim.json", installer.json_bytes(first))
    client = Client()
    assert installer.redeem_claim(job, TICKET, client) == first
    assert client.requests == []


def test_bootstrap_control_requests_never_follow_redirect_or_echo_http_body():
    body = io.BytesIO(SECRET.encode())
    client = Client(urllib.error.HTTPError(
        CONTROL, 307, "untrusted", {"Location": "https://attacker.test/"}, body
    ))
    with pytest.raises(installer.BootstrapError) as caught:
        installer.request_json(client, CONTROL + installer.API_PATH + "/redeem",
                               payload={"ticket": TICKET, "claim_nonce": "n" * 43})
    assert SECRET not in str(caught.value)
    assert len(client.requests) == 1
    assert body.closed


@pytest.mark.parametrize("mode", [0o644, 0o620, 0o660, 0o4600])
def test_bootstrap_rejects_overbroad_or_special_input_permissions(job, mode):
    target = job.directory / "request.json"
    target.chmod(mode)
    with pytest.raises(installer.BootstrapError):
        installer.read_owned(target)


def test_bootstrap_rejects_hardlinks_symlinks_and_linked_parents(job, tmp_path):
    request = job.directory / "request.json"
    hardlink = tmp_path / "hardlink"
    os.link(request, hardlink)
    with pytest.raises(installer.BootstrapError):
        installer.read_owned(request)
    hardlink.unlink()
    link = job.directory / "linked.json"
    link.symlink_to(request)
    with pytest.raises(installer.BootstrapError):
        installer.read_owned(link)
    directory_link = tmp_path / "linked-directory"
    directory_link.symlink_to(job.directory, target_is_directory=True)
    with pytest.raises(installer.BootstrapError):
        installer.read_owned(directory_link / "request.json")
    with pytest.raises(installer.BootstrapError):
        installer.write_new(link, b"must not replace")
    assert link.is_symlink()


def test_bootstrap_rejects_unsafe_file_owner(job, monkeypatch):
    original = installer.os.fstat

    def unsafe(fd):
        info = original(fd)
        return SimpleNamespace(
            st_uid=installer.ROOT_UID + 1, st_mode=info.st_mode,
            st_nlink=info.st_nlink, st_size=info.st_size,
        )

    monkeypatch.setattr(installer.os, "fstat", unsafe)
    with pytest.raises(installer.BootstrapError):
        installer.read_owned(job.directory / "request.json")


def test_bootstrap_private_directory_cannot_be_widened_or_replaced(job):
    job.directory.chmod(0o750)
    with pytest.raises(installer.BootstrapError):
        installer.prepare_job(control_url=CONTROL, ticket=TICKET, server_id=SERVER)


@pytest.mark.parametrize("path", ["/tmp/test", "/opt", "/opt/open-node", "/opt/a/../b", "/"])
def test_bootstrap_test_override_does_not_allow_arbitrary_paths(path):
    with pytest.raises(installer.BootstrapError):
        installer.prepare_job(
            control_url=CONTROL, ticket=TICKET, server_id=SERVER, test_directory=Path(path)
        )


def test_bootstrap_download_uses_only_panel_origin_hash_and_private_cache(job):
    data = b"verified fixture artifact"
    artifact = {
        **manifest()["agent"]["wheel"], "sha256": digest(data), "bytes": len(data)
    }
    client = Client(Response(data, headers={
        "Content-Length": str(len(data)), "X-Content-SHA256": digest(data),
    }))
    result = installer.download_artifact(client, CONTROL, artifact, job.directory, limit=1024)
    assert result.read_bytes() == data
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    assert result.stat().st_nlink == 1
    assert len(client.requests) == 1
    assert client.requests[0].full_url == CONTROL + artifact["path"]
    assert installer.download_artifact(
        Client(), CONTROL, artifact, job.directory, limit=1024
    ) == result
    result.write_bytes(b"tampered")
    with pytest.raises(installer.BootstrapError, match="SHA-256"):
        installer.download_artifact(Client(), CONTROL, artifact, job.directory, limit=1024)
    assert result.read_bytes() == b"tampered"


@pytest.mark.parametrize(
    ("data", "headers", "sha256", "limit"),
    [
        (b"wrong", {}, digest(b"expected"), 1024),
        (b"oversized", {}, digest(b"oversized"), 3),
        (b"small", {"Content-Length": "9999"}, digest(b"small"), 1024),
        (b"small", {"Content-Length": "7"}, digest(b"small"), 1024),
        (b"small", {"Content-Encoding": "gzip"}, digest(b"small"), 1024),
    ],
)
def test_bootstrap_bad_download_never_publishes_an_artifact(job, data, headers, sha256, limit):
    artifact = {
        **manifest()["agent"]["wheel"], "sha256": sha256, "bytes": len(data)
    }
    headers = {
        "Content-Length": str(len(data)), "X-Content-SHA256": sha256, **headers,
    }
    client = Client(Response(data, headers=headers))
    with pytest.raises(installer.BootstrapError):
        installer.download_artifact(client, CONTROL, artifact, job.directory, limit=limit)
    assert not (job.directory / artifact["filename"]).exists()
    assert not list(job.directory.glob(".download-*"))


def test_bootstrap_download_rejects_external_redirect_before_contact(job):
    artifact = manifest()["agent"]["wheel"]
    client = Client(urllib.error.HTTPError(
        CONTROL + artifact["path"], 302, "redirect", {"Location": "https://evil.test/"}, None
    ))
    with pytest.raises(installer.BootstrapError):
        installer.download_artifact(client, CONTROL, artifact, job.directory, limit=1024)
    assert len(client.requests) == 1


def test_bootstrap_archive_contains_only_exact_regular_top_level_files(job):
    source = private_file(job.directory / "source.tar.gz", bootstrap_tar())
    result = installer.unpack_bootstrap(source, job.directory / "bootstrap")
    assert {item.name for item in result.iterdir()} == installer.BOOTSTRAP_FILES
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in result.iterdir())
    assert installer.unpack_bootstrap(source, result) == result


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../service.py", tarfile.REGTYPE), ("prefix/service.py", tarfile.REGTYPE),
        ("/etc/passwd", tarfile.REGTYPE), ("extra.py", tarfile.REGTYPE),
        ("service.py", tarfile.SYMTYPE), ("service.py", tarfile.LNKTYPE),
        ("service.py", tarfile.DIRTYPE), ("service.py", tarfile.FIFOTYPE),
    ],
)
def test_bootstrap_archive_rejects_links_paths_and_special_members(job, name, kind):
    source = private_file(job.directory / "unsafe.tar.gz", bootstrap_tar([(name, kind, b"x")]))
    target = job.directory / "bootstrap"
    with pytest.raises(installer.BootstrapError):
        installer.unpack_bootstrap(source, target)
    assert not target.exists()


def test_bootstrap_archive_rejects_duplicate_and_missing_members(job):
    for index, members in enumerate([
        [("service.py", tarfile.REGTYPE, b"x"), ("service.py", tarfile.REGTYPE, b"y")],
        [("service.py", tarfile.REGTYPE, b"x")],
    ]):
        source = private_file(job.directory / f"bad-{index}.tar.gz", bootstrap_tar(members))
        with pytest.raises(installer.BootstrapError):
            installer.unpack_bootstrap(source, job.directory / f"bootstrap-{index}")


def test_bootstrap_xray_archive_extracts_only_binary_and_license(job):
    source = private_file(job.directory / "xray.zip", xray_zip())
    target = installer.unpack_xray(source, job.directory / "xray")
    assert {path.name for path in target.iterdir()} == {"xray", "LICENSE"}
    assert stat.S_IMODE((target / "xray").stat().st_mode) == 0o700
    assert not (target / "geoip.dat").exists()


def test_bootstrap_mihomo_archive_is_hash_size_and_architecture_bound(job, monkeypatch):
    binary = bytearray(128)
    binary[:6] = b"\x7fELF\x02\x01"
    binary[18:20] = (62).to_bytes(2, "little")
    compressed = gzip.compress(bytes(binary), mtime=0)
    expected = {
        "filename": "fixture-mihomo.gz",
        "sha256": digest(compressed),
        "bytes": len(compressed),
        "machine": 62,
    }
    monkeypatch.setattr(installer, "MIHOMO_ASSETS", {"linux-amd64": expected})
    source = private_file(job.directory / expected["filename"], compressed)
    target = installer.unpack_mihomo(source, job.directory / "mihomo", "linux-amd64")
    assert (target / "mihomo").read_bytes() == bytes(binary)
    assert stat.S_IMODE((target / "mihomo").stat().st_mode) == 0o700
    source = private_file(job.directory / "bad-mihomo.gz", compressed[:-1] + b"x")
    with pytest.raises(installer.BootstrapError, match="release pin"):
        installer.unpack_mihomo(source, job.directory / "bad", "linux-amd64")


@pytest.mark.parametrize(
    ("name", "mode"),
    [("../secret", stat.S_IFREG), ("nested/xray", stat.S_IFREG),
     ("README.md", stat.S_IFLNK), ("setup.sh", stat.S_IFREG)],
)
def test_bootstrap_xray_archive_rejects_unexpected_or_link_members(job, name, mode):
    source = private_file(job.directory / "xray.zip", xray_zip([(name, b"x", mode)]))
    with pytest.raises(installer.BootstrapError):
        installer.unpack_xray(source, job.directory / "xray")
    assert not (job.directory / "xray").exists()


def test_bootstrap_wheel_validates_name_version_and_digest(job):
    pinned = manifest()
    data = agent_wheel()
    pinned["agent"]["wheel"]["sha256"] = digest(data)
    source = private_file(job.directory / pinned["agent"]["wheel"]["filename"], data)
    installer.validate_wheel(source, pinned)
    for data in [agent_wheel(version="0.4.0"), agent_wheel(package="not-open-node")]:
        source.write_bytes(data)
        pinned["agent"]["wheel"]["sha256"] = digest(data)
        with pytest.raises(installer.BootstrapError):
            installer.validate_wheel(source, pinned)


def test_bootstrap_prepares_managed_loopback_only_configuration_and_preserves_it(job):
    source, xray, mihomo = installer.prepare_configuration(job, claim())
    agent_data = json.loads(source.read_bytes())
    xray_data = json.loads(xray.read_bytes())
    assert agent_data["runtime_mode"] == "managed"
    assert agent_data["allow_xray_takeover"] is False
    assert agent_data["token"] == SECRET
    assert "lifecycle_socket" not in agent_data
    assert "nginx_binary" not in agent_data
    assert "certificate_http_address" not in agent_data
    assert xray_data["inbounds"] == []
    assert xray_data["api"]["listen"].startswith("127.0.0.1:")
    assert xray_data["api"]["services"] == ["StatsService"]
    assert json.loads(mihomo.read_bytes()) == installer.mihomo_configuration_data()
    assert json.loads(mihomo.read_bytes())["listeners"] == []
    assert installer.prepare_configuration(job, claim()) == (source, xray, mihomo)
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    changed = {**agent_data, "allow_xray_takeover": True}
    source.write_bytes(installer.json_bytes(changed))
    with pytest.raises(installer.BootstrapError):
        installer.prepare_configuration(job, claim())
    assert json.loads(source.read_bytes())["allow_xray_takeover"] is True


def test_bootstrap_existing_root_is_never_adopted(job, tmp_path, monkeypatch):
    root = tmp_path / "already-installed"
    root.mkdir()
    (root / "user-data").write_text("preserve")
    monkeypatch.setattr(
        installer, "run_command", lambda *args, **kwargs: pytest.fail("host command")
    )
    with pytest.raises(installer.BootstrapError, match="already exists"):
        installer.require_fresh_resources(replace(job, root=root))
    assert (root / "user-data").read_text() == "preserve"


def test_bootstrap_existing_account_is_never_adopted(job, tmp_path, monkeypatch):
    import pwd

    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=12345))
    monkeypatch.setattr(
        installer, "run_command", lambda *args, **kwargs: pytest.fail("host command")
    )
    with pytest.raises(installer.BootstrapError, match="account or group"):
        installer.require_fresh_resources(replace(job, root=tmp_path / "absent"))


def test_bootstrap_existing_loaded_unit_is_never_adopted(job, tmp_path, monkeypatch):
    import grp
    import pwd

    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(pwd, "getpwnam", missing)
    monkeypatch.setattr(grp, "getgrnam", missing)
    monkeypatch.setattr(installer, "run_command", lambda *args, **kwargs: b"FragmentPath=/owned\n")
    with pytest.raises(installer.BootstrapError, match="already claimed"):
        installer.require_fresh_resources(replace(job, root=tmp_path / "absent"))


def test_bootstrap_dependencies_require_explicit_opt_in_and_verified_platform(monkeypatch):
    commands, checks = [], []
    monkeypatch.setattr(installer, "dependencies_missing", lambda: True)
    monkeypatch.setattr(installer, "run_command", lambda args, **kwargs: commands.append(args))
    with pytest.raises(installer.BootstrapError, match="install-dependencies"):
        installer.ensure_dependencies()
    assert not commands
    states = iter([True, False])
    monkeypatch.setattr(installer, "dependencies_missing", lambda: next(states))
    monkeypatch.setattr(installer, "check_platform", lambda: checks.append(True))
    installer.ensure_dependencies(install=True)
    assert checks == [True]
    assert commands == [
        ["apt-get", "update"],
        [
            "apt-get", "install", "--yes", "--no-install-recommends",
            "python3-venv", "ca-certificates",
        ],
    ]


def test_bootstrap_dependencies_never_install_on_an_unverified_host(monkeypatch):
    monkeypatch.setattr(installer, "dependencies_missing", lambda: True)

    def rejected():
        raise installer.BootstrapError("Unsupported distribution")

    monkeypatch.setattr(installer, "check_platform", rejected)
    monkeypatch.setattr(
        installer, "run_command", lambda *args, **kwargs: pytest.fail("apt must not run")
    )
    with pytest.raises(installer.BootstrapError, match="Unsupported"):
        installer.ensure_dependencies(install=True)


def test_bootstrap_installer_arguments_never_include_long_lived_token(job, tmp_path, monkeypatch):
    job = replace(job, root=tmp_path / "installed")
    pinned, calls = manifest(), []
    artifacts = {
        "manifest": pinned, "service": job.directory / "bootstrap/service.py",
        "wheel": job.directory / pinned["agent"]["wheel"]["filename"],
        "xray": job.directory / "xray/xray",
        "mihomo": job.directory / "mihomo/mihomo",
    }
    monkeypatch.setattr(installer, "require_fresh_resources", lambda value: None)

    def simulated_host_install(arguments, **kwargs):
        calls.append(list(map(str, arguments)))
        job.root.mkdir(mode=0o700)
        private_file(job.root / "installation.json", installer.json_bytes({
            "root": str(job.root), "unit": job.unit, "user": job.unit.removesuffix(".service"),
            "uid": 12345, "gid": 12345, "status": "installed", "current": "release",
            "pending": None, "staging": None, "network_diagnostics": False,
            "installation_id": "fixture", "releases": {"release": {
                "version": VERSION, "sha256": pinned["agent"]["wheel"]["sha256"],
            }},
        }))
        return b"{}"

    monkeypatch.setattr(installer, "run_command", simulated_host_install)
    installer.install_agent(job, claim(), artifacts)
    assert len(calls) == 1
    arguments = calls[0]
    assert SECRET not in " ".join(arguments)
    assert TICKET not in " ".join(arguments)
    assert "--network-diagnostics" not in arguments
    assert "enable-remote" not in arguments
    assert "--config" in arguments and "--xray-config" in arguments
    assert "--mihomo-config" in arguments and "--mihomo" in arguments
    assert arguments[arguments.index("--root") + 1] == str(job.root)
    assert json.loads((job.directory / "success.json").read_bytes())["version"] == VERSION


def test_bootstrap_claim_and_zero_exit_without_installation_are_not_success(job, monkeypatch):
    monkeypatch.setattr(installer, "require_fresh_resources", lambda value: None)
    monkeypatch.setattr(installer, "run_command", lambda *args, **kwargs: b"{}")
    artifacts = {
        "manifest": manifest(), "service": job.directory / "service.py",
        "wheel": job.directory / "agent.whl", "xray": job.directory / "xray",
        "mihomo": job.directory / "mihomo",
    }
    with pytest.raises(installer.BootstrapError):
        installer.install_agent(job, claim(), artifacts)
    assert not (job.directory / "success.json").exists()
    assert (job.directory / "agent-input.json").exists()


def test_bootstrap_panel_client_uses_only_explicit_or_debian_trust(monkeypatch):
    calls = []

    class Context:
        def __init__(self, protocol):
            calls.append(("private", protocol))

        def load_verify_locations(self, **kwargs):
            calls.append(("ca", kwargs))

    monkeypatch.setattr(installer.ssl, "SSLContext", Context)
    monkeypatch.setattr(
        installer.ssl, "create_default_context",
        lambda **kwargs: calls.append(("system", kwargs)) or object(),
    )
    monkeypatch.setattr(installer.urllib.request, "HTTPSHandler", lambda **kwargs: object())
    monkeypatch.setattr(installer.urllib.request, "build_opener", lambda *args: object())
    monkeypatch.setenv("SSL_CERT_FILE", "/untrusted/env-ca.pem")
    installer.make_client(ca_data=b"PRIVATE FIXTURE CA")
    installer.make_client()
    assert ("ca", {"cadata": "PRIVATE FIXTURE CA"}) in calls
    assert ("system", {"cafile": str(installer.SYSTEM_CA)}) in calls
    assert "/untrusted/env-ca.pem" not in str(calls)


def test_bootstrap_main_prepares_before_claim_and_does_not_leak_failures(
    tmp_path, monkeypatch, capsys
):
    order, contexts = [], []
    ca_path = private_file(tmp_path / "control-ca.pem", b"fixture-private-ca")
    monkeypatch.setenv("OPEN_NODE_AGENT_CA_FILE", str(ca_path))
    monkeypatch.setattr(installer, "check_platform", lambda: None)
    monkeypatch.setattr(installer, "ensure_dependencies", lambda **kwargs: None)
    monkeypatch.setattr(installer, "job_lock", lambda value: nullcontext())
    monkeypatch.setattr(installer, "require_fresh_resources", lambda value: None)
    monkeypatch.setattr(installer, "make_client", lambda **kwargs: contexts.append(kwargs))

    def prepare(*args):
        order.append("prepare")
        return {"manifest": manifest()}

    def redeem(*args):
        order.append("claim")
        return claim()

    def install(*args):
        order.append("install")
        raise ValueError("upstream diagnostic includes " + SECRET)

    monkeypatch.setattr(installer, "prepare_artifacts", prepare)
    monkeypatch.setattr(installer, "redeem_claim", redeem)
    monkeypatch.setattr(installer, "install_agent", install)
    assert installer.main([
        "--control-url", CONTROL, "--ticket", TICKET, "--server-id", SERVER
    ]) == 1
    captured = capsys.readouterr()
    assert order == ["prepare", "claim", "install"]
    assert contexts == [{"ca_data": b"fixture-private-ca"}]
    assert SECRET not in captured.out + captured.err
    assert TICKET not in captured.out + captured.err
    assert "installed and ready" not in captured.out
    assert "retained" in captured.err


def test_bootstrap_cli_errors_do_not_echo_pasted_credentials(capsys):
    assert installer.main(["--unrecognized", SECRET]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.err + captured.out


def test_bootstrap_subprocess_environment_is_minimal_and_has_no_credentials(monkeypatch):
    monkeypatch.setenv("OPEN_NODE_AGENT_CA_FILE", "/private/control-ca.pem")
    monkeypatch.setenv("OPEN_NODE_AGENT_TOKEN", SECRET)
    monkeypatch.setenv("PYTHONPATH", "/untrusted/modules")
    result = installer.command_environment()
    assert SECRET not in str(result)
    assert "PYTHONPATH" not in result
    assert "OPEN_NODE_AGENT_CA_FILE" not in result
    assert "SSL_CERT_FILE" not in result
