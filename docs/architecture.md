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
`ping`, and `rpc_reply` messages. Successful auth registers or refreshes the
agent record and stores its capability flags.

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
- `POST /api/v1/servers/{server_id}/operations/scan`
- `POST /api/v1/servers/{server_id}/operations/xray/test-config`
- `POST /api/v1/servers/{server_id}/operations/xray/config/read`
- `POST /api/v1/servers/{server_id}/operations/xray/config/write`
- `POST /api/v1/servers/{server_id}/operations/xray/system-config/read`
- `POST /api/v1/servers/{server_id}/operations/xray/system-config/write`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/list`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/read`
- `POST /api/v1/servers/{server_id}/operations/xray/config-files/write`
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
NIC enumeration, service logs, agent-side scan, and Xray config validation. The
service-control wrapper only accepts the active agent's `xray` and `nginx`
targets with `start`, `stop`, or `restart`; the logs wrapper clamps requests to
the agent-supported `1..2000` line range before building the child query.

Config wrappers cover the active agent's Xray and nginx main config and
`config-files` routes. Control-plane requests serialize structured Xray JSON
into the text shape the agent expects, validate obvious path and URL hazards,
and preserve the agent-side write/test/reload behavior. Agent setting wrappers
cover Xray mode, listen port, master URL probe/update, and WARP credential
updates without changing the Open Node no-license contract.

High-level workflow wrappers cover active agent inbound, outbound, routing,
batch apply, certificate deployment, nginx SSL setup, nginx website inventory
and deletion, return-route testing, website validation, and embedded limiter
configuration. Routing manage requests preserve the agent's camel-case
`burstObservatory` field while keeping the Open Node API typed and explicit.

The frontend exposes these wrappers in a dedicated `/config` workspace. It can
queue Xray and nginx read/write operations, load completed read results back
into editors, manage config-file read/write calls, dispatch high-level runtime
and site payloads, and inspect each command's request, result body, error, and
stream frames.

## Subscription Catalog

Open Node keeps the MMWX subscription workflow as first-party product data
without licensing or entitlement checks. The backend stores product users,
managed nodes, and subscription plans in SQLite and exposes them through:

- `GET /api/v1/users`
- `POST /api/v1/users`
- `GET /api/v1/nodes`
- `POST /api/v1/nodes`
- `GET /api/v1/plans`
- `POST /api/v1/plans`
- `POST /api/v1/users/{username}/plan`

Managed nodes link Open Node catalog records to server inventory records. They
can hold inbound tags, routed outbound or rule markers, tags, opaque config,
and a JSON client template. The template supports simple placeholders such as
`{username}`, `{node_name}`, and `{server_name}` so plan assignment can prepare
the same `inbound_clients` and `routing_user_additions` batch body expected by
the active `mmw-agent` `/api/child/batch-apply` route.

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
- `GET /api/v1/subscribe/{token_or_short_code}`

The public subscription endpoint renders a minimal Clash-compatible YAML file
from managed node proxy configs, injects the user's stored credential into each
proxy, includes a select group, and emits the standard `subscription-userinfo`
header from the latest telemetry available for that user's credential emails.
This is intentionally smaller than the original MMWX template/conversion
system, but it establishes the durable token and credential boundary.

The frontend exposes this in `/subscriptions`, where operators can create users,
catalog nodes, plans, assignments, public links, and generated credentials, then
inspect the last calculated batch before or after dispatching it.

## Public Probe API

Open Node exposes the read-only public probe surface without authentication or
license gates. The primary endpoints are:

- `GET /api/v1/public/probe-servers`
- `GET /api/v1/public/probe-series`
- `GET /api/v1/public/probe-ws`

For compatibility with the `mmwx-probe` Worker route mapping, the same handlers
are also mounted at:

- `GET /api/public/probe-servers`
- `GET /api/public/probe-series`
- `GET /api/public/probe-ws`

Probe responses are built from persisted agent telemetry snapshots. The server
list exposes only public status, speed, resource, traffic, and latency fields;
internal identifiers, IP addresses, bootstrap tokens, and agent secrets are not
serialized. Series lookups use the public server index from the sanitized list
instead of private server IDs.

The WebSocket stream is also public and read-only. It sends the same
`ProbePayload` structure as the HTTP list endpoint, drops any client messages,
limits concurrent connections in memory, and keeps the no-license response
contract.
