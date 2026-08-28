import base64
from copy import deepcopy
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import pytest
import yaml
from open_node.domain.subscriptions import SubscriptionCatalogPlanEntry, SubscriptionPlanCreate
from open_node.services.inventory import CommandModel, ManagedNodeModel, SubscriptionAccessModel
from pydantic import ValidationError
from sqlalchemy import select, text
from test_plan_management import base, node, update_payload
from test_subscription_access import complete, current, setup
from test_subscriptions import make_client


@pytest.fixture
def env(tmp_path):
    values = setup(tmp_path)
    complete(values[0], values[1], current(values[0], values[1]))
    return values


def save(env, **changes):
    response = env[0].put(base(env) + "/settings", json=update_payload(env, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def link(client, username="alice"):
    return client.post(f"/api/v1/users/{username}/subscription-token").json()["subscription"]


def export(client, token, target):
    response = client.get("/api/v1/subscribe/" + token, params={"format": target})
    assert response.status_code == 200, response.text
    if target == "clash":
        return yaml.safe_load(response.text)
    if target in {"xray", "sing-box"}:
        return response.json()
    raw = base64.b64decode(response.text).decode() if target == "base64" else response.text
    return raw.splitlines()


def names(content, target):
    if target == "clash":
        result = [proxy["name"] for proxy in content["proxies"]]
        assert content["proxy-groups"][0]["proxies"] == result
        return result
    if target in {"sing-box", "xray"}:
        return [row["tag"] for row in content["outbounds"] if row["tag"] not in {"Proxy", "direct"}]
    return [unquote(urlsplit(uri).fragment) for uri in content]


@pytest.mark.parametrize("target", ["clash", "sing-box", "xray", "uri-list", "base64"])
def test_aliases_apply_before_multipliers_to_every_format_and_preview(env, target):
    client, _, _, node_id, *_ = env
    original_nodes = client.get("/api/v1/nodes").json()
    credentials = client.get("/api/v1/users/alice/credentials").json()
    links = link(client)
    before = export(client, links["token"], target)
    assert names(before, target) == ["[1.5] Tokyo base"]
    alias = "\u4e1c\u4eac / Premium #1"
    saved = save(
        env, node_name_overrides={node_id: "  " + alias + "  "}, node_name_override_enabled=True
    )
    assert saved["commands"] == []
    assert names(export(client, links["token"], target), target) == ["[1.5] " + alias]
    report = client.get("/api/v1/users/alice/subscription-preview", params={"format": target})
    assert report.status_code == 200, report.text
    assert report.json()["nodes"][0]["name"] == "[1.5] " + alias
    assert client.get("/api/v1/nodes").json() == original_nodes
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    assert link(client) == links
    save(env, node_name_override_enabled=False)
    assert export(client, links["token"], target) == before
    assert client.get(base(env) + "/settings").json()["plan"]["node_name_overrides"] == {
        node_id: alias
    }


def test_cosmetic_edit_leaves_runtime_rows_and_other_plan_unchanged(env):
    client, _, _, node_id, *_ = env
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    other = client.post(
        "/api/v1/plans", json={"name": "Other", "traffic_limit_gb": 1, "node_ids": [node_id]}
    ).json()["plan"]
    client.post("/api/v1/users/bob/plan", json={"plan_id": other["id"]}).raise_for_status()
    bob = link(client, "bob")
    before_export = export(client, bob["token"], "clash")
    store = client.app.state.inventory

    def runtime_rows():
        with store._session() as db:
            return [
                [
                    {
                        column.name: deepcopy(getattr(row, column.name))
                        for column in model.__table__.columns
                    }
                    for row in db.scalars(select(model))
                ]
                for model in (CommandModel, SubscriptionAccessModel)
            ]

    before = runtime_rows()
    stale = update_payload(env)
    save(env, node_name_overrides={node_id: "Alias"}, node_name_override_enabled=True)
    assert runtime_rows() == before
    assert export(client, bob["token"], "clash") == before_export
    assert client.put(base(env) + "/settings", json=stale).status_code == 409
    legacy = update_payload(env, description="Legacy edit")
    del legacy["node_name_overrides"], legacy["node_name_override_enabled"]
    result = client.put(base(env) + "/settings", json=legacy).raise_for_status().json()
    assert result["plan"]["node_name_overrides"] == {node_id: "Alias"}
    assert result["plan"]["node_name_override_enabled"] is True
    assert save(env, node_name_overrides={})["plan"]["node_name_overrides"] == {}


@pytest.mark.parametrize(
    "invalid", ["x" * 129, "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "a\x85b", 42, None]
)
def test_validation_rejects_invalid_names_in_create_update_and_catalog(env, invalid):
    client, _, _, node_id, *_ = env
    payload = update_payload(env, node_name_overrides={node_id: invalid})
    assert client.put(base(env) + "/settings", json=payload).status_code == 422
    create = {
        key: value for key, value in payload.items() if key in SubscriptionPlanCreate.model_fields
    }
    create["name"] = "Invalid"
    assert client.post("/api/v1/plans", json=create).status_code == 422
    with pytest.raises(ValidationError):
        SubscriptionCatalogPlanEntry(
            name="Invalid",
            traffic_limit_gb=1,
            node_names=["node"],
            node_name_overrides={"node": invalid},
        )


def test_normalization_duplicates_pruning_and_unicode_limits(env):
    other = node(env)
    client, _, _, node_id, *_ = env
    duplicate = update_payload(
        env,
        node_ids=[node_id, other["id"]],
        node_name_overrides={node_id: "Alias", other["id"]: " Alias "},
    )
    assert client.put(base(env) + "/settings", json=duplicate).status_code == 422
    name = "\U0001f680" * 128
    saved = save(
        env, node_name_overrides={node_id: name, other["id"]: "ignored", str(uuid4()): "ignored"}
    )
    assert saved["plan"]["node_name_overrides"] == {node_id: name}
    created = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Aliases",
                "traffic_limit_gb": 1,
                "node_ids": [node_id],
                "node_name_overrides": {node_id: "  "},
                "node_name_override_enabled": True,
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    assert created["node_name_overrides"] == {} and created["node_name_override_enabled"]
    save(env, node_ids=[], node_multipliers={}, node_name_overrides={node_id: name})
    assert client.get(base(env) + "/settings").json()["plan"]["node_name_overrides"] == {}


@pytest.mark.parametrize("target", ["clash", "sing-box", "xray", "uri-list", "base64"])
def test_alias_collisions_with_original_and_reserved_names_are_deduplicated(env, target):
    client, _, _, node_id, *_ = env
    other = node(env)
    save(
        env,
        node_ids=[node_id, other["id"]],
        node_multipliers={},
        node_name_overrides={node_id: "direct"},
        node_name_override_enabled=True,
    )
    with client.app.state.inventory._coordinated_session() as db:
        row = db.get(ManagedNodeModel, other["id"])
        row.config = {**row.config, "name": "direct"}
        db.commit()
    assert names(export(client, link(client)["token"], target), target) == [
        "direct (2)",
        "direct (3)",
    ]


def test_catalog_round_trip_remaps_ids_preserves_toggle_and_legacy_updates(env, tmp_path):
    client, _, _, node_id, *_ = env
    save(env, node_name_overrides={node_id: "Alias"}, node_name_override_enabled=True)
    catalog = client.get("/api/v1/catalog/export").raise_for_status().json()["catalog"]
    assert catalog["plans"][0]["node_name_overrides"] == {"Tokyo vless": "Alias"}
    destination = tmp_path / "destination"
    destination.mkdir()
    other = make_client(destination)
    other.post("/api/v1/servers", json={"name": "edge-sub"}).raise_for_status()
    other.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    plan = other.get("/api/v1/plans").json()["plans"][0]
    assert plan["node_ids"] != [node_id]
    assert plan["node_name_overrides"] == {plan["node_ids"][0]: "Alias"}
    assert plan["node_name_override_enabled"] is True
    del (
        catalog["plans"][0]["node_name_overrides"],
        catalog["plans"][0]["node_name_override_enabled"],
    )
    other.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    assert (
        other.get("/api/v1/plans").json()["plans"][0]["node_name_overrides"]
        == plan["node_name_overrides"]
    )
    catalog["plans"][0]["node_name_overrides"] = {}
    catalog["plans"][0]["node_name_override_enabled"] = False
    other.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    assert other.get("/api/v1/plans").json()["plans"][0]["node_name_overrides"] == {}


def test_ambiguous_catalog_names_fail_without_partial_changes(env):
    client, _, _, node_id, *_ = env
    save(env, node_name_overrides={node_id: "Alias"}, node_name_override_enabled=True)
    catalog = client.get("/api/v1/catalog/export").json()["catalog"]
    catalog["users"][0]["remark"] = "must roll back"
    duplicate = deepcopy(catalog["nodes"][0])
    other = client.post("/api/v1/servers", json={"name": "other"}).json()["server"]
    duplicate["server_name"] = other["name"]
    catalog["nodes"].append(duplicate)
    assert client.post("/api/v1/catalog/import", json={"catalog": catalog}).status_code == 409
    assert client.get("/api/v1/users").json()["users"][0]["remark"] != "must roll back"
    fresh = node(env)
    with client.app.state.inventory._coordinated_session() as db:
        db.get(ManagedNodeModel, fresh["id"]).name = "Tokyo vless"
        db.commit()
    assert client.get("/api/v1/catalog/export").status_code == 409


def test_old_database_upgrade_is_repeatable_and_preserves_plan_and_credentials(env):
    client = env[0]
    before = client.get(base(env) + "/settings").json()
    credentials = client.get("/api/v1/users/alice/credentials").json()
    store = client.app.state.inventory
    with store._engine.begin() as db:
        db.execute(text("ALTER TABLE subscription_plans DROP COLUMN node_name_overrides"))
        db.execute(text("ALTER TABLE subscription_plans DROP COLUMN node_name_override_enabled"))
    store.create_schema()
    store.create_schema()
    assert client.get(base(env) + "/settings").json() == before
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    save(env, node_name_overrides={env[3]: "After upgrade"})
    store.create_schema()
    assert store.list_subscription_plans()[0].node_name_overrides == {UUID(env[3]): "After upgrade"}


def test_node_removal_prunes_only_removed_aliases(env):
    from test_node_management import remove

    client, token, _, node_id, *_ = env
    client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "aliases",
            "capabilities": {
                "rpc": True,
                "native_limiter": True,
                "subscription_access": True,
                "node_cleanup": True,
            },
        },
    ).raise_for_status()
    other = node(env)
    save(
        env,
        node_ids=[node_id, other["id"]],
        node_name_overrides={node_id: "First", other["id"]: "Second"},
        node_name_override_enabled=True,
    )
    complete(client, token, current(client, token))
    response = remove(client, node_id)
    assert response.status_code == 202, response.text
    plan = client.get(base(env) + "/settings").json()["plan"]
    assert plan["node_name_overrides"] == {other["id"]: "Second"}
    assert plan["node_name_override_enabled"] is True
    client.get("/api/v1/catalog/export").raise_for_status()
