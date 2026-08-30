# Testing

All tests for Open Node run on the VPS at `185.99.135.224` over SSH. Local work
is limited to editing and static inspection.

GitHub also runs the same repository-level language gates on every push and
pull request through `.github/workflows/ci.yml`: backend Ruff/pytest, Agent
Ruff/pytest/wheel build, frontend Vitest plus both the administrator and public
Probe production bundles, and Probe Worker behavior tests/type checking.
Actions are pinned to immutable revisions and receive only read access to
repository contents. This hosted CI is an independent clean checkout check; it
uses the configured Python interpreter under `sudo` for the Agent tests because
host-policy fixtures exercise real file ownership changes to a service UID.
Dependency installation, linting and wheel building remain unprivileged. The
privilege applies only to GitHub's disposable hosted test runner, not deployment.
Hosted CI does not replace the root-only Docker installer smoke, systemd lifecycle
smokes, protocol-runtime builds, or real forwarding checks on the designated
VPS.

## Remote Test Command

### Committed Agent bootstrap and hosted-runner fixture fix (2026-08-31)

The feature commit is `1515a7bd56a2dbf257d861fe8760038a9329bae4`; its
host-fixture correction is `a677280ece64a71d7ee4e8c4f0720cd819bcf584`. Both are
published on `main`. The following results are separate from the in-progress
React/Ant Design rewrite; the frontend counts here still describe the Vue
baseline.

- Clean-checkout [GitHub run 33325869097](https://github.com/FengYuchen1314/open-node/actions/runs/33325869097)
  passed all four jobs at `a677280`: backend **1,253 passed** (640.48 s), Agent
  **605 passed** (10.31 s), frontend **268 tests in 37 files** and both production
  bundles, and Probe Worker **5 tests** plus type checking. The backend emitted
  only the known Starlette/httpx warning.
- The earlier `1515a7b` hosted run failed during the installer fixture's parent
  ownership/mode precheck because it depended on the runner's real `/opt`.
  The correction makes that fixture use its owned temporary install base and
  explicit private directory modes. The actual installer still defaults to
  `/opt`, with unchanged rejection of unsafe parents; regression cases reject
  both `0775` and `0777` before creating a job directory.
- An independent VPS reproduction runs all **124 host-installer tests** as the
  existing unprivileged `nobody` account with `umask 0002`. It passed, along
  with Ruff, in `/tmp/open-node-ci-owner.wVwaXkVt`. No production paths or
  service accounts were adopted by those fixtures.
- The exact clean `a677280` checkout at
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/source` passed backend Ruff and
  **323 focused tests** (76.60 s): 75 API, 98 store, 124 installer and 26
  authentication cases. Log: the parent directory's `backend-focused.log`.
- The same checkout's real-systemd smoke reran the actual installer bytes,
  SHA-256 `00e18bc0c4c55a461b1b811c4e4faa636f558590325e3a2e26827e15cb468913`,
  against the unchanged public Agent 0.3.0a0 assets and official Xray v26.3.27.
  Forced WebSocket and HTTP both reached non-root Agent/runtime readiness and
  forwarded **1,223,915** and **1,102,535** downlink bytes. Wrong-nonce replay,
  redemption after registration and repeated installation were refused; no
  secret leaks or cleanup errors were reported. Evidence:
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/real-bootstrap/report.json`.

The feature-only `1515a7b` verification also checked all 142 tracked backend
files (2,201,738 bytes) against Git and the complete 1,250-test VPS source tree.
Its exact frontend/Worker/browser rerun is retained under
`/tmp/open-node-bootstrap-exact-ui-1515a7b`; 53 Compose preflight checks and
two isolation negative controls are at
`/root/open-node-bootstrap-committed-check.OmEteOtb/results/report.json`.
These counts are not added to the later CI or focused totals.

Production remained on container `c2594ea5b436950a92e310f320b072bfe5bbeda15b178672b4d14008e6e841aa`,
image `open-node:cb1eb0c`, start time `2026-08-29T12:59:02.442246035Z`, with
zero restarts. The shared candidate remained clean at `6ca84e2`. This is a
source publication, not a production upgrade or proof of public DNS/TLS,
reverse-proxy subpaths, or automatic transport fallback.

### Panel-issued Agent installation (2026-08-31)

The feature was tested in private VPS source snapshots based on `6ca84e2` with
the bootstrap changes overlaid. The shared candidate stayed clean at `6ca84e2`;
production stayed on `open-node:cb1eb0c`. These working-source checks are distinct
from clean-checkout CI after the feature is committed.

- Complete backend gate: **1,250 passed** (694.29 s), with only the known
  Starlette/httpx deprecation warning, in
  `/tmp/open-node-bootstrap-feature.3peB48sZ/backend-suite.log`. The SSH output
  session disconnected near the end; the VPS process continued and wrote the
  complete successful pytest summary. This is the retained VPS result, not an
  inferred success from that disconnected shell.
- Focused backend gate: **320 passed** (76.06 s), comprising 98 ticket-store,
  75 API/release-helper, 121 host-installer and 26 authentication tests. This
  includes concurrent claims, hash-only tickets, nonce/expiry/replay bounds,
  post-claim reissue refusal, Origin/CSRF, bounded JSON, persistent rate limiting,
  HTTPS/path validation, release-source/hash validation, owned paths and secret
  redaction. Backend Ruff passed.
- Frontend: **268 tests in 37 files** (7.65 s), administrator and probe-only
  production builds passed. The added cases cover late request invalidation,
  close/target change/disposal, replacement/revoke/claim, existing heartbeat,
  registration, failures and no persistent command storage.
- Probe Worker: **5 behavior tests** and TypeScript checks passed, including
  GET/POST/DELETE rejection of all Agent-bootstrap endpoints without contacting
  the origin or asset fallback, plus write rejection of public Probe routes.
- Production browser gate passed against disposable FastAPI/SQLite: disabled
  configuration, explicit mobile issuance/copy, close/reopen clearing, command
  replacement, revocation, claim vs. registration, existing-heartbeat refusal,
  installer checksum binding, cache/privacy headers and no page errors. The
  final registration and heartbeat are explicit API fixtures, **not** proof of
  installation. Desktop/mobile screenshots mask commands and manual tokens.
- Separate real-systemd bootstrap passed over forced **WebSocket and HTTP**
  using the published Agent 0.3.0a0 artifacts and official Xray v26.3.27. The
  gate exercises the actual panel-generated HTTPS command, claim, non-root
  process/runtime readiness, a subsequently configured VLESS inbound and live
  HTTP forwarding. Control-plane downlink counters observed **1,223,915 bytes**
  and **1,092,420 bytes**, respectively. Wrong-nonce replay returned 401;
  registration blocked redemption (401) and ticket reissue (409). Re-running
  the command was refused with PID/config/unit unchanged; logs leaked no Agent
  token and both owned fixtures were cleaned.
- Root installer compatibility: **53 preflight checks** passed for legacy and
  new Compose files, exact runtime environment matching, inherited-shell
  isolation and safe URL values. A separate real Docker fixture passed fresh
  install, same-source enable, identical-value no-op and same-source disable,
  keeping the administrator/inventory and two immutable stopped-volume backups.
  The non-root image could read the bundled installer/manifest, and HTTP
  resources plus configured state matched in all three deployment states.
  Owned fixture cleanup completed and the production snapshot was unchanged.
- Additional fixture safety controls passed after review: 15 injected `GIT_*`
  variables could not change an external sentinel repository's files, index,
  configuration, refs or objects, including through the imported Git helper.
  A mocked pre-existing namespace caused **zero cleanup calls**. These controls
  created no Docker resources and are reproducible with
  `--safety-negative-controls`.

The real bootstrap gate's only command deviation is appending its restricted
`--test-directory` option. Its root-URL loopback HTTPS control plane uses a
private trusted CA; GitHub downloads still use Debian system trust. It does not
prove public DNS/TLS, reverse-proxy subpath operation or an actual Auto-to-HTTP
fallback event. Those must not be inferred from running each transport
separately. The backend and browser fixture never use the production database.

From a reviewed isolated feature checkout, with the backend/browser dependencies
and Chromium installed:

```bash
npm --prefix frontend run build
backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap-browser.py \
  --output /tmp/open-node-bootstrap-browser-reviewed-revision
sudo backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap.py \
  --output /tmp/open-node-bootstrap-real-reviewed-revision
sudo python3 scripts/vps/smoke-installer-bootstrap-setting.py \
  --safety-negative-controls --guarded-update \
  --output /tmp/open-node-bootstrap-setting-reviewed-revision
```

The latter two require a disposable Debian 12 amd64 VPS with root, systemd,
Docker/Compose and the documented host tools. They create and remove only
explicitly owned test resources. Failed cleanup retains private recovery inputs;
inspect the reported exact fixture before doing anything else.

VPS evidence locations:

- `/tmp/mmwx-agent-bootstrap-store.lE9RnE` — frozen backend overlay and focused gate.
- `/tmp/open-node-bootstrap-ui.a8xVJ6VG/frontend-final.log` — final frontend gate.
- `/tmp/open-node-bootstrap-browser-20260831-final/report.json` — browser workflow.
- `/tmp/open-node-bootstrap-real-gate-20260831a/report.json` — real double-transport bootstrap.
- `/tmp/open-node-bootstrap-setting-guarded-20260831b/report.json` — root installer
  matrix, Docker setting transitions, image resource/API checks and cleanup.
- `/tmp/open-node-bootstrap-setting-safety-20260831a/report.json` — Git environment
  isolation and refused-namespace cleanup negative controls.

The Agent release itself is independently pinned to committed source
`6ca84e21202950bf5ee4754a8ae20e28dbde42ed`, not the newer control-plane overlay.
Its exact four assets passed pre-upload service/lifecycle gates, fresh anonymous
download/tag/BUILD/wheel/tar verification and default-GitHub-source WebSocket/HTTP
upgrade, VLESS forwarding and rollback (104.95 s). See
[the release record](releases/agent-0.3.0a0.md). The previous wheel in the upgrade
gate is a synthetic fixture; this is not a claim of a tested 0.2-to-0.3 migration.

### Administrator MFA acceptance (2026-08-30/31)

All commands below target the isolated `/opt/open-node/mmwx-parity-candidate`
checkout, never the production service or database.

- At `45515b6`, the complete backend suite passed: **955 tests** (621.28 s),
  with one known Starlette/httpx deprecation warning. This supersedes the earlier
  948-test result at `ee16ed3`.
- At `58b33af`, the expanded authentication suite passed: **26 tests** (23.68 s),
  including a persisted cross-IP/cross-challenge verification budget, two-store
  contention at the final allowed attempt, key-loss recovery and
  local/password-change invalidation. The concurrent-budget regression was
  added after the complete 955-test run; the two counts are separate evidence.
- The subsequent clean-checkout GitHub backend job at `58b33af` passed
  **956 tests** (561.06 s), including that added regression. This is separate
  hosted-CI evidence, not a claim that the earlier VPS run contained 956 tests.
- At `fb1aaaf`, frontend **239 tests**, main production build and probe-only
  production build passed. The remaining build messages are chunk-size warnings.
- The real production-frontend browser smoke passed at both `fb1aaaf` and
  `45515b6`: enrollment, recovery-code
  acknowledgement, mandatory policy, password-only challenge denial, recovery
  login, code replacement, policy removal, disablement and local reset followed
  by mandatory enrollment. Desktop (1440 px) and mobile (390 px) screenshots
  were inspected; authenticator secrets and QR codes are masked in artifacts.
  The script also checks horizontal overflow and absence of secrets from browser
  storage, and disposes its private SQLite database and loopback process.
- The independent Agent suite passed **605 tests** and Ruff on the VPS;
  the Probe Worker passed **3 behavior tests** and TypeScript checks. At
  `58b33af`, all four GitHub clean-checkout jobs passed. The Agent job now
  exercises real ownership changes with its installed
  interpreter under `sudo`, instead of failing on the hosted runner's missing
  `chown` privilege.

Run after building the frontend on the VPS:

```bash
cd /opt/open-node/mmwx-parity-candidate
backend/.venv/bin/python -m pip install -e './backend[browser]'
backend/.venv/bin/python -m playwright install chromium
backend/.venv/bin/python scripts/vps/smoke-administrator-mfa.py \
  --output /tmp/open-node-admin-mfa-reviewed-revision
```

The smoke creates random fixture credentials and an encryption key; it does not
read deployment secrets. Its CLI reset is performed only against the disposable
database. It is not evidence of public HTTPS deployment, multi-administrator
support, or automatic recovery backups. See [administrator security](administrator-security.md).

### Public Probe Worker acceptance (2026-08-31)

With the candidate's dependencies and Playwright Chromium installed, run on the
isolated VPS checkout:

```bash
npm --prefix frontend ci
npm --prefix frontend run build:probe
npm --prefix probe-worker ci
backend/.venv/bin/python scripts/vps/smoke-public-probe-worker.py \
  --output /tmp/open-node-public-probe-worker-reviewed-revision
```

The gate uses `wrangler deploy --dry-run` to compile the actual Worker, then
executes it in Cloudflare's Miniflare/workerd with the real production
`frontend/dist-probe` assets. It retains the repository's compatibility settings,
SPA fallback and `run_worker_first` policy. It neither deploys nor logs into a
Cloudflare account. All listeners are ephemeral loopback ports; the upstream is
a disposable fixture, not the production control plane or its database.

Passed using Wrangler **4.127.0**, Miniflare **5.20260826.0-alpha** and workerd
**1.20260826.1**, as locked by `probe-worker/package-lock.json`:

- Production HTML, JS and CSS byte/hash checks, nine HTTP aliases and three real
  WebSocket aliases; token replacement, bidirectional credential stripping,
  security headers, private-route 404, write-method 405 and no followed redirects.
- Anonymous Chromium requests use only the public API surface. Complete headers,
  HTTP bodies, WebSocket frames, DOM and browser storage are checked for leaked
  Worker credentials; cookies remain absent and there are no page errors.
- Both status and target polling continue while an established WebSocket sends
  no frames. Malformed frames do not break subsequent live updates. Polling
  continues through a forced disconnect and rejected reconnect, then automatic
  reconnect applies a new live snapshot.
- Target ranges, ping/system series, public-only deep links and 1440/390px
  layouts passed. Desktop/mobile screenshots were visually checked. The report
  records runtime versions, asset/bundle hashes and observed requests, without
  retaining the generated secret. Private runtime files and processes are removed.

The earlier `wrangler dev --local` attempts failed because its development
ProxyWorker exited on a connection error. The official SDK tracker describes
the same fatal error handling in [issue 15317](https://github.com/cloudflare/workers-sdk/issues/15317)
and a related five-second connection-reuse race in
[issue 14641](https://github.com/cloudflare/workers-sdk/issues/14641).
The gate uses the [official dry-run bundle](https://developers.cloudflare.com/workers/wrangler/bundling/)
with direct Miniflare instead; it does not patch dependencies, change application
polling intervals or mock the Worker's fetch implementation to bypass the failure.
The adapter targets the locked Miniflare v5 API and must be reviewed when updating
those dependencies. Build/runtime failures retain a stage report and redacted logs.

This closes the local anonymous Worker/browser gate, not a real Cloudflare
deployment, custom-domain/TLS setup, production-origin connectivity or all visual
themes from the reference probe. Those remain distinct operational/parity checks.

### Repository-wide runner

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

The runner removes stale local Agent wheels before building. In the same shell,
resolve the single artifact once and reuse it in the smoke commands below:

```bash
mapfile -t AGENT_WHEELS < <(
  find "$PWD/agent/dist" -maxdepth 1 -type f -name 'open_node_agent-*.whl' -print | sort
)
if (( ${#AGENT_WHEELS[@]} != 1 )); then
  printf 'expected exactly one built Agent wheel, found %s\n' "${#AGENT_WHEELS[@]}" >&2
  exit 1
fi
AGENT_WHEEL="${AGENT_WHEELS[0]}"
```

## Legacy MMWX Identity Smoke

With the frontend built and an Xray binary available on the VPS:

```bash
PYTHONPATH=backend/app backend/.venv/bin/python \
  scripts/vps/smoke-legacy-mmwx.py \
  --xray /absolute/path/to/xray \
  --output /tmp/open-node-legacy-mmwx-screenshots
```

The isolated fixture creates an active-main-shaped MMWX SQLite database, runs the
mode-0600 exporter, uploads the result through the Vue preview/confirmation dialog
and explicit package mapping, then verifies secret clearing. It checks imported
multi-file assignments, administrator profile editing, subscriber profile selection,
bcrypt-to-Argon2id upgrade, original TOTP, one-use legacy recovery and source-admin
demotion. Long/generated/custom keys and direct file, file+user and package+user
`/x` links all render the same valid profile; one `/x` result forwards real VLESS
traffic. Screenshots and overflow checks cover 1440px, 390px and 320px. See
[legacy-mmwx-identities.md](legacy-mmwx-identities.md) for raw/template/rule limits.

## Subscriber Limit Smoke

On the designated VPS, with the frontend built and the independent Agent wheel
and free native-limiter Xray binary available:

```bash
python scripts/vps/smoke-user-limits.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-user-limits-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated root/systemd fixture installs a
non-root Agent and verifies real speed/connection caps, explicit unlimited,
inheritance, Agent restart persistence, offline quota withdrawal, unchanged
credentials and charged usage, and unrelated-user forwarding. Browser checks
cover stale forms, numeric validation, user overrides and subscriber visibility
at 1440px, 390px and 320px widths. See [user-limits.md](user-limits.md).

## Custom Subscription Link Smoke

Use the same VPS prerequisites, built frontend, Agent wheel and free Xray
binary as the subscriber-limit fixture:

```bash
python scripts/vps/smoke-subscription-links.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-link-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Operator/subscriber browser edits, password
and second-factor proof, stale/colliding values, clearing, custom-URL downloads
and complete link reset are checked against real forwarding and an unchanged
runtime PID. The same run creates a [temporary subscription link](temporary-subscriptions.md)
through the administrator UI, copies it, consumes its access limit with Xray and
URI-list downloads, proves real forwarding, checks exhaustion and revokes it.
Screenshots and overflow checks cover 1440px, 390px and 320px. The temporary
Agent installation is removed after the run. See
[subscription-links.md](subscription-links.md) for permanent link identity and
security rules.

## Plan Alias Smoke

With the same VPS prerequisites and built frontend:

```bash
python scripts/vps/smoke-plan-node-aliases.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-alias-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated fixture checks browser creation,
alias editing, stale revisions, saved enable/disable state, clearing, all five
export formats and a subscriber's downloaded Xray configuration forwarding
real traffic. Credentials, subscription keys, the unrelated plan and runtime
PID remain unchanged. It captures 1440/390/320px views and removes its temporary
Agent installation. See [plan-management.md](plan-management.md) for semantics.

## Plan Speed Rules Smoke

Use the current Agent wheel, built frontend and a free Xray binary reporting
`user_auto_speed_rules: 1` on the VPS:

```bash
python scripts/vps/smoke-plan-speed-rules.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-rule-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Real clients exercise sustained and burst
activation, measured throttling, expiry, an unrelated plan, hot refresh and
restart persistence. Browser coverage includes creation, ordered edits,
invalid values, continuous typing, clearing and preservation from Config >
Limits. Exports, credentials and subscription keys remain unchanged.
Screenshots cover 1440/390/320px. The fixture removes its non-root Agent.

## Subscription Client Smoke

Build the frontend and [patched runtime](fork-runtime.md) on the VPS. Use the
backend development environment with Playwright Chromium, the current Agent
wheel, Nginx, Mihomo v1.19.30 (digest below) and official sing-box v1.13.19.
The `sing-box-1.13.19-linux-amd64.tar.gz` SHA-256 is
`ef88a9e577d474210867bd708933d042e9b70106529df2656182c9db90106aa1`.

```bash
python scripts/vps/smoke-subscription-clients.py \
  --xray /tmp/open-node-runtime-build/xray \
  --mihomo /absolute/path/to/mihomo \
  --sing-box /absolute/path/to/sing-box \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-screenshots
```

Pass `--templates-only` to retain the full 18-variant fixture while running only
the custom-template API, Mihomo forwarding and administrator/subscriber browser
workflow. Surge output is validated from the real endpoint but not imported
into the proprietary Apple client on this Linux host.

This disposable root/systemd fixture installs a non-root Agent and provisions
18 inbound variants. It validates complete native exports, switches selectors,
tests each selected Xray node, and feeds unchanged URI/Base64 payloads to the
pinned Mihomo parser. Every compatible entry must forward TCP and UDP. Mieru is
covered over both TCP and UDP underlays only after a fresh Agent scan reports
strict integer `mieru_udp_target: 1`; the fixture checks both transports and
the backend's fail-closed capability gate. Explicit expected node sets prevent
a broken converter from passing by excluding everything.

It also verifies that the Shadowsocks 2022 shared key stays out of imported
node metadata and compatibility reports. Browser checks cover the format report,
Xray selection, selected URLs, desktop/mobile/narrow layout, and delayed
responses during format/user changes. Consult [subscriptions.md](subscriptions.md)
for the exact version-specific boundaries; this fixture is not an assertion
that arbitrary protocol extension fields are portable.

The template workflow additionally covers CRUD revisions, plan bindings,
unchanged credentials/tokens/runtime PID, custom Clash group order, real Mihomo
TCP/UDP forwarding, custom Surge section/node validation, personal permission,
and 1440/390/320px screenshots for both workspaces.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- Backend: 913 tests; Agent: 544 tests; frontend: 32 files and 216 tests,
  TypeScript checks and production build. Ruff, targeted formatting and probe
  Worker TypeScript checks passed.
- All 18 inbound variants passed their supported native client formats and
  unchanged URI/Base64 imports with real TCP/UDP target traffic. Mieru TCP and
  UDP underlays both passed through Mihomo v1.19.30; its executable SHA-256 was
  `8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a`.
  The sing-box v1.13.19 executable SHA-256 was
  `7e9dcd7239c49478a576d79f272751e5ed1c2aba7cc08ab1b2bd69c00c904ba1`.
- The custom Clash/Surge API, real Mihomo forwarding and both template browser
  workspaces passed. Generated credentials, subscription tokens and runtime
  identity remained stable.
- The patched runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  matching-source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Its `build.json` records four MPL-2.0 patches, package and race tests, and
  successful module verification.
- Existing Starlette/httpx deprecation, npm install-script approval and frontend
  bundle-size warnings remain.

These results do not close the other [migration gates](migration-map.md).

## Fork Protocol Smoke

Build the optional [compatibility runtime](fork-runtime.md), its unmodified
reference executable, and the current Agent wheel on the VPS. Obtain Mihomo
v1.19.30's `mihomo-linux-amd64-compatible-v1.19.30.gz` from its official release.
Verify the gzip SHA-256 before extraction:
`db214c7a2517e63c150d123178d16d102e03a241ccdae4e5e07ffbe9cf56c6f9`.
The tested Go 1.26.7 Linux amd64 tarball has SHA-256
`ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca`.

```bash
python scripts/vps/smoke-protocol-runtime.py \
  --xray /tmp/open-node-runtime-build/xray \
  --reference /tmp/open-node-runtime-build/xray-reference \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx
```

Use the backend development environment. The fixture requires root/systemd
only to install disposable dedicated non-root Agents and remove their units
and accounts afterward. All listeners are loopback-only; there is no public
provider registration. Both HTTPS lease and WSS paths use a trusted fixture CA.
The source runtime is first exercised unchanged, followed by the installed
Agent's patched runtime using the same configuration. The tests then import
nodes, assign a plan, consume actual subscribed credentials, check per-user
statistics, rotate non-managed credentials, exercise direct zero-user listeners,
withdraw managed access, restart the service with suspended listeners and
reactivate the same catalog credentials. Direct empty-user edits must keep each
TCP/UDP listener owned only by the new fork PID and reject old credentials;
managed withdrawal must remove every listener and preserve the private recovery
template across an Agent restart. The smoke also checks invalid-write
preservation and refusal of an official Xray switch without changing the fork
PID.

AnyTLS, every Snell variant and both Mieru underlays cover TCP and UDP target
bytes. Mieru additionally covers transformed UDP echo, DNS, a 4096-byte packet,
multiple targets on one association, user-attributed statistics and three fresh
negative associations. The UDP targets accept traffic only from Xray's explicit
loopback egress address, preventing Mihomo local-direct behavior from becoming a
false positive. The unmodified reference runtime is the Mieru UDP negative
control. Snell v6 uses the free fork client. Complete mixed exports are covered
separately by the subscription-client smoke above.
Other architectures, multi-file takeover and public-provider staging are not
established by these tests.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- The complete smoke passed independently over WebSocket and HTTP with exit
  code zero. Both used disposable non-root systemd Agents, trusted local TLS,
  real Mihomo and the pinned fork client.
- The unmodified reference accepted all original TCP paths but rejected Mieru
  UDP target traffic over both underlays. The patched runtime passed TCP/UDP,
  DNS, multi-target, large-packet, statistics, rotation, direct zero-user,
  managed suspension, Agent-restart persistence and exact reactivation checks.
- The failed official-Xray migration preserved the config bytes and the exact
  running fork PID. Every deliberate runtime restart removed the prior PID;
  `fuser` proved each active TCP/UDP listener belonged only to the replacement
  process, while managed suspension left no owner.
- The runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  the unmodified reference SHA-256 was
  `b0f43766871def4cad3952b9cecd2f4dfd4ac4dd9771866e9e778980682e5cbb`.
  Fixed source revision was `d3fdae5833a92070414db588ee9893264147b789`.
- Matching source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Patch SHA-256 values were
  `0914ab8149646801904d91f6229520acbe6cae1e749229fb5c8e129fee458814`
  (empty users),
  `d85463cfdf6b0c5ca3f17f046e2bf78e1dc44a1e21146baff9faf804137708d7`
  (AnyTLS UDP),
  `3841a90cae74b978de31671057a3bb05ec84589d86cecdf34222175f318da506`
  (Mieru UDP) and
  `91e7f33c3752f5fb8f46852e89cdc02ef2cfd7479657e154e26e5ea184c7d644`
  (limiter). Go 1.26.7 package, race and module-verification gates passed.

## Host Policy Smoke

Build the current Agent wheel on the VPS. Keep a trusted pre-policy bootstrap
checkout (including its sibling lifecycle modules) to exercise old-helper
compatibility. Use the backend test environment, Debian Nginx and the pinned
NextTrace binary from [agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-host-policy.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --previous-bootstrap /path/to/previous-checkout/agent/app/open_node_agent/service.py
```

The root-only fixture installs isolated non-root systemd services using the
previous installer and copied lifecycle helpers. It exercises both HTTPS leases
and WSS, actual TCP/ICMP and IPv4/IPv6 NextTrace results, capability removal,
unchanged PID on no-op, checksum-verified executable replacement failure,
SIGKILL during the transaction, old-bootstrap refusal, and separate helper
restart recovery. It preserves helper hashes and boot-enable preferences,
verifies stopped Agent/Xray intent, performs a real remote wheel upgrade through
the old helper, and checks VLESS forwarding after transitions. A deliberately
faulty fixture wheel exits only under the newly granted raw capability, proving
rollback after a real systemd startup failure. No fixture wheels are published.
GeoIP is disabled; this smoke does not query public IP/ASN providers or register
public accounts. The designated VPS denies unprivileged ICMP datagram sockets
(`ping_group_range: 1 0`), so removing the raw capability also denies ICMP
fallback there. The smoke does not change that global setting. It removes the
isolated units/accounts on exit.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 269 tests; frontend: 98 tests and production build.
- Agent Ruff and the new smoke's Ruff checks passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full host-policy smoke passed over both transports using the previous
  bootstrap from commit `84d0bc3`, including genuine process termination and
  startup failure, private recovery metadata and unchanged helper hashes.
- The separate systemd installation/upgrade/rollback/uninstall smoke passed.
- The current remote lifecycle helper passed its complete regression smoke,
  including interrupted staging/switch/removal, durable final callbacks,
  retained data, real VLESS forwarding and confirmed desktop/mobile/narrow
  browser actions. Desktop and narrow/mobile screenshots were inspected.
- These results do not establish other OS/architecture coverage, public
  provider registration, fork-specific protocols or the remaining migration
  gates in [migration-map.md](migration-map.md).

## Native WARP Smoke

Build the current Agent wheel and frontend on the VPS. Use the backend test
environment with Playwright/Chromium and a trusted Debian Nginx executable:

```bash
python scripts/vps/smoke-warp.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --output /tmp/open-node-warp-shots
```

The root-only fixture installs disposable non-root systemd services and uses a
local TLS provider fixture with actual Xray WireGuard peers. Tests cover both
Agent transports, explicit first-registration consent, free-account status,
real IPv4/IPv6 encrypted forwarding, reapply, optional account/config updates,
Agent restart, blocked referenced-outbound removal, retryable provider failure,
preserved direct traffic, private state and non-disclosure in WARP results/logs.
Browser checks cover 1440px, 390px and 320px confirmation/result layouts. Host
routes and interface names must be unchanged after cleanup.

This does not create a public Cloudflare account or establish public-provider
compatibility. Live registration and deletion require operator acceptance of
Cloudflare terms. See [warp.md](warp.md#verification-boundary). The wheel is a
source build, not a replacement for immutable published Agent 0.1.0 artifacts.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 269 tests; Agent: 231 tests; frontend: 98 tests and production build.
- Agent/backend Ruff checks and the WARP smoke passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full non-root WARP fixture smoke passed over both transports, including
  real encrypted IPv4/IPv6 forwarding, restart, provider-failure recovery and
  inspection of desktop/mobile/narrow screenshots. No routes or interfaces changed.
- A fresh installation of the unmodified built wheel passed the independent
  Agent runtime smoke on both transports, including real VLESS traffic,
  provisioning/revocation, statistics, failed configuration recovery and
  persistent stopped-runtime intent.
- These results do not verify Cloudflare public registration or a paid WARP+
  account. No public provider terms were accepted by the test harness.

## Native Diagnostics Smoke

Build the current Agent wheel and frontend on the VPS first. Use the backend
test environment with Playwright/Chromium installed, a trusted Debian Nginx
binary, and the pinned NextTrace Tiny executable documented in
[agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-diagnostics.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --output /tmp/open-node-diagnostic-shots
```

The root-only fixture installs disposable non-root services, uses its own
trusted HTTPS/WSS gateway, and removes owned units/accounts on exit. It checks
real TCP and ICMP fallback, DNS failure, IPv4/IPv6 TCP trace hops, public
ASN/geolocation evidence, history ingestion, log ownership/clearing, persistent
VLESS traffic, and a default service without raw socket privileges. The public
GeoIP check needs upstream connectivity; it is not substituted with fixture
metadata. Browser checks cover 1440px, 390px and 320px layouts, real queued
probes, confirmed log clearing and scheduled return-route creation.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 267 tests; Agent: 182 tests; frontend: 95 tests and production build.
- Ruff passed for the Agent and diagnostic smoke. Existing backend deprecation
  and frontend bundle-size warnings remain.
- The installed non-root Agent passed the complete diagnostic smoke over both
  transports, including default-denied raw-socket behavior, public NextTrace
  ASN evidence, real scheduled-task dispatch, VLESS forwarding after log
  clearing, and inspected desktop/mobile/narrow screenshots.
- The separate real systemd install/upgrade/rollback/failure/recovery/uninstall
  smoke passed again. Fixture cleanup reported no remaining owned resources.

These checks do not establish broader OS/tool support, automatic in-place
permission changes, cross-version public-release upgrades, or completion of
the remaining [migration gates](migration-map.md). Agent 0.2.0 is a source
build here, not a replacement for the immutable published 0.1.0 assets.

## Control Plane Deployment Smoke

On the designated VPS, with Docker Compose and a trusted Nginx binary:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser]'
backend/.venv/bin/playwright install --with-deps chromium
AGENT_ENV="$(mktemp -d /tmp/open-node-package-agent.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
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
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
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

## Native Limiter Smoke

Build the [free runtime](fork-runtime.md), Agent wheel and frontend on the VPS.
With the backend development environment, Chromium and a verified Mihomo binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-native-limiter.py \
  --xray /absolute/path/to/free-runtime/xray \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-native-limits
```

The fixture installs a dedicated non-root Agent over trusted HTTPS/WSS, imports
18 protocol variants and provisions their plan caps. It measures actual
combined upload/download rates and UDP target forwarding where supported,
checks real Vision TLS traffic, live cap changes on existing connections,
shared parallel buckets and admission quotas, automatic rules and persistence.
Its browser portion exercises desktop/mobile/narrow limit editing, stale
revisions and confirmed removal. It does not reuse existing host services.
Both Mieru underlays carry UDP targets and retain the authenticated user's
limiter context.

Core unit tests cover policy persistence, private files, stale revisions,
concurrent admission, live bucket updates and automatic rule timing. Run
`go test -race ./common/nodelimits` inside the matching source tree with an
isolated C compiler on the VPS. Do not run tests or builds on the local workstation.

Verified again on 2026-08-29 on the designated Linux amd64 VPS: the real smoke
measured all 18 TCP variants and all 18 UDP-target variants, including Mieru
TCP/UDP underlays. It also passed Vision TLS bulk, hot caps, shared credential
aliases, admission/release, parallel slot release, sustained/burst activation
and expiry, restart persistence and desktop/mobile/narrow editing. The current
runtime SHA-256 is
`7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
the rebuilt Agent wheel SHA-256 is
`a049c7b76a34341b01c3de6705edd8fa888011054330bb42b9133e371ed552f2`.
These results do not establish arbitrary OS, external-service or public-provider
compatibility.

## Agent Service Lifecycle

After building the Agent wheel, run the following on the designated VPS as root:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-service.py \
  --wheel "$AGENT_WHEEL"
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

## Xray Multifile Takeover Smoke

Build the frontend and Agent wheel on the designated VPS. Run with the backend
development environment, Playwright Chromium, systemd, polkit and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-takeover.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-takeover-screenshots
```

The root-only fixture creates a disposable root-owned virtual environment and
dedicated non-root services. It obtains official Xray 26.3.27 using the same
pinned archive digest as the runtime smoke. An existing verified archive can
be supplied with `--xray-archive`. It never operates on an existing MMWX service.

Both HTTPS polling and WSS exercise repeated explicit JSON/JSONC inputs plus
a directory. A separate polling case uses only `-confdir`, with an existing
target inside it. Conflicting credentials, outbound order and routing distinguish
the actual core's merge from generic JSON merging. Real VLESS traffic verifies
the source and consolidated layouts, newly provisioned users and Agent restarts.
Checks cover secret-free GET previews, stale checksums, exact original-byte
backups, unchanged unit definitions, neutralized secondary files, repeated no-op
requests, and consolidation of a stopped service without starting it.

Fixture-only wheels inject real SIGKILLs after the prepared, stopping and
activating records and after the first config replacement. Restarted Agents
restore files and forwarding; interrupted commands are redelivered and return
409, not a manufactured success. An independent file edit blocks recovery until
the host repairs it. A real occupied listener makes Xray activation fail and
verifies delayed rollback after the port is released. These modified wheels are
never published. The unmodified wheel then reruns the existing external-systemd
fixture over both transports, including ownership and authorization guards.

Browser checks exercise preview, explicit acknowledgment, checksum-bound apply,
command completion and actual forwarding at 1440x900, 390x844 and 320x740.
The dialog scrolls internally, keeps actions visible and wraps long paths and
checksums. Unit tests also cover read-only previews during pending recovery,
backup-before-commit ordering, input/output size limits and file safety.

Recorded verification on 2026-08-28 (UTC) on the designated VPS:

- Backend: 451 tests; Agent: 434 tests; frontend: 99 tests, totaling 984.
- Frontend production build, Ruff and probe Worker TypeScript checks passed.
- The installed-wheel takeover fixture passed both control transports, the
  directory-only case, all crash/failure cases and both original systemd regressions.
- Desktop, mobile and narrow browser workflows passed; screenshots were inspected.
- The final Agent wheel SHA-256 is
  `b971c38c455a0a5adc5a7f74fb703a54f25301923da17a07a4ab74acc3731b77`.
- Existing Starlette/httpx deprecation and frontend bundle-size warnings remain.

The verified host scope remains Debian 12 x86-64 and official Xray 26.3.27.
Other runtime/OS combinations, arbitrary host-process adoption and crash recovery
for ordinary config mutations are not established by this workflow. See
[takeover boundaries](xray-takeover.md) and the other [migration gates](migration-map.md).

## External Systemd Smoke

Build the Agent wheel and install it into a separate, root-owned virtual
environment readable by the disposable service account. On the designated
systemd/polkit VPS, with the existing smoke dependencies and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-external-systemd.py \
  --agent-python /path/to/installed-agent/bin/python \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx
```

The root-only fixture creates unique non-root accounts, independent Agent/Xray
units and exact polkit rules. For HTTPS polling and WSS it verifies actual
VLESS forwarding, provisioning, user stats, invalid-write rejection, failed
restart rollback, Agent restart without Xray interruption, remembered stop
intent, binding mismatch while the Agent stays online, and grant revocation
without stopping the host-owned runtime. It rejects aliases, mismatched binary
paths and writable unit files. Negative permission checks cover unrelated
services, manager reload and enablement. The polling fixture also exercises
`CAP_NET_BIND_SERVICE` on both services. Modified rules cannot be overwritten
or removed; fixture resources are cleaned up after the run.

This proves the [documented single-file binding](external-systemd.md), not
multi-file takeover, other OS/architectures, public providers, or a durable
rollback after a crash in the middle of an ordinary config mutation.

Recorded verification for this milestone on the designated Debian 12 VPS:
365 Agent tests, 387 backend tests, 98 frontend tests, the production frontend
build, and Ruff checks passed. The final installed wheel passed the external
fixture over both transports. The independent managed-runtime smoke and real
host install/upgrade/rollback/interruption/uninstall smoke also passed. These
results do not close the other [migration gates](migration-map.md).

## Remote Agent Lifecycle

Build the Agent wheel and production Vue assets first. On the designated VPS,
with the browser/cryptography dependencies and a trusted Nginx binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-lifecycle.py \
  --wheel "$AGENT_WHEEL" \
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
PUBLISHED_AGENT_WHEEL=/absolute/path/to/published/open_node_agent-wheel.whl
backend/.venv/bin/python scripts/vps/smoke-agent-release.py \
  --wheel "$PUBLISHED_AGENT_WHEEL" \
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
  --wheel "$AGENT_WHEEL" \
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
  --wheel "$AGENT_WHEEL" \
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
  --wheel "$AGENT_WHEEL" \
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

## HTTP-01 Lifecycle Smoke

Use the same VPS dependencies, pinned binaries and free loopback DNS ports as
the ACME lifecycle smoke above. Build the frontend on the VPS first. This test
also needs the existing Debian `www-data` account for an independently running
non-root Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-http.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so \
  --screenshots /tmp/open-node-http01-screenshots
```

The browser creates and issues standalone and webroot profiles without a DNS
provider. It checks mode-specific controls, wildcard rejection, CA consent,
renewal controls, collapsed/expanded EAB fields and 1440/390/320px layouts.
Pebble fetches actual challenge responses through a loopback fault-injection
hop and Nginx: standalone requests reach lego's listener, while webroot
requests read real public challenge files as a different Unix user.

The test covers SAN issuance, not-due skips, deliberate HTTP validation
failure, forced renewal, file/listener cleanup and active-version preservation.
It kills the backend while lego survives, verifies the inherited worker lock,
then kills lego and checks interrupted-job recovery and stale-token removal.
Both modes renew automatically after actual elapsed time. HTTP-issued
certificates are deployed to non-root Agent Nginx instances over WebSocket
and HTTP, with trusted TLS serial checks and version rollback.

The website's original content is checked unchanged, and all private vault
files are checked for private permissions. Test listeners, processes, Agent
services and data are disposable; public-CA orders and production websites
are not used. This does not prove an operator's public DNS/port-80 routing.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 317 tests; Agent: 231 tests; frontend: 98 tests and production build.
- HTTP-01 standalone/webroot and existing DNS-01/EAB lifecycle smokes passed,
  including real automatic renewal and trusted Agent TLS/version rollback.
- HTTP hard-crash recovery retained the old certificate and removed stale
  challenge responses only after the surviving lego process released its lock.
- The operator browser regression passed. HTTP forms and expanded EAB fields
  were checked at 1440px, 390px and 320px, including fully visible submit controls.
- Additive SQLite migration retained DNS/imported profiles. EAB-only HTTP
  catalogs also detect missing vault keys instead of generating a replacement.
- Ruff passed for changed backend modules and the HTTP smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

## Remote HTTP-01 Smoke

Build the frontend and current Agent wheel on the VPS. Use the backend
development/browser/ACME-test extras and the same pinned Pebble, Nginx and
official Xray artifacts as the existing ACME and Agent smokes:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-remote.py \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --nginx-stream-module /path/to/ngx_stream_module.so \
  --screenshots /tmp/open-node-remote-http01-screenshots
```

The fixture starts a TLS-verified controller without lego or central HTTP-01
listeners. Real non-root systemd Agents connect over HTTPS polling and WSS.
An EAB-required Pebble CA reads standalone responses and owned Nginx webroots
on those nodes, through an observable fault-injection proxy. It never supplies
synthetic successful challenge data.

The workflow covers issue, not-due skip, failed validation retaining the old
version, forced renewal, node-disconnected cleanup and reconnect, actual TLS
deployment, account contact changes and elapsed-time automatic renewal. A
controller hard kill leaves the ACME child holding the inherited lock; after
the child is killed, recovery must reuse the same job/order and create a new
challenge lease after cleaning the old one.

Playwright creates remote profiles, selects validation nodes, checks wildcard
rejection and explicit terms/EAB fields, and reads issued versions. Layout
checks and screenshots cover 1440px, 390px and 320px. The test leaves existing
services untouched and removes its temporary systemd users/services/directories.
It does not use public CA orders or provider accounts.

Focused backend tests cover additive scan/profile migration, live capability
checks, command/lease receipts, cleanup retries, deletion protection, cancellation,
order-response loss, persisted CSR/key binding and public-only EAB payloads.
Agent tests cover host opt-in, exact HTTP host/path/token matching, expiry,
idempotent release-before-present ordering, restart, occupied ports, immutable
leases and filesystem replacement/link protection.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 387 tests; Agent: 304 tests; frontend: 98 tests and production build.
- Remote standalone/webroot issuance, EAB, HTTPS/WSS, cleanup after reconnect,
  inherited-lock/order recovery and elapsed-time renewal with live TLS passed.
- Existing DNS-01/EAB, control-plane HTTP-01 and account/revocation lifecycle
  smokes passed, including forced interruption and automatic renewal.
- Desktop/mobile/narrow screenshots were inspected; the changed Python code
  passed Ruff. Existing Starlette/httpx and frontend bundle-size warnings remain.

## Certificate Administration Smoke

Use the same pinned lego/Pebble binaries, backend development/browser/ACME-test
extras and free loopback DNS ports as the lifecycle smokes. Build the frontend
on the VPS first:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-administration.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --screenshots /tmp/open-node-ca-admin-screenshots
```

The fixture uses real HTTP-01 issuance and an EAB-required CA. It preserves a key
left by failed registration, edits EAB before registration, updates the registered
CA contact while checking the original key/URI, and renews with lego afterward.
Historical-version revocation is independently checked at Pebble's management API.

A TLS-verified forwarding fixture deliberately loses accepted account/revocation
responses. Retries must query and reconcile actual CA state, including
`alreadyRevoked`. A backend hard kill while the helper holds a confirmed response
verifies inherited locking and durable receipt recovery without a duplicate request.
The test also checks forced new-key reissuance, imported certificate revocation,
duplicate blocking and ledger retention after profile deletion.

Playwright operates account/EAB and revoke/retry dialogs with real backend requests.
Screenshots and layout checks cover 1440px, 390px and 320px, including visible
confirmation controls, masked credentials and disabled revoked-version actions.
The fixture checks private permissions and removes temporary request files.
No public CA, DNS-provider credential, production certificate or website is used.

The focused `test_certificate_administration.py` suite additionally exercises
input/secret validation, additive schema migration, competing deployment/revocation
and import transactions, retained commands without targets, receipt mismatches,
graceful cancellation and revoked on-disk candidate recovery.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 231 tests; frontend: 98 tests and production build.
- The administration smoke passed with actual CA contact/status checks, lost
  responses, hard restart, duplicate/import protection and new-key reissuance.
- Existing DNS-01/EAB and HTTP-01 standalone/webroot smokes passed, including
  automatic renewal, both Agent transports, trusted TLS and version rollback.
- Operator UI regression and 1440/390/320px account/revocation layouts passed.
  The final browser run also checks the revocation icon's loaded glyph.
- Ruff passed for changed backend code and the new smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

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
  --wheel "$AGENT_WHEEL" \
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

## Earlier Registration Invitation Verification

Registration invitations passed on the designated VPS:

- Backend full regression: 883 tests. Frontend: 32 files and 216 tests, Vue
  typecheck and production build. Ruff passed for all backend sources, tests and
  the new smoke script.
- Focused coverage passed digest-only persistence, working subscriber login,
  exact plan/runtime enrollment, generic invalid/revoked/expired/used responses,
  case-insensitive username retry, atomic concurrent claims, plan cleanup and
  administrator/public route isolation.
- The WebSocket smoke installed a temporary non-root Agent, claimed a plan-bound
  invitation, waited for the durable access command, exported the invited user's
  Xray configuration and forwarded 32 KiB of real TCP traffic. Reuse failed with
  the generic unavailable response and temporary services were removed.
- The production preview completed actual HTTP registration and subscriber login.
  Administrator invitation management and the invited account form passed at
  1440, 390 and 320 pixels without horizontal overflow or console errors.
- A copy of the prior preview database retained every count across 48 existing
  tables and two existing rows. Startup added an empty `registration_invitations`
  table and `PRAGMA foreign_key_check` remained clean.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not enable open anonymous signup or close the remaining
[migration gates](migration-map.md).

## Earlier Template Verification

Custom Clash and Surge templates passed on the designated VPS:

- Backend full regression: 858 tests. Frontend: 205 tests and production
  build. Ruff formatting and checks passed for all backend sources, tests and
  the subscription-client smoke script.
- The existing 18-variant client fixture passed real Mihomo, sing-box and Xray
  forwarding. Its focused template run passed administrator/subscriber CRUD,
  revision guards, personal permission, plan/system defaults and catalog
  remapping without changing credentials, tokens or the runtime PID.
- Custom Clash output was downloaded from the public endpoint, loaded by
  Mihomo and forwarded real TCP and UDP traffic. Custom Surge output from the
  same endpoint preserved the non-proxy profile text and matched the exact
  compatible node set under an independent parser.
- Administrator and subscriber workspaces passed at 1440, 390 and 320 pixels.
  Actual Surge application import remains an Apple-platform gate.
- Agent sources did not change. The prior 536-test Agent baseline, wheel and
  free-core artifacts were reused by the real lifecycle/client smoke.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Automatic Speed Rule Verification

Per-plan automatic speed rules passed on the designated VPS:

- Backend full regression: 815 tests. The final command-payload guard then
  passed 99 focused tests, including four new malformed-payload cases.
  Agent: 536 tests and wheel build; frontend: 202 tests and production build.
- The free core rebuilt successfully; protocol/core tests and the native
  limiter/dispatcher race tests passed. The existing multi-protocol smoke
  passed real TCP and supported UDP limits, Vision TLS bulk, shared connection
  quotas, live updates, sustained/burst rules, expiry and restart persistence.
- HTTP and WebSocket plan smokes passed create/edit/order/clear, validation,
  sequential input, native-editor preservation and independent subscribers.
  A 64 KiB echo took about 2.00 seconds under the automatic 0.5 Mbps combined
  cap, and under 1 ms for the other plan and after expiry, on this local VPS
  fixture. This is an enforcement check, not a network performance benchmark.
- Credentials, subscription exports and tokens stayed unchanged. Runtime
  policy survived restart; unchanged hot policy saves preserved active timers.
  Old Agent/core capability rejection, catalog roundtrips, legacy omission
  and additive schema upgrades passed focused tests.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for changed Python sources and smoke scripts. Temporary non-root
  Agent installations were removed after each smoke.

Verified Linux amd64 artifacts for this milestone:

- Agent wheel SHA-256:
  `7cf9f6463e13f691dbf198ded77fa49f3923cd600d64f507a47f2fb52a4374ca`.
- Free core SHA-256:
  `348434f6700cd49df8015c7707910fdc1bbfd196f9ea3fea05f8ed4189d4dc7a`.
- Matching MPL-2.0 source archive SHA-256:
  `4c0fa9c730ea58f88e3b0d5dca5b1a456085a3933a69d29eb73cf1dc79f63d43`.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Plan Alias Verification

Plan node aliases passed on the designated VPS:

- Full regression: backend 791 tests, Agent 522 tests, frontend 187 tests and
  production build passed. The earlier focused backend run passed 159 tests.
- All five subscription formats and previews use aliases before multipliers;
  reserved/original-name collisions, Unicode validation, isolated plans,
  preserved runtime records, legacy field omission, catalog remapping/rollback,
  node/server removal and repeated SQLite upgrades passed.
- Final HTTP and WebSocket browser runs passed creation, alias edits, stale
  revision rejection, disable/clear, and subscriber downloads. The downloaded
  Xray configuration forwarded real traffic while the runtime PID, credentials,
  subscription keys and unrelated plan remained unchanged.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for all changed Python sources and the smoke script. Temporary
  Agent installations were removed by the fixture.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Short-Code Verification

Custom subscription short-code verification on the designated VPS:

- Full regression: backend 765 tests, Agent 522 tests, frontend 177 tests and
  production build passed. After the final additive lookup-index change,
  84 focused backend tests passed, including a new query-plan check and both
  new-database and old-schema upgrade coverage.
- The final schema uses indexed lookups for long, generated and custom keys;
  the preceding table-scan query plan was reproduced and eliminated.
- The final WebSocket and HTTP runs passed operator/subscriber edits, stale
  revisions, case collisions, password/TOTP proof and actual browser downloads
  through the custom short URL. The downloaded Xray configuration forwarded
  real traffic. Clearing and resetting links preserved the runtime PID and
  node credentials; another subscriber kept forwarding.
- Desktop/mobile/narrow screenshots were inspected. Ruff passed for changed
  Python sources. Temporary Agent installations and private state were removed.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Subscriber Limit Verification

The subscriber-limit worktree passed on the designated VPS:

- Backend: 725 tests; Agent: 522 tests; frontend: 153 tests and production build.
- Ruff passed for the changed Python sources and the new smoke fixture.
- Real non-root installed Agents applied user/default/node speed and connection
  caps over trusted WebSocket and HTTP polling, including explicit unlimited,
  restored plan inheritance and persisted limits after an Agent restart.
- A paused Agent left existing forwarding available while quota withdrawal was
  pending. Reconnection denied the old credentials; raising the quota restored
  those same identities without resetting charged usage. Another subscriber
  kept forwarding throughout the quota changes.
- Browser checks covered stale saves, invalid values, subscriber visibility and
  1440px, 390px and 320px layouts. Screenshots were inspected. Fixture services
  and private state were removed after both transport runs.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Release Verification

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
