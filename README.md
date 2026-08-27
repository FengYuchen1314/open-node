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

- Backend: FastAPI on Python 3.12+.
- Frontend: Vue 3, Vuetify, Vite, TypeScript.
- Repository shape: one monorepo with `backend/`, `frontend/`, `docs/`, and
  `scripts/`.
- Verification target: tests are run on the VPS at `185.99.135.224` over SSH.

## Current Milestone

The first milestone establishes the project skeleton and the no-license
contract as executable tests. It does not yet replace the full MMWX product.

```text
backend/   FastAPI app, system metadata, no-license contract API
frontend/  Vue 3 + Vuetify shell, frontend no-license contract
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
