from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class PublicProbeStreamLimits:
    max_clients: int = 200
    max_per_ip: int = 5
    broadcast_interval_sec: float = 5.0


class PublicProbeStreamManager:
    def __init__(self, limits: PublicProbeStreamLimits | None = None) -> None:
        self._limits = limits or PublicProbeStreamLimits()
        self._lock = Lock()
        self._client_count = 0
        self._per_ip: dict[str, int] = {}

    @property
    def broadcast_interval_sec(self) -> float:
        return self._limits.broadcast_interval_sec

    def try_connect(self, client_ip: str) -> bool:
        with self._lock:
            if self._client_count >= self._limits.max_clients:
                return False
            if self._per_ip.get(client_ip, 0) >= self._limits.max_per_ip:
                return False

            self._client_count += 1
            self._per_ip[client_ip] = self._per_ip.get(client_ip, 0) + 1
            return True

    def disconnect(self, client_ip: str) -> None:
        with self._lock:
            if self._client_count > 0:
                self._client_count -= 1

            count = self._per_ip.get(client_ip, 0)
            if count <= 1:
                self._per_ip.pop(client_ip, None)
            else:
                self._per_ip[client_ip] = count - 1
