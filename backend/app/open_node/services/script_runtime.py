"""Hard-timeout parent for the private QuickJS subscription-script worker."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from typing import Any, Literal

MAX_SCRIPT_BYTES = 256 * 1024
MAX_VALUE_BYTES = 8 * 1024 * 1024
SCRIPT_TIMEOUT_SECONDS = 6.5


class ScriptRuntimeError(ValueError):
    pass


def _request(operation: str, script: str, *, hook=None, value=None):
    if not isinstance(script, str) or not script.strip():
        raise ScriptRuntimeError("Override script content is required")
    if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ScriptRuntimeError("Override script exceeds 256 KiB")
    request = {"operation": operation, "script": script}
    if hook is not None:
        request.update(hook=hook, value=value)
    try:
        raw = json.dumps(
            request, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError):
        raise ScriptRuntimeError("Override script input is not JSON-compatible") from None
    if len(raw) > MAX_VALUE_BYTES + MAX_SCRIPT_BYTES + 4096:
        raise ScriptRuntimeError("Override script input exceeds 8 MiB")
    return raw


def _invoke(raw: bytes):
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    with tempfile.TemporaryDirectory(prefix="open-node-script-") as directory:
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-m", "open_node.script_worker"],
                cwd=directory,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            raise ScriptRuntimeError("Override script runtime is unavailable") from None
        try:
            output, _stderr = process.communicate(raw, timeout=SCRIPT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
            raise ScriptRuntimeError("Override script exceeded the 5 second limit") from None
    if len(output) > MAX_VALUE_BYTES + 4096:
        raise ScriptRuntimeError("Override script result exceeds 8 MiB")
    try:
        response = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ScriptRuntimeError("Override script runtime failed") from None
    if process.returncode != 0 or not isinstance(response, dict) or response.get("ok") is not True:
        code = response.get("code") if isinstance(response, dict) else None
        if code == "script_js_error":
            raise ScriptRuntimeError("Override script JavaScript execution failed")
        if code == "script_invalid":
            raise ScriptRuntimeError("Override script request or result is invalid")
        raise ScriptRuntimeError("Override script is invalid or failed")
    return response.get("value")


def lint_script(script: str) -> None:
    _invoke(_request("lint", script))


def run_script(
    hook: Literal["post_fetch", "pre_save_nodes"], script: str, value: Any
) -> dict[str, Any] | list[dict[str, Any]]:
    result = _invoke(_request("run", script, hook=hook, value=value))
    if hook == "post_fetch":
        if not isinstance(result, dict):
            raise ScriptRuntimeError("post_fetch script must return an object")
        return result
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise ScriptRuntimeError("pre_save_nodes script must return a proxy object array")
    if len(result) > 10_000:
        raise ScriptRuntimeError("pre_save_nodes script returned too many proxies")
    return result
