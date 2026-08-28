# Testing

All tests for Open Node run on the VPS at `185.99.135.224` over SSH. Local work
is limited to editing and static inspection.

## Remote Test Command

From Windows PowerShell in the repository root:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The script pushes the named local branch, records its exact commit and uses
the default SSH key for `root@185.99.135.224`. The VPS needs Python 3.11+ and Git
before the first call. It clones into a missing/empty target or fast-forwards
an existing clean checkout with the matching origin and branch. Local edits,
untracked files, divergence, symlinked paths, incoming ignored-file conflicts,
and a remote branch that moved after the push stop the update. Nothing is
reset or recursively removed. Uncommitted local Windows edits are not tested.

The default target is `/opt/open-node`; `-RemoteDir` can select a direct,
non-hidden child. Use a separate checkout for tests when the default directory
serves a live process. This helper does not stop services or back up databases;
follow [deployment.md](deployment.md) for production upgrades. The script then
bootstraps the Debian test host (unless `-SkipBootstrap` is set) and runs:

1. Python venv and Node.js bootstrap;
2. backend dependency installation;
3. backend pytest suite;
4. independent-agent dependency installation, Ruff, pytest, and wheel build;
5. frontend dependency installation;
6. frontend Vitest suite;
7. frontend production build;
8. probe Worker dependency installation and TypeScript checks.

The checkout safety tests use disposable local Git repositories on the VPS.
For the actual PowerShell-to-SSH path, run:

```bash
python3 scripts/vps/smoke-sync-and-test.py --pwsh /path/to/pwsh
```

This root-only fixture starts its own loopback `sshd`, generates temporary
client/host keys, and uses strict host-key checking. It verifies quoted branch
and repository names, the exact tested revision, bootstrap selection, and
preservation of a dirty checkout. It uses fixture bootstrap/test commands to
check the launch contract, not as a substitute for the application suites.
It does not change the existing SSH daemon, authorized keys or live checkout;
its temporary direct-child checkout and SSH files are removed on exit.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```

## Control Plane Deployment Smoke

On the designated VPS, with Docker Compose and a trusted Nginx binary:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser]'
backend/.venv/bin/playwright install --with-deps chromium
AGENT_ENV="$(mktemp -d /tmp/open-node-package-agent.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install agent/dist/open_node_agent-0.1.0-py3-none-any.whl
OPEN_NODE_IMAGE_TAG=local OPEN_NODE_REVISION="$(git rev-parse HEAD)" \
  docker compose --env-file /dev/null -f deploy/compose.yaml build
backend/.venv/bin/python scripts/vps/smoke-control-plane.py \
  --image-tag local \
  --agent-python "$AGENT_ENV/bin/python" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-package-shots
```

Build the Agent wheel with the normal test runner first. The image tag must
identify the image built from the checkout under test.
This smoke uses the shipped Compose file and HTTPS proxy template. It creates
randomized projects with loopback-only ports, private named volumes, a local
TLS identity, and a private Nginx prefix. No public CA, DNS account, host
certificate store, production service, or existing volume is modified.

It verifies non-root/read-only runtime restrictions, an empty installation,
administrator creation and recovery, Secure/HttpOnly/SameSite cookies,
Origin/CSRF rejection, SPA route reloads, API/static-file boundaries, and an
actual WSS probe stream. It then checks session, inventory, and encrypted-key
persistence after container/network recreation, a stopped-volume backup
restored into a new project, a changed-image upgrade, and explicit rollback
after a deliberately broken release fails startup. Temporary candidate images
and owned volumes are removed afterward. No arbitrary future database
downgrade, multi-host deployment, or zero-downtime upgrade is claimed.

The installed Agent also connects through HTTPS/WSS using only the fixture
CA, with TLS verification enabled. The full real-Xray forwarding, client
provisioning/revocation, failed-restart rollback, config recovery, telemetry,
and persistent-deduplication smoke runs on both transports against the
container. It uses the pinned Xray archive documented below; the optional
`--xray-archive` argument reuses a copy without bypassing its checksum.

The full operator browser smoke runs against the production image through
HTTPS at desktop 1440x900 and mobile 390x844. HTTP and WSS clients validate the
fixture certificate and hostname. Chromium allows only the generated fixture
SPKI via its per-process test switch, not a blanket TLS bypass. Screenshots
remain at `--output`; fixture credentials are not written there.

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

## Remote Agent Lifecycle

Build the Agent wheel and production Vue assets first. On the designated VPS,
with the browser/cryptography dependencies and a trusted Nginx binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-lifecycle.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-agent-lifecycle-shots
```

The fixture uses separate HTTPS release and controller endpoints with explicit
local CA trust. Both native transports perform version/digest-pinned upgrades,
rollback, wrong-digest rejection, failed-preflight/start recovery, and actual
VLESS forwarding after changes. Mismatched wheel metadata and redirects outside
the host-approved source are rejected. Unix socket ownership and a foreign-UID request
test cover both filesystem permissions and the peer-credential boundary.

One-shot candidate-wheel pauses allow the test to kill the maintenance cgroup
during package staging and service switching. It checks persisted recovery,
unchanged configuration, old-version traffic, explicit interrupted results,
request deduplication, expired-lease redelivery, skipped dependent commands, and
a new explicit retry after staging recovery. A paused shutdown verifies recovery
from a crash during removal, before the Agent service finishes stopping.
Final uninstall reports are temporarily rejected by the fixture proxy, proving
the controller cannot claim completion before acknowledgment. Worker restart,
eventual reporting, worker shutdown and data-preserving reinstall are checked.

The browser checks explicit version/SHA input, confirmation, actual command
completion and resumed progress at 1440, 390 and 320 pixel widths. It also reopens
the uninstall dialog while the Agent is gone but its callback is still blocked,
and waits for the actual acknowledgment before displaying completion. Chromium
trusts only the fixture SPKI; the Agent and host downloader use normal TLS
verification. Screenshots remain in `--output` without fixture credentials.

After publishing the matching wheel, verify the actual default GitHub release
source separately, using that exact release artifact on the designated VPS:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-release.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --nginx /path/to/extracted/usr/sbin/nginx
```

This performs real public release downloads without a test mirror, checks the
wheel pin and running release identity, sends VLESS traffic and rolls back on
both transports. Its controller remains a private trusted HTTPS fixture.

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

## Atomic Tunnel Smoke

Use the same binary/module fixtures and built Agent wheel as the Nginx smoke:

```bash
backend/.venv/bin/python scripts/vps/smoke-tunnel.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

For each transport, this installs a fresh non-root systemd Agent and exercises
the real FastAPI tunnel planner and queue. It verifies fresh deployment without
prior Nginx installation, hostname-verified TLS SNI routing to static and proxy
sites, unmatched SNI reaching a fixed loopback TLS fallback, actual traffic
statistics, post-deployment snapshot refresh, stale-template rejection,
Nginx/Xray occupied-listener rollback, and owned stream-to-Xray listener
handover while preserving a neighboring stream server. It injects a durable
multi-file undo record with conflicting stored start intentions, restarts the
Agent, and verifies both running and intentionally stopped recovery. A failed
cold deployment must leave both services stopped. Unit tests also cover
command cancellation, corrupt intent records, and idempotent map merging.

This verifies official Xray v26.3.27 on Debian 12 x86-64, not an arbitrary
future Xray schema, zero-downtime switching, or fork-specific protocol support.

## ACME Lifecycle Smoke

On the same VPS, install the test-only DNS fixture dependency:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser,acme-test]'
```

Supply the verified lego v4.35.2 binary described in
[certificate setup](certificates.md#host-setup), and the
[Pebble v2.6.0 release](https://github.com/letsencrypt/pebble/releases/tag/v2.6.0).
The tested `pebble-linux-amd64.tar.gz` archive has SHA256
`ce5d87e1f674934c134b7cbcbc468e3df420994a17e77bdbf7aec611e2d373b9`.
Verify before extraction; the Pebble binary needs executable permission.

```bash
backend/.venv/bin/python scripts/vps/smoke-certificates.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

This root-accessible systemd test needs free UDP/TCP port 53 on `127.0.0.1`
and `::1`. It binds exclusively and fails instead of replacing an existing
listener. Existing DNS services on other addresses remain untouched; neither
`/etc/hosts` nor `/etc/resolv.conf` is modified. The fixture's authoritative
NS is `localhost`, keeping lego's OS-level NS address lookup offline. ACME,
webhook, backend, Agent and Nginx listeners are all loopback-only.

The test does real DNS ownership validation, not Pebble's always-valid mode.
It verifies HTTPS CA trust, EAB account creation, apex plus wildcard SANs,
TXT presentation and cleanup, not-due skips, credential rejection retaining
the active certificate, forced renewal, backend restart persistence, and
actual elapsed-time automatic renewal of four-minute certificates. Real
non-root Agent services then deploy/reload the certificate and restore a
historical version, checking trusted TLS leaf serials and HTTP bytes for
both transports. Test services, accounts, DNS listeners and private state
are removed on completion. No public CA or real DNS account is used.

## Reference-Agent Smoke

After installing the backend development dependencies, run this on the VPS
with Docker available:

```bash
docker pull ghcr.io/iluobei/mmw-agent@sha256:d9ff8cd1525947e1e535ca49d6b22f1b63ff28d393c46efea6f88eeb40e8840d
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py --secure-channel
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
is unchanged on disk. With `--secure-channel`, it also verifies rejection of
wrong and malformed pins before registration, encrypted round trips, and fresh
encrypted sessions after controller restart with the same stored identity.
Both modes run in external Xray mode without a live Xray process. They do not
prove forwarding traffic, embedded runtime behavior or legacy HTTP callbacks.
They do not make the reference image the distributable Open Node agent.

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

Certificate coverage also creates a DNS provider and profile, requires explicit
CA terms, imports a real PEM pair, downloads certificate/private key separately,
verifies secret fields clear on reopening, and checks desktop/mobile forms.
Private keys and provider credentials must not appear in browser storage.

The Access page also verifies the configured Agent public key/fingerprint,
native clipboard copy and desktop/mobile layout. The disposable browser fixture
creates its own private identity. The production-image smoke creates the seed
with the non-root container CLI and verifies refusal to overwrite it, private
permissions and identity preservation through container recreation and volume
backup/restore; its HTTPS browser run checks the same public metadata.

The reference-agent smoke also creates a temporary administrator and signs in
as the operator; the reference agent still authenticates only with its own
bootstrap token. No test disables management authentication.

## Managed Xray Release Smoke

With the backend's browser extra/Chromium and a built Agent wheel on the VPS:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-releases.py \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --output /tmp/open-node-xray-release-shots < /dev/null
```

This root-only fixture installs dedicated non-root systemd Agents and uses
official Xray `v26.2.6` and `v26.3.27` archives. Each transport verifies real
version changes, process executable paths, actual VLESS forwarding, checksum
rejection, validation before stopping the old runtime, and geodata discovery.
It checks untouched root-owned bootstrap binaries and unchanged user config.

The ordinary wheel is exercised first. A separate fixture-only wheel then
supplies deterministic occupied-port and interruption faults while retaining
the real Xray binaries. The smoke verifies failed-start rollback, timeout
recovery, process-group crash recovery, an explicit interrupted-command result,
and restoration of the ordinary Agent wheel. Removal/reinstallation preserve
configuration and stopped intentions. Desktop/mobile browser checks submit
real version/checksum requests and require acknowledgment before rollback.
Temporary installations/accounts are purged; requested screenshots remain.

Unit coverage also checks archive/path/size boundaries, cached file integrity,
version mismatch, initial missing config, no-op reinstall preserving rollback,
unresolved transaction rejection and removal with a damaged config. See
[xray-releases.md](xray-releases.md) for ownership and recovery semantics.

## Multi-Node Change-Set Smoke

On the designated VPS, build the frontend, install the backend's `browser`
extra and Chromium, and install the Agent wheel into a separate environment.
Then run:

```bash
backend/.venv/bin/python scripts/vps/smoke-change-sets.py \
  --agent-python "$AGENT_ENV/bin/python" \
  --output /tmp/open-node-change-artifacts < /dev/null
```

This uses the same pinned official Xray archive as the independent-agent smoke
and accepts `--xray-archive` for a checksum-verified local copy. It starts an
authenticated disposable FastAPI controller with the production frontend,
two installed Agents and real VLESS traffic for WebSocket/WebSocket, HTTP/HTTP
and mixed transport pairs. Temporary gates verify forward ordering, reverse
rollback ordering, cancellation while a forward command is executing, and
automatic compensation after native Xray validation fails. Bootstrap and
newly provisioned client traffic are checked before and after recovery.

The mixed pair also exercises the real Vue rollback-failure/retry workflow,
retained command history, incomplete compensation, and explicit acceptance
with a required reason and checkbox on desktop and mobile. Layout failures
retain screenshots and element-bound diagnostics. Temporary processes and
private state are removed; only requested artifacts remain. Unit tests cover
lease races, overlapping reservations, draining earlier sequences, late
rollback rejection, restart persistence and missing-column SQLite migration.

## Latest Verification

The managed Xray release worktree passed on the designated VPS:

- Backend: 252 tests; Agent: 110 tests; frontend: 87 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real non-root systemd Agents changed between official Xray v26.2.6 and
  v26.3.27 over WebSocket and HTTP. Tests checked actual executable paths,
  VLESS forwarding, archive geodata, untouched root-owned bootstrap files,
  unchanged user configuration and checksum/validation failures.
- Fixture-only faults verified occupied-port rollback, command timeout,
  process-group crash recovery and an explicit interrupted-command result.
  Agent wheel rollback retained the selected runtime. Removal, stopped
  reinstallation and explicit service start preserved configuration.
- Installed-Agent forwarding, provisioning, revocation, failed-start recovery,
  journal deduplication and stop-intent checks passed again on both transports.
- Host service upgrade/rollback/removal, real Nginx HTTP/TLS and certificate
  rotation, atomic tunnel recovery and all three multi-node transport pairings
  passed again. Fixture installations and accounts were removed afterward.
- Desktop 1440x900, mobile 390x844 and narrow 320x740 release dialogs submitted
  real version/checksum requests, displayed the complete checksum and required
  acknowledgment before rollback. Each change was followed by real forwarding.
- The production image passed HTTPS/WSS, installed-Agent forwarding, private
  identity and session persistence, volume backup/restore and image rollback.
  Its operator flow also verified the complete product name at 320px after
  compacting the edition badge; desktop/mobile screenshots were inspected.
- The unmodified pinned reference Agent passed encrypted authentication,
  controller restart, config refresh, drift acceptance and validation-gated
  recovery again.

These results do not close the remaining gates in
[migration-map.md](migration-map.md), including remote Agent lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Encrypted-Agent Verification

The encrypted-Agent and safe-sync worktree passed on the designated VPS:

- Backend: 242 tests; Agent: 86 tests; frontend: 84 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- The unmodified pinned reference Agent passed both plaintext compatibility
  and encrypted WebSocket auth, config writes, refresh, controller/Agent
  restart and recovery. Wrong pins and malformed-pin plaintext fallback were
  rejected without registering the Agent or issuing work.
- Replay/tamper/direction/sequence checks, handshake deadlines, private-key
  files, concurrent send order, UTF-8/finite JSON and oversized historical
  command handling passed. Attempted historical work is not falsely completed.
- The production image passed HTTPS/WSS, real Xray forwarding on both native
  transports, private identity creation, non-overwrite, recreation and volume
  restore. Public key/fingerprint display and real clipboard copy passed in
  the desktop/mobile operator flow; screenshots were inspected.
- The real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport smoke
  passed again, including reverse compensation, in-flight cancellation and
  the desktop/mobile retry and explicit-acceptance workflows.
- The sync launcher passed real loopback SSH with key authentication and
  PowerShell 7.6.5 on Linux. Git fixtures verified non-destructive refusal of
  dirty/diverged/wrong-origin/wrong-branch checkouts and ignored-file conflicts.
  Windows PowerShell itself was not executed because tests run only on the VPS.

These results do not close the remaining runtime gates in
[migration-map.md](migration-map.md), including remote runtime lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Change-Set Verification

The coordinated change-set worktree passed on the designated VPS:

- Backend: 189 tests; Agent: 86 tests; frontend: 82 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport changes
  verified ordered execution, actual client forwarding, reverse compensation,
  cancellation in flight and automatic recovery after native validation failure.
- Desktop/mobile browser flows verified compensation retry, expanded command
  results, retained history, required acceptance reason/acknowledgment and live
  status. A deliberately delayed list response cannot overwrite a newer action.
- Independent installed-Agent and pinned reference-Agent smokes passed again,
  including snapshot refresh, validation-gated recovery and persistent journal
  behavior. No reference source is needed by the independent Agent.
- Missing-column SQLite upgrades preserve old command outcomes and pause legacy
  execution for review, including concurrent ordinary dependency sequences.

These results do not close the other runtime gates in
[migration-map.md](migration-map.md). Existing Starlette/httpx deprecation and
frontend bundle-size warnings remain.

## Earlier Certificate Verification

Certificate management was verified on the designated VPS:

- Backend: 167 tests; Agent: 86 tests; frontend: 77 tests and production build.
- Probe Worker type checks and Ruff passed.
- Real Pebble DNS-01/EAB, wildcard issuance, automatic and forced renewal,
  restart persistence, failure preservation, and trusted TLS/version rollback
  passed over both Agent transports.
- Browser certificate forms, terms confirmation, secret clearing and explicit
  PEM downloads passed on desktop and mobile; screenshots were inspected.
- Installed Agent, systemd lifecycle, Nginx, tunnel and reference-agent smokes
  passed again. No public-CA orders or real DNS credentials were used.

Public-provider staging and the remaining migration gates are not covered by
these results. Existing deprecation and bundle-size warnings remain.

## Previous Verification

On 2026-08-27 (UTC), the atomic-tunnel worktree passed on the VPS:

- Backend: 153 tests, including HTTP/WebSocket Nginx scan reporting, legacy SQLite
  scan-schema migration, anonymous management-route rejection, session
  persistence/expiry/revocation, CSRF/Origin rejection, concurrent login limiting,
  a password-reset/login race, administrator CLI recovery, and the existing
  inventory, dependency, migration, subscription, and change-set suites. Native
  tunnel coverage checks profile/capability selection, snapshot prerequisites,
  listener validation, generated paths/config, and post-deploy refresh.
- Independent agent: 86 tests, including private state/lock protection, TLS
  configuration, persistent deduplication, transport reconnects, heartbeats
  during commands, interrupted execution, bounded errors/subprocesses, atomic
  rollback, client edits, stop intent, network rate calculation, deployment
  ownership/path guards, package identity, activation recovery, and readiness checks.
  New coverage includes certificate matching/SAN/dates, file-boundary enforcement,
  include parsing/cycles, multi-file rollback, command cancellation, interrupted-file
  recovery, exact stream cleanup, separate stop intent, and master PID reuse guards.
  Coupled tunnel tests cover fresh files, map merging, stale snapshot rejection,
  start/cancellation rollback, durable file/intent recovery, invalid metadata,
  loopback stats discovery, and dynamic path rejection.
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
- Native tunnel: both transports with the real planner, queue, and installed
  wheel; verified TLS static/proxy/fallback bytes, traffic reporting, stale hash
  rejection, both runtime port conflicts, owned listener handover, and recovery
  of files plus running/stopped intentions. Failed cold deployment leaves no
  unwanted running service.
- Frontend: 74 tests, including session/CSRF request handling, expired-session
  transitions, waiting/skipped Vuetify component rendering, and the production build.
- Probe Worker: TypeScript checks.
- Ruff: backend, independent agent, and all six smoke scripts.
- Reference-agent smoke: all ten stages, with the pinned image.
- Chromium operator smoke: desktop 1440x900 and mobile 390x844 sign-in/access,
  server creation, reload persistence, password change, logout, and expiry. Nginx
  form defaults, page/control bounds, and full visibility of the active tab are
  also checked on both viewports, with configuration screenshots. Tunnel form
  checks cover default node-owned paths, duplicate/out-of-range port rejection,
  real request payloads, and single-line toggle text. Desktop and mobile
  screenshots were inspected after fixing the narrow desktop toggle layout.

The backend test run still reports a Starlette/httpx deprecation warning, and
the frontend build reports a large bundle warning. Neither is a failed check.
