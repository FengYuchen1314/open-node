import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from open_node.domain.inventory import AgentTelemetryReport, ServerTrafficUpdate
from open_node.services.inventory import ServerModel, TelemetrySnapshotModel
from open_node.services.server_traffic import ServerTrafficWorker
from sqlalchemy import select, text
from test_inventory import make_client


@pytest.fixture
def env(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "billing"}).json()
    return client, created, client.app.state.inventory


def report(env, offset, *, stats=None, system=None, at=None):
    client, created, _ = env
    payload = {
        "token": created["agent_token"],
        "reported_at": (
            at or datetime(2024, 1, 1, tzinfo=UTC) + timedelta(seconds=offset)
        ).isoformat(),
        "stats": stats,
        "system": system,
    }
    response = client.post("/api/v1/agents/telemetry", json=payload)
    assert response.status_code == 200, response.text
    return payload


def stats(up, down, **extra):
    return {"inbound": {"edge": {"uplink": up, "downlink": down}}, **extra}


def url(env):
    return f"/api/v1/servers/{env[1]['server']['id']}/traffic"


def read(env):
    response = env[0].get(url(env))
    assert response.status_code == 200, response.text
    return response.json()


def configure(env, **changes):
    payload = (
        dict(
            traffic_limit=1000,
            traffic_reset_day=0,
            traffic_source="xray",
            traffic_stats_mode="both",
        )
        | changes
    )
    response = env[0].put(url(env), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "mode,expected", [("both", 240), ("upload", 170), ("download", 70), ("max", 170)]
)
def test_node_totals_modes_and_public_probe(env, mode, expected):
    report(
        env,
        0,
        stats=stats(
            70,
            40,
            outbound={"direct": {"uplink": 100, "downlink": 30}},
            user={"person": {"uplink": 9000, "downlink": 9000}},
        ),
    )
    result = configure(env, traffic_stats_mode=mode)
    assert (result["upload"], result["download"], result["used"]) == (170, 70, expected)
    probe = env[0].get("/api/v1/public/probe-servers").json()["servers"][0]
    assert probe["traffic_used"] == expected
    assert probe["traffic_used_total"] == 240
    assert result["license_required"] is False


def test_counter_deltas_are_per_tag_persistent_and_not_double_booked(env):
    report(env, 0, stats={"inbound": {"a": {"uplink": 100}, "b": {"uplink": 100}}})
    report(env, 1, stats={"inbound": {"a": {"uplink": 2}, "b": {"uplink": 500}}})
    assert read(env)["upload"] == 602
    report(env, 1, stats=stats(9000, 9000))
    report(env, 0, stats=stats(9000, 9000))
    report(env, 2)  # No stats must not erase a counter baseline.
    reopened = type(env[2])(str(env[2]._engine.url))
    reopened.create_schema()
    assert reopened._server_traffic().read(env[1]["server"]["id"]).upload == 602
    report(env, 3, stats={"inbound": {"b": {"uplink": 550}}})
    assert read(env)["upload"] == 652
    report(env, 4, stats={"inbound": {"a": {"uplink": 10}, "b": {"uplink": 560}}})
    assert read(env)["upload"] == 672


def test_system_source_reboots_drops_and_missing_samples(env):
    configure(env, traffic_source="system", traffic_stats_mode="max")
    report(env, 0, system={"tx_total": 1000, "rx_total": 2000, "boot_time_unix": 1})
    assert read(env)["used"] == 0
    report(env, 1, system={"tx_total": 1100, "rx_total": 2200, "boot_time_unix": 1})
    report(env, 2, stats=stats(9000, 9000))
    report(env, 3, system={"tx_total": 1200, "rx_total": 2250, "boot_time_unix": 1})
    assert read(env)["used"] == 250
    report(env, 4, system={"tx_total": 1300, "rx_total": 2400, "boot_time_unix": 2})
    report(env, 5, system={"tx_total": 5, "rx_total": 2450, "boot_time_unix": 2})
    assert read(env)["used"] == 250
    report(env, 6, system={"tx_total": 105, "rx_total": 2550, "boot_time_unix": 2})
    assert (read(env)["upload"], read(env)["download"]) == (300, 350)
    probe = env[0].get("/api/v1/public/probe-servers").json()["servers"][0]
    assert probe["traffic_used"] == 350


def test_reset_keeps_counters_history_and_both_source_baselines(env):
    now = datetime.now(UTC) - timedelta(seconds=10)
    report(
        env,
        0,
        stats=stats(100, 200),
        at=now,
        system={"tx_total": 1000, "rx_total": 2000, "boot_time_unix": 1},
    )
    report(
        env,
        0,
        stats=stats(150, 260),
        at=now + timedelta(seconds=1),
        system={"tx_total": 1100, "rx_total": 2300, "boot_time_unix": 1},
    )
    probe_before = env[0].get("/api/v1/public/probe-servers").json()["servers"][0]
    result = env[0].post(url(env) + "/reset").json()
    assert result["used"] == 0
    assert result["cumulative_upload"] == 150
    probe_after = env[0].get("/api/v1/public/probe-servers").json()["servers"][0]
    assert probe_after["daily_traffic"] == probe_before["daily_traffic"]
    assert probe_after["traffic_used"] == 0
    assert configure(env, traffic_source="system")["used"] == 0
    report(
        env,
        0,
        stats=stats(170, 300),
        at=datetime.now(UTC) + timedelta(seconds=1),
        system={"tx_total": 1200, "rx_total": 2400, "boot_time_unix": 1},
    )
    assert read(env)["used"] == 200
    assert configure(env)["used"] == 60
    assert env[0].get(url(env).rsplit("/traffic", 1)[0] + "/commands").json()["commands"] == []


def test_late_pre_reset_report_is_not_charged_to_new_cycle(env):
    report(env, 0, stats=stats(100, 200))
    env[2]._server_traffic().reset(env[1]["server"]["id"], datetime(2024, 1, 1, 0, 1, tzinfo=UTC))
    report(env, 30, stats=stats(130, 250))
    assert read(env)["used"] == 0
    report(env, 120, stats=stats(150, 300))
    assert read(env)["used"] == 70


def test_first_source_report_after_reset_is_only_a_baseline(env):
    env[2]._server_traffic().reset(env[1]["server"]["id"], datetime(2024, 1, 1, tzinfo=UTC))
    report(env, 10, stats=stats(1_000_000, 2_000_000))
    assert read(env)["used"] == 0
    report(env, 20, stats=stats(1_000_100, 2_000_200))
    assert read(env)["used"] == 300


def test_reset_day_creation_and_manual_reset_before_0005_do_not_reset_again(env):
    configure(env, traffic_reset_day=1)
    at = datetime(2024, 2, 1, 0, 2, tzinfo=UTC)
    with env[2]._coordinated_session() as session:
        server = session.get(ServerModel, env[1]["server"]["id"])
        server.last_traffic_reset_at = at
        session.commit()
    assert env[2]._server_traffic().reset_due(at + timedelta(minutes=4)) == 0
    env[2]._server_traffic().reset(env[1]["server"]["id"], at)
    assert env[2]._server_traffic().reset_due(at + timedelta(minutes=4)) == 0


def test_reset_racing_a_report_keeps_durable_totals_and_future_deltas(env):
    report(env, 0, stats=stats(100, 100))
    now = datetime.now(UTC)
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        if action == "report":
            env[2].record_telemetry(
                AgentTelemetryReport(
                    token=env[1]["agent_token"],
                    stats=stats(200, 200),
                    reported_at=now + timedelta(seconds=1),
                )
            )
        else:
            env[2]._server_traffic().reset(env[1]["server"]["id"], now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ("report", "reset")))
    before = read(env)
    assert before["cumulative_upload"] == 200
    assert before["used"] in (0, 200)
    report(env, 0, stats=stats(225, 225), at=now + timedelta(seconds=2))
    assert read(env)["used"] == before["used"] + 50


@pytest.mark.parametrize("year,day", [(2024, 29), (2025, 28)])
def test_month_end_utc_reset_is_idempotent_and_survives_restart(env, year, day):
    configure(env, traffic_reset_day=31)
    with env[2]._coordinated_session() as session:
        server = session.get(ServerModel, env[1]["server"]["id"])
        server.last_traffic_reset_at = datetime(year, 2, 1, tzinfo=UTC)
        session.commit()
    meter = env[2]._server_traffic()
    before = datetime(year, 2, day, 0, 4, 59, tzinfo=UTC)
    after = before + timedelta(seconds=1)
    assert meter.reset_due(before) == 0
    assert meter.reset_due(after) == 1
    env[2].create_schema()
    assert meter.reset_due(after + timedelta(days=1)) == 0
    assert meter.reset_due(datetime(year, 3, 31, 0, 5, tzinfo=UTC)) == 1


def test_new_server_manual_reset_disabled_schedule_and_delayed_worker(env):
    meter = env[2]._server_traffic()
    now = datetime.now(UTC)
    assert meter.reset_due(now + timedelta(days=40)) == 0
    configure(env, traffic_reset_day=now.day)
    assert meter.reset_due(now) == 0
    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    configure(env, traffic_reset_day=1)
    meter.reset(env[1]["server"]["id"], next_month.replace(hour=1))
    assert meter.reset_due(next_month.replace(day=3)) == 0
    with env[2]._coordinated_session() as session:
        server = session.get(ServerModel, env[1]["server"]["id"])
        server.last_traffic_reset_at = now - timedelta(days=80)
        session.commit()
    assert meter.reset_due(next_month.replace(day=3)) == 1


def test_daily_source_selection_and_missing_reports(env):
    now = datetime.now(UTC) - timedelta(seconds=10)
    report(
        env,
        0,
        stats=stats(100, 100),
        at=now,
        system={"tx_total": 1000, "rx_total": 1000, "boot_time_unix": 1},
    )
    report(env, 0, at=now + timedelta(seconds=1))
    report(
        env,
        0,
        stats=stats(130, 140),
        at=now + timedelta(seconds=2),
        system={"tx_total": 1100, "rx_total": 1200, "boot_time_unix": 1},
    )

    def probe():
        return env[0].get("/api/v1/public/probe-servers").json()["servers"][0]

    assert sum(row["total"] for row in probe()["daily_traffic"]) == 70
    configure(env, traffic_source="system")
    assert sum(row["total"] for row in probe()["daily_traffic"]) == 300


def test_upgrade_replays_history_once_and_preserves_baselines(env):
    report(env, 0, stats=stats(100, 200))
    report(env, 1, stats=stats(5, 10))
    store = env[2]
    with store._engine.begin() as connection:
        connection.execute(text("DROP TABLE server_traffic_daily"))
        connection.execute(text("DROP TABLE server_traffic"))
        connection.execute(text("ALTER TABLE servers DROP COLUMN traffic_reset_day"))
        connection.execute(text("ALTER TABLE servers DROP COLUMN last_traffic_reset_at"))
    store.create_schema()
    assert read(env)["used"] == 315
    assert read(env)["traffic_reset_day"] == 0
    env[0].post(url(env) + "/reset").raise_for_status()
    store.create_schema()
    assert read(env)["used"] == 0
    assert read(env)["cumulative_upload"] == 105
    with store._session() as session:
        assert len(session.scalars(select(TelemetrySnapshotModel)).all()) == 2


def test_concurrent_duplicate_ingestion_and_reset_are_serialized(env):
    payload = report(env, 0, stats=stats(100, 100))
    payload["reported_at"] = datetime.now(UTC).isoformat()
    payload["stats"] = stats(200, 200)
    barrier = Barrier(4)

    def ingest(_):
        barrier.wait()
        env[2].record_telemetry(AgentTelemetryReport.model_validate(payload))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(ingest, range(4)))
    assert read(env)["used"] == 400
    configure(env, traffic_reset_day=1)
    with env[2]._coordinated_session() as session:
        row = session.get(ServerModel, env[1]["server"]["id"])
        row.last_traffic_reset_at = datetime.now(UTC) - timedelta(days=40)
        session.commit()
    barrier = Barrier(4)

    def reset(_):
        barrier.wait()
        return env[2]._server_traffic().reset_due(datetime.now(UTC).replace(hour=1))

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reset, range(4))) == 1
    assert read(env)["used"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"traffic_reset_day": 32},
        {"traffic_reset_day": -1},
        {"traffic_reset_day": None},
        {"traffic_source": "invalid"},
        {"traffic_stats_mode": "invalid"},
        {"traffic_limit": -1},
        {"traffic_limit": 2**53},
        {"traffic_limit": 1.2},
        {"unexpected": True},
    ],
)
def test_config_validation(env, changes):
    payload = (
        dict(
            traffic_limit=1000,
            traffic_reset_day=0,
            traffic_source="xray",
            traffic_stats_mode="both",
        )
        | changes
    )
    assert env[0].put(url(env), json=payload).status_code == 422


def test_traffic_routes_require_administrator_and_csrf(env):
    anonymous = TestClient(env[0].app, base_url="https://testserver")
    assert anonymous.get(url(env)).status_code == 401
    assert anonymous.post(url(env) + "/reset").status_code == 401
    del env[0].headers["X-CSRF-Token"]
    assert env[0].post(url(env) + "/reset").status_code == 403


def test_missing_server_and_no_telemetry(env):
    assert read(env)["last_reported_at"] is None
    assert read(env)["used"] == 0
    path = f"/api/v1/servers/{uuid4()}/traffic"
    assert env[0].get(path).status_code == 404
    assert env[0].post(path + "/reset").status_code == 404
    payload = ServerTrafficUpdate(
        traffic_limit=0, traffic_reset_day=0, traffic_source="system", traffic_stats_mode="both"
    )
    assert env[0].put(path, json=payload.model_dump()).status_code == 404


def test_worker_runs_off_event_loop_and_retries_failures(env):
    worker = ServerTrafficWorker(env[2], interval=0)
    with patch.object(
        worker, "tick", AsyncMock(side_effect=[RuntimeError("db busy"), asyncio.CancelledError])
    ) as tick:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.run())
        assert tick.await_count == 2
    assert asyncio.run(ServerTrafficWorker(env[2]).tick()) == 0
