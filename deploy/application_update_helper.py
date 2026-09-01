#!/usr/bin/env python3
"""Root-owned fixed-function bridge from the panel to ``install.sh update``."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
INSTALLER_MANIFEST_VERSION = "2"
RUNTIME_CONTAINER_PORT = "62031"
OFFICIAL_REPOSITORY = "https://github.com/FengYuchen1314/open-node.git"
OFFICIAL_REF = "main"
REVISION = re.compile(r"^[0-9a-f]{40}$")
REQUEST_ID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
CONFIG_KEYS = {
    "schema_version", "project_name", "repository", "ref", "install_dir", "config_dir",
    "backup_dir", "image_repository", "state_dir", "runtime_uid", "runtime_gid",
}
REQUEST_KEYS = {
    "schema_version", "request_id", "action", "expected_revision", "requested_at",
}


def now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def strict_json(content: bytes, maximum: int) -> dict[str, Any]:
    if not 1 < len(content) <= maximum:
        raise ValueError("invalid JSON size")
    value = json.loads(
        content.decode("utf-8"), object_pairs_hook=unique_object,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def read_bounded(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short file write")
        view = view[written:]


def safe_root_file(path: Path, maximum: int) -> bytes:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022 or not 1 < info.st_size <= maximum
    ):
        raise ValueError(f"unsafe root file: {path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("file changed while opening")
        return read_bounded(descriptor, maximum)
    finally:
        os.close(descriptor)


def absolute_path(value: Any) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or "\n" in value or "\r" in value:
        raise ValueError("absolute path required")
    result = Path(value)
    if result == Path("/") or ".." in result.parts:
        raise ValueError("unsafe absolute path")
    return result


def load_config(path: Path) -> dict[str, Any]:
    value = strict_json(safe_root_file(path, 16_384), 16_384)
    if set(value) != CONFIG_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid helper configuration")
    if value.get("repository") != OFFICIAL_REPOSITORY or value.get("ref") != OFFICIAL_REF:
        raise ValueError("only the official main branch supports panel updates")
    if not isinstance(value.get("project_name"), str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,62}", value["project_name"]
    ):
        raise ValueError("invalid project name")
    if not isinstance(value.get("image_repository"), str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._/-]{0,200}", value["image_repository"]
    ):
        raise ValueError("invalid image repository")
    for key in ("install_dir", "config_dir", "backup_dir", "state_dir"):
        value[key] = absolute_path(value[key])
    for key in ("runtime_uid", "runtime_gid"):
        if not isinstance(value.get(key), int) or not 1 <= value[key] <= 2_147_483_647:
            raise ValueError("invalid runtime identity")
    return value


def state_directory(config: dict[str, Any]) -> tuple[Path, int]:
    root: Path = config["state_dir"]
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
        or info.st_gid != config["runtime_gid"] or stat.S_IMODE(info.st_mode) != 0o1770
    ):
        raise ValueError("unsafe state directory")
    return root, os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def manifest(config: dict[str, Any]) -> dict[str, str]:
    path: Path = config["config_dir"] / "installer.manifest"
    values: dict[str, str] = {}
    for line in safe_root_file(path, 65_536).decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("invalid installer manifest")
        values[key] = value
    expected = {
        "MANIFEST_VERSION": INSTALLER_MANIFEST_VERSION,
        "REPOSITORY": OFFICIAL_REPOSITORY,
        "REF": OFFICIAL_REF,
        "INSTALL_DIR": str(config["install_dir"]),
        "CONFIG_DIR": str(config["config_dir"]),
        "BACKUP_DIR": str(config["backup_dir"]),
        "PROJECT_NAME": config["project_name"],
        "IMAGE_REPOSITORY": config["image_repository"],
        "DEPLOYED_RUNTIME_PORT": RUNTIME_CONTAINER_PORT,
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise ValueError("helper configuration does not match installer manifest")
    if not REVISION.fullmatch(values.get("DEPLOYED_REVISION", "")):
        raise ValueError("invalid deployed revision")
    return values


def state_payload(
    config: dict[str, Any], status: str, message: str, *, request_id: str | None = None,
    latest: str | None = None, checked_at: str | None = None, started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    current = manifest(config)["DEPLOYED_REVISION"]
    return {
        "schema_version": SCHEMA_VERSION,
        "managed": True,
        "status": status,
        "request_id": request_id,
        "current_revision": current,
        "latest_revision": latest,
        "has_update": None if latest is None else latest != current,
        "checked_at": checked_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "message": message,
        "release_url": None if latest is None else
            f"https://github.com/FengYuchen1314/open-node/commit/{latest}",
        "license_required": False,
    }


def write_state(config: dict[str, Any], payload: dict[str, Any]) -> None:
    root, directory = state_directory(config)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".state.", dir=root)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o640)
            os.fchown(descriptor, 0, config["runtime_gid"])
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
            write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, root / "state.json")
        temporary = None
        os.fsync(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(directory)


def read_request(config: dict[str, Any]) -> dict[str, Any] | None:
    _root, directory = state_directory(config)
    try:
        try:
            descriptor = os.open("request.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != config["runtime_uid"] or info.st_gid != config["runtime_gid"]
                or stat.S_IMODE(info.st_mode) != 0o600 or not 1 < info.st_size <= 4096
            ):
                raise ValueError("unsafe request file")
            content = read_bounded(descriptor, 4096)
            current = os.stat("request.json", dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("request changed while opening")
            os.unlink("request.json", dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
    value = strict_json(content, 4096)
    if set(value) != REQUEST_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid request fields")
    if not isinstance(value.get("request_id"), str) or not REQUEST_ID.fullmatch(value["request_id"]):
        raise ValueError("invalid request identifier")
    if value.get("action") not in {"check", "apply"}:
        raise ValueError("invalid request action")
    expected = value.get("expected_revision")
    if value["action"] == "check" and expected is not None:
        raise ValueError("check request cannot select a revision")
    if value["action"] == "apply" and (
        not isinstance(expected, str) or not REVISION.fullmatch(expected)
    ):
        raise ValueError("apply request requires a revision")
    if not isinstance(value.get("requested_at"), str) or len(value["requested_at"]) > 40:
        raise ValueError("invalid request time")
    return value


def latest_revision() -> str:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", OFFICIAL_REPOSITORY, OFFICIAL_REF],
        check=True, capture_output=True, text=True, timeout=30, env=environment,
    )
    lines = [line for line in result.stdout.splitlines() if line]
    if len(lines) != 1:
        raise ValueError("unexpected remote result")
    revision, separator, ref = lines[0].partition("\t")
    if not separator or ref != f"refs/heads/{OFFICIAL_REF}" or not REVISION.fullmatch(revision):
        raise ValueError("invalid remote revision")
    return revision


def run_update(config: dict[str, Any], expected: str) -> int:
    installer: Path = config["install_dir"] / "install.sh"
    safe_root_file(installer, 2_000_000)
    environment = {
        **os.environ,
        "OPEN_NODE_REPOSITORY": OFFICIAL_REPOSITORY,
        "OPEN_NODE_REF": OFFICIAL_REF,
        "OPEN_NODE_INSTALL_DIR": str(config["install_dir"]),
        "OPEN_NODE_CONFIG_DIR": str(config["config_dir"]),
        "OPEN_NODE_BACKUP_DIR": str(config["backup_dir"]),
        "OPEN_NODE_PROJECT_NAME": config["project_name"],
        "OPEN_NODE_IMAGE_REPOSITORY": config["image_repository"],
        "OPEN_NODE_EXPECTED_REVISION": expected,
        "OPEN_NODE_CREATE_ADMIN": "0",
        "OPEN_NODE_AUTO_INSTALL_DEPENDENCIES": "0",
        "OPEN_NODE_UPDATE_HELPER_ACTIVE": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return subprocess.run(["bash", str(installer), "update"], check=False, env=environment).returncode


def process(config: dict[str, Any]) -> None:
    try:
        request = read_request(config)
    except Exception:
        write_state(config, state_payload(
            config, "failed", "更新请求未通过宿主机安全校验。", completed_at=now(),
        ))
        raise
    if request is None:
        return
    request_id = request["request_id"]
    started = now()
    write_state(config, state_payload(
        config, "checking", "正在从官方 GitHub 检查目标版本。",
        request_id=request_id, started_at=started,
    ))
    try:
        latest = latest_revision()
    except Exception:
        write_state(config, state_payload(
            config, "failed", "无法从官方 GitHub 读取目标版本。",
            request_id=request_id, started_at=started, completed_at=now(),
        ))
        raise
    checked = now()
    current = manifest(config)["DEPLOYED_REVISION"]
    if request["action"] == "check":
        status = "current" if latest == current else "available"
        message = "当前已经是最新版本。" if latest == current else "发现可用更新，请核对目标提交后再执行。"
        write_state(config, state_payload(
            config, status, message, request_id=request_id, latest=latest,
            checked_at=checked, started_at=started, completed_at=now(),
        ))
        return
    if latest != request["expected_revision"]:
        write_state(config, state_payload(
            config, "failed", "目标版本已经变化，请重新检查更新。",
            request_id=request_id, latest=latest, checked_at=checked,
            started_at=started, completed_at=now(),
        ))
        return
    if latest == current:
        write_state(config, state_payload(
            config, "current", "当前已经是目标版本。", request_id=request_id,
            latest=latest, checked_at=checked, started_at=started, completed_at=now(),
        ))
        return
    write_state(config, state_payload(
        config, "updating", "正在备份数据、构建候选镜像并执行健康检查。",
        request_id=request_id, latest=latest, checked_at=checked, started_at=started,
    ))
    result = run_update(config, latest)
    if result == 0 and manifest(config)["DEPLOYED_REVISION"] == latest:
        write_state(config, state_payload(
            config, "succeeded", "应用更新完成，服务已通过健康检查。",
            request_id=request_id, latest=latest, checked_at=checked,
            started_at=started, completed_at=now(),
        ))
        return
    recovery = config["config_dir"] / "installer.recovery"
    status = "recovery_required" if recovery.exists() else "failed"
    message = (
        "更新未完成且需要人工恢复，请在宿主机运行安装脚本 status 查看恢复标记。"
        if status == "recovery_required" else
        "更新未完成，原部署状态已保留；请在宿主机查看更新助手日志。"
    )
    write_state(config, state_payload(
        config, status, message, request_id=request_id, latest=latest, checked_at=checked,
        started_at=started, completed_at=now(),
    ))
    raise RuntimeError("installer-managed update failed")


def initialize(config: dict[str, Any]) -> None:
    recovery = config["config_dir"] / "installer.recovery"
    status = "recovery_required" if recovery.exists() else "idle"
    message = (
        "检测到安装器恢复标记，请先在宿主机完成恢复。"
        if status == "recovery_required" else "宿主机更新助手已就绪。"
    )
    write_state(config, state_payload(config, status, message))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--initialize", action="store_true")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("application update helper requires root")
    config = load_config(arguments.config)
    if arguments.initialize:
        initialize(config)
    else:
        process(config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"open-node application update helper failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
