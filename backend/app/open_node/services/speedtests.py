"""Durable speed-test history, paired tester transport and async coordination."""

import asyncio
import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from secrets import token_urlsafe
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.speedtests import (
    SpeedTesterRead,
    SpeedTestError,
    SpeedTesterSecret,
    SpeedTestersRead,
    SpeedTestResultRead,
    SpeedTestRunAccepted,
    SpeedTestRunRequest,
)
from open_node.services.backup_runtime import backup_operation
from open_node.services.inventory import Base, ManagedNodeModel
from open_node.services.mihomo_speedtest import Measurement, MihomoSpeedTest


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SpeedTesterModel(Base):
    __tablename__ = "speed_testers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80))
    caps: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpeedTestResultModel(Base):
    __tablename__ = "speed_test_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("managed_nodes.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(20))
    tester_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("speed_testers.id", ondelete="SET NULL"), nullable=True
    )
    tester_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    down_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    egress_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_speed_test_results_node_created", "node_id", "created_at"),)


class SpeedTestStore:
    def __init__(self, inventory, backup_writes):
        self.inventory, self.backup_writes = inventory, backup_writes

    def create_schema(self) -> None:
        SpeedTesterModel.__table__.create(self.inventory._engine, checkfirst=True)
        SpeedTestResultModel.__table__.create(self.inventory._engine, checkfirst=True)
        with self.inventory._session_factory.begin() as db:
            db.execute(
                update(SpeedTestResultModel)
                .where(SpeedTestResultModel.status == "running")
                .values(
                    status="failed", error_code="speedtest_runtime_unavailable",
                    completed_at=_now(),
                )
            )

    @staticmethod
    def _tester(row: SpeedTesterModel, online: bool) -> SpeedTesterRead:
        return SpeedTesterRead(
            id=row.id, name=row.name, online=online, caps=list(row.caps or []),
            version=row.version, last_seen_at=_aware(row.last_seen_at),
            created_at=_aware(row.created_at), created_by=row.created_by,
        )

    @staticmethod
    def _result(row: SpeedTestResultModel) -> SpeedTestResultRead:
        return SpeedTestResultRead(
            id=row.id, node_id=row.node_id, node_name=row.node_name, source=row.source,
            tester_id=row.tester_id, tester_name=row.tester_name, status=row.status,
            down_mbps=row.down_mbps, latency_ms=row.latency_ms, egress_ip=row.egress_ip,
            bytes=row.test_bytes, error_code=row.error_code,
            created_at=_aware(row.created_at), completed_at=_aware(row.completed_at),
        )

    def create_tester(self, name: str, created_by: str, online: set[str]) -> SpeedTesterSecret:
        token, now = token_urlsafe(32), _now()
        row = SpeedTesterModel(
            id=str(uuid4()), name=name, token_hash=_token_hash(token), created_by=created_by,
            caps=[], version=None, last_seen_at=None, created_at=now, updated_at=now,
        )
        with self.inventory._session_factory.begin() as db:
            db.add(row)
        return SpeedTesterSecret(tester=self._tester(row, row.id in online), token=token)

    def list_testers(self, online: set[str]) -> SpeedTestersRead:
        with self.inventory._session_factory() as db:
            rows = db.scalars(select(SpeedTesterModel).order_by(SpeedTesterModel.created_at)).all()
            return SpeedTestersRead(testers=[self._tester(row, row.id in online) for row in rows])

    def tester(self, identifier: str) -> SpeedTesterModel:
        with self.inventory._session_factory() as db:
            row = db.get(SpeedTesterModel, identifier)
            if row is None:
                raise SpeedTestError(404, "speedtest_tester_not_found")
            db.expunge(row)
            return row

    def authenticate(self, token: str) -> tuple[str, str] | None:
        if not 32 <= len(token) <= 128 or not token.isascii():
            return None
        with self.inventory._session_factory() as db:
            row = db.scalar(
                select(SpeedTesterModel).where(SpeedTesterModel.token_hash == _token_hash(token))
            )
            return (row.id, row.name) if row else None

    def rotate(self, identifier: str, online: set[str]) -> SpeedTesterSecret:
        token, now = token_urlsafe(32), _now()
        with self.inventory._session_factory.begin() as db:
            row = db.get(SpeedTesterModel, identifier)
            if row is None:
                raise SpeedTestError(404, "speedtest_tester_not_found")
            row.token_hash, row.updated_at = _token_hash(token), now
            db.flush()
            result = self._tester(row, row.id in online)
        return SpeedTesterSecret(tester=result, token=token)

    def revoke(self, identifier: str) -> None:
        with self.inventory._session_factory.begin() as db:
            row = db.get(SpeedTesterModel, identifier)
            if row is None:
                raise SpeedTestError(404, "speedtest_tester_not_found")
            db.delete(row)

    def touch(self, identifier: str, caps: list[str] | None = None, version: str | None = None):
        with backup_operation(self.backup_writes):
            with self.inventory._session_factory.begin() as db:
                row = db.get(SpeedTesterModel, identifier)
                if row is None:
                    return
                row.last_seen_at = row.updated_at = _now()
                if caps is not None:
                    row.caps = caps
                    row.version = version

    def insert_running(
        self, node_id: str, node_name: str, payload: SpeedTestRunRequest,
        tester_name: str | None,
    ) -> SpeedTestResultRead:
        row = SpeedTestResultModel(
            id=str(uuid4()), node_id=node_id, node_name=node_name,
            source="tester" if payload.tester_id else "master",
            tester_id=str(payload.tester_id) if payload.tester_id else None,
            tester_name=tester_name, status="running", down_mbps=None, latency_ms=None,
            egress_ip=None, test_bytes=payload.bytes, error_code=None,
            created_at=_now(), completed_at=None,
        )
        with self.inventory._session_factory.begin() as db:
            if db.get(ManagedNodeModel, node_id) is None:
                raise SpeedTestError(404, "speedtest_node_not_found")
            db.add(row)
        return self._result(row)

    def finish(self, identifier: str, measurement: Measurement | None, error_code: str | None):
        with backup_operation(self.backup_writes):
            with self.inventory._session_factory.begin() as db:
                row = db.get(SpeedTestResultModel, identifier)
                if row is None or row.status != "running":
                    return
                row.status = "failed" if error_code else "ok"
                row.error_code = error_code
                row.completed_at = _now()
                if measurement is not None:
                    row.down_mbps = measurement.down_mbps
                    row.latency_ms = measurement.latency_ms
                    row.egress_ip = measurement.egress_ip
                    row.test_bytes = measurement.bytes

    def results(
        self, *, node_id: str | None = None, limit: int = 50, latest: bool = False
    ) -> list[SpeedTestResultRead]:
        with self.inventory._session_factory() as db:
            statement = select(SpeedTestResultModel).order_by(
                SpeedTestResultModel.created_at.desc()
            )
            if node_id:
                statement = statement.where(SpeedTestResultModel.node_id == node_id)
            rows = db.scalars(statement.limit(1000 if latest else limit)).all()
            if latest:
                unique = {}
                for row in rows:
                    unique.setdefault(row.node_id, row)
                rows = list(unique.values())
            return [self._result(row) for row in rows]


@dataclass
class _TesterConnection:
    id: str
    name: str
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)


class SpeedTesterConnections:
    def __init__(self, store: SpeedTestStore):
        self.store = store
        self._connections: dict[str, _TesterConnection] = {}
        self._lock = asyncio.Lock()

    def online_ids(self) -> set[str]:
        return set(self._connections)

    async def serve(self, websocket: WebSocket) -> None:
        token = websocket.query_params.get("token", "")
        identity = await asyncio.to_thread(self.store.authenticate, token)
        if identity is None:
            await websocket.accept()
            await websocket.close(code=1008, reason="invalid token")
            return
        identifier, name = identity
        connection = _TesterConnection(identifier, name, websocket)
        await websocket.accept()
        async with self._lock:
            old = self._connections.get(identifier)
            self._connections[identifier] = connection
        if old is not None:
            with suppress(Exception):
                await old.websocket.close(code=1012, reason="reconnected")
        await asyncio.to_thread(self.store.touch, identifier)
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > 65_536:
                    await websocket.close(code=1009)
                    return
                try:
                    message = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                message_type = message.get("type")
                if message_type in {"hello", "ping"}:
                    caps = None
                    version = None
                    if message_type == "hello":
                        raw_caps = message.get("caps", [])
                        if isinstance(raw_caps, list):
                            caps = sorted({
                                item for item in raw_caps
                                if isinstance(item, str) and 1 <= len(item) <= 40 and item.isascii()
                            })[:32]
                        raw_version = message.get("version")
                        if isinstance(raw_version, str) and len(raw_version) <= 80:
                            version = raw_version
                    await asyncio.to_thread(self.store.touch, identifier, caps, version)
                    await connection.send({"type": "pong"})
                    continue
                if message_type != "result":
                    continue
                job_id = message.get("job_id")
                future = connection.pending.get(job_id) if isinstance(job_id, str) else None
                if future is not None and not future.done():
                    future.set_result(message)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            async with self._lock:
                if self._connections.get(identifier) is connection:
                    self._connections.pop(identifier, None)
            for future in connection.pending.values():
                if not future.done():
                    future.set_exception(SpeedTestError(503, "speedtest_dispatch_failed"))

    async def disconnect(self, identifier: str) -> None:
        connection = self._connections.get(identifier)
        if connection:
            with suppress(Exception):
                await connection.websocket.close(code=1008, reason="token revoked")

    async def close(self) -> None:
        for identifier in list(self._connections):
            await self.disconnect(identifier)

    async def dispatch(self, identifier: str, proxy: dict[str, Any], payload) -> Measurement:
        connection = self._connections.get(identifier)
        if connection is None:
            raise SpeedTestError(503, "speedtest_tester_offline")
        async with connection.dispatch_lock:
            if self._connections.get(identifier) is not connection:
                raise SpeedTestError(503, "speedtest_tester_offline")
            job_id = str(uuid4())
            future = asyncio.get_running_loop().create_future()
            connection.pending[job_id] = future
            try:
                await connection.send({
                    "type": "run", "job_id": job_id,
                    "clash_config": json.dumps(proxy, ensure_ascii=False, separators=(",", ":")),
                    "bytes": payload.bytes, "url": str(payload.url) if payload.url else "",
                    "threads": payload.threads, "buf_size": payload.buf_size,
                    "latency_only": payload.latency_only,
                })
                try:
                    message = await asyncio.wait_for(future, 120)
                except TimeoutError:
                    raise SpeedTestError(504, "speedtest_timeout") from None
            finally:
                connection.pending.pop(job_id, None)
            if message.get("status") != "ok":
                raise SpeedTestError(502, "speedtest_download_failed")
            try:
                down = float(message.get("down_mbps")) if not payload.latency_only else None
                latency = float(message.get("latency_ms"))
                egress = message.get("egress_ip") or None
                if egress is not None:
                    egress = str(ip_address(egress))
                received = int(message.get("bytes", payload.bytes or 0))
                if down is not None and not 0 <= down <= 1_000_000:
                    raise ValueError
                if not 0 <= latency <= 600_000 or not 0 <= received <= 2_147_483_648:
                    raise ValueError
            except (TypeError, ValueError):
                raise SpeedTestError(502, "speedtest_dispatch_failed") from None
            return Measurement(down, latency, egress, received)


class SpeedTestCoordinator:
    def __init__(
        self, store: SpeedTestStore, inventory, mihomo: MihomoSpeedTest,
        connections: SpeedTesterConnections,
    ):
        self.store, self.inventory = store, inventory
        self.mihomo, self.connections = mihomo, connections
        self._tasks: set[asyncio.Task] = set()

    async def queue(self, payload: SpeedTestRunRequest) -> SpeedTestRunAccepted:
        node_name, proxy = await asyncio.to_thread(
            self.inventory.speedtest_proxy_config, str(payload.node_id)
        )
        tester_name = None
        if payload.tester_id:
            tester = await asyncio.to_thread(self.store.tester, str(payload.tester_id))
            if str(payload.tester_id) not in self.connections.online_ids():
                raise SpeedTestError(503, "speedtest_tester_offline")
            tester_name = tester.name
        result = await asyncio.to_thread(
            self.store.insert_running, str(payload.node_id), node_name, payload, tester_name
        )
        task = asyncio.create_task(self._execute(str(result.id), proxy, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return SpeedTestRunAccepted(result=result)

    async def _execute(self, identifier: str, proxy: dict[str, Any], payload) -> None:
        measurement = None
        error_code = None
        try:
            async with asyncio.timeout(180):
                if payload.tester_id:
                    measurement = await self.connections.dispatch(
                        str(payload.tester_id), proxy, payload
                    )
                else:
                    measurement = await self.mihomo.run(
                        proxy, requested_bytes=payload.bytes,
                        url=str(payload.url) if payload.url else None,
                        threads=payload.threads, buf_size=payload.buf_size,
                        latency_only=payload.latency_only,
                    )
        except asyncio.CancelledError:
            error_code = "speedtest_runtime_unavailable"
            raise
        except TimeoutError:
            error_code = "speedtest_timeout"
        except SpeedTestError as exc:
            error_code = exc.code
        except Exception:
            error_code = (
                "speedtest_dispatch_failed" if payload.tester_id else "speedtest_download_failed"
            )
        finally:
            await asyncio.to_thread(self.store.finish, identifier, measurement, error_code)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
