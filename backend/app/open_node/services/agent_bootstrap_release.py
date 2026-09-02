"""Pinned Agent release metadata and panel-owned artifact distribution."""

import http.client
import json
import os
import re
import shlex
import ssl
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import UUID

INSTALLER_LIMIT_BYTES = 262_144
MANIFEST_LIMIT_BYTES = 65_536
ARTIFACT_PATH = "/api/v1/agents/bootstrap/artifacts/"
PROJECT_RELEASE_BASE = "https://github.com/FengYuchen1314/open-node/releases/download"
XRAY_UPSTREAM = (
    "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip"
)
MIHOMO_RELEASE_RESOURCE = "mihomo-release.json"
APPROVED_REDIRECT_HOSTS = frozenset(
    {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
)
DOWNLOAD_CHUNK_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 180


class AgentBootstrapReleaseUnavailable(ValueError):
    pass


class AgentBootstrapArtifactUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class AgentArtifact:
    filename: str
    path: str
    sha256: str
    size: int
    upstream: str


def installer_bytes() -> bytes:
    try:
        with files("open_node.resources").joinpath("agent_installer.py").open("rb") as source:
            content = source.read(INSTALLER_LIMIT_BYTES + 1)
        if not content or len(content) > INSTALLER_LIMIT_BYTES:
            raise ValueError("Invalid installer size")
        return content
    except (OSError, ModuleNotFoundError, ValueError):
        raise AgentBootstrapReleaseUnavailable("Agent installer is not available") from None


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Invalid release fields")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate release field")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("Invalid release constant")


def _artifact(value, filename, *, maximum):
    artifact = _object(value, {"filename", "path", "sha256", "bytes"})
    if (
        artifact["filename"] != filename
        or artifact["path"] != ARTIFACT_PATH + filename
        or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
        or type(artifact["bytes"]) is not int
        or not 1 <= artifact["bytes"] <= maximum
    ):
        raise ValueError("Invalid release artifact")
    return artifact


def release_manifest() -> dict:
    try:
        with files("open_node.resources").joinpath("agent-release.json").open("rb") as source:
            content = source.read(MANIFEST_LIMIT_BYTES + 1)
        if len(content) > MANIFEST_LIMIT_BYTES:
            raise ValueError("Invalid release size")
        payload = _object(
            json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant),
            {"schema_version", "agent", "xray", "mihomo", "license_required"},
        )
        agent = _object(
            payload["agent"], {"version", "source_commit", "tag", "wheel", "bootstrap", "build"}
        )
        xray = _object(payload["xray"], {"version", "architecture", "archive"})
        mihomo = _object(payload["mihomo"], {"version", "assets"})
        version = agent["version"]
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != 2
            or payload["license_required"] is not False
            or not isinstance(version, str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+|b[0-9]+|rc[0-9]+)?", version)
            or agent["tag"] != f"agent-v{version}"
            or not re.fullmatch(r"[0-9a-f]{40}", agent["source_commit"])
        ):
            raise ValueError("Invalid release identity")
        for key, filename in (
            ("wheel", f"open_node_agent-{version}-py3-none-any.whl"),
            ("bootstrap", f"open-node-agent-bootstrap-{version}.tar.gz"),
            ("build", "BUILD.json"),
        ):
            maximum = 65_536 if key == "build" else 32 * 1024 * 1024
            _artifact(agent[key], filename, maximum=maximum)
        if (
            xray["version"] != "v26.3.27"
            or xray["architecture"] != "x86_64"
        ):
            raise ValueError("Invalid Xray artifact")
        archive = _artifact(xray["archive"], "Xray-linux-64.zip", maximum=128 * 1024 * 1024)
        if archive != {
            "filename": "Xray-linux-64.zip",
            "path": ARTIFACT_PATH + "Xray-linux-64.zip",
            "sha256": "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae",
            "bytes": 21_136_402,
        }:
            raise ValueError("Invalid Xray artifact")
        with files("open_node.resources").joinpath(MIHOMO_RELEASE_RESOURCE).open("rb") as source:
            pinned_mihomo = _object(
                json.loads(
                    source.read(MANIFEST_LIMIT_BYTES + 1),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                ),
                {"schema_version", "version", "assets", "sing_box"},
            )
        if pinned_mihomo["schema_version"] != 1 or mihomo["version"] != pinned_mihomo["version"]:
            raise ValueError("Invalid Mihomo release identity")
        assets = _object(mihomo["assets"], {"linux-amd64", "linux-arm64"})
        for platform, filename in (
            ("linux-amd64", "mihomo-linux-amd64-compatible-v1.19.30.gz"),
            ("linux-arm64", "mihomo-linux-arm64-v1.19.30.gz"),
        ):
            source_asset = _object(
                pinned_mihomo["assets"][platform], {"url", "sha256", "compressed_bytes"}
            )
            artifact = _artifact(assets[platform], filename, maximum=64 * 1024 * 1024)
            if artifact["sha256"] != source_asset["sha256"] or artifact["bytes"] != source_asset[
                "compressed_bytes"
            ]:
                raise ValueError("Mihomo artifact does not match its release pin")
        return payload
    except (OSError, ModuleNotFoundError, ValueError, KeyError, TypeError, RecursionError):
        raise AgentBootstrapReleaseUnavailable("Verified Agent release is not available") from None


def release_artifacts() -> dict[str, AgentArtifact]:
    """Return the only filenames the public artifact endpoint may distribute."""
    manifest = release_manifest()
    agent = manifest["agent"]
    result = {}
    for key in ("wheel", "bootstrap", "build"):
        item = agent[key]
        filename = item["filename"]
        result[filename] = AgentArtifact(
            filename=filename,
            path=item["path"],
            sha256=item["sha256"],
            size=item["bytes"],
            upstream=f"{PROJECT_RELEASE_BASE}/{agent['tag']}/{filename}",
        )
    item = manifest["xray"]["archive"]
    result[item["filename"]] = AgentArtifact(
        filename=item["filename"],
        path=item["path"],
        sha256=item["sha256"],
        size=item["bytes"],
        upstream=XRAY_UPSTREAM,
    )
    for item in manifest["mihomo"]["assets"].values():
        result[item["filename"]] = AgentArtifact(
            filename=item["filename"],
            path=item["path"],
            sha256=item["sha256"],
            size=item["bytes"],
            upstream=f"{PROJECT_RELEASE_BASE}/{agent['tag']}/{item['filename']}",
        )
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _upstream_url(url: str, *, initial: str) -> str:
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 8192
        or not url.isascii()
        or "\\" in url
        or any(ord(character) < 32 or ord(character) == 127 for character in url)
    ):
        raise AgentBootstrapArtifactUnavailable("Agent artifact source is invalid")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise AgentBootstrapArtifactUnavailable("Agent artifact source is invalid") from None
    if not (
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and not parts.fragment
        and port in {None, 443}
        and (
            (url == initial and parts.hostname == "github.com")
            or parts.hostname in APPROVED_REDIRECT_HOSTS
        )
    ):
        raise AgentBootstrapArtifactUnavailable("Agent artifact source left its allowlist")
    return url


class AgentArtifactStore:
    """Fetch fixed upstream bytes once, verify them, then serve the private cache."""

    def __init__(self, directory: Path, opener=None):
        self.directory = directory
        self._lock = threading.Lock()
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            _NoRedirect(),
        )

    @staticmethod
    def _digest(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
                digest.update(block)
        return digest.hexdigest()

    def _prepare_directory(self) -> None:
        if not self.directory.is_absolute() or self.directory == Path(self.directory.anchor):
            raise AgentBootstrapArtifactUnavailable("Agent artifact cache path is unsafe")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.lstat()
        except OSError:
            raise AgentBootstrapArtifactUnavailable("Agent artifact cache is unavailable") from None
        if not stat.S_ISDIR(info.st_mode) or self.directory.is_symlink():
            raise AgentBootstrapArtifactUnavailable("Agent artifact cache is unsafe")
        if os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise AgentBootstrapArtifactUnavailable("Agent artifact cache ownership is unsafe")

    def descriptor(self, filename: str) -> AgentArtifact:
        try:
            return release_artifacts()[filename]
        except (KeyError, AgentBootstrapReleaseUnavailable):
            raise AgentBootstrapArtifactUnavailable("Unknown Agent artifact") from None

    def _cached(self, artifact: AgentArtifact) -> Path | None:
        target = self.directory / artifact.filename
        try:
            info = target.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise AgentBootstrapArtifactUnavailable("Agent artifact cache is unavailable") from None
        owner_ok = os.name == "nt" or info.st_uid == os.geteuid()
        if not (
            stat.S_ISREG(info.st_mode)
            and not target.is_symlink()
            and info.st_nlink == 1
            and owner_ok
            and info.st_size == artifact.size
            and (os.name == "nt" or stat.S_IMODE(info.st_mode) == 0o600)
            and self._digest(target) == artifact.sha256
        ):
            raise AgentBootstrapArtifactUnavailable("Cached Agent artifact failed verification")
        return target

    @staticmethod
    def _declared_size(response, expected: int) -> None:
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise AgentBootstrapArtifactUnavailable("Encoded Agent artifacts are not accepted")
        declared = response.headers.get("Content-Length")
        if not isinstance(declared, str) or not declared.isdecimal() or int(declared) != expected:
            raise AgentBootstrapArtifactUnavailable("Agent artifact size does not match its pin")

    def _download(self, artifact: AgentArtifact) -> Path:
        initial = artifact.upstream
        url = initial
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
        for _ in range(6):
            _upstream_url(url, initial=initial)
            if time.monotonic() >= deadline:
                raise AgentBootstrapArtifactUnavailable("Agent artifact download timed out")
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "User-Agent": "OpenNode-AgentArtifactProxy/1",
                },
            )
            try:
                response = self._opener.open(request, timeout=30)
            except urllib.error.HTTPError as error:
                code, location = error.code, error.headers.get("Location")
                error.close()
                if code in {301, 302, 303, 307, 308} and location:
                    if (
                        not isinstance(location, str)
                        or not 1 <= len(location) <= 8192
                        or any(
                            ord(character) < 32 or ord(character) == 127
                            for character in location
                        )
                    ):
                        raise AgentBootstrapArtifactUnavailable(
                            "Agent artifact redirect is invalid"
                        ) from None
                    next_url = urljoin(url, location)
                    _upstream_url(next_url, initial=initial)
                    url = next_url
                    continue
                raise AgentBootstrapArtifactUnavailable(
                    "Agent artifact upstream rejected the request"
                ) from None
            except (OSError, urllib.error.URLError, http.client.HTTPException):
                raise AgentBootstrapArtifactUnavailable(
                    "Agent artifact upstream is unavailable"
                ) from None
            fd, temporary_name = tempfile.mkstemp(prefix=".download-", dir=self.directory)
            temporary = Path(temporary_name)
            try:
                with response, os.fdopen(fd, "wb") as output:
                    if response.status != 200:
                        raise AgentBootstrapArtifactUnavailable(
                            "Agent artifact upstream returned an unexpected status"
                        )
                    self._declared_size(response, artifact.size)
                    digest, total = sha256(), 0
                    while block := response.read(DOWNLOAD_CHUNK_BYTES):
                        total += len(block)
                        if total > artifact.size or time.monotonic() >= deadline:
                            raise AgentBootstrapArtifactUnavailable(
                                "Agent artifact exceeded its verified bounds"
                            )
                        digest.update(block)
                        output.write(block)
                    if total != artifact.size or digest.hexdigest() != artifact.sha256:
                        raise AgentBootstrapArtifactUnavailable(
                            "Agent artifact failed SHA-256 verification"
                        )
                    os.fchmod(output.fileno(), 0o600)
                    output.flush()
                    os.fsync(output.fileno())
                target = self.directory / artifact.filename
                os.replace(temporary, target)
                return self._cached(artifact) or target
            except AgentBootstrapArtifactUnavailable:
                raise
            except (OSError, urllib.error.URLError, http.client.HTTPException):
                raise AgentBootstrapArtifactUnavailable(
                    "Agent artifact could not be cached safely"
                ) from None
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        raise AgentBootstrapArtifactUnavailable("Agent artifact redirected too many times")

    def get(self, filename: str) -> tuple[Path, AgentArtifact]:
        artifact = self.descriptor(filename)
        with self._lock:
            self._prepare_directory()
            path = self._cached(artifact)
            if path is None:
                path = self._download(artifact)
            return path, artifact


def installation_command(control_url: str, ticket: str, server_id: UUID) -> str:
    """The short ticket is explicit; no long-lived Agent credential enters the shell."""
    from open_node.services.agent_bootstrap import normalize_control_url

    control_url = normalize_control_url(control_url)
    source = control_url + "/api/v1/agents/bootstrap/installer.py"
    checksum = sha256(installer_bytes()).hexdigest()
    quote = shlex.quote
    steps = [
        "( set -eu",
        'test "$(id -u)" -eq 0 || { echo "Run as root on a supported amd64 server" >&2; exit 1; }',
        'test -r /etc/os-release || { echo "Cannot verify the operating system" >&2; exit 1; }',
        'os_id=$(sed -n \'s/^ID=//p\' /etc/os-release | tr -d \'"\')',
        'os_version=$(sed -n \'s/^VERSION_ID=//p\' /etc/os-release | tr -d \'"\')',
        'case "$os_id:$os_version" in debian:12|debian:13|ubuntu:24.04|ubuntu:26.04) ;; '
        '*) echo "Supported systems: Debian 12/13 or Ubuntu 24.04/26.04 amd64" >&2; exit 1 ;; esac',
        'test "$(uname -m)" = x86_64 || { echo "Only amd64 servers are supported" >&2; exit 1; }',
        "export DEBIAN_FRONTEND=noninteractive",
        "apt-get update",
        "apt-get install --yes --no-install-recommends ca-certificates curl python3 python3-venv",
        "python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || "
        '{ echo "Python 3.11 or newer is required" >&2; exit 1; }',
        "umask 077",
        "installer=$(mktemp --tmpdir open-node-agent-install.XXXXXXXX.py)",
        "trap 'rm -f -- \"$installer\"' EXIT HUP INT TERM",
        "curl --disable --proto '=https' --tlsv1.2 --fail --silent --show-error --max-time 60 "
        f"--max-filesize {INSTALLER_LIMIT_BYTES} {quote(source)} -o \"$installer\"",
        f"printf '%s  %s\\n' {quote(checksum)} \"$installer\" | sha256sum --check --status",
        f'python3 "$installer" --control-url {quote(control_url)} --ticket {quote(ticket)} '
        f"--server-id {quote(str(server_id))} --install-dependencies",
        ")",
    ]
    return "; ".join(steps)
