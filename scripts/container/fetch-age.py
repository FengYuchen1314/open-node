"""Install only age and its complete license from the pinned official release.

The destination must not exist; its parent must be a trusted, exclusive build
directory. Both files are prepared in a private sibling directory and published
with one same-filesystem rename. No partially prepared destination is published.
The download has a socket timeout and a total budget checked between reads; this
is not a mechanism for interrupting an indefinitely blocked operating-system DNS
lookup. No version, URL, digest, proxy, or TLS override is accepted by this CLI.
TLS verification uses Python's default trust context, including the system's
OpenSSL trust-store environment configuration; those CA settings are not reset.
"""

import gzip
import hashlib
import io
import os
import sys
import tarfile
import tempfile
import time
import zlib
from collections.abc import Callable, Sequence
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = "1.3.2"
SOURCE_COMMIT = "b74dce4cdbe35b5e5f66c06d9612b72f89028758"
DIGESTS = {
    "amd64": "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
    "arm64": "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4",
}
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_TAR_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_LICENSE_BYTES = 64 * 1024
MAX_MEMBERS = 32
READ_CHUNK_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 30
DOWNLOAD_BUDGET_SECONDS = 90
ERROR_MESSAGE = "Unable to install the pinned age release."
USAGE = "Usage: fetch-age.py {amd64|arm64} NEW_DESTINATION_DIRECTORY"
RELEASE_HOSTS = frozenset({
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
})
# v1.3.2 contains these auxiliary binaries, but none is installed by this script.
REGULAR_MEMBERS = frozenset({
    "age/age", "age/LICENSE", "age/age-keygen", "age/age-inspect",
    "age/age-plugin-pq", "age/age-plugin-tag", "age/age-plugin-tagpq",
    "age/age-plugin-batchpass",
})
SELECTED_MEMBERS = ("age/age", "age/LICENSE")


class AgeFetchError(ValueError):
    def __init__(self) -> None:
        super().__init__(ERROR_MESSAGE)


def _check(condition: bool) -> None:
    if not condition:
        raise AgeFetchError()


def _release_url(url: str) -> None:
    _check(type(url) is str and len(url) <= 8192)
    _check(not any(ord(character) <= 32 or ord(character) == 127 for character in url))
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise AgeFetchError() from None
    _check(
        parts.scheme == "https" and parts.hostname in RELEASE_HOSTS
        and port in (None, 443) and parts.username is None and parts.password is None
        and not parts.fragment
    )


class _ReleaseRedirect(HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _release_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_bounded(
    read: Callable[[int], bytes], limit: int, deadline: float | None = None,
) -> bytes:
    data = bytearray()
    while True:
        if deadline is not None:
            _check(time.monotonic() < deadline)
        block = read(min(READ_CHUNK_BYTES, limit - len(data) + 1))
        _check(type(block) is bytes)
        if deadline is not None:
            _check(time.monotonic() < deadline)
        _check(len(block) <= min(READ_CHUNK_BYTES, limit - len(data) + 1))
        _check(len(data) + len(block) <= limit)
        if not block:
            return bytes(data)
        data.extend(block)


def _download_archive(architecture: str) -> bytes:
    _check(type(architecture) is str and architecture in DIGESTS)
    url = (
        f"https://github.com/FiloSottile/age/releases/download/v{VERSION}/"
        f"age-v{VERSION}-linux-{architecture}.tar.gz"
    )
    _release_url(url)
    request = Request(url, headers={"User-Agent": "open-node-age-fetch/1", "Accept": "*/*"})
    opener = build_opener(ProxyHandler({}), _ReleaseRedirect())
    deadline = time.monotonic() + DOWNLOAD_BUDGET_SECONDS
    with opener.open(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
        _check(type(response.status) is int and response.status == 200)
        _release_url(response.geturl())
        length = response.headers.get("Content-Length")
        expected = None
        if length is not None:
            _check(type(length) is str and length.isascii() and length.isdecimal())
            _check(len(length) <= 10)
            expected = int(length)
            _check(0 < expected <= MAX_ARCHIVE_BYTES)
        # read1 makes a single underlying read, avoiding read(n)'s fill loop.
        data = _read_bounded(response.read1, MAX_ARCHIVE_BYTES, deadline)
        _check(expected is None or len(data) == expected)
        return data


def _preflight_members(unpacked: bytes) -> list[tarfile.TarInfo]:
    """Reject extensions before TarFile can follow PAX/GNU metadata chains."""
    _check(len(unpacked) % tarfile.BLOCKSIZE == 0)
    members = []
    seen = set()
    end = 0
    while end < len(unpacked):
        header = unpacked[end:end + tarfile.BLOCKSIZE]
        if not any(header):
            break
        _check(len(members) < MAX_MEMBERS)
        member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
        _check(member.name not in seen)
        seen.add(member.name)
        _check(not member.pax_headers and member.sparse is None and not member.linkname)
        if member.name == "age":
            _check(member.type == tarfile.DIRTYPE and member.size == 0)
        else:
            _check(member.name in REGULAR_MEMBERS)
            _check(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE))
            limit = MAX_LICENSE_BYTES if member.name == "age/LICENSE" else MAX_MEMBER_BYTES
            _check(0 < member.size <= limit)
        member.offset = end
        member.offset_data = end + tarfile.BLOCKSIZE
        content_end = member.offset_data + member.size
        end = member.offset_data + (
            (member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE * tarfile.BLOCKSIZE
        )
        _check(content_end <= end <= len(unpacked))
        _check(not any(unpacked[content_end:end]))
        members.append(member)
    _check(len(unpacked) - end >= 2 * tarfile.BLOCKSIZE and not any(unpacked[end:]))
    _check(set(SELECTED_MEMBERS) <= seen)
    return members


def _selected_files(data: bytes, architecture: str) -> dict[str, bytes]:
    _check(type(data) is bytes and 0 < len(data) <= MAX_ARCHIVE_BYTES)
    _check(hashlib.sha256(data).hexdigest() == DIGESTS[architecture])
    # Inflate only after the full compressed checksum passes, with an actual
    # uncompressed-byte limit that also bounds skipped auxiliary members.
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as compressed:
        unpacked = _read_bounded(compressed.read1, MAX_TAR_BYTES)
    members = _preflight_members(unpacked)
    selected = {}
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
        for member in members:
            if member.name in SELECTED_MEMBERS:
                source = archive.extractfile(member)
                _check(source is not None)
                with source:
                    content = _read_bounded(source.read, member.size)
                _check(len(content) == member.size)
                selected[member.name] = content
    _check(set(selected) == set(SELECTED_MEMBERS))
    return selected


def _write_file(path: Path, data: bytes, mode: int) -> None:
    with path.open("xb") as target:
        position = 0
        while position < len(data):
            block = data[position:position + READ_CHUNK_BYTES]
            written = target.write(block)
            _check(type(written) is int and 0 < written <= len(block))
            position += written
        target.flush()
        os.fsync(target.fileno())
        os.fchmod(target.fileno(), mode)


def fetch_age(architecture: str, destination: Path) -> None:
    """Publish two complete files, never overwrite an existing destination.

    The caller owns the destination's parent exclusively throughout this call;
    concurrent hostile directory replacement is outside this build-time API.
    """
    try:
        _check(type(architecture) is str and architecture in DIGESTS)
        _check(destination.name not in ("", ".", "..") and destination.parent.is_dir())
        _check(not os.path.lexists(destination))
        selected = _selected_files(_download_archive(architecture), architecture)
        with tempfile.TemporaryDirectory(prefix=".age-fetch-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            _write_file(staging / "age", selected["age/age"], 0o755)
            _write_file(staging / "LICENSE", selected["age/LICENSE"], 0o644)
            staging.chmod(0o755)
            _check(not os.path.lexists(destination))
            staging.rename(destination)
    except (OSError, ValueError, EOFError, HTTPException, tarfile.TarError, zlib.error):
        raise AgeFetchError() from None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        fetch_age(arguments[0], Path(arguments[1]))
    except AgeFetchError:
        print(ERROR_MESSAGE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
