"""Validate native cleanup receipts before acknowledging a remote operation."""

import re

ENDPOINT = "/api/child/node-cleanup"


def confirmation_error(command, body):
    if command.path != ENDPOINT:
        return None
    requested = command.body if isinstance(command.body, dict) else {}
    value = body.get("node_cleanup")
    if (
        not command.attempts
        or body.get("success") is not True
        or body.get("restart_required") is True
        or not isinstance(value, dict)
        or not isinstance(value.get("revision"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["revision"])
        or not isinstance(value.get("impact"), dict)
        or type(value.get("applied")) is not bool
    ):
        return "Agent did not confirm the native node cleanup operation"
    action = requested.get("action")
    if action == "apply":
        if (
            value["applied"] is not True
            or value["revision"] != requested.get("expected_revision")
            or not requested.get("operation_id")
            or value.get("operation_id") != requested["operation_id"]
        ):
            return "Agent did not confirm the requested node cleanup identity and revision"
    elif action == "preview":
        if value["applied"]:
            return "Node cleanup preview unexpectedly reported an applied operation"
    elif action == "status":
        if (
            not requested.get("operation_id")
            or value.get("operation_id") != requested["operation_id"]
        ):
            return "Node cleanup status identity does not match"
    else:
        return "Unknown node cleanup action cannot be confirmed"
    return None
