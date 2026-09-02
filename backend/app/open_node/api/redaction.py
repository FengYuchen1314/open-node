"""Public API redaction for sensitive Agent commands.

The durable command queue deliberately stores complete payloads so an Agent can
execute, retry, or roll back a change. Browser-facing APIs must never serialize
those executable copies: Xray configurations contain client credentials and
WARP outbounds contain the WireGuard private key.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from open_node.domain.changes import AgentChangeSetRead, AgentChangeSetStepRead
from open_node.domain.inventory import AgentCommandCreate, AgentCommandRead, AgentScanResultRead

_EGRESS_APPLY_PATH = "/api/child/egress/apply"
_INBOUNDS_PATH = "/api/child/inbounds"
_OUTBOUNDS_PATH = "/api/child/outbounds"
_XRAY_CONFIG_PATH = "/api/child/xray/config"
_XRAY_CONFIG_FILES_PATH = "/api/child/xray/config-files"
_XRAY_TEST_CONFIG_PATH = "/api/child/xray/test-config"
_WARP_LICENSE_PATH = "/api/child/warp/license"
_SCAN_PATH = "/api/child/scan"
_BATCH_APPLY_PATH = "/api/child/batch-apply"
_GENERATED_OUTBOUND_TAGS = frozenset({"warp-v4", "warp-v6"})
_MANAGED_EGRESS_OUTBOUND_PREFIX = "managed-egress:"

_SENSITIVE_WRITE_PATHS = frozenset(
    {
        _EGRESS_APPLY_PATH,
        _INBOUNDS_PATH,
        _OUTBOUNDS_PATH,
        _XRAY_CONFIG_PATH,
        _XRAY_CONFIG_FILES_PATH,
        _XRAY_TEST_CONFIG_PATH,
        _WARP_LICENSE_PATH,
        _BATCH_APPLY_PATH,
    }
)
_REDACTED_BODY = {"redacted": True}


def _is_sensitive_write(command: AgentCommandCreate | AgentCommandRead) -> bool:
    return command.method.upper() != "GET" and command.path in _SENSITIVE_WRITE_PATHS


def _entry_summaries(
    value: Any,
    key: str,
    *,
    generated_only: bool = False,
) -> dict[str, object]:
    """Strip generated secrets while retaining editable operator-owned entries."""

    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return dict(_REDACTED_BODY)
    summaries: list[dict[str, Any]] = []
    for item in value[key]:
        if not isinstance(item, dict):
            continue
        tag = item.get("tag")
        generated = (
            isinstance(tag, str)
            and (
                tag in _GENERATED_OUTBOUND_TAGS
                or tag.startswith(_MANAGED_EGRESS_OUTBOUND_PREFIX)
            )
        )
        if generated_only and not generated:
            summaries.append(copy.deepcopy(item))
            continue
        summary = {
            field: item[field]
            for field in ("tag", "protocol")
            if isinstance(item.get(field), str)
        }
        summaries.append(summary)
    result: dict[str, object] = {key: summaries}
    if isinstance(value.get("success"), bool):
        result["success"] = value["success"]
    return result


def _contains_config_file_content(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key).lower() in {"config", "content"} for key in value):
            return True
        return any(_contains_config_file_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_config_file_content(item) for item in value)
    return False


def _is_generated_outbound_tag(value: Any) -> bool:
    return isinstance(value, str) and (
        value in _GENERATED_OUTBOUND_TAGS
        or value.startswith(_MANAGED_EGRESS_OUTBOUND_PREFIX)
    )


def _contains_generated_xray_record(value: Any) -> bool:
    if isinstance(value, str):
        if any(
            marker in value
            for marker in (
                _MANAGED_EGRESS_OUTBOUND_PREFIX,
                "open_node_egress__",
                '"warp-v4"',
                '"warp-v6"',
            )
        ):
            return True
        try:
            return _contains_generated_xray_record(json.loads(value))
        except (TypeError, ValueError):
            return False
    if isinstance(value, list):
        return any(_contains_generated_xray_record(item) for item in value)
    if not isinstance(value, dict):
        return False
    if _is_generated_outbound_tag(value.get("tag")):
        return True
    if str(value.get("email") or "").startswith("open_node_egress__"):
        return True
    return any(_contains_generated_xray_record(item) for item in value.values())


def _without_managed_egress_clients(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_managed_egress_clients(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = {}
    for key, item in value.items():
        if key in {"clients", "users", "accounts"} and isinstance(item, list):
            result[key] = [
                _without_managed_egress_clients(client)
                for client in item
                if not (
                    isinstance(client, dict)
                    and str(client.get("email") or "").startswith("open_node_egress__")
                )
            ]
        else:
            result[key] = _without_managed_egress_clients(item)
    return result


def public_xray_config(config: str | None) -> str | None:
    """Expose operator-owned configuration but never generated egress/WARP secrets."""

    if config is None or _contains_generated_xray_record(config):
        return None
    return config


def public_scan_result(scan: AgentScanResultRead | None) -> AgentScanResultRead | None:
    if scan is None:
        return None
    return scan.model_copy(
        update={"inbounds": _without_managed_egress_clients(scan.inbounds)},
        deep=True,
    )


def _public_result_body(command: AgentCommandRead) -> Any:
    if command.result_body is None:
        return None
    if command.path == _OUTBOUNDS_PATH:
        return _entry_summaries(command.result_body, "outbounds", generated_only=True)
    if command.path == _INBOUNDS_PATH:
        return _entry_summaries(command.result_body, "inbounds")
    if command.path == _SCAN_PATH:
        return _without_managed_egress_clients(command.result_body)
    if command.path in {_EGRESS_APPLY_PATH, _WARP_LICENSE_PATH, _BATCH_APPLY_PATH}:
        return dict(_REDACTED_BODY)
    if command.path in {_XRAY_CONFIG_PATH, _XRAY_TEST_CONFIG_PATH}:
        return (
            dict(_REDACTED_BODY)
            if _contains_generated_xray_record(command.result_body)
            else command.result_body
        )
    if command.path == _XRAY_CONFIG_FILES_PATH:
        # Directory listings contain metadata only. A selected file and every
        # write response may contain the complete active configuration.
        if not _contains_config_file_content(command.result_body):
            return command.result_body
        return (
            dict(_REDACTED_BODY)
            if _contains_generated_xray_record(command.result_body)
            else command.result_body
        )
    return command.result_body


def _has_sensitive_public_error(command: AgentCommandRead) -> bool:
    return (
        command.path in {
            _EGRESS_APPLY_PATH,
            _INBOUNDS_PATH,
            _OUTBOUNDS_PATH,
            _XRAY_CONFIG_PATH,
            _XRAY_CONFIG_FILES_PATH,
            _XRAY_TEST_CONFIG_PATH,
            _WARP_LICENSE_PATH,
            _BATCH_APPLY_PATH,
            _SCAN_PATH,
        }
        and bool(command.result_error)
    )


def public_command_create(command: AgentCommandCreate | None) -> AgentCommandCreate | None:
    """Return a display-safe command copy without mutating the queued payload."""

    if command is None or not _is_sensitive_write(command):
        return command
    return command.model_copy(update={"body": dict(_REDACTED_BODY)}, deep=True)


def public_command_read(command: AgentCommandRead | None) -> AgentCommandRead | None:
    """Return browser-safe command metadata while preserving internal execution."""

    if command is None:
        return None
    updates: dict[str, object] = {}
    if _is_sensitive_write(command):
        updates["body"] = dict(_REDACTED_BODY)
    public_result = _public_result_body(command)
    if public_result is not command.result_body:
        updates["result_body"] = public_result
    if _has_sensitive_public_error(command):
        updates["result_error"] = "Sensitive Agent command failed"
    if not updates:
        return command
    return command.model_copy(update=updates, deep=True)


def _public_step(step: AgentChangeSetStepRead) -> AgentChangeSetStepRead:
    return step.model_copy(
        update={
            "forward": public_command_create(step.forward),
            "rollback": public_command_create(step.rollback),
            "forward_command": public_command_read(step.forward_command),
            "rollback_command": public_command_read(step.rollback_command),
            "rollback_history": [
                public_command_read(command) for command in step.rollback_history
            ],
        },
        deep=True,
    )


def public_change_set(change_set: AgentChangeSetRead) -> AgentChangeSetRead:
    """Return a display-safe copy without mutating the executable DB models."""

    return change_set.model_copy(
        update={"steps": [_public_step(step) for step in change_set.steps]},
        deep=True,
    )
