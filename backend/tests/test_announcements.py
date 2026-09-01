"""Official-style Web announcement instances without Bot or Mini App paths."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.announcements import AnnouncementModel
from open_node.services.inventory import ProductUserModel
from sqlalchemy import func, select
from test_subscriber_auth import login, provision

ADMIN = "/api/v1/announcements"
ACCOUNT = "/api/v1/account/announcements"


def make(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'announcements.db'}",
        certificate_state_dir=tmp_path / "certificates",
        _env_file=None,
    ))
    operator = authenticated_client(app)
    assert operator.post("/api/v1/users", json={"username": "alice"}).status_code == 201
    plan = operator.post("/api/v1/plans", json={
        "name": "公告套餐", "cycle_days": 30, "traffic_limit_gb": 10,
    })
    assert plan.status_code == 201, plan.text
    assigned = operator.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan.json()["plan"]["id"]}
    )
    assert assigned.status_code == 200, assigned.text
    provision(operator)
    subscriber = TestClient(app, base_url="https://testserver")
    assert login(subscriber).status_code == 200
    return app, operator, subscriber


def publish(operator, **changes):
    return operator.post(ADMIN, json={
        "type": "general",
        "title": "服务公告",
        "body": "节点列表已经更新，请重新拉取订阅。",
        "expires_minutes": 60,
    } | changes)


def test_administrator_publishes_and_active_subscriber_reads_plain_text(tmp_path):
    _app, operator, subscriber = make(tmp_path)
    response = publish(operator, title="  服务公告  ", body="  第一行\r\n第二行  ")
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["title"] == "服务公告" and item["body"] == "第一行\n第二行"
    assert item["type"] == "general" and item["expires_at"].endswith("Z")
    assert response.headers["cache-control"] == "no-store"
    assert operator.get(ADMIN).json() == {
        "announcements": [item], "license_required": False,
    }
    account = subscriber.get(ACCOUNT)
    assert account.status_code == 200
    assert account.json() == {"announcements": [item], "license_required": False}
    assert account.headers["referrer-policy"] == "no-referrer"


def test_active_plan_and_expiry_filter_follow_official_web_scope(tmp_path):
    app, operator, subscriber = make(tmp_path)
    now = [datetime(2026, 9, 1, 8, tzinfo=UTC)]
    app.state.announcements.clock = lambda: now[0]
    expiring = publish(operator, type="maintenance", title="", expires_minutes=5)
    permanent = publish(operator, type="sub_update", title="", expires_minutes=0)
    assert expiring.status_code == permanent.status_code == 201
    assert expiring.json()["title"] == "系统维护"
    assert permanent.json()["title"] == "订阅更新"
    assert len(subscriber.get(ACCOUNT).json()["announcements"]) == 2

    now[0] += timedelta(minutes=6)
    active = subscriber.get(ACCOUNT).json()["announcements"]
    assert [item["id"] for item in active] == [permanent.json()["id"]]
    with app.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        user.plan_expires_at = now[0] - timedelta(seconds=1)
        session.commit()
    assert subscriber.get(ACCOUNT).json()["announcements"] == []
    with app.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        user.plan_expires_at = now[0] + timedelta(days=1)
        user.is_active = False
        session.commit()
    assert subscriber.get(ACCOUNT).status_code == 401
    assert [item["id"] for item in operator.get(ADMIN).json()["announcements"]] == [
        permanent.json()["id"]
    ]


def test_delete_is_administrator_only_and_missing_id_is_fixed(tmp_path):
    app, operator, subscriber = make(tmp_path)
    item = publish(operator).json()
    assert subscriber.delete(ADMIN + "/" + item["id"]).status_code == 401
    deleted = operator.delete(ADMIN + "/" + item["id"])
    assert deleted.json() == {
        "id": item["id"], "deleted": True, "license_required": False,
    }
    assert subscriber.get(ACCOUNT).json()["announcements"] == []
    repeated = operator.delete(ADMIN + "/" + item["id"])
    assert repeated.status_code == 404
    assert repeated.json()["code"] == "announcement_not_found"
    with app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(AnnouncementModel)) == 0


@pytest.mark.parametrize("body,status", [
    ({"type": "unknown", "title": "标题", "body": "正文", "expires_minutes": 0}, 422),
    ({"type": "general", "title": "标题", "body": "", "expires_minutes": 0}, 422),
    ({"type": "general", "title": "标题", "body": "正文", "expires_minutes": -1}, 422),
    ({"type": "general", "title": "标题", "body": "正文", "expires_minutes": True}, 422),
    ({"type": "general", "title": "标题\n秘密", "body": "正文", "expires_minutes": 0}, 422),
    ({"type": "general", "title": "标题", "body": "正文", "expires_minutes": 0,
      "PRIVATE": "secret"}, 422),
])
def test_strict_bounded_payloads_never_echo_rejected_input(tmp_path, body, status):
    _app, operator, _subscriber = make(tmp_path)
    response = operator.post(ADMIN, json=body)
    assert response.status_code == status
    assert response.json()["code"] == "announcement_invalid_request"
    assert "PRIVATE" not in response.text and "secret" not in response.text


def test_authentication_and_request_envelope_are_bounded(tmp_path):
    _app, operator, subscriber = make(tmp_path)
    anonymous = TestClient(operator.app, base_url="https://testserver")
    assert anonymous.get(ADMIN).status_code == 401
    assert anonymous.get(ACCOUNT).status_code == 401
    wrong_type = operator.post(ADMIN, content="PRIVATE", headers={"Content-Type": "text/plain"})
    assert wrong_type.status_code == 415 and "PRIVATE" not in wrong_type.text
    duplicate = operator.post(
        ADMIN,
        content='{"body":"first","body":"PRIVATE"}',
        headers={"Content-Type": "application/json"},
    )
    assert duplicate.status_code == 422 and "PRIVATE" not in duplicate.text
    oversized = operator.post(
        ADMIN,
        content='{"body":"' + "a" * 17_000 + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert subscriber.get(ACCOUNT).headers["cache-control"] == "no-store"
