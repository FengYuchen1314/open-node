from datetime import UTC, datetime
from uuid import uuid4

import yaml
from open_node.services.inventory import SubscriptionProfileModel
from test_subscriber_auth import login, make

BASE = "/api/v1/subscription-customizations"


def profile(app, *, code="customized"):
    now = datetime.now(UTC)
    row = SubscriptionProfileModel(
        id=str(uuid4()),
        owner_username="alice",
        name="Customized",
        description="Official custom rule and provider semantics",
        node_ids=[],
        enabled=True,
        sort_order=0,
        source_type="managed",
        source_filename="",
        source_template_filename="",
        legacy_file_short_code=code,
        legacy_selected_node_ids=[],
        legacy_selected_tags=[],
        migration_warnings=[],
        created_at=now,
        updated_at=now,
    )
    with app.state.inventory._session() as session:
        session.add(row)
        session.commit()
    return row.id


def profile_update(value, **changes):
    fields = (
        "name",
        "description",
        "node_ids",
        "clash_template_id",
        "surge_template_id",
        "custom_rules_enabled",
        "selected_custom_rule_ids",
        "proxy_providers_enabled",
        "selected_proxy_provider_ids",
        "assigned_usernames",
        "enabled",
    )
    return {
        **{field: value[field] for field in fields},
        "expected_revision": value["revision"],
        **changes,
    }


def test_profile_rules_and_snapshot_provider_render_as_one_clash_subscription(tmp_path):
    app, admin, _subscriber = make(tmp_path, catalog=True)
    profile_id = profile(app)
    source = admin.post(
        "/api/v1/external-subscriptions",
        json={
            "owner_username": "alice",
            "name": "Confirmed snapshot",
            "url": "https://provider.example/subscription?token=never-return-this",
        },
    ).raise_for_status().json()
    rule = admin.post(
        BASE + "/rules",
        json={
            "owner_username": "alice",
            "name": "Private domains",
            "type": "rules",
            "mode": "prepend",
            "content": "- DOMAIN-SUFFIX,example.com,Proxy\n",
            "enabled": True,
        },
    ).raise_for_status().json()
    provider = admin.post(
        BASE + "/providers",
        json={
            "owner_username": "alice",
            "external_source_id": source["id"],
            "name": "Airport",
            "filter": "Hong Kong|Japan",
        },
    ).raise_for_status().json()
    current = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    assert current["id"] == profile_id
    saved = admin.put(
        f"/api/v1/subscription-profiles/{profile_id}",
        json=profile_update(
            current,
            custom_rules_enabled=True,
            selected_custom_rule_ids=[rule["id"]],
            proxy_providers_enabled=True,
            selected_proxy_provider_ids=[provider["id"]],
        ),
    )
    assert saved.status_code == 200, saved.text

    rendered = admin.get("/x/customized")
    assert rendered.status_code == 200, rendered.text
    value = yaml.safe_load(rendered.text)
    assert value["rules"][0] == "DOMAIN-SUFFIX,example.com,Proxy"
    assert value["proxy-groups"][0]["use"] == ["Airport"]
    provider_url = value["proxy-providers"]["Airport"]["url"]
    assert provider_url == (
        f"https://testserver/api/v1/proxy-provider/customized/{provider['id']}"
    )
    assert "provider.example" not in rendered.text and "never-return-this" not in rendered.text
    provider_response = admin.get(provider_url)
    assert provider_response.status_code == 200
    assert yaml.safe_load(provider_response.text) == {"proxies": []}
    assert provider_response.headers["x-open-node-included-nodes"] == "0"

    latest = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    admin.put(
        f"/api/v1/subscription-profiles/{profile_id}",
        json=profile_update(latest, proxy_providers_enabled=False),
    ).raise_for_status()
    assert admin.get(provider_url).status_code == 404
    removed = admin.post(
        f"{BASE}/providers/{provider['id']}/delete",
        json={"expected_revision": provider["revision"]},
    )
    assert removed.status_code == 204
    assert admin.get("/api/v1/subscription-profiles").json()["profiles"][0][
        "selected_proxy_provider_ids"
    ] == []


def test_owner_boundaries_validation_and_subscriber_crud(tmp_path):
    app, admin, subscriber = make(tmp_path, catalog=True)
    profile_id = profile(app, code="owner-boundary")
    admin.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    bob_rule = admin.post(
        BASE + "/rules",
        json={
            "owner_username": "bob",
            "name": "Bob only",
            "type": "dns",
            "mode": "replace",
            "content": "enable: true\nnameserver: [1.1.1.1]\n",
        },
    ).raise_for_status().json()
    current = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    crossed = admin.put(
        f"/api/v1/subscription-profiles/{profile_id}",
        json=profile_update(
            current,
            custom_rules_enabled=True,
            selected_custom_rule_ids=[bob_rule["id"]],
        ),
    )
    assert crossed.status_code == 409

    assert login(subscriber).status_code == 200
    assert subscriber.get("/api/v1/account/subscription-customizations/rules").json()[
        "rules"
    ] == []
    hidden = subscriber.put(
        f"/api/v1/account/subscription-customizations/rules/{bob_rule['id']}",
        json={
            "name": "Stolen",
            "type": "dns",
            "mode": "replace",
            "content": "enable: false\n",
            "enabled": True,
            "expected_revision": bob_rule["revision"],
        },
    )
    assert hidden.status_code == 404
    own = subscriber.post(
        "/api/v1/account/subscription-customizations/rules",
        json={
            "name": "Alice DNS",
            "type": "dns",
            "mode": "replace",
            "content": "dns:\n  enable: true\n  nameserver: [1.1.1.1]\n",
            "enabled": True,
        },
    )
    assert own.status_code == 201, own.text
    assert own.json()["owner_username"] == "alice"


def test_invalid_rule_and_provider_inputs_are_rejected_without_echo(tmp_path):
    _app, admin, _subscriber = make(tmp_path, catalog=True)
    bad_rule = admin.post(
        BASE + "/rules",
        json={
            "owner_username": "alice",
            "name": "Broken",
            "type": "rules",
            "mode": "append",
            "content": "rules: {secret-value: not-a-list}",
        },
    )
    assert bad_rule.status_code == 422
    assert "secret-value" not in bad_rule.text
    duplicate = admin.post(
        BASE + "/rules",
        json={
            "owner_username": "alice",
            "name": "Duplicate YAML keys",
            "type": "rules",
            "mode": "append",
            "content": "rules: [MATCH,Proxy]\nrules: [MATCH,DIRECT]\n",
        },
    )
    assert duplicate.status_code == 422
    source = admin.post(
        "/api/v1/external-subscriptions",
        json={
            "owner_username": "alice",
            "name": "Provider",
            "url": "https://provider.example/secret",
        },
    ).raise_for_status().json()
    bad_provider = admin.post(
        BASE + "/providers",
        json={
            "owner_username": "alice",
            "external_source_id": source["id"],
            "name": "Broken regex",
            "filter": "(?P<private-secret>",
        },
    )
    assert bad_provider.status_code == 422
    assert "private-secret" not in bad_provider.text


def test_deleting_external_source_cascades_provider_and_profile_reference(tmp_path):
    app, admin, _subscriber = make(tmp_path, catalog=True)
    profile_id = profile(app, code="source-cascade")
    source = admin.post(
        "/api/v1/external-subscriptions",
        json={
            "owner_username": "alice",
            "name": "Disposable source",
            "url": "https://provider.example/disposable",
        },
    ).raise_for_status().json()
    provider = admin.post(
        BASE + "/providers",
        json={
            "owner_username": "alice",
            "external_source_id": source["id"],
            "name": "Disposable provider",
        },
    ).raise_for_status().json()
    current = admin.get("/api/v1/subscription-profiles").json()["profiles"][0]
    admin.put(
        f"/api/v1/subscription-profiles/{profile_id}",
        json=profile_update(
            current,
            proxy_providers_enabled=True,
            selected_proxy_provider_ids=[provider["id"]],
        ),
    ).raise_for_status()

    removed = admin.post(
        f"/api/v1/external-subscriptions/{source['id']}/delete",
        json={"expected_revision": source["revision"], "confirm": True},
    )
    assert removed.status_code == 200, removed.text
    assert admin.get(BASE + "/providers").json()["providers"] == []
    assert admin.get("/api/v1/subscription-profiles").json()["profiles"][0][
        "selected_proxy_provider_ids"
    ] == []
