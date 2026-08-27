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
4. independent-agent dependency installation, Ruff, pytest, and wheel build;
5. frontend dependency installation;
6. frontend Vitest suite;
7. frontend production build;
8. probe Worker dependency installation and TypeScript checks.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```

## Independent-Agent Smoke

After the normal test runner, install the built wheel into a separate environment
and run the real-runtime smoke on the VPS (Linux x86-64, Python 3.11+, and curl):

```bash
AGENT_ENV="$(mktemp -d /tmp/open-node-agent-wheel.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install agent/dist/open_node_agent-0.1.0-py3-none-any.whl
backend/.venv/bin/python scripts/vps/smoke-open-node-agent.py --agent-python "$AGENT_ENV/bin/python"
```

The smoke downloads the official
[Xray v26.3.27 Linux 64-bit release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)
and verifies the archive SHA-256
`23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae`.
`--xray-archive /absolute/path/Xray-linux-64.zip` can reuse a downloaded archive;
the same digest check is mandatory. It extracts only the binary into a private
temporary directory, never installs over a host Xray binary, and deletes its
runtime fixtures on completion. The separate wheel environment remains available
for inspection. No MMWX image or activation server is involved.

For each transport (WebSocket and HTTP), the test starts disposable FastAPI,
Agent, Xray server/client, and HTTP fixture processes. All listeners use loopback
and ephemeral ports. It checks actual SOCKS-to-VLESS forwarding, new-client
provisioning and revocation without removing other users, per-user traffic
reporting, invalid config rejection with protocol-sized error messages, failed
runtime restart with file/traffic rollback, recovery test/write/restart, and
persistence of users and stop intent across Agent restarts. Redelivery is
simulated by requeuing one completed non-idempotent command only in the fixture
database; a second execution would fail, so a cached successful result proves
restart deduplication. Owned process groups are terminated on exit.

This proves the managed official-Xray VLESS path, not every protocol, encrypted
legacy-agent migration, systemd mode, or host install/upgrade/uninstall lifecycle.

## Agent Service Lifecycle

After building the Agent wheel, run the following on the designated VPS as root:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-service.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl
```

This requires a running systemd manager plus `useradd`, `runuser`, and curl.
It uses the same pinned official Xray archive as the independent-runtime smoke;
`--xray-archive` can reuse that archive without skipping digest verification.
The fixture creates a uniquely named `open-node-agent-<id>.service`, dedicated
non-login account, and `/opt/open-node-agent-smoke-<id>` directory. It does not
reuse existing MMWX services, tokens, databases, unit names, or install roots.

The test verifies failed first installation and corrected-input retry, non-root
systemd readiness/hardening, real forwarding and runtime edits, successful
upgrade, explicit rollback, failed preflight without stopping the old process,
failed-start rollback, and recovery after forcibly terminating the deployment
process during a recorded switch. It also kills the Agent process to verify
systemd restart and Xray child cleanup, then checks uninstall/reinstall with
config/journal preservation and explicit purge of only owned files/account.

Good and deliberately broken candidate wheels are generated only inside the
test fixture with updated wheel records. They are not published artifacts.
Fixtures are removed at the end; failures print the service journal and report
any cleanup that needs attention. Stopped-Agent upgrades and path/ownership
guards have additional focused unit tests. External `runtime_mode: systemd`
and arbitrary future schema rollback are not covered by this smoke.

## Nginx And Certificate Smoke

On the root-accessible systemd VPS, supply a trusted Nginx binary and matching
stream module. Debian packages can be downloaded and extracted into a disposable
directory with `apt-get download` and `dpkg-deb -x`, without installing a global
service. Install `cryptography` in the smoke runner environment, then run:

```bash
backend/.venv/bin/pip install cryptography
backend/.venv/bin/python scripts/vps/smoke-nginx.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

An optional `--xray-archive` uses the existing pinned-digest Xray fixture. The
test installs a separate non-root Agent service for each transport, then checks
real HTTP, verified TLS, leaf serial rotation, key mismatch rejection, actual
reverse-proxy and stream response bytes, invalid configuration and occupied-port
rollback, exact stream cleanup, private file boundaries, site deletion, logs,
independent stop intent, Agent/Nginx crashes, durable interrupted-file recovery,
and data-preserving uninstall/reinstall. Test certificates are local fixtures;
no public CA or real domain validation is used. Fixture units/accounts and
directories are purged after the run, with existing services untouched.

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

## Operator Browser Smoke

Install the optional browser dependencies and Chromium on the VPS, then run:

```bash
backend/.venv/bin/pip install -e 'backend[browser]'
backend/.venv/bin/python -m playwright install --with-deps chromium
backend/.venv/bin/python scripts/vps/smoke-operator-ui.py --output /tmp/open-node-ui-artifacts
```

The script creates a temporary administrator/database and starts disposable
FastAPI and Vite processes on loopback ports. It checks that private views do
not load before sign-in, rejects an incorrect password, creates a server
through the UI, verifies session persistence across reloads, changes the
password on mobile, checks rejection of the old password, signs out, and
expires a session to verify the UI returns to sign-in. It captures desktop
and mobile login/access screenshots and checks horizontal overflow and form
control bounds. Services and database files are removed on completion; only
the requested screenshots remain. No existing administrator is changed.

The reference-agent smoke also creates a temporary administrator and signs in
as the operator; the reference agent still authenticates only with its own
bootstrap token. No test disables management authentication.

## Latest Verification

On 2026-08-27 (UTC), the Nginx/certificate worktree passed on the VPS:

- Backend: 143 tests, including HTTP/WebSocket Nginx scan reporting, legacy SQLite
  scan-schema migration, anonymous management-route rejection, session
  persistence/expiry/revocation, CSRF/Origin rejection, concurrent login limiting,
  a password-reset/login race, administrator CLI recovery, and the existing
  inventory, dependency, migration, subscription, and change-set suites.
- Independent agent: 74 tests, including private state/lock protection, TLS
  configuration, persistent deduplication, transport reconnects, heartbeats
  during commands, interrupted execution, bounded errors/subprocesses, atomic
  rollback, client edits, stop intent, network rate calculation, deployment
  ownership/path guards, package identity, activation recovery, and readiness checks.
  New coverage includes certificate matching/SAN/dates, file-boundary enforcement,
  include parsing/cycles, multi-file rollback, command cancellation, interrupted-file
  recovery, exact stream cleanup, separate stop intent, and master PID reuse guards.
- Agent wheel: isolated build and installation into a separate environment;
  real Xray smoke passed over WebSocket and HTTP, including provisioning,
  revocation, actual forwarding/statistics, failed-start rollback, recovery,
  restart deduplication, and preserved stop intent.
- Real systemd lifecycle: failed first installation/retry, non-root service
  ownership and forwarding, upgrade/rollback, failed preflight and startup,
  interrupted-switch recovery, crash restart with child cleanup, data-preserving
  uninstall/reinstall, and explicit purge. No fixture units/accounts remain.
- Real Nginx: both transports with non-root Debian Nginx 1.22.1, verified HTTP/TLS,
  leaf serial rotation, key rejection, proxy/stream response bytes, occupied-listener
  rollback, exact stream cleanup, Agent and Nginx master crash recovery, interrupted
  file recovery, site deletion, and data-preserving service removal/reinstallation.
- Frontend: 74 tests, including session/CSRF request handling, expired-session
  transitions, waiting/skipped Vuetify component rendering, and the production build.
- Probe Worker: TypeScript checks.
- Ruff: backend, independent agent, and all five smoke scripts.
- Reference-agent smoke: all ten stages, with the pinned image.
- Chromium operator smoke: desktop 1440x900 and mobile 390x844 sign-in/access,
  server creation, reload persistence, password change, logout, and expiry. Nginx
  form defaults, page/control bounds, and full visibility of the active tab are
  also checked on both viewports, with configuration screenshots.

The backend test run still reports a Starlette/httpx deprecation warning, and
the frontend build reports a large bundle warning. Neither is a failed check.
