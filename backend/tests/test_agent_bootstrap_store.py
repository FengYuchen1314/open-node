import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from threading import Barrier, Event
from uuid import uuid4

import pytest
from open_node.domain.agent_bootstrap import AgentBootstrapConfig
from open_node.domain.inventory import (
    AgentHeartbeatRequest,
    AgentRegistrationRequest,
    ServerCreate,
    ServerRecord,
    ServerStatus,
)
from open_node.services.agent_bootstrap import (
    CLAIM_RETRY_SECONDS,
    TICKET_LIFETIME_SECONDS,
    AgentBootstrapRedemptionError,
    AgentBootstrapStore,
    AgentBootstrapTicketModel,
    AgentBootstrapUnavailableError,
    normalize_control_url,
)
from open_node.services.inventory import (
    AgentModel,
    InventoryStore,
    ServerModel,
    ServerNotFoundError,
)
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, update

CONTROL_URL = "https://control.example/prefix"
REDEMPTION_ERROR = "Invalid or expired installation ticket"


@dataclass
class Clock:
    now: float = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Environment:
    inventory: InventoryStore
    store: AgentBootstrapStore
    server: ServerRecord
    clock: Clock
    database_url: str


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Environment]:
    database_url = f"sqlite:///{(tmp_path / 'bootstrap.db').as_posix()}"
    inventory = InventoryStore(database_url)
    inventory.create_schema()
    clock = Clock()
    store = AgentBootstrapStore(inventory, clock=clock)
    server = inventory.create_server(ServerCreate(name="bootstrap-server"))
    try:
        yield Environment(inventory, store, server, clock, database_url)
    finally:
        inventory._engine.dispose()


@contextmanager
def independent_store(env: Environment) -> Iterator[AgentBootstrapStore]:
    inventory = InventoryStore(env.database_url)
    try:
        yield AgentBootstrapStore(inventory, clock=env.clock)
    finally:
        inventory._engine.dispose()


def denied(store, ticket, nonce):
    with pytest.raises(AgentBootstrapRedemptionError) as error:
        store.redeem(ticket, nonce)
    assert str(error.value) == REDEMPTION_ERROR


def register(env: Environment):
    return env.inventory.register_agent(
        AgentRegistrationRequest(
            token=env.server.agent_token,
            hostname="remote-agent",
            agent_version="open-node/0.3.0a0",
        )
    )


def test_never_issued_state_contains_only_public_observations(env):
    state = env.store.read(env.server.id)
    assert state.model_dump(mode="json") == {
        "server_id": str(env.server.id),
        "server_name": env.server.name,
        "status": "not_issued",
        "issued_at": None,
        "expires_at": None,
        "claimed_at": None,
        "agent_registered": False,
        "agent_registered_at": None,
        "agent_last_seen_at": None,
        "agent_version": None,
        "server_last_heartbeat": None,
    }
    assert env.store.revoke(env.server.id) == state


def test_issue_persists_hashes_only_and_redacts_secret_by_default(env):
    issued = env.store.issue(env.server.id, "HTTPS://CONTROL.example:443/prefix/")
    ticket = issued.ticket.get_secret_value()
    assert len(ticket) == 43
    assert issued.control_url == CONTROL_URL
    assert issued.transport == "auto"
    assert issued.issued_at == datetime.fromtimestamp(env.clock.now, UTC)
    assert (issued.expires_at - issued.issued_at).total_seconds() == TICKET_LIFETIME_SECONDS
    assert issued.model_dump(mode="json")["ticket"] == "**********"
    assert ticket not in repr(issued)
    assert ticket not in issued.model_dump_json()
    assert env.server.agent_token not in issued.model_dump_json()

    with env.inventory._session() as session:
        row = session.get(AgentBootstrapTicketModel, str(env.server.id))
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        assert values["ticket_hash"] == sha256(ticket.encode()).hexdigest()
        assert values["credential_hash"] == sha256(env.server.agent_token.encode()).hexdigest()
        assert values["claim_nonce_hash"] is None
        assert values["claimed_at"] is None
        assert values["revoked_at"] is None
        assert "ticket" not in values and "agent_token" not in values
        assert ticket not in json.dumps(values)
        assert env.server.agent_token not in json.dumps(values)

    state = env.store.read(env.server.id)
    assert state.status == "issued"
    assert state.expires_at == issued.expires_at
    assert not state.agent_registered
    sensitive = {"ticket", "ticket_hash", "credential_hash", "agent_token"}
    assert not (sensitive & type(state).model_fields.keys())
    assert ticket not in state.model_dump_json()
    assert env.server.agent_token not in state.model_dump_json()


@pytest.mark.parametrize("transport", ["auto", "websocket", "http"])
def test_redeem_returns_existing_token_and_claim_is_not_registration(env, transport):
    issued = env.store.issue(env.server.id, CONTROL_URL, transport)
    nonce = token_urlsafe(32)
    configuration = env.store.redeem(issued.ticket, SecretStr(nonce))
    assert configuration.server_id == env.server.id
    assert configuration.server_name == env.server.name
    assert configuration.control_url == CONTROL_URL
    assert configuration.transport == transport
    assert configuration.agent_token.get_secret_value() == env.server.agent_token
    assert configuration.model_dump(mode="json")["agent_token"] == "**********"
    assert env.server.agent_token not in repr(configuration)
    assert env.server.agent_token not in configuration.model_dump_json()
    assert configuration.expires_at.timestamp() == env.clock.now + CLAIM_RETRY_SECONDS

    state = env.store.read(env.server.id)
    assert state.status == "claimed"
    assert state.claimed_at == datetime.fromtimestamp(env.clock.now, UTC)
    assert state.expires_at == configuration.expires_at
    assert not state.agent_registered
    assert state.agent_version is None
    assert state.agent_last_seen_at is None
    assert state.server_last_heartbeat is None
    with env.inventory._session() as session:
        row = session.get(AgentBootstrapTicketModel, str(env.server.id))
        assert row.claim_nonce_hash == sha256(nonce.encode()).hexdigest()
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        assert nonce not in json.dumps(values)
    assert nonce not in state.model_dump_json()


def test_claim_retries_are_persistent_and_do_not_extend_the_deadline(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    first = env.store.redeem(issued.ticket, nonce)
    env.clock.advance(30)
    env.inventory._engine.dispose()
    with independent_store(env) as restarted:
        second = restarted.redeem(issued.ticket, nonce)
        assert second == first
        assert restarted.read(env.server.id).status == "claimed"
        denied(restarted, issued.ticket, token_urlsafe(32))
        assert restarted.redeem(issued.ticket, nonce) == first
        env.clock.advance(CLAIM_RETRY_SECONDS - 30)
        denied(restarted, issued.ticket, nonce)
        assert restarted.read(env.server.id).status == "expired"


def test_unclaimed_ticket_survives_store_restart(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    env.inventory._engine.dispose()
    with independent_store(env) as restarted:
        assert restarted.read(env.server.id).status == "issued"
        configuration = restarted.redeem(issued.ticket, token_urlsafe(32))
        assert configuration.agent_token.get_secret_value() == env.server.agent_token


def test_unclaimed_ticket_expires_at_the_exact_ten_minute_boundary(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    env.clock.advance(TICKET_LIFETIME_SECONDS)
    assert env.store.read(env.server.id).status == "expired"
    denied(env.store, issued.ticket, token_urlsafe(32))
    with env.inventory._session() as session:
        row = session.get(AgentBootstrapTicketModel, str(env.server.id))
        assert row.claim_nonce_hash is None
        assert row.claimed_at is None


def test_late_claim_does_not_extend_the_original_ticket_lifetime(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    env.clock.advance(TICKET_LIFETIME_SECONDS - 1)
    nonce = token_urlsafe(32)
    configuration = env.store.redeem(issued.ticket, nonce)
    assert configuration.expires_at == issued.expires_at
    env.clock.advance(1)
    denied(env.store, issued.ticket, nonce)
    assert env.store.read(env.server.id).status == "expired"


@pytest.mark.parametrize("claim_first", [False, True])
def test_revoke_is_persistent_idempotent_and_preserves_existing_credentials(env, claim_first):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    if claim_first:
        env.store.redeem(issued.ticket, nonce)
    revoked = env.store.revoke(env.server.id)
    assert revoked.status == "revoked"
    assert (revoked.claimed_at is not None) == claim_first
    env.clock.advance(10)
    assert env.store.revoke(env.server.id) == revoked
    with independent_store(env) as restarted:
        assert restarted.read(env.server.id) == revoked
        denied(restarted, issued.ticket, nonce)
    assert env.inventory.authenticate_agent(env.server.agent_token).id == env.server.id


def test_revoking_after_registration_does_not_revoke_the_running_agent(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    configuration = env.store.redeem(issued.ticket, nonce)
    agent, _ = register(env)
    revoked = env.store.revoke(env.server.id)
    assert revoked.status == "revoked"
    assert revoked.agent_registered
    assert revoked.agent_version == agent.agent_version
    assert (
        env.inventory.authenticate_agent(configuration.agent_token.get_secret_value()).id
        == env.server.id
    )
    denied(env.store, issued.ticket, nonce)


@pytest.mark.parametrize("old_state", ["issued", "expired", "revoked"])
def test_reissue_replaces_the_single_row_and_invalidates_previous_tickets(env, old_state):
    old = env.store.issue(env.server.id, CONTROL_URL)
    old_nonce = token_urlsafe(32)
    if old_state == "expired":
        env.clock.advance(TICKET_LIFETIME_SECONDS)
    elif old_state == "revoked":
        env.store.revoke(env.server.id)
    fresh = env.store.issue(env.server.id, "https://new-control.example", "http")
    assert fresh.ticket.get_secret_value() != old.ticket.get_secret_value()
    assert env.store.read(env.server.id).status == "issued"
    assert env.store.read(env.server.id).claimed_at is None
    with env.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(AgentBootstrapTicketModel)) == 1
    denied(env.store, old.ticket, old_nonce)
    configuration = env.store.redeem(fresh.ticket, token_urlsafe(32))
    assert configuration.control_url == "https://new-control.example"
    assert configuration.transport == "http"


@pytest.mark.parametrize("claimed_state", ["claimed", "expired", "revoked", "credential_changed"])
def test_claim_permanently_blocks_reissue_for_the_same_server_identity(env, claimed_state):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    original = env.store.redeem(issued.ticket, nonce)
    if claimed_state == "expired":
        env.clock.advance(CLAIM_RETRY_SECONDS)
    elif claimed_state == "revoked":
        env.store.revoke(env.server.id)
    elif claimed_state == "credential_changed":
        with env.inventory._session() as session:
            session.execute(
                update(ServerModel)
                .where(ServerModel.id == str(env.server.id))
                .values(agent_token=token_urlsafe(32))
            )
            session.commit()
    before = env.store.read(env.server.id)
    with independent_store(env) as restarted:
        with pytest.raises(AgentBootstrapUnavailableError, match="already claimed"):
            restarted.issue(env.server.id, "https://another-control.example", "http")
    assert env.store.read(env.server.id) == before
    assert before.claimed_at is not None
    with env.inventory._session() as session:
        row = session.get(AgentBootstrapTicketModel, str(env.server.id))
        assert row.ticket_hash == sha256(issued.ticket.get_secret_value().encode()).hexdigest()
        assert row.claim_nonce_hash == sha256(nonce.encode()).hexdigest()
        assert row.control_url == CONTROL_URL
    if claimed_state == "claimed":
        assert env.store.redeem(issued.ticket, nonce) == original
    else:
        denied(env.store, issued.ticket, nonce)


@pytest.mark.parametrize("claim_first", [False, True])
def test_changing_the_existing_credential_invalidates_first_and_repeat_redemption(env, claim_first):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    if claim_first:
        env.store.redeem(issued.ticket, nonce)
    replacement = token_urlsafe(32)
    with env.inventory._session() as session:
        session.execute(
            update(ServerModel)
            .where(ServerModel.id == str(env.server.id))
            .values(agent_token=replacement)
        )
        session.commit()
    assert env.store.read(env.server.id).status == "revoked"
    denied(env.store, issued.ticket, nonce)
    if claim_first:
        with pytest.raises(AgentBootstrapUnavailableError, match="already claimed"):
            env.store.issue(env.server.id, CONTROL_URL)
    else:
        fresh = env.store.issue(env.server.id, CONTROL_URL)
        assert (
            env.store.redeem(fresh.ticket, token_urlsafe(32)).agent_token.get_secret_value()
            == replacement
        )


def test_server_deletion_cascades_ticket_and_all_redemptions_fail_closed(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    with env.inventory._session() as session:
        session.execute(delete(ServerModel).where(ServerModel.id == str(env.server.id)))
        session.commit()
        assert session.get(AgentBootstrapTicketModel, str(env.server.id)) is None
    denied(env.store, issued.ticket, token_urlsafe(32))
    with pytest.raises(ServerNotFoundError):
        env.store.read(env.server.id)


@pytest.mark.parametrize("claim_first", [False, True])
def test_registration_blocks_first_and_repeat_redemption_and_further_issue(env, claim_first):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    if claim_first:
        env.store.redeem(issued.ticket, nonce)
    agent, server = register(env)
    state = env.store.read(env.server.id)
    assert state.status == "revoked"
    assert state.agent_registered is True
    assert state.agent_registered_at == agent.registered_at.replace(tzinfo=UTC)
    assert state.agent_last_seen_at == agent.last_seen_at.replace(tzinfo=UTC)
    assert state.agent_version == agent.agent_version
    assert state.server_last_heartbeat == server.last_heartbeat.replace(tzinfo=UTC)
    denied(env.store, issued.ticket, nonce)
    with pytest.raises(AgentBootstrapUnavailableError):
        env.store.issue(env.server.id, CONTROL_URL)


def test_real_agent_registration_without_prior_ticket_is_reported_as_not_issued(env):
    agent, _ = register(env)
    state = env.store.read(env.server.id)
    assert state.status == "not_issued"
    assert state.agent_registered
    assert state.agent_version == agent.agent_version
    with pytest.raises(AgentBootstrapUnavailableError):
        env.store.issue(env.server.id, CONTROL_URL)


def test_heartbeat_without_agent_registration_is_still_not_a_new_server(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    env.inventory.record_heartbeat(AgentHeartbeatRequest(token=env.server.agent_token))
    state = env.store.read(env.server.id)
    assert not state.agent_registered
    assert state.server_last_heartbeat is not None
    assert state.status == "revoked"
    denied(env.store, issued.ticket, token_urlsafe(32))
    with pytest.raises(AgentBootstrapUnavailableError):
        env.store.issue(env.server.id, CONTROL_URL)


@pytest.mark.parametrize("status", [ServerStatus.CONNECTED, ServerStatus.OFFLINE])
def test_nonpending_server_cannot_receive_an_installation_ticket(env, status):
    with env.inventory._session() as session:
        session.execute(
            update(ServerModel)
            .where(ServerModel.id == str(env.server.id))
            .values(status=status.value)
        )
        session.commit()
    with pytest.raises(AgentBootstrapUnavailableError):
        env.store.issue(env.server.id, CONTROL_URL)
    assert env.store.read(env.server.id).status == "not_issued"


def test_agent_row_blocks_issue_even_if_legacy_server_heartbeat_was_cleared(env):
    register(env)
    with env.inventory._session() as session:
        session.execute(
            update(ServerModel)
            .where(ServerModel.id == str(env.server.id))
            .values(status=ServerStatus.PENDING.value, last_heartbeat=None)
        )
        session.commit()
    with pytest.raises(AgentBootstrapUnavailableError):
        env.store.issue(env.server.id, CONTROL_URL)
    assert env.store.read(env.server.id).agent_registered


def test_observed_last_seen_and_version_come_from_agent_record(env):
    register(env)
    observed = datetime(2026, 8, 31, 10, 15, tzinfo=UTC)
    with env.inventory._session() as session:
        session.execute(
            update(AgentModel)
            .where(AgentModel.server_id == str(env.server.id))
            .values(last_seen_at=observed, agent_version="open-node/actual-version")
        )
        session.commit()
    state = env.store.read(env.server.id)
    assert state.agent_last_seen_at == observed
    assert state.agent_version == "open-node/actual-version"


@pytest.mark.parametrize("operation", ["issue", "read", "revoke"])
def test_management_operations_on_unknown_servers_use_inventory_not_found(env, operation):
    with pytest.raises(ServerNotFoundError):
        if operation == "issue":
            env.store.issue(uuid4(), CONTROL_URL)
        else:
            getattr(env.store, operation)(uuid4())


def test_unknown_ticket_and_every_bad_ticket_or_nonce_have_the_same_error(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    nonce = token_urlsafe(32)
    denied(env.store, token_urlsafe(32), nonce)
    bad_values = [
        None,
        42,
        {},
        "",
        "short-secret",
        "a" * 4096,
        "A" * 42 + "B",  # Noncanonical encoding aliases the same 32 bytes as all 'A'.
        "A" * 43 + "=",
        "A" * 42 + "+",
        "A" * 42 + "/",
        "A" * 42 + "\n",
        "\N{LATIN SMALL LETTER E WITH ACUTE}" * 43,
        SecretStr("not-a-nonce"),
    ]
    for value in bad_values:
        denied(env.store, value, nonce)
        denied(env.store, issued.ticket, value)
    assert env.store.read(env.server.id).status == "issued"
    assert env.store.redeem(issued.ticket, nonce).server_id == env.server.id


@pytest.mark.parametrize("same_nonce", [False, True])
def test_claim_is_atomic_across_two_independent_store_instances(env, same_nonce):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    first_nonce = token_urlsafe(32)
    second_nonce = first_nonce if same_nonce else token_urlsafe(32)
    barrier = Barrier(2, timeout=10)

    def claim(store, nonce):
        barrier.wait()
        try:
            return store.redeem(issued.ticket, nonce)
        except AgentBootstrapRedemptionError as error:
            assert str(error) == REDEMPTION_ERROR
            return None

    with independent_store(env) as first_store, independent_store(env) as second_store:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(claim, first_store, first_nonce)
            second = executor.submit(claim, second_store, second_nonce)
            outcomes = [first.result(timeout=15), second.result(timeout=15)]
    if same_nonce:
        assert all(isinstance(result, AgentBootstrapConfig) for result in outcomes)
        assert outcomes[0] == outcomes[1]
    else:
        assert sum(isinstance(result, AgentBootstrapConfig) for result in outcomes) == 1
        winner = first_nonce if outcomes[0] is not None else second_nonce
        loser = second_nonce if outcomes[0] is not None else first_nonce
        assert env.store.redeem(issued.ticket, winner).server_id == env.server.id
        denied(env.store, issued.ticket, loser)
    assert env.store.read(env.server.id).status == "claimed"
    with env.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(AgentBootstrapTicketModel)) == 1


def test_expiry_is_checked_after_waiting_for_the_sqlite_write_lock(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    entered_begin = Event()
    with independent_store(env) as other_store:

        @event.listens_for(other_store.inventory._engine, "before_cursor_execute")
        def before_cursor_execute(_connection, _cursor, statement, _parameters, _context, _many):
            if statement == "BEGIN IMMEDIATE":
                entered_begin.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with env.store._coordinated_session():
                pending = executor.submit(other_store.redeem, issued.ticket, token_urlsafe(32))
                assert entered_begin.wait(timeout=5)
                env.clock.advance(TICKET_LIFETIME_SECONDS)
            with pytest.raises(AgentBootstrapRedemptionError, match=REDEMPTION_ERROR):
                pending.result(timeout=10)
    assert env.store.read(env.server.id).status == "expired"


@pytest.mark.parametrize(
    ("original", "normalized"),
    [
        ("HTTPS://CONTROL.EXAMPLE:443/", "https://control.example"),
        ("https://control.example.:0443/prefix/", CONTROL_URL),
        ("https://control.example:8443/prefix/", "https://control.example:8443/prefix"),
        ("https://[2001:0DB8:0:0::1]:443/prefix/", "https://[2001:db8::1]/prefix"),
        ("https://127.0.0.1:443", "https://127.0.0.1"),
        ("https://localhost:8443", "https://localhost:8443"),
        (
            "https://m\N{LATIN SMALL LETTER U WITH DIAERESIS}nich.example/",
            "https://xn--mnich-kva.example",
        ),
        ("https://control.example/a-b_1.2/", "https://control.example/a-b_1.2"),
    ],
)
def test_configured_https_base_url_is_normalized(original, normalized):
    assert normalize_control_url(original) == normalized


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://control.example",
        "ftp://control.example",
        "//control.example",
        "https://",
        "https:///missing-host",
        "https://user:secret@control.example",
        "https://@control.example",
        "https://control.example?secret=value",
        "https://control.example?",
        "https://control.example#fragment",
        "https://control.example#",
        "https://control.example:invalid",
        "https://control.example:",
        "https://control.example:0",
        "https://control.example:65536",
        "https://control.example:-1",
        "https://control.example:+443",
        "https://control.example:443:443",
        " https://control.example",
        "https://control.example ",
        "https://control.example\n/path",
        "https://control.example/\x00",
        "https://control.example/\x7f",
        "https://control.example\\@evil.example",
        "https://control.example/a b",
        "https://control.example/%",
        "https://control.example/%zz",
        "https://control.example/a%20b",
        "https://control.example/a%2fb",
        "https://control.example/a/../b",
        "https://control.example/a/./b",
        "https://control.example/a//b",
        "https://control.example/\N{LATIN SMALL LETTER E WITH ACUTE}",
        "https://control.example..",
        "https://control..example",
        "https://-control.example",
        "https://control_.example",
        "https://[::not-an-ip]",
        "https://[::1]unexpected:443",
        "https://[::1]unexpected",
        "https://[v1.future]",
        "https://[fe80::1%25eth0]",
        "https://" + "a" * 64 + ".example",
        "https://control.example/" + "a" * 2048,
    ],
)
def test_invalid_control_urls_fail_without_echoing_the_input(url):
    with pytest.raises(ValueError) as error:
        normalize_control_url(url)
    assert str(error.value) == (
        "control_url must be a valid HTTPS base URL without credentials, query, or fragment"
    )
    assert "secret@" not in str(error.value)


@pytest.mark.parametrize("transport", ["pull", "push", "", "AUTO", None, {}])
def test_invalid_transport_cannot_invalidate_an_existing_ticket(env, transport):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    with pytest.raises(ValueError, match="transport must be auto, websocket, or http"):
        env.store.issue(env.server.id, CONTROL_URL, transport)
    assert env.store.read(env.server.id).status == "issued"
    assert env.store.redeem(issued.ticket, token_urlsafe(32)).server_id == env.server.id


def test_invalid_control_url_cannot_invalidate_an_existing_ticket(env):
    issued = env.store.issue(env.server.id, CONTROL_URL)
    with pytest.raises(ValueError):
        env.store.issue(env.server.id, "http://control.example")
    assert env.store.read(env.server.id).status == "issued"
    assert env.store.redeem(issued.ticket, token_urlsafe(32)).server_id == env.server.id
