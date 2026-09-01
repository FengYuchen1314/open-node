import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40


@pytest.fixture
def helper():
    path = ROOT / "deploy/application_update_helper.py"
    specification = importlib.util.spec_from_file_location(
        "application_update_helper_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def helper_config(tmp_path: Path) -> dict[str, object]:
    return {
        "project_name": "open-node",
        "repository": "https://github.com/FengYuchen1314/open-node.git",
        "ref": "main",
        "install_dir": tmp_path / "install",
        "config_dir": tmp_path / "config",
        "backup_dir": tmp_path / "backup",
        "image_repository": "open-node",
        "state_dir": tmp_path / "state",
        "runtime_uid": 10001,
        "runtime_gid": 10001,
    }


def manifest_bytes(config: dict[str, object], **changes: str | None) -> bytes:
    values = {
        "MANIFEST_VERSION": "2",
        "REPOSITORY": str(config["repository"]),
        "REF": "main",
        "INSTALL_DIR": str(config["install_dir"]),
        "CONFIG_DIR": str(config["config_dir"]),
        "BACKUP_DIR": str(config["backup_dir"]),
        "PROJECT_NAME": "open-node",
        "IMAGE_REPOSITORY": "open-node",
        "DEPLOYED_REVISION": REVISION,
        "DEPLOYED_RUNTIME_PORT": "62031",
    }
    values.update(changes)
    return "".join(
        f"{key}={value}\n" for key, value in values.items() if value is not None
    ).encode()


def test_initialize_accepts_current_installer_manifest(helper, monkeypatch, tmp_path):
    config = helper_config(tmp_path)
    content = manifest_bytes(config)
    written = []
    monkeypatch.setattr(helper, "safe_root_file", lambda _path, _maximum: content)
    monkeypatch.setattr(
        helper, "write_state", lambda actual, payload: written.append((actual, payload))
    )

    helper.initialize(config)

    assert written[0][0] is config
    assert written[0][1]["status"] == "idle"
    assert written[0][1]["current_revision"] == REVISION


@pytest.mark.parametrize(
    "changes",
    [
        {"MANIFEST_VERSION": "1"},
        {"DEPLOYED_RUNTIME_PORT": "8080"},
        {"DEPLOYED_RUNTIME_PORT": None},
    ],
)
def test_manifest_rejects_wrong_runtime_contract(
    helper, monkeypatch, tmp_path, changes
):
    config = helper_config(tmp_path)
    content = manifest_bytes(config, **changes)
    monkeypatch.setattr(helper, "safe_root_file", lambda _path, _maximum: content)

    with pytest.raises(ValueError, match="does not match"):
        helper.manifest(config)
