"""Run only extracted enrollment functions; never run an installer deployment."""

import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "install.sh"


def functions(*names):
    source = INSTALLER.read_text()
    return "\n".join(re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.M | re.S)[0]
                     for name in names)


STUBS = """
INSTALL_DIR=/synthetic/source ENV_FILE=/synthetic/env ADMIN_USERNAME=admin
ADMIN_PASSWORD_FILE=''
log() { printf '%s\\n' "$*"; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
compose_with() { printf 'COMPOSE %s\\n' "$*"; }
"""


@pytest.mark.parametrize("mode", ["auto", "web"])
def test_default_browser_enrollment_does_not_prompt_for_password(mode):
    script = STUBS + functions("prepare_browser_setup", "create_administrator")
    result = subprocess.run(
        ["bash", "-eu", "-c", script + f"\nCREATE_ADMIN={mode}\ncreate_administrator"],
        input="", text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "exec -T open-node open-node-admin prepare-setup" in result.stdout
    assert "--password-stdin" not in result.stdout


def test_setup_checks_identity_health_and_propagates_failure():
    script = STUBS + functions("verify_administrator_action", "prepare_browser_setup") + """
require_no_recovery() { log recovery; }
require_manifest() { log manifest; }
require_environment_file() { log environment; }
read_manifest_value() { printf synthetic; }
verify_checkout() { log checkout; }
verify_active_identity() { log identity; }
verify_volume() { log volume; }
wait_for_health() { log health; return 1; }
verify_administrator_action
prepare_browser_setup
"""
    result = subprocess.run(
        ["bash", "-eu", "-c", script], text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 1
    assert result.stdout.splitlines() == ["recovery", "manifest", "environment", "checkout",
                                         "identity", "volume", "health"]
    assert "COMPOSE" not in result.stdout


def test_failed_cli_is_not_hidden_by_installer_conditional():
    script = STUBS + functions("prepare_browser_setup", "create_administrator") + """
compose_with() { return 1; }
CREATE_ADMIN=auto
[[ "$CREATE_ADMIN" == "0" ]] || create_administrator
log should-not-reach
"""
    result = subprocess.run(
        ["bash", "-eu", "-c", script], text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 1
    assert "签发失败" in result.stderr
    assert "should-not-reach" not in result.stdout


def test_terminal_password_file_remains_private_stdin(tmp_path):
    password_file = tmp_path / "password"
    password_file.write_text("test-only-setup-password\n")
    password_file.chmod(0o600)
    script = STUBS + functions("prepare_browser_setup", "create_administrator") + r'''
validate_absolute_path() { :; }
validate_safe_file() { :; }
stat() { if [[ "$2" == '%a' ]]; then printf 600; else printf 0; fi; }
compose_with() {
  [[ "$*" == *'open-node-admin create --username admin --password-stdin' ]]
  IFS= read -r secret
  [[ "$secret" == 'test-only-setup-password' ]]
  printf 'password-stdin-ok\n'
}
CREATE_ADMIN=auto
ADMIN_PASSWORD_FILE="$1"
create_administrator
'''
    result = subprocess.run(["bash", "-eu", "-c", script, "test", str(password_file)],
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "password-stdin-ok\n"


def test_installer_bash_syntax():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True, timeout=5)
