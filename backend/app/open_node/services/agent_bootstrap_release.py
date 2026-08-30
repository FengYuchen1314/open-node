"""Pinned release metadata and a checksum-bound panel installation command."""

import json
import re
import shlex
from hashlib import sha256
from importlib.resources import files
from uuid import UUID

from open_node.services.agent_bootstrap import normalize_control_url

INSTALLER_LIMIT_BYTES = 262_144
MANIFEST_LIMIT_BYTES = 65_536


class AgentBootstrapReleaseUnavailable(ValueError):
    pass


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


def release_manifest() -> dict:
    try:
        with files("open_node.resources").joinpath("agent-release.json").open("rb") as source:
            content = source.read(MANIFEST_LIMIT_BYTES + 1)
        if len(content) > MANIFEST_LIMIT_BYTES:
            raise ValueError("Invalid release size")
        payload = _object(
            json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant),
            {"schema_version", "agent", "xray", "license_required"},
        )
        agent = _object(
            payload["agent"], {"version", "source_commit", "tag", "wheel", "bootstrap", "build"}
        )
        xray = _object(payload["xray"], {"version", "architecture", "archive"})
        version = agent["version"]
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
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
            artifact = _object(agent[key], {"filename", "url", "sha256"})
            expected = (
                "https://github.com/FengYuchen1314/open-node/releases/download/"
                + agent["tag"]
                + "/"
                + filename
            )
            if (
                artifact["filename"] != filename
                or artifact["url"] != expected
                or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
            ):
                raise ValueError("Invalid release artifact")
        if (
            xray["version"] != "v26.3.27"
            or xray["architecture"] != "x86_64"
            or xray["archive"] != {
                "filename": "Xray-linux-64.zip",
                "url": (
                    "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip"
                ),
                "sha256": "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae",
            }
        ):
            raise ValueError("Invalid Xray artifact")
        return payload
    except (OSError, ModuleNotFoundError, ValueError, KeyError, TypeError, RecursionError):
        raise AgentBootstrapReleaseUnavailable("Verified Agent release is not available") from None


def installation_command(control_url: str, ticket: str, server_id: UUID) -> str:
    """The short ticket is explicit; no long-lived Agent credential enters the shell."""
    control_url = normalize_control_url(control_url)
    source = control_url + "/api/v1/agents/bootstrap/installer.py"
    checksum = sha256(installer_bytes()).hexdigest()
    quote = shlex.quote
    steps = [
        "( set -eu",
        'test "$(id -u)" -eq 0 || { echo "Run as root on Debian 12 amd64" >&2; exit 1; }',
        'command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }',
        'command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }',
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
