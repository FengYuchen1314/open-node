# Coordinated Change Sets

The `/changes` workspace and `/api/v1/change-sets` API execute an ordered list
of Agent commands, optionally followed by compensating commands. They use the
same authenticated WebSocket and HTTP transports as ordinary node operations.
There are no license, activation, payment, or node-count requirements.

## Execution

A plan contains a name, `rollback_on_failure`, and `steps`. Each step has a
`server_id`, a label, a forward command and an optional rollback command.
Creating a plan leaves it unexecuted unless `dispatch` is true.

Dispatch reserves every target node in one database transaction. Overlapping
change sets receive HTTP 409 without partially creating commands or acquiring
reservations. Forward commands have persisted dependencies across nodes: a
step becomes eligible only after its predecessor succeeds. HTTP results also
wake the next connected WebSocket node without waiting for its heartbeat.

Already-started ordinary commands and the remaining steps of their dependency
sequences drain before the new change begins. New unrelated commands remain
queued while their node is reserved. This coordinates Open Node's own command
queue; it cannot prevent an operator or another program from changing a host
directly. SQLite is the verified persistence backend, with serialized lease,
result, dispatch and rollback transitions.

## Failure and Rollback

On a forward failure, unattempted successors are skipped. With
`rollback_on_failure: true`, compensation starts automatically; otherwise the
change stops in `failed` and retains its reservations.

Rollback of an undispatched plan cancels it without sending commands. Rollback
of an active change first stops unsent work and waits for every attempted
forward command to return. A lease expiry is not proof that the Agent stopped:
the original command ID is retried through the Agent's persistent journal.
An offline Agent can therefore keep a change blocked until it returns.

Compensation runs in reverse step order, with the same dependency guarantees.
Only attempted forward steps are eligible. Failed forward commands are
included because an error may follow a partial mutation. Supply compensators
that are safe after both successful and partially failed execution; these are
explicit commands, not automatically derived snapshots. Missing compensators
produce `rollback_incomplete`, not a claim that all changes were undone.

A failed compensator stops its successors and retains all node reservations.
`Retry rollback` creates new command IDs for failed or skipped compensation,
preserves prior results in `rollback_history`, and does not repeat successful
compensators. Inspect the failure before retrying.

After a successful change releases its reservations, a late rollback is
rejected if a node has received later potentially mutating work, including a
pending mutation in an already-started sequence. Prepare a new recovery plan
from the current state instead. The small read-only allowlist covers GETs for
Xray/nginx config, system information, traffic and speed.

## States and Operator Resolution

| State | Meaning |
| --- | --- |
| `planned` | No commands sent; dispatch or cancel is available. |
| `dispatched` | Ordered execution is active; targets are reserved. |
| `succeeded` | Every forward step succeeded; reservations released. |
| `failed` | Forward execution stopped with automatic rollback disabled. |
| `rollback_queued` | Waiting for attempted work or executing compensation. |
| `rolled_back` | Every required compensator succeeded; reservations released. |
| `rollback_failed` | A compensator failed; inspection and retry are available. |
| `rollback_incomplete` | Executed steps lack compensators; review is required. |
| `cancelled` | Undispatched plan cancelled without Agent commands. |
| `needs_review` | Legacy execution paused during upgrade. |
| `accepted` | Operator explicitly accepted the current state and released reservations. |

The API exposes `held_server_ids`, `blocking_command_ids`, persistent warnings,
rollback reasons and resolution reasons. The UI refreshes active changes and
shows forward results, compensation results and retry history.

For `failed`, `rollback_failed`, `rollback_incomplete`, or `needs_review`, an
operator can inspect the nodes and explicitly accept the current state:

```http
POST /api/v1/change-sets/{id}/accept
Content-Type: application/json

{"acknowledge": true, "reason": "Verified the remaining configuration on both nodes"}
```

Normal operator authentication and CSRF protection apply. A nonblank reason
and explicit acknowledgment are required. Acceptance is rejected while any
attempted command belonging to this change is unresolved. Acceptance does not
undo anything or label a partial change as rolled back; it records the reason
and releases reservations. An accepted change cannot be dispatched or rolled
back again.

## Upgrade From Earlier Open Node Builds

Back up the persistent volume before upgrading. SQLite schema migration adds
coordination metadata and preserves command IDs, attempts, payloads and results.
Unexecuted legacy plans become normal coordinated plans. Legacy `dispatched`
and `rollback_queued` records become `needs_review`; unsent commands are stopped
and attempted commands remain eligible to return their real outcomes.

Legacy target nodes reject new changes until review is complete. Do not infer
success from the old status, which tracked queuing rather than completion.
Inspect the Agent results and host state, wait for outstanding attempts, then
accept the state with a reason. Build a fresh recovery plan when changes are
still needed. Repeated application startup does not manufacture completion or
automatically replay legacy compensation.

Older three-state builds cannot read the new execution statuses. Downgrading
requires the pre-upgrade database backup; do not attach an old image directly
to an upgraded database. Stop new dispatches and settle outstanding Agent
work before recovery. A database restore does not undo host-side changes.

See [the VPS verification procedure](testing.md#multi-node-change-set-smoke)
and [deployment backup and restore](deployment.md).
