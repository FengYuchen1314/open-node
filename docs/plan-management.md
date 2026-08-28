# Plan Editing, Unassignment And Removal

The subscription catalog exposes plan editing/removal and per-user unassignment.
All operations require the local administrator session and CSRF protection.
There is no activation service, license key, paid tier or third-party account.

## Edit

Editing changes the plan name, description, quota, counted traffic directions,
default duration/reset preferences, node membership, display multipliers and
native speed/concurrent-connection limits. Per-node limits override plan
defaults; a blank override inherits, and an explicit zero means unlimited.
Display multipliers affect the subscription label, not charged traffic.
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

## Migration Boundaries

This implements the lifecycle of existing Open Node plan fields, not every
original package feature. Per-plan node name aliases, automatic speed-rule
binding and custom Clash/Surge template selection still need migration and
real-client verification. [User profile editing and removal](user-management.md)
are implemented separately; ordinary node editing/removal remains product work.
These are not license-gated features.
