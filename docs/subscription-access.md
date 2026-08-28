# Subscription Access

Managed subscriptions revoke actual runtime credentials when an account is
disabled, its plan expires, its charged traffic reaches the quota, or its node
leaves the active plan. Existing client configurations stop authenticating;
blocking the subscription download alone is not considered enforcement.
Re-enabling, renewing, increasing the quota or resetting traffic restores the
same credentials on the still-authorized nodes. There is no activation service
or paid entitlement check.

## Enrollment And Operations

Assign a plan with `queue_agent_commands: true` to enroll and provision its
credentials. Plan metadata, access intent and queued commands commit together.
The Subscriptions view labels this action **Apply to nodes (restart Xray)**.
Queued assignments require activation; `no_restart: true` is rejected. A
metadata-only assignment does not enroll new credentials. It can change the
availability of credentials that were already enrolled.

On startup, the controller recognizes historical `batch-apply` commands only
when their inbound, email and credential match a stored subscription credential.
Old unsent batches cannot restore an unavailable user. Previously attempted
batches finish or return their journaled failure before reconciliation proceeds.
Catalog data or a preview without a deployment command is not proof of previous
provisioning. Reassign with node application when importing such a deployment.

- `GET /api/v1/users/{username}/access` reports desired access and confirmed,
  pending or failed state per server, without credential secrets.
- `PATCH /api/v1/users/{username}/active`, with `is_active: false` or `true`,
  updates account availability and schedules reconciliation.
- `POST /api/v1/users/{username}/access/sync` retries or rechecks enrolled access;
  it does not enroll preview-only credentials.
- Existing traffic-reset endpoints also permit recovery. Due monthly user
  resets now run automatically and preserve the current counter baselines.

The controller checks every 10 seconds by default. Set
`OPEN_NODE_SUBSCRIPTION_ACCESS_POLL_SECONDS` between 1 and 300 to change this.
Quota detection also depends on Xray per-user statistics and Agent telemetry.
Enforcement is eventual, not a byte-exact network quota. Offline Agents and
reserved nodes remain pending; controller downtime delays enforcement. Check
the node results rather than treating an account switch as proof of revocation.

## Execution And Recovery

The Agent advertises `subscription_access` and receives
`POST /api/child/subscription-access`. Old Agents must be upgraded. Limited
restoration also requires the [native limiter](native-limits.md). A success
must confirm the exact requested revision and enabled/disabled counts.
Expired unsent commands are superseded; attempted commands are drained before
opposing changes. Leases respect existing change-set reservations. Multiple
controller workers share the database's serialized command transitions.

The Agent checks each credential's protocol, email and authentication identity
before editing. Independently changed credentials cause a visible failure,
not an overwrite. Other runtime users and settings are retained. Routing user
membership remains while authentication is revoked, avoiding empty rules that
could accidentally become catch-all routes; restoration validates its selectors.
If another remaining user shares the revoked secret, the batch fails rather
than falsely claiming that secret can no longer authenticate.

Removing the final user suspends the entire inbound. This avoids protocols that
reject an empty user list or fall back to a shared server password. The private
Agent journal keeps its empty template and position for restoration. Interrupted
suspension is recovered only with matching configuration evidence; independent
host changes require operator review. Keep the Agent state directory in backups.

Each applied access batch restarts a running bound Xray, disconnecting its
current connections, including unrelated users' connections. A stopped runtime
is configured but is not automatically started. Native caps are persisted before
restored credentials become usable. A failed config update can therefore leave
new caps in place. Access changes are not a cross-file atomic transaction.
Failures remain visible and retry after 60 seconds; an explicit sync can retry
sooner. The original Agent command journal still rejects interrupted replay of
the same command ID; reconciliation uses a new guarded request.

## VPS Verification

`scripts/vps/smoke-subscription-access.py` installs a dedicated non-root Agent
and uses actual Mihomo and free Xray clients against 18 protocol variants. It
checks old-credential rejection, existing-stream closure, preservation of
unrelated users, expiry, empty-listener suspension, Agent restart, stable
credential renewal, actual traffic exhaustion and monthly reset recovery.
It also tests the Vue controls at desktop, mobile and 320-pixel widths.
Use `--transport websocket` or `--transport http` with the existing VPS tools.
This access smoke covers managed runtime mode; separately owned systemd runtime
access still needs its own host-specific end-to-end verification.
