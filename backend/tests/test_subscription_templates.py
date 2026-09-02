from copy import deepcopy
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from open_node.domain.subscription_templates import TemplateWrite
from open_node.domain.subscriptions import SubscriptionClientFormat, SubscriptionPlanCreate
from open_node.services.template_rendering import (
    DEFAULT_CLASH,
    TemplateError,
    parse_template,
    render,
)
from test_subscriber_auth import login, make

BASE = "/api/v1/subscription-templates"
ACCOUNT = "/api/v1/account/subscription-templates"
PROXY = {
    "name": "Japan",
    "type": "vmess",
    "server": "127.0.0.1",
    "port": 12345,
    "uuid": "00000000-0000-4000-8000-000000000001",
}


@pytest.fixture
def env(tmp_path):
    app, admin, subscriber = make(tmp_path, catalog=True)
    assert login(subscriber).status_code == 200
    return app, admin, subscriber


def create(client, **changes):
    return client.post(
        BASE,
        json={"name": "custom.yaml", "format": "clash", "content": DEFAULT_CLASH, **changes},
    )


def update_payload(value, **changes):
    return {
        **{field: value[field] for field in TemplateWrite.model_fields},
        "expected_revision": value["revision"],
        **changes,
    }


def settings(client, **changes):
    current = client.get(BASE + "/settings").raise_for_status().json()
    return client.put(
        BASE + "/settings",
        json={
            "clash_template_id": current["clash_template_id"],
            "expected_revision": current["revision"],
            **changes,
        },
    )


def plan_edit(admin, **changes):
    plan = admin.get("/api/v1/plans").json()["plans"][0]
    path = f"/api/v1/plans/{plan['id']}/settings"
    detail = admin.get(path).json()
    return admin.put(
        path,
        json={
            **{field: detail["plan"][field] for field in SubscriptionPlanCreate.model_fields},
            "expected_revision": detail["revision"],
            "acknowledge_runtime_restart": True,
            **changes,
        },
    )


def exported(admin):
    link = admin.get("/api/v1/user-subscription-token", params={"username": "alice"}).json()[
        "subscription"
    ]["subscription_url"]
    return yaml.safe_load(admin.get(link).raise_for_status().text)


def marked(marker):
    return DEFAULT_CLASH + f"profile:\n  store-selected: true\nx-label: {marker}\n"


def test_fresh_database_seeds_a_global_all_proxy_default(env):
    _, admin, subscriber = env
    library = admin.get(BASE).raise_for_status().json()
    assert len(library["templates"]) == 1
    default = library["templates"][0]
    assert default["name"] == "default.yaml"
    assert default["owner_username"] is None and default["is_public"]
    assert library["settings"]["clash_template_id"] == default["id"]
    detail = admin.get(BASE + "/" + default["id"]).raise_for_status().json()
    assert detail["content"] == DEFAULT_CLASH
    assert exported(admin)["rules"] == ["MATCH,Proxy"]
    assert subscriber.get(ACCOUNT).status_code == 404


def test_global_template_crud_is_revision_guarded_and_admin_only(env):
    app, admin, subscriber = env
    row = create(admin, owner_username="alice", is_public=False).raise_for_status().json()
    assert row["owner_username"] is None and row["is_public"]
    stale = update_payload(row)
    row = (
        admin.put(BASE + "/" + row["id"], json=update_payload(row, name="renamed.yaml"))
        .raise_for_status()
        .json()
    )
    assert admin.put(BASE + "/" + row["id"], json=stale).status_code == 409
    assert subscriber.get(ACCOUNT + "/" + row["id"]).status_code == 404
    assert TestClient(app).get(BASE).status_code == 401
    assert create(admin, name="RENAMED.YAML").status_code == 409
    assert (
        admin.post(
            BASE + "/" + row["id"] + "/remove",
            json={"expected_revision": row["revision"], "confirm_name": "wrong"},
        ).status_code
        == 409
    )
    assert (
        admin.post(
            BASE + "/" + row["id"] + "/remove",
            json={"expected_revision": row["revision"], "confirm_name": row["name"]},
        ).status_code
        == 204
    )
    assert [item["name"] for item in admin.get(BASE).json()["templates"]] == ["default.yaml"]


def test_plan_template_overrides_global_and_null_falls_back_without_agent_commands(env):
    _, admin, _ = env
    system = create(admin, name="system.yaml", content=marked("system")).json()
    package = create(admin, name="package.yaml", content=marked("package")).json()
    assert settings(admin, clash_template_id=system["id"]).status_code == 200
    assert exported(admin)["x-label"] == "system"
    result = plan_edit(admin, clash_template_id=package["id"]).raise_for_status().json()
    assert result["commands"] == [] and exported(admin)["x-label"] == "package"
    assert plan_edit(admin, clash_template_id=None).status_code == 200
    assert exported(admin)["x-label"] == "system"
    reset = settings(admin, clash_template_id=None).raise_for_status().json()
    assert reset["clash_template_id"] is not None
    assert "x-label" not in exported(admin)


def test_template_binding_rejects_missing_ids_and_bound_delete(env):
    _, admin, _ = env
    row = create(admin).raise_for_status().json()
    assert plan_edit(admin, clash_template_id=str(uuid4())).status_code == 404
    assert plan_edit(admin, clash_template_id=row["id"]).status_code == 200
    row = admin.get(BASE + "/" + row["id"]).json()
    assert row["plan_names"]
    assert (
        admin.post(
            BASE + "/" + row["id"] + "/remove",
            json={"expected_revision": row["revision"], "confirm_name": row["name"]},
        ).status_code
        == 409
    )
    assert create(admin, name="legacy.conf", format="surge").status_code == 422


def test_catalog_remaps_global_template_names_and_defaults(env, tmp_path):
    _, admin, _ = env
    row = create(admin, name="catalog.yaml", content=marked("catalog")).json()
    settings(admin, clash_template_id=row["id"]).raise_for_status()
    plan_edit(admin, clash_template_id=row["id"]).raise_for_status()
    catalog = admin.get("/api/v1/catalog/export").json()["catalog"]
    assert catalog["plans"][0]["clash_template_name"] == row["name"]
    assert catalog["template_preferences"] == []
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _, target, _ = make(other_dir)
    target.post("/api/v1/servers", json={"name": catalog["nodes"][0]["server_name"]})
    target.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    imported = next(
        item for item in target.get(BASE).json()["templates"] if item["name"] == row["name"]
    )
    assert imported["id"] != row["id"]
    assert target.get("/api/v1/plans").json()["plans"][0]["clash_template_id"] == imported["id"]
    assert target.get(BASE + "/settings").json()["clash_template_id"] == imported["id"]
    bad = deepcopy(catalog)
    bad["template_defaults"]["clash_template_name"] = "missing.yaml"
    assert target.post("/api/v1/catalog/import", json={"catalog": bad}).status_code == 422
    fallback = deepcopy(catalog)
    fallback["template_defaults"]["clash_template_name"] = None
    target.post("/api/v1/catalog/import", json={"catalog": fallback}).raise_for_status()
    default = next(
        item for item in target.get(BASE).json()["templates"] if item["name"] == "default.yaml"
    )
    assert target.get(BASE + "/settings").json()["clash_template_id"] == default["id"]


@pytest.mark.parametrize(
    "content",
    [
        "# comment",
        "[]",
        "a: 1\na: 2",
        "a: &loop [*loop]",
        "!!python/object/apply:os.system ['false']",
        "a: [",
        "proxy-groups: [1]",
        "proxy-groups: [{name: DIRECT, type: select}]",
        "proxy-groups: [{name: A, type: select, proxies: [B]}, "
        "{name: B, type: select, proxies: [A]}]",
    ],
)
def test_invalid_yaml_is_rejected(content):
    with pytest.raises(TemplateError):
        parse_template(content, "clash")


def test_clash_expansion_preserves_group_order_rules_and_source():
    source = """proxies: [{name: placeholder}]
proxy-providers:
  External: {type: file, path: ./provider.yaml}
proxy-groups:
  - {name: Second, type: select, proxies: [First, __PROXY_NODES__, __PROXY_PROVIDERS__]}
  - {name: First, type: select, proxies: [DIRECT]}
rules: ["MATCH,Second"]
"""
    result, _ = render(source, "clash", [PROXY])
    value = yaml.safe_load(result)
    assert [group["name"] for group in value["proxy-groups"]] == ["Second", "First"]
    assert value["proxy-groups"][0]["proxies"] == ["First", "Japan"]
    assert value["proxy-groups"][0]["use"] == ["External"]
    assert value["proxies"][0]["uuid"] == PROXY["uuid"]
    assert parse_template(source, "clash")["proxies"][0]["name"] == "placeholder"


def test_node_renaming_updates_dialer_proxy_references(env, monkeypatch):
    app, _, _ = env
    store = app.state.inventory
    first, second = str(uuid4()), str(uuid4())
    candidates = [
        (first, PROXY | {"name": "Proxy"}),
        (second, PROXY | {"name": "Chain", "dialer-proxy": "Proxy"}),
    ]
    monkeypatch.setattr(store, "_subscription_proxy_configs", lambda *_: (deepcopy(candidates), []))
    with store._session() as session:
        user = store.subscription_templates().user(session, "alice")
        plan = store._available_subscription_plan(session, user)
        proxies, report = store._prepare_subscription_format(
            session, user, plan, SubscriptionClientFormat.CLASH
        )
    assert [node.available for node in report.nodes] == [True, True]
    assert proxies[0]["name"] == "Proxy (2)"
    assert proxies[1]["dialer-proxy"] == "Proxy (2)"


def test_yaml_alias_limits_merge_overrides_and_non_clash_rejection():
    source = (
        "base: &base {type: select, proxies: [__PROXY_NODES__]}\n"
        "proxy-groups: [{<<: *base, name: Proxy, type: select}]"
    )
    assert yaml.safe_load(render(source, "clash", [PROXY])[0])["proxy-groups"][0]["proxies"] == [
        "Japan"
    ]
    with pytest.raises(TemplateError):
        parse_template("a: " + "[" * 60 + "1" + "]" * 60, "clash")
    with pytest.raises(TemplateError, match="Only Clash"):
        render(DEFAULT_CLASH, "surge", [PROXY])
