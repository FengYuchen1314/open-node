"""Public gateway installer policy without starting a deployment."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"
PUBLIC_KEYS = {
    "OPEN_NODE_PUBLIC_HOSTNAME",
    "OPEN_NODE_BIND_ADDRESS",
    "OPEN_NODE_SESSION_COOKIE_SECURE",
    "OPEN_NODE_TRUSTED_PROXIES",
    "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL",
}


def installer_definitions(tmp_path):
    source = INSTALLER.read_text()
    definitions = source[: source.rindex('\nmain "$@"')]
    path = tmp_path / "installer-definitions.sh"
    path.write_text(definitions)
    path.chmod(0o600)
    return path


def run_bash(script, *, definitions, arguments=(), overrides=None):
    environment = {key: value for key, value in os.environ.items() if key not in PUBLIC_KEYS}
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", "-c", script, "gateway-test", str(definitions), *map(str, arguments)],
        text=True,
        capture_output=True,
        timeout=10,
        env=environment,
    )


@pytest.mark.parametrize(
    "hostname,accepted",
    [
        ("panel.example.com", True),
        ("xn--fiqs8s.example.com", True),
        ("a-b.example.co.uk", True),
        ("Panel.example.com", False),
        ("panel.example", False),
        ("panel.example.test", False),
        ("panel.arpa", False),
        ("panel.local", False),
        ("panel.internal", False),
        ("localhost", False),
        ("127.0.0.1", False),
        ("panel.example.com:443", False),
        ("https://panel.example.com", False),
        ("-panel.example.com", False),
        ("panel..example.com", False),
        ("panel.example.123", False),
    ],
)
def test_public_hostname_policy(tmp_path, hostname, accepted):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        'source "$1"; trap - EXIT INT TERM HUP; validate_public_hostname "$2"',
        definitions=definitions,
        arguments=(hostname,),
    )
    assert (result.returncode == 0) is accepted, result.stderr


def parse_environment(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def candidate_environment(tmp_path, *, source=ROOT, base=None, overrides=None):
    definitions = installer_definitions(tmp_path)
    candidate = tmp_path / "candidate.env"
    script = r'''
source "$1"
trap - EXIT INT TERM HUP
IMAGE_REPOSITORY=open-node-test
create_candidate_environment "$2" "$3" "${4:-}"
'''
    result = run_bash(
        script,
        definitions=definitions,
        arguments=(source, candidate, base or ""),
        overrides=overrides,
    )
    return result, candidate


def configured_base(tmp_path):
    path = tmp_path / "active.env"
    values = (ROOT / "deploy/.env.example").read_text()
    replacements = {
        "OPEN_NODE_SESSION_COOKIE_SECURE=false": "OPEN_NODE_SESSION_COOKIE_SECURE=true",
        "OPEN_NODE_TRUSTED_PROXIES=": "OPEN_NODE_TRUSTED_PROXIES=*",
        "OPEN_NODE_PUBLIC_HOSTNAME=": "OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com",
        "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=": (
            "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=https://panel.example.com"
        ),
    }
    for old, new in replacements.items():
        values = values.replace(old, new)
    path.write_text(values)
    path.chmod(0o600)
    return path


def test_fresh_public_gateway_sets_consistent_private_runtime(tmp_path):
    result, candidate = candidate_environment(
        tmp_path, overrides={"OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com"}
    )
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_BIND_ADDRESS"] == "127.0.0.1"
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "true"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == "*"
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == "panel.example.com"
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == "https://panel.example.com"


def test_gateway_asset_is_required_only_when_public_mode_is_enabled(tmp_path):
    legacy = tmp_path / "legacy-source"
    (legacy / "deploy").mkdir(parents=True)
    (legacy / "deploy/.env.example").write_bytes(
        (ROOT / "deploy/.env.example").read_bytes()
    )
    disabled, _candidate = candidate_environment(tmp_path, source=legacy)
    enabled, _candidate = candidate_environment(
        tmp_path,
        source=legacy,
        overrides={"OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com"},
    )
    assert disabled.returncode == 0, disabled.stderr
    assert enabled.returncode != 0
    assert "candidate public gateway Caddyfile" in enabled.stderr


def test_existing_public_gateway_is_preserved_without_an_override(tmp_path):
    result, candidate = candidate_environment(tmp_path, base=configured_base(tmp_path))
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == "panel.example.com"
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "true"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == "*"
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == "https://panel.example.com"


def test_explicit_disable_clears_managed_public_defaults(tmp_path):
    result, candidate = candidate_environment(
        tmp_path,
        base=configured_base(tmp_path),
        overrides={"OPEN_NODE_PUBLIC_HOSTNAME": ""},
    )
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == ""
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "false"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == ""
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == ""


@pytest.mark.parametrize(
    "overrides,message",
    [
        (
            {
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_BIND_ADDRESS": "0.0.0.0",
                "OPEN_NODE_ALLOW_PUBLIC_HTTP": "1",
            },
            "listener to remain on 127.0.0.1",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            },
            "SESSION_COOKIE_SECURE=true",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_TRUSTED_PROXIES": "127.0.0.1",
            },
            "TRUSTED_PROXIES=*",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL": "https://other.example.com",
            },
            "must equal the managed public HTTPS URL",
        ),
    ],
)
def test_public_gateway_rejects_conflicting_security_settings(tmp_path, overrides, message):
    result, _candidate = candidate_environment(tmp_path, overrides=overrides)
    assert result.returncode != 0
    assert message in result.stderr


def test_same_revision_public_change_is_detected(tmp_path):
    definitions = installer_definitions(tmp_path)
    active = tmp_path / "active.env"
    active.write_text("OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com\n")
    active.chmod(0o600)
    script = r'''
source "$1"
trap - EXIT INT TERM HUP
ENV_FILE="$2"
requested_public_gateway_change
'''
    unchanged = run_bash(
        script,
        definitions=definitions,
        arguments=(active,),
        overrides={"OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com"},
    )
    changed = run_bash(
        script,
        definitions=definitions,
        arguments=(active,),
        overrides={"OPEN_NODE_PUBLIC_HOSTNAME": "other.example.com"},
    )
    absent = run_bash(script, definitions=definitions, arguments=(active,))
    assert unchanged.returncode != 0
    assert changed.returncode == 0
    assert absent.returncode != 0
