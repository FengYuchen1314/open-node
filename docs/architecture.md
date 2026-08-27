# Open Node Architecture

## Repository Boundary

Open Node is intentionally a single repository. Backend, frontend, deployment
scripts, and verification utilities live together so feature contracts can be
changed atomically.

```text
open-node
|-- backend   FastAPI application and tests
|-- frontend  Vue 3 + Vuetify application and tests
|-- docs      migration notes and architecture decisions
`-- scripts   VPS bootstrap and verification helpers
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

## Agent Telemetry

Open Node accepts agent telemetry through `/api/v1/agents/telemetry` and the
traffic-compatible `/api/v1/agents/traffic` alias. Reports are authenticated
only with the server bootstrap token and remain license-free. The payload shape
tracks the active MMWX agent wire format: Xray traffic stats, system network
counters, probe system metrics, latency samples, user speeds, and connection
counts.

The backend persists telemetry snapshots in SQLite and exposes the latest
snapshot at `/api/v1/servers/{server_id}/telemetry/latest`.

## Agent Scan Results

The active `mmw-agent` sends Xray runtime discovery as `scan_result` messages
over the authenticated WebSocket, and the same scan body can arrive later as a
successful `/api/child/scan` command result. Open Node stores the latest scan
per server in SQLite, including Xray running state, version, API port, config
path, inbound objects, device kick counters, and config-repair metadata.

The control plane exposes that latest snapshot at
`/api/v1/servers/{server_id}/scan/latest`. Scan records are operational health
data only; they do not contain or imply licensing state.

## Agent Commands

Open Node models master-to-agent work as a transport-neutral command queue.
Control-plane calls are created under `/api/v1/servers/{server_id}/commands`
using the same method, path, query, body, timeout, and stream fields as the
active MMWX WebSocket RPC payload. Agents lease pending commands through
`/api/v1/agents/commands/lease` with their bootstrap token and submit
HTTP-like results to `/api/v1/agents/commands/{command_id}/result`.

The first implementation is intentionally queue-based so pull-mode agents and
future WebSocket RPC can share one persisted state machine.

## Agent WebSocket RPC

Agents can connect to `/api/v1/agents/ws` and authenticate with the same
server bootstrap token used by HTTP registration. The first message must be
`auth`; after that, the socket accepts `heartbeat`, `traffic`/`telemetry`,
`scan_result`, `ping`, and `rpc_reply` messages. Successful auth registers or
refreshes the agent record and stores its capability flags.

When a command is created for a server with an active RPC-capable socket, Open
Node leases that persisted command and immediately sends an MMWX-compatible
`rpc_call` payload. The agent can complete it over the socket with `rpc_reply`;
offline or non-RPC agents still use the HTTP lease/result endpoints.

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
- `POST /api/v1/servers/{server_id}/operations/xray/remove`
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
- `POST /api/v1/servers/{server_id}/operations/nginx/remove`
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

These wrappers enqueue the active `mmw-agent` child paths and then reuse the
same WebSocket RPC dispatch, HTTP lease, result, and stream-frame persistence
contracts as generic commands. They do not introduce separate execution state
or license checks.

Maintenance wrappers for Xray, nginx, and agent lifecycle tasks target the
active `*-stream` child endpoints with `stream=true`, so install, remove,
upgrade, and uninstall output is preserved as command stream frames. WARP
install, status, and remove wrappers target the active non-stream WARP child
endpoints and remain normal command queue entries.

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
WebSocket RPC and HTTP lease/result paths as ordinary commands. Repeated
dispatch calls only create missing forward commands.

Rollback queues rollback commands in reverse step order, again through
`agent_commands`, and records the operator-provided reason on the change set.
Steps without rollback payloads are skipped and returned as warnings. The
current state machine is intentionally small: `planned`, `dispatched`, and
`rollback_queued`. Automatic failure detection can be layered on later without
changing the command transport.

The frontend exposes this in `/changes`, where operators can create JSON step
plans, dispatch them, queue reverse rollback, and inspect each step's forward
and rollback command status.

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
and routed outbound entries. Applying a preset creates a normal managed node
for an existing inventory server while allowing operators to override host,
port, tags, and route markers.

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
password credentials for Trojan/AnyTLS/Hysteria, base64 Shadowsocks keys, and
user/pass pairs for socks/http. The same credential row is reused for agent
batch provisioning and Clash subscription rendering.

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
- `sing-box`: sing-box JSON outbounds with a selector.
- `uri-list`: plaintext proxy URI lines.
- `base64`: base64-encoded URI list for clients that expect legacy
  subscription bodies.

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

Open Node exposes the read-only public probe surface without authentication or
license gates. The primary endpoints are:

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
return-route, and renewal columns. When the probe is disabled, `/probe-servers`
still returns a no-license JSON payload with `enabled=false`, but the public
server list is empty so node telemetry is not exposed. The Vue `/probe` view
can edit these settings and immediately uses the same public payload to render
or hide table sections, status and region filters, region summaries, seven-day
traffic bars, health chips, latency history buckets, quota meters,
return-route badges, renewal badges, live traffic hotspot rows, and per-node
drill-down charts. It also calls the public target comparison endpoint to rank
latency targets across nodes. Drill-downs call the public series endpoint with
the selected public server index, range, and metric mode, then render latency,
loss, CPU, memory, and throughput history without revealing private server IDs.

Probe task schedules are private management data stored in SQLite. Each task
targets one server and one active agent child operation: system info,
domain-latency probing, or return-route testing. `dispatch-due` can be called
from a frontend button, cron job, or systemd timer; due tasks create normal
`agent_commands` rows and then reuse the existing WebSocket push or HTTP lease
flow. Domain-latency command results from `/api/child/domains/latency` are
converted into telemetry latency samples so the public series endpoint can show
new probe history without exposing server IDs or agent tokens. Return-route
results continue to use the sanitized carrier summary table.

The WebSocket stream is also public and read-only. It sends the same
`ProbePayload` structure as the HTTP list endpoint, drops any client messages,
limits concurrent connections in memory, follows the configured refresh
interval, and keeps the no-license response contract.
