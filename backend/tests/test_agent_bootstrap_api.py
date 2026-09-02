import asyncio
import json
import shlex
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from threading import get_ident
from uuid import uuid4

import pytest
from conftest import ADMIN_PASSWORD
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from open_node.api.routes.agent_bootstrap import BootstrapRedeemRequest, redeem_bootstrap
from open_node.api.routes.agents import _request_public_ipv4
from open_node.core.config import Settings
from open_node.domain.inventory import ServerCreate, ServerRecord
from open_node.main import create_app
from open_node.resources import agent_installer
from open_node.services import agent_bootstrap_release as releases
from open_node.services.agent_bootstrap import AgentBootstrapTicketModel, normalize_control_url
from open_node.services.backup_runtime import backup_operation
from open_node.services.inventory import ServerModel
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, update

CONTROL_URL = "https://bootstrap.example/panel"
REDEMPTION_ERROR = {"detail": "Invalid or expired installation ticket"}
INPUT_SECRET = "bootstrap-request-input-must-not-be-echoed"


def test_agent_request_ipv4_prefers_the_socket_and_trusts_only_a_private_proxy_peer():
    assert _request_public_ipv4("8.8.8.8", "1.1.1.1") == "8.8.8.8"
    assert _request_public_ipv4("172.18.0.2", "9.9.9.9, 1.1.1.1") == "1.1.1.1"
    assert _request_public_ipv4("testclient", "1.1.1.1") is None
    assert _request_public_ipv4("172.18.0.2", "127.0.0.1, 10.0.0.2") is None
    assert _request_public_ipv4("2001:4860:4860::8888", None) is None


@dataclass
class BootstrapAPI:
    app: FastAPI
    admin: TestClient
    public: TestClient
    server: ServerRecord = field(repr=False)
    prefix: str = "/api/v1"

    @property
    def path(self):
        return f"{self.prefix}/servers/{self.server.id}/bootstrap"

    @property
    def public_path(self):
        return f"{self.prefix}/agents/bootstrap"


@contextmanager
def api_context(tmp_path: Path, **settings) -> Iterator[BootstrapAPI]:
    configured = Settings(
        database_url=f"sqlite:///{(tmp_path / 'bootstrap-api.db').as_posix()}",
        certificate_state_dir=tmp_path / "certificates",
        agent_bootstrap_public_url=settings.pop("agent_bootstrap_public_url", CONTROL_URL),
        agent_bootstrap_artifact_dir=settings.pop(
            "agent_bootstrap_artifact_dir", tmp_path / "agent-artifacts"
        ),
        **settings,
    )
    app = create_app(configured)
    app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    admin = TestClient(app, base_url="https://testserver")
    public = TestClient(app, base_url="https://testserver")
    login = admin.post(
        configured.api_prefix + "/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert login.status_code == 200
    admin.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    server = app.state.inventory.create_server(ServerCreate(name="api-bootstrap-server"))
    try:
        yield BootstrapAPI(app, admin, public, server, configured.api_prefix)
    finally:
        admin.close()
        public.close()
        app.state.inventory._engine.dispose()
        app.state.auth.engine.dispose()


@pytest.fixture
def api(tmp_path: Path) -> Iterator[BootstrapAPI]:
    with api_context(tmp_path) as context:
        yield context


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def issue(api, **payload):
    response = api.admin.post(api.path, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    arguments = shlex.split(result["command"])
    ticket = arguments[arguments.index("--ticket") + 1]
    return result, ticket


def redeem(api, ticket, nonce=None, **options):
    return api.public.post(
        api.public_path + "/redeem",
        json={"ticket": ticket, "claim_nonce": nonce or token_urlsafe(32)},
        **options,
    )


def override_resources(monkeypatch, tmp_path, resources):
    directory = tmp_path / "resource-fixture"
    directory.mkdir()
    for filename, content in resources.items():
        (directory / filename).write_bytes(content)
    monkeypatch.setattr(releases, "files", lambda _package: directory)


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_management_bootstrap_routes_require_administrator_identity(api, method):
    response = api.public.request(method, api.path, json={})
    assert response.status_code == 401
    private(response)
    assert api.server.agent_token not in response.text
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"


@pytest.mark.parametrize("method", ["post", "delete"])
@pytest.mark.parametrize("csrf", [None, "incorrect-csrf"])
def test_bootstrap_management_mutations_require_csrf(api, method, csrf):
    api.admin.headers.pop("X-CSRF-Token")
    if csrf is not None:
        api.admin.headers["X-CSRF-Token"] = csrf
    response = api.admin.request(method, api.path, json={})
    assert response.status_code == 403
    private(response)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"
    assert api.admin.get(api.path).status_code == 200


@pytest.mark.parametrize("method", ["post", "delete"])
def test_bootstrap_management_mutations_reject_untrusted_origin(api, method):
    response = api.admin.request(
        method, api.path, json={}, headers={"Origin": "https://untrusted.example"}
    )
    assert response.status_code == 403
    private(response)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"


def test_issue_exposes_only_a_short_ticket_command_and_status_has_no_secrets(api):
    issued, ticket = issue(api, transport="websocket")
    assert set(issued) == {"issued", "command", "license_required"}
    assert issued["license_required"] is False
    assert set(issued["issued"]) == {
        "server_id",
        "server_name",
        "control_url",
        "transport",
        "issued_at",
        "expires_at",
    }
    assert issued["issued"]["server_id"] == str(api.server.id)
    assert issued["issued"]["control_url"] == CONTROL_URL
    assert issued["issued"]["transport"] == "websocket"
    assert len(ticket) == 43
    assert api.server.agent_token not in json.dumps(issued)
    assert "--agent-token" not in issued["command"]
    state_response = api.admin.get(api.path)
    private(state_response)
    state = state_response.json()
    assert state["configured"] is True
    assert state["control_url"] == CONTROL_URL
    assert state["release"]["agent_version"] == releases.release_manifest()["agent"]["version"]
    assert state["bootstrap"]["status"] == "issued"
    assert state["bootstrap"]["agent_registered"] is False
    assert ticket not in state_response.text
    assert api.server.agent_token not in state_response.text
    assert api.admin.get(api.path).json() == state


def test_installer_download_is_public_and_exact_bytes_are_checksum_bound_in_command(api):
    issued, _ = issue(api)
    download = api.public.get(api.public_path + "/installer.py")
    assert download.status_code == 200
    private(download)
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.content == releases.installer_bytes()
    checksum = sha256(download.content).hexdigest()
    assert checksum in issued["command"]
    assert issued["command"].index("sha256sum --check") < issued["command"].index(
        'python3 "$installer"'
    )
    assert "command -v python3 >/dev/null ||" in issued["command"]
    assert "command -v curl >/dev/null ||" in issued["command"]
    assert "curl --disable --proto '=https'" in issued["command"]
    assert "--tlsv1.2" in issued["command"]
    assert "--location" not in issued["command"]
    assert "--insecure" not in issued["command"]
    assert CONTROL_URL + "/api/v1/agents/bootstrap/installer.py" in issued["command"]
    manifest = api.public.get(api.public_path + "/manifest")
    assert manifest.status_code == 200
    private(manifest)
    assert manifest.json() == releases.release_manifest()
    assert agent_installer.validate_manifest(manifest.json()) == manifest.json()
    assert not api.public.cookies


def test_every_deployment_artifact_is_served_by_the_panel_with_manifest_pins(
    api,
    monkeypatch,
    tmp_path,
):
    manifest = releases.release_manifest()
    artifacts = [manifest["agent"][key] for key in ("wheel", "bootstrap", "build")] + [
        manifest["xray"]["archive"],
        *manifest["mihomo"]["assets"].values(),
    ]
    requested = []

    def local_artifact(filename):
        descriptor = api.app.state.agent_bootstrap_artifacts.descriptor(filename)
        target = tmp_path / filename
        target.write_bytes(b"fixture")
        requested.append(filename)
        return target, releases.AgentArtifact(
            filename=descriptor.filename,
            path=descriptor.path,
            sha256=descriptor.sha256,
            size=len(b"fixture"),
            upstream=descriptor.upstream,
        )

    monkeypatch.setattr(api.app.state.agent_bootstrap_artifacts, "get", local_artifact)
    for artifact in artifacts:
        response = api.public.get(artifact["path"])
        assert response.status_code == 200
        private(response)
        assert response.content == b"fixture"
        assert response.headers["x-content-sha256"] == artifact["sha256"]
        assert response.headers["content-disposition"].endswith(
            f'filename="{artifact["filename"]}"'
        )
        assert artifact["path"] == api.public_path + "/artifacts/" + artifact["filename"]
    agent = manifest["agent"]
    legacy_helper_path = (
        api.public_path + "/artifacts/agent-v" + agent["version"] + "/" + agent["wheel"]["filename"]
    )
    legacy_helper_response = api.public.get(legacy_helper_path)
    assert legacy_helper_response.status_code == 200
    private(legacy_helper_response)
    assert legacy_helper_response.content == b"fixture"
    assert requested == [artifact["filename"] for artifact in artifacts] + [
        agent["wheel"]["filename"]
    ]
    assert all("github" not in artifact["path"].lower() for artifact in artifacts)

    unavailable = api.public.get(api.public_path + "/artifacts/agent-v0.0.0/not-a-release.whl")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Unknown Agent release"}


def test_unknown_panel_artifact_fails_closed_without_redirecting(api):
    response = api.public.get(api.public_path + "/artifacts/not-a-release.bin")
    assert response.status_code == 503
    private(response)
    assert response.headers.get("location") is None
    assert response.json() == {"detail": "Unknown Agent artifact"}


def test_control_url_uses_explicit_settings_not_host_or_forwarding_headers(api):
    headers = {
        "Host": "untrusted.example:8443",
        "Forwarded": "for=192.0.2.1;host=untrusted.example;proto=http",
        "X-Forwarded-Host": "second-untrusted.example",
        "X-Forwarded-Proto": "http",
    }
    response = api.admin.post(api.path, json={}, headers=headers)
    assert response.status_code == 201
    private(response)
    assert response.json()["issued"]["control_url"] == CONTROL_URL
    assert CONTROL_URL in response.json()["command"]
    assert "untrusted.example" not in response.text
    state = api.admin.get(api.path, headers=headers)
    assert state.json()["control_url"] == CONTROL_URL


@pytest.mark.parametrize(
    "control_url",
    [
        "HTTPS://BOOTSTRAP.EXAMPLE:443/panel/",
        "https://[2001:0db8:0:0::1]:443/panel/",
        "https://m\N{LATIN SMALL LETTER U WITH DIAERESIS}nich.example/panel",
        "https://bootstrap.example/panel!$&'()*+,;=:@-",
    ],
)
def test_every_configured_canonical_url_can_be_consumed_by_the_installer(control_url):
    settings = Settings(agent_bootstrap_public_url=control_url)
    canonical = settings.agent_bootstrap_public_url
    assert canonical == normalize_control_url(control_url)
    assert agent_installer.validate_control_url(canonical) == canonical
    ticket = token_urlsafe(32)
    command = releases.installation_command(canonical, ticket, uuid4())
    arguments = shlex.split(command)
    assert arguments[arguments.index("--control-url") + 1] == canonical
    assert arguments[arguments.index("--ticket") + 1] == ticket


def test_startup_settings_error_does_not_echo_userinfo_credentials(monkeypatch):
    malformed = "https://mistaken-user:" + INPUT_SECRET + "@bootstrap.example/panel"
    monkeypatch.setenv("OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL", malformed)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert INPUT_SECRET not in str(error.value)
    assert malformed not in str(error.value)
    assert "input_value" not in str(error.value)


@pytest.mark.parametrize("control_url", [None, ""])
def test_unconfigured_control_url_disables_issue_without_creating_a_ticket(tmp_path, control_url):
    with api_context(tmp_path, agent_bootstrap_public_url=control_url) as api:
        state = api.admin.get(api.path)
        assert state.json()["configured"] is False
        assert state.json()["bootstrap"]["status"] == "not_issued"
        response = api.admin.post(api.path, json={})
        assert response.status_code == 503
        private(response)
        assert "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL" in response.text
        assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"


def test_nondefault_api_prefix_disables_the_fixed_agent_bootstrap_protocol(tmp_path):
    with api_context(tmp_path, api_prefix="/custom/v1") as api:
        assert api.admin.get(api.path).json()["configured"] is False
        response = api.admin.post(api.path, json={})
        assert response.status_code == 503
        private(response)
        assert "default /api/v1 prefix" in response.text
        assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"


def test_redeem_without_administrator_cookie_returns_the_existing_long_term_credential(api, caplog):
    _, ticket = issue(api)
    nonce = token_urlsafe(32)
    response = redeem(api, ticket, nonce)
    assert response.status_code == 200
    private(response)
    assert "set-cookie" not in response.headers
    configuration = response.json()["configuration"]
    assert set(configuration) == {
        "server_id",
        "server_name",
        "control_url",
        "agent_token",
        "transport",
        "expires_at",
    }
    assert configuration["agent_token"] == api.server.agent_token
    assert configuration["server_id"] == str(api.server.id)
    assert configuration["control_url"] == CONTROL_URL
    assert ticket not in response.text and nonce not in response.text
    assert not api.public.cookies
    state = api.admin.get(api.path)
    assert state.json()["bootstrap"]["status"] == "claimed"
    assert state.json()["bootstrap"]["agent_registered"] is False
    assert api.server.agent_token not in state.text
    assert ticket not in caplog.text
    assert nonce not in caplog.text
    assert api.server.agent_token not in caplog.text


def test_redeem_retries_require_the_original_nonce_and_do_not_reset_it(api):
    _, ticket = issue(api)
    nonce = token_urlsafe(32)
    first = redeem(api, ticket, nonce)
    second = redeem(api, ticket, nonce)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    rejected = redeem(api, ticket, token_urlsafe(32))
    assert rejected.status_code == 401
    assert rejected.json() == REDEMPTION_ERROR
    private(rejected)
    assert redeem(api, ticket, nonce).json() == first.json()
    refused_issue = api.admin.post(api.path, json={})
    assert refused_issue.status_code == 409
    assert "already claimed" in refused_issue.text


def test_administrator_session_cannot_replace_or_bypass_installation_ticket(api):
    response = api.admin.post(
        api.public_path + "/redeem",
        json={"ticket": token_urlsafe(32), "claim_nonce": token_urlsafe(32)},
    )
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    private(response)


def test_redeem_rejects_untrusted_origin_without_consuming_the_ticket(api):
    _, ticket = issue(api)
    response = redeem(api, ticket, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    private(response)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "issued"
    assert redeem(api, ticket, headers={"Origin": "https://testserver"}).status_code == 200


@pytest.mark.parametrize("claimed_first", [False, True])
def test_actual_registration_blocks_new_or_repeat_redemption_and_updates_public_status(
    api,
    claimed_first,
):
    _, ticket = issue(api)
    nonce = token_urlsafe(32)
    if claimed_first:
        assert redeem(api, ticket, nonce).status_code == 200
    registered = api.public.post(
        api.prefix + "/agents/register",
        json={
            "token": api.server.agent_token,
            "hostname": "bootstrap-registered",
            "agent_version": "open-node/0.3.0a0",
        },
    )
    assert registered.status_code == 201
    response = redeem(api, ticket, nonce)
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    state = api.admin.get(api.path).json()["bootstrap"]
    assert state["agent_registered"] is True
    assert state["agent_last_seen_at"] is not None
    assert state["agent_version"] == "open-node/0.3.0a0"
    assert api.admin.post(api.path, json={}).status_code == 409


def test_revocation_blocks_redeem_without_invalidating_the_existing_agent_credential(api):
    _, ticket = issue(api)
    response = api.admin.delete(api.path)
    assert response.status_code == 200
    private(response)
    assert response.json()["bootstrap"]["status"] == "revoked"
    rejected = redeem(api, ticket)
    assert rejected.status_code == 401
    assert rejected.json() == REDEMPTION_ERROR
    assert api.app.state.inventory.authenticate_agent(api.server.agent_token).id == api.server.id


@pytest.mark.parametrize("state", ["unknown", "expired", "credential_changed", "deleted"])
def test_unavailable_tickets_have_the_same_http_error_without_secret_disclosure(api, state):
    _, ticket = issue(api)
    with api.app.state.inventory._session() as session:
        if state == "expired":
            session.execute(
                update(AgentBootstrapTicketModel)
                .where(AgentBootstrapTicketModel.server_id == str(api.server.id))
                .values(expires_at=0)
            )
        elif state == "credential_changed":
            session.execute(
                update(ServerModel)
                .where(ServerModel.id == str(api.server.id))
                .values(agent_token=token_urlsafe(32))
            )
        elif state == "deleted":
            session.execute(delete(ServerModel).where(ServerModel.id == str(api.server.id)))
        session.commit()
    if state == "unknown":
        ticket = token_urlsafe(32)
    response = redeem(api, ticket)
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    private(response)
    assert ticket not in response.text
    assert api.server.agent_token not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"ticket": None},
        {"ticket": INPUT_SECRET},
        {"ticket": "A" * 42 + "B"},
        {"claim_nonce": None},
        {"claim_nonce": INPUT_SECRET},
        {"claim_nonce": "A" * 42 + "B"},
        {"claim_nonce": [INPUT_SECRET]},
        {"extra": INPUT_SECRET},
    ],
)
def test_secret_payload_validation_uses_uniform_errors_and_never_echoes_input(api, changes):
    _, ticket = issue(api)
    body = {"ticket": ticket, "claim_nonce": token_urlsafe(32), **changes}
    response = api.public.post(api.public_path + "/redeem", json=body)
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    private(response)
    assert INPUT_SECRET not in response.text
    assert ticket not in response.text
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "issued"


@pytest.mark.parametrize("body", [b"", b"not-json", b"[]", b"\xff", b"[" * 2000 + b"]" * 2000])
def test_malformed_json_is_rejected_without_unhandled_errors(api, body):
    response = api.public.post(
        api.public_path + "/redeem", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    private(response)


def test_duplicate_secret_request_fields_are_rejected_without_claiming(api):
    _, ticket = issue(api)
    nonce = token_urlsafe(32)
    body = json.dumps({"ticket": ticket, "claim_nonce": nonce})[:-1]
    body += ',"claim_nonce":' + json.dumps(nonce) + "}"
    response = api.public.post(
        api.public_path + "/redeem", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401
    assert response.json() == REDEMPTION_ERROR
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "issued"


@pytest.mark.parametrize("size", [8192, 8193])
def test_request_body_limit_uses_received_bytes_not_declared_content_length(api, size):
    _, ticket = issue(api)
    content = json.dumps({"ticket": ticket, "claim_nonce": token_urlsafe(32)}).encode()
    content += b" " * (size - len(content))
    response = api.public.post(
        api.public_path + "/redeem",
        content=content,
        headers={"Content-Type": "application/json; charset=utf-8", "Content-Length": "1"},
    )
    assert response.status_code == (200 if size == 8192 else 413)
    private(response)
    if size > 8192:
        assert ticket not in response.text
        assert api.app.state.agent_bootstrap.read(api.server.id).status == "issued"


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_redeem_requires_json_content_type(api, content_type):
    _, ticket = issue(api)
    response = api.public.post(
        api.public_path + "/redeem",
        content=json.dumps({"ticket": ticket, "claim_nonce": token_urlsafe(32)}),
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 415
    private(response)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "issued"


def test_redeem_rate_budget_survives_another_app_and_ignores_forwarded_ip_headers(api):
    another = create_app(api.app.state.settings)
    try:
        with TestClient(another, base_url="https://testserver") as other_client:
            clients = [api.public, other_client]
            for attempt in range(10):
                response = clients[attempt % 2].post(
                    api.public_path + "/redeem",
                    json={"ticket": token_urlsafe(32), "claim_nonce": token_urlsafe(32)},
                )
                assert response.status_code == 401
                assert response.json() == REDEMPTION_ERROR
            limited = api.public.post(
                api.public_path + "/redeem",
                json={"ticket": token_urlsafe(32), "claim_nonce": token_urlsafe(32)},
                headers={"X-Forwarded-For": "192.0.2.99", "Forwarded": "for=192.0.2.99"},
            )
            assert limited.status_code == 429
            assert limited.headers["retry-after"] == "60"
            private(limited)
            # Administrator login uses a separate budget from public installation attempts.
            login = api.admin.post(
                api.prefix + "/auth/login",
                json={"username": "admin", "password": ADMIN_PASSWORD},
                headers={"X-Open-Node-Client": "browser"},
            )
            assert login.status_code == 200
    finally:
        another.state.inventory._engine.dispose()
        another.state.auth.engine.dispose()


def test_redeem_database_lock_waits_do_not_block_the_async_request_loop(api, monkeypatch):
    _, ticket = issue(api)
    original_budget = api.app.state.auth.allow_login_attempt
    original_redeem = api.app.state.agent_bootstrap.redeem
    database_threads = []

    def record_budget(*args):
        database_threads.append(get_ident())
        return original_budget(*args)

    def record_redeem(*args):
        database_threads.append(get_ident())
        return original_redeem(*args)

    monkeypatch.setattr(api.app.state.auth, "allow_login_attempt", record_budget)
    monkeypatch.setattr(api.app.state.agent_bootstrap, "redeem", record_redeem)
    body = json.dumps({"ticket": ticket, "claim_nonce": token_urlsafe(32)}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def request():
        loop_thread = get_ident()
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": api.public_path + "/redeem",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("testclient", 12345),
            "server": ("testserver", 443),
            "app": api.app,
        }
        # Direct endpoint invocation bypasses the ASGI middleware that normally
        # establishes this operation; keep the real offloaded database calls.
        with backup_operation(api.app.state.backup_writes):
            result = await redeem_bootstrap(Request(scope, receive=receive))
        assert result["configuration"]["agent_token"] == api.server.agent_token
        assert len(database_threads) == 2
        assert all(identifier != loop_thread for identifier in database_threads)

    asyncio.run(request())


def test_secret_request_model_masks_repr_and_validation_text():
    ticket, nonce = token_urlsafe(32), token_urlsafe(32)
    model = BootstrapRedeemRequest(ticket=SecretStr(ticket), claim_nonce=SecretStr(nonce))
    assert ticket not in repr(model) and nonce not in repr(model)
    assert ticket not in model.model_dump_json() and nonce not in model.model_dump_json()
    with pytest.raises(ValidationError) as error:
        BootstrapRedeemRequest(ticket=INPUT_SECRET, claim_nonce=nonce)
    assert INPUT_SECRET not in str(error.value)
    assert nonce not in str(error.value)


def test_issue_validation_does_not_reflect_values_or_create_a_ticket(api):
    response = api.admin.post(api.path, json={"transport": "pull", "unexpected": INPUT_SECRET})
    assert response.status_code == 422
    private(response)
    assert INPUT_SECRET not in response.text
    assert all("input" not in detail for detail in response.json()["detail"])
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_management_unknown_server_is_404_not_a_credential_error(api, method):
    response = api.admin.request(method, f"{api.prefix}/servers/{uuid4()}/bootstrap", json={})
    assert response.status_code == 404
    private(response)


@pytest.mark.parametrize("missing", ["manifest", "installer", "empty_installer", "large_installer"])
def test_missing_or_unusable_resources_disable_issue(api, monkeypatch, tmp_path, missing):
    resource_files = {
        "agent-release.json": json.dumps(releases.release_manifest()).encode(),
        "agent_installer.py": releases.installer_bytes(),
    }
    if missing == "manifest":
        resource_files.pop("agent-release.json")
    elif missing == "installer":
        resource_files.pop("agent_installer.py")
    elif missing == "empty_installer":
        resource_files["agent_installer.py"] = b""
    else:
        resource_files["agent_installer.py"] = b"x" * (releases.INSTALLER_LIMIT_BYTES + 1)
    override_resources(monkeypatch, tmp_path, resource_files)
    status = api.admin.get(api.path)
    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert status.json()["release"] is None
    response = api.admin.post(api.path, json={})
    assert response.status_code == 503
    private(response)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"
    path = "/manifest" if missing == "manifest" else "/installer.py"
    download = api.public.get(api.public_path + path)
    assert download.status_code == 503
    private(download)


@pytest.mark.parametrize(
    "invalid",
    [
        "json",
        "duplicate",
        "boolean_schema",
        "extra_field",
        "missing_build",
        "path",
        "hash",
        "source",
        "tag",
        "xray",
        "extra_artifact_field",
        "nonfinite",
        "large",
        "deep",
    ],
)
def test_invalid_release_manifest_is_unavailable_before_issuing_commands(
    api,
    monkeypatch,
    tmp_path,
    invalid,
):
    manifest = releases.release_manifest()
    if invalid == "boolean_schema":
        manifest["schema_version"] = True
    elif invalid == "extra_field":
        manifest["extra"] = INPUT_SECRET
    elif invalid == "missing_build":
        del manifest["agent"]["build"]
    elif invalid == "path":
        manifest["agent"]["wheel"]["path"] = "/untrusted/agent.whl"
    elif invalid == "hash":
        manifest["agent"]["wheel"]["sha256"] = INPUT_SECRET
    elif invalid == "source":
        manifest["agent"]["source_commit"] = "main"
    elif invalid == "tag":
        manifest["agent"]["tag"] = "latest"
    elif invalid == "xray":
        manifest["xray"]["archive"]["sha256"] = "0" * 64
    elif invalid == "extra_artifact_field":
        manifest["agent"]["wheel"]["extra"] = INPUT_SECRET
    elif invalid == "nonfinite":
        manifest["schema_version"] = float("nan")
    content = json.dumps(manifest).encode()
    if invalid == "json":
        content = INPUT_SECRET.encode()
    elif invalid == "duplicate":
        content = content[:-1] + b',"schema_version":1}'
    elif invalid == "large":
        content += b" " * releases.MANIFEST_LIMIT_BYTES
    elif invalid == "deep":
        content = b"[" * 2000 + b"]" * 2000
    override_resources(
        monkeypatch,
        tmp_path,
        {"agent-release.json": content, "agent_installer.py": releases.installer_bytes()},
    )
    assert api.admin.get(api.path).json()["configured"] is False
    unavailable = api.admin.post(api.path, json={})
    assert unavailable.status_code == 503
    assert INPUT_SECRET not in unavailable.text
    public = api.public.get(api.public_path + "/manifest")
    assert public.status_code == 503
    assert public.json() == {"detail": "Verified Agent release is not available"}
    private(public)
    assert api.app.state.agent_bootstrap.read(api.server.id).status == "not_issued"
