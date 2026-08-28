"""Cloudflare registration and owned, userspace-only Xray WARP outbounds."""

import asyncio
import base64
import contextlib
import copy
import json
import os
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from open_node_agent.host_files import FileTransaction, guarded_path, read_private, remove_file
from open_node_agent.runtime import RuntimeFailure, atomic_write

API_BASE = "https://api.cloudflareclient.com/v0a4005"
TAGS = ("warp-v4", "warp-v6")
MAX_RESPONSE = 64 * 1024


def decode_key(value: str, length: int = 32) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) == length:
            return decoded
    except (ValueError, TypeError):
        pass
    raise ValueError("Invalid WARP key encoding")


class PeerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    client_id: str
    addr_v4: str
    addr_v6: str
    peer_public_key: str
    peer_endpoint: str = Field(max_length=300)
    account_type: str = Field(default="free", max_length=40)

    @field_validator("client_id", "peer_public_key")
    @classmethod
    def key(cls, value, info):
        decode_key(value, 3 if info.field_name == "client_id" else 32)
        return value

    @field_validator("addr_v4", "addr_v6")
    @classmethod
    def address(cls, value, info):
        if not value:
            return value
        address = ip_address(value)
        if (
            address.version != (4 if info.field_name == "addr_v4" else 6)
            or address.is_unspecified
            or address.is_multicast
            or "%" in value
        ):
            raise ValueError("Invalid WARP interface address")
        return str(address)

    @field_validator("peer_endpoint")
    @classmethod
    def endpoint(cls, value):
        parts = urlsplit("//" + value)
        if (
            not parts.hostname
            or not parts.port
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
            or any(character.isspace() for character in value)
            or "%" in value
        ):
            raise ValueError("Invalid WARP peer endpoint")
        return value


class WarpState(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    device_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    access_token: SecretStr = Field(min_length=1, max_length=4096)
    private_key: SecretStr
    public_key: str
    registered_at: str
    phase: str = Field(default="registered", pattern=r"^(registered|removing)$")
    config: PeerConfig | None = None
    owned_outbounds: list[dict] = Field(default_factory=list, max_length=2, repr=False)

    @field_validator("private_key", "public_key")
    @classmethod
    def key(cls, value):
        decode_key(value.get_secret_value() if isinstance(value, SecretStr) else value)
        return value

    def private_bytes(self) -> bytes:
        value = self.model_dump(mode="json")
        value["private_key"] = self.private_key.get_secret_value()
        value["access_token"] = self.access_token.get_secret_value()
        return json.dumps(value).encode() + b"\n"


class WarpAPI:
    def __init__(self, *, base_url=API_BASE, verify=True, transport=None):
        self.base_url, self.verify, self.transport = base_url, verify, transport

    async def request(
        self, method, path, *, token=None, body=None, deleting=False, expect_json=True
    ):
        headers = {"CF-Client-Version": "a-6.30-3596", "Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            async with asyncio.timeout(20):
                async with httpx.AsyncClient(
                    verify=self.verify,
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=15,
                ) as client:
                    async with client.stream(
                        method,
                        self.base_url + path,
                        headers=headers,
                        json=body,
                    ) as response:
                        if deleting and response.status_code in {200, 204, 404}:
                            return {}
                        if deleting or not 200 <= response.status_code < 300:
                            # Provider bodies can echo credentials; never return them to the panel.
                            raise RuntimeFailure(
                                f"WARP provider returned HTTP {response.status_code}"
                            )
                        if not expect_json:
                            return {}
                        data = bytearray()
                        async for block in response.aiter_bytes():
                            data.extend(block)
                            if len(data) > MAX_RESPONSE:
                                raise RuntimeFailure("WARP provider response exceeded 64 KiB")
                        value = json.loads(data)
                        if not isinstance(value, dict):
                            raise RuntimeFailure("WARP provider returned an invalid object")
                        return value
        except (httpx.HTTPError, TimeoutError):
            raise RuntimeFailure("WARP provider request failed or timed out") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeFailure("WARP provider returned invalid JSON") from None

    async def register(self, public_key):
        return await self.request(
            "POST",
            "/reg",
            body={
                "key": public_key,
                "tos": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "type": "PC",
                "model": "open-node-agent",
                "name": "open-node",
            },
        )

    async def refresh(self, state):
        return await self.request(
            "GET", "/reg/" + state.device_id, token=state.access_token.get_secret_value()
        )

    async def license(self, state, value):
        await self.request(
            "PUT",
            "/reg/" + state.device_id + "/account",
            token=state.access_token.get_secret_value(),
            body={"license": value},
            expect_json=False,
        )

    async def delete(self, state):
        await self.request(
            "DELETE",
            "/reg/" + state.device_id,
            token=state.access_token.get_secret_value(),
            deleting=True,
        )


def peer_config(response: dict) -> PeerConfig:
    try:
        config = response["config"]
        addresses = config["interface"]["addresses"]
        peer = config["peers"][0]
        result = PeerConfig(
            client_id=config["client_id"],
            addr_v4=addresses.get("v4", ""),
            addr_v6=addresses.get("v6", ""),
            peer_public_key=peer["public_key"],
            peer_endpoint=peer["endpoint"]["host"],
            account_type=response.get("account", {}).get("account_type", "free"),
        )
        if not result.addr_v4 and not result.addr_v6:
            raise ValueError()
        return result
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeFailure("WARP provider configuration is incomplete or invalid") from None


def build_outbounds(state: WarpState) -> list[dict]:
    config = state.config
    if config is None:
        raise RuntimeFailure("WARP provider configuration needs refresh")
    return [
        {
            "tag": tag,
            "protocol": "wireguard",
            "settings": {
                "secretKey": state.private_key.get_secret_value(),
                "address": [
                    value + suffix
                    for value, suffix in ((config.addr_v4, "/32"), (config.addr_v6, "/128"))
                    if value
                ],
                "peers": [{"publicKey": config.peer_public_key, "endpoint": config.peer_endpoint}],
                "reserved": list(decode_key(config.client_id, 3)),
                "mtu": 1280,
                "noKernelTun": True,
                "domainStrategy": strategy,
            },
        }
        for tag, strategy in zip(TAGS, ("ForceIPv4v6", "ForceIPv6v4"), strict=True)
    ]


def referenced(config: dict) -> bool:
    def walk(value):
        if isinstance(value, list):
            return any(walk(item) for item in value)
        if not isinstance(value, dict):
            return False
        for key, item in value.items():
            if key in {"outboundTag", "dialerProxy", "fallbackTag"} and item in TAGS:
                return True
            if key == "proxySettings" and isinstance(item, dict) and item.get("tag") in TAGS:
                return True
            if (
                key in {"selector", "subjectSelector"}
                and isinstance(item, list)
                and any(
                    isinstance(prefix, str) and any(tag.startswith(prefix) for tag in TAGS)
                    for prefix in item
                )
            ):
                return True
            if walk(item):
                return True
        return False

    return walk(config)


class Warp:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.config.state_dir / "warp.json"
        self.api = WarpAPI()
        self.transaction = FileTransaction(
            runtime.config.state_dir / "warp-transaction.json",
            self.check_path,
        )
        self.transaction.recover()

    def check_path(self, path: Path) -> Path:
        if path == self.path:
            return guarded_path(self.path.parent, path)
        if path == self.runtime.config.xray_config:
            return guarded_path(path.parent, path)
        raise RuntimeFailure("Path is not owned by the WARP manager")

    def load(self) -> WarpState | None:
        self.check_path(self.path)
        if not self.path.exists():
            return None
        info = self.path.stat()
        if info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise RuntimeFailure("WARP state must be owned by the Agent with permissions 0600")
        try:
            return WarpState.model_validate_json(read_private(self.path))
        except (ValidationError, ValueError):
            raise RuntimeFailure("WARP state is invalid; preserve it for host recovery") from None

    def save(self, state: WarpState):
        self.check_path(self.path)
        atomic_write(self.path, state.private_bytes())
        self.path.chmod(0o600)

    def status(self) -> dict:
        state = self.load()
        if state is None:
            return {
                "success": True,
                "installed": False,
                "registered": False,
                "phase": "absent",
                "license_active": False,
            }
        expected = build_outbounds(state) if state.config else []
        try:
            current = [
                item for item in self.runtime.read().get("outbounds", []) if item.get("tag") in TAGS
            ]
        except (ValueError, OSError):
            current = []
        installed = (
            bool(expected)
            and len(current) == len(expected)
            and all(current.count(item) == 1 for item in expected)
        )
        config = state.config
        return {
            "success": True,
            "registered": True,
            "installed": installed and state.phase == "registered",
            "phase": "removal_pending"
            if state.phase == "removing"
            else "configured"
            if installed
            else "needs_apply",
            "license_active": bool(config and config.account_type in {"limited", "unlimited"}),
            "account_type": config.account_type if config else "unknown",
            "device_id": state.device_id,
            "registered_at": state.registered_at,
            "addr_v4": config.addr_v4 if config else "",
            "addr_v6": config.addr_v6 if config else "",
            "outbound_tags": list(TAGS) if installed else [],
        }

    def snapshot(self) -> dict:
        try:
            return self.status()
        except (OSError, ValueError):
            return {
                "success": False,
                "installed": False,
                "phase": "error",
                "error": "WARP state requires host recovery",
            }

    def candidate(self, state: WarpState | None, *, remove=False) -> dict:
        config = copy.deepcopy(self.runtime.read())
        entries = config.setdefault("outbounds", [])
        current = [item for item in entries if item.get("tag") in TAGS]
        owned = state.owned_outbounds if state else []
        if any(item not in owned for item in current) or len(
            {item["tag"] for item in current}
        ) != len(current):
            raise RuntimeFailure("WARP tags conflict with unmanaged or edited outbounds")
        if remove and referenced(config):
            raise RuntimeFailure(
                "Remove routing, balancer and proxy references to WARP before removal"
            )
        if remove and entries and entries[0].get("tag") in TAGS:
            raise RuntimeFailure("Select a non-WARP default outbound before removal")
        replacement = [] if remove or state is None else build_outbounds(state)
        # Retain the default outbound and every unrelated entry in its original position.
        replacements = {item["tag"]: item for item in replacement}
        config["outbounds"] = [
            replacements.pop(item["tag"]) if item.get("tag") in replacements else item
            for item in entries
            if not remove or item.get("tag") not in TAGS
        ]
        config["outbounds"].extend(replacements.values())
        if not config["outbounds"]:
            raise RuntimeFailure("WARP removal requires an explicit remaining default outbound")
        return config

    async def apply(self, state: WarpState, *, remove=False):
        candidate = self.candidate(state, remove=remove)
        ok, _ = await self.runtime.validate(candidate)
        if not ok:
            raise RuntimeFailure("Xray rejected WARP configuration; no changes were activated")
        was_running = await self.runtime.running()
        state = state.model_copy(deep=True)
        state.owned_outbounds = [] if remove else build_outbounds(state)
        self.transaction.begin(
            {
                self.runtime.config.xray_config: json.dumps(candidate, indent=2).encode() + b"\n",
                self.path: state.private_bytes(),
            }
        )
        try:
            if was_running:
                await self.runtime.restart()
            self.transaction.commit()
        except BaseException as error:
            self.transaction.recover()
            if was_running:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.runtime.restart())
            if isinstance(error, Exception):
                raise RuntimeFailure(
                    "WARP activation failed; original files restored; inspect runtime status"
                ) from None
            raise

    async def install(self, body):
        state = self.load()
        if state and state.phase == "removing":
            raise RuntimeFailure("WARP removal is pending; retry removal before installing")
        if not self.runtime.enabled:
            raise RuntimeFailure("Install Xray before installing WARP")
        if state is None:
            if body.get("accept_terms") is not True:
                raise RuntimeFailure(
                    "Explicit acceptance of Cloudflare application terms is required"
                )
            self.candidate(None)
            ok, _ = await self.runtime.validate(self.runtime.read())
            if not ok:
                raise RuntimeFailure("Install a working Xray runtime before registering WARP")
            key = X25519PrivateKey.generate()
            private = base64.b64encode(key.private_bytes_raw()).decode()
            public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
            response = await self.api.register(public)
            try:
                state = WarpState(
                    device_id=response["id"],
                    access_token=response["token"],
                    private_key=private,
                    public_key=public,
                    registered_at=datetime.now(UTC).isoformat(),
                )
            except (KeyError, TypeError, ValueError):
                raise RuntimeFailure(
                    "WARP registration returned invalid device credentials"
                ) from None
            # Keep the device identity even when a peer response or Xray apply fails.
            self.save(state)
        else:
            response = await self.api.refresh(state)
        state.config = peer_config(response)
        self.save(state)
        await self.apply(state)
        return self.status()

    async def set_license(self, body):
        value = body.get("license")
        if (
            not isinstance(value, str)
            or not 1 <= len(value.strip()) <= 255
            or any(ord(character) < 32 for character in value)
        ):
            raise RuntimeFailure("A valid WARP+ credential is required")
        state = self.load()
        if state is None or state.phase != "registered":
            raise RuntimeFailure("Register free WARP before updating WARP+ credentials")
        self.candidate(state)
        await self.api.license(state, value.strip())
        state.config = peer_config(await self.api.refresh(state))
        self.save(state)
        await self.apply(state)
        return self.status()

    async def remove(self, body):
        if body.get("confirm") is not True:
            raise RuntimeFailure("Explicit WARP removal confirmation is required")
        state = self.load()
        if state is None:
            self.candidate(None, remove=True)
            return self.status()
        self.candidate(state, remove=True)
        state.phase = "removing"
        self.save(state)
        await self.apply(state, remove=True)
        # Credentials remain retryable until remote deletion is confirmed. A crash cannot
        # restore revoked outbounds: the local disable transaction has already committed.
        await self.api.delete(state)
        remove_file(self.path)
        return self.status()

    async def handle(self, method, path, body):
        if path == "/api/child/warp/status" and method == "GET":
            return self.status()
        if method == "POST":
            if path == "/api/child/warp/install":
                return await self.install(body)
            if path == "/api/child/warp/license":
                return await self.set_license(body)
            if path == "/api/child/warp/remove":
                return await self.remove(body)
        raise NotImplementedError("Unsupported WARP operation")
