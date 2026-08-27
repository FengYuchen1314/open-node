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
