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
- Repository shape: one monorepo with `backend/`, `frontend/`, `docs/`, and
  `scripts/`.
- Verification target: tests are run on the VPS at `185.99.135.224` over SSH.

## Current Milestone

The first milestone establishes the project skeleton, the no-license contract,
persisted server/agent inventory, agent telemetry and command slices, initial
agent operation, maintenance, diagnostic, and config-preparation wrappers, and
Xray/nginx config plus agent setting wrappers, high-level runtime/site
operation wrappers, a subscription catalog with user-plan binding, optional
agent `batch-apply` provisioning, public subscription links with generated
per-user credentials, Clash/sing-box/URI subscription rendering, durable
per-user traffic ledgering, rollback-friendly multi-server change sets, and
the public probe read-only surface. The Vue frontend now includes a config
workspace, runtime/site operation payload workbenches, subscription catalog,
link, format, and traffic management, a change-set dispatch/rollback
workspace, and an expandable command-result inspector. It does not yet replace
the full MMWX product.

```text
backend/   FastAPI app, no-license API, inventory, telemetry, commands, changes, subscriptions, probe
frontend/  Vue 3 + Vuetify shell, server, config, change, subscription, command, and probe views
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
tests, runs frontend tests, and builds the frontend on the VPS.
