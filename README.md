# Open Node

Open Node is a single-repository refactor of the MMWX stack. The target is a
free-to-use server and subscription management system with no activation keys,
paid entitlement checks, commercial license server calls, or feature gates.

## Scope

This repository is the new implementation home for the active MMWX line:

- `miaomiaowuX`: control plane behavior reference.
- `mmw-agent`: remote server agent behavior reference.
- `mmwx-probe`: standalone public probe behavior reference.
- `Xray-core-mmwx`: Xray fork integration reference for agent/runtime work.

The older `miaomiaowu` project and the archived `NodeControll` rebuild are
intentionally out of scope for this refactor.

## Stack

- Backend: FastAPI on Python 3.11+.
- Agent: independent Python 3.11+ Linux package with an operator-provided Xray runtime.
- Frontend: Vue 3, Vuetify, Vite, TypeScript.
- Probe worker: Cloudflare Worker with Workers Static Assets.
- Repository shape: one monorepo with `backend/`, `agent/`, `frontend/`, `probe-worker/`,
  `docs/`, and `scripts/`.
- Verification target: tests are run on the VPS at `185.99.135.224` over SSH.

## Administrator Access

Management APIs require a local administrator session. There is no default
password or activation key. Create the account with `open-node-admin create`
using the same database configuration as the backend, then sign in through
the Vue interface. [Administrator setup and recovery](docs/administrator-access.md)
also covers HTTPS cookies, local previews, session expiry, and API clients.

## Deployment

The root Dockerfile and [Compose deployment](docs/deployment.md) build a
single non-root image containing FastAPI, the Vue production frontend, and
pinned lego. The deployment guide covers HTTPS, administrator initialization,
private persistent storage, backup/restore, upgrades, and explicit rollback.
No development server is needed. The actual Compose setup has been exercised
on the VPS with HTTPS desktop/mobile browser workflows and volume recovery.
Remaining migration gates still apply; this is not yet full MMWX parity.

## Current Milestone

Existing encrypted MMWX Agents have a [legacy migration path](docs/legacy-agent-migration.md)
with pinned controller identity, encrypted WebSocket RPC and explicit key custody.

The independent [Open Node Agent](agent/README.md) now handles WebSocket and
HTTP control connections, durable command execution, host telemetry, and Xray
configuration and client management without activation or license checks.
Its built wheel has been exercised against real Xray forwarding on the VPS,
including newly provisioned users, failed-restart rollback, and restart-safe
command deduplication. The [host deployment CLI](docs/agent-deployment.md) adds
a dedicated systemd account, per-release environments, upgrade rollback,
interrupted-switch recovery, and data-preserving uninstall/reinstall.
Managed [Xray release operations](docs/xray-releases.md) add checksum-pinned
installation, version switching, explicit rollback and data-preserving removal.
They preserve the root-owned bootstrap binary and recover the prior runtime
after a failed start, command timeout or interrupted switch.
Optional [Nginx and certificate management](docs/nginx-management.md) supports
owned HTTP/TLS sites, supplied certificate rotation, and atomic Nginx/Xray
tunnel deployment with configurable listeners and failure recovery.
[Remote Agent lifecycle](docs/agent-lifecycle.md) adds host-approved package
upgrades, rollback and data-preserving removal through a separate privileged
helper, with verified release pins and durable final results.
WARP and fork-specific runtime workflows are still incomplete; control-plane wrappers
for those operations do not imply the independent agent implements them.

[Central certificate management](docs/certificates.md) now provides DNS
credentials, PEM import/export, DNS-01 and HTTP-01 issuance and renewal through pinned
lego v4 and Certbot ACME, EAB, encrypted version history, and automatic Agent deployment.
The VPS smoke uses a real Pebble test CA and authoritative DNS, including
short-lived automatic renewal and trusted TLS/version rollback on both
Agent transports. HTTP-01 supports a standalone listener or an allowlisted
webroot on the control plane or a host-enabled validation node. Remote validation
retains central accounts/keys, durable orders and acknowledged challenge cleanup.
Account contact editing and exact-version
revocation include crash reconciliation and persistent duplicate protection.
Public-CA/provider-account staging remains a separate gate.

The first milestone establishes the project skeleton, the no-license contract,
persisted server/agent inventory, agent telemetry and command slices, initial
agent operation, maintenance, diagnostic, and config-preparation wrappers, and
Xray/nginx config plus agent setting wrappers, high-level runtime/site
operation wrappers, a subscription catalog with user-plan binding, optional
agent `batch-apply` provisioning, public subscription links with generated
per-user credentials, Clash/sing-box/URI subscription rendering, durable
per-user traffic ledgering, [coordinated multi-server change sets](docs/change-sets.md) plus a
routed-outbound change-set planner, and subscription template presets including
Xray fork AnyTLS/Snell/Mieru coverage,
catalog import/export workflows, quota status plus traffic reset automation,
and the customizable public probe surface with
region/provider/renewal metadata, daily traffic aggregation, and public insight
views plus return-route summaries from agent test results, scheduled probe task
dispatch, domain-latency result ingestion, cross-node target comparison, and
external Xray takeover for legacy `-config` plus `-confdir` nodes, plus latest
agent scan-result persistence for Xray runtime status and inbound inventory
with sanitized runtime summaries plus latest traffic counters and
snapshot-derived tunnel inventory/delete dispatch, managed-node drafts, and
runtime tunnel chain planning/queueing across ordered servers,
tunnel-mode deployment planning/queueing for Nginx plus Xray,
runtime/catalog
reconciliation plus stale-node public-field sync and credential email drift
checks plus missing-client repair and extra-client cleanup dispatch with
follow-up runtime scans, Xray config pending-recovery decisions and
post-mutation snapshot refreshes, first-connect/reconnect config synchronization
with WebSocket queue delivery, agent
command dependencies that stop recovery/deployment sequences on failure,
log-file listing/cleanup, nginx stream-port cleanup, and
compatibility wrappers for non-stream Xray/nginx install/remove agent routes,
plus optional standalone probe Worker token access for hiding direct public
probe endpoints.
The Vue frontend now includes a config workspace, runtime/site operation
payload workbenches, subscription catalog, link, format, traffic, quota,
preset, and import/export management, probe settings, metadata, and task
controls, probe status/region filters, health scoring, latency buckets,
traffic hotspots, three-carrier route badges, probe network drill-down charts,
target comparison rows, external Xray takeover controls, Xray scan status in
the inventory table, runtime inventory summaries, tunnel lists, and tunnel
delete/create-chain/deploy controls in the config workspace, latest runtime traffic counters,
runtime-to-managed-node
creation, missing-node bulk import, and catalog drift status plus stale-node
sync and credential drift controls, log-file and nginx
stream cleanup controls, Xray snapshot recovery controls,
missing runtime client repair and extra runtime client
cleanup dispatch with follow-up scans, a change-set
dispatch/rollback workspace with a routed-outbound planner form, and an
expandable command-result inspector. It
does not yet replace the full MMWX product.

```text
backend/   FastAPI app, no-license API, inventory, telemetry, scan results, commands, changes, subscriptions, probe
agent/     Independent Linux agent, persistent command journal, owned/systemd Xray runtime
frontend/  Vue 3 + Vuetify shell, server, config, change, subscription, command, and probe views
probe-worker/  Cloudflare Worker for the standalone public probe surface
docs/      migration and architecture notes
scripts/   VPS test runner
deploy/    Control-plane Compose environment and HTTPS proxy example
```

## Local Development

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
open-node-admin create --username admin
export OPEN_NODE_SESSION_COOKIE_SECURE=false  # Loopback HTTP development only.
uvicorn open_node.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## VPS Tests

After pushing a branch to GitHub, run all tests on the VPS:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The remote runner installs dependencies, runs backend, independent-agent, and
frontend tests, builds the agent wheel and frontend, and type-checks the probe
worker on the VPS.

An additional isolated reference-agent smoke test verifies the real
`mmw-agent` JSON WebSocket path, config writes, automatic snapshot refreshes,
reconnect recovery, and validation failure preventing writes or restarts.
An independent-agent smoke installs the built wheel into a separate environment
and verifies real VLESS traffic, provisioning, telemetry, rollback, recovery,
and restart persistence over both WebSocket and HTTP. The pinned runtime,
VPS commands, and coverage limits
are documented in [docs/testing.md](docs/testing.md). Outstanding runtime and
release gates are recorded in [docs/migration-map.md](docs/migration-map.md).
