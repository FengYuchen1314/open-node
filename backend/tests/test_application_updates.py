import json
import os
from pathlib import Path

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app

CURRENT = "a" * 40
LATEST = "b" * 40
BASE = "/api/v1/application-update"


def payload(**changes):
    value = {
        "schema_version": 1,
        "managed": True,
        "status": "available",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "current_revision": CURRENT,
        "latest_revision": LATEST,
        "has_update": True,
        "checked_at": "2026-09-01T01:02:03Z",
        "started_at": "2026-09-01T01:02:00Z",
        "completed_at": "2026-09-01T01:02:03Z",
        "message": "发现可用更新，请核对目标提交后再执行。",
        "release_url": f"https://github.com/FengYuchen1314/open-node/commit/{LATEST}",
        "license_required": False,
    }
    value.update(changes)
    return value


def environment(tmp_path: Path):
    state = tmp_path / "maintenance"
    state.mkdir()
    state.chmod(0o1770)
    state_file = state / "state.json"
    state_file.write_text(json.dumps(payload(), separators=(",", ":")) + "\n")
    state_file.chmod(0o640)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'application-update.db'}",
        source_revision=CURRENT,
        application_update_dir=state,
        application_update_state_owner_uid=os.getuid(),
        application_update_state_group_gid=os.getgid(),
    )
    return authenticated_client(create_app(settings)), state


def test_update_state_is_administrator_only_and_never_cacheable(tmp_path: Path):
    operator, _state = environment(tmp_path)
    anonymous = TestClient(operator.app, base_url="https://testserver")

    assert anonymous.get(BASE).status_code == 401
    response = operator.get(BASE)

    assert response.status_code == 200
    assert response.json() == payload()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_check_and_exact_revision_apply_create_only_bounded_requests(tmp_path: Path):
    operator, state = environment(tmp_path)

    checked = operator.post(BASE + "/check")
    assert checked.status_code == 202
    request = json.loads((state / "request.json").read_text())
    assert request == {
        "schema_version": 1,
        "request_id": checked.json()["request_id"],
        "action": "check",
        "expected_revision": None,
        "requested_at": request["requested_at"],
    }
    assert (state / "request.json").stat().st_mode & 0o777 == 0o600
    assert operator.post(BASE + "/check").status_code == 409

    (state / "request.json").unlink()
    applied = operator.post(BASE + "/apply", json={
        "target_revision": LATEST, "confirmed": True,
    })
    assert applied.status_code == 202
    request = json.loads((state / "request.json").read_text())
    assert request["action"] == "apply"
    assert request["expected_revision"] == LATEST
    assert set(request) == {
        "schema_version", "request_id", "action", "expected_revision", "requested_at",
    }


def test_apply_requires_csrf_confirmation_and_the_checked_target(tmp_path: Path):
    operator, state = environment(tmp_path)
    headers = dict(operator.headers)
    operator.headers.pop("X-CSRF-Token")
    assert operator.post(BASE + "/apply", json={
        "target_revision": LATEST, "confirmed": True,
    }).status_code == 403
    operator.headers.update(headers)

    for body in [
        {"target_revision": LATEST, "confirmed": False},
        {"target_revision": LATEST},
        {"target_revision": CURRENT, "confirmed": True},
        {"target_revision": LATEST, "confirmed": True, "command": "PRIVATE"},
    ]:
        response = operator.post(BASE + "/apply", json=body)
        assert response.status_code in {409, 422}
        assert not (state / "request.json").exists()
        assert "PRIVATE" not in response.text


def test_unsafe_or_absent_helper_state_fails_closed_without_writing(tmp_path: Path):
    operator, state = environment(tmp_path)
    (state / "state.json").unlink()
    secret = tmp_path / "private-state"
    secret.write_text(json.dumps(payload()) + "\n")
    (state / "state.json").symlink_to(secret)

    response = operator.get(BASE)
    assert response.status_code == 200
    assert response.json()["managed"] is False
    assert response.json()["status"] == "unavailable"
    assert operator.post(BASE + "/check").status_code == 503
    assert not (state / "request.json").exists()


def test_failed_request_handoff_is_explicit_and_removes_partial_file(
    monkeypatch, tmp_path: Path
):
    operator, state = environment(tmp_path)

    def fail_write(_descriptor, _content):
        raise OSError("injected short handoff")

    monkeypatch.setattr(
        "open_node.services.application_updates._write_all", fail_write
    )
    response = operator.post(BASE + "/check")

    assert response.status_code == 503
    assert response.json() == {
        "code": "application_update_state_unavailable",
        "detail": "更新状态暂时不可用，请稍后重新读取。",
        "license_required": False,
    }
    assert response.headers["cache-control"] == "no-store"
    assert not (state / "request.json").exists()


def test_invalid_root_settings_are_rejected():
    for value in ["relative", "/", "/tmp/../state"]:
        try:
            Settings(application_update_dir=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe update path: {value}")
