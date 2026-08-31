from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from open_node.domain.inventory import AgentTelemetryReport
from open_node.resources.agent_installer import configuration_data
from open_node.services.inventory import TelemetrySnapshotModel
from pydantic import ValidationError
from sqlalchemy import select, text, update
from test_subscriber_auth import login, make


@pytest.fixture
def env(tmp_path):
    app, operator, subscriber = make(tmp_path)
    created = operator.post("/api/v1/servers", json={"name": "online-test"}).json()
    yield app, operator, subscriber, created
    operator.close()
    subscriber.close()
    app.state.inventory._engine.dispose()


def sample(created, status="ready", users=None):
    return {
        "token": created["agent_token"],
        "online_users": {"alice": ["198.51.100.2", "2001:db8::1"]} if users is None else users,
        "online_collection": {"status": status, "source": "xray_stats_api", "interval_seconds": 30},
    }


def latest_path(created):
    return f"/api/v1/servers/{created['server']['id']}/telemetry/latest"


def test_private_online_report_and_valid_empty(env):
    _, operator, _, created = env
    response = operator.post("/api/v1/agents/telemetry", json=sample(created))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    response = operator.get(latest_path(created))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    latest = response.json()["latest"]
    assert latest["online_users"] == sample(created)["online_users"]
    assert latest["online_collection"]["status"] == "ready"
    assert latest["online_collection"]["expires_at"].endswith("Z")
    empty = operator.post("/api/v1/agents/telemetry", json=sample(created, users={}))
    assert empty.json()["telemetry"]["online_collection"]["status"] == "ready"
    assert operator.get(latest_path(created)).json()["latest"]["online_users"] == {}


@pytest.mark.parametrize("status", ["not_configured", "stopped", "unsupported", "error"])
def test_unavailable_does_not_return_supplied_ips(env, status):
    app, operator, _, created = env
    response = operator.post("/api/v1/agents/telemetry", json=sample(created, status))
    assert response.status_code == 200
    data = operator.get(latest_path(created)).json()["latest"]
    assert data["online_collection"]["status"] == status
    assert data["online_users"] == {}
    with app.state.inventory._session() as session:
        assert session.scalar(select(TelemetrySnapshotModel)).online_users == {}


def test_limited_unknown_and_expired_samples_are_distinct(env):
    app, operator, _, created = env
    operator.post("/api/v1/agents/telemetry", json=sample(created, "limited"))
    latest = operator.get(latest_path(created)).json()["latest"]
    assert latest["online_collection"]["status"] == "limited"
    with app.state.inventory._engine.begin() as connection:
        connection.execute(update(TelemetrySnapshotModel).values(
            received_at=datetime.now(UTC) - timedelta(seconds=91),
            reported_at=datetime.now(UTC) + timedelta(days=3),
        ))
    expired = operator.get(latest_path(created)).json()["latest"]
    assert expired["online_collection"]["status"] == "stale"
    assert expired["online_users"] == {}
    legacy = sample(created)
    legacy.pop("online_collection")
    operator.post("/api/v1/agents/telemetry", json=legacy)
    legacy_view = operator.get(latest_path(created)).json()["latest"]
    assert legacy_view["online_collection"]["status"] == "unknown"
    assert legacy_view["online_users"] == {}


def test_ips_are_admin_only_and_absent_from_public_probe(env):
    app, operator, subscriber, created = env
    operator.post("/api/v1/agents/telemetry", json=sample(created))
    assert login(subscriber).status_code == 200
    response = subscriber.get(latest_path(created))
    assert response.status_code in {401, 403}
    assert "198.51.100.2" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with TestClient(app, base_url="https://testserver") as anonymous:
        assert anonymous.get(latest_path(created)).status_code in {401, 403}
        public = anonymous.get("/api/v1/public/probe-servers")
        assert public.status_code == 200
        assert "198.51.100.2" not in public.text and "online_users" not in public.text
        assert anonymous.post("/api/v1/agents/telemetry", json={
            **sample(created), "token": "wrong-agent-token",
        }).status_code == 401


@pytest.mark.parametrize("users", [
    {"alice": ["hostname.invalid"]}, {"alice": ["fe80::1%eth0"]},
    {"alice": ["198.51.100.2:443"]}, {"alice\n": ["198.51.100.2"]},
    {"u" * 256: []}, {"alice": ["198.51.100.2"] * 65},
    {str(i): [] for i in range(257)},
    {str(i): ["198.51.100.2"] * 64 for i in range(65)},
])
def test_online_payload_limits(users):
    with pytest.raises(ValidationError):
        AgentTelemetryReport(token="test", online_users=users)


def test_invalid_online_payload_does_not_echo_ips_or_tokens(env):
    _, operator, _, created = env
    payload = sample(created, users={"alice": ["secret-hostname.invalid"]})
    response = operator.post("/api/v1/agents/telemetry", json=payload)
    assert response.status_code == 422
    assert "secret-hostname" not in response.text
    assert created["agent_token"] not in response.text


def test_sqlite_upgrade_adds_nullable_collection_without_inventing_support(env):
    app, operator, _, created = env
    operator.post("/api/v1/agents/telemetry", json=sample(created))
    with app.state.inventory._engine.begin() as connection:
        connection.execute(text("ALTER TABLE telemetry_snapshots DROP COLUMN online_collection"))
    app.state.inventory.create_schema()
    app.state.inventory.create_schema()
    latest = operator.get(latest_path(created)).json()["latest"]
    assert latest["online_collection"]["status"] == "unknown"
    assert latest["online_users"] == {}


def test_bootstrap_config_enables_online_statistics(tmp_path):
    _, xray = configuration_data(
        SimpleNamespace(control_url="https://control.invalid", directory=tmp_path),
        {"configuration": {"agent_token": "secret", "transport": "websocket"}},
        "127.0.0.1:46736",
    )
    assert xray["policy"]["levels"]["0"]["statsUserOnline"] is True
    assert "StatsService" in xray["api"]["services"]
