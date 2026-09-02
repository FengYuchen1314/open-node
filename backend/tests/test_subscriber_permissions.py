"""Official-style optional pages and quotas with direct API enforcement."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from test_subscriber_auth import login, provision

ADMIN = "/api/v1/subscriber-permissions"
ACCOUNT = "/api/v1/account/permissions"
ALL_PAGES = ["templates", "external_subscriptions", "private_routes", "renewals"]


def make(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'permissions.db'}",
        external_subscriptions_state_dir=tmp_path / "external-subscriptions",
        certificate_state_dir=tmp_path / "certificates",
        _env_file=None,
    ))
    operator = authenticated_client(app)
    assert operator.post("/api/v1/users", json={"username": "alice"}).status_code == 201
    provision(operator)
    subscriber = TestClient(app, base_url="https://testserver")
    assert login(subscriber).status_code == 200
    return app, operator, subscriber


def policy(revision, *, pages=ALL_PAGES, template_quota=0, external_source_quota=0):
    return {
        "expected_revision": revision,
        "pages": pages,
        "template_quota": template_quota,
        "external_source_quota": external_source_quota,
        "license_required": False,
    }


def test_defaults_are_open_but_private_and_versioned(tmp_path):
    app, operator, subscriber = make(tmp_path)
    try:
        anonymous = TestClient(app, base_url="https://testserver")
        assert anonymous.get(ADMIN).status_code == 401
        assert anonymous.get(ACCOUNT).status_code == 401
        saved = operator.get(ADMIN)
        assert saved.status_code == 200
        assert saved.json() == {
            "revision": 0,
            "pages": ALL_PAGES,
            "template_quota": 0,
            "external_source_quota": 0,
            "license_required": False,
        }
        own = subscriber.get(ACCOUNT)
        assert own.status_code == 200
        assert own.json() == {
            "pages": ALL_PAGES,
            "templates": {"used": 0, "maximum": 0},
            "external_sources": {"used": 0, "maximum": 0},
            "license_required": False,
        }
        assert own.headers["cache-control"] == "no-store"
        changed = operator.put(ADMIN, json=policy(0, pages=["templates", "renewals"]))
        assert changed.status_code == 200 and changed.json()["revision"] == 1
        assert operator.put(ADMIN, json=policy(0)).status_code == 409
    finally:
        subscriber.close()
        operator.close()
        for engine in (app.state.auth.engine, app.state.inventory._engine,
                       app.state.certificates.engine):
            engine.dispose()
        app.state.backup_writes.close()


def test_disabled_pages_are_rejected_by_direct_account_apis(tmp_path):
    app, operator, subscriber = make(tmp_path)
    try:
        response = operator.put(ADMIN, json=policy(0, pages=[]))
        assert response.status_code == 200, response.text
        for path in (
            "/api/v1/account/external-subscriptions",
            "/api/v1/account/private-routed-nodes",
            "/api/v1/account/renewals",
        ):
            blocked = subscriber.get(path)
            assert blocked.status_code == 403, (path, blocked.text)
            assert blocked.json() == {
                "code": "subscriber_feature_disabled",
                "detail": "管理员未开放此账户功能。",
                "license_required": False,
            }
        assert operator.get("/api/v1/subscription-templates").status_code == 200
        assert subscriber.get("/api/v1/account/subscription-templates").status_code == 404
        assert operator.get("/api/v1/external-subscriptions").status_code == 200
        assert subscriber.get(ACCOUNT).json()["pages"] == []
    finally:
        subscriber.close()
        operator.close()
        for engine in (app.state.auth.engine, app.state.inventory._engine,
                       app.state.certificates.engine):
            engine.dispose()
        app.state.backup_writes.close()


def test_external_source_quota_is_atomic_and_templates_are_global_only(tmp_path):
    app, operator, subscriber = make(tmp_path)
    try:
        saved = operator.put(ADMIN, json=policy(
            0, template_quota=1, external_source_quota=1,
        ))
        assert saved.status_code == 200, saved.text
        assert subscriber.post("/api/v1/account/subscription-templates", json={}).status_code == 404

        first = subscriber.post("/api/v1/account/external-subscriptions", json={
            "name": "个人来源", "url": "https://provider.example/first?token=private",
        })
        assert first.status_code == 201, first.text
        second = subscriber.post("/api/v1/account/external-subscriptions", json={
            "name": "第二来源", "url": "https://provider.example/second?token=private",
        })
        assert second.status_code == 409
        assert second.json()["code"] == "subscriber_quota_exceeded"
        assert operator.post("/api/v1/external-subscriptions", json={
            "owner_username": "alice", "name": "管理员来源",
            "url": "https://provider.example/admin?token=private",
        }).status_code == 201
        usage = subscriber.get(ACCOUNT).json()
        assert usage["templates"] == {"used": 0, "maximum": 1}
        assert usage["external_sources"] == {"used": 2, "maximum": 1}
    finally:
        subscriber.close()
        operator.close()
        for engine in (app.state.auth.engine, app.state.inventory._engine,
                       app.state.certificates.engine):
            engine.dispose()
        app.state.backup_writes.close()


def test_concurrent_personal_creates_cannot_overrun_external_source_quota(tmp_path):
    app, operator, first = make(tmp_path)
    second = TestClient(app, base_url="https://testserver")
    assert login(second).status_code == 200
    try:
        assert operator.put(ADMIN, json=policy(
            0, template_quota=1, external_source_quota=1,
        )).status_code == 200

        def race(call):
            barrier = Barrier(2)

            def run(index):
                barrier.wait()
                return call((first, second)[index], index)

            with ThreadPoolExecutor(max_workers=2) as executor:
                return [future.result() for future in (
                    executor.submit(run, 0), executor.submit(run, 1),
                )]

        sources = race(lambda client, index: client.post(
            "/api/v1/account/external-subscriptions",
            json={
                "name": f"来源 {index}",
                "url": f"https://provider.example/race-{index}?token=private",
            },
        ))
        assert sorted(response.status_code for response in sources) == [201, 409]
        assert next(item for item in sources if item.status_code == 409).json()["code"] == (
            "subscriber_quota_exceeded"
        )
        usage = first.get(ACCOUNT).json()
        assert usage["templates"] == {"used": 0, "maximum": 1}
        assert usage["external_sources"] == {"used": 1, "maximum": 1}
    finally:
        second.close()
        first.close()
        operator.close()
        for engine in (app.state.auth.engine, app.state.inventory._engine,
                       app.state.certificates.engine):
            engine.dispose()
        app.state.backup_writes.close()


def test_strict_bounded_policy_request_never_reflects_input(tmp_path):
    app, operator, subscriber = make(tmp_path)
    try:
        for body, status in (
            ('{"pages":[],"pages":["templates"]}', 422),
            ('{"expected_revision":NaN}', 422),
            ("PRIVATE" * 2000, 413),
        ):
            response = operator.put(
                ADMIN, content=body, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == status
            assert "PRIVATE" not in response.text
        wrong_media = operator.put(
            ADMIN, content="{}", headers={"Content-Type": "text/plain"}
        )
        assert wrong_media.status_code == 415
        assert operator.get(ADMIN).json()["revision"] == 0
    finally:
        subscriber.close()
        operator.close()
        for engine in (app.state.auth.engine, app.state.inventory._engine,
                       app.state.certificates.engine):
            engine.dispose()
        app.state.backup_writes.close()
