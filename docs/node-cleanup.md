# Native Node Resource Cleanup

The independent Agent advertises `node_cleanup` for revision-guarded Xray
resource removal. This is the remote operation needed by the managed-node
lifecycle, not a completed catalog deletion workflow. It has no license gate.

## Contract

Send an authenticated administrator command to the existing per-server command
queue, with `method: POST`, `path: /api/child/node-cleanup` and a JSON body:

- `action: preview`, with `inbound_tags` and/or `outbound_tags`, returns an
  opaque revision and a secret-free impact summary without changing the host.
- `action: apply` requires the same targets, `expected_revision`, a canonical
  UUID `operation_id`, and `acknowledge_runtime_restart: true`.
- `action: status` requires `operation_id` and returns the durable receipt.

The revision covers the entire current Xray configuration, privately suspended
inbounds, limiter policy document and selected targets. Changes after preview
require another preview. Old Agents cannot lease this operation. The controller
requires a leased command and a matching applied identity/revision before
accepting an apply result; previews do not trigger configuration reconciliation.

Selected inbounds and their suspended templates and native limiter policies are
removed together. Selected outbounds include transitive `proxySettings.tag` and
`streamSettings.sockopt.dialerProxy` dependents. Rules targeting deleted outbounds
are removed. Shared inbound-tag rules retain their other tags and conditions;
a rule whose inbound selection becomes empty is removed, not widened globally.
The preview reports whether the default outbound changes. API listener removal,
duplicate tags, unnamed dependent outbounds and referenced balancers require
explicit prior resolution. At least one existing outbound must be retained.

Removing a remote inbound removes all its clients. The caller must determine
catalog ownership and shared-inbound siblings before selecting it. Selecting a
tag alone does not prove exclusive ownership. This primitive does not edit
catalog rows, plans, traffic ledgers or user assignments.

## Recovery

An accepted apply persists a private prepared record before changing host state.
It writes and activates the candidate configuration before retiring the selected
limiter policies, so a failed initial restart can restore the old clients with
their old caps. It then atomically removes suspended templates and completes
the receipt. A running Xray is restarted; an intentionally stopped Xray is not
started. Retiring native policies can require a second restart. Successful retries
with the same operation identity do not reapply the
operation. Reusing an identity for different content is rejected.

After interruption, recovery accepts only the recorded old or intended state.
Unrelated host edits block recovery and further Agent runtime mutations rather
than being overwritten. Config reads and cleanup status remain available for
inspection. The runtime monitor recovers before auto-start, and mutating commands
and lifecycle work recover before proceeding. A prepared operation may still
have its old clients forwarding; only `applied: true` confirms completion.

The prepared record temporarily contains private configuration for recovery.
Completion removes those snapshots from the receipt, retaining its public impact
and identity. Existing command history, other snapshots and backups are separate
retained history. This is not whole-host data erasure or protection against direct
host edits and independent backup restoration.

## Verification And Remaining Work

The VPS unit suites cover read-only previews, stale revisions, exact receipts,
capability upgrades, input validation, shared routing, chained outbounds, limiter
cleanup, suspended-template removal and interruptions on either side of the
runtime write. `scripts/vps/smoke-node-cleanup.py` exercises real VLESS traffic
through a non-root installed Agent, trusted TLS, a paused Agent and a process kill
after the runtime update, separately over WebSocket and HTTP polling. A forced
restart failure also verifies that the restored client still forwards with its
previous bandwidth cap before recovery resumes.

Managed-node profile editing, catalog removal jobs, parent/owner relationships,
shared-inbound ownership checks, cross-server target discovery, Nginx/tunnel
cleanup and the Vue node-management dialog remain to be connected and verified.
These must not be represented as completed by the low-level operation above.
