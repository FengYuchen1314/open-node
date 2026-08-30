#!/usr/bin/env python3
"""Install a new, isolated Agent on Debian 12 using a short-lived panel ticket.

This file deliberately imports only Python's standard library. It is served as a
download by the control plane; it must also work outside the backend package.
Existing deployments are never adopted or upgraded by this entry point.
"""

from __future__ import annotations

import argparse
import contextlib
import email.parser
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
import platform
import re
import secrets
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

ROOT_UID = 0
API_PATH = "/api/v1/agents/bootstrap"
RELEASE_BASE = "https://github.com/FengYuchen1314/open-node/releases/download"
XRAY_VERSION = "v26.3.27"
XRAY_URL = (
    "https://github.com/XTLS/Xray-core/releases/download/" + XRAY_VERSION + "/Xray-linux-64.zip"
)
XRAY_SHA256 = "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae"
ASSET_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
BOOTSTRAP_FILES = {
    "service.py", "lifecycle_protocol.py", "lifecycle_host.py", "lifecycle_report.py", "LICENSE"
}
JSON_LIMIT = 64 * 1024
WHEEL_LIMIT = 32 * 1024 * 1024
BOOTSTRAP_LIMIT = 8 * 1024 * 1024
XRAY_LIMIT = 128 * 1024 * 1024
CA_LIMIT = 1024 * 1024
JOB_BASE = Path("/var/lib/open-node-agent-bootstrap")
SYSTEM_CA = Path("/etc/ssl/certs/ca-certificates.crt")
TOKEN_PATTERN = r"[A-Za-z0-9_-]{43}"
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"


class BootstrapError(RuntimeError):
    """A safe, deliberately non-secret diagnostic suitable for the terminal."""


class RetryableRequest(BootstrapError):
    pass


def require(condition, message):
    if not condition:
        raise BootstrapError(message)


def string(value, *, minimum=1, maximum=2048):
    return (
        isinstance(value, str)
        and minimum <= len(value) <= maximum
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names), "Invalid response fields")
    return value


def parse_json(data, *, limit=JSON_LIMIT):
    require(isinstance(data, bytes) and len(data) <= limit, "JSON response exceeds its limit")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON response field")
            result[key] = value
        return result

    def constant(_value):
        raise BootstrapError("Non-finite JSON value")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError):
        raise BootstrapError("Invalid JSON response") from None


def validate_control_url(value):
    require(
        string(value)
        and value.isascii()
        and value == value.strip()
        and not any(char in value for char in "\\?#"),
        "Control URL must be an explicit HTTPS root without credentials or query parameters",
    )
    try:
        parts = urlsplit(value)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise BootstrapError("Invalid control URL or port") from None
    require(
        parts.scheme == "https"
        and hostname
        and "%" not in hostname
        and not parts.netloc.endswith(":")
        and parts.username is None
        and parts.password is None
        and (port is None or 1 <= port <= 65535),
        "Control URL requires HTTPS and a valid host and port",
    )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        require(
            len(hostname) <= 253
            and all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in hostname.split(".")
            ),
            "Invalid control host",
        )
        hostname = hostname.lower()
    else:
        hostname = f"[{address.compressed}]" if address.version == 6 else str(address)
    path = parts.path.rstrip("/")
    require(
        re.fullmatch(r"[/a-zA-Z0-9._~!$&'()*+,;=:@-]*", path)
        and "//" not in path
        and not any(piece in {".", ".."} for piece in path.split("/")),
        "Control URL path must be canonical and cannot contain encoded or traversal segments",
    )
    authority = hostname + (f":{port}" if port not in {None, 443} else "")
    return urlunsplit(("https", authority, path, "", ""))


def validate_server_id(value):
    try:
        require(isinstance(value, str), "Invalid server identity")
        result = UUID(value)
    except (ValueError, AttributeError):
        raise BootstrapError("Invalid server identity") from None
    require(str(result) == value.lower(), "Server identity must be a canonical UUID")
    return str(result)


def validate_ticket(value):
    require(canonical_secret(value), "Invalid bootstrap ticket")
    return value


def canonical_secret(value):
    if not isinstance(value, str) or not re.fullmatch(TOKEN_PATTERN, value):
        return False
    return urlsafe_b64encode(urlsafe_b64decode(value + "=")).decode().rstrip("=") == value


def validate_asset(value, *, filename, tag):
    fields(value, {"filename", "url", "sha256"})
    require(
        value["filename"] == filename
        and value["url"] == f"{RELEASE_BASE}/{tag}/{filename}"
        and isinstance(value["sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", value["sha256"]),
        "Release artifact identity or SHA-256 is invalid",
    )
    return value


def validate_manifest(value):
    fields(value, {"schema_version", "agent", "xray", "license_required"})
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["license_required"] is False,
        "Unsupported bootstrap manifest",
    )
    agent = fields(
        value["agent"], {"version", "source_commit", "tag", "wheel", "bootstrap", "build"}
    )
    require(
        isinstance(agent["version"], str)
        and re.fullmatch(VERSION_PATTERN, agent["version"])
        and isinstance(agent["source_commit"], str)
        and re.fullmatch(r"[a-f0-9]{40}", agent["source_commit"])
        and agent["tag"] == "agent-v" + agent["version"],
        "Agent release must have an explicit version, tag, and source commit",
    )
    validate_asset(
        agent["wheel"],
        filename=f"open_node_agent-{agent['version']}-py3-none-any.whl",
        tag=agent["tag"],
    )
    validate_asset(
        agent["bootstrap"],
        filename=f"open-node-agent-bootstrap-{agent['version']}.tar.gz",
        tag=agent["tag"],
    )
    validate_asset(agent["build"], filename="BUILD.json", tag=agent["tag"])
    xray = fields(value["xray"], {"version", "architecture", "archive"})
    archive = fields(xray["archive"], {"filename", "url", "sha256"})
    require(
        xray["version"] == XRAY_VERSION
        and xray["architecture"] == "x86_64"
        and archive == {
            "filename": "Xray-linux-64.zip", "url": XRAY_URL, "sha256": XRAY_SHA256
        },
        "Xray release does not match the supported pinned artifact",
    )
    return value


def validate_build(value, manifest):
    fields(value, {"source_commit", "version", "python", "platform", "artifacts"})
    agent = manifest["agent"]
    require(
        value["source_commit"] == agent["source_commit"]
        and value["version"] == agent["version"]
        and string(value["python"], maximum=512)
        and string(value["platform"], maximum=512)
        and value["artifacts"] == {
            agent[key]["filename"]: agent[key]["sha256"] for key in ("wheel", "bootstrap")
        },
        "BUILD.json does not match the pinned release manifest",
    )
    return value


def validate_claim(value, *, server_id, control_url):
    fields(value, {"configuration", "license_required"})
    require(value["license_required"] is False, "Unexpected license requirement")
    config = fields(
        value["configuration"],
        {"server_id", "server_name", "control_url", "agent_token", "transport", "expires_at"},
    )
    require(
        config["server_id"] == server_id
        and validate_control_url(config["control_url"]) == control_url
        and string(config["server_name"], maximum=255)
        and isinstance(config["agent_token"], str)
        and re.fullmatch(r"[A-Za-z0-9_.~-]{16,2048}", config["agent_token"])
        and isinstance(config["transport"], str)
        and config["transport"] in {"auto", "websocket", "http"}
        and string(config["expires_at"], maximum=64),
        "Redeemed configuration does not match this installation",
    )
    try:
        expiry = datetime.fromisoformat(config["expires_at"])
        require(expiry.utcoffset() is not None, "Claim expiry must contain a time zone")
    except ValueError:
        raise BootstrapError("Invalid claim expiry") from None
    # This is the retry deadline, not an expiry of the long-lived Agent credential.
    # Already persisted input remains recoverable after that deadline.
    return value


def check_parents(path):
    require(path.is_absolute() and ".." not in path.parts, "Unsafe absolute filesystem path")
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except OSError:
            raise BootstrapError("Cannot safely inspect a directory component") from None
        sticky_temp = (
            parent == Path("/tmp") and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        )
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid in {0, ROOT_UID}
            and (not info.st_mode & 0o022 or sticky_temp),
            "Unsafe directory component or ownership",
        )


def private_directory(path, *, create=False):
    check_parents(path)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == ROOT_UID
        and stat.S_IMODE(info.st_mode) == 0o700,
        "Job directories must be root-owned, private, and not symlinks",
    )


def read_owned(path, *, limit=JSON_LIMIT, private=True):
    check_parents(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise BootstrapError("Cannot safely open an installation input") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == ROOT_UID
            and info.st_nlink == 1
            and not info.st_mode & (0o7077 if private else 0o7022)
            and info.st_size <= limit,
            "Unsafe installation input ownership, permissions, links, type, or size",
        )
        data = stream.read(limit + 1)
    require(len(data) <= limit, "Installation input exceeds its size limit")
    return data


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, data, *, mode=0o600):
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".prepare-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link() publishes without ever overwriting an existing name, including
        # a symlink. The private temporary link is removed before returning.
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            raise BootstrapError("Installation input already exists; refusing overwrite") from None
        Path(temporary).unlink()
        fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def json_bytes(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class Job:
    directory: Path
    root: Path
    unit: str
    server_id: str
    control_url: str
    ticket_sha256: str
    nonce: str = field(repr=False)


def prepare_job(*, control_url, ticket, server_id, ca_data=None, test_directory=None):
    control_url = validate_control_url(control_url)
    ticket = validate_ticket(ticket)
    server_id = validate_server_id(server_id)
    identifier = UUID(server_id).hex
    suffix = identifier[:12]
    unit = f"open-node-agent-{suffix}.service"
    base = JOB_BASE
    root = Path(f"/opt/open-node-agent-{suffix}")
    if test_directory is not None:
        require(
            re.fullmatch(r"/opt/open-node-bootstrap-smoke-[a-zA-Z0-9]{12}", str(test_directory)),
            "Test directory must be an explicit /opt/open-node-bootstrap-smoke-<12 letters/digits>",
        )
        check_parents(test_directory)
        info = test_directory.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == ROOT_UID
            and stat.S_IMODE(info.st_mode) == 0o755,
            "Test directory must already exist with root ownership and mode 0755",
        )
        base = test_directory / "private"
        root = test_directory / f"agent-{suffix}"
    check_parents(root)
    private_directory(base, create=True)
    digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
    directory = base / f"{identifier}-{digest[:16]}"
    private_directory(directory, create=True)
    identity = {
        "schema_version": 1,
        "server_id": server_id,
        "control_url": control_url,
        "ticket_sha256": digest,
        "root": str(root),
        "unit": unit,
        "ca_sha256": hashlib.sha256(ca_data).hexdigest() if ca_data is not None else None,
    }
    request = directory / "request.json"
    if not os.path.lexists(request):
        initial = {**identity, "claim_nonce": secrets.token_urlsafe(32)}
        try:
            write_new(request, json_bytes(initial))
        except BootstrapError:
            # A simultaneous invocation may have published the same request.
            # Read its original nonce; never make a second claim identity.
            if not os.path.lexists(request):
                raise
    saved = parse_json(read_owned(request))
    fields(saved, {*identity, "claim_nonce"})
    require(
        {key: saved[key] for key in identity} == identity
        and canonical_secret(saved["claim_nonce"]),
        "Saved job does not match this ticket, server, control plane, or CA",
    )
    if ca_data is not None:
        target = directory / "control-ca.pem"
        if os.path.lexists(target):
            require(read_owned(target, limit=CA_LIMIT) == ca_data, "Saved control CA changed")
        else:
            write_new(target, ca_data)
    return Job(directory, root, unit, server_id, control_url, digest, saved["claim_nonce"])


@contextlib.contextmanager
def job_lock(job):
    import fcntl

    private_directory(job.directory)
    path = job.directory / "operation.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "a") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == ROOT_UID
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600,
            "Unsafe installation lock",
        )
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BootstrapError("This bootstrap job is already running") from None
        yield


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def make_client(*, ca_data=None):
    if ca_data is None:
        # Explicit Debian system CA ignores SSL_CERT_FILE/SSL_CERT_DIR supplied
        # by a caller. A private control-plane CA never authorizes GitHub assets.
        context = ssl.create_default_context(cafile=str(SYSTEM_CA))
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            context.load_verify_locations(cadata=ca_data.decode("ascii"))
        except (UnicodeError, ssl.SSLError):
            raise BootstrapError("Invalid control-plane CA certificate") from None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
    )


def bounded_response(response, limit):
    require(
        response.headers.get("Content-Encoding", "identity").lower() == "identity",
        "Encoded download responses are not supported",
    )
    declared = response.headers.get("Content-Length")
    if declared is not None:
        require(
            isinstance(declared, str) and declared.isdecimal() and int(declared) <= limit,
            "Download Content-Length exceeds its limit",
        )
    return int(declared) if declared is not None else None


def request_json(client, url, *, payload=None):
    # No automatic redirect: in particular a redeemed credential must never be
    # redirected to a different origin or exposed by a URL or HTTP error body.
    data = json_bytes(payload) if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with client.open(request, timeout=30) as response:
            require(response.status == 200, "Unexpected control-plane response status")
            bounded_response(response, JSON_LIMIT)
            body = response.read(JSON_LIMIT + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code >= 500:
            raise RetryableRequest("Control-plane request temporarily failed") from None
        raise BootstrapError(
            "Control-plane request rejected; redirects and expired/revoked tickets are not accepted"
        ) from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise RetryableRequest("Control-plane HTTPS request failed") from None
    return parse_json(body)


def validate_redirect(url, *, initial_url):
    require(
        string(url, maximum=8192) and url.isascii() and "\\" not in url,
        "Unsafe release redirect",
    )
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BootstrapError("Invalid release redirect") from None
    require(
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and not parts.fragment
        and port in {None, 443}
        and (
            parts.hostname in ASSET_HOSTS
            or (parts.hostname == "github.com" and url == initial_url)
        ),
        "Release redirect left the approved GitHub asset hosts",
    )
    return url


def download_artifact(client, artifact, directory, *, limit):
    private_directory(directory)
    filename = artifact["filename"]
    require(
        isinstance(filename, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,160}", filename),
        "Unsafe artifact filename",
    )
    target = directory / filename
    if os.path.lexists(target):
        data = read_owned(target, limit=limit)
        require(
            hashlib.sha256(data).hexdigest() == artifact["sha256"],
            "Cached artifact SHA-256 mismatch; refusing overwrite",
        )
        return target
    initial = artifact["url"]
    require(
        initial == XRAY_URL
        or re.fullmatch(
            re.escape(RELEASE_BASE) + r"/agent-v" + VERSION_PATTERN + r"/[a-zA-Z0-9_.-]+",
            initial,
        ),
        "Artifact URL is outside the fixed project release sources",
    )
    url = initial
    deadline = time.monotonic() + 180
    for _ in range(6):
        validate_redirect(url, initial_url=initial)
        require(time.monotonic() < deadline, "Artifact download exceeded its time limit")
        try:
            response = client.open(
                urllib.request.Request(url, headers={"Accept-Encoding": "identity"}), timeout=30
            )
        except urllib.error.HTTPError as error:
            code, location = error.code, error.headers.get("Location")
            error.close()
            if code in {301, 302, 303, 307, 308} and location:
                require(string(location, maximum=8192), "Unsafe release redirect")
                validate_redirect(urljoin(url, location), initial_url=initial)
                url = urljoin(url, location)
                continue
            raise BootstrapError("Release artifact download failed") from None
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            raise BootstrapError("Release artifact HTTPS download failed") from None
        fd, temporary = tempfile.mkstemp(prefix=".download-", dir=directory)
        try:
            with response, os.fdopen(fd, "wb") as output:
                require(response.status == 200, "Unexpected release download status")
                declared = bounded_response(response, limit)
                digest, size = hashlib.sha256(), 0
                while block := response.read(64 * 1024):
                    size += len(block)
                    require(size <= limit, "Release artifact exceeds its size limit")
                    require(time.monotonic() < deadline, "Artifact download timed out")
                    digest.update(block)
                    output.write(block)
                require(declared is None or declared == size, "Truncated release artifact")
                require(digest.hexdigest() == artifact["sha256"], "Artifact SHA-256 mismatch")
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                raise BootstrapError(
                    "Artifact appeared during download; refusing overwrite"
                ) from None
            Path(temporary).unlink()
            fsync_directory(directory)
            return target
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            raise BootstrapError("Release download or private write failed") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
    raise BootstrapError("Too many release redirects")


def extract_files(directory, members):
    private_directory(directory, create=True)
    require(
        {path.name for path in directory.iterdir()} <= set(members),
        "Unexpected existing bootstrap files",
    )
    for name, (content, mode) in members.items():
        path = directory / name
        if os.path.lexists(path):
            require(
                read_owned(path, limit=XRAY_LIMIT) == content,
                "Previously prepared artifact changed; refusing overwrite",
            )
            require(stat.S_IMODE(path.lstat().st_mode) == mode, "Prepared artifact mode changed")
        else:
            write_new(path, content, mode=mode)
    return directory


def unpack_bootstrap(path, directory):
    read_owned(path, limit=BOOTSTRAP_LIMIT)
    files = {}
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                require(
                    member.name in BOOTSTRAP_FILES
                    and member.name not in files
                    and member.isfile()
                    and member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}
                    and not member.linkname
                    and not member.sparse
                    and 0 <= member.size <= 2 * 1024 * 1024,
                    "Unsafe bootstrap archive member",
                )
                stream = archive.extractfile(member)
                require(stream is not None, "Missing bootstrap archive data")
                with stream:
                    data = stream.read(2 * 1024 * 1024 + 1)
                require(len(data) == member.size, "Invalid bootstrap archive member size")
                files[member.name] = (data, 0o600)
        require(set(files) == BOOTSTRAP_FILES, "Incomplete bootstrap archive")
    except (tarfile.TarError, OSError, EOFError):
        raise BootstrapError("Invalid bootstrap archive") from None
    return extract_files(directory, files)


def unpack_xray(path, directory):
    read_owned(path, limit=XRAY_LIMIT)
    files, names, total = {}, set(), 0
    try:
        with zipfile.ZipFile(path) as archive:
            require(len(archive.infolist()) <= 32, "Too many Xray archive members")
            for member in archive.infolist():
                kind = stat.S_IFMT(member.external_attr >> 16)
                require(
                    member.filename in {"xray", "LICENSE", "README.md", "geoip.dat", "geosite.dat"}
                    and member.filename not in names
                    and not member.is_dir()
                    and kind in {0, stat.S_IFREG}
                    and not member.flag_bits & 1
                    and 0 <= member.file_size <= XRAY_LIMIT,
                    "Unsafe Xray archive member",
                )
                names.add(member.filename)
                total += member.file_size
                require(total <= 256 * 1024 * 1024, "Expanded Xray archive exceeds its limit")
                if member.filename in {"xray", "LICENSE"}:
                    bound = XRAY_LIMIT if member.filename == "xray" else CA_LIMIT
                    require(member.file_size <= bound, "Xray archive member exceeds its limit")
                    with archive.open(member) as source:
                        data = source.read(bound + 1)
                    require(len(data) == member.file_size, "Invalid Xray archive member size")
                    files[member.filename] = (data, 0o700 if member.filename == "xray" else 0o600)
        require(set(files) == {"xray", "LICENSE"}, "Xray binary or license is missing")
        binary = files["xray"][0]
        require(
            len(binary) >= 20
            and binary[:6] == b"\x7fELF\x02\x01"
            and int.from_bytes(binary[18:20], "little") == 62,
            "Xray binary is not Linux x86_64 ELF",
        )
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as error:
        if isinstance(error, BootstrapError):
            raise
        raise BootstrapError("Invalid Xray archive") from None
    return extract_files(directory, files)


def validate_wheel(path, manifest):
    agent = manifest["agent"]
    content = read_owned(path, limit=WHEEL_LIMIT)
    require(
        path.name == agent["wheel"]["filename"]
        and hashlib.sha256(content).hexdigest() == agent["wheel"]["sha256"],
        "Wheel filename or digest does not match the manifest",
    )
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), "Duplicate wheel archive members")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            require(len(metadata_names) == 1, "Invalid wheel metadata")
            item = archive.getinfo(metadata_names[0])
            require(item.file_size <= JSON_LIMIT, "Wheel metadata exceeds its limit")
            metadata = email.parser.BytesParser().parsebytes(archive.read(item))
            require(
                metadata.get_all("Name") == ["open-node-agent"]
                and metadata.get_all("Version") == [agent["version"]],
                "Wheel package or version does not match the manifest",
            )
    except (zipfile.BadZipFile, OSError, EOFError):
        raise BootstrapError("Invalid Agent wheel") from None


def prepare_artifacts(job, control_client, release_client):
    manifest_path = job.directory / "manifest.json"
    if os.path.lexists(manifest_path):
        manifest = validate_manifest(parse_json(read_owned(manifest_path)))
    else:
        manifest = validate_manifest(
            request_json(control_client, job.control_url + API_PATH + "/manifest")
        )
        write_new(manifest_path, json_bytes(manifest))
    agent = manifest["agent"]
    build = download_artifact(release_client, agent["build"], job.directory, limit=JSON_LIMIT)
    validate_build(parse_json(read_owned(build)), manifest)
    wheel = download_artifact(release_client, agent["wheel"], job.directory, limit=WHEEL_LIMIT)
    bootstrap = download_artifact(
        release_client, agent["bootstrap"], job.directory, limit=BOOTSTRAP_LIMIT
    )
    validate_wheel(wheel, manifest)
    helper = unpack_bootstrap(bootstrap, job.directory / "bootstrap")
    xray_archive = download_artifact(
        release_client, manifest["xray"]["archive"], job.directory, limit=XRAY_LIMIT
    )
    xray = unpack_xray(xray_archive, job.directory / "xray") / "xray"
    return {"manifest": manifest, "wheel": wheel, "service": helper / "service.py", "xray": xray}


def redeem_claim(job, ticket, client):
    validate_ticket(ticket)
    require(
        hashlib.sha256(ticket.encode("ascii")).hexdigest() == job.ticket_sha256,
        "Bootstrap ticket does not match the saved job",
    )
    target = job.directory / "claim.json"
    if os.path.lexists(target):
        return validate_claim(
            parse_json(read_owned(target)), server_id=job.server_id, control_url=job.control_url
        )
    for attempt in range(3):
        try:
            response = request_json(
                client,
                job.control_url + API_PATH + "/redeem",
                payload={"ticket": ticket, "claim_nonce": job.nonce},
            )
            claim = validate_claim(response, server_id=job.server_id, control_url=job.control_url)
            write_new(target, json_bytes(claim))
            return claim
        except RetryableRequest:
            if attempt == 2:
                raise
            time.sleep(1)
    raise BootstrapError("Bootstrap claim did not complete")


def command_environment():
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DEBIAN_FRONTEND": "noninteractive",
    }


def run_command(arguments, *, timeout=60):
    process = None
    try:
        process = subprocess.Popen(
            [str(value) for value in arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=command_environment(),
            start_new_session=True,
        )
        output, _error = process.communicate(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
        if process is not None and process.poll() is None:
            # Only our dedicated child process group is signalled. Never kill
            # unrelated pip, systemd, Agent, or Xray processes by name.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        raise BootstrapError(
            "Host operation failed or timed out; private inputs were retained"
        ) from None
    # Neither argv nor subprocess output is printed: validation errors from a
    # dependency must not turn a private configuration into terminal output.
    require(
        process.returncode == 0,
        "Host operation failed; inspect the owned service for recovery",
    )
    return output


def check_platform():
    require(
        sys.platform == "linux"
        and sys.version_info >= (3, 11)
        and os.geteuid() == 0
        and platform.machine() in {"x86_64", "amd64"}
        and Path("/run/systemd/system").is_dir(),
        "Bootstrap requires root, Python 3.11+, systemd, and a Debian 12 amd64 host",
    )
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        raise BootstrapError("Cannot verify the Debian host release") from None
    require(
        release.get("ID") == "debian" and release.get("VERSION_ID") == "12",
        "Automatic Agent bootstrap supports Debian 12 only",
    )


def dependencies_missing():
    return (
        importlib.util.find_spec("venv") is None
        or importlib.util.find_spec("ensurepip") is None
        or not SYSTEM_CA.is_file()
    )


def ensure_dependencies(*, install=False):
    if not dependencies_missing():
        return
    require(
        install,
        "Missing Python venv or system CA certificates. On Debian 12 run: "
        "apt-get update && apt-get install --no-install-recommends python3-venv ca-certificates; "
        "then retry, or explicitly use --install-dependencies",
    )
    # Recheck here as well as in main: this function never runs apt on an
    # unverified distribution, as another user, or without explicit opt-in.
    check_platform()
    run_command(["apt-get", "update"], timeout=300)
    run_command(
        [
            "apt-get", "install", "--yes", "--no-install-recommends",
            "python3-venv", "ca-certificates",
        ],
        timeout=600,
    )
    require(
        not dependencies_missing(),
        "Required Python venv or system CA dependency is still missing",
    )


def require_fresh_resources(job):
    import grp
    import pwd

    check_parents(job.root)
    require(
        not os.path.lexists(job.root),
        "Installation directory already exists; private inputs are retained for explicit recovery",
    )
    user = job.unit.removesuffix(".service")
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup(user)
        except KeyError:
            pass
        else:
            raise BootstrapError("Agent account or group already exists; refusing takeover")
    units = [job.unit, user + "-lifecycle.service", user + "-lifecycle.socket"]
    for unit in units:
        for base in ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system"):
            require(
                not os.path.lexists(Path(base) / unit)
                and not os.path.lexists(Path(base) / (unit + ".d")),
                "Agent service unit or override already exists; refusing takeover",
            )
        output = run_command(
            ["systemctl", "show", unit, "--property=FragmentPath,DropInPaths"], timeout=15
        ).decode("utf-8", "replace")
        props = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        require(
            not props.get("FragmentPath") and not props.get("DropInPaths"),
            "Agent service name is already claimed; refusing takeover",
        )


def prepare_configuration(job, claim):
    config = claim["configuration"]
    agent_path, xray_path = job.directory / "agent-input.json", job.directory / "xray-input.json"
    if os.path.lexists(agent_path) or os.path.lexists(xray_path):
        require(
            os.path.lexists(agent_path) and os.path.lexists(xray_path),
            "Partial private configuration retained; inspect it before retrying",
        )
        agent = parse_json(read_owned(agent_path))
        require(
            isinstance(agent, dict)
            and agent.get("master_url") == job.control_url
            and agent.get("token") == config["agent_token"]
            and agent.get("connection_mode") == config["transport"]
            and isinstance(agent.get("stats_address"), str)
            and re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", agent["stats_address"]),
            "Private Agent configuration no longer matches the redeemed claim",
        )
        expected_agent, expected_xray = configuration_data(job, claim, agent["stats_address"])
        require(agent == expected_agent, "Private Agent configuration changed")
        require(
            parse_json(read_owned(xray_path)) == expected_xray,
            "Private Xray configuration changed",
        )
        return agent_path, xray_path
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        address = f"127.0.0.1:{listener.getsockname()[1]}"
    agent, xray = configuration_data(job, claim, address)
    # Prepare the non-secret runtime config first. An interrupted pair is
    # retained for inspection, never silently overwritten on a future attempt.
    write_new(xray_path, json_bytes(xray))
    write_new(agent_path, json_bytes(agent))
    return agent_path, xray_path


def configuration_data(job, claim, stats_address):
    agent = {
        "master_url": job.control_url,
        "token": claim["configuration"]["agent_token"],
        "connection_mode": claim["configuration"]["transport"],
        "stats_address": stats_address,
        "runtime_mode": "managed",
        "allow_xray_takeover": False,
    }
    if (job.directory / "control-ca.pem").exists():
        agent["ca_file"] = str(job.directory / "control-ca.pem")
    xray = {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "listen": stats_address, "services": ["StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
        },
        "inbounds": [],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    return agent, xray


def install_agent(job, claim, artifacts):
    # A ticket claim, process spawn, or a running systemd unit alone is never
    # success. service.py owns its full version/PID/connection/runtime gate.
    require_fresh_resources(job)
    agent_path, xray_path = prepare_configuration(job, claim)
    run_command(
        [
            sys.executable, "-I", artifacts["service"],
            "--root", job.root, "--unit", job.unit, "--timeout", "90", "install",
            "--wheel", artifacts["wheel"], "--config", agent_path,
            "--xray-config", xray_path, "--xray", artifacts["xray"],
        ],
        timeout=1200,
    )
    installed = parse_json(read_owned(job.root / "installation.json"))
    require(
        isinstance(installed, dict)
        and installed.get("root") == str(job.root)
        and installed.get("unit") == job.unit
        and installed.get("user") == job.unit.removesuffix(".service")
        and type(installed.get("uid")) is int and installed["uid"] > 0
        and type(installed.get("gid")) is int and installed["gid"] > 0
        and installed.get("status") == "installed"
        and installed.get("current")
        and installed.get("pending") is None
        and installed.get("staging") is None
        and installed.get("network_diagnostics") is False
        and not installed.get("lifecycle"),
        "Host installer did not confirm a ready, isolated Agent installation",
    )
    current = installed.get("releases", {}).get(installed["current"], {})
    require(
        current.get("version") == artifacts["manifest"]["agent"]["version"]
        and current.get("sha256") == artifacts["manifest"]["agent"]["wheel"]["sha256"],
        "Installed release does not match the verified artifact",
    )
    write_new(job.directory / "success.json", json_bytes({
        "schema_version": 1,
        "root": str(job.root),
        "unit": job.unit,
        "installation_id": installed.get("installation_id"),
        "version": current["version"],
        "source_commit": artifacts["manifest"]["agent"]["source_commit"],
    }))


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally repeats an offending argument, which may contain a
        # ticket or a mistakenly pasted credential. Keep failures non-secret.
        raise BootstrapError("Invalid installer arguments; run --help for usage")


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--server-id", required=True)
    parser.add_argument(
        "--ca-file", type=Path, default=os.environ.get("OPEN_NODE_AGENT_CA_FILE") or None,
        help="Control-plane CA only (or OPEN_NODE_AGENT_CA_FILE); GitHub uses system trust",
    )
    parser.add_argument("--install-dependencies", action="store_true")
    parser.add_argument("--test-directory", type=Path, help=argparse.SUPPRESS)
    job = None
    try:
        args = parser.parse_args(argv)
        control_url = validate_control_url(args.control_url)
        validate_ticket(args.ticket)
        server_id = validate_server_id(args.server_id)
        check_platform()
        ensure_dependencies(install=args.install_dependencies)
        ca_data = read_owned(args.ca_file, limit=CA_LIMIT, private=False) if args.ca_file else None
        job = prepare_job(
            control_url=control_url, ticket=args.ticket, server_id=server_id,
            ca_data=ca_data, test_directory=args.test_directory,
        )
        with job_lock(job):
            require_fresh_resources(job)
            control_client = make_client(ca_data=ca_data)
            release_client = make_client()
            print("Preparing and verifying pinned Agent and Xray artifacts.", flush=True)
            artifacts = prepare_artifacts(job, control_client, release_client)
            print(
                "Artifacts verified. Claiming this server's short-lived bootstrap ticket.",
                flush=True,
            )
            claim = redeem_claim(job, args.ticket, control_client)
            print(
                "Claim saved privately. Installing and waiting for authenticated readiness.",
                flush=True,
            )
            install_agent(job, claim, artifacts)
        print(f"Agent installed and ready: {job.unit}", flush=True)
        print(f"Private recovery inputs: {job.directory}", flush=True)
        return 0
    except BootstrapError as error:
        print(f"Bootstrap failed: {error}", file=sys.stderr)
    except (Exception, KeyboardInterrupt):
        print(
            "Bootstrap failed; no untrusted response or private credential was printed.",
            file=sys.stderr,
        )
    if job is not None:
        print(f"Private inputs retained for explicit recovery: {job.directory}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
