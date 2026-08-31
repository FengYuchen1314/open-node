"""Real SQLite scheduling, explicit consent, shared merge and stale result fences."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
import yaml
from open_node.services.backup_coordination import BackupBusyError
from open_node.services.external_fetch import ExternalFetchError
from open_node.services.external_refresh import ExternalRefreshService, ExternalRefreshWorker
from open_node.services.external_subscriptions import ExternalRefreshModel
from open_node.services.inventory import ProductUserModel
from test_external_subscriptions import (
    PREFIX,
    ROTATED_UUID,
    SOURCE_URL,
    UPSTREAM_UUID,
    confirm,
    create_source,
    detail,
    feed,
    preview,
    proxy,
    token_path,
)
from test_external_subscriptions import catalog as catalog


def configure(client, source, **overrides):
    payload = dict(expected_revision=source["revision"], enabled=True, interval_minutes=15,
                   scope="saved_only", accept_changes=True)
    payload.update(overrides)
    return client.put(f"{PREFIX}/{source['id']}/refresh-schedule", json=payload)


def due(client, source):
    sources = client.app.state.external_subscriptions
    with sources._write() as session:
        row = session.get(ExternalRefreshModel, source["id"])
        row.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
    return ExternalRefreshService(sources)


def enabled(client, *, scope="saved_only"):
    source = create_source(client)
    response = configure(client, source, scope=scope)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("override", [
    {"accept_changes": False}, {"accept_changes": 1}, {"enabled": 1},
    {"interval_minutes": 14}, {"interval_minutes": 10081}, {"interval_minutes": "15"},
    {"interval_minutes": 15.1}, {"scope": "unknown"}, {"owner_username": "bob"},
])
def test_schedule_requires_strict_consent_and_bounded_interval(catalog, override):
    client, *_ = catalog
    source = create_source(client)
    assert configure(client, source, **override).status_code == 422
    assert detail(client, source)["source"]["refresh"]["enabled"] is False


def test_schedule_is_offline_opt_in_delayed_and_persistent(catalog):
    client, *_ = catalog
    calls = feed(client, [proxy()])
    source = create_source(client)
    path = token_path(client)
    assert not ExternalRefreshService(client.app.state.external_subscriptions).tick()
    response = configure(client, source, scope="all")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["revision"] == source["revision"] + 1
    next_run = datetime.fromisoformat(value["refresh"]["next_run_at"])
    assert next_run > datetime.now(UTC) + timedelta(minutes=14)
    client.get(PREFIX).raise_for_status()
    client.get(path).raise_for_status()
    assert not ExternalRefreshService(client.app.state.inventory.external_subscriptions()).tick()
    assert calls == []
    assert configure(client, source).status_code == 409
    assert due(client, source).tick()
    assert len(calls) == 1
    assert detail(client, source)["source"]["refresh"]["imported_count"] == 1
    assert not due_service(client).tick()  # no immediate repeat after success


def due_service(client):
    return ExternalRefreshService(client.app.state.inventory.external_subscriptions())


def test_saved_only_updates_credentials_missing_nodes_and_preserves_local_choices(catalog):
    client, *_ = catalog
    source = create_source(client)
    feed(client, [proxy(), proxy("Old B")])
    confirm(client, preview(client, source))
    original = detail(client, source)
    row = next(node for node in original["nodes"] if node["upstream_name"] == "Upstream A")
    renamed = client.put(f"{PREFIX}/{source['id']}/nodes/{row['id']}", json={
        "expected_revision": original["source"]["revision"], "name": "自定义名称", "enabled": False,
    })
    assert renamed.status_code == 200
    assert configure(client, renamed.json()["source"]).status_code == 200
    paths = ["/api/v1/users/alice/traffic", "/api/v1/users/alice/credentials", "/api/v1/nodes"]
    before = [client.get(path).json() for path in paths]
    feed(client, [proxy(credential=ROTATED_UUID), proxy("New C")], metadata={"download": 42})
    assert due(client, source).tick()
    after = detail(client, source)
    assert after["source"]["metadata"] == {"download": 42}
    assert after["source"]["refresh"]["code"] == "refresh_succeeded"
    assert {key: after["source"]["refresh"][key] for key in (
        "imported_count", "updated_count", "missing_count", "new_available_count",
    )} == dict(imported_count=0, updated_count=1, missing_count=1, new_available_count=1)
    changed = next(node for node in after["nodes"] if node["id"] == row["id"])
    assert changed["name"] == "自定义名称" and changed["enabled"] is False
    assert len(after["nodes"]) == 2
    assert [client.get(path).json() for path in paths] == before
    # The updated credential is encrypted even though this node remains disabled.
    sources = client.app.state.external_subscriptions
    with sources.store._session() as session:
        saved = sources._source(session, source["id"])
        cipher, _ = sources._keys(session)
        node = next(n for n in sources._nodes(session, saved.id) if n.id == row["id"])
        assert sources._open(cipher, saved, "node:" + node.id, node.secret)["uuid"] == ROTATED_UUID


def test_all_scope_adds_supported_nodes_and_invalidates_manual_preview(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = enabled(client, scope="all")
    pending = preview(client, source)
    path = token_path(client)
    assert due(client, source).tick()
    result = yaml.safe_load(client.get(path).text)["proxies"]
    assert result[-1]["uuid"] == UPSTREAM_UUID
    assert SOURCE_URL not in client.get(PREFIX).text
    response = client.post(f"{PREFIX}/{source['id']}/previews/{pending['id']}/confirm", json={
        "expected_revision": pending["source_revision"], "selected_node_ids": [],
        "accept_changes": True,
    })
    assert response.status_code == 409


@pytest.mark.parametrize("kind,expected", [
    ("fetch", "fetch_failed"), ("parse", "parse_failed"), ("unexpected", "refresh_failed"),
])
def test_failure_retains_snapshot_redacts_details_and_backs_off(catalog, caplog, kind, expected):
    client, *_ = catalog
    feed(client, [proxy()])
    source = enabled(client, scope="all")
    assert due(client, source).tick()
    previous = detail(client, source)
    sources = client.app.state.external_subscriptions
    if kind == "parse":
        from open_node.services.external_fetch import ExternalFetchResult
        sources.fetcher = lambda *a, **k: ExternalFetchResult(
            body=b"not a subscription", metadata={},
        )
    else:
        def fail(*args, **kwargs):
            if kind == "fetch":
                raise ExternalFetchError("timeout")
            raise RuntimeError(SOURCE_URL)
        sources.fetcher = fail
    for count in (1, 2, 3):
        assert due(client, source).tick()
        current = detail(client, source)
        status = current["source"]["refresh"]
        assert status["code"] == expected and status["consecutive_failures"] == count
        delta = (
            datetime.fromisoformat(status["next_run_at"])
            - datetime.fromisoformat(status["last_finished_at"])
        )
        assert delta == timedelta(minutes=15 * 2 ** (count - 1))
        assert current["nodes"] == previous["nodes"]
        assert current["source"]["revision"] == previous["source"]["revision"]
        assert current["source"]["last_synced_at"] == previous["source"]["last_synced_at"]
        assert SOURCE_URL not in str(current) + caplog.text


def test_claim_is_exclusive_and_expired_work_cannot_apply(catalog):
    client, *_ = catalog
    calls = feed(client, [proxy()])
    source = enabled(client, scope="all")
    service = due(client, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: service.claim(), range(2)))
    claim, = [value for value in claims if value is not None]
    assert detail(client, source)["source"]["refresh"]["running"] is True
    service.clock = lambda: datetime.now(UTC) + timedelta(minutes=3)
    assert service.claim() is None
    service.refresh(claim)
    state = detail(client, source)["source"]["refresh"]
    assert state["code"] == "worker_interrupted" and not calls
    assert state["consecutive_failures"] == 1


@pytest.mark.parametrize("change", ["url", "schedule", "disabled", "deleted", "owner_disabled"])
def test_inflight_fetch_cannot_overwrite_newer_decisions(catalog, change):
    client, *_ = catalog
    source = enabled(client, scope="all")
    service = due(client, source)
    sources = client.app.state.external_subscriptions
    feed(client, [proxy()])
    fetch = sources.fetcher
    def concurrent_change(*args, **kwargs):
        if change == "schedule":
            assert configure(client, source, enabled=False, accept_changes=False).status_code == 200
        elif change == "deleted":
            assert client.post(f"{PREFIX}/{source['id']}/delete", json={
                "expected_revision": source["revision"], "confirm": True,
            }).status_code == 200
        elif change == "owner_disabled":
            with sources._write() as session:
                session.get(ProductUserModel, "alice").is_active = False
        else:
            payload = dict(expected_revision=source["revision"], name=source["name"], enabled=False)
            if change == "url":
                payload.update(enabled=True, url="https://another.example/sub")
            assert client.put(f"{PREFIX}/{source['id']}", json=payload).status_code == 200
        return fetch(*args, **kwargs)
    sources.fetcher = concurrent_change
    assert service.tick()
    if change == "deleted":
        assert client.get(f"{PREFIX}/{source['id']}").status_code == 404
    else:
        current = detail(client, source)
        assert current["nodes"] == []
        assert current["source"]["last_synced_at"] is None
        if change in {"url", "schedule"}:
            assert not current["source"]["refresh"]["enabled"]
        assert not service.tick()


def test_node_limit_rolls_back_entire_refresh(catalog, monkeypatch):
    import open_node.services.external_refresh as module
    client, *_ = catalog
    source = enabled(client, scope="all")
    feed(client, [proxy(), proxy("second")])
    monkeypatch.setattr(module, "MAX_SAVED_NODES", 1)
    assert due(client, source).tick()
    current = detail(client, source)
    assert current["nodes"] == []
    assert current["source"]["refresh"]["code"] == "node_limit"
    assert current["source"]["revision"] == source["revision"]


def test_restore_turns_off_refresh_without_affecting_sources(catalog):
    from open_node.services.backup_restore import _quiesce
    client, *_ = catalog
    source = enabled(client, scope="all")
    service = due(client, source)
    assert service.claim() is not None
    database = client.app.state.inventory._engine.url.database
    with sqlite3.connect(database) as connection:
        _quiesce(connection)
    current = client.app.state.external_subscriptions.detail(source["id"])
    assert current.source.refresh.enabled is False
    assert current.source.refresh.code == "restore_paused"
    assert current.source.refresh.next_run_at is None
    assert not current.source.refresh.running
    assert not service.tick()


@pytest.mark.asyncio
async def test_worker_retains_backup_barrier_until_cancelled_executor_really_finishes(catalog):
    client, *_ = catalog
    source = enabled(client, scope="all")
    service = due(client, source)
    feed(client, [proxy()])
    sources = client.app.state.external_subscriptions
    fetch = sources.fetcher
    entered, release, finished = Event(), Event(), Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return fetch(*args, **kwargs)
    sources.fetcher = slow
    barrier = client.app.state.backup_writes
    worker = ExternalRefreshWorker(sources, backup_writes=barrier)
    original = service.tick
    def finalizing():
        try:
            return original()
        finally:
            finished.set()
    worker.service.tick = finalizing
    task = asyncio.create_task(worker.tick())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(BackupBusyError):
            with barrier.snapshot(timeout=0):
                pass
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    for _ in range(100):
        try:
            with barrier.snapshot(timeout=0):
                break
        except BackupBusyError:
            await asyncio.sleep(0.01)
    else:
        pytest.fail("Finished refresh retained the backup barrier")
