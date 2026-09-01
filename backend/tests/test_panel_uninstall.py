"""Static safety contract for the interactive panel uninstaller."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_panel_uninstall_entrypoint_is_interactive_and_defaults_to_purge():
    entrypoint = ROOT / "uninstall.sh"
    source = entrypoint.read_text(encoding="utf-8")
    result = subprocess.run(
        ["bash", "-n", str(entrypoint)],
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert source.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n")
    assert "[[ -t 0 && -t 1 && -t 2 ]]" in source
    assert "是否彻底清除以上数据？[Y/n]" in source
    assert '""|y|yes)' in source
    assert "OPEN_NODE_PURGE_CONFIRMED=YES" in source
    assert 'exec bash "$INSTALLER" purge' in source
    assert "n|no)" in source
    assert 'exec bash "$INSTALLER" uninstall' in source
    assert "rm -rf" not in source
    assert "curl" not in source


def test_panel_purge_reuses_installer_identity_and_removes_only_managed_targets():
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    purge = source[
        source.index("\npurge_installation() {") : source.index(
            "\nverify_administrator_action() {"
        )
    ]

    assert '[[ "${OPEN_NODE_PURGE_CONFIRMED:-}" == "YES" ]]' in purge
    for guard in (
        "require_no_recovery",
        "require_manifest",
        "require_environment_file",
        "verify_checkout",
        "verify_active_identity 1",
        "project_runtime_is_absent",
        "volume_is_safe",
        "postgres_volume_is_safe",
        "public_gateway_volume_is_safe",
        "public_gateway_container_is_safe 0 0",
    ):
        assert guard in purge
    for exact_target in (
        'docker volume rm -- "$DATA_VOLUME"',
        'docker volume rm -- "$POSTGRES_VOLUME"',
        'docker volume rm -- "$PUBLIC_GATEWAY_VOLUME"',
        'remove_managed_tree "application update state" "$UPDATE_STATE_DIR" update-state',
        'remove_managed_tree "backup directory" "$BACKUP_DIR" private',
        'remove_managed_tree "configuration directory" "$CONFIG_DIR" private',
        'remove_managed_tree "installed source" "$INSTALL_DIR" public',
    ):
        assert exact_target in purge
    assert "rm -rf" not in purge
    assert 'find "$directory" -xdev -depth -delete' in source
    assert "purge) purge_installation" in source
