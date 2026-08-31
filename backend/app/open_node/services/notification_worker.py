"""Independent, durable notification dispatch; no outbound work in request handlers."""

import asyncio
import logging
import time
from datetime import UTC, datetime

from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_runtime import backup_operation, run_in_backup_thread
from open_node.services.telegram_transport import SEND_TIMEOUT_SECONDS, TelegramOutcome

log = logging.getLogger(__name__)
LEASE_SECONDS = 40
SCAN_SECONDS = 60
SHUTDOWN_RECEIPT_SECONDS = 3


class NotificationWorker:
    def __init__(
        self,
        store,
        transport,
        *,
        interval=1,
        clock=None,
        monotonic=None,
        backup_writes: BackupWriteBarrier | None = None,
    ):
        self.store = store
        self.transport = transport
        self.interval = interval
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic = monotonic or time.monotonic
        self._next_scan = float("-inf")
        self.backup_writes = (
            backup_writes if backup_writes is not None else BackupWriteBarrier(None)
        )

    async def _finish(self, claim, outcome):
        try:
            # A shielded shutdown receipt may outlive tick's awaiting task.
            # Both this task and its actual worker retain their own reference.
            with backup_operation(self.backup_writes):
                return await run_in_backup_thread(
                    self.store.finish, claim, outcome, now=self.clock()
                )
        except Exception:
            # A failed receipt commit leaves durable `sending` for conservative
            # deadline recovery. Never log a claim, HTTP exception or SQL values.
            log.warning("Notification receipt could not be persisted; recovery is required")
            return None

    async def _interrupted(self, claim):
        receipt = asyncio.create_task(
            self._finish(
                claim,
                TelegramOutcome(state="unknown", code="notification_worker_interrupted"),
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(receipt), SHUTDOWN_RECEIPT_SECONDS)
        except TimeoutError:
            # The short DB operation may finish after the bounded shutdown wait.
            # If it cannot, lease recovery still prevents automatic resending.
            log.warning("Notification shutdown receipt is pending durable recovery")

    async def tick(self):
        with backup_operation(self.backup_writes):
            return await self._tick()

    async def _tick(self):
        await run_in_backup_thread(self.store.recover, now=self.clock())
        current = self.monotonic()
        if current >= self._next_scan:
            await run_in_backup_thread(self.store.scan, now=self.clock())
            self._next_scan = self.monotonic() + SCAN_SECONDS

        claim = await run_in_backup_thread(
            self.store.claim, now=self.clock(), lease_seconds=LEASE_SECONDS
        )
        if claim is None:
            return False

        # A busy database or a paused process may consume a claim's lease before
        # it reaches the network. That case is known not to have sent anything.
        remaining = (claim.deadline_at - self.clock()).total_seconds()
        if remaining < SEND_TIMEOUT_SECONDS + 2:
            await self._finish(
                claim,
                TelegramOutcome(
                    state="failed", code="notification_claim_expired", retryable=True
                ),
            )
            return True

        try:
            # The transport enforces its own complete wire deadline. This guard
            # also bounds a defective adapter; it never retries a partial send.
            async with asyncio.timeout(SEND_TIMEOUT_SECONDS + 1):
                outcome = await self.transport.send(claim.token, claim.chat_id, claim.text)
        except asyncio.CancelledError:
            await self._interrupted(claim)
            raise
        except Exception:
            log.warning("Notification transport ended without a reliable receipt")
            outcome = TelegramOutcome(state="unknown", code="notification_transport_failure")
        await self._finish(claim, outcome)
        return True

    async def run(self):
        failure_delay = self.interval
        next_failure_log = float("-inf")
        while True:
            try:
                await self.tick()
            except BackupBusyError:
                failure_delay = self.interval
                delay = self.interval
            except Exception:
                current = self.monotonic()
                if current >= next_failure_log:
                    log.warning("Notification worker cycle failed; durable state is retained")
                    next_failure_log = current + 60
                failure_delay = min(failure_delay * 2, 60)
                delay = failure_delay
            else:
                failure_delay = self.interval
                delay = self.interval
            await asyncio.sleep(delay)
