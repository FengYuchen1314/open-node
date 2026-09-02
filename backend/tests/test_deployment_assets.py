import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_production_container_disables_uvicorn_access_log():
    dockerfile = (ROOT / "Dockerfile").read_text()
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD ["))
    command = json.loads(command_line.removeprefix("CMD "))

    assert command[:2] == ["uvicorn", "open_node.main:app"]
    assert command[command.index("--port") + 1] == "62031"
    assert "--no-access-log" in command
    assert "EXPOSE 62031" in dockerfile
    assert "http://127.0.0.1:62031/healthz" in dockerfile


def test_production_build_does_not_fetch_a_remote_dockerfile_frontend():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert not dockerfile.startswith("# syntax=")
    assert "COPY --chmod=" not in dockerfile
    assert "COPY scripts/container/entrypoint.sh /usr/local/bin/open-node-entrypoint" in dockerfile
    assert "RUN chmod 0755 /usr/local/bin/open-node-entrypoint" in dockerfile


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


def test_postgres_overlay_is_digest_pinned_private_and_health_checked():
    overlay = yaml.safe_load((ROOT / "deploy/compose.postgresql.yaml").read_text())
    service = overlay["services"]["postgres"]
    expected_image = (
        "postgres:15.18-bookworm@sha256:"
        "e8db9bd3e9e1751eb639fb17be53cc6d1b62a322adf75b99e791767a7a16ce69"
    )

    assert service["image"] == expected_image
    assert "ports" not in service
    assert service["init"] is True
    assert service["restart"] == "unless-stopped"
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["volumes"] == ["postgres-data:/var/lib/postgresql/data"]
    assert service["stop_grace_period"] == "1m"
    assert service["healthcheck"] == {
        "test": [
            "CMD-SHELL",
            'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"',
        ],
        "interval": "5s",
        "timeout": "5s",
        "retries": 20,
        "start_period": "10s",
    }
    assert f'POSTGRES_IMAGE="{expected_image}"' in (ROOT / "install.sh").read_text(
        encoding="utf-8"
    )


def test_application_update_bridge_is_fixed_function_and_has_no_docker_socket_mount():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())
    service = compose["services"]["open-node"]
    mounts = service["volumes"]
    helper = ROOT / "deploy/application_update_helper.py"
    source = helper.read_text()

    compile(source, str(helper), "exec")
    assert any(
        isinstance(item, dict) and item["target"] == "/run/open-node-maintenance"
        for item in mounts
    )
    assert all("docker.sock" not in str(item) for item in mounts)
    assert "OPEN_NODE_EXPECTED_REVISION" in source
    assert '["bash", str(installer), "update"]' in source
    assert "shell=True" not in source
    assert "OFFICIAL_REPOSITORY" in source and 'OFFICIAL_REF = "main"' in source
    assert 'INSTALLER_MANIFEST_VERSION = "2"' in source
    assert 'RUNTIME_CONTAINER_PORT = "62031"' in source
    assert '"MANIFEST_VERSION": INSTALLER_MANIFEST_VERSION' in source
    assert '"DEPLOYED_RUNTIME_PORT": RUNTIME_CONTAINER_PORT' in source


def test_compose_defaults_to_loopback_but_allows_an_explicit_installer_bind():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())

    assert compose["services"]["open-node"]["ports"] == [
        "${OPEN_NODE_BIND_ADDRESS:-127.0.0.1}:${OPEN_NODE_HTTP_PORT:-62031}:62031"
    ]
    assert compose["services"]["open-node"]["image"] == (
        "${OPEN_NODE_IMAGE_REPOSITORY:-open-node}:${OPEN_NODE_IMAGE_TAG:-local}"
    )
    assert "OPEN_NODE_BIND_ADDRESS=127.0.0.1" in (
        ROOT / "deploy/.env.example"
    ).read_text()
    assert "OPEN_NODE_HTTP_PORT=62031" in (ROOT / "deploy/.env.example").read_text()
    assert "OPEN_NODE_TRUSTED_AUTHORITIES=[]" in (
        ROOT / "deploy/.env.example"
    ).read_text()
    assert compose["services"]["open-node"]["environment"][
        "OPEN_NODE_TRUSTED_AUTHORITIES"
    ] == "${OPEN_NODE_TRUSTED_AUTHORITIES:-[]}"
    restore = yaml.safe_load(
        (ROOT / "deploy/compose.restore.example.yaml").read_text(encoding="utf-8")
    )
    assert restore["services"]["open-node"]["environment"][
        "OPEN_NODE_TRUSTED_AUTHORITIES"
    ] == "${OPEN_NODE_TRUSTED_AUTHORITIES:-[]}"


def test_manual_restore_guidance_resets_authority_trust_for_private_staging():
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")

    assert "Set `OPEN_NODE_TRUSTED_AUTHORITIES=[]`" in deployment
    assert "printf 'OPEN_NODE_TRUSTED_AUTHORITIES=[]\\n'" in deployment


def test_installer_manifest_v2_records_the_fixed_62031_runtime_contract():
    installer = (ROOT / "install.sh").read_text()
    manifest_check = installer[
        installer.index("require_manifest() {") : installer.index(
            "\nverify_active_identity() {"
        )
    ]
    manifest_write = installer[
        installer.index("write_manifest() {") : installer.index("\nwrite_recovery_marker() {")
    ]
    reinstall_start = installer.index("reinstall_existing() {")
    reinstall_running = installer[
        installer.index('if [[ "$state" == "running" ]]', reinstall_start) : installer.index(
            'CANDIDATE_SOURCE="$INSTALL_DIR"', reinstall_start
        )
    ]
    reinstall = installer[
        reinstall_start : installer.index("\nupdate_existing() {", reinstall_start)
    ]
    host_integrations = installer[
        installer.index("provision_committed_host_integrations() {") : installer.index(
            "\nverify_public_gateway_status() {"
        )
    ]

    assert 'readonly MANIFEST_VERSION="2"' in installer
    assert 'readonly RUNTIME_CONTAINER_PORT="62031"' in installer
    assert 'readonly DEFAULT_PUBLIC_HTTPS_PORT="58090"' in installer
    assert 'read_manifest_value DEPLOYED_RUNTIME_PORT' in manifest_check
    assert '== "$RUNTIME_CONTAINER_PORT"' in manifest_check
    assert "unsupported or damaged installer manifest" in manifest_check
    assert "printf 'DEPLOYED_RUNTIME_PORT=%s\\n' \"$RUNTIME_CONTAINER_PORT\"" in manifest_write
    assert host_integrations.index("reconcile_public_gateway") < host_integrations.index(
        "provision_application_update_helper"
    )
    assert "provision_committed_host_integrations" in reinstall_running
    assert (
        reinstall_running.index("provision_committed_host_integrations")
        < reinstall_running.index("ensure_administrator_setup")
        < reinstall_running.index("log_success")
    )
    assert reinstall.count("ensure_administrator_setup") == 2
    assert reinstall.rindex("provision_committed_host_integrations") < reinstall.rindex(
        "ensure_administrator_setup"
    ) < reinstall.rindex("log_success")


def test_managed_public_gateway_is_pinned_and_keeps_the_app_on_loopback():
    installer = (ROOT / "install.sh").read_text()
    environment = (ROOT / "deploy/.env.example").read_text()
    caddyfiles = {
        "domain": (ROOT / "deploy/Caddyfile").read_text(),
        "ip": (ROOT / "deploy/Caddyfile.ip").read_text(),
        "dual": (ROOT / "deploy/Caddyfile.dual").read_text(),
    }

    assert (
        'PUBLIC_GATEWAY_IMAGE="caddy:2.11.4-alpine@sha256:'
        '5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"'
        in installer
    )
    assert "OPEN_NODE_PUBLIC_HOSTNAME=" in environment
    assert "OPEN_NODE_PUBLIC_IP=auto" in environment
    assert "OPEN_NODE_PUBLIC_HTTPS_PORT=58090" in environment
    for caddyfile in caddyfiles.values():
        assert "admin off" in caddyfile
        assert "auto_https disable_redirects" in caddyfile
        assert "issuer acme" in caddyfile
        assert "dir https://acme-v02.api.letsencrypt.org/directory" in caddyfile
        assert "disable_http_challenge" in caddyfile
        assert "encode zstd gzip" in caddyfile
        assert "-Server" in caddyfile
        assert 'Strict-Transport-Security "max-age=31536000"' in caddyfile
        assert "reverse_proxy 127.0.0.1:{$OPEN_NODE_UPSTREAM_PORT}" in caddyfile
        assert "header_up X-Forwarded-For" not in caddyfile
        assert "header_up X-Real-IP {remote_host}" in caddyfile
        assert "health_uri /healthz" in caddyfile
        assert "health_interval 30s" in caddyfile
        assert "tls internal" not in caddyfile
        assert "local_certs" not in caddyfile
        assert "self_signed" not in caddyfile

    assert "{$OPEN_NODE_PUBLIC_HOSTNAME}" in caddyfiles["domain"]
    assert "OPEN_NODE_PUBLIC_IP_AUTHORITY" not in caddyfiles["domain"]
    assert "listener_wrappers" not in caddyfiles["domain"]
    assert "http_redirect" not in caddyfiles["domain"]
    assert "profile shortlived" not in caddyfiles["domain"]
    assert caddyfiles["domain"].count("reverse_proxy ") == 1

    ip_site = "https://{$OPEN_NODE_PUBLIC_IP_AUTHORITY}:{$OPEN_NODE_PUBLIC_HTTPS_PORT}"
    redirect_listener = """servers :{$OPEN_NODE_PUBLIC_HTTPS_PORT} {
		listener_wrappers {
			http_redirect
			tls
		}
	}"""
    assert ip_site in caddyfiles["ip"]
    assert redirect_listener in caddyfiles["ip"]
    assert caddyfiles["ip"].count("http_redirect") == 1
    assert "OPEN_NODE_PUBLIC_HOSTNAME" not in caddyfiles["ip"]
    assert caddyfiles["ip"].count("profile shortlived") == 1
    assert caddyfiles["ip"].count("reverse_proxy ") == 1

    assert "{$OPEN_NODE_PUBLIC_HOSTNAME}" in caddyfiles["dual"]
    assert ip_site in caddyfiles["dual"]
    assert redirect_listener in caddyfiles["dual"]
    assert caddyfiles["dual"].count("http_redirect") == 1
    assert caddyfiles["dual"].count("issuer acme") == 2
    assert caddyfiles["dual"].count("disable_http_challenge") == 2
    assert caddyfiles["dual"].count("profile shortlived") == 1
    assert "profile shortlived" not in caddyfiles["dual"].split(ip_site, 1)[0]
    assert caddyfiles["dual"].count("reverse_proxy ") == 2
    assert "--network host" in installer
    assert "--read-only --init" in installer
    assert "--cap-drop ALL --cap-add NET_BIND_SERVICE" in installer
    for variable in [
        "OPEN_NODE_GATEWAY_MODE",
        "OPEN_NODE_PUBLIC_HOSTNAME",
        "OPEN_NODE_PUBLIC_IP_AUTHORITY",
        "OPEN_NODE_PUBLIC_HTTPS_PORT",
        "OPEN_NODE_UPSTREAM_PORT",
    ]:
        assert f'--env "{variable}=' in installer
    assert '--resolve "$hostname:443:127.0.0.1"' in installer

    health_check = installer[
        installer.index("public_gateway_endpoints_are_healthy() {") : installer.index(
            "\nreconcile_public_gateway() {"
        )
    ]
    assert "public_ip_url" in health_check
    assert "--connect-to" not in health_check
    assert '"$public_url/healthz"' in health_check
    assert "curl -k" not in health_check
    assert "--insecure" not in health_check


def test_public_gateway_docker_policy_smoke_never_starts_its_fixture():
    smoke = ROOT / "scripts/vps/smoke-installer-public-gateway.py"
    source = smoke.read_text()

    compile(source, str(smoke), "exec")
    assert '        "create",\n        "--name",' in source
    assert '"docker", "run"' not in source
    assert "public_gateway_container_is_safe 0" in source
    assert "public_gateway_volume_is_safe" in source
    assert 'OPEN_NODE_TRUSTED_AUTHORITIES=["127.0.0.1:62031",' in source
    assert '"panel.example.com","1.1.1.1:58090"]' in source
    assert "Refusing to remove" in source


def test_vps_backend_smoke_environments_do_not_inherit_authority_policy():
    smoke_directory = ROOT / "scripts/vps"
    audited = []
    for smoke in smoke_directory.glob("*.py"):
        source = smoke.read_text(encoding="utf-8")
        if "OPEN_NODE_DATABASE_URL" not in source and "OPEN_NODE_FRONTEND_DIR" not in source:
            continue
        audited.append(smoke.name)
        assert "OPEN_NODE_TRUSTED_AUTHORITIES" in source, smoke.name

    assert "smoke-control-plane.py" in audited
    assert "smoke-branding-browser.py" in audited
    assert "smoke-nginx.py" in audited


def test_backend_subprocess_environment_fixtures_pin_private_authority_policy():
    audited = []
    for test_module in (ROOT / "backend/tests").glob("test_*.py"):
        source = test_module.read_text(encoding="utf-8")
        if "OPEN_NODE_DATABASE_URL" not in source:
            continue
        audited.append(test_module.name)
        assert "OPEN_NODE_TRUSTED_AUTHORITIES" in source, test_module.name

    assert "test_auth.py" in audited
    assert "test_backup_restore.py" in audited
    assert "test_initial_setup.py" in audited


def test_ci_uses_parallel_backend_and_frontend_shards():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    backend = workflow["jobs"]["backend"]
    frontend = workflow["jobs"]["frontend"]
    lint = workflow["jobs"]["backend-lint"]
    runner = ROOT / "scripts/ci/run_backend_test_shard.py"

    assert backend["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": list(range(18))},
    }
    backend_commands = [step.get("run", "") for step in backend["steps"]]
    assert any(
        "run_backend_test_shard.py" in command
        and "--shard-count 18" in command
        and "--shard-index ${{ matrix.shard }}" in command
        for command in backend_commands
    )
    assert all("ruff check" not in command for command in backend_commands)
    assert sum("ruff check backend" in step.get("run", "") for step in lint["steps"]) == 1
    all_commands = [
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
    ]
    assert sum("ruff check backend" in command for command in all_commands) == 1

    result = subprocess.run(
        [sys.executable, str(runner), "--check-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    test_file_count = len(list((ROOT / "backend/tests").rglob("test_*.py")))
    assert f"{test_file_count} files across 18 shards" in result.stdout
    assert "shard 0: 1 files" in result.stdout
    assert "(test_inventory.py)" in result.stdout

    assert frontend["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": list(range(1, 13))},
    }
    frontend_steps = frontend["steps"]
    assert any(
        step.get("run") == "npm test -- --shard=${{ matrix.shard }}/12"
        for step in frontend_steps
    )
    for command in ("npm run build", "npm run build:probe", "test -f dist-probe/index.html"):
        step = next(item for item in frontend_steps if item.get("run") == command)
        assert step["if"] == "matrix.shard == 1"


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
    assert '"OPEN_NODE_TRUSTED_AUTHORITIES": "[]"' in source
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
