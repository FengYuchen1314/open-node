# Open Node Architecture

## Repository Boundary

Open Node is intentionally a single repository. Backend, frontend, deployment
scripts, and verification utilities live together so feature contracts can be
changed atomically.

```text
open-node
|-- backend       FastAPI application and tests
|-- frontend      Vue 3 + Vuetify application and tests
|-- probe-worker  Cloudflare Worker for the standalone probe surface
|-- docs          migration notes and architecture decisions
`-- scripts       VPS bootstrap and verification helpers
```

## Product Boundary

The refactor tracks the active MMWX product line only:

- Control plane: `FengYuchen1314/miaomiaowuX`
- Agent: `FengYuchen1314/mmw-agent`
- Probe: `FengYuchen1314/mmwx-probe`
- Xray integration fork: `FengYuchen1314/Xray-core-mmwx`

The older `miaomiaowu` project and archived `NodeControll` rebuild are not
inputs for this implementation.

## No-License Contract

Open Node may have an open source software license, but the runtime product must
not require a license key. The backend exposes this as API data and tests assert
that:

- `license_required` is always `false`.
- paid entitlements are disabled.
- no external license server is configured.
- no feature gates are returned.

Future feature work should add capability flags only for availability, health,
configuration, or compatibility states. It must not introduce paid unlocks.

## Initial Runtime Shape

The backend serves JSON APIs under `/api/v1`. The frontend is a Vite application
that can point to the backend with `VITE_API_BASE_URL` or use same-origin API
paths in production.

The production image serves the built Vue frontend from FastAPI using
`OPEN_NODE_FRONTEND_DIR`. Static assets retain conditional/range responses;
HTML is revalidated and hashed assets are immutable. Browser navigation can
fall back to `index.html`, but missing API routes, static assets, and dotfiles
cannot. The shipped Compose service has one backend process and one private
persistent volume. See [deployment.md](deployment.md) for the HTTPS and
single-host operating contract.

## Administrator Sessions

Management routes use a shared FastAPI administrator dependency. Public
subscription rendering and Agent transport routes are explicitly separate;
Agent inventory listing and both probe-settings write aliases remain private.
An unconfigured installation is closed to management access, with account
creation and recovery available only through the local administrator CLI.

Authentication uses an Argon2id password hash and opaque cookie sessions.
Only session-secret hashes are persisted; session-bound CSRF tokens, absolute
expiry, idle expiry, revocation, and login-rate windows are persisted alongside
the inventory database. Credential-version checks prevent a password-reset
race from issuing a session with an obsolete password. Changing a password
revokes every session. The Vue shell waits for a session before mounting
management views and returns to sign-in when an API request reports expiry.
No authentication state is a paid entitlement or licensing gate.

See [administrator access](administrator-access.md) for deployment settings
and the HTTP contract.

## Central Certificates

The authenticated `/api/v1/certificates` API stores DNS providers, certificate
profiles, durable jobs, encrypted versions and deployment targets in the same
database as inventory. A host-local private directory holds the vault key,
lego account state and worker lock. Credential and material responses exclude
secrets unless private-key export is explicitly requested. The existing Agent
command queue still carries deployment PEM material and must be protected.

One lifespan worker per shared state directory schedules DNS-01 jobs through
an operator-provided lego v4 executable. Its child inherits the worker lock;
timeouts/cancellation terminate the owned process group. The worker retains
the last active version after failures and resumes queued work after restart.
Imports and deployments do not require an ACME executable. Activating a new
version queues automatic targets through the normal Agent command and owned
certificate-file transaction, so issuance and runtime activation have separate
results. No license service participates in either path.

See [certificate management](certificates.md) for provider fields, setup,
backup requirements, retry semantics and remaining challenge/account limits.

## Agent Telemetry

The `agent/` package is an independent Linux implementation, not a repackaged
MMWX Agent. It uses an operator-provided Xray binary, verifies HTTPS by default,
and supports WebSocket plus HTTP polling/fallback. Commands are serialized and
journaled in a private SQLite database before their results are transmitted.
Completed requests are replayed from the journal after redelivery; interrupted
requests return a conflict requiring reconciliation rather than an automatic
second execution. A local exclusive lock prevents two agents sharing a journal.

Managed mode owns one Xray subprocess. Systemd mode controls only its configured
service. Candidate configs are validated by Xray before atomic file replacement;
edit-and-restart operations restore the previous config if restart fails.
Service-stop intent is persisted separately from process liveness. The installed
wheel has been tested with live VLESS traffic in managed mode. A host deployment
CLI installs this mode as a hardened non-root systemd service. It records owned
paths/account/unit content, stages immutable version/digest releases, checks
process/package identity and fresh Agent health, and persists release-switch
transactions before stopping the current service. Failed activation restores
the previous release; interrupted switches can be recovered explicitly.
Uninstall preserves configuration/state unless purge is explicitly selected.
See [Agent deployment](agent-deployment.md). The separate
[external systemd mode](external-systemd.md) verifies the canonical unit,
root-owned host files, dedicated account, explicit JSON config and live process
before mutation. Scoped polkit rules authorize only that unit's start/stop/restart.
Binding failures keep the connection alive; Agent shutdown leaves Xray running.
HTTPS/WSS real-traffic verification covers this single-file mode, not arbitrary
multi-file takeover.

Optional [remote Agent lifecycle](agent-lifecycle.md) uses a root-owned helper
with a host-approved HTTPS release source and a permission-restricted Unix
socket. The Agent remains non-root. Version/checksum-pinned jobs are journaled
by request identity and return deferred acceptance, not an early RPC success.
Only these host jobs allow pending-request redelivery through the Agent journal;
the generic interrupted-command conflict contract remains unchanged. The helper
recovers package staging, switching and removal after crashes and reports its
actual outcome using an unprivileged reporter. It remains after uninstall until
the controller acknowledges that result, then stops its socket/service.

The control-plane operation wrappers cover more endpoints than this agent
currently implements. See [the agent contract](../agent/README.md) for its
supported operations. Unsupported operations return 501, never simulated success.

Open Node accepts agent telemetry through `/api/v1/agents/telemetry` and the
traffic-compatible `/api/v1/agents/traffic` alias. Reports are authenticated
only with the server bootstrap token and remain license-free. The payload shape
tracks the active MMWX agent wire format: Xray traffic stats, system network
counters, probe system metrics, latency samples, user speeds, and connection
counts.

The backend persists telemetry snapshots in SQLite and exposes the latest
snapshot at `/api/v1/servers/{server_id}/telemetry/latest`.

## Agent Scan Results

The independent agent also sends the same node-token-authenticated payload to
`POST /api/v1/agents/scan` when using HTTP transport. This endpoint is separate
from operator-only inventory inspection and does not echo the token.

The active `mmw-agent` sends Xray runtime discovery as `scan_result` messages
over the authenticated WebSocket, and the same scan body can arrive later as a
successful `/api/child/scan` command result. Open Node stores the latest scan
per server in SQLite, including Xray running state, version, API port, config
path, inbound objects, device kick counters, and config-repair metadata.

The control plane exposes that latest snapshot at
`/api/v1/servers/{server_id}/scan/latest`. It also derives a sanitized runtime
inventory at `/api/v1/servers/{server_id}/xray/runtime`, summarizing inbound
tags, protocols, ports, transport/security names, client counts, client email
labels, sniffing state, protocol totals, and config-repair metadata without
returning UUIDs, passwords, PSKs, or account secrets. When latest telemetry has
Xray stats, runtime inventory also reports matched inbound traffic counters and
per-inbound user traffic summed only from already exposed client email labels.
The current Xray config snapshot also powers
`/api/v1/servers/{server_id}/xray/runtime/tunnels`, a sanitized tunnel
inventory that lists `protocol=tunnel` inbounds, `tunnel-*` routed forwarding
rules, and grouped `tunnel-<label>-h<i>` chains without returning full outbound
configuration or credential material. Operators can then use
`/api/v1/servers/{server_id}/xray/runtime/tunnels/delete` to preview or queue
the matching agent cleanup commands: inbound/chain deletes use
`/api/child/inbounds`, while routed tunnel deletes remove the routing rule and
then the matching outbound. Operators can also plan an ordered multi-server
tunnel chain through `/api/v1/servers/xray/runtime/tunnel-chains`, which picks
conflict-free hop ports from current snapshots, previews the
`/api/child/inbounds` add commands, and can queue those commands plus
follow-up scans for every hop server.
The single-server tunnel-mode deployment endpoint
`/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy` renders the tunnel
Nginx main config, the selected static/proxy camouflage domain config, and the
baseline Xray `tunnel-in` config. By default it previews the clear-stream,
Nginx setup, Xray config write, and restart commands; when queueing against a
snapshot that already contains user inbounds or custom outbounds, callers must
set `force=true` to make the overwrite explicit.
It can also derive managed-node drafts from those inbounds at
`/api/v1/servers/{server_id}/xray/runtime/node-drafts` and create catalog nodes
through `/api/v1/servers/{server_id}/xray/runtime/nodes`. Operators can also
bulk-import all available missing runtime inbounds through
`/api/v1/servers/{server_id}/xray/runtime/nodes/import`, which reports created,
already-managed, and skipped entries. The reconciliation endpoint at
`/api/v1/servers/{server_id}/xray/runtime/nodes/reconciliation` compares scan
inbounds with managed catalog nodes, reporting unmanaged runtime entries,
managed nodes missing from runtime, and public connection-field drift. Stale
physical managed nodes can be synced from runtime through
`/api/v1/servers/{server_id}/xray/runtime/nodes/{node_id}/sync`, which updates
only public connection fields and preserves operator-owned names, tags,
enabled state, and generated subscription credentials. The credential
reconciliation endpoint at
`/api/v1/servers/{server_id}/xray/runtime/credentials/reconciliation` compares
locally generated subscription credential emails with the sanitized runtime
client email labels for each physical managed node, reporting missing and extra
runtime clients without returning credential UUIDs, passwords, PSKs, or other
secrets. Operators can queue missing-client repair through
`/api/v1/servers/{server_id}/xray/runtime/credentials/repair-missing`, which
builds an agent `batch-apply` body for current active subscriptions that are
absent from runtime. Extra runtime clients remain report-only by default, and
operators can explicitly queue cleanup through
`/api/v1/servers/{server_id}/xray/runtime/credentials/cleanup-extra`, which
sends per-email `remove-client` commands to the matching inbound. Repair and
cleanup requests can optionally queue a follow-up `/api/child/scan` command
after queued changes, so reconciliation can refresh from the agent's next
runtime snapshot. Those drafts, drift records, sync writes, and credential
checks copy public connection metadata only; per-user credentials are still
generated by the subscription system. Scan records are operational health data
only; they do not contain or imply licensing state.

## Agent Commands

Open Node models master-to-agent work as a transport-neutral command queue.
Control-plane calls are created under `/api/v1/servers/{server_id}/commands`
using the same method, path, query, body, timeout, and stream fields as the
active MMWX WebSocket RPC payload. Agents lease pending commands through
`/api/v1/agents/commands/lease` with their bootstrap token and submit
HTTP-like results to `/api/v1/agents/commands/{command_id}/result`.

HTTP leasing and WebSocket RPC share one persisted state machine. A conditional
database update claims each lease for one transport; completed commands and
unexpired leases cannot be pushed again. Failed socket sends release their own
lease without resetting a newer attempt. Expired leases can be retried, so
execution is at-least-once rather than exactly-once.

Recovery apply, tunnel deployment/deletion, and runtime credential repair or
cleanup create each server's command sequence in one transaction. Commands
after the first start as `waiting`, with `depends_on_command_id` pointing to
the preceding command. A successful result makes the immediate successor
`pending`; a failed result marks all remaining successors `skipped` without
leasing them. Follow-up scans use the same dependency chain. Tunnel-chain
servers can proceed independently, but each server's scan waits for its own
changes. Existing SQLite databases gain the nullable dependency column and
index without changing earlier commands or history.

An Xray test-config response must explicitly contain `ok=true` before recovery
can continue. HTTP errors, transport errors, and `success=false` bodies fail
the command; failure cannot update config snapshots or trigger a success
refresh. Results for waiting commands are rejected, and terminal results are
accepted once using a conditional update, including concurrent replies. The
Vue command inspector distinguishes waiting and skipped commands and shows
their prerequisite IDs.

## Agent WebSocket RPC

Agents can connect to `/api/v1/agents/ws` or the active MMWX agent's
`/api/remote/ws` address and authenticate with the same
server bootstrap token used by HTTP registration. Authentication uses `auth`,
optionally preceded by the legacy key exchange; after that, the socket accepts `heartbeat`, `traffic`/`telemetry`,
`scan_result`, `ping`, and `rpc_reply` messages. Successful auth registers or
refreshes the agent record and stores its capability flags.
An auth payload with `probe=true` only verifies the token and closes after the
auth response. It does not change inventory, enqueue work, or replace a live
agent connection.

When a command is created for a server with an active RPC-capable socket, Open
Node leases that persisted command and immediately sends an MMWX-compatible
`rpc_call` payload. The agent can complete it over the socket with `rpc_reply`;
offline or non-RPC agents still use the HTTP lease/result endpoints.
After authentication, after incoming socket messages, and after HTTP command
completion, the connection manager also dispatches queued work and expired
leases. This delivers offline backlog and automatically generated config
refreshes to connected RPC agents. Unsupported stream commands remain queued
for a compatible transport. An authentication response is always sent before
any RPC work on that socket.

The transport adapter supports MMWX `securechan-v1` using the `cryptography`
library. An explicitly configured private signing seed enables X25519 key
exchange, Ed25519 identity verification, HKDF and directional AES-GCM frames.
After exchange, authentication, acknowledgments, RPC and stream messages all
use encrypted binary frames. Replay checks advance only after authentication
of the ciphertext. The legacy `/api/remote/ws` endpoint requires exchange when
an identity is configured; the native endpoint retains its TLS-protected JSON
contract. There is no silent key regeneration or in-connection downgrade.
See [legacy-agent-migration.md](legacy-agent-migration.md) for key custody,
HTTP/pull migration and protocol limits. TLS and operator authentication remain
required; the legacy extension is not an alternative to either.

Stream-capable agents can also receive `rpc_call` payloads with `stream=true`.
They may send any number of MMWX-compatible `rpc_stream_data` text frames before
the final `rpc_reply`. Open Node persists each frame in command sequence order
and exposes them at `/api/v1/servers/{server_id}/commands/{command_id}/stream`.

## Agent Operations

The generic command queue remains available for low-level and future MMWX child
routes, while common agent actions also have stable control-plane wrappers:

- `POST /api/v1/servers/{server_id}/operations/system-info`
- `POST /api/v1/servers/{server_id}/operations/traffic`
- `POST /api/v1/servers/{server_id}/operations/speed`
- `POST /api/v1/servers/{server_id}/operations/domain-latency`
- `POST /api/v1/servers/{server_id}/operations/inbounds/list`
- `POST /api/v1/servers/{server_id}/operations/inbounds/manage`
- `POST /api/v1/servers/{server_id}/operations/outbounds/list`
- `POST /api/v1/servers/{server_id}/operations/outbounds/manage`
- `POST /api/v1/servers/{server_id}/operations/routing/read`
- `POST /api/v1/servers/{server_id}/operations/routing/manage`
- `POST /api/v1/servers/{server_id}/operations/batch-apply`
- `POST /api/v1/servers/{server_id}/operations/cert/deploy`
- `POST /api/v1/servers/{server_id}/operations/services/status`
- `POST /api/v1/servers/{server_id}/operations/services/control`
- `POST /api/v1/servers/{server_id}/operations/system/nics`
- `POST /api/v1/servers/{server_id}/operations/logs`
- `POST /api/v1/servers/{server_id}/operations/logs/files/list`
- `POST /api/v1/servers/{server_id}/operations/logs/files/delete`
- `POST /api/v1/servers/{server_id}/operations/scan`
- `POST /api/v1/servers/{server_id}/operations/xray/test-config`
- `POST /api/v1/servers/{server_id}/operations/xray/config/read`
- `POST /api/v1/servers/{server_id}/operations/xray/config/write`
- `POST /api/v1/servers/{server_id}/operations/xray/system-config/read`
- `POST /api/v1/servers/{server_id}/operations/xray/system-config/write`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/list`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/read`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/write`
- `POST /api/v1/servers/{server_id}/operations/xray/takeover-external`
- `POST /api/v1/servers/{server_id}/operations/xray/install`
- `POST /api/v1/servers/{server_id}/operations/xray/install-legacy`
- `POST /api/v1/servers/{server_id}/operations/xray/remove`
- `POST /api/v1/servers/{server_id}/operations/xray/remove-legacy`
- `POST /api/v1/servers/{server_id}/operations/nginx/config/read`
- `POST /api/v1/servers/{server_id}/operations/nginx/config/write`
- `POST /api/v1/servers/{server_id}/operations/nginx/config-files/list`
- `POST /api/v1/servers/{server_id}/operations/nginx/config-files/read`
- `POST /api/v1/servers/{server_id}/operations/nginx/config-files/write`
- `POST /api/v1/servers/{server_id}/operations/nginx/setup-ssl`
- `POST /api/v1/servers/{server_id}/operations/nginx/servers-list`
- `POST /api/v1/servers/{server_id}/operations/nginx/websites/list`
- `POST /api/v1/servers/{server_id}/operations/nginx/websites/delete`
- `POST /api/v1/servers/{server_id}/operations/nginx/install`
- `POST /api/v1/servers/{server_id}/operations/nginx/install-legacy`
- `POST /api/v1/servers/{server_id}/operations/nginx/remove`
- `POST /api/v1/servers/{server_id}/operations/nginx/remove-legacy`
- `POST /api/v1/servers/{server_id}/operations/nginx/clear-stream-port`
- `POST /api/v1/servers/{server_id}/operations/network/return-route-test`
- `POST /api/v1/servers/{server_id}/operations/validate-site`
- `POST /api/v1/servers/{server_id}/operations/limiter`
- `POST /api/v1/servers/{server_id}/operations/warp/install`
- `POST /api/v1/servers/{server_id}/operations/warp/status`
- `POST /api/v1/servers/{server_id}/operations/warp/license`
- `POST /api/v1/servers/{server_id}/operations/warp/remove`
- `POST /api/v1/servers/{server_id}/operations/agent/switch-xray-mode`
- `POST /api/v1/servers/{server_id}/operations/agent/switch-listen-port`
- `POST /api/v1/servers/{server_id}/operations/agent/probe-master-url`
- `POST /api/v1/servers/{server_id}/operations/agent/update-master-url`
- `POST /api/v1/servers/{server_id}/operations/agent/upgrade`
- `POST /api/v1/servers/{server_id}/operations/agent/uninstall`
- `GET /api/v1/servers/{server_id}/xray/runtime`
- `GET /api/v1/servers/{server_id}/xray/runtime/tunnels`
- `POST /api/v1/servers/{server_id}/xray/runtime/tunnels/delete`
- `POST /api/v1/servers/xray/runtime/tunnel-chains`
- `POST /api/v1/servers/{server_id}/xray/runtime/tunnel-deploy`
- `GET /api/v1/servers/{server_id}/xray/runtime/node-drafts`
- `POST /api/v1/servers/{server_id}/xray/runtime/nodes`
- `POST /api/v1/servers/{server_id}/xray/runtime/nodes/import`
- `GET /api/v1/servers/{server_id}/xray/runtime/nodes/reconciliation`
- `POST /api/v1/servers/{server_id}/xray/runtime/nodes/{node_id}/sync`
- `GET /api/v1/servers/{server_id}/xray/runtime/credentials/reconciliation`
- `POST /api/v1/servers/{server_id}/xray/runtime/credentials/repair-missing`
- `POST /api/v1/servers/{server_id}/xray/runtime/credentials/cleanup-extra`
- `GET /api/v1/servers/{server_id}/xray/config-snapshots`
- `GET /api/v1/servers/{server_id}/xray/config-snapshots/recovery`
- `POST /api/v1/servers/{server_id}/xray/config-snapshots/recovery/accept`
- `POST /api/v1/servers/{server_id}/xray/config-snapshots/recovery/apply`
- `POST /api/v1/servers/{server_id}/xray/config-snapshots/{snapshot_id}/restore`

These wrappers enqueue the active `mmw-agent` child paths and then reuse the
same WebSocket RPC dispatch, HTTP lease, result, and stream-frame persistence
contracts as generic commands. They do not introduce separate execution state
or license checks.

Maintenance wrappers for Xray, nginx, and agent lifecycle tasks target the
active `*-stream` child endpoints with `stream=true`, so install, remove,
upgrade, and uninstall output is preserved as command stream frames. WARP
install, status, and remove wrappers target the active non-stream WARP child
endpoints and remain normal command queue entries.
Compatibility wrappers with the `-legacy` suffix target the active non-stream
Xray/nginx install and remove child endpoints for automation that still expects
one final JSON result instead of stream frames.

Diagnostic and config-preparation wrappers cover service status/control, system
NIC enumeration, service logs, agent log-file listing/cleanup, agent-side scan,
and Xray config validation. The service-control wrapper only accepts the active
agent's `xray` and `nginx` targets with `start`, `stop`, or `restart`; the logs
wrapper clamps requests to the agent-supported `1..2000` line range before
building the child query. Log-file cleanup rejects path-like names before
queuing the active agent's `DELETE /api/child/logs/files` query.
Successful scan command results update the same latest scan-result record used
by WebSocket `scan_result` messages, so dashboard runtime status stays
transport-neutral.

Config wrappers cover the active agent's Xray and nginx main config and
`config-files` routes. Control-plane requests serialize structured Xray JSON
into the text shape the agent expects, validate obvious path and URL hazards,
and preserve the agent-side write/test/reload behavior. Agent setting wrappers
cover Xray mode, listen port, master URL probe/update, and WARP credential
updates without changing the Open Node no-license contract.
Successful `GET` and `POST` results for `/api/child/xray/config` are also
stored as Xray config snapshots. The first agent report becomes `current`.
Later agent-reported drift is stored as `pending_recovery` so it does not
silently replace the master snapshot. Operators can accept the pending agent
config as the new current snapshot, queue the current master snapshot back to
the agent through test, write, and restart commands, or restore any saved
snapshot through the same command dispatch path. Recovery apply defaults to
merging agent-only `inbounds` and `outbounds` from the pending config into the
master snapshot before queuing it, but leaves routing rules untouched because
they do not have stable tags. Successful master writes discard stale pending
recovery rows.
Recovery validation, write, and optional restart are separate dependent
commands. A validation result with HTTP 200 and `ok=false` skips both write
and restart, preserving the agent's current file.
Successful mutating Xray child commands for inbounds, outbounds, routing,
batch apply, config files, system config, direct config writes, and external
takeover also enqueue one deduplicated `GET /api/child/xray/config` refresh.
That follow-up read is marked as a master-write refresh, so it updates the
current snapshot and runtime inventory instead of creating a false drift
warning from the master's own operation.
Every normal HTTP registration or WebSocket authentication also queues a
deduplicated agent-report config read, including the first connection before
any snapshot exists. Repeated registration reuses a pending or leased read.
Missing, empty, and failed reads do not create snapshots or interrupt the
authenticated connection, and the next registration can try again. A report
matching the current snapshot clears an obsolete pending recovery. A differing
report preserves the current snapshot and awaits an operator decision.

The Xray external takeover wrapper queues the active agent's
`/api/child/external-xray/takeover` route. It lets an operator merge an
existing external Xray `-config` plus `-confdir` layout into the single
MMWX-managed config file before using the normal runtime inbounds, outbounds,
and routing operations.

High-level workflow wrappers cover active agent inbound, outbound, routing,
batch apply, certificate deployment, nginx SSL setup, nginx website inventory
and deletion, return-route testing, website validation, and embedded limiter
configuration. Successful return-route test command results are parsed into a
local latest-result table keyed by server and carrier; public probe output only
exposes the carrier, region, route type, and timestamp, leaving hop evidence
and diagnostic reasons private. Routing manage requests preserve the agent's
camel-case `burstObservatory` field while keeping the Open Node API typed and
explicit. The nginx stream-port cleanup wrapper queues
`/api/child/nginx/clear-stream-port` with an explicit port for removing stale
stream server configs after migration or proxy mode changes.

The frontend exposes these wrappers in a dedicated `/config` workspace. It can
queue Xray and nginx read/write operations, load completed read results back
into editors, manage config-file read/write calls, dispatch high-level runtime
and site payloads, and inspect each command's request, result body, error, and
stream frames.

## Change Sets and Rollback

Open Node groups coordinated multi-server agent work as persisted change sets
under `/api/v1/change-sets`. A change set contains ordered steps. Each step
targets one server and stores a forward `AgentCommandCreate` payload plus an
optional rollback payload.

Creating a change set can be a dry plan or an immediate dispatch. Dispatch
creates one persisted `agent_commands` row per forward step and reuses the same
WebSocket RPC and HTTP lease/result paths as ordinary commands. Atomic target
reservations and persisted cross-node dependencies serialize execution. Repeat
dispatch while active is idempotent; terminal changes cannot be redispatched.
Already-started ordinary dependency sequences finish before the new change,
while new unrelated work waits for reservations to be released.

`POST /api/v1/change-sets/routed-outbound` builds an MMWX-compatible routed
outbound plan for one inventory server. The planner generates the routed
outbound tag, admin user email, admin inbound credential, optional Reality SNI
sniffing excludes, outbound add command, and routing `add_rule` command while
leaving dispatch optional. If callers pass `parent_ref` such as `p42`, generated
tags follow the legacy `routed:p42:<label>` shape; otherwise the server ID
prefix is used as a stable fallback. Rollback avoids the agent's index-based
`remove_rule` path and instead removes the admin user from the marked rule,
then removes the outbound and admin client. Additive sniffing excludes are left
in place by design because the active agent has no remove-exclude operation.

Forward failures stop unsent successors and optionally start automatic
compensation. Rollback waits for in-flight forward outcomes, then executes
compensators only for attempted steps, in reverse dependency order. Failed
compensation retains reservations; retries preserve history and do not repeat
successful compensation. Missing compensation is explicitly incomplete.
Terminal states distinguish success, rollback, cancellation and operator
acceptance. SQLite upgrades pause legacy executions for review.

The frontend exposes this in `/changes`, where operators can use the guided
routed-outbound form or raw JSON step plans, dispatch them, queue reverse
rollback, inspect command results and retry history, or explicitly accept a
partial state with an audit reason. See [change-sets.md](change-sets.md) for
state transitions, concurrency boundaries and upgrade procedures.

## Subscription Catalog

Open Node keeps the MMWX subscription workflow as first-party product data
without licensing or entitlement checks. The backend stores product users,
managed nodes, and subscription plans in SQLite and exposes them through:

- `GET /api/v1/users`
- `POST /api/v1/users`
- `GET /api/v1/nodes`
- `POST /api/v1/nodes`
- `GET /api/v1/node-presets`
- `POST /api/v1/node-presets/{preset_id}/nodes`
- `GET /api/v1/plans`
- `POST /api/v1/plans`
- `POST /api/v1/users/{username}/plan`
- `GET /api/v1/catalog/export`
- `POST /api/v1/catalog/import`

Managed nodes link Open Node catalog records to server inventory records. They
can hold inbound tags, routed outbound or rule markers, tags, opaque config,
and a JSON client template. The template supports simple placeholders such as
`{username}`, `{node_name}`, and `{server_name}` so plan assignment can prepare
the same `inbound_clients` and `routing_user_additions` batch body expected by
the active `mmw-agent` `/api/child/batch-apply` route.

Node presets provide ready-made free catalog templates for common MMWX
subscription shapes: VLESS Vision TLS, Trojan TLS, Shadowsocks 2022, Hysteria2,
AnyTLS, Snell v4/v6, Mieru, and routed outbound entries. Applying a preset
creates a normal managed node for an existing inventory server while allowing
operators to override host, port, tags, and route markers.

Catalog export serializes users, nodes, plans, and optionally generated
credentials by stable names instead of local database IDs. Catalog import can
recreate or update those resources in another Open Node database, remapping
servers through `server_map` when the destination inventory uses different
server IDs. Imported credentials are opt-in so operators can choose between a
fresh credential lease and an exact migration.

Plan assignment always returns the calculated per-server provisioning batches.
Callers can keep this as a preview, or set `queue_agent_commands=true` to create
and dispatch persisted batch-apply commands through the existing WebSocket RPC
and HTTP lease paths. The catalog response models all include
`license_required=false`.

Open Node also stores stable per-user, per-node credentials and public
subscription tokens. Credential generation follows the active MMWX contract:
`<username>__<inbound_tag>` client emails, UUID-style IDs for VLESS/VMess,
password credentials for Trojan/AnyTLS/Hysteria, Snell PSK credentials, Mieru
username/password credentials, base64 Shadowsocks keys, and user/pass pairs
for socks/http. The same credential row is reused for agent batch provisioning
and Clash subscription rendering.

Users can receive a full token URL or short-code URL at:

- `GET /api/v1/users/{username}/subscription-token`
- `POST /api/v1/users/{username}/subscription-token`
- `POST /api/v1/users/{username}/subscription-token/reset`
- `GET /api/v1/users/{username}/credentials`
- `GET /api/v1/users/{username}/quota`
- `POST /api/v1/users/{username}/traffic/reset`
- `POST /api/v1/traffic/reset-due`
- `GET /api/v1/subscribe/{token_or_short_code}`

The public subscription endpoint renders from managed node proxy configs,
injects the user's stored credential into each proxy, and emits the standard
`subscription-userinfo` header. The default output remains Clash-compatible
YAML, and callers can request alternate formats with
`GET /api/v1/subscribe/{token_or_short_code}?format=...`:

- `clash`: Clash-compatible YAML with a select group.
- `sing-box`: sing-box JSON outbounds with a selector, including AnyTLS and
  Snell when their managed-node config contains the required client fields.
- `uri-list`: plaintext proxy URI lines.
- `base64`: base64-encoded URI list for clients that expect legacy
  subscription bodies.

Fork protocol extensions are not a guarantee of stock-client compatibility:
stock Mihomo does not accept Snell v6 and stock sing-box does not implement
Snell. Per-client filtering and a native free-client v6 export remain unfinished.
See [fork-runtime.md](fork-runtime.md) for actual traffic coverage and limits.

Open Node records subscription traffic in a durable ledger when agent telemetry
contains `stats.user` counters for known credential emails. The first observed
counter value is counted, later telemetry only adds positive deltas, and
counter resets are treated as a new epoch. The ledger backs both
`subscription-userinfo` and `GET /api/v1/users/{username}/traffic`; old
databases without ledger entries still fall back to the latest telemetry
snapshot until new telemetry arrives.

Quota status compares ledger usage against the assigned plan using the plan's
traffic mode: `oneway` charges download traffic only, while `twoway` charges
upload plus download. Expired, inactive, unassigned, and over-quota users are
reported as unavailable, and over-quota users cannot render public
subscriptions until usage is reset or the plan changes.

Traffic reset keeps Xray counter baselines instead of deleting ledger rows, so
the next telemetry report only counts post-reset deltas. Operators can reset a
single user's ledger or run `POST /api/v1/traffic/reset-due` from an external
cron/automation job. Due resets use each user's monthly `reset_day` and
`last_traffic_reset_at` so repeated automation calls in the same reset window
are idempotent. SQLite databases created before this field existed are
upgraded in place during schema creation.

The frontend exposes this in `/subscriptions`, where operators can create users,
catalog nodes, plans, assignments, public links, generated credentials, format
URLs, traffic ledger summaries, quota status, traffic resets, preset-created
nodes, and catalog export/import bundles, then inspect the last calculated
batch before or after dispatching it.

## Public Probe API

Open Node exposes the read-only public probe surface without license gates. The
data endpoints can optionally require the `X-MMwx-Probe-Token` header used by
the standalone Cloudflare Worker, so direct origin access can return the same
compact public `404` shape while Worker traffic is still served. The primary
endpoints are:

- `GET /api/v1/public/probe-servers`
- `GET /api/v1/public/probe-settings`
- `PUT /api/v1/public/probe-settings`
- `GET /api/v1/public/probe-series`
- `GET /api/v1/public/probe-targets`
- `GET /api/v1/public/probe-ws`
- `GET /api/v1/probe/tasks`
- `POST /api/v1/probe/tasks`
- `PATCH /api/v1/probe/tasks/{task_id}`
- `POST /api/v1/probe/tasks/dispatch-due`
- `POST /api/v1/probe/access-token`
- `DELETE /api/v1/probe/access-token`

For compatibility with the `mmwx-probe` Worker route mapping, the same handlers
are also mounted at:

- `GET /api/public/probe-servers`
- `GET /api/public/probe-settings`
- `PUT /api/public/probe-settings`
- `GET /api/public/probe-series`
- `GET /api/public/probe-targets`
- `GET /api/public/probe-ws`

Probe responses are built from persisted agent telemetry snapshots. The server
list exposes only public status, speed, resource, traffic, latency, seven-day
daily traffic summary, and optional return-route summary fields; internal
identifiers, IP addresses, bootstrap tokens, route entry hops, trace reasons,
and agent secrets are not serialized. Daily traffic is calculated from Xray stat
counters when present and falls back to system network counters across
consecutive telemetry snapshots. Series lookups use the public server index
from the sanitized list instead of private server IDs. Target comparison
lookups group latency series by target key across all public nodes and return
only public server indexes, names, regions, current latency, loss, and buckets.

Server probe metadata can be supplied when a server is created or updated with
`PATCH /api/v1/servers/{server_id}/probe-metadata`. The metadata covers region
code/country/name/city, provider name/URL, expiry date, renewal price in native
and CNY currencies, renewal cycle, and telecom paid-peer status. The public
probe payload includes these sanitized values while continuing to omit private
server IDs and connectivity details.

Probe settings are stored locally in SQLite and are license-free. They control
the public title, description, logo URL, refresh interval, appearance metadata,
and visibility flags such as traffic quota, resource, health, traffic-history,
return-route, renewal columns, and the optional Worker token requirement. Token
generation returns the plaintext token once and stores only a SHA-256 hash in
SQLite; clearing the token also disables the requirement. When the probe is
disabled, `/probe-servers` still returns a no-license JSON payload with
`enabled=false`, but the public server list is empty so node telemetry is not
exposed. The Vue `/probe` view can edit these settings, generate or clear the
Worker token, and immediately uses the same public payload to render or hide
table sections, status and region filters, region summaries, seven-day traffic
bars, health chips, latency history buckets, quota meters, return-route badges,
renewal badges, live traffic hotspot rows, and per-node drill-down charts. It
also calls the public target comparison endpoint to rank latency targets across
nodes. Drill-downs call the public series endpoint with the selected public
server index, range, and metric mode, then render latency, loss, CPU, memory,
and throughput history without revealing private server IDs.

Probe task schedules are private management data stored in SQLite. Each task
targets one server and one active agent child operation: system info,
domain-latency probing, or return-route testing. `dispatch-due` can be called
from a frontend button, cron job, or systemd timer; due tasks create normal
`agent_commands` rows and then reuse the existing WebSocket push or HTTP lease
flow. Domain-latency command results from `/api/child/domains/latency` are
converted into telemetry latency samples so the public series endpoint can show
new probe history without exposing server IDs or agent tokens. Return-route
results continue to use the sanitized carrier summary table.

The WebSocket stream is also read-only and honors the same optional Worker token
header before accepting a streaming client. It sends the same `ProbePayload`
structure as the HTTP list endpoint, drops any client messages, limits
concurrent connections in memory, follows the configured refresh interval, and
keeps the no-license response contract.

The `probe-worker/` package is a small Cloudflare Worker that hosts the built
Vue app from `frontend/dist` with Workers Static Assets and proxies only the
MMWX-compatible probe routes to the origin. It maps `/api/probe`,
`/api/series`, `/api/targets`, `/api/stream`, `/api/public/*`, and
`/api/v1/public/*` paths onto the Open Node v1 public probe endpoints, strips
cookies and authorization headers, sets `X-Forwarded-Host`, adds
`X-MMwx-Probe-Token` from its `PROBE_TOKEN` secret, and returns all proxy
responses with `Cache-Control: no-store`.
