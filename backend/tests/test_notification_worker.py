import asyncio
import logging
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from open_node.domain.notifications import ClaimedNotification
from open_node.services import notification_worker as module
from open_node.services.notification_worker import NotificationWorker
from open_node.services.telegram_transport import TelegramOutcome
from pydantic import SecretStr

NOW = datetime(2026, 8, 31, 1, tzinfo=UTC)
TOKEN = "123456:notification-worker-test-secret-only"


def claim(*, seconds=40):
    return ClaimedNotification(
        delivery_id=uuid4(),
        attempt_id=uuid4(),
        token=SecretStr(TOKEN),
        chat_id="-1001234567890",
        text="Open Node 通知测试",
        deadline_at=NOW + timedelta(seconds=seconds),
    )


class Store:
    def __init__(self, claims=()):
        self.claims = list(claims)
        self.calls = []
        self.receipts = []

    def recover(self, *, now):
        self.calls.append(("recover", now))
        return 0

    def scan(self, *, now):
        self.calls.append(("scan", now))
        return 0

    def claim(self, *, now, lease_seconds):
        self.calls.append(("claim", now, lease_seconds))
        return self.claims.pop(0) if self.claims else None

    def finish(self, value, outcome, *, now):
        self.calls.append(("finish", now))
        self.receipts.append((value, outcome))


class Transport:
    def __init__(self, store, *, error=None):
        self.store = store
        self.error = error
        self.calls = []

    async def send(self, token, chat_id, text):
        assert self.store.calls[-1][0] == "claim"
        self.calls.append((token, chat_id, text))
        if self.error:
            raise self.error
        return TelegramOutcome(state="accepted", code="telegram_accepted", message_id=123)


@pytest.mark.asyncio
async def test_worker_commits_claim_before_network_and_receipt_after_network():
    value = claim()
    store = Store([value])
    transport = Transport(store)
    worker = NotificationWorker(store, transport, clock=lambda: NOW, monotonic=lambda: 0)
    assert await worker.tick() is True
    assert [call[0] for call in store.calls] == ["recover", "scan", "claim", "finish"]
    assert store.calls[2][2] == 40
    assert transport.calls == [(value.token, value.chat_id, value.text)]
    assert store.receipts[0][1].state == "accepted"
    assert await worker.tick() is False
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_worker_scans_once_per_minute_but_recovers_and_dispatches_each_tick():
    clock = [0]
    store = Store()
    worker = NotificationWorker(
        store, Transport(store), clock=lambda: NOW, monotonic=lambda: clock[0]
    )
    await worker.tick()
    clock[0] = 59.9
    await worker.tick()
    assert sum(call[0] == "scan" for call in store.calls) == 1
    clock[0] = 60
    await worker.tick()
    assert sum(call[0] == "scan" for call in store.calls) == 2
    assert sum(call[0] == "recover" for call in store.calls) == 3
    assert sum(call[0] == "claim" for call in store.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [21, 0, -10])
async def test_claim_with_insufficient_lease_never_starts_outbound_request(remaining):
    store = Store([claim(seconds=remaining)])
    transport = Transport(store)
    assert await NotificationWorker(store, transport, clock=lambda: NOW).tick()
    assert transport.calls == []
    outcome = store.receipts[0][1]
    assert outcome.state == "failed"
    assert outcome.code == "notification_claim_expired" and outcome.retryable


@pytest.mark.asyncio
async def test_transport_exception_is_unknown_without_secret_logging(caplog):
    store = Store([claim()])
    transport = Transport(store, error=RuntimeError(f"https://api.telegram.org/bot{TOKEN}/send"))
    with caplog.at_level(logging.WARNING):
        await NotificationWorker(store, transport, clock=lambda: NOW).tick()
    outcome = store.receipts[0][1]
    assert outcome.state == "unknown" and not outcome.retryable
    assert outcome.code == "notification_transport_failure"
    assert TOKEN not in caplog.text
    assert "api.telegram.org" not in caplog.text


@pytest.mark.asyncio
async def test_receipt_write_failure_does_not_resend_or_log_database_values(caplog):
    class BrokenReceipt(Store):
        def finish(self, value, outcome, *, now):
            raise RuntimeError("SQL parameters contain " + TOKEN)

    store = BrokenReceipt([claim()])
    transport = Transport(store)
    worker = NotificationWorker(store, transport, clock=lambda: NOW)
    with caplog.at_level(logging.WARNING):
        await worker.tick()
        await worker.tick()
    assert len(transport.calls) == 1
    assert TOKEN not in caplog.text
    assert "recovery is required" in caplog.text


class BlockingTransport:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def send(self, *_):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_cancelled_inflight_send_closes_transport_and_persists_unknown():
    store = Store([claim()])
    transport = BlockingTransport()
    worker = NotificationWorker(store, transport, clock=lambda: NOW)
    task = asyncio.create_task(worker.tick())
    await asyncio.wait_for(transport.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert transport.closed
    outcome = store.receipts[0][1]
    assert outcome.state == "unknown" and not outcome.retryable
    assert outcome.code == "notification_worker_interrupted"


@pytest.mark.asyncio
async def test_shutdown_receipt_wait_is_bounded_and_keeps_late_receipt_alive(monkeypatch):
    release = Event()
    finished = Event()

    class SlowReceipt(Store):
        def finish(self, value, outcome, *, now):
            try:
                assert release.wait(2)
                return super().finish(value, outcome, now=now)
            finally:
                finished.set()

    monkeypatch.setattr(module, "SHUTDOWN_RECEIPT_SECONDS", 0.03)
    store = SlowReceipt([claim()])
    transport = BlockingTransport()
    worker = NotificationWorker(store, transport, clock=lambda: NOW)
    task = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(transport.started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        assert not finished.is_set()
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        await asyncio.sleep(0)
    assert store.receipts[0][1].state == "unknown"


@pytest.mark.asyncio
async def test_cancel_during_database_claim_never_starts_a_send():
    entered = Event()
    release = Event()
    finished = Event()

    class SlowClaim(Store):
        def claim(self, *, now, lease_seconds):
            entered.set()
            try:
                assert release.wait(2)
                return super().claim(now=now, lease_seconds=lease_seconds)
            finally:
                finished.set()

    store = SlowClaim([claim()])
    transport = Transport(store)
    task = asyncio.create_task(NotificationWorker(store, transport, clock=lambda: NOW).tick())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
    assert transport.calls == []
    # A completed-but-unreturned durable claim is recovered by its stored
    # deadline, not guessed safe and replayed by the stopped worker.
    assert store.receipts == []


@pytest.mark.asyncio
async def test_loop_survives_cycle_failure_without_logging_exception_text(caplog):
    reached = asyncio.Event()

    class RecoveringWorker(NotificationWorker):
        attempts = 0

        async def tick(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError(TOKEN)
            reached.set()
            return False

    worker = RecoveringWorker(Store(), None, interval=0.001)
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(reached.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert worker.attempts >= 2
    assert TOKEN not in caplog.text
    assert "durable state is retained" in caplog.text


@pytest.mark.asyncio
async def test_repeated_database_or_key_failure_backs_off_and_does_not_flood_logs(
    monkeypatch, caplog
):
    class BrokenWorker(NotificationWorker):
        async def tick(self):
            raise RuntimeError(TOKEN)

    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 7:
            raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    worker = BrokenWorker(Store(), None, interval=1, monotonic=lambda: 0)
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await worker.run()
    assert delays == [2, 4, 8, 16, 32, 60, 60]
    assert caplog.text.count("durable state is retained") == 1
    assert TOKEN not in caplog.text
