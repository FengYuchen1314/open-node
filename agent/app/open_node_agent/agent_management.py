import asyncio
import json
import os
import stat
from pathlib import Path
from time import monotonic

import yaml
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from open_node_agent.config import AgentConfig
from open_node_agent.runtime import RuntimeFailure, atomic_write

MAX_AGENT_CONFIG_BYTES = 1024 * 1024


class AgentManagement:
    """MMWX-compatible Agent settings that are safe for an outbound-only Agent."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._master_url_changed = False

    async def probe_master_url(self, body: dict) -> dict:
        candidate = self._candidate_url(body.get("master_url"))
        started = monotonic()
        try:
            async with asyncio.timeout(12):
                async with connect(
                    candidate.websocket_url(),
                    proxy=None,
                    max_size=1024 * 1024,
                    ping_interval=None,
                    open_timeout=10,
                    close_timeout=2,
                    **(
                        {"ssl": candidate.tls_context()}
                        if candidate.master_url.startswith("https:")
                        else {}
                    ),
                ) as connection:
                    await connection.send(
                        json.dumps(
                            {
                                "type": "auth",
                                "payload": {
                                    "token": self.config.token.get_secret_value(),
                                    "probe": True,
                                },
                            }
                        )
                    )
                    reply = json.loads(await connection.recv())
                    if (
                        not isinstance(reply, dict)
                        or reply.get("type") != "auth_result"
                        or not isinstance(reply.get("payload"), dict)
                        or reply["payload"].get("success") is not True
                    ):
                        message = (
                            reply.get("payload", {}).get("message")
                            if isinstance(reply, dict)
                            and isinstance(reply.get("payload"), dict)
                            else None
                        )
                        return {
                            "success": False,
                            "message": str(message or "Agent authentication was rejected")[:512],
                        }
        except (OSError, TimeoutError, UnicodeError, ValueError, WebSocketException) as exc:
            return {
                "success": False,
                "message": self._probe_error(exc),
            }
        return {"success": True, "latency_ms": max(0, int((monotonic() - started) * 1000))}

    def update_master_url(self, body: dict) -> dict:
        if set(body) - {"master_url", "only_if_recovery"}:
            raise RuntimeFailure("Unsupported master URL update field")
        if not isinstance(body.get("only_if_recovery", False), bool):
            raise RuntimeFailure("only_if_recovery must be a boolean")
        candidate = self._candidate_url(body.get("master_url"))
        path, data, raw = self._read_source_config()
        current = self._source_url(data, "master_url")
        if current != self.config.master_url:
            raise RuntimeFailure("Agent configuration changed since startup; restart before editing")

        if body.get("only_if_recovery"):
            recovery = self._optional_source_url(data, "recovery_url")
            if recovery is None or recovery != current:
                return {
                    "success": True,
                    "unchanged": True,
                    "updated": False,
                    "message": "Working non-recovery master_url preserved",
                }
        if candidate.master_url == current:
            return {
                "success": True,
                "unchanged": True,
                "updated": False,
                "message": "master_url already up to date",
            }

        data["master_url"] = candidate.master_url
        encoded = self._encode_source_config(path, data, raw)
        if len(encoded) > MAX_AGENT_CONFIG_BYTES:
            raise RuntimeFailure("Agent configuration exceeds 1 MiB")
        atomic_write(path, encoded)
        self.config.master_url = candidate.master_url
        self._master_url_changed = True
        return {
            "success": True,
            "unchanged": False,
            "updated": True,
            "message": f"master_url updated to {candidate.master_url}; reconnecting",
        }

    def consume_master_url_changed(self) -> bool:
        changed = self._master_url_changed
        self._master_url_changed = False
        return changed

    def _candidate_url(self, value: object) -> AgentConfig:
        if not isinstance(value, str) or not value.strip():
            raise RuntimeFailure("master_url is required")
        data = self.config.model_dump()
        data["token"] = self.config.token.get_secret_value()
        data["master_url"] = value
        try:
            return AgentConfig.model_validate(data)
        except ValueError as exc:
            raise RuntimeFailure(str(exc)) from None

    def _read_source_config(self) -> tuple[Path, dict, bytes]:
        path = self.config.source_path
        if path is None:
            raise RuntimeFailure("Agent config path is unavailable; restart from a config file")
        try:
            info = path.lstat()
        except OSError as exc:
            raise RuntimeFailure(f"Cannot inspect Agent configuration: {exc.strerror}") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise RuntimeFailure(
                "Agent configuration must be a private regular file owned by the service account"
            )
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeFailure("Agent configuration changed while opening")
            raw = source.read(MAX_AGENT_CONFIG_BYTES + 1)
        if len(raw) > MAX_AGENT_CONFIG_BYTES:
            raise RuntimeFailure("Agent configuration exceeds 1 MiB")
        try:
            data = yaml.safe_load(raw)
        except (UnicodeError, yaml.YAMLError):
            raise RuntimeFailure("Invalid Agent configuration syntax") from None
        if not isinstance(data, dict):
            raise RuntimeFailure("Agent configuration must be an object")
        return path, data, raw

    def _source_url(self, data: dict, field: str) -> str:
        value = data.get(field)
        if not isinstance(value, str):
            raise RuntimeFailure(f"{field} is missing from Agent configuration")
        try:
            return self.config.validate_control_url(value, field=field)
        except ValueError as exc:
            raise RuntimeFailure(str(exc)) from None

    def _optional_source_url(self, data: dict, field: str) -> str | None:
        if data.get(field) is None:
            return None
        return self._source_url(data, field)

    @staticmethod
    def _encode_source_config(path: Path, data: dict, raw: bytes) -> bytes:
        if path.suffix.lower() == ".json" or raw.lstrip().startswith(b"{"):
            return json.dumps(data, indent=2).encode() + b"\n"
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode()

    @staticmethod
    def _probe_error(exc: Exception) -> str:
        if isinstance(exc, TimeoutError):
            return "Master probe timed out"
        message = str(exc).strip()
        return (message or type(exc).__name__)[:512]
