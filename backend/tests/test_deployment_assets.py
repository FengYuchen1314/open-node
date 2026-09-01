import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_production_container_disables_uvicorn_access_log():
    dockerfile = (ROOT / "Dockerfile").read_text()
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD ["))
    command = json.loads(command_line.removeprefix("CMD "))

    assert command[:2] == ["uvicorn", "open_node.main:app"]
    assert "--no-access-log" in command


def test_nginx_example_disables_access_logs_for_redirect_and_tls_servers():
    nginx = (ROOT / "deploy/nginx.conf.example").read_text()
    servers = nginx.split("server {")[1:]

    assert len(servers) == 2
    assert all("access_log off;" in server for server in servers)
    assert all("error_log /var/log/nginx/open-node-error.log crit;" in server for server in servers)
    tls_server = servers[1]
    assert 'location ~ "^/x(?:/|$)"' in tls_server
    assert 'location ~ "^/api/v1/subscribe/(?![A-Za-z0-9_-]{43}$)"' in tls_server


def test_compose_bounds_local_container_logs():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())
    logging = compose["services"]["open-node"]["logging"]

    assert logging == {
        "driver": "local",
        "options": {"max-size": "10m", "max-file": "5"},
    }


def test_compose_defaults_to_loopback_but_allows_an_explicit_installer_bind():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())

    assert compose["services"]["open-node"]["ports"] == [
        "${OPEN_NODE_BIND_ADDRESS:-127.0.0.1}:${OPEN_NODE_HTTP_PORT:-8080}:8080"
    ]
    assert compose["services"]["open-node"]["image"] == (
        "${OPEN_NODE_IMAGE_REPOSITORY:-open-node}:${OPEN_NODE_IMAGE_TAG:-local}"
    )
    assert "OPEN_NODE_BIND_ADDRESS=127.0.0.1" in (
        ROOT / "deploy/.env.example"
    ).read_text()


def test_managed_public_gateway_is_pinned_and_keeps_the_app_on_loopback():
    installer = (ROOT / "install.sh").read_text()
    caddyfile = (ROOT / "deploy/Caddyfile").read_text()
    environment = (ROOT / "deploy/.env.example").read_text()

    assert (
        'PUBLIC_GATEWAY_IMAGE="caddy:2.11.4-alpine@sha256:'
        '5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"'
        in installer
    )
    assert "OPEN_NODE_PUBLIC_HOSTNAME=" in environment
    assert "admin off" in caddyfile
    assert "{$OPEN_NODE_PUBLIC_HOSTNAME}" in caddyfile
    assert "reverse_proxy 127.0.0.1:{$OPEN_NODE_UPSTREAM_PORT}" in caddyfile
    assert "header_up X-Forwarded-For" not in caddyfile
    assert "header_up X-Real-IP {remote_host}" in caddyfile
    assert "health_uri /healthz" in caddyfile
    assert "tls internal" not in caddyfile
    assert "--network host" in installer
    assert "--read-only --init" in installer
    assert "--cap-drop ALL --cap-add NET_BIND_SERVICE" in installer
    assert '--resolve "$hostname:443:127.0.0.1"' in installer


def test_public_gateway_docker_policy_smoke_never_starts_its_fixture():
    smoke = ROOT / "scripts/vps/smoke-installer-public-gateway.py"
    source = smoke.read_text()

    compile(source, str(smoke), "exec")
    assert '        "create",\n        "--name",' in source
    assert '"docker", "run"' not in source
    assert "public_gateway_container_is_safe 0" in source
    assert "public_gateway_volume_is_safe" in source
    assert "Refusing to remove" in source


def test_github_installer_has_safe_lifecycle_defaults_and_valid_bash():
    installer = ROOT / "install.sh"
    source = installer.read_text()

    result = subprocess.run(
        ["bash", "-n", str(installer)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert source.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n")
    assert 'DEFAULT_REPOSITORY="https://github.com/FengYuchen1314/open-node.git"' in source
    assert "install|update|status|uninstall" in source
    assert "OPEN_NODE_IMAGE_REPOSITORY" in source
    assert "OPEN_NODE_BIND_ADDRESS" in source
    assert "127.0.0.1" in source
    assert 'OPEN_NODE_ALLOW_PUBLIC_HTTP:-0' in source
    assert "open-node-admin create" in source
    assert "installer.recovery" in source
    assert "deployment.meta" in source
    assert "volume inspect" in source
    assert "down --remove-orphans" in source
    assert "down --volumes" not in source
    assert "rm -rf" not in source
    assert '--project-directory "$source_dir/deploy"' in source
    assert 'project.working_dir"] == ($source + "/deploy")' in source
    active_identity = source[
        source.index("verify_active_identity() {") : source.index("\nwrite_manifest() {")
    ]
    assert "project_runtime_is_absent" in active_identity
    update_source = source[
        source.index("update_existing() {") : source.index("\nshow_status() {")
    ]
    assert "verify_active_identity 1" in update_source
    status_source = source[
        source.index("show_status() {") : source.index(
            "\nuninstall_preserving_data() {"
        )
    ]
    assert status_source.index('[[ -e "$RECOVERY_FILE"') < status_source.index(
        "require_manifest"
    )
    assert "verify_active_identity 1" in status_source
    assert 'case "$state" in' in status_source
    assert "deployment container is stopped" in status_source
    assert "deployment container is absent; managed data is preserved" in status_source


def test_installer_interruption_cleanup_preserves_recoverable_transaction_state():
    source = (ROOT / "install.sh").read_text()
    marker_cleanup = source[
        source.index("clear_candidate_recovery_marker() {") : source.index(
            "\nacquire_lock() {"
        )
    ]
    exit_cleanup = source[
        source.index("cleanup_transaction_on_exit() {") : source.index(
            "\ntrap cleanup_transaction_on_exit EXIT"
        )
    ]
    backup = source[
        source.index("backup_stopped_volume() {") : source.index(
            "\ncreate_administrator() {"
        )
    ]

    assert 'read_key "$RECOVERY_FILE" CANDIDATE_REVISION' in marker_cleanup
    assert 'read_key "$RECOVERY_FILE" CANDIDATE_IMAGE_TAG' in marker_cleanup
    assert "clear_recovery_marker" in marker_cleanup
    candidate_built_cleanup = exit_cleanup[
        exit_cleanup.index("prepared|candidate-built)") : exit_cleanup.index(
            "\n    *)"
        )
    ]
    assert "clear_candidate_recovery_marker" in candidate_built_cleanup
    assert (
        '( ensure_private_directory "OPEN_NODE_BACKUP_DIR" "$BACKUP_DIR" ) || return 1'
        in backup
    )
    backup_lines = backup.splitlines()
    assignment_index = backup_lines.index(
        '  BACKUP_PATH="$final_bundle" TXN_BACKUP="$final_bundle" \\'
    )
    assert backup_lines[assignment_index + 1] == (
        '    TXN_TEMP_BACKUP="" TXN_ROLLBACK_IMAGE=""'
    )


def test_installer_vps_smoke_keeps_destructive_resources_isolated():
    smoke = ROOT / "scripts/vps/smoke-control-plane-installer.py"
    source = smoke.read_text()

    compile(source, str(smoke), "exec")
    assert 'dir="/root"' in source
    assert "OPEN_NODE_IMAGE_REPOSITORY" in source
    assert "OPEN_NODE_PROJECT_NAME" in source
    assert "fixture.volume" in source
    assert "run_missing_volume_scenario" in source
    assert "run_concurrent_lock_scenario" in source
    assert "run_interruption_scenario" in source
    assert "OPEN_NODE_SMOKE_FAIL_BACKUP" in source
    assert "assert_rendered_fixture_namespace" in source
    assert "assert_candidate_compose_isolated" in source
    assert "assert_preflight_rejects_host_bind" in source
    assert "verify_backup_restore" in source
    assert "PRAGMA integrity_check" in source
    assert "SHA256SUMS" in source
    assert "mutate_then_fail=True" in source
    assert "backend/app/open_node/main.py" in source
    assert "/installer-smoke-mutate.py" not in source
    assert "backup-restore-smoke" in source
    assert "OPEN_NODE_SMOKE_FAIL_COMPOSE_DOWN" in source
    assert "OPEN_NODE_SMOKE_FAIL_PROJECT_RM" in source
    assert "containment-failed-" in source
    assert 'phase="compose-up-after"' in source
    assert "run_active_identity_drift_scenarios" in source
    assert "environment, tag, manifest, container, and mount drift" in source
    cleanup_source = source[
        source.index("    def cleanup(self) -> None:") : source.index(
            "\ndef assert_fixture_prerequisites"
        )
    ]
    assert '"down"' not in cleanup_source
    assert '"--volumes"' not in source
    assert "inspect_expected_container" in cleanup_source
    assert "inspect_expected_backup_container" in cleanup_source
    assert "backup-helper" in source
    assert "inspect_expected_network" in cleanup_source
    assert "inspect_expected_volume" in cleanup_source
    assert "org.opencontainers.image.revision" not in cleanup_source
    assert '"image",\n                "rm",\n                "-f"' not in cleanup_source
    assert "fixture resources remain after cleanup" in source
