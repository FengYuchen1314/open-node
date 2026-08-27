import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from open_node.domain.changes import AgentChangeSetRollbackRequest
from open_node.domain.inventory import (
    AgentCapabilities,
    AgentCommandCreate,
    AgentCommandResultRequest,
)
from open_node.services.inventory import AgentChangeSetModel, AgentChangeSetStepModel, CommandModel
from sqlalchemy import select, update
from test_change_sets import make_client


@pytest.fixture
def setup(tmp_path):
    client = make_client(tmp_path)
    edges = [
        client.post("/api/v1/servers", json={"name": name}).json() for name in ("edge-a", "edge-b")
    ]
    return client, edges


def plan(client, edges, *, auto=True, rollback=True):
    response = client.post(
        "/api/v1/change-sets",
        json={
            "name": "Ordered rollout",
            "rollback_on_failure": auto,
            "steps": [
                {
                    "server_id": edge["server"]["id"],
                    "label": f"Step {index}",
                    "forward": {
                        "method": "POST",
                        "path": "/api/child/outbounds",
                        "body": {"action": "add", "tag": str(index)},
                    },
                    "rollback": {
                        "method": "POST",
                        "path": "/api/child/outbounds",
                        "body": {"action": "remove", "tag": str(index)},
                    }
                    if rollback
                    else None,
                }
                for index, edge in enumerate(edges)
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["change_set"]["id"]


def state(client, identifier):
    return client.get(f"/api/v1/change-sets/{identifier}").json()["change_set"]


def lease(client, edge):
    response = client.post(
        "/api/v1/agents/commands/lease", json={"token": edge["agent_token"], "max_commands": 10}
    )
    assert response.status_code == 200, response.text
    return response.json()["commands"]


def result(client, edge, command, success=True):
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": edge["agent_token"],
            "status": 200,
            "body": {"success": success},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["command"]


def dispatch(client, identifier):
    response = client.post(f"/api/v1/change-sets/{identifier}/dispatch")
    assert response.status_code == 200, response.text
    return response.json()["commands"]


def rollback(client, identifier):
    response = client.post(
        f"/api/v1/change-sets/{identifier}/rollback", json={"reason": "test rollback"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def standalone(client, edge):
    return client.post(
        f"/api/v1/servers/{edge['server']['id']}/commands",
        json={
            "method": "POST",
            "path": "/api/child/outbounds",
            "body": {"action": "add", "tag": "standalone"},
        },
    ).json()["command"]


def test_failure_stops_later_nodes_and_compensates_in_reverse_order(setup):
    client, edges = setup
    identifier = plan(client, [*edges, edges[0]])
    commands = dispatch(client, identifier)
    assert [c["status"] for c in commands] == ["pending", "waiting", "waiting"]
    assert lease(client, edges[1]) == []
    assert lease(client, edges[0])[0]["id"] == commands[0]["id"]
    result(client, edges[0], commands[0])
    assert lease(client, edges[1])[0]["id"] == commands[1]["id"]
    result(client, edges[1], commands[1], False)
    current = state(client, identifier)
    assert current["status"] == "rollback_queued"
    assert current["steps"][2]["forward_command"]["status"] == "skipped"
    assert current["steps"][2]["rollback_command"] is None
    assert lease(client, edges[0]) == []
    reverse_b = lease(client, edges[1])[0]
    assert reverse_b["body"]["action"] == "remove"
    result(client, edges[1], reverse_b)
    reverse_a = lease(client, edges[0])[0]
    assert reverse_a["body"]["action"] == "remove"
    result(client, edges[0], reverse_a)
    assert state(client, identifier)["status"] == "rolled_back"
    assert state(client, identifier)["held_server_ids"] == []


def test_manual_rollback_waits_for_inflight_even_after_lease_expiry(setup, tmp_path):
    client, edges = setup
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    lease(client, edges[0])
    queued = rollback(client, identifier)
    assert queued["commands"] == []
    assert queued["change_set"]["blocking_command_ids"] == [commands[0]["id"]]
    assert lease(client, edges[1]) == []
    with client.app.state.inventory._session() as session:
        session.execute(
            update(CommandModel)
            .where(CommandModel.id == commands[0]["id"])
            .values(
                leased_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        session.commit()
    client = make_client(tmp_path)
    assert lease(client, edges[0])[0]["id"] == commands[0]["id"]
    assert all(step["rollback_command"] is None for step in state(client, identifier)["steps"])
    result(client, edges[0], commands[0])
    compensation = lease(client, edges[0])[0]
    assert compensation["id"] != commands[0]["id"] and compensation["body"]["action"] == "remove"
    result(client, edges[0], compensation)
    assert state(client, identifier)["status"] == "rolled_back"


def test_reservations_drain_old_work_and_hold_new_work(setup):
    client, edges = setup
    old = standalone(client, edges[1])
    assert lease(client, edges[1])[0]["id"] == old["id"]
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    assert lease(client, edges[0]) == []
    assert state(client, identifier)["blocking_command_ids"] == [old["id"]]
    new = standalone(client, edges[0])
    overlapping = plan(client, edges)
    assert client.post(f"/api/v1/change-sets/{overlapping}/dispatch").status_code == 409
    assert all(step["forward_command"] is None for step in state(client, overlapping)["steps"])
    result(client, edges[1], old)
    assert [c["id"] for c in lease(client, edges[0])] == [commands[0]["id"]]
    result(client, edges[0], commands[0])
    assert [c["id"] for c in lease(client, edges[1])] == [commands[1]["id"]]
    result(client, edges[1], commands[1])
    assert state(client, identifier)["status"] == "succeeded"
    assert new["id"] in {c["id"] for c in lease(client, edges[0])}
    assert client.post(f"/api/v1/change-sets/{identifier}/rollback").status_code == 409
    assert state(client, identifier)["held_server_ids"] == []


def test_failed_compensation_can_retry_without_losing_history(setup):
    client, edges = setup
    identifier = plan(client, edges)
    forwards = dispatch(client, identifier)
    for edge, forward in zip(edges, forwards, strict=True):
        lease(client, edge)
        result(client, edge, forward)
    rollback(client, identifier)
    failed = lease(client, edges[1])[0]
    result(client, edges[1], failed, False)
    assert state(client, identifier)["status"] == "rollback_failed"
    assert lease(client, edges[0]) == []
    retry = rollback(client, identifier)
    assert len(retry["commands"]) == 2
    assert retry["commands"][0]["id"] != failed["id"]
    assert retry["change_set"]["steps"][1]["rollback_history"][0]["id"] == failed["id"]
    for edge in reversed(edges):
        result(client, edge, lease(client, edge)[0])
    assert state(client, identifier)["status"] == "rolled_back"
    assert rollback(client, identifier)["commands"] == []


@pytest.mark.parametrize(
    "auto,has_rollback,expected", [(False, True, "failed"), (True, False, "rollback_incomplete")]
)
def test_partial_state_requires_explicit_acceptance(setup, auto, has_rollback, expected):
    client, edges = setup
    identifier = plan(client, edges, auto=auto, rollback=has_rollback)
    commands = dispatch(client, identifier)
    lease(client, edges[0])
    result(client, edges[0], commands[0], False)
    current = state(client, identifier)
    assert current["status"] == expected and len(current["held_server_ids"]) == 2
    path = f"/api/v1/change-sets/{identifier}/accept"
    assert client.post(path, json={"reason": "checked"}).status_code == 422
    assert client.post(path, json={"acknowledge": True, "reason": "  "}).status_code == 422
    accepted = client.post(path, json={"acknowledge": True, "reason": "inspected node state"})
    assert accepted.status_code == 200
    assert accepted.json()["change_set"]["status"] == "accepted"
    assert accepted.json()["change_set"]["held_server_ids"] == []
    assert accepted.json()["change_set"]["resolution_reason"] == "inspected node state"
    assert client.post(f"/api/v1/change-sets/{identifier}/dispatch").status_code == 409


def test_unleased_and_duplicate_results_cannot_advance_or_reverse_outcomes(setup):
    client, edges = setup
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    for edge, command in zip(edges, commands, strict=True):
        response = client.post(
            f"/api/v1/agents/commands/{command['id']}/result",
            json={
                "token": edge["agent_token"],
                "status": 200,
            },
        )
        assert response.status_code == 409
    lease(client, edges[0])
    result(client, edges[0], commands[0], False)
    result(client, edges[0], commands[0], True)
    assert state(client, identifier)["steps"][0]["forward_command"]["status"] == "failed"
    assert lease(client, edges[1]) == []


def test_concurrent_dispatch_and_rollback_are_idempotent(setup):
    client, edges = setup
    identifier = UUID(plan(client, edges))
    store = client.app.state.inventory
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=5)
        return store.dispatch_change_set(identifier)[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sorted(len(commands) for commands in results) == [0, 2]
    for step, edge in zip(state(client, identifier)["steps"], edges, strict=True):
        lease(client, edge)
        result(client, edge, step["forward_command"])

    def undo():
        barrier.wait(timeout=5)
        return store.rollback_change_set(identifier, AgentChangeSetRollbackRequest())[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: undo(), range(2)))
    assert sorted(len(commands) for commands in results) == [0, 2]


def test_legacy_work_is_paused_without_fabricating_completion(setup, tmp_path):
    client, edges = setup
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    lease(client, edges[0])
    with client.app.state.inventory._session() as session:
        session.execute(
            update(AgentChangeSetModel)
            .where(AgentChangeSetModel.id == identifier)
            .values(coordination_version=0)
        )
        session.commit()
    client = make_client(tmp_path)
    current = state(client, identifier)
    assert current["status"] == "needs_review"
    assert current["steps"][0]["forward_command"]["status"] == "leased"
    assert current["steps"][1]["forward_command"]["status"] == "skipped"
    assert client.post(f"/api/v1/change-sets/{identifier}/rollback").status_code == 409
    path = f"/api/v1/change-sets/{identifier}/accept"
    assert client.post(path, json={"acknowledge": True, "reason": "checked"}).status_code == 409
    result(client, edges[0], commands[0])
    assert client.post(path, json={"acknowledge": True, "reason": "checked"}).status_code == 200
    with client.app.state.inventory._session() as session:
        assert session.scalar(
            select(AgentChangeSetStepModel).where(
                AgentChangeSetStepModel.change_set_id == identifier
            )
        )


def test_rollback_and_command_claim_race_cannot_dispatch_both_directions(setup):
    client, edges = setup
    identifier = UUID(plan(client, edges))
    store = client.app.state.inventory
    commands = store.dispatch_change_set(identifier)[1]
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return store.lease_command_for_push(commands[0].id)

    def undo():
        barrier.wait(timeout=5)
        return store.rollback_change_set(identifier, AgentChangeSetRollbackRequest())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(claim), pool.submit(undo)
        claimed, rolled = first.result(timeout=10), second.result(timeout=10)
    assert rolled[1] == []
    current = state(client, identifier)
    if claimed:
        assert current["status"] == "rollback_queued"
        store.complete_command(
            claimed.id, AgentCommandResultRequest(token=edges[0]["agent_token"], status=200)
        )
        assert state(client, identifier)["steps"][0]["rollback_command"] is not None
    else:
        assert current["status"] == "rolled_back"


def test_http_result_wakes_another_websocket_node_without_its_heartbeat(setup):
    client, edges = setup

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    target = Socket()
    client.app.state.agent_connections.register(
        UUID(edges[1]["server"]["id"]),
        target,
        AgentCapabilities(rpc=True),
    )
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    assert target.messages == []
    lease(client, edges[0])
    result(client, edges[0], commands[0])
    assert len(target.messages) == 1
    assert target.messages[0]["payload"]["request_id"] == commands[1]["request_id"]


def test_reservation_drains_the_rest_of_an_already_started_dependency_chain(setup):
    client, edges = setup
    store = client.app.state.inventory
    old = store.create_command_sequence(
        UUID(edges[1]["server"]["id"]),
        [
            AgentCommandCreate(method="POST", path="/api/child/outbounds", body={"tag": str(i)})
            for i in range(3)
        ],
    )
    assert lease(client, edges[1])[0]["id"] == str(old[0].id)
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    for index, command in enumerate(old):
        assert lease(client, edges[0]) == []
        result(client, edges[1], {"id": str(command.id)})
        if index < 2:
            assert lease(client, edges[1])[0]["id"] == str(old[index + 1].id)
    assert lease(client, edges[0])[0]["id"] == commands[0]["id"]


def test_retry_does_not_repeat_successful_compensation(setup):
    client, edges = setup
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    for edge, command in zip(edges, commands, strict=True):
        lease(client, edge)
        result(client, edge, command)
    rollback(client, identifier)
    restored = lease(client, edges[1])[0]
    result(client, edges[1], restored)
    failed = lease(client, edges[0])[0]
    result(client, edges[0], failed, False)
    retried = rollback(client, identifier)
    assert len(retried["commands"]) == 1
    assert retried["commands"][0]["server_id"] == edges[0]["server"]["id"]
    result(client, edges[0], lease(client, edges[0])[0])
    final = state(client, identifier)
    assert final["status"] == "rolled_back"
    assert final["steps"][1]["rollback_command"]["id"] == restored["id"]
    assert final["steps"][1]["rollback_command"]["attempts"] == 1


@pytest.mark.parametrize("old_status", ["dispatched", "rollback_queued"])
def test_missing_column_upgrade_preserves_legacy_execution(setup, tmp_path, old_status):
    client, edges = setup
    identifier = plan(client, edges)
    planned = plan(client, edges)
    commands = dispatch(client, identifier)
    if old_status == "rollback_queued":
        for edge, command in zip(edges, commands, strict=True):
            lease(client, edge)
            result(client, edge, command)
        commands = rollback(client, identifier)["commands"]
        lease(client, edges[1])
    else:
        lease(client, edges[0])
    store = client.app.state.inventory
    store._engine.dispose()
    with sqlite3.connect(tmp_path / "open-node-test.db") as database:
        database.execute("DROP TABLE change_set_server_locks")
        database.execute("ALTER TABLE agent_change_sets DROP COLUMN resolution_reason")
        database.execute("ALTER TABLE agent_change_sets DROP COLUMN coordination_version")
        database.execute("ALTER TABLE agent_change_set_steps DROP COLUMN rollback_history_ids")
        database.execute("UPDATE agent_commands SET depends_on_command_id = NULL")
        database.execute("UPDATE agent_commands SET status = 'pending' WHERE status = 'waiting'")
    client = make_client(tmp_path)
    current = state(client, identifier)
    assert current["status"] == "needs_review"
    assert set(current["held_server_ids"]) == {edge["server"]["id"] for edge in edges}
    assert current["resolution_reason"] == ""
    own_commands = [
        step[key]
        for step in current["steps"]
        for key in ("forward_command", "rollback_command")
        if step[key]
    ]
    preserved = {command["id"]: command for command in own_commands}
    assert preserved[commands[0]["id"]]["status"] == "leased"
    assert preserved[commands[1]["id"]]["status"] == "skipped"
    assert all(step["rollback_history"] == [] for step in current["steps"])
    assert lease(client, edges[0]) == []
    assert lease(client, edges[1]) == []
    assert client.post(f"/api/v1/change-sets/{planned}/dispatch").status_code == 409
    path = f"/api/v1/change-sets/{identifier}/accept"
    assert client.post(path, json={"acknowledge": True, "reason": "checked"}).status_code == 409
    result(client, edges[1] if old_status == "rollback_queued" else edges[0], commands[0])
    client = make_client(tmp_path)
    assert state(client, identifier)["status"] == "needs_review"
    assert state(client, identifier)["blocking_command_ids"] == []
    assert (
        client.post(path, json={"acknowledge": True, "reason": "verified legacy state"}).status_code
        == 200
    )
    assert len(dispatch(client, planned)) == 2


def test_late_rollback_rejects_mutating_descendant_of_started_read(setup):
    client, edges = setup
    identifier = plan(client, edges)
    for edge, command in zip(edges, dispatch(client, identifier), strict=True):
        lease(client, edge)
        result(client, edge, command)
    store = client.app.state.inventory
    store.create_command_sequence(
        UUID(edges[0]["server"]["id"]),
        [
            AgentCommandCreate(method="GET", path="/api/child/xray/config"),
            AgentCommandCreate(method="POST", path="/api/child/outbounds", body={"tag": "later"}),
        ],
    )
    read = lease(client, edges[0])[0]
    result(client, edges[0], read)
    assert client.post(f"/api/v1/change-sets/{identifier}/rollback").status_code == 409
    assert state(client, identifier)["held_server_ids"] == []


def test_concurrent_overlapping_dispatch_has_one_reservation_owner(setup):
    from open_node.services.change_sets import ChangeSetConflict

    client, edges = setup
    identifiers = [plan(client, edges), plan(client, list(reversed(edges)))]
    barrier = Barrier(2)

    def start(identifier):
        barrier.wait(timeout=5)
        try:
            client.app.state.inventory.dispatch_change_set(UUID(identifier))
            return True
        except ChangeSetConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(start, identifiers)) == [False, True]
    states = [state(client, identifier) for identifier in identifiers]
    assert sorted(item["status"] for item in states) == ["dispatched", "planned"]
    pending = next(item for item in states if item["status"] == "planned")
    assert not pending["held_server_ids"]
    assert all(step["forward_command"] is None for step in pending["steps"])


def test_legacy_review_does_not_strand_an_earlier_foreign_sequence(setup, tmp_path):
    client, edges = setup
    identifier = plan(client, edges)
    commands = dispatch(client, identifier)
    lease(client, edges[0])
    store = client.app.state.inventory
    prior = store.create_command_sequence(
        UUID(edges[1]["server"]["id"]),
        [
            AgentCommandCreate(method="POST", path="/api/child/outbounds", body={"tag": str(i)})
            for i in range(2)
        ],
    )
    # Earlier builds allowed change sets and ordinary sequences to run together.
    with store._session() as session:
        session.execute(
            update(AgentChangeSetModel)
            .where(
                AgentChangeSetModel.id == identifier,
            )
            .values(coordination_version=0)
        )
        session.execute(
            update(CommandModel)
            .where(CommandModel.id == str(prior[0].id))
            .values(
                status="leased",
                attempts=1,
                leased_at=datetime.now(UTC),
            )
        )
        session.commit()
    client = make_client(tmp_path)
    result(client, edges[0], commands[0])
    result(client, edges[1], {"id": str(prior[0].id)})
    assert lease(client, edges[1])[0]["id"] == str(prior[1].id)
    result(client, edges[1], {"id": str(prior[1].id)})
    assert state(client, identifier)["blocking_command_ids"] == []
    assert state(client, identifier)["status"] == "needs_review"
