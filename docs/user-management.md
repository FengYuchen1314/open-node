# User Editing And Removal

The subscription catalog has user edit and removal actions. These operate on
product subscribers, not the separate control-plane administrator account.
All endpoints require administrator authentication and CSRF protection.
There is no activation, license check or paid feature gate.

## Edit And Disable

Editing changes the display name, contact email, remark and active state.
The username and product role are immutable in this editor. Administrator-role
product users cannot be disabled or removed. Editing their other metadata does
not change the controller administrator or its login credentials.

Edits retain subscription tokens, credential identities, assignment dates,
reset preferences and charged usage. A revision guards concurrent profile,
assignment and credential changes; stale submissions return 409. Live Agent
confirmation and telemetry do not invalidate an edit. Remarks also round-trip
through catalog export/import.

Disabling makes public subscriptions unavailable and tracks withdrawal of all
stored managed credentials, including preview-only credentials that may have
been installed manually. Re-enabling uses the same identities, but does not
bypass an expired plan, exhausted quota or removed node. Credentials without a
managed inbound require manual remote cleanup; the editor displays a warning.
Changing metadata alone does not enroll preview-only subscribers.

## Remove

The removal preview shows stored credentials, warnings and blockers. Confirmation
requires the exact username, the current revision and acknowledgment of runtime
restarts. Unmanaged credentials require a separate explicit acknowledgment of
manual cleanup responsibility. Shared credential labels block removal or
disablement until their ownership is resolved. Cancellation before confirmation
does nothing; confirmed removal cannot be cancelled.

Removal is a durable two-phase operation:

1. Mark the user as removing, disable access, clear the assignment/reset schedule
   and invalidate subscription links. Preserve the credential catalog and
   withdrawal intents while remote work is pending.
2. After all managed withdrawals are confirmed, delete the product user,
   subscription-token record, credential catalog, access rows and per-user
   current/archived traffic ledgers. Plans, shared nodes and other users remain.

A 202 response means the operation was accepted, not that remote access has
stopped. An offline runtime can continue forwarding until its Agent applies
the withdrawal. The dialog shows pending/failed/completed state and per-server
confirmation, supports retry, and can be reopened from the pending catalog row.
The worker resumes unfinished removals after backend restart.

Users pending removal cannot be edited, re-enabled, assigned a plan, issued new
subscription links or overwritten through catalog import. The removal marker
also prevents access if an older writer changes active/plan fields. A server
with pending user withdrawals cannot be removed from the controller.

Existing runtime-changing work must settle before final cleanup. Started work
is allowed to finish, then withdrawal is reconfirmed. Unsent commands containing
retired credential identities are skipped, including structured config payloads
and JSON-encoded config bodies. The removal receipt retains authentication
fingerprints rather than raw credential secrets for these checks.

Recreating the same username after completion generates fresh credentials and
a new traffic label namespace, so old counters are not charged to the new user.
Old subscription tokens remain invalid. Catalog imports cannot restore retired
credentials or reuse their traffic labels.

## Retained History

Removal is not a whole-system erasure operation. Minimal removal receipts,
credential fingerprints and prior command/change history remain. Historical
command/config bodies, shared telemetry and backups can still contain old
credential material or labels. Server-wide traffic totals are unchanged.

The restoration guard examines controller command payloads. It does not inspect
arbitrary files on a remote host or prevent direct host edits. Review old remote
snapshots and backups before restoring them; a full database restore also
restores the deletion and revocation state from that backup.

## API And Upgrade

- `GET /api/v1/users/{username}/settings` and `GET .../removal`: profile,
  revision, access state, credential count, blockers and warnings.
- `PUT /api/v1/users/{username}/settings`: display name, email, remark,
  active state, expected revision and runtime acknowledgment.
- `POST /api/v1/users/{username}/remove`: expected revision, exact confirmation
  name, runtime acknowledgment and optional unmanaged-cleanup acknowledgment.
- `GET /api/v1/user-removals/{id}`: durable operation status.
- `POST /api/v1/user-removals/{id}/retry`: retry outstanding managed withdrawal.

SQLite startup adds `product_users.remark`, `product_users.removal_id` and
`product_user_removals`. Existing profiles and credentials remain unchanged.
Back up before upgrading. Older application versions do not enforce removal
markers or retired fingerprints; rollback needs the corresponding data backup
and review of any already-applied remote changes.

## Verification And Remaining Migration

The VPS suite covers profile validation/revision guards, identity preservation,
disable/re-enable, preview credentials, quota/expiry behavior, durable removal,
restart/retry, old-command replay, opaque runtime-work draining, concurrent API
guards, import rollback, same-name recreation, database upgrade, authorization,
server-removal blocking and foreign-key integrity.

`scripts/vps/smoke-user-management.py` installs an isolated non-root Agent and
uses actual VLESS subscriptions through the free Xray runtime. Browser tests
cover profile edits, stale forms, cancellation, disable/reactivation, removal
with a paused Agent, progress reopening and same-name recreation at 1440, 390
and 320px widths. Run separately over WebSocket and HTTP polling with trusted TLS.
All tests, formatting, builds and browser execution run on the configured VPS.

This completes the existing subscriber profile/lifecycle surface, not every
original account feature. Per-user quota/speed/node overrides, custom short-code
editing, subscriber login/password/session/TOTP workflows and privately owned
routed-node cleanup still require migration or dedicated parity verification.
Node editing/removal and the remaining plan template/policy features remain
separate work. See [migration-map.md](migration-map.md).
