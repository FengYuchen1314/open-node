"""Standard-library-only messages shared with the privileged lifecycle helper."""

import hashlib
import json
import re

VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"
SHA_PATTERN = r"[0-9a-f]{64}"
REQUEST_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}"
MAX_MESSAGE_BYTES = 32 * 1024
UPGRADE_PATHS = {"/api/child/agent/upgrade", "/api/child/agent/upgrade-stream"}
UNINSTALL_PATHS = {"/api/child/agent/uninstall", "/api/child/agent/uninstall-stream"}
ROLLBACK_PATH = "/api/child/agent/rollback"
LIFECYCLE_PATHS = UPGRADE_PATHS | UNINSTALL_PATHS | {ROLLBACK_PATH}
COMMAND_FIELDS = ("method", "path", "query", "body", "stream")


def fingerprint(command):
    return hashlib.sha256(
        json.dumps({key: command.get(key) for key in COMMAND_FIELDS}, sort_keys=True).encode()
    ).hexdigest()


def is_lifecycle_command(command):
    return (
        isinstance(command, dict)
        and command.get("method") == "POST"
        and isinstance(command.get("path"), str)
        and command["path"] in LIFECYCLE_PATHS
    )


def validate_command(command):
    if not isinstance(command, dict) or set(command) != {"request_id", *COMMAND_FIELDS}:
        raise ValueError("Invalid lifecycle command fields")
    request_id = command["request_id"]
    if not isinstance(request_id, str) or not re.fullmatch(REQUEST_PATTERN, request_id):
        raise ValueError("Invalid lifecycle request ID")
    if not is_lifecycle_command(command) or command["query"] not in ("", None):
        raise ValueError("Unsupported lifecycle operation")
    if type(command["stream"]) is not bool:
        raise ValueError("Invalid lifecycle stream flag")
    body = command["body"]
    if not isinstance(body, dict):
        raise ValueError("A lifecycle operation requires an explicit request")
    if command["path"] in UPGRADE_PATHS:
        if set(body) != {"version", "sha256"}:
            raise ValueError("Upgrade requires only version and sha256")
        if (
            not isinstance(body["version"], str)
            or len(body["version"]) > 64
            or not re.fullmatch(VERSION_PATTERN, body["version"])
            or not isinstance(body["sha256"], str)
            or not re.fullmatch(SHA_PATTERN, body["sha256"])
        ):
            raise ValueError("An explicit Agent version and lowercase SHA-256 are required")
        return "upgrade"
    if set(body) != {"confirm"} or body["confirm"] is not True:
        raise ValueError("Explicit confirmation is required")
    return "uninstall" if command["path"] in UNINSTALL_PATHS else "rollback"
