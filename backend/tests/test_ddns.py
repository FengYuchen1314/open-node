from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest
from conftest import authenticated_client
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services import ddns as ddns_service
from open_node.services import ddns_providers
from open_node.services.ddns_providers import (
    CloudflareClient,
    DNSProviderFailure,
    provider_client,
    split_fqdn,
)
from open_node.services.inventory import ServerModel


def client(tmp_path: Path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'ddns.db'}",
        certificate_state_dir=tmp_path / "certificates",
    ))
    return authenticated_client(app)


def provider(browser, kind="cloudflare"):
    credentials = {
        "cloudflare": {"CF_DNS_API_TOKEN": "private-cloudflare-token"},
        "godaddy": {"GODADDY_API_KEY": "key", "GODADDY_API_SECRET": "secret"},
    }[kind]
    response = browser.post("/api/v1/certificates/providers", json={
        "name": "主 DNS", "provider": kind, "credentials": credentials,
    })
    assert response.status_code == 201, response.text
    return response.json()


def server(browser, *, address="203.0.113.12"):
    response = browser.post("/api/v1/servers", json={
        "name": "动态节点", "ip_address": address,
        "ip_address_v6": "2001:db8::12", "xray_mode": "external",
    })
    assert response.status_code == 201, response.text
    return response.json()["server"]


class FakeProvider:
    def __init__(self):
        self.records = []
        self.probes = []

    def upsert(self, fqdn, record_type, content):
        self.records.append((fqdn, record_type, content))

    def can_manage(self, fqdn):
        self.probes.append(fqdn)
        return True


def test_ddns_config_sync_ip_drift_manual_queue_and_provider_guard(tmp_path, monkeypatch):
    browser = client(tmp_path)
    dns = provider(browser)
    node = server(browser)
    fake = FakeProvider()
    monkeypatch.setattr(ddns_service, "provider_client", lambda *_: fake)

    configured = browser.put(f"/api/v1/ddns/{node['id']}", json={
        "enabled": True, "provider_id": dns["id"],
        "pull_address": "edge.example.co.uk", "pull_address_v6": "edge6.example.co.uk",
        "expected_revision": 0,
    })
    assert configured.status_code == 200, configured.text
    assert configured.json()["revision"] == 1
    job = browser.app.state.ddns.claim()
    assert job and job["revision"] == 1
    browser.app.state.ddns.execute(job)
    browser.app.state.ddns.finish(job)
    assert fake.records == [
        ("edge.example.co.uk", "A", "203.0.113.12"),
        ("edge6.example.co.uk", "AAAA", "2001:db8::12"),
    ]
    status = browser.get("/api/v1/ddns").json()["servers"][0]
    assert status["last_synced_at"] and status["last_error"] is None and not status["pending"]

    with browser.app.state.inventory._session_factory.begin() as db:
        row = db.get(ServerModel, node["id"])
        row.ip_address = "203.0.113.13"
    drift = browser.app.state.ddns.claim()
    assert drift and drift["ipv4"] == "203.0.113.13"
    browser.app.state.ddns.execute(drift)
    browser.app.state.ddns.finish(drift)

    queued = browser.post(f"/api/v1/ddns/{node['id']}/sync")
    assert queued.status_code == 200 and queued.json()["queued"] is True
    assert browser.app.state.ddns.claim() is not None
    blocked = browser.delete("/api/v1/certificates/providers/" + dns["id"])
    assert blocked.status_code == 409


def test_ddns_revision_and_stale_completion_are_fenced(tmp_path, monkeypatch):
    browser = client(tmp_path)
    dns = provider(browser)
    node = server(browser)
    monkeypatch.setattr(ddns_service, "provider_client", lambda *_: FakeProvider())
    payload = {"enabled": True, "provider_id": dns["id"],
               "pull_address": "edge.example.com", "pull_address_v6": None,
               "expected_revision": 0}
    assert browser.put(f"/api/v1/ddns/{node['id']}", json=payload).status_code == 200
    job = browser.app.state.ddns.claim()
    conflict = browser.put(f"/api/v1/ddns/{node['id']}", json=payload)
    assert conflict.status_code == 409 and conflict.json()["code"] == "ddns_revision_conflict"
    disabled = browser.put(f"/api/v1/ddns/{node['id']}", json={
        **payload, "enabled": False, "expected_revision": 1,
    })
    assert disabled.status_code == 200
    browser.app.state.ddns.finish(job)
    status = browser.get("/api/v1/ddns").json()["servers"][0]
    assert status["enabled"] is False and status["last_synced_at"] is None


def test_ddns_auto_provider_and_fixed_failure_do_not_echo_remote_body(tmp_path, monkeypatch):
    browser = client(tmp_path)
    dns = provider(browser)
    node = server(browser, address="")
    with browser.app.state.inventory._session_factory.begin() as db:
        row = db.get(ServerModel, node["id"])
        row.ip_address_v6 = ""
    fake = FakeProvider()
    monkeypatch.setattr(ddns_service, "provider_client", lambda *_: fake)
    response = browser.put(f"/api/v1/ddns/{node['id']}", json={
        "enabled": True, "provider_id": None, "pull_address": "edge.example.com",
        "pull_address_v6": None, "expected_revision": 0,
    })
    assert response.status_code == 200
    job = browser.app.state.ddns.claim()
    with pytest.raises(DNSProviderFailure) as caught:
        browser.app.state.ddns.execute(job)
    assert caught.value.code == "ddns_no_public_address"
    browser.app.state.ddns.finish(job, caught.value.code)
    state = browser.get("/api/v1/ddns").json()["servers"][0]
    assert state["last_error"] == "ddns_no_public_address"
    assert dns["id"] not in browser.get("/api/v1/ddns").text or "private" not in browser.get(
        "/api/v1/ddns"
    ).text

    secret = "provider-body-secret"
    error = HTTPError("https://fixed.example", 403, "denied", {}, BytesIO(secret.encode()))
    monkeypatch.setattr(
        ddns_providers, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(DNSProviderFailure) as remote:
        ddns_providers._read(ddns_providers.Request("https://fixed.example"))
    assert remote.value.code == "ddns_provider_rejected" and secret not in str(remote.value)


def test_provider_contracts_and_public_suffix_split(monkeypatch):
    assert split_fqdn("Edge.Example.Co.Uk.") == ("example.co.uk", "edge")
    for kind, credentials in {
        "cloudflare": {"CF_DNS_API_TOKEN": "token"},
        "alidns": {"ALICLOUD_ACCESS_KEY": "id", "ALICLOUD_SECRET_KEY": "secret"},
        "tencentcloud": {"TENCENTCLOUD_SECRET_ID": "id", "TENCENTCLOUD_SECRET_KEY": "secret"},
        "dnspod": {"DNSPOD_API_KEY": "1,token"},
        "godaddy": {"GODADDY_API_KEY": "id", "GODADDY_API_SECRET": "secret"},
        "namesilo": {"NAMESILO_API_KEY": "secret"},
    }.items():
        assert provider_client(kind, credentials)

    client = CloudflareClient({"CF_DNS_API_TOKEN": "token"})
    calls = []
    def call(path, *, method="GET", body=None):
        calls.append((path, method, body))
        if path.startswith("/zones?"):
            return [{"id": "zone"}]
        if "dns_records?" in path:
            return []
        return {}
    monkeypatch.setattr(client, "call", call)
    client.upsert("edge.example.com", "A", "203.0.113.4")
    assert calls[-1][1] == "POST" and calls[-1][2]["proxied"] is False

    signed = []
    def signed_json(request):
        signed.append(request)
        return {"Response": {}} if request.full_url.startswith(
            "https://dnspod.tencentcloudapi.com"
        ) else {}
    monkeypatch.setattr(ddns_providers, "_json", signed_json)
    ali = provider_client("alidns", {
        "ALICLOUD_ACCESS_KEY": "ali-id", "ALICLOUD_SECRET_KEY": "ali-secret",
    })
    assert ali.can_manage("edge.example.com")
    assert "Signature=" in signed[-1].full_url and "ali-secret" not in signed[-1].full_url
    tencent = provider_client("tencentcloud", {
        "TENCENTCLOUD_SECRET_ID": "tc-id", "TENCENTCLOUD_SECRET_KEY": "tc-secret",
    })
    assert tencent.can_manage("edge.example.com")
    headers = dict(signed[-1].header_items())
    assert "tc-id" in headers["Authorization"] and "tc-secret" not in str(headers)
