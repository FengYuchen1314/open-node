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
- Frontend: Vue 3, Vuetify, Vite, TypeScript.
- Probe worker: Cloudflare Worker with Workers Static Assets.
- Repository shape: one monorepo with `backend/`, `frontend/`, `probe-worker/`,
  `docs/`, and `scripts/`.
- Verification target: tests are run on the VPS at `185.99.135.224` over SSH.

## Current Milestone

The first milestone establishes the project skeleton, the no-license contract,
persisted server/agent inventory, agent telemetry and command slices, initial
agent operation, maintenance, diagnostic, and config-preparation wrappers, and
Xray/nginx config plus agent setting wrappers, high-level runtime/site
operation wrappers, a subscription catalog with user-plan binding, optional
agent `batch-apply` provisioning, public subscription links with generated
per-user credentials, Clash/sing-box/URI subscription rendering, durable
per-user traffic ledgering, rollback-friendly multi-server change sets plus a
routed-outbound change-set planner, and subscription template presets including
Xray fork AnyTLS/Snell/Mieru coverage,
catalog import/export workflows, quota status plus traffic reset automation,
and the customizable public probe surface with
region/provider/renewal metadata, daily traffic aggregation, and public insight
views plus return-route summaries from agent test results, scheduled probe task
dispatch, domain-latency result ingestion, cross-node target comparison, and
external Xray takeover for legacy `-config` plus `-confdir` nodes, plus latest
agent scan-result persistence for Xray runtime status and inbound inventory
with sanitized runtime summaries, managed-node drafts, and runtime/catalog
reconciliation plus stale-node public-field sync and credential email drift
checks plus missing-client repair and extra-client cleanup dispatch with
follow-up runtime scans, agent
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
the inventory table, runtime inventory summaries in the config workspace,
runtime-to-managed-node creation, missing-node bulk import, and catalog drift
status plus stale-node sync and credential drift controls, log-file and nginx
stream cleanup controls, missing runtime client repair and extra runtime client
cleanup dispatch with follow-up scans, a change-set
dispatch/rollback workspace with a routed-outbound planner form, and an
expandable command-result inspector. It
does not yet replace the full MMWX product.

```text
backend/   FastAPI app, no-license API, inventory, telemetry, scan results, commands, changes, subscriptions, probe
frontend/  Vue 3 + Vuetify shell, server, config, change, subscription, command, and probe views
probe-worker/  Cloudflare Worker for the standalone public probe surface
docs/      migration and architecture notes
scripts/   VPS test runner
```

## Local Development

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
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

The remote runner installs backend and frontend dependencies, runs backend
tests, runs frontend tests, builds the frontend, and type-checks the probe
worker on the VPS.
