"""Opt-in, owner-scoped external subscription refresh with fenced durable leases."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select

from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_runtime import backup_operation, run_in_backup_thread
from open_node.services.external_fetch import ExternalFetchError
from open_node.services.external_subscription_parser import ExternalSubscriptionParseError
from open_node.services.external_subscriptions import (
    MAX_SAVED_NODES,
    ExternalRefreshModel,
    ExternalSourceModel,
    ExternalSubscriptionUnavailable,
    _utc,
)
from open_node.services.inventory import ProductUserModel

log = logging.getLogger(__name__)
LEASE_SECONDS = 120  # The shared fetcher has a 30-second total wire deadline.


@dataclass(frozen=True)
class RefreshClaim:
    source_id: str
    lease_id: str
    source_revision: int


class ExternalRefreshService:
    def __init__(self, sources, *, clock=None):
        self.sources = sources
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _finish(row, code, now, *, counts=None):
        row.lease_id = row.lease_until = None
        row.last_finished_at = now
        row.code = code
        row.counts = counts or {}
        if code == "refresh_succeeded":
            row.last_success_at = now
            row.consecutive_failures = 0
        elif code != "source_changed":
            row.consecutive_failures = min(row.consecutive_failures + 1, 32)
        delay = min(row.interval_minutes * 2 ** min(max(row.consecutive_failures - 1, 0), 6), 10080)
        row.next_run_at = now + timedelta(minutes=delay) if row.enabled else None

    def claim(self):
        now = self.clock()
        with self.sources._write() as session:
            query = (
                select(ExternalRefreshModel)
                .join(ExternalSourceModel, ExternalSourceModel.id == ExternalRefreshModel.source_id)
                .join(
                    ProductUserModel,
                    ProductUserModel.username == ExternalSourceModel.owner_username,
                )
                .where(
                    ExternalRefreshModel.enabled.is_(True),
                    ExternalSourceModel.enabled.is_(True), ProductUserModel.is_active.is_(True),
                    ProductUserModel.removal_id.is_(None),
                    ExternalRefreshModel.next_run_at <= now,
                    or_(
                        ExternalRefreshModel.lease_id.is_(None),
                        ExternalRefreshModel.lease_until <= now,
                    ),
                )
                .order_by(ExternalRefreshModel.next_run_at, ExternalRefreshModel.source_id)
                .limit(1).with_for_update(skip_locked=True)
            )
            row = session.scalar(query)
            if row is None:
                return None
            if row.lease_id is not None:
                # GET refreshes may be retried, but never replay a pre-crash payload.
                # Persist an interrupted result and back off before fetching again.
                self._finish(row, "worker_interrupted", now)
                return None
            source = session.get(ExternalSourceModel, row.source_id)
            row.lease_id = str(uuid4())
            row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            row.last_attempt_at = now
            return RefreshClaim(source.id, row.lease_id, source.revision)

    def _current(self, session, claim):
        row = session.get(ExternalRefreshModel, claim.source_id)
        if row is None or row.lease_id != claim.lease_id:
            return None
        source = session.get(ExternalSourceModel, claim.source_id)
        owner = session.get(ProductUserModel, source.owner_username) if source else None
        if not (
            row.enabled and source and source.enabled and owner and owner.is_active
            and not owner.removal_id and source.revision == claim.source_revision
            and row.lease_until and _utc(row.lease_until) > self.clock()
        ):
            self._finish(row, "source_changed", self.clock())
            return None
        return row, source

    def fail(self, claim, code):
        with self.sources._write() as session:
            current = self._current(session, claim)
            if current:
                self._finish(current[0], code, self.clock())

    def refresh(self, claim):
        # Resolve secrets only for the current claim; commit before DNS/network.
        with self.sources._write() as session:
            current = self._current(session, claim)
            if current is None:
                return
            _row, source = current
            cipher, _key = self.sources._keys(session)
            secret = self.sources._open(cipher, source, "source", source.secret)
        fetched = self.sources.fetcher(secret["url"], user_agent=secret["user_agent"])
        parsed = self.sources.parser(fetched.body)
        now = self.clock()
        with self.sources._write() as session:
            current = self._current(session, claim)
            if current is None:
                return
            row, source = current
            cipher, _key = self.sources._keys(session)
            existing = {
                node.upstream_name: node for node in self.sources._nodes(session, source.id)
            }
            entries, new_ids = [], set()
            for entry in parsed:
                old = existing.get(entry.name)
                identifier = old.id if old else str(uuid5(UUID(source.id), "node:" + entry.name))
                entries.append(dict(
                    id=identifier, name=entry.name, protocol=entry.protocol,
                    config=entry.config, reason=entry.reason,
                ))
                if old is None and entry.config is not None and entry.reason is None:
                    new_ids.add(identifier)
            selected = new_ids if row.scope == "all" else set()
            if len(existing) + len(selected) > MAX_SAVED_NODES:
                self._finish(row, "node_limit", now)
                return
            counts = self.sources._apply_snapshot(session, source, cipher, entries, selected, now)
            counts["new_available_count"] = len(new_ids - selected)
            source.upstream_metadata = fetched.metadata
            source.last_synced_at = now
            self.sources._bump(session, source, claim.source_revision, now)
            self.sources._purge_expired(session, source.id, now)
            self._finish(row, "refresh_succeeded", now, counts=counts)

    def tick(self):
        claim = self.claim()
        if claim is None:
            return False
        try:
            self.refresh(claim)
        except ExternalFetchError:
            self.fail(claim, "fetch_failed")
        except ExternalSubscriptionParseError:
            self.fail(claim, "parse_failed")
        except ExternalSubscriptionUnavailable:
            self.fail(claim, "credentials_unavailable")
        except Exception:
            # Neither SQL parameters, response snippets nor URLs reach logs/status.
            self.fail(claim, "refresh_failed")
        return True


class ExternalRefreshWorker:
    def __init__(self, sources, *, interval=5, backup_writes=None):
        self.service = ExternalRefreshService(sources)
        self.interval = interval
        self.backup_writes = (
            backup_writes if backup_writes is not None else BackupWriteBarrier(None)
        )

    async def tick(self):
        # The real executor thread retains the barrier even if its task is cancelled.
        with backup_operation(self.backup_writes):
            return await run_in_backup_thread(self.service.tick)

    async def run(self):
        while True:
            try:
                worked = await self.tick()
            except BackupBusyError:
                worked = False
            except Exception:
                log.warning("External subscription refresh cycle failed; saved state is retained")
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(1 if worked else self.interval)
