import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "uninstall.sh"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def policy_source() -> str:
    script = source()
    marker = "cat >\"$policy_file\" <<'PY'\n"
    assert marker in script
    return script.split(marker, 1)[1].split("\nPY\nchmod 0600", 1)[0]


def test_agent_uninstaller_has_valid_bash_and_embedded_python_syntax():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed on this development host")
    result = subprocess.run(
        [bash, "-n", str(SCRIPT)], text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    compile(policy_source(), "agent/uninstall.sh embedded policy", "exec")


def test_agent_uninstaller_is_strictly_local_interactive_and_defaults_to_purge():
    script = source()
    assert '[[ "${EUID}" -eq 0 ]]' in script
    assert "[[ -t 0 && -t 1 && -t 2 ]]" in script
    assert "是否彻底清除以上数据？[Y/n]" in script
    assert "''|y|Y|yes|YES|Yes) purge=1" in script
    assert "n|N|no|NO|No) purge=0" in script
    assert 'command+=(--purge)' in script
    assert "if ((purge)); then" in script
    assert "purge-jobs" in script
    assert "Disable or delete this server" in script
    assert not re.search(r"\b(?:curl|wget)\b", script)
    assert "rm -rf" not in script and "rm -fr" not in script


def test_agent_uninstaller_binds_installation_helper_and_private_job_identities():
    script = source()
    policy = policy_source()
    for required in (
        'record.get("root") == str(root)',
        'record.get("user") == unit.removesuffix(".service")',
        '"--unit does not match the installation manifest"',
        '"manifest_sha256"',
        '[[ "$revalidated" == "$selected" ]]',
        'sibling_helper="$script_directory/app/open_node_agent/service.py"',
        '"$(basename -- "$script_directory")" == "agent"',
        '-e "$checkout_root/.git" && ! -L "$checkout_root/.git"',
        'saved bootstrap service.py differs from the verified archive',
    ):
        assert required in script or required in policy
    assert 'request["root"] == root and request["unit"] == unit' in policy
    assert 'success.get("installation_id") == installation_id' in policy
    assert "hashlib.sha256(archive_data).hexdigest() == expected_sha" in policy
    assert "archived_helper == helper_data" in policy


def test_agent_uninstaller_purges_only_safe_exact_bootstrap_jobs_after_host_purge():
    script = source()
    policy = policy_source()
    assert 'stat.S_IMODE(info.st_mode) == 0o700' in policy
    assert 'parse_json(job / "request.json", private=True)' in policy
    assert 'request["root"] == root and request["unit"] == unit' in policy
    assert 'getattr(shutil.rmtree, "avoids_symlink_attacks", False)' in policy
    assert "os.O_NOFOLLOW" in policy
    assert '[[ ! -e "$selected_root" && ! -L "$selected_root" ]]' in script
    host_check = script.index('[[ ! -e "$selected_root" && ! -L "$selected_root" ]]')
    job_purge = script.rindex("purge-jobs")
    assert host_check < job_purge
    preserve_message = "recovery data and private bootstrap jobs were preserved"
    assert preserve_message in script


def test_agent_uninstaller_signal_handlers_exit_before_exit_trap_cleanup():
    script = source()
    assert "trap cleanup_policy EXIT" in script
    assert "trap 'exit 129' HUP" in script
    assert "trap 'exit 130' INT" in script
    assert "trap 'exit 143' TERM" in script
    assert "trap cleanup_policy EXIT HUP INT TERM" not in script
