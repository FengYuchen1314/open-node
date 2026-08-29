from datetime import UTC, datetime
from pathlib import Path

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import LegacySubscriptionPlanCodeModel
from test_subscriber_auth import login, provision
from test_subscriptions import create_catalog_fixture


def setup(tmp_path: Path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'ip-policy.db'}"))
    operator = authenticated_client(app)
    _token, _server_id, _node_id, plan_id = create_catalog_fixture(operator)
    assigned = operator.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan_id}
    )
    assert assigned.status_code == 200, assigned.text
    subscription = operator.post("/api/v1/users/alice/subscription-token")
    assert subscription.status_code == 201, subscription.text
    return app, operator, plan_id, subscription.json()["subscription"]


def public(app, address):
    return TestClient(app, client=(address, 43120))


def test_admin_policy_normalizes_hosts_networks_and_defaults(tmp_path):
    _app, operator, _plan_id, _subscription = setup(tmp_path)
    path = "/api/v1/users/alice/subscription-ip-policy"
    default = operator.get(path)
    assert default.status_code == 200, default.text
    assert default.json() == {
        "username": "alice",
        "enabled": False,
        "networks": [],
        "updated_at": None,
        "license_required": False,
    }

    updated = operator.put(
        path,
        json={
            "networks": [
                "203.0.113.8",
                "203.0.113.8/32",
                "2001:db8:1234::8/48",
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is True
    assert updated.json()["networks"] == ["203.0.113.8/32", "2001:db8:1234::/48"]
    assert updated.json()["updated_at"]

    invalid = operator.put(path, json={"networks": ["not-an-address"]})
    assert invalid.status_code == 422
    assert "Invalid IP address or network" in invalid.text
    assert operator.get("/api/v1/users/missing/subscription-ip-policy").status_code == 404

    created = operator.post("/api/v1/users", json={"username": "group/alice"})
    assert created.status_code == 201, created.text
    encoded = operator.put(
        "/api/v1/user-subscription-ip-policy",
        params={"username": "group/alice"},
        json={"networks": ["192.0.2.0/24"]},
    )
    assert encoded.status_code == 200, encoded.text
    assert encoded.json()["username"] == "group/alice"


def test_long_short_and_legacy_links_enforce_ipv4_and_ipv6_policy(tmp_path):
    app, operator, plan_id, subscription = setup(tmp_path)
    now = datetime.now(UTC)
    with app.state.inventory._session() as session:
        session.add(
            LegacySubscriptionPlanCodeModel(
                code="pkg",
                plan_id=plan_id,
                source_package_id=91,
                source_name="Legacy package",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    policy = operator.put(
        "/api/v1/users/alice/subscription-ip-policy",
        json={"networks": ["203.0.113.0/24", "2001:db8::/48"]},
    )
    assert policy.status_code == 200, policy.text
    paths = [
        f"/api/v1/subscribe/{subscription['token']}",
        f"/api/v1/subscribe/{subscription['short_code']}",
        f"/x/pkg{subscription['short_code']}?format=xray",
    ]
    for address in ("203.0.113.25", "2001:db8::25", "::ffff:203.0.113.25"):
        client = public(app, address)
        for path in paths:
            assert client.get(path).status_code == 200, (address, path)

    denied = public(app, "198.51.100.25")
    for path in paths:
        response = denied.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "subscription not found"
    assert denied.get("/api/v1/subscribe/not-a-real-token").json() == {
        "detail": "subscription not found"
    }

    cleared = operator.put(
        "/api/v1/users/alice/subscription-ip-policy", json={"networks": []}
    )
    assert cleared.status_code == 200 and cleared.json()["enabled"] is False
    assert denied.get(paths[0]).status_code == 200


def test_subscriber_can_manage_only_their_policy_with_csrf(tmp_path):
    app, operator, _plan_id, _subscription = setup(tmp_path)
    provision(operator)
    account = TestClient(app, base_url="https://testserver", client=("192.0.2.40", 43120))
    assert login(account).status_code == 200
    path = "/api/v1/account/subscription-ip-policy"
    assert account.get(path).json()["enabled"] is False
    updated = account.put(path, json={"networks": ["192.0.2.40"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["networks"] == ["192.0.2.40/32"]
    assert operator.get("/api/v1/users/alice/subscription-ip-policy").json()[
        "networks"
    ] == ["192.0.2.40/32"]

    csrf = account.headers.pop("X-CSRF-Token")
    assert account.put(path, json={"networks": []}).status_code == 403
    account.headers["X-CSRF-Token"] = csrf
    assert TestClient(app).get(path).status_code == 401
