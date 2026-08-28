# Server Editing And Removal

The dashboard's server row has edit and removal actions. These are local
administrator operations with CSRF protection, without activation, licensing
or a third-party account.

## Edit

Editing changes the display name, advertised IPv4/IPv6 addresses and domains,
and stored IPv6 preference. Names are unique. Addresses must match their IP
family; domains accept hostnames only and normalize IDNA, case and a trailing
dot. Empty address fields clear that override.

The default subscription host follows the existing domain, IPv4, IPv6-domain,
IPv6, then server-name precedence. With address synchronization enabled, only
node `config.server` and `client_template.server` values equal to the previous
default host are replaced. Custom hosts, TLS SNI, keys and user credentials
are unchanged. Template placeholders keep their existing rendering behavior.

The server ID and Agent token do not change. Editing does not restart services,
alter host network settings, change Agent connection/listen mode, or modify a
remote Xray configuration. The IPv6 field is metadata, not an OS command.
Subsequent Agent registration or heartbeat address reports can replace stored
IP addresses; use a domain when a stable advertised endpoint is required.

Settings reads include a revision. Saving stale settings returns 409; reload
and review the current values before retrying. Heartbeat timestamps alone do
not change that revision.

## Remove

Removal is a control-plane operation, including for an offline server. **It
does not uninstall or stop the remote Agent, Xray or Nginx, and does not revoke
credentials already installed in a remote runtime.** Stop/revoke/uninstall
through the existing operations and confirm their completion first when access
must cease. [Agent lifecycle](agent-lifecycle.md) and host-side uninstall remain
separate explicit actions. Keep host access if removing an offline server.

The confirmation displays affected nodes, plans, command records, telemetry,
users, change sets and certificates. It requires the exact current server name
and acknowledgment of the remote-runtime behavior. No side effects occur on
preview or cancellation. A changed impact revision returns 409 and requires a
new preview and confirmation.

Removal performs one serialized database transaction:

- Deletes the server, Agent registration, nodes, credentials, command/frame
  records, probe tasks, snapshots and server traffic records.
- Removes those node IDs and per-node overrides from affected plans. Plans,
  users, subscription tokens and unrelated servers remain. A plan left with
  no usable nodes returns the existing 404 unavailable-subscription response.
- Retains already-counted user upload/download in an archived usage entry,
  labeled with the removed server's name. User quotas and subscription headers
  include it; manual and automatic user-cycle reset clear it. Traffic after
  removal cannot be collected, including traffic from still-running clients.
- Archives all steps of each affected settled change set, including steps on
  other servers and available command results. History remains readable but
  cannot be dispatched or rolled back after any target is removed.
- Keeps certificate material, profiles and released challenge history. It
  removes this server's deployment targets and disables automatic renewal for
  profiles using it for HTTP validation, recording an explicit error. Other
  deployment targets and certificates remain unchanged. Such profiles retain
  the old validation-server reference for audit; create a replacement profile
  with an available validation server before resuming issuance.

Pending user-removal withdrawals, unsettled change sets/reservations, active remote certificate jobs, unreleased
HTTP challenge leases and unfinished cross-server command dependents block
removal. Resolve or cancel them using their own workflows first. An ordinary
unfinished command on the removed server is forgotten, not remotely cancelled.
Terminal cross-server commands are retained with their removed dependency
detached. Their archived change-set history keeps the original result.

The Agent token stops authenticating immediately. The local active WebSocket
is closed; connections held by another backend worker recheck authentication
on the next incoming message. HTTP requests and reconnect attempts are rejected.
No new Agent command is sent by removal itself.

## API And Upgrade

- `GET /api/v1/servers/{id}/settings`: public server fields and revision.
- `PUT /api/v1/servers/{id}/settings`: complete editable fields,
  `expected_revision`, and optional `sync_node_hosts` (default true).
- `GET /api/v1/servers/{id}/removal`: current impact, revision and blockers.
- `POST /api/v1/servers/{id}/remove`: `expected_revision`, `confirm_name`,
  and `acknowledge_remote_runtime: true`.

SQLite startup adds `agent_change_sets.archived_steps` and the
`subscription_archived_traffic` table. Existing servers, ledgers and change
history are unchanged until an administrator removes a server. Back up before
upgrading; an older backend does not account for the new archived usage table.
Database rollback requires the matching backup, not only old application code.

## VPS Verification

`backend/tests/test_server_management.py` covers field validation, selective
address synchronization, identity preservation, stale revisions, plan cleanup,
legacy and current usage, resets, change-set archives, certificate preflight
races, command dependencies, concurrent creation/removal, authorization and
connected/other-worker WebSocket rejection.

`scripts/vps/smoke-server-management.py` installs an isolated non-root systemd
Agent and provisions real VLESS credentials. It uses exported subscriptions
before and after browser editing, checks retained quotas/history, verifies
Agent rejection after removal and confirms that forwarding persists until
explicit host uninstall. Browser validation, stale edits, cancellation and
confirmation are exercised at 1440, 390 and 320px widths. Run it separately
with `--transport websocket` and `--transport http`; both use trusted TLS.
Tests, builds, formatting and browser execution run only on the configured VPS.
