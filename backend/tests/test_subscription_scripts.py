import json

import pytest
import yaml
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.services import script_runtime
from open_node.services.script_runtime import ScriptRuntimeError, lint_script, run_script
from test_subscriber_auth import login, make
from test_subscription_customizations import profile, profile_update

BASE = "/api/v1/subscription-scripts"


def script_payload(**changes):
    return {
        "owner_username": "alice",
        "name": "Rewrite mode",
        "hook": "post_fetch",
        "content": "function main(config) { config.mode = 'global'; return config; }",
        "enabled": True,
        "sort_order": 10,
        **changes,
    }


def test_quickjs_worker_lints_runs_produces_and_hard_times_out(monkeypatch):
    lint_script("function main(config) { return config; }")
    assert run_script(
        "post_fetch",
        "function main(config) { config.mode = 'global'; return config; }",
        {"mode": "rule"},
    ) == {"mode": "global"}
    assert run_script(
        "pre_save_nodes",
        "function main(proxies) { return proxies.filter(p => p.name !== 'drop'); }",
        [{"name": "keep"}, {"name": "drop"}],
    ) == [{"name": "keep"}]
    produced = run_script(
        "post_fetch",
        (
            "function main(config) { config.output = produce(config.proxies, 'clash'); "
            "return config; }"
        ),
        {"proxies": []},
    )
    assert yaml.safe_load(produced["output"])["proxies"] == []
    with pytest.raises(ScriptRuntimeError) as syntax:
        lint_script("function main( {")
    assert "function main" not in str(syntax.value)
    with pytest.raises(ScriptRuntimeError) as secret:
        run_script(
            "post_fetch",
            "function main(config) { throw new Error(config.private); }",
            {"private": "never-echo-this"},
        )
    assert "never-echo-this" not in str(secret.value)
    monkeypatch.setattr(script_runtime, "SCRIPT_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(ScriptRuntimeError, match="5 second"):
        run_script("post_fetch", "function main(config) { while (true) {} }", {})


def test_profile_applies_ordered_scripts_and_deletion_cleans_selection(tmp_path):
    app, admin, _subscriber = make(tmp_path, catalog=True)
    profile_id = profile(app, code="scripted")
    first = admin.post(BASE, json=script_payload()).raise_for_status().json()
    second = admin.post(
        BASE,
        json=script_payload(
            name="Rewrite rules",
            sort_order=20,
            content=(
                "function main(config) { "
                "config.rules = ['DOMAIN-SUFFIX,example.com,DIRECT', 'MATCH,DIRECT']; "
                "return config; }"
            ),
        ),
    ).raise_for_status().json()
    invalid = admin.post(
        BASE,
        json=script_payload(
            name="Invalid node output",
            hook="pre_save_nodes",
            sort_order=0,
            content="function main(proxies) { return [{name: 'missing fields'}]; }",
        ),
    ).raise_for_status().json()
    current = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    saved = admin.put(
        f"/api/v1/subscription-profiles/{profile_id}",
        json=profile_update(
            current,
            override_scripts_enabled=True,
            selected_override_script_ids=[second["id"], invalid["id"], first["id"]],
        ),
    )
    assert saved.status_code == 200, saved.text
    rendered = admin.get("/x/scripted")
    assert rendered.status_code == 200, rendered.text
    value = yaml.safe_load(rendered.text)
    assert value["mode"] == "global"
    assert value["rules"] == ["DOMAIN-SUFFIX,example.com,DIRECT", "MATCH,DIRECT"]
    resolved = app.state.inventory._subscription_profiles().resolve(
        "scripted", SubscriptionClientFormat.CLASH
    )
    assert "An enabled override script failed and was skipped" in resolved.warnings

    removed = admin.post(
        f"{BASE}/{first['id']}/delete",
        json={"expected_revision": first["revision"]},
    )
    assert removed.status_code == 204
    profile_value = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    assert profile_value["selected_override_script_ids"] == [second["id"], invalid["id"]]


def test_owner_boundary_account_crud_and_secret_free_syntax_error(tmp_path):
    _app, admin, subscriber = make(tmp_path, catalog=True)
    admin.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    bob = admin.post(
        BASE,
        json=script_payload(owner_username="bob", name="Bob script"),
    ).raise_for_status().json()
    assert login(subscriber).status_code == 200
    assert subscriber.get("/api/v1/account/subscription-scripts").json()["scripts"] == []
    hidden = subscriber.put(
        f"/api/v1/account/subscription-scripts/{bob['id']}",
        json={
            "name": "Taken",
            "hook": "post_fetch",
            "content": "function main(config) { return config; }",
            "enabled": True,
            "sort_order": 0,
            "expected_revision": bob["revision"],
        },
    )
    assert hidden.status_code == 404
    invalid = subscriber.post(
        "/api/v1/account/subscription-scripts",
        json={
            "name": "Broken",
            "hook": "post_fetch",
            "content": "function main( { /* private-script-value */",
            "enabled": True,
            "sort_order": 0,
        },
    )
    assert invalid.status_code == 422
    assert "private-script-value" not in invalid.text
    own = subscriber.post(
        "/api/v1/account/subscription-scripts",
        json={
            "name": "My script",
            "hook": "pre_save_nodes",
            "content": "function main(proxies) { return proxies; }",
            "enabled": True,
            "sort_order": 0,
        },
    )
    assert own.status_code == 201, own.text
    assert own.json()["owner_username"] == "alice"
    assert "owner_username" not in json.dumps(own.request.content.decode())
