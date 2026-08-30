# Plan Editing, Unassignment And Removal

The subscription catalog exposes plan editing/removal and per-user unassignment.
All operations require the local administrator session and CSRF protection.
There is no activation service, license key, paid tier or third-party account.

## Edit

Editing changes the plan name, description, quota, traffic billing factor,
default duration/reset preferences, node membership, node aliases, billing multipliers and
native speed/concurrent-connection limits and automatic speed rules. Per-node limits override plan
defaults; a blank override inherits, and an explicit zero means unlimited.
Direct-parent inheritance and [user overrides](user-limits.md) participate in
the same resolver. User overrides survive edits and take precedence over plan
values; changing a plan quota does not replace a user's explicit quota.
`oneway` applies a package factor of 1 and `twoway` a factor of 2 to the sum of
upload and download. The selected node's billing multiplier is then applied.
The combined weight is frozen for every telemetry delta, so editing either
value never rewrites already charged usage. Multipliers remain visible in the
subscription label as an operator-facing hint.
Quotas must fit the database's byte counter and represent at least one byte.

Existing subscribers keep their assignment dates, reset preferences, tokens,
credentials and charged usage. Duration and reset defaults apply to subsequent
assignments. Quota changes affect current access without resetting usage;
lowering the quota below charged usage withdraws managed access.

For each subscriber, selected nodes receive stable credentials. A subscriber
already enrolled in managed access gets an updated durable access intent:
removed nodes are disabled and added nodes are provisioned with their limits.
Old credentials and bindings remain so a node can be restored without a new
identity. Preview-only subscribers are not automatically enrolled; their
updated subscription and provisioning preview remain available for manual use.

The settings response includes a revision covering editable settings, affected
users and credential bindings. Stale edits return 409. Live telemetry and Agent
confirmation do not invalidate a form, but changed membership does. Settings,
credentials and queued intents commit together; a provisioning conflict rolls
back the edit. Saving requires explicit acceptance of runtime restarts.

## Node Aliases

Create and edit forms provide a `Custom subscription names` switch and an
optional name for each selected node. `node_name_overrides` maps stable node
UUIDs to names; `node_name_override_enabled` defaults to false. Switching off
keeps the saved names, while clearing a name restores the original label.
Aliases do not inherit from a parent node or affect other plans.

Names are trimmed, blank entries are removed, and only selected nodes retain
aliases. Names must be distinct within the alias map, case-sensitive, and at
most 128 Unicode code points without control characters. Collisions with
original node names or reserved client tags receive deterministic numeric
suffixes in the exported subscription.

The alias is applied before the billing multiplier in the shared renderer.
Clash, sing-box, Xray, URI-list, base64 and format previews use the same name;
generated group references point to those final names. Operational inventory,
inbound/outbound resource identities, user credentials, subscription keys and
traffic attribution do not change. An edit that changes only aliases or their
switch does not provision credentials or enqueue Agent commands. Concurrent
node/limit changes retain the ordinary deployment and acknowledgment contract.

Catalog exports use original inventory node names as map keys, remapped to
destination UUIDs during import. Ambiguous alias keys fail with 409 instead of
silently targeting another node; skipped missing nodes produce catalog
warnings. Legacy edits/imports that omit the new fields preserve saved aliases
on retained nodes. Explicit empty maps clear them. Removing a node or server
prunes only the associated entries. SQLite upgrades add the two columns with
empty/disabled defaults and preserve existing plans and credentials.

## Automatic Speed Rules

Create and edit forms support an ordered list of sustained or burst rules.
Each rule specifies a positive trigger Mbps, hold seconds, cap Mbps and cap
duration. Burst rules also specify a window and occurrence count. The editor
supports reordering and removal; invalid values prevent saving. A plan holds
at most 100 rules, durations are 1-86400 seconds, and the burst window must
cover the hold duration. Rates must be finite and at least one byte/second.

`auto_speed_rules` bind only to the plan's subscriber credentials. Two plans
sharing an inbound do not acquire each other's rules. Inbound-wide rules are
preserved and evaluated first, followed by the user's rules in saved order;
the first match applies, without relaxing a stricter static cap. Static user
overrides, including explicit zero, do not remove automatic rules. See
[native limits](native-limits.md) for sampling and expiry semantics.

Saving updates the durable access intent for managed subscribers, with the
ordinary restart acknowledgment and pending/applied status. Tokens, credentials
and exported client configurations remain unchanged. Clearing the list removes
package rules. Rules persist across runtime restarts, but active timers restart.
Both the current Agent and the per-user-rule-capable free core are required;
old components cannot report unenforced rules as applied.

Catalog exports/imports preserve ordered rules. A legacy edit/import that omits
the field preserves existing rules; an explicit empty list clears them. SQLite
upgrades default existing plans to an empty list and the new Agent capability
to false, without changing prior settings.

## Unassign Or Remove

Unassignment requires the exact username; removal requires the exact plan name.
Both also require the current preview revision and runtime acknowledgment.
Preview and cancellation have no side effects. Unassignment retains the plan;
removal unassigns all its subscribers and deletes only that plan.

The transaction clears each affected user's plan, dates and reset preferences,
making their public subscription unavailable. It retains the user, tokens,
credentials and traffic/history. Reassignment can reuse those credentials and
tokens; it does not erase previously charged usage.

All stored credentials, including preview-only credentials that may have been
installed manually, are tracked for withdrawal. Credentials without an inbound
tag are retained with a manual-cleanup warning. Changed runtime identities or
missing referenced servers require review before the operation can proceed.

**A successful API response confirms the local transaction, not remote
revocation.** Offline credentials can still forward until the Agent applies the
withdrawal. The dialog shows per-user, per-server pending/applied/failed states
and offers retry. Existing in-flight commands must settle before the replacement
intent; failed or offline intents persist across controller restarts. Runtime
changes may restart Xray and interrupt other connections on that runtime.
See [subscription access](subscription-access.md) for the reconciliation contract.

## API

- `GET /api/v1/plans/{id}/settings`: plan, subscribers, revision and warnings.
- `PUT /api/v1/plans/{id}/settings`: all editable plan fields,
  `expected_revision` and `acknowledge_runtime_restart: true`.
- `POST /api/v1/plans/{id}/remove`: `expected_revision`, `confirm_name`
  and `acknowledge_runtime_restart: true`.
- `GET /api/v1/users/{username}/plan/removal`: current assignment preview.
- `POST /api/v1/users/{username}/plan/remove`: the same removal fields,
  with the username as `confirm_name`.

The write response includes affected users, tracked commands and warnings.
Editing also returns the saved plan and a reusable revision. No new database
table is required; the existing durable access coordinator stores withdrawal
intent separately from the plan being removed.

## VPS Verification

`backend/tests/test_plan_management.py` covers validation, repeated saves,
stale settings/membership, preserved identities and usage, preview-only
behavior, quota withdrawal, failed retries, in-flight command draining,
restart recovery, concurrent assignment/removal, rollback, authorization and
foreign-key integrity. Frontend tests cover complete settings payloads,
explicit-zero overrides, revision/acknowledgment and error responses.

`scripts/vps/smoke-plan-management.py` uses a dedicated non-root systemd Agent
and the free native-limit Xray build. Three real subscribers exercise browser
editing, node replacement, actual rate/connection caps, cancellation,
unassignment/reactivation and deletion while the Agent is paused. The test
checks continued offline forwarding, later confirmed withdrawal, preservation
of the unrelated subscriber and bounded connection-slot release. It captures
1440, 390 and 320px dialog states. Run with `--transport websocket` and
`--transport http`; both connect through trusted TLS. All tests, builds,
formatting and browser execution run on the configured VPS.

`backend/tests/test_plan_node_aliases.py` covers all five formats, naming
collisions, Unicode validation, unchanged runtime records, plan isolation,
catalog remapping/rollback, legacy-field omission, removal and repeatable
schema upgrades. `scripts/vps/smoke-plan-node-aliases.py` exercises browser
creation/editing, stale edits, toggle/clear, subscriber downloads, actual Xray
forwarding and unchanged runtime PID with both transports at 1440/390/320px.

`backend/tests/test_plan_speed_rules.py` and Agent/native tests cover validation,
rule ordering, credential isolation, capability rejection, persistence, old
payloads, catalog roundtrips and schema upgrades. The VPS
`scripts/vps/smoke-plan-speed-rules.py` checks browser create/edit/reorder/clear,
native-UI preservation, real sustained/burst caps, expiry, an independent
subscriber and runtime restart through both transports at 1440/390/320px.

## Migration Boundaries

This implements the lifecycle of existing Open Node plan fields, including
separate [Clash and Surge template](subscription-templates.md) bindings.
Template-only edits are revision guarded and issue no Agent commands. Actual
Surge application import remains an Apple-platform verification gate.
[User profile editing and removal](user-management.md)
and [node editing/removal](node-management.md) are implemented separately.
These are not license-gated features.
