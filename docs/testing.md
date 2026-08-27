# Testing

All tests for Open Node run on the VPS at `185.99.135.224` over SSH. Local work
is limited to editing and static inspection.

## Remote Test Command

From Windows PowerShell in the repository root:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The script uses the default SSH key for `root@185.99.135.224`, checks out the
GitHub repository into `/opt/open-node`, bootstraps the Debian test host, and
runs:

1. Python venv and Node.js bootstrap;
2. backend dependency installation;
3. backend pytest suite;
4. frontend dependency installation;
5. frontend Vitest suite;
6. frontend production build;
7. probe Worker dependency installation and TypeScript checks.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```

## Reference-Agent Smoke

After installing the backend development dependencies, run this on the VPS
with Docker available:

```bash
docker pull ghcr.io/iluobei/mmw-agent@sha256:d9ff8cd1525947e1e535ca49d6b22f1b63ff28d393c46efea6f88eeb40e8840d
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py
```

The script uses the unmodified `mmw-agent` 0.4.7 image pinned by digest. It
creates a private, internal Docker network, a temporary SQLite database and
config directories, and a backend listener on that bridge with an ephemeral
port. The agent has no host-network access, published ports, or host config
mounts. Container capabilities are dropped. Only disposable files are
modified, and the container, network, and backend are removed when it exits.

The smoke verifies actual `/api/remote/ws` authentication, the initial config
snapshot, an agent-validated config write, the automatic WebSocket refresh
and its returned config, restart-induced drift, and manual acceptance of the
pending config. It also checks sequential recovery validation/write and the
failure path: when the real agent returns HTTP 200 with `ok=false`, neither
the write nor restart is attempted and a previously repaired healthy config
is unchanged on disk. It runs in external Xray mode without a live Xray process or
key-exchange configuration. It does not prove forwarding traffic, embedded
runtime behavior, or migration from an encrypted MMWX connection. It also
does not make the reference image the distributable Open Node agent.

## Latest Verification

On 2026-08-28, the dependent-command worktree passed on the VPS:

- Backend: 128 tests, including lease/result contention, dependency persistence,
  old SQLite schema migration, and validation failure stopping later commands.
- Frontend: 65 tests, including waiting/skipped Vuetify component rendering,
  and the production build.
- Probe Worker: TypeScript checks.
- Ruff: backend and reference-agent smoke script.
- Reference-agent smoke: all ten stages, with the pinned image.

The backend test run still reports a Starlette/httpx deprecation warning, and
the frontend build reports a large bundle warning. Neither is a failed check.
