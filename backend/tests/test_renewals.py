from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.renewals import RenewalCreate, RenewalDecision, RenewalError
from open_node.main import create_app
from open_node.services.inventory import (
    CommandModel,
    ProductUserModel,
    SubscriptionPlanModel,
)
from open_node.services.renewals import RenewalRequestModel
from open_node.services.subscription_access import SubscriptionAccessConflict
from sqlalchemy import func, select
from test_subscriber_auth import login, provision
from test_subscriptions import create_catalog_fixture

ADMIN = "/api/v1/renewals"
ACCOUNT = "/api/v1/account/renewals"
SECRET = "renewal-reference-ONLY-for-this-request"


def make(tmp_path, *, catalog=False):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'renewals.db'}"))
    operator = authenticated_client(app)
    if catalog:
        _token, _server, _node, plan_id = create_catalog_fixture(operator)
    else:
        assert operator.post("/api/v1/users", json={"username": "alice"}).status_code == 201
        response = operator.post("/api/v1/plans", json={
            "name": "月付套餐", "cycle_days": 30, "traffic_limit_gb": 10,
        })
        assert response.status_code == 201, response.text
        plan_id = response.json()["plan"]["id"]
    response = operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id})
    assert response.status_code == 200, response.text
    provision(operator)
    subscriber = TestClient(app, base_url="https://testserver")
    assert login(subscriber).status_code == 200
    return app, operator, subscriber, plan_id


def submit(subscriber, identifier=None, **extra):
    return subscriber.post(ACCOUNT, json={
        "request_id": identifier or str(uuid4()), "passphrase": SECRET, **extra,
    })


def review(operator, identifier, *, decision="approve", passphrase=SECRET):
    return operator.post(ADMIN + f"/{identifier}/review", json={
        "decision": decision, "confirm_reviewed": True,
        **({"passphrase": passphrase} if decision == "approve" else {}),
    })


def aware(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)


def test_submission_freezes_plan_cycle_and_stores_only_argon_hash(tmp_path):
    app, operator, subscriber, plan_id = make(tmp_path)
    before = subscriber.get(ACCOUNT).json()
    assert before["eligible"] and before["plan_id"] == plan_id
    response = submit(subscriber)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["renew_days"] == 30 and row["status"] == "pending"
    assert row["created_at"].endswith("Z")
    assert SECRET not in response.text and "passphrase" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with app.state.inventory._session() as session:
        stored = session.get(RenewalRequestModel, row["id"])
        assert stored.passphrase_hash.startswith("$argon2id$")
        assert SECRET not in str(stored.__dict__)
        plan = session.get(SubscriptionPlanModel, plan_id)
        plan.cycle_days = 90
        session.commit()
    approved = review(operator, row["id"])
    assert approved.status_code == 200, approved.text
    result = approved.json()["request"]
    assert aware(result["new_end_date"]) == aware(row["previous_end_date"]) + timedelta(days=30)
    with app.state.inventory._session() as session:
        stored = session.get(RenewalRequestModel, row["id"])
        assert stored.passphrase_hash is None and stored.pending_username is None


def test_duplicate_post_pending_and_duplicate_review_extend_only_once(tmp_path):
    app, operator, subscriber, _plan = make(tmp_path)
    identifier = str(uuid4())
    first = submit(subscriber, identifier).json()
    assert submit(subscriber, identifier).json() == first
    assert submit(subscriber).json()["code"] == "renewal_pending"
    approved = review(operator, identifier)
    repeated = review(operator, identifier)
    assert approved.status_code == repeated.status_code == 200
    assert approved.json()["processed"] is True
    assert repeated.json()["processed"] is False and repeated.json()["commands"] == []
    assert approved.json()["request"] == repeated.json()["request"]
    assert review(operator, identifier, decision="reject").status_code == 409
    with app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(RenewalRequestModel)) == 1
        user = session.get(ProductUserModel, "alice")
        expected = aware(first["previous_end_date"]) + timedelta(days=30)
        assert user.plan_expires_at.replace(tzinfo=UTC) == expected


def test_expired_package_renews_from_now_and_preserves_reset_policy_and_limits(tmp_path):
    app, operator, subscriber, _plan = make(tmp_path)
    old_reset = datetime(2026, 1, 3, tzinfo=UTC)
    with app.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        user.plan_expires_at = datetime.now(UTC) - timedelta(days=3)
        user.is_reset, user.reset_day, user.last_traffic_reset_at = True, 9, old_reset
        user.traffic_limit_override_bytes, user.device_limit_override = 123456, 4
        session.commit()
    row = submit(subscriber).json()
    before = datetime.now(UTC)
    response = review(operator, row["id"])
    assert response.status_code == 200, response.text
    expires = aware(response.json()["request"]["new_end_date"])
    assert before + timedelta(days=30) <= expires <= datetime.now(UTC) + timedelta(days=30)
    with app.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        assert user.is_reset and user.reset_day == 9
        assert user.last_traffic_reset_at.replace(tzinfo=UTC) == old_reset
        assert user.traffic_limit_override_bytes == 123456 and user.device_limit_override == 4


def test_approval_uses_later_current_expiry_and_does_not_replace_other_plan(tmp_path):
    app, operator, subscriber, plan_id = make(tmp_path)
    row = submit(subscriber).json()
    later = datetime.now(UTC) + timedelta(days=120)
    with app.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        user.plan_expires_at = later
        session.commit()
    approved = review(operator, row["id"])
    assert aware(approved.json()["request"]["new_end_date"]) == later + timedelta(days=30)
    next_row = submit(subscriber).json()
    other = operator.post("/api/v1/plans", json={
        "name": "变更后的套餐", "traffic_limit_gb": 20,
    }).json()["plan"]["id"]
    assert other != plan_id
    assert operator.post("/api/v1/users/alice/plan", json={"plan_id": other}).status_code == 200
    assert review(operator, next_row["id"]).status_code == 409
    with app.state.inventory._session() as session:
        assert session.get(ProductUserModel, "alice").current_plan_id == other
        assert session.get(RenewalRequestModel, next_row["id"]).status == "pending"


def test_wrong_reference_rejection_and_cancellation_never_extend_package(tmp_path):
    app, operator, subscriber, _plan = make(tmp_path)
    row = submit(subscriber).json()
    wrong = review(operator, row["id"], passphrase="wrong")
    assert wrong.status_code == 400 and wrong.json()["code"] == "renewal_wrong_passphrase"
    assert review(operator, row["id"], decision="reject").json()["request"]["status"] == "rejected"
    second = submit(subscriber).json()
    cancelled = subscriber.post(ACCOUNT + f"/{second['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert subscriber.post(ACCOUNT + f"/{second['id']}/cancel").json() == cancelled.json()
    assert review(operator, second["id"]).status_code == 409
    assert subscriber.get(ACCOUNT).json()["eligible"] is True
    with app.state.inventory._session() as session:
        current = session.get(ProductUserModel, "alice").plan_expires_at.replace(tzinfo=UTC)
        assert current == aware(row["previous_end_date"])


def test_roles_owner_filters_missing_targets_csrf_and_origin(tmp_path):
    app, operator, alice, _plan = make(tmp_path)
    identifier = submit(alice).json()["id"]
    assert operator.post("/api/v1/users", json={"username": "bob"}).status_code == 201
    provision(operator, "bob")
    bob = TestClient(app, base_url="https://testserver")
    assert login(bob, username="bob").status_code == 200
    assert bob.get(ACCOUNT).json()["requests"] == []
    assert bob.get(ACCOUNT + "/" + identifier).status_code == 404
    assert bob.post(ACCOUNT + "/" + identifier + "/cancel").status_code == 404
    assert submit(bob, identifier).status_code == 404
    missing = bob.get(ACCOUNT + "/" + str(uuid4()))
    assert missing.json() == bob.get(ACCOUNT + "/" + identifier).json()
    assert alice.get(ADMIN).status_code == 401
    assert operator.get(ACCOUNT).status_code == 401
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get(ACCOUNT).status_code == anonymous.get(ADMIN).status_code == 401
    assert alice.post(ACCOUNT, json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert alice.post(
        ACCOUNT, json={}, headers={"Origin": "https://other.invalid"}
    ).status_code == 403
    assert operator.post(
        ADMIN + f"/{identifier}/review", json={}, headers={"X-CSRF-Token": "wrong"}
    ).status_code == 403


@pytest.mark.parametrize("changes", [
    {"username": "bob"}, {"renew_days": 365}, {"plan_id": str(uuid4())},
    {"passphrase": ""}, {"passphrase": "a\nb"}, {"passphrase": "x" * 257},
])
def test_subscriber_cannot_choose_identity_plan_cycle_or_invalid_reference(tmp_path, changes):
    _app, _operator, subscriber, _plan = make(tmp_path)
    response = submit(subscriber, **changes)
    assert response.status_code == 422
    assert response.json()["code"] == "renewal_invalid_request"
    assert SECRET not in response.text and "bob" not in response.text


def test_raw_secret_json_is_bounded_and_errors_never_echo_values(tmp_path):
    _app, operator, subscriber, _plan = make(tmp_path)
    duplicated = subscriber.post(
        ACCOUNT, content='{"passphrase":"PRIVATE","passphrase":"OTHER"}',
        headers={"Content-Type": "application/json"},
    )
    oversized = subscriber.post(
        ACCOUNT, content="PRIVATE" * 1500, headers={"Content-Type": "application/json"}
    )
    assert duplicated.status_code == 422 and oversized.status_code == 413
    assert "PRIVATE" not in duplicated.text + oversized.text
    invalid = operator.post(ADMIN + f"/{uuid4()}/review", json={
        "decision": "approve", "confirm_reviewed": False, "passphrase": "PRIVATE",
    })
    assert invalid.status_code == 422 and "PRIVATE" not in invalid.text


def test_concurrent_review_and_durable_commands_are_atomic(tmp_path):
    app, _operator, subscriber, _plan = make(tmp_path, catalog=True)
    row = submit(subscriber).json()
    decision = RenewalDecision(decision="approve", confirm_reviewed=True, passphrase=SECRET)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: app.state.renewals.review(row["id"], decision, "admin"), range(2)
        ))
    assert sum(result.processed for result in results) == 1
    assert len([command for result in results for command in result.commands]) == 1
    with app.state.inventory._session() as session:
        commands = session.scalars(select(CommandModel).where(
            CommandModel.path == "/api/child/subscription-access"
        )).all()
        assert len(commands) == 1
        assert session.get(RenewalRequestModel, row["id"]).status == "approved"


def test_provision_failure_rolls_back_approval_and_can_be_retried(tmp_path, monkeypatch):
    app, operator, subscriber, _plan = make(tmp_path)
    row = submit(subscriber).json()
    original = app.state.inventory._subscription_provision_batches

    def fail(*args, **kwargs):
        raise SubscriptionAccessConflict("PRIVATE backend detail")

    monkeypatch.setattr(app.state.inventory, "_subscription_provision_batches", fail)
    response = review(operator, row["id"])
    assert response.status_code == 409 and response.json()["code"] == "renewal_access_conflict"
    assert "PRIVATE" not in response.text
    with app.state.inventory._session() as session:
        assert session.get(RenewalRequestModel, row["id"]).status == "pending"
        current = session.get(ProductUserModel, "alice").plan_expires_at.replace(tzinfo=UTC)
        assert current == aware(row["previous_end_date"])
    monkeypatch.setattr(app.state.inventory, "_subscription_provision_batches", original)
    assert review(operator, row["id"]).status_code == 200


def test_restart_history_pagination_and_no_plan_eligibility(tmp_path):
    app, operator, subscriber, _plan = make(tmp_path)
    row = submit(subscriber).json()
    assert review(operator, row["id"], decision="reject").status_code == 200
    second = submit(subscriber).json()
    restarted = create_app(app.state.settings)
    client = authenticated_client(restarted)
    page = client.get(ADMIN, params={"limit": 1}).json()
    assert page["total"] == 2 and page["requests"][0]["id"] == second["id"]
    assert client.get(ADMIN, params={"status": "rejected"}).json()["total"] == 1
    older = client.get(ADMIN, params={"offset": 1, "limit": 1}).json()["requests"]
    assert older[0]["id"] == row["id"]
    with restarted.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        user.current_plan_id = None
        session.commit()
    assert restarted.state.renewals.account("alice").eligible is False
    with pytest.raises(RenewalError, match="当前没有可续费"):
        restarted.state.renewals.submit(
            "alice", RenewalCreate(request_id=uuid4(), passphrase=SECRET)
        )
