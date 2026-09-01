"""Private QuickJS worker. The parent sends one bounded JSON request over stdin."""

import json
import sys
from typing import Any

import quickjs

MAX_REQUEST_BYTES = 9 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_SCRIPT_BYTES = 256 * 1024
MAX_PROXIES = 10_000


def _apply_process_limits() -> None:
    """Limit the already-isolated worker before it reads any untrusted bytes."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (ImportError, OSError, ValueError):
        pass
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0)
        except (AttributeError, OSError):
            pass


def _target_format(value: str):
    from open_node.domain.subscriptions import SubscriptionClientFormat

    normalized = value.strip().casefold().replace("_", "-").replace(" ", "-")
    aliases = {
        "clashmeta": "clash",
        "clash-meta": "clash",
        "mihomo": "clash",
        "singbox": "sing-box",
        "qx": "quantumult-x",
        "quantumultx": "quantumult-x",
        "uri": "uri-list",
    }
    return SubscriptionClientFormat(aliases.get(normalized, normalized))


def _produce(raw_proxies: str, target: str) -> str:
    from open_node.services.inventory import InventoryStore

    proxies = json.loads(raw_proxies)
    if (
        not isinstance(proxies, list)
        or len(proxies) > MAX_PROXIES
        or any(not isinstance(proxy, dict) for proxy in proxies)
    ):
        raise ValueError("produce expects a bounded proxy object array")
    content, _media_type, _extension = InventoryStore._render_subscription_content(
        proxies, _target_format(target)
    )
    if len(content.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("produce output is too large")
    return json.dumps(content, ensure_ascii=False)


def _context() -> quickjs.Context:
    context = quickjs.Context()
    context.set_memory_limit(64 * 1024 * 1024)
    context.set_max_stack_size(512 * 1024)
    context.add_callable("__open_node_produce__", _produce)
    context.eval(
        """
        Object.defineProperty(globalThis, "console", {
          configurable: false,
          writable: false,
          value: Object.freeze({log() {}, warn() {}, error() {}})
        });
        Object.defineProperty(globalThis, "produce", {
          configurable: false,
          writable: false,
          value(proxies, targetFormat) {
            return JSON.parse(__open_node_produce__(JSON.stringify(proxies), String(targetFormat)));
          }
        });
        """
    )
    return context


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request_too_large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid_request")
    return value


def _script(request: dict[str, Any]) -> str:
    content = request.get("script")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("invalid_script")
    if len(content.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ValueError("script_too_large")
    return content


def _execute(request: dict[str, Any]) -> Any:
    operation = request.get("operation")
    script = _script(request)
    context = _context()
    if operation == "lint":
        context.set("__open_node_script__", script)
        context.eval("new Function(__open_node_script__)")
        return None
    if operation != "run" or request.get("hook") not in {"post_fetch", "pre_save_nodes"}:
        raise ValueError("invalid_operation")
    value = request.get("value")
    input_json = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    context.set("__open_node_input__", input_json)
    context.eval(script)
    result = context.eval(
        """
        (() => {
          if (typeof main !== "function") throw new TypeError("main must be a function");
          const original = JSON.parse(__open_node_input__);
          const value = main(original);
          return JSON.stringify(value === undefined || value === null ? original : value);
        })()
        """
    )
    if not isinstance(result, str) or len(result.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("result_too_large")
    return json.loads(result)


def _write(value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_RESULT_BYTES:
        raw = b'{"ok":false,"code":"result_too_large"}'
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main() -> int:
    _apply_process_limits()
    try:
        result = _execute(_read_request())
    except quickjs.JSException:
        _write({"ok": False, "code": "script_js_error"})
        return 2
    except (json.JSONDecodeError, TypeError, ValueError):
        _write({"ok": False, "code": "script_invalid"})
        return 2
    except Exception:
        _write({"ok": False, "code": "worker_failure"})
        return 3
    _write({"ok": True, "value": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
