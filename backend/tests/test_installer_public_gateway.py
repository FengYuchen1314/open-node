"""Public gateway installer policy without starting a deployment."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"
PUBLIC_KEYS = {
    "OPEN_NODE_PUBLIC_HOSTNAME",
    "OPEN_NODE_PUBLIC_IP",
    "OPEN_NODE_PUBLIC_HTTPS_PORT",
    "OPEN_NODE_BIND_ADDRESS",
    "OPEN_NODE_SESSION_COOKIE_SECURE",
    "OPEN_NODE_TRUSTED_PROXIES",
    "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL",
    "OPEN_NODE_TEST_DETECTED_PUBLIC_IP",
}


def installer_definitions(tmp_path):
    source = INSTALLER.read_text(encoding="utf-8")
    definitions = source[: source.rindex('\nmain "$@"')]
    path = tmp_path / "installer-definitions.sh"
    path.write_text(definitions, encoding="utf-8")
    path.chmod(0o600)
    python3 = tmp_path / "python3"
    python3.write_text(
        "#!/usr/bin/env bash\n"
        f"exec {shlex.quote(Path(sys.executable).resolve().as_posix())} \"$@\"\n",
        encoding="utf-8",
    )
    python3.chmod(0o700)
    return path


def run_bash(script, *, definitions, arguments=(), overrides=None):
    environment = {key: value for key, value in os.environ.items() if key not in PUBLIC_KEYS}
    environment["PATH"] = os.pathsep.join(
        (str(definitions.parent), environment.get("PATH", ""))
    )
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
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def candidate_environment(tmp_path, *, source=ROOT, base=None, overrides=None):
    definitions = installer_definitions(tmp_path)
    candidate = tmp_path / "candidate.env"
    script = r'''
source "$1"
trap - EXIT INT TERM HUP
if [[ -n "${OPEN_NODE_TEST_DETECTED_PUBLIC_IP:-}" ]]; then
  detect_public_ipv4() { printf '%s\n' "$OPEN_NODE_TEST_DETECTED_PUBLIC_IP"; }
fi
# The real installer runs as root.  This isolated unit test may run under an
# unprivileged CI account, so keep every file/mode/symlink check while trusting
# the current test owner in place of uid 0.  The root-only policy has separate
# static and root-run VPS coverage.
validate_safe_file() {
  local label="$1" path="$2" private="${3:-0}" owner mode
  [[ -f "$path" && ! -L "$path" ]] || die "$label must be a regular non-symlink file: $path"
  owner="$(stat -c '%u' -- "$path")" || die "could not inspect $label"
  mode="$(stat -c '%a' -- "$path")" || die "could not inspect $label"
  [[ "$owner" == "$(id -u)" ]] || die "$label must be owned by the isolated test account: $path"
  (( (8#$mode & 022) == 0 )) || die "$label must not be group/world writable: $path"
  if [[ "$private" == "1" ]]; then
    (( (8#$mode & 077) == 0 )) || die "$label must not grant group/other access: $path"
  fi
}
IMAGE_REPOSITORY=open-node-test
create_candidate_environment "$2" "$3" "${4:-}"
'''
    environment = {"OPEN_NODE_TEST_DETECTED_PUBLIC_IP": "1.1.1.1"}
    environment.update(overrides or {})
    result = run_bash(
        script,
        definitions=definitions,
        arguments=(source, candidate, base or ""),
        overrides=environment,
    )
    return result, candidate


def test_public_ip_auto_requires_two_matching_https_results(tmp_path):
    definitions = installer_definitions(tmp_path)
    script = r'''
source "$1"
trap - EXIT INT TERM HUP
curl() {
  case " $* " in
    *api.ipify.org*) printf '%s\n' "$TEST_FIRST_PUBLIC_IP" ;;
    *checkip.amazonaws.com*) printf '%s\n' "$TEST_SECOND_PUBLIC_IP" ;;
    *) return 1 ;;
  esac
}
detect_public_ipv4
'''
    matching = run_bash(
        script,
        definitions=definitions,
        overrides={
            "TEST_FIRST_PUBLIC_IP": "1.1.1.1",
            "TEST_SECOND_PUBLIC_IP": "1.1.1.1",
        },
    )
    disagreeing = run_bash(
        script,
        definitions=definitions,
        overrides={
            "TEST_FIRST_PUBLIC_IP": "1.1.1.1",
            "TEST_SECOND_PUBLIC_IP": "8.8.8.8",
        },
    )

    assert matching.returncode == 0, matching.stderr
    assert matching.stdout == "1.1.1.1\n"
    assert disagreeing.returncode != 0
    assert "detection disagreed" in disagreeing.stderr


@pytest.mark.parametrize(
    "value,normalized",
    [
        ("1.1.1.1", "1.1.1.1"),
        ("2606:4700:4700:0:0:0:0:1111", "2606:4700:4700::1111"),
    ],
)
def test_public_ip_literal_is_normalized(tmp_path, value, normalized):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        'source "$1"; trap - EXIT INT TERM HUP; normalize_public_ip "$2"',
        definitions=definitions,
        arguments=(value,),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{normalized}\n"


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "10.0.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "ff02::1",
        "panel.example.com",
        "1.1.1.1/32",
    ],
)
def test_public_ip_literal_rejects_non_public_or_non_literal_values(tmp_path, value):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        'source "$1"; trap - EXIT INT TERM HUP; '
        'validate_public_ip_input "$2" && normalize_public_ip "$2"',
        definitions=definitions,
        arguments=(value,),
    )
    assert result.returncode != 0


def test_public_ip_endpoint_requires_off_instead_of_an_empty_override(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        'source "$1"; trap - EXIT INT TERM HUP; ACTION=install; '
        'validate_absolute_path() { :; }; validate_path_separation() { :; }; '
        "validate_inputs",
        definitions=definitions,
        overrides={"OPEN_NODE_PUBLIC_IP": ""},
    )
    assert result.returncode != 0
    assert "must use off" in result.stderr


def test_fresh_default_auto_ip_gateway_sets_consistent_private_runtime(tmp_path):
    result, candidate = candidate_environment(tmp_path)
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_BIND_ADDRESS"] == "127.0.0.1"
    assert values["OPEN_NODE_HTTP_PORT"] == "62031"
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "true"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == "*"
    assert values["OPEN_NODE_PUBLIC_IP"] == "1.1.1.1"
    assert values["OPEN_NODE_PUBLIC_HTTPS_PORT"] == "58090"
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == ""
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == "https://1.1.1.1:58090"


@pytest.mark.parametrize(
    "overrides,expected_ip,expected_hostname,expected_url",
    [
        (
            {"OPEN_NODE_PUBLIC_IP": "8.8.8.8"},
            "8.8.8.8",
            "",
            "https://8.8.8.8:58090",
        ),
        (
            {"OPEN_NODE_PUBLIC_IP": "2606:4700:4700:0:0:0:0:1111"},
            "2606:4700:4700::1111",
            "",
            "https://[2606:4700:4700::1111]:58090",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_IP": "off",
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
            },
            "",
            "panel.example.com",
            "https://panel.example.com",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_IP": "8.8.8.8",
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
            },
            "8.8.8.8",
            "panel.example.com",
            "https://panel.example.com",
        ),
    ],
)
def test_fresh_ip_domain_and_dual_modes_have_one_canonical_url(
    tmp_path, overrides, expected_ip, expected_hostname, expected_url
):
    result, candidate = candidate_environment(tmp_path, overrides=overrides)
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_PUBLIC_IP"] == expected_ip
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == expected_hostname
    assert values["OPEN_NODE_PUBLIC_HTTPS_PORT"] == "58090"
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == expected_url
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "true"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == "*"


def test_fresh_public_ip_can_use_an_explicit_public_https_port(tmp_path):
    result, candidate = candidate_environment(
        tmp_path,
        overrides={
            "OPEN_NODE_PUBLIC_IP": "8.8.8.8",
            "OPEN_NODE_PUBLIC_HTTPS_PORT": "58443",
        },
    )
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_PUBLIC_HTTPS_PORT"] == "58443"
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == "https://8.8.8.8:58443"


def test_gateway_asset_is_required_only_when_public_mode_is_enabled(tmp_path):
    source = tmp_path / "source-without-gateway-assets"
    (source / "deploy").mkdir(parents=True)
    (source / "deploy/.env.example").write_bytes(
        (ROOT / "deploy/.env.example").read_bytes()
    )
    disabled, _candidate = candidate_environment(
        tmp_path,
        source=source,
        overrides={"OPEN_NODE_PUBLIC_IP": "off"},
    )
    ip_enabled, _candidate = candidate_environment(tmp_path, source=source)
    domain_enabled, _candidate = candidate_environment(
        tmp_path,
        source=source,
        overrides={
            "OPEN_NODE_PUBLIC_IP": "off",
            "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
        },
    )
    dual_enabled, _candidate = candidate_environment(
        tmp_path,
        source=source,
        overrides={
            "OPEN_NODE_PUBLIC_IP": "8.8.8.8",
            "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
        },
    )
    assert disabled.returncode == 0, disabled.stderr
    for result, filename in [
        (ip_enabled, "Caddyfile.ip"),
        (domain_enabled, "Caddyfile"),
        (dual_enabled, "Caddyfile.dual"),
    ]:
        assert result.returncode != 0
        assert "candidate public gateway Caddyfile" in result.stderr
        assert filename in result.stderr


def test_fresh_explicit_off_disables_all_public_defaults(tmp_path):
    result, candidate = candidate_environment(
        tmp_path,
        overrides={"OPEN_NODE_PUBLIC_IP": "off", "OPEN_NODE_PUBLIC_HOSTNAME": ""},
    )
    assert result.returncode == 0, result.stderr
    values = parse_environment(candidate)
    assert values["OPEN_NODE_PUBLIC_IP"] == ""
    assert values["OPEN_NODE_PUBLIC_HTTPS_PORT"] == "58090"
    assert values["OPEN_NODE_PUBLIC_HOSTNAME"] == ""
    assert values["OPEN_NODE_SESSION_COOKIE_SECURE"] == "false"
    assert values["OPEN_NODE_TRUSTED_PROXIES"] == ""
    assert values["OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"] == ""


@pytest.mark.parametrize(
    "overrides,message",
    [
        (
            {
                "OPEN_NODE_PUBLIC_IP": "off",
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_BIND_ADDRESS": "0.0.0.0",
                "OPEN_NODE_ALLOW_PUBLIC_HTTP": "1",
            },
            "listener to remain on 127.0.0.1",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_IP": "off",
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            },
            "SESSION_COOKIE_SECURE=true",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_IP": "off",
                "OPEN_NODE_PUBLIC_HOSTNAME": "panel.example.com",
                "OPEN_NODE_TRUSTED_PROXIES": "127.0.0.1",
            },
            "TRUSTED_PROXIES=*",
        ),
        (
            {
                "OPEN_NODE_PUBLIC_IP": "off",
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
