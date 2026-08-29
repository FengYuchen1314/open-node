from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.registration_invitations import (
    RegistrationClaim,
    RegistrationInvitationCreate,
)
from open_node.main import create_app
from open_node.services.inventory import ProductUserModel, RegistrationInvitationModel
from open_node.services.registration_invitations import RegistrationInvitationUnavailable
from open_node.services.subscriber_auth import SubscriberAccount
from sqlalchemy import func, select, update
from test_subscriptions import create_catalog_fixture

PASSWORD = "subscriber-password-for-invitation-tests"
BASE = "/api/v1/registration-invitations"
REGISTER = "/api/v1/account/register"
BROWSER = {"X-Open-Node-Client": "browser"}


def make(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'invitations.db'}"))
    return app, authenticated_client(app)


def plan(operator, name="Invite plan"):
    response = operator.post(
        "/api/v1/plans", json={"name": name, "traffic_limit_gb": 10, "cycle_days": 30}
    )
    assert response.status_code == 201, response.text
    return response.json()["plan"]


def issue(operator, plan_id, *, expires_minutes=1440):
    response = operator.post(BASE, json={"plan_id": plan_id, "expires_minutes": expires_minutes})
    assert response.status_code == 201, response.text
    payload = response.json()
    parts = urlsplit(payload["registration_url"])
    assert parts.path == "/account" and not parts.query
    token = parse_qs(parts.fragment)["invite"][0]
    return payload, token


def claim(client, token, username, password=PASSWORD):
    return client.post(
        REGISTER,
        json={"token": token, "username": username, "password": password},
        headers=BROWSER,
    )


def test_admin_issues_hashed_invite_and_claim_creates_working_subscriber(tmp_path):
    app, operator = make(tmp_path)
    _agent_token, _server_id, _node_id, plan_id = create_catalog_fixture(operator)
    created, token = issue(operator, plan_id)

    assert created["license_required"] is False
    assert token not in str(created["invitation"])
    anonymous = TestClient(app, base_url="https://testserver")
    response = claim(anonymous, token, "bob")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["username"] == "bob"
    assert body["user"]["role"] == "user"
    assert body["user"]["current_plan_id"] == plan_id
    assert body["plan"]["id"] == plan_id
    assert [command["path"] for command in body["commands"]] == ["/api/child/subscription-access"]

    signed_in = anonymous.post(
        "/api/v1/account/login",
        json={"username": "bob", "password": PASSWORD},
        headers=BROWSER,
    )
    assert signed_in.status_code == 200 and signed_in.json()["authenticated"] is True
    listed = operator.get(BASE).json()["invitations"]
    assert listed[0]["status"] == "used" and listed[0]["used_by"] == "bob"
    assert "registration_url" not in listed[0] and token not in str(listed)

    with app.state.inventory._session() as session:
        invitation = session.scalar(select(RegistrationInvitationModel))
        account = session.get(SubscriberAccount, "bob")
        assert invitation.token_hash == sha256(token.encode()).hexdigest()
        assert token not in str(invitation.__dict__)
        assert account.password_hash.startswith("$argon2id$")
        assert PASSWORD not in account.password_hash

    reused = claim(TestClient(app, base_url="https://testserver"), token, "charlie")
    assert reused.status_code == 404
    assert reused.json() == {"detail": "Invitation unavailable"}


def test_invalid_revoked_and_expired_invites_have_the_same_public_response(tmp_path):
    app, operator = make(tmp_path)
    plan_id = plan(operator)["id"]
    created, revoked_token = issue(operator, plan_id)
    revoked = operator.delete(BASE + "/" + created["invitation"]["id"])
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    expired, expired_token = issue(operator, plan_id)
    with app.state.inventory._session() as session:
        session.execute(
            update(RegistrationInvitationModel)
            .where(RegistrationInvitationModel.id == expired["invitation"]["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    public = TestClient(app, base_url="https://testserver")
    responses = [
        claim(public, "x" * 43, "unknown"),
        claim(public, revoked_token, "revoked"),
        claim(public, expired_token, "expired"),
    ]
    assert [(response.status_code, response.json()) for response in responses] == [
        (404, {"detail": "Invitation unavailable"}),
        (404, {"detail": "Invitation unavailable"}),
        (404, {"detail": "Invitation unavailable"}),
    ]
    statuses = {item["id"]: item["status"] for item in operator.get(BASE).json()["invitations"]}
    assert statuses[created["invitation"]["id"]] == "revoked"
    assert statuses[expired["invitation"]["id"]] == "expired"


def test_username_conflict_does_not_consume_invitation(tmp_path):
    app, operator = make(tmp_path)
    plan_id = plan(operator)["id"]
    operator.post("/api/v1/users", json={"username": "Alice"}).raise_for_status()
    created, token = issue(operator, plan_id)
    public = TestClient(app, base_url="https://testserver")

    conflict = claim(public, token, "alice")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Username is unavailable"}
    assert operator.get(BASE).json()["invitations"][0]["status"] == "active"
    assert claim(public, token, "bob").status_code == 201
    assert operator.get(BASE).json()["invitations"][0]["used_by"] == "bob"
    assert created["invitation"]["id"] == operator.get(BASE).json()["invitations"][0]["id"]

    server = operator.post("/api/v1/servers", json={"name": "preview-only"}).json()["server"]
    node = operator.post(
        "/api/v1/nodes",
        json={"name": "No inbound", "server_id": server["id"], "protocol": "vless"},
    ).json()["node"]
    invalid_plan = operator.post(
        "/api/v1/plans",
        json={"name": "Invalid runtime", "traffic_limit_gb": 1, "node_ids": [node["id"]]},
    ).json()["plan"]
    invalid, invalid_token = issue(operator, invalid_plan["id"])
    failed = claim(public, invalid_token, "charlie")
    assert failed.status_code == 409
    assert failed.json() == {"detail": "Invitation plan cannot provision subscriber access"}
    invitations = operator.get(BASE).json()["invitations"]
    assert (
        next(item for item in invitations if item["id"] == invalid["invitation"]["id"])["status"]
        == "active"
    )
    with app.state.inventory._session() as session:
        assert session.get(ProductUserModel, "charlie") is None


def test_concurrent_claim_is_atomic(tmp_path):
    app, operator = make(tmp_path)
    plan_id = plan(operator)["id"]
    service = app.state.inventory._registration_invitations()
    issued = service.create(RegistrationInvitationCreate(plan_id=plan_id))
    payload = RegistrationClaim(token=issued.token, username="bob", password=PASSWORD)

    def run():
        try:
            return service.claim(payload)
        except RegistrationInvitationUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result is not None for result in results) == 1
    with app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(ProductUserModel)) == 1
        assert session.scalar(select(func.count()).select_from(SubscriberAccount)) == 1
        invitation = session.get(RegistrationInvitationModel, str(issued.invitation.id))
        assert invitation.used_by == "bob" and invitation.used_at is not None


def test_plan_removal_deletes_its_invitations(tmp_path):
    app, operator = make(tmp_path)
    current = plan(operator)
    issue(operator, current["id"])
    status = operator.get(f"/api/v1/plans/{current['id']}/settings").json()
    removed = operator.post(
        f"/api/v1/plans/{current['id']}/remove",
        json={
            "expected_revision": status["revision"],
            "confirm_name": current["name"],
            "acknowledge_runtime_restart": True,
        },
    )
    assert removed.status_code == 200, removed.text
    assert operator.get(BASE).json()["invitations"] == []


def test_invitation_management_requires_admin_and_register_requires_browser_client(tmp_path):
    app, operator = make(tmp_path)
    plan_id = plan(operator)["id"]
    _created, token = issue(operator, plan_id)
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get(BASE).status_code == 401
    assert anonymous.post(BASE, json={"plan_id": plan_id}).status_code == 401
    assert claim(anonymous, token, "bob").status_code == 201

    _created, token = issue(operator, plan_id)
    missing_header = anonymous.post(
        REGISTER, json={"token": token, "username": "charlie", "password": PASSWORD}
    )
    assert missing_header.status_code == 403
    assert operator.get(BASE).json()["invitations"][0]["status"] == "active"
