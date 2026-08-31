import gzip
import hashlib
import importlib.util
import io
import socket
import stat
import tarfile
from http.client import HTTPException
from pathlib import Path
from types import SimpleNamespace
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/container/fetch-age.py"
AGE = b"offline synthetic age executable, never run\n"
LICENSE = b"offline synthetic license\n"
ARCHIVES = {
    "amd64": "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
    "arm64": "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4",
}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This test must remain offline")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def fetcher():
    spec = importlib.util.spec_from_file_location("container_age_fetch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def member(name, data=b"", kind=tarfile.REGTYPE, *, linkname="", size=None):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.size = len(data) if size is None else size
    return info, data


def default_members():
    return [
        member("age/", kind=tarfile.DIRTYPE),
        member("age/age", AGE),
        member("age/LICENSE", LICENSE),
    ]


def archive_bytes(members=None, *, format=tarfile.USTAR_FORMAT):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:", format=format) as archive:
        for info, data in default_members() if members is None else members:
            archive.addfile(info, io.BytesIO(data) if info.isreg() else None)
    return gzip.compress(output.getvalue(), mtime=0)


def supplied(fetcher, monkeypatch, data, architecture="amd64", *, match_digest=True):
    calls = []

    def download(value):
        calls.append(value)
        return data

    if match_digest:
        monkeypatch.setitem(fetcher.DIGESTS, architecture, hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(fetcher, "_download_archive", download)
    return calls


def rejected(fetcher, tmp_path, architecture="amd64"):
    destination = tmp_path / "out"
    with pytest.raises(fetcher.AgeFetchError) as caught:
        fetcher.fetch_age(architecture, destination)
    assert str(caught.value) == "Unable to install the pinned age release."
    assert not destination.exists() and not destination.is_symlink()
    assert list(tmp_path.iterdir()) == []


def test_official_version_commit_digests_and_resource_limits(fetcher):
    assert fetcher.VERSION == "1.3.2"
    assert fetcher.SOURCE_COMMIT == "b74dce4cdbe35b5e5f66c06d9612b72f89028758"
    assert fetcher.DIGESTS == ARCHIVES
    assert fetcher.MAX_ARCHIVE_BYTES == 32 * 1024 * 1024
    assert fetcher.MAX_TAR_BYTES == 64 * 1024 * 1024
    assert fetcher.MAX_MEMBER_BYTES == 16 * 1024 * 1024
    assert fetcher.MAX_LICENSE_BYTES == fetcher.READ_CHUNK_BYTES == 64 * 1024
    assert fetcher.MAX_MEMBERS == 32
    assert fetcher.SOCKET_TIMEOUT_SECONDS == 30 and fetcher.DOWNLOAD_BUDGET_SECONDS == 90


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_only_two_complete_files_are_published(fetcher, monkeypatch, tmp_path, architecture):
    members = default_members() + [
        member(name, b"auxiliary executable, not installed")
        for name in sorted(fetcher.REGULAR_MEMBERS - set(fetcher.SELECTED_MEMBERS))
    ]
    calls = supplied(fetcher, monkeypatch, archive_bytes(members), architecture)
    destination = tmp_path / "out"
    assert fetcher.fetch_age(architecture, destination) is None
    assert calls == [architecture]
    assert list(tmp_path.iterdir()) == [destination]
    assert {path.name for path in destination.iterdir()} == {"age", "LICENSE"}
    assert (destination / "age").read_bytes() == AGE
    assert (destination / "LICENSE").read_bytes() == LICENSE
    assert stat.S_IMODE((destination / "age").stat().st_mode) == 0o755
    assert stat.S_IMODE((destination / "LICENSE").stat().st_mode) == 0o644
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755


@pytest.mark.parametrize(
    "architecture",
    ["", "x86_64", "aarch64", "arm", "386", "ppc64le", "AMD64", "../amd64",
     "amd64?override=1", None, True, 123, b"amd64", ["amd64"]],
)
def test_unknown_architecture_has_no_network_or_output(
    fetcher, monkeypatch, tmp_path, architecture,
):
    calls = supplied(fetcher, monkeypatch, archive_bytes())
    rejected(fetcher, tmp_path, architecture)
    assert calls == []


def test_full_compressed_hash_is_checked_before_any_decompression(fetcher, monkeypatch, tmp_path):
    supplied(fetcher, monkeypatch, archive_bytes(), match_digest=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Checksum must be checked first")

    monkeypatch.setattr(fetcher.gzip, "GzipFile", forbidden)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("payload", [b"", b"not a gzip archive", b"PK\x03\x04not gzip"])
def test_invalid_archives_fail_without_output(fetcher, monkeypatch, tmp_path, payload):
    supplied(fetcher, monkeypatch, payload)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("mutation", ["truncated_gzip", "gzip_crc", "no_tar_end", "trailing_data"])
def test_malformed_streams_are_rejected(fetcher, monkeypatch, tmp_path, mutation):
    data = archive_bytes()
    unpacked = gzip.decompress(data)
    if mutation == "truncated_gzip":
        data = data[:-5]
    elif mutation == "gzip_crc":
        data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
    elif mutation == "no_tar_end":
        data = gzip.compress(unpacked[:2560], mtime=0)
    else:
        data = gzip.compress(unpacked + b"x" * 512, mtime=0)
    supplied(fetcher, monkeypatch, data)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("missing", ["age/age", "age/LICENSE", "both"])
def test_missing_required_member(fetcher, monkeypatch, tmp_path, missing):
    keep = {"age/"} if missing == "both" else {"age/", "age/age", "age/LICENSE"} - {missing}
    data = archive_bytes([entry for entry in default_members() if entry[0].name in keep])
    supplied(fetcher, monkeypatch, data)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("name", ["age/", "age/age", "age/LICENSE", "age/age-keygen"])
def test_duplicate_members_even_skipped_files_fail(fetcher, monkeypatch, tmp_path, name):
    extra = member(name, kind=tarfile.DIRTYPE) if name == "age/" else member(name, b"duplicate")
    members = default_members() + [extra]
    if name == "age/age-keygen":
        members.append(extra)
    supplied(fetcher, monkeypatch, archive_bytes(members))
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("name", ["age/age", "age/LICENSE", "age/age-keygen"])
@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE,
             tarfile.BLKTYPE, tarfile.DIRTYPE, tarfile.CONTTYPE],
)
def test_links_and_nonregular_targets_or_skipped_members_fail(
    fetcher, monkeypatch, tmp_path, name, kind,
):
    members = [entry for entry in default_members() if entry[0].name != name]
    linkname = "" if kind == tarfile.DIRTYPE else "bad-target"
    members.append(member(name, kind=kind, linkname=linkname))
    supplied(fetcher, monkeypatch, archive_bytes(members))
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize(
    "name", ["../age", "/age/age", "age//age", "age/../age", "./age/age", "age\\age",
             "age/other", "LICENSE", "age/age/child", "age/subdir/"],
)
def test_nonallowlisted_paths_never_materialize(fetcher, monkeypatch, tmp_path, name):
    supplied(fetcher, monkeypatch, archive_bytes(default_members() + [member(name, b"bad")]))
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("name", ["age/age", "age/LICENSE", "age/age-keygen"])
def test_empty_regular_members_rejected(fetcher, monkeypatch, tmp_path, name):
    members = [entry for entry in default_members() if entry[0].name != name]
    members.append(member(name))
    supplied(fetcher, monkeypatch, archive_bytes(members))
    rejected(fetcher, tmp_path)


def test_root_directory_must_be_empty_directory(fetcher, monkeypatch, tmp_path):
    supplied(fetcher, monkeypatch, archive_bytes(default_members()[1:] + [member("age", b"bad")]))
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("name", ["age/age", "age/LICENSE", "age/age-keygen"])
def test_oversized_declared_member_rejected_without_allocating_it(
    fetcher, monkeypatch, tmp_path, name,
):
    limit = fetcher.MAX_LICENSE_BYTES if name == "age/LICENSE" else fetcher.MAX_MEMBER_BYTES
    info, _ = member(name, size=limit + 1)
    data = gzip.compress(info.tobuf(format=tarfile.USTAR_FORMAT) + bytes(1024), mtime=0)
    supplied(fetcher, monkeypatch, data)
    rejected(fetcher, tmp_path)


def test_member_content_outside_archive_rejected(fetcher, monkeypatch, tmp_path):
    info, _ = member("age/age", size=4096)
    supplied(fetcher, monkeypatch, gzip.compress(info.tobuf() + bytes(1024), mtime=0))
    rejected(fetcher, tmp_path)


def test_nonzero_member_padding_rejected(fetcher, monkeypatch, tmp_path):
    unpacked = bytearray(gzip.decompress(archive_bytes()))
    unpacked[1024 + len(AGE)] = 1
    supplied(fetcher, monkeypatch, gzip.compress(unpacked, mtime=0))
    rejected(fetcher, tmp_path)


def test_pax_metadata_is_not_silently_accepted(fetcher, monkeypatch, tmp_path):
    members = default_members()
    members[1][0].pax_headers = {"comment": "unexpected extension"}
    supplied(fetcher, monkeypatch, archive_bytes(members, format=tarfile.PAX_FORMAT))
    rejected(fetcher, tmp_path)


def test_gnu_longname_extension_is_not_silently_accepted(fetcher, monkeypatch, tmp_path):
    members = default_members() + [member("age/" + "a" * 150, b"bad")]
    supplied(fetcher, monkeypatch, archive_bytes(members, format=tarfile.GNU_FORMAT))
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize(
    "kind", [tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_SPARSE],
)
def test_extensions_rejected_before_tarfile_can_follow_them(
    fetcher, monkeypatch, tmp_path, kind,
):
    info, _ = member("age/age", kind=kind, size=4096)
    data = gzip.compress(info.tobuf(format=tarfile.GNU_FORMAT) + bytes(5120), mtime=0)
    supplied(fetcher, monkeypatch, data)

    def forbidden(*args, **kwargs):
        raise AssertionError("Extended metadata must be rejected before opening TarFile")

    monkeypatch.setattr(fetcher.tarfile, "open", forbidden)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("boundary", ["compressed", "inflated", "members", "member", "license"])
def test_each_independent_size_limit_rejects_overflow(fetcher, monkeypatch, tmp_path, boundary):
    data = archive_bytes()
    supplied(fetcher, monkeypatch, data)
    if boundary == "compressed":
        monkeypatch.setattr(fetcher, "MAX_ARCHIVE_BYTES", len(data) - 1)
    elif boundary == "inflated":
        monkeypatch.setattr(fetcher, "MAX_TAR_BYTES", len(gzip.decompress(data)) - 1)
    elif boundary == "members":
        monkeypatch.setattr(fetcher, "MAX_MEMBERS", 2)
    elif boundary == "member":
        monkeypatch.setattr(fetcher, "MAX_MEMBER_BYTES", len(AGE) - 1)
    else:
        monkeypatch.setattr(fetcher, "MAX_LICENSE_BYTES", len(LICENSE) - 1)
    rejected(fetcher, tmp_path)


def test_exact_limits_are_inclusive(fetcher, monkeypatch, tmp_path):
    data = archive_bytes()
    supplied(fetcher, monkeypatch, data)
    monkeypatch.setattr(fetcher, "MAX_ARCHIVE_BYTES", len(data))
    monkeypatch.setattr(fetcher, "MAX_TAR_BYTES", len(gzip.decompress(data)))
    monkeypatch.setattr(fetcher, "MAX_MEMBERS", 3)
    monkeypatch.setattr(fetcher, "MAX_MEMBER_BYTES", len(AGE))
    monkeypatch.setattr(fetcher, "MAX_LICENSE_BYTES", len(LICENSE))
    fetcher.fetch_age("amd64", tmp_path / "out")
    assert (tmp_path / "out/age").read_bytes() == AGE


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling_symlink"])
def test_existing_destination_is_never_changed(fetcher, monkeypatch, tmp_path, kind):
    calls = supplied(fetcher, monkeypatch, archive_bytes())
    destination = tmp_path / "out"
    if kind == "file":
        destination.write_bytes(b"original")
    elif kind == "directory":
        destination.mkdir()
        (destination / "original").write_bytes(b"original")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.write_bytes(b"original")
        destination.symlink_to(target)
    before = sorted(path.name for path in tmp_path.iterdir())
    with pytest.raises(fetcher.AgeFetchError):
        fetcher.fetch_age("amd64", destination)
    assert calls == [] and sorted(path.name for path in tmp_path.iterdir()) == before
    if kind == "file":
        assert destination.read_bytes() == b"original"
    elif kind == "directory":
        assert (destination / "original").read_bytes() == b"original"
    else:
        assert destination.is_symlink() and destination.readlink() == tmp_path / "target"


def test_missing_destination_parent_is_not_created(fetcher, monkeypatch, tmp_path):
    calls = supplied(fetcher, monkeypatch, archive_bytes())
    with pytest.raises(fetcher.AgeFetchError):
        fetcher.fetch_age("amd64", tmp_path / "missing/out")
    assert calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["first_write", "second_write", "fsync", "chmod", "rename"])
def test_io_failure_cleans_staging_without_publishing(fetcher, monkeypatch, tmp_path, operation):
    supplied(fetcher, monkeypatch, archive_bytes())
    original = fetcher._write_file

    def broken(*args, **kwargs):
        raise OSError("SECRET path and error must not escape")

    def write(path, data, mode):
        if operation == "first_write" or path.name == "LICENSE":
            broken()
        original(path, data, mode)

    if operation in ("first_write", "second_write"):
        monkeypatch.setattr(fetcher, "_write_file", write)
    elif operation == "fsync":
        monkeypatch.setattr(fetcher.os, "fsync", broken)
    elif operation == "chmod":
        monkeypatch.setattr(fetcher.os, "fchmod", broken)
    else:
        monkeypatch.setattr(fetcher.Path, "rename", broken)
    rejected(fetcher, tmp_path)


def test_destination_rechecked_before_publication(fetcher, monkeypatch, tmp_path):
    supplied(fetcher, monkeypatch, archive_bytes())
    original = fetcher._write_file
    destination = tmp_path / "out"

    def write(path, data, mode):
        original(path, data, mode)
        if path.name == "LICENSE":
            destination.write_bytes(b"created by other owner")

    monkeypatch.setattr(fetcher, "_write_file", write)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher.fetch_age("amd64", destination)
    assert list(tmp_path.iterdir()) == [destination]
    assert destination.read_bytes() == b"created by other owner"


def test_publish_happens_once_only_after_both_complete_files(fetcher, monkeypatch, tmp_path):
    supplied(fetcher, monkeypatch, archive_bytes())
    destination = tmp_path / "out"
    rename = Path.rename
    calls = []

    def publish(staging, target):
        assert not destination.exists() and target == destination
        assert staging.parent == destination.parent and staging.name.startswith(".age-fetch-")
        assert {path.name for path in staging.iterdir()} == {"age", "LICENSE"}
        assert (staging / "age").read_bytes() == AGE
        assert (staging / "LICENSE").read_bytes() == LICENSE
        calls.append(target)
        return rename(staging, target)

    monkeypatch.setattr(fetcher.Path, "rename", publish)
    fetcher.fetch_age("amd64", destination)
    assert calls == [destination] and list(tmp_path.iterdir()) == [destination]


class ShortWriter:
    def __init__(self, target, result="short"):
        self.target = target
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.target.close()

    def write(self, block):
        assert 0 < len(block) <= 64 * 1024
        if self.result == "short":
            return self.target.write(block[:3])
        if self.result == "overreturn":
            return len(block) + 1
        return self.result

    def flush(self):
        return self.target.flush()

    def fileno(self):
        return self.target.fileno()


@pytest.mark.parametrize("result", ["short", None, True, 0, -1, "bad", "overreturn"])
def test_short_writes_are_completed_or_rejected_without_partial_output(
    fetcher, monkeypatch, tmp_path, result,
):
    supplied(fetcher, monkeypatch, archive_bytes())
    original = Path.open

    def open_file(path, mode="r", *args, **kwargs):
        target = original(path, mode, *args, **kwargs)
        return ShortWriter(target, result) if mode == "xb" else target

    monkeypatch.setattr(fetcher.Path, "open", open_file)
    if result == "short":
        fetcher.fetch_age("amd64", tmp_path / "out")
        assert (tmp_path / "out/age").read_bytes() == AGE
        assert (tmp_path / "out/LICENSE").read_bytes() == LICENSE
    else:
        rejected(fetcher, tmp_path)


def test_only_selected_members_are_read_and_no_extraction_api_is_used(
    fetcher, monkeypatch, tmp_path,
):
    data = archive_bytes(default_members() + [member("age/age-keygen", b"unused")])
    supplied(fetcher, monkeypatch, data)
    original = tarfile.TarFile.extractfile
    selected = []

    def read_member(archive, info):
        selected.append(info.name)
        return original(archive, info)

    def forbidden(*args, **kwargs):
        raise AssertionError("Never extract archive-provided filesystem paths")

    monkeypatch.setattr(fetcher.tarfile.TarFile, "extractfile", read_member)
    monkeypatch.setattr(fetcher.tarfile.TarFile, "extract", forbidden)
    monkeypatch.setattr(fetcher.tarfile.TarFile, "extractall", forbidden)
    fetcher.fetch_age("amd64", tmp_path / "out")
    assert selected == ["age/age", "age/LICENSE"]


class FakeResponse:
    def __init__(self, data=b"download", *, status=200, headers=None, url=None, chunk=3):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = {} if headers is None else headers
        self.url = url or "https://release-assets.githubusercontent.com/example?signature=public"
        self.chunk = chunk
        self.requests = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def geturl(self):
        return self.url

    def read1(self, size):
        self.requests.append(size)
        return self.data.read(min(size, self.chunk))


def fake_opener(fetcher, monkeypatch, response):
    calls = []

    def create(*handlers):
        def open_response(request, *, timeout):
            calls.append((handlers, request, timeout))
            return response

        return SimpleNamespace(open=open_response)

    monkeypatch.setattr(fetcher, "build_opener", create)
    return calls


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_download_maps_architecture_has_timeout_no_proxy_and_short_reads(
    fetcher, monkeypatch, architecture,
):
    response = FakeResponse(headers={"Content-Length": "8"})
    calls = fake_opener(fetcher, monkeypatch, response)
    assert fetcher._download_archive(architecture) == b"download"
    assert response.closed and len(calls) == 1
    handlers, request, timeout = calls[0]
    assert request.full_url == (
        "https://github.com/FiloSottile/age/releases/download/v1.3.2/"
        f"age-v1.3.2-linux-{architecture}.tar.gz"
    )
    assert request.get_method() == "GET" and timeout == 30
    assert any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers)
    redirect = next(handler for handler in handlers if isinstance(handler, HTTPRedirectHandler))
    assert redirect.max_repeats == 1 and redirect.max_redirections == 3
    assert all(0 < size <= 64 * 1024 for size in response.requests)


@pytest.mark.parametrize("length", ["0", "-1", "+8", " 8", "8.0", "8,8", "a", "９", "9" * 20])
def test_invalid_content_length_rejected_before_body(fetcher, monkeypatch, length):
    response = FakeResponse(headers={"Content-Length": length})
    fake_opener(fetcher, monkeypatch, response)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed and response.requests == []


@pytest.mark.parametrize("status", [201, 204, 301, 302, 400, 403, 404, 429, 500, True, "200"])
def test_non_200_response_is_not_downloaded(fetcher, monkeypatch, status):
    response = FakeResponse(status=status)
    fake_opener(fetcher, monkeypatch, response)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed and response.requests == []


@pytest.mark.parametrize("declared", [None, "9"])
def test_network_body_limit_is_enforced_with_or_without_length(fetcher, monkeypatch, declared):
    headers = {} if declared is None else {"Content-Length": declared}
    response = FakeResponse(b"oversized", headers=headers)
    fake_opener(fetcher, monkeypatch, response)
    monkeypatch.setattr(fetcher, "MAX_ARCHIVE_BYTES", 8)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed
    assert declared is None or response.requests == []


def test_download_exact_byte_limit_and_content_length_match(fetcher, monkeypatch):
    response = FakeResponse(headers={"Content-Length": "8"})
    fake_opener(fetcher, monkeypatch, response)
    monkeypatch.setattr(fetcher, "MAX_ARCHIVE_BYTES", 8)
    assert fetcher._download_archive("amd64") == b"download" and response.closed
    response = FakeResponse(headers={"Content-Length": "9"})
    fake_opener(fetcher, monkeypatch, response)
    monkeypatch.setattr(fetcher, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed


@pytest.mark.parametrize("times", [(0, 91), (0, 0, 91)])
def test_total_download_budget_checked_around_reads(fetcher, monkeypatch, times):
    clock = iter(times)
    monkeypatch.setattr(fetcher, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    response = FakeResponse()
    fake_opener(fetcher, monkeypatch, response)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed


@pytest.mark.parametrize("failure", [TimeoutError, OSError, EOFError, HTTPException])
def test_network_failure_has_safe_error_and_no_partial_destination(
    fetcher, monkeypatch, tmp_path, failure,
):
    def broken(*args, **kwargs):
        raise failure("SECRET network error")

    monkeypatch.setattr(fetcher, "_download_archive", broken)
    rejected(fetcher, tmp_path)


@pytest.mark.parametrize("block", [None, "text", bytearray(b"x"), True, b"oversized"])
def test_invalid_or_overreturning_reads_rejected(fetcher, block):
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._read_bounded(lambda size: block, 2)


@pytest.mark.parametrize(
    "url", ["http://github.com/file", "file:///tmp/age", "https://example.com/age",
            "https://github.com.evil.example/file", "https://user:secret@github.com/file",
            "https://github.com:444/file", "https://github.com:bad/file",
            "https://github.com/file#fragment", " https://github.com/file",
            "https://github.com/\nfile", "https://github.com/" + "a" * 8192],
)
def test_redirect_downgrade_other_host_or_ambiguous_url_is_rejected(fetcher, url):
    request = Request("https://github.com/FiloSottile/age")
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._ReleaseRedirect().redirect_request(request, None, 302, "Found", {}, url)


def test_official_https_release_redirect_is_allowed(fetcher):
    request = Request("https://github.com/FiloSottile/age")
    url = "https://release-assets.githubusercontent.com/asset?public-signature=1"
    result = fetcher._ReleaseRedirect().redirect_request(request, None, 302, "Found", {}, url)
    assert result.full_url == url and result.get_method() == "GET"


def test_final_response_host_is_checked_too(fetcher, monkeypatch):
    response = FakeResponse(url="http://example.com/unsafe")
    fake_opener(fetcher, monkeypatch, response)
    with pytest.raises(fetcher.AgeFetchError):
        fetcher._download_archive("amd64")
    assert response.closed and response.requests == []


@pytest.mark.parametrize("arguments", [[], ["amd64"], ["amd64", "out", "extra"]])
def test_cli_usage_is_fixed_safe_text(fetcher, capsys, arguments):
    assert fetcher.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == fetcher.USAGE + "\n"


def test_cli_failure_no_traceback_or_input_echo(fetcher, tmp_path, capsys):
    assert fetcher.main(["SECRET_UNKNOWN_ARCH", str(tmp_path / "SECRET_PATH")]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == fetcher.ERROR_MESSAGE + "\n"
    assert list(tmp_path.iterdir()) == []


def test_cli_success_is_silent(fetcher, monkeypatch, tmp_path, capsys):
    supplied(fetcher, monkeypatch, archive_bytes())
    assert fetcher.main(["amd64", str(tmp_path / "out")]) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert (tmp_path / "out/age").read_bytes() == AGE


def test_docker_stage_copies_only_age_and_complete_license():
    dockerfile = (ROOT / "Dockerfile").read_text()
    stage = dockerfile.split("FROM python-base AS age\n", 1)[1].split("\nFROM ", 1)[0]
    assert stage.splitlines() == [
        "ARG TARGETARCH",
        "COPY scripts/container/fetch-age.py /fetch-age.py",
        'RUN python /fetch-age.py "$TARGETARCH" /out',
    ]
    copies = [line for line in dockerfile.splitlines() if line.startswith("COPY --from=age ")]
    assert copies == [
        "COPY --from=age /out/age /usr/local/bin/age",
        "COPY --from=age /out/LICENSE /usr/share/licenses/age/LICENSE",
    ]
    assert "USER 10001:10001\n" in dockerfile
    assert 'ENTRYPOINT ["open-node-entrypoint"]\n' in dockerfile
    assert 'RUN python /fetch-lego.py "$TARGETARCH" /out\n' in dockerfile
