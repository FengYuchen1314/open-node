"""Installer progress reporting without starting a real deployment."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"


def installer_definitions(tmp_path):
    source = INSTALLER.read_text(encoding="utf-8")
    definitions = source[: source.rindex('\nmain "$@"')]
    path = tmp_path / "installer-progress-definitions.sh"
    path.write_text(definitions, encoding="utf-8")
    path.chmod(0o600)
    return path


def run_bash(script, *, definitions, arguments=(), overrides=None):
    environment = os.environ.copy()
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", "-c", script, "progress-test", str(definitions), *map(str, arguments)],
        text=True,
        capture_output=True,
        timeout=10,
        env=environment,
    )


def progress_elapsed(stdout, phase, status="waiting"):
    return [
        int(value)
        for value in re.findall(
            rf"PROGRESS phase={re.escape(phase)} status={status} elapsed=([0-9]+)s",
            stdout,
        )
    ]


def test_application_health_uses_elapsed_deadline_and_newline_heartbeats(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        r'''
source "$1"
trap - EXIT INT TERM HUP
PROBES=0
read_key() {
  case "$2" in
    OPEN_NODE_HTTP_PORT) printf '%s\n' 62031 ;;
    OPEN_NODE_BIND_ADDRESS) printf '%s\n' 127.0.0.1 ;;
    *) return 1 ;;
  esac
}
runtime_container_is_safe() { return 0; }
curl() {
  local argument previous='' probe_timeout=0
  ((PROBES += 1))
  for argument in "$@"; do
    if [[ "$previous" == '--max-time' ]]; then probe_timeout="$argument"; fi
    previous="$argument"
  done
  (( probe_timeout > 0 )) || return 2
  SECONDS=$((SECONDS + probe_timeout))
  return 1
}
sleep() { SECONDS=$((SECONDS + $1)); }
compose_with() { :; }
SECONDS=0
if wait_for_health /synthetic/source /synthetic/environment sha256:expected; then
  result=0
else
  result=$?
fi
printf 'RESULT=%s PROBES=%s ELAPSED=%s\n' "$result" "$PROBES" "$SECONDS"
''',
        definitions=definitions,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT=1" in result.stdout
    probes = int(re.search(r"PROBES=([0-9]+)", result.stdout).group(1))
    elapsed = int(re.search(r"ELAPSED=([0-9]+)", result.stdout).group(1))
    assert probes < 40
    assert 90 <= elapsed <= 93
    heartbeats = progress_elapsed(result.stdout, "application-health")
    assert heartbeats[0] == 0
    assert len(heartbeats) >= 10
    assert progress_elapsed(result.stdout, "application-health", "timeout")
    assert "ACTION_COMPLETE" not in result.stdout


def test_database_health_uses_elapsed_deadline_and_newline_heartbeats(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        r'''
source "$1"
trap - EXIT INT TERM HUP
PROBES=0
read_key() { printf '%s\n' postgresql; }
compose_with() { :; }
postgres_volume_is_safe() {
  ((PROBES += 1))
  SECONDS=$((SECONDS + 6))
  return 1
}
network_is_safe() { return 0; }
postgres_container_is_safe() { return 0; }
sleep() { SECONDS=$((SECONDS + $1)); }
SECONDS=0
if ensure_database_ready /synthetic/source /synthetic/environment; then
  result=0
else
  result=$?
fi
printf 'RESULT=%s PROBES=%s ELAPSED=%s\n' "$result" "$PROBES" "$SECONDS"
''',
        definitions=definitions,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT=1" in result.stdout
    probes = int(re.search(r"PROBES=([0-9]+)", result.stdout).group(1))
    elapsed = int(re.search(r"ELAPSED=([0-9]+)", result.stdout).group(1))
    assert probes < 20
    assert 90 <= elapsed <= 96
    heartbeats = progress_elapsed(result.stdout, "postgres-health")
    assert heartbeats[0] == 0
    assert len(heartbeats) >= 10
    assert progress_elapsed(result.stdout, "postgres-health", "timeout")


def test_public_gateway_uses_elapsed_deadline_and_newline_heartbeats(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        r'''
source "$1"
trap - EXIT INT TERM HUP
PROBES=0
public_gateway_container_is_safe() { return 0; }
public_gateway_endpoints_are_healthy() {
  local deadline="$1" remaining step=11
  ((PROBES += 1))
  remaining=$((deadline - SECONDS))
  if (( remaining < step )); then step="$remaining"; fi
  (( step > 0 )) || return 1
  SECONDS=$((SECONDS + step))
  return 1
}
sleep() { SECONDS=$((SECONDS + $1)); }
docker() { :; }
SECONDS=0
if wait_for_public_gateway; then result=0; else result=$?; fi
printf 'RESULT=%s PROBES=%s ELAPSED=%s\n' "$result" "$PROBES" "$SECONDS"
''',
        definitions=definitions,
    )

    assert result.returncode == 0, result.stderr
    assert "RESULT=1" in result.stdout
    probes = int(re.search(r"PROBES=([0-9]+)", result.stdout).group(1))
    elapsed = int(re.search(r"ELAPSED=([0-9]+)", result.stdout).group(1))
    assert probes < 30
    assert elapsed == 180
    heartbeats = progress_elapsed(result.stdout, "public-https")
    assert heartbeats[0] == 0
    assert len(heartbeats) >= 10
    assert progress_elapsed(result.stdout, "public-https", "timeout")


def test_successful_health_waits_keep_three_stable_observations(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        r'''
source "$1"
trap - EXIT INT TERM HUP
APP_PROBES=0 PUBLIC_PROBES=0
read_key() {
  case "$2" in
    OPEN_NODE_HTTP_PORT) printf '%s\n' 62031 ;;
    OPEN_NODE_BIND_ADDRESS) printf '%s\n' 127.0.0.1 ;;
  esac
}
runtime_container_is_safe() { return 0; }
curl() { ((APP_PROBES += 1)); return 0; }
public_gateway_container_is_safe() { return 0; }
public_gateway_endpoints_are_healthy() { ((PUBLIC_PROBES += 1)); return 0; }
sleep() { SECONDS=$((SECONDS + $1)); }
SECONDS=0
wait_for_health /synthetic/source /synthetic/environment sha256:expected
wait_for_public_gateway
printf 'APP_PROBES=%s PUBLIC_PROBES=%s\n' "$APP_PROBES" "$PUBLIC_PROBES"
''',
        definitions=definitions,
    )

    assert result.returncode == 0, result.stderr
    assert "APP_PROBES=3 PUBLIC_PROBES=3" in result.stdout
    assert progress_elapsed(result.stdout, "application-health", "ready")
    assert progress_elapsed(result.stdout, "public-https", "ready")
    assert "status=timeout" not in result.stdout


def test_action_complete_is_unique_final_success_marker(tmp_path):
    definitions = installer_definitions(tmp_path)
    common = r'''
source "$1"
trap - EXIT INT TERM HUP
ACTION=status
require_root() { :; }
load_manifest_defaults() { :; }
validate_inputs() { :; }
validate_safe_directory() { :; }
ensure_private_directory() { :; }
acquire_lock() { :; }
ensure_dependencies() { :; }
acquire_global_lock() { :; }
show_status() { %s; }
main
'''
    success = run_bash(common % "log ACTION_BODY", definitions=definitions)
    failure = run_bash(common % "return 7", definitions=definitions)

    assert success.returncode == 0, success.stderr
    assert success.stdout.splitlines()[-2:] == [
        "[open-node] ACTION_BODY",
        "[open-node] ACTION_COMPLETE action=status",
    ]
    assert success.stdout.count("ACTION_COMPLETE") == 1
    assert failure.returncode == 7
    assert "ACTION_COMPLETE" not in failure.stdout


def test_exit_trap_turns_silent_early_success_into_an_explicit_failure(tmp_path):
    definitions = installer_definitions(tmp_path)
    result = run_bash(
        'source "$1"; TXN_PHASE=idle; exit 0',
        definitions=definitions,
    )

    assert result.returncode == 1
    assert "ACTION_INCOMPLETE action=" in result.stderr
    assert "status=0" in result.stderr
    assert "installer exited before completion" in result.stderr


def test_progress_contract_is_static_and_gateway_pull_is_visible():
    source = INSTALLER.read_text(encoding="utf-8")
    assert source.count('log "ACTION_COMPLETE action=$ACTION"') == 1
    assert source.count("ACTION_COMPLETE_REACHED=1") == 1
    assert "ACTION_INCOMPLETE action=$ACTION" in source

    main_start = source.rindex("\nmain() {")
    main_end = source.index('\n}\n\nmain "$@"', main_start)
    main = source[main_start:main_end]
    assert main.index("\n  esac") < main.index("ACTION_COMPLETE")
    assert main.rstrip().endswith('log "ACTION_COMPLETE action=$ACTION"')

    for constant, value in (
        ("WAIT_HEARTBEAT_SECONDS", "5"),
        ("DATABASE_READY_TIMEOUT_SECONDS", "90"),
        ("APPLICATION_HEALTH_TIMEOUT_SECONDS", "90"),
        ("PUBLIC_GATEWAY_TIMEOUT_SECONDS", "180"),
    ):
        assert f'readonly {constant}="{value}"' in source

    function_boundaries = (
        ("ensure_database_ready", "wait_for_health"),
        ("wait_for_health", "public_gateway_volume_is_safe"),
        ("wait_for_public_gateway", "preflight_fresh_ports"),
    )
    for name, next_name in function_boundaries:
        body = source[source.index(f"\n{name}() {{") : source.index(f"\n{next_name}() {{")]
        assert "while (( SECONDS < deadline ))" in body
        assert "PROGRESS phase=" in body
        assert "for attempt in $(seq" not in body

    image_start = source.index("\nensure_public_gateway_image() {")
    image_end = source.index("\nwait_for_public_gateway() {", image_start)
    image_body = source[image_start:image_end]
    assert 'docker pull "$PUBLIC_GATEWAY_IMAGE"' in image_body
    assert 'docker pull "$PUBLIC_GATEWAY_IMAGE" >/dev/null' not in image_body
