from copy import deepcopy
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from open_node.domain.subscription_templates import TemplateWrite
from open_node.domain.subscriptions import SubscriptionClientFormat, SubscriptionPlanCreate
from open_node.services import subscription_clients
from open_node.services.inventory import SubscriptionPlanModel
from open_node.services.template_rendering import (
    DEFAULT_CLASH,
    DEFAULT_SURGE,
    TemplateError,
    parse_template,
    render,
    surge_parts,
)
from sqlalchemy import Column, ForeignKey, MetaData, Table, select
from test_subscriber_auth import login, make, provision

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


def create(client, *, path=BASE, **changes):
    return client.post(
        path, json={"name": "custom.yaml", "format": "clash", "content": DEFAULT_CLASH, **changes}
    )


def update_payload(value, **changes):
    return {
        **{field: value[field] for field in TemplateWrite.model_fields},
        "expected_revision": value["revision"],
        **changes,
    }


def settings(client, username=None, *, path=BASE, **changes):
    params = {"username": username} if username else {}
    current = client.get(path + "/settings", params=params).raise_for_status().json()
    return client.put(
        path + "/settings",
        params=params,
        json={
            "clash_template_id": current["clash_template_id"],
            "surge_template_id": current["surge_template_id"],
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


def test_template_crud_revision_visibility_and_authenticated_download(env):
    app, admin, subscriber = env
    row = create(admin).raise_for_status().json()
    assert admin.get(BASE).json()["templates"][0]["content"] is None
    assert subscriber.get(ACCOUNT + "/" + row["id"]).status_code == 404
    assert subscriber.get(ACCOUNT + "/" + row["id"] + "/file").status_code == 404
    assert TestClient(app).get(BASE).status_code == 401
    stale = update_payload(row)
    row = (
        admin.put(
            BASE + "/" + row["id"], json=update_payload(row, name="renamed.yaml", is_public=True)
        )
        .raise_for_status()
        .json()
    )
    assert admin.put(BASE + "/" + row["id"], json=stale).status_code == 409
    assert subscriber.get(ACCOUNT + "/" + row["id"]).json()["content"] == DEFAULT_CLASH
    download = subscriber.get(ACCOUNT + "/" + row["id"] + "/file")
    assert download.text == DEFAULT_CLASH and download.headers["cache-control"] == "no-store"
    assert "renamed.yaml" in download.headers["content-disposition"]
    assert create(admin, name="RENAMED.YAML").status_code == 409
    assert subscriber.put(ACCOUNT + "/" + row["id"], json=update_payload(row)).status_code == 403
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
    assert admin.get(BASE).json()["templates"] == []


def test_personal_plan_system_builtin_precedence_and_no_commands(env):
    _, admin, subscriber = env
    credentials = admin.get("/api/v1/users/alice/credentials").json()
    system = create(admin, name="system.yaml", content=marked("system")).json()
    package = create(admin, name="package.yaml", content=marked("package")).json()
    assert settings(admin, clash_template_id=system["id"]).status_code == 200
    assert exported(admin)["x-label"] == "system"
    result = plan_edit(admin, clash_template_id=package["id"]).raise_for_status().json()
    assert result["commands"] == [] and exported(admin)["x-label"] == "package"
    assert create(subscriber, path=ACCOUNT).status_code == 403
    assert settings(admin, "alice", enabled=True).status_code == 200
    own = (
        create(subscriber, path=ACCOUNT, name="own.yaml", content=marked("own"))
        .raise_for_status()
        .json()
    )
    assert own["owner_username"] == "alice" and not own["is_public"]
    assert settings(subscriber, path=ACCOUNT, clash_template_id=system["id"]).status_code == 403
    assert settings(subscriber, path=ACCOUNT, clash_template_id=own["id"]).status_code == 200
    assert exported(admin)["x-label"] == "own"
    assert settings(admin, "alice", enabled=False).status_code == 200
    assert exported(admin)["x-label"] == "package"
    assert plan_edit(admin, clash_template_id=None).status_code == 200
    assert exported(admin)["x-label"] == "system"
    assert settings(admin, clash_template_id=None).status_code == 200
    assert "x-label" not in exported(admin)
    assert admin.get("/api/v1/users/alice/credentials").json() == credentials


def test_partial_settings_update_preserves_personal_defaults(env):
    _, admin, subscriber = env
    settings(admin, "alice", enabled=True).raise_for_status()
    own = create(subscriber, path=ACCOUNT).raise_for_status().json()
    current = settings(subscriber, path=ACCOUNT, clash_template_id=own["id"]).json()
    saved = (
        subscriber.put(
            ACCOUNT + "/settings",
            json={"expected_revision": current["revision"]},
        )
        .raise_for_status()
        .json()
    )
    assert saved["clash_template_id"] == own["id"]


def test_private_ownership_forgery_and_csrf(env):
    app, admin, subscriber = env
    settings(admin, "alice", enabled=True).raise_for_status()
    admin.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    provision(admin, "bob")
    bob = TestClient(app, base_url="https://testserver")
    login(bob, username="bob").raise_for_status()
    assert create(subscriber, path=ACCOUNT, owner_username="bob").status_code == 403
    assert create(subscriber, path=ACCOUNT, is_public=True).status_code == 403
    own = create(subscriber, path=ACCOUNT).raise_for_status().json()
    assert bob.get(ACCOUNT).json()["templates"] == []
    for suffix in ("", "/file"):
        assert bob.get(ACCOUNT + "/" + own["id"] + suffix).status_code == 404
    assert subscriber.get(ACCOUNT + "/settings", params={"username": "bob"}).status_code == 403
    assert (
        subscriber.post(
            ACCOUNT + "/preview",
            json={"format": "clash", "content": DEFAULT_CLASH, "username": "bob"},
        ).status_code
        == 403
    )
    assert create(subscriber, path=ACCOUNT, name="second.yaml").status_code == 201
    subscriber.headers["X-CSRF-Token"] = "wrong"
    assert create(subscriber, path=ACCOUNT, name="csrf.yaml").status_code == 403
    assert TestClient(app).get(ACCOUNT + "/" + own["id"]).status_code == 401


def test_bound_delete_wrong_type_and_legacy_plan_omission(env):
    _, admin, _ = env
    row = create(admin).json()
    assert plan_edit(admin, surge_template_id=row["id"]).status_code == 422
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
    plan = admin.get("/api/v1/plans").json()["plans"][0]
    path = f"/api/v1/plans/{plan['id']}/settings"
    detail = admin.get(path).json()
    payload = {
        field: detail["plan"][field]
        for field in SubscriptionPlanCreate.model_fields
        if field not in {"clash_template_id", "surge_template_id"}
    }
    payload |= {
        "expected_revision": detail["revision"],
        "acknowledge_runtime_restart": True,
        "description": "legacy edit",
    }
    assert admin.put(path, json=payload).json()["plan"]["clash_template_id"] == row["id"]


def test_catalog_remaps_template_ids_and_rolls_back_bad_defaults(env, tmp_path):
    _, admin, subscriber = env
    settings(admin, "alice", enabled=True).raise_for_status()
    row = create(subscriber, path=ACCOUNT).json()
    settings(subscriber, path=ACCOUNT, clash_template_id=row["id"]).raise_for_status()
    plan_edit(admin, clash_template_id=row["id"]).raise_for_status()
    catalog = admin.get("/api/v1/catalog/export").json()["catalog"]
    assert catalog["plans"][0]["clash_template_name"] == row["name"]
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    _, target, _ = make(other_dir)
    target.post("/api/v1/servers", json={"name": catalog["nodes"][0]["server_name"]}).json()
    target.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    imported = target.get(BASE).json()["templates"][0]
    assert imported["id"] != row["id"]
    assert target.get("/api/v1/plans").json()["plans"][0]["clash_template_id"] == imported["id"]
    assert (
        target.get(BASE + "/settings", params={"username": "alice"}).json()["clash_template_id"]
        == imported["id"]
    )
    bad = deepcopy(catalog)
    bad["templates"][0]["content"] = marked("should roll back")
    bad["template_defaults"]["surge_template_name"] = "missing.conf"
    assert target.post("/api/v1/catalog/import", json={"catalog": bad}).status_code == 422
    assert target.get(BASE + "/" + imported["id"]).json()["content"] == DEFAULT_CLASH
    del catalog["templates"], catalog["template_defaults"], catalog["template_preferences"]
    del catalog["plans"][0]["clash_template_name"], catalog["plans"][0]["surge_template_name"]
    target.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    assert target.get("/api/v1/plans").json()["plans"][0]["clash_template_id"] == imported["id"]


def test_old_database_upgrade_adds_template_tables_and_plan_bindings(env):
    app, admin, _ = env
    before = admin.get("/api/v1/plans").json()["plans"][0]
    store = app.state.inventory
    original = SubscriptionPlanModel.__table__
    metadata = MetaData()
    legacy = Table(
        "legacy_subscription_plans",
        metadata,
        *[
            Column(
                column.name,
                column.type,
                *(
                    ForeignKey(key.target_fullname, ondelete=key.ondelete)
                    for key in column.foreign_keys
                ),
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
            if column.name not in {"clash_template_id", "surge_template_id"}
        ],
    )
    with store._engine.begin() as connection:
        legacy.create(connection)
        names = list(legacy.columns.keys())
        connection.execute(
            legacy.insert().from_select(names, select(*(original.c[name] for name in names)))
        )
        original.drop(connection)
        connection.exec_driver_sql(
            "ALTER TABLE legacy_subscription_plans RENAME TO subscription_plans"
        )
        connection.exec_driver_sql("DROP TABLE subscription_template_preferences")
        connection.exec_driver_sql("DROP TABLE subscription_templates")
    store.create_schema()
    store.create_schema()
    after = admin.get("/api/v1/plans").json()["plans"][0]
    assert after["clash_template_id"] is None and after["surge_template_id"] is None
    assert {key: value for key, value in after.items() if not key.endswith("_template_id")} == {
        key: value for key, value in before.items() if not key.endswith("_template_id")
    }
    assert admin.get(BASE).json()["templates"] == []


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


@pytest.mark.parametrize("format", [SubscriptionClientFormat.CLASH, SubscriptionClientFormat.SURGE])
def test_node_renaming_updates_dialer_proxy_references(env, monkeypatch, format):
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
        proxies, report = store._prepare_subscription_format(session, user, plan, format)
    assert [node.available for node in report.nodes] == [True, True]
    assert proxies[0]["name"] == "Proxy (2)"
    assert proxies[1]["dialer-proxy"] == "Proxy (2)"


def test_yaml_alias_limits_and_merge_overrides():
    source = (
        "base: &base {type: select, proxies: [__PROXY_NODES__]}\n"
        "proxy-groups: [{<<: *base, name: Proxy, type: select}]"
    )
    assert yaml.safe_load(render(source, "clash", [PROXY])[0])["proxy-groups"][0]["proxies"] == [
        "Japan"
    ]
    with pytest.raises(TemplateError):
        parse_template("a: " + "[" * 60 + "1" + "]" * 60, "clash")


def test_surge_injection_preserves_other_sections_and_quoted_credentials():
    source = (
        DEFAULT_SURGE.replace(
            "[Proxy]\n", "[Proxy]\n# keep\nstale = ss, example.invalid, 1, password=old\n"
        )
        + "\n[URL Rewrite]\n^http://example.com https://example.com 302\n"
    )
    proxy = PROXY | {
        "type": "trojan",
        "password": 'comma, quote" slash\\ equals= #hash',
        "tls": True,
    }
    value, _ = render(source, "surge", [proxy])
    assert "stale =" not in value and "# keep" in value and "[URL Rewrite]" in value
    line = next(line for line in value.splitlines() if line.startswith("Japan ="))
    parts = surge_parts(line.partition("=")[2])
    assert "password=" + proxy["password"] in parts
    assert value.partition("[Proxy Group]")[2] == source.partition("[Proxy Group]")[2]


def test_surge_rejects_duplicate_cyclic_and_missing_group_references():
    with pytest.raises(TemplateError):
        parse_template("[Proxy Group]\nA = select, DIRECT\nA = select, REJECT\n", "surge")
    with pytest.raises(TemplateError):
        parse_template("[Proxy Group]\nA = select, B\nB = select, A\n", "surge")
    with pytest.raises(TemplateError):
        render("[Proxy Group]\nA = select, Missing\n", "surge", [PROXY])


@pytest.mark.parametrize(
    "kind,extras",
    [
        ("vmess", {}),
        ("trojan", {"password": "pass"}),
        ("shadowsocks", {"password": "pass", "cipher": "aes-128-gcm"}),
        ("snell", {"psk": "pass", "version": 6}),
        ("anytls", {"password": "pass"}),
        ("hysteria2", {"password": "pass"}),
        ("socks", {}),
        ("http", {}),
    ],
)
def test_surge_protocol_representation(kind, extras):
    proxy = PROXY | {"type": kind} | extras
    assert subscription_clients.unsupported_reason(proxy, "surge") is None
    assert "Japan = " in render(DEFAULT_SURGE, "surge", [proxy])[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "vless"},
        {"type": "mieru", "username": "x", "password": "x"},
        {"network": "grpc"},
        {"network": "ws", "ws-opts": {"max-early-data": 1}},
        {"cipher": "none"},
        {"type": "trojan", "password": "x", "tls": False},
        {"type": "snell", "psk": "x", "version": 5, "obfs-opts": {"mode": "tls"}},
        {"type": "snell", "psk": "x", "version": 6, "obfs-opts": {"mode": "http"}},
        {"plugin": "obfs", "plugin-opts": {"mode": "http"}},
    ],
)
def test_surge_incompatible_options_have_explicit_reasons(changes):
    assert subscription_clients.unsupported_reason(PROXY | changes, "surge")
