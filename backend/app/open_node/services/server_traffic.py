import asyncio
import calendar
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from open_node.domain.inventory import ServerTrafficRead
from open_node.domain.probe import ProbeDailyTraffic
from open_node.services.inventory import (
    ServerModel,
    ServerNotFoundError,
    ServerTrafficDailyModel,
    ServerTrafficModel,
    TelemetrySnapshotModel,
)

log = logging.getLogger(__name__)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def boundary(now, day):
    return now.replace(
        day=min(day, calendar.monthrange(now.year, now.month)[1]),
        hour=0,
        minute=5,
        second=0,
        microsecond=0,
    )


class ServerTrafficCoordinator:
    def __init__(self, store):
        self.store = store

    def _server(self, session, server_id):
        server = session.get(ServerModel, str(server_id))
        if server is None:
            raise ServerNotFoundError(f"server not found: {server_id}")
        return server

    @staticmethod
    def _state(session, server_id, source):
        row = session.get(ServerTrafficModel, (server_id, source))
        if row is None:
            row = ServerTrafficModel(
                server_id=server_id,
                source=source,
                counters={},
                upload=0,
                download=0,
                baseline_upload=0,
                baseline_download=0,
            )
            session.add(row)
            session.flush()
        return row

    def backfill(self):
        with self.store._coordinated_session() as session:
            for server in session.scalars(select(ServerModel)).all():
                if session.get(ServerTrafficModel, (server.id, "xray")) is not None:
                    continue
                self._state(session, server.id, "xray")
                self._state(session, server.id, "system")
                snapshots = session.scalars(
                    select(TelemetrySnapshotModel)
                    .where(TelemetrySnapshotModel.server_id == server.id)
                    .order_by(
                        TelemetrySnapshotModel.reported_at,
                        TelemetrySnapshotModel.received_at,
                        TelemetrySnapshotModel.id,
                    )
                    .execution_options(yield_per=500)
                )
                for snapshot in snapshots:
                    self.record(session, server, snapshot)
                if server.last_traffic_reset_at is None:
                    server.last_traffic_reset_at = server.created_at
            session.commit()

    def record(self, session, server, snapshot):
        for source in ("xray", "system"):
            row = self._state(session, server.id, source)
            at = aware(snapshot.reported_at)
            if row.last_reported_at and at <= aware(row.last_reported_at):
                continue
            previous = row.counters
            if source == "xray":
                if snapshot.stats is None:
                    continue
                counters = {}
                # Node counters include both proxy legs, but never user counters again.
                for kind in ("inbound", "outbound"):
                    for tag, item in (snapshot.stats.get(kind) or {}).items():
                        counters[f"{kind}:{tag}"] = [
                            self.store._traffic_counter_value(item.get("uplink")),
                            self.store._traffic_counter_value(item.get("downlink")),
                        ]
                delta = [0, 0]
                for key, values in counters.items():
                    old = previous.get(key, [0, 0])
                    for index in (0, 1):
                        delta[index] += (
                            values[index] - old[index]
                            if values[index] >= old[index]
                            else values[index]
                        )
            else:
                if snapshot.system_tx_total is None or snapshot.system_rx_total is None:
                    continue
                counters = {
                    "values": [snapshot.system_tx_total, snapshot.system_rx_total],
                    "boot": snapshot.system_boot_time_unix,
                }
                old = previous.get("values")
                # Host counters predate enrollment. Reboots/drops establish a fresh baseline.
                valid = (
                    old is not None
                    and previous.get("boot") == counters["boot"]
                    and all(counters["values"][i] >= old[i] for i in (0, 1))
                )
                delta = [counters["values"][i] - old[i] if valid else 0 for i in (0, 1)]

            row.upload += delta[0]
            row.download += delta[1]
            if row.baseline_at and (at <= aware(row.baseline_at) or row.last_reported_at is None):
                row.baseline_upload += delta[0]
                row.baseline_download += delta[1]
            # The first Xray snapshot has no known day of consumption.
            if row.last_reported_at is not None and any(delta):
                key = (server.id, source, at.date().isoformat())
                daily = session.get(ServerTrafficDailyModel, key)
                if daily is None:
                    daily = ServerTrafficDailyModel(
                        server_id=server.id, source=source, day=key[2], upload=0, download=0
                    )
                    session.add(daily)
                daily.upload += delta[0]
                daily.download += delta[1]
            row.counters = counters
            row.last_reported_at = at

    def read_in_session(self, session, server, now=None):
        row = session.get(ServerTrafficModel, (server.id, server.traffic_source))
        up = max(0, row.upload - row.baseline_upload) if row else 0
        down = max(0, row.download - row.baseline_download) if row else 0
        used = {"both": up + down, "upload": up, "download": down, "max": max(up, down)}[
            server.traffic_stats_mode
        ]
        now = aware(now or datetime.now(UTC))
        next_reset = None
        if server.traffic_reset_day:
            next_reset = boundary(now, server.traffic_reset_day)
            marker = server.last_traffic_reset_at or server.created_at
            # Manual resets satisfy a scheduled boundary, but not a later one.
            if aware(marker) >= next_reset.replace(minute=0):
                following = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
                next_reset = boundary(following, server.traffic_reset_day)
        return ServerTrafficRead(
            server_id=server.id,
            traffic_limit=server.traffic_limit,
            traffic_reset_day=server.traffic_reset_day,
            traffic_source=server.traffic_source,
            traffic_stats_mode=server.traffic_stats_mode,
            upload=up,
            download=down,
            used=used,
            cumulative_upload=row.upload if row else 0,
            cumulative_download=row.download if row else 0,
            last_reported_at=aware(row.last_reported_at) if row and row.last_reported_at else None,
            last_reset_at=aware(server.last_traffic_reset_at)
            if server.last_traffic_reset_at
            else None,
            next_reset_at=next_reset,
        )

    def read(self, server_id):
        with self.store._session() as session:
            return self.read_in_session(session, self._server(session, server_id))

    def update(self, server_id, payload):
        with self.store._coordinated_session() as session:
            server = self._server(session, server_id)
            for key, value in payload.model_dump(mode="json").items():
                setattr(server, key, value)
            server.updated_at = datetime.now(UTC)
            session.commit()
            return self.read_in_session(session, server)

    def _reset(self, session, server, at):
        for source in ("xray", "system"):
            row = self._state(session, server.id, source)
            row.baseline_upload, row.baseline_download = row.upload, row.download
            row.baseline_at = at
        server.last_traffic_reset_at = at
        server.updated_at = at

    def reset(self, server_id, now=None):
        with self.store._coordinated_session() as session:
            server = self._server(session, server_id)
            self._reset(session, server, aware(now or datetime.now(UTC)))
            session.commit()
            return self.read_in_session(session, server, now)

    def reset_due(self, now=None):
        now = aware(now or datetime.now(UTC))
        count = 0
        with self.store._coordinated_session() as session:
            servers = session.scalars(
                select(ServerModel).where(ServerModel.traffic_reset_day > 0)
            ).all()
            for server in servers:
                due = boundary(now, server.traffic_reset_day)
                last = aware(server.last_traffic_reset_at or server.created_at)
                if now >= due and last < due.replace(minute=0):
                    self._reset(session, server, now)
                    count += 1
            session.commit()
        return count

    @staticmethod
    def daily(session, server, day_count=7):
        end = datetime.now(UTC).date()
        start = end - timedelta(days=day_count - 1)
        rows = session.scalars(
            select(ServerTrafficDailyModel).where(
                ServerTrafficDailyModel.server_id == server.id,
                ServerTrafficDailyModel.source == server.traffic_source,
                ServerTrafficDailyModel.day >= start.isoformat(),
                ServerTrafficDailyModel.day <= end.isoformat(),
            )
        ).all()
        if not rows:
            return None
        by_day = {row.day: row for row in rows}
        result = []
        for offset in range(day_count):
            day = (start + timedelta(days=offset)).isoformat()
            row = by_day.get(day)
            up, down = (row.upload, row.download) if row else (0, 0)
            result.append(ProbeDailyTraffic(date=day, uplink=up, downlink=down, total=up + down))
        return result


class ServerTrafficWorker:
    def __init__(self, store, interval=60):
        self.store, self.interval = store, interval

    async def tick(self):
        return await asyncio.to_thread(self.store._server_traffic().reset_due)

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Server traffic cycle reset failed")
            await asyncio.sleep(self.interval)
