"""MMWX securechan-v1 compatibility using cryptography's standard primitives."""

import asyncio
import base64
import binascii
import hashlib
import json
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import WebSocket, WebSocketDisconnect

from open_node.domain.inventory import MAX_AGENT_MESSAGE_BYTES

MAX_MESSAGE_BYTES = MAX_AGENT_MESSAGE_BYTES
MAX_SEQUENCE = (1 << 64) - 1
AUTH_TIMEOUT_SECONDS = 10


class ChannelError(ValueError):
    pass


def reject_nonfinite(_value):
    raise ChannelError("Agent messages require finite JSON numbers")


def decode_public_key(value: object) -> bytes:
    if not isinstance(value, str) or len(value) != 44:
        raise ChannelError("Invalid ephemeral public key")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ChannelError("Invalid ephemeral public key") from exc
    if len(decoded) != 32:
        raise ChannelError("Invalid ephemeral public key")
    return decoded


class ChannelSession:
    def __init__(
        self, shared: bytes, agent_public: bytes, master_public: bytes, *, is_master: bool = True
    ):
        material = HKDF(
            algorithm=hashes.SHA256(),
            length=88,
            salt=agent_public + master_public,
            info=b"securechan-v1",
        ).derive(shared)
        first, second = material[:32], material[32:64]
        first_nonce, second_nonce = material[64:76], material[76:88]
        self.send_cipher = AESGCM(first if is_master else second)
        self.recv_cipher = AESGCM(second if is_master else first)
        self.send_nonce = first_nonce if is_master else second_nonce
        self.recv_nonce = second_nonce if is_master else first_nonce
        self.send_sequence = 0
        self.recv_max = 0
        self.recv_bitmap = 0

    @staticmethod
    def nonce(base: bytes, sequence: int) -> bytes:
        return (int.from_bytes(base, "big") ^ sequence).to_bytes(12, "big")

    def encrypt(self, plaintext: bytes) -> bytes:
        if len(plaintext) > MAX_MESSAGE_BYTES or self.send_sequence >= MAX_SEQUENCE:
            raise ChannelError("Secure channel send limit reached")
        self.send_sequence += 1
        sequence = self.send_sequence
        return (
            b"\x01"
            + sequence.to_bytes(8, "big")
            + self.send_cipher.encrypt(
                self.nonce(self.send_nonce, sequence),
                plaintext,
                None,
            )
        )

    def decrypt(self, envelope: bytes) -> bytes:
        if len(envelope) < 25 or len(envelope) > MAX_MESSAGE_BYTES + 25 or envelope[0] != 1:
            raise ChannelError("Invalid secure channel envelope")
        sequence = int.from_bytes(envelope[1:9], "big")
        distance = self.recv_max - sequence
        if (
            sequence == 0
            or distance >= 64
            or (distance >= 0 and self.recv_bitmap & (1 << distance))
        ):
            raise ChannelError("Replayed or expired secure channel sequence")
        try:
            plaintext = self.recv_cipher.decrypt(
                self.nonce(self.recv_nonce, sequence),
                envelope[9:],
                None,
            )
        except InvalidTag as exc:
            raise ChannelError("Invalid secure channel authentication tag") from exc
        # Only authenticated packets may move the replay window.
        if sequence > self.recv_max:
            shift = sequence - self.recv_max
            self.recv_bitmap = 0 if shift >= 64 else (self.recv_bitmap << shift) & MAX_SEQUENCE
            self.recv_max = sequence
            self.recv_bitmap |= 1
        else:
            self.recv_bitmap |= 1 << distance
        return plaintext


class AgentIdentity:
    def __init__(self, seed: bytes):
        self._key = Ed25519PrivateKey.from_private_bytes(seed)

    @classmethod
    def load(cls, path: Path):
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("Agent identity must be a private regular file (mode 0600)")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise ValueError("Agent identity must belong to the service account")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                seed = source.read(33)
        finally:
            os.close(descriptor)
        if len(seed) != 32:
            raise ValueError("Agent identity must contain exactly one 32-byte Ed25519 seed")
        return cls(seed)

    @classmethod
    def create(cls, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if parent.st_mode & 0o022 or (hasattr(os, "geteuid") and parent.st_uid != os.geteuid()):
            raise ValueError("Agent identity directory must be owned and not group/world writable")
        seed = Ed25519PrivateKey.generate().private_bytes_raw()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(seed)
            target.flush()
            os.fsync(target.fileno())
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return cls.load(path)

    def public_metadata(self):
        public = self._key.public_key().public_bytes_raw()
        return {
            "enabled": True,
            "protocol": "securechan-v1",
            "public_key": base64.b64encode(public).decode("ascii"),
            "fingerprint": hashlib.sha256(public).hexdigest(),
            "license_required": False,
        }

    def exchange(self, payload: object):
        if not isinstance(payload, dict):
            raise ChannelError("Invalid key exchange")
        agent_public = decode_public_key(payload.get("agent_ephemeral_pub"))
        ephemeral = X25519PrivateKey.generate()
        public = ephemeral.public_key().public_bytes_raw()
        try:
            shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(agent_public))
        except ValueError as exc:
            raise ChannelError("Invalid ephemeral public key") from exc
        response = {
            "type": "key_exchange_resp",
            "payload": {
                "master_ephemeral_pub": base64.b64encode(public).decode("ascii"),
                "signature": base64.b64encode(self._key.sign(public)).decode("ascii"),
            },
        }
        return response, ChannelSession(shared, agent_public, public)


class AgentSocket:
    """Keep RPC handlers transport-neutral; encrypted connections never fall back."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session: ChannelSession | None = None
        self._send_lock = asyncio.Lock()

    async def receive_json(self):
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if self.session:
            envelope = message.get("bytes")
            if envelope is None:
                raise ChannelError("Encrypted connection requires binary messages")
            data = self.session.decrypt(envelope)
        else:
            data = message.get("text")
            if not isinstance(data, str):
                raise ChannelError("Key exchange or JSON authentication required")
            data = data.encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            raise ChannelError("Agent message is too large")
        try:
            return json.loads(data.decode("utf-8"), parse_constant=reject_nonfinite)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ChannelError("Agent message must be JSON") from exc

    async def send_json(self, data):
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ChannelError("Agent message is too large")
        async with self._send_lock:
            if self.session:
                await asyncio.wait_for(
                    self.websocket.send_bytes(
                        self.session.encrypt(encoded.encode("utf-8")),
                    ),
                    timeout=10,
                )
            else:
                await asyncio.wait_for(self.websocket.send_text(encoded), timeout=10)

    async def close(self, code=1000):
        await self.websocket.close(code=code)

    async def authenticate_message(
        self, identity: AgentIdentity | None, *, require_encryption=False
    ):
        async with asyncio.timeout(AUTH_TIMEOUT_SECONDS):
            message = await self.receive_json()
            if isinstance(message, dict) and message.get("type") == "key_exchange":
                if identity is None:
                    raise ChannelError("Legacy agent identity is not configured")
                response, session = identity.exchange(message.get("payload"))
                await self.send_json(response)
                self.session = session
                message = await self.receive_json()
            elif require_encryption:
                raise ChannelError("Legacy Agent key exchange required")
            return message
