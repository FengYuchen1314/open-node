"""Unprivileged requests to the installation's root-owned Unix socket."""

import asyncio
import contextlib
import json
import socket
import struct

from open_node_agent.lifecycle_protocol import (
    COMMAND_FIELDS,
    MAX_MESSAGE_BYTES,
    validate_command,
)
from open_node_agent.runtime import RuntimeFailure


class DeferredCommand(Exception):
    """The independent helper owns completion, including uncertain submissions."""


class HostLifecycle:
    def __init__(self, config):
        self.config = config

    async def request(self, message):
        if self.config.lifecycle_socket is None:
            raise RuntimeFailure("Remote Agent lifecycle must first be enabled by the host owner")
        encoded = json.dumps(message).encode() + b"\n"
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise RuntimeFailure("Lifecycle request is too large")
        writer = None
        submitted = False
        try:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_unix_connection(
                    self.config.lifecycle_socket, limit=MAX_MESSAGE_BYTES
                )
                peer = writer.get_extra_info("socket")
                _, uid, _ = struct.unpack(
                    "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if uid != 0:
                    raise RuntimeFailure("Lifecycle socket is not served by root")
                writer.write(encoded)
                submitted = True
                await writer.drain()
                raw = await reader.readline()
                if not raw or len(raw) > MAX_MESSAGE_BYTES:
                    raise ValueError("Invalid lifecycle response")
                reply = json.loads(raw)
                if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                    raise ValueError("Invalid lifecycle response")
        except (OSError, TimeoutError, ValueError) as exc:
            if submitted and message.get("op") == "submit":
                raise DeferredCommand() from exc
            raise RuntimeFailure(
                "Lifecycle helper is unavailable or rejected its connection"
            ) from exc
        finally:
            if writer:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
        if not reply["ok"]:
            raise RuntimeFailure(str(reply.get("error", "Lifecycle request rejected"))[:2048])
        return reply

    async def submit(self, command):
        payload = {key: command.get(key) for key in ("request_id", *COMMAND_FIELDS)}
        validate_command(payload)
        reply = await self.request({"op": "submit", "command": payload})
        if reply.get("result") is not None:
            result = reply["result"]
            if (
                not isinstance(result, dict)
                or result.get("request_id") != command["request_id"]
                or type(result.get("status")) is not int
                or not 100 <= result["status"] <= 599
            ):
                raise DeferredCommand()
            return result
        raise DeferredCommand()

    async def status(self):
        return (await self.request({"op": "status"}))["status"]
