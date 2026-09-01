"""Official MMWX token-bound ephemeral federation encryption."""

import hashlib
import hmac
import threading
from dataclasses import dataclass
from time import monotonic

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from open_node.services.secure_channel import ChannelError, ChannelSession

FEDERATION_KEY_EXCHANGE_HEADER = "X-Fed-KeyEx"
FEDERATION_ENCRYPTED_HEADER = "X-Encrypted"
SESSION_SECONDS = 30 * 60
MAX_SESSIONS = 1024


class FederationCryptoError(ValueError):
    pass


class LockedFederationSession:
    def __init__(self, channel):
        self.channel = channel
        self._lock = threading.Lock()

    def encrypt(self, plaintext):
        with self._lock:
            return self.channel.encrypt(plaintext)

    def decrypt(self, envelope):
        with self._lock:
            return self.channel.decrypt(envelope)


def generate_ephemeral():
    private = X25519PrivateKey.generate()
    return private, private.public_key().public_bytes_raw()


def derive_federation_session(
    private, owner_public, consumer_public, token, *, is_initiator
):
    if not isinstance(token, str) or not token:
        raise FederationCryptoError("Invalid federation token")
    try:
        peer = owner_public if is_initiator else consumer_public
        shared = private.exchange(X25519PublicKey.from_public_bytes(peer))
        mixed = hmac.new(token.encode("utf-8"), shared, hashlib.sha256).digest()
        channel = ChannelSession(
            mixed,
            owner_public,
            consumer_public,
            is_master=is_initiator,
        )
    except (TypeError, ValueError, ChannelError) as exc:
        raise FederationCryptoError("Invalid federation key exchange") from exc
    return LockedFederationSession(channel)


@dataclass
class _SessionEntry:
    session: LockedFederationSession
    expires_at: float


class FederationSessionCache:
    def __init__(self, *, ttl=SESSION_SECONDS, maximum=MAX_SESSIONS):
        self.ttl = ttl
        self.maximum = maximum
        self._entries = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode("utf-8")).digest()

    def get(self, token):
        now, key = monotonic(), self._key(token)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            entry.expires_at = now + self.ttl
            return entry.session

    def set(self, token, session):
        now, key = monotonic(), self._key(token)
        with self._lock:
            expired = [
                current for current, entry in self._entries.items()
                if entry.expires_at <= now
            ]
            for current in expired:
                self._entries.pop(current, None)
            if key not in self._entries and len(self._entries) >= self.maximum:
                oldest = min(
                    self._entries, key=lambda current: self._entries[current].expires_at
                )
                self._entries.pop(oldest, None)
            self._entries[key] = _SessionEntry(session, now + self.ttl)

    def delete(self, token):
        with self._lock:
            self._entries.pop(self._key(token), None)
