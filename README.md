# Open Node

Open Node is a single-repository refactor of the MMWX stack. The target is a
free-to-use server and subscription management system with no activation keys,
paid entitlement checks, commercial license server calls, or feature gates.

The current implementation status, deployment snapshot, remaining work, and
next-task handoff are recorded in [the refactor handoff](docs/refactor-handoff.md).
Source-confirmed compatibility against the four pinned upstream repositories is
tracked separately in the [MMWX source parity matrix](docs/mmwx-source-parity.md).

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
- Frontend: React, official Ant Design, Vite, TypeScript.
- Probe worker: Cloudflare Worker with Workers Static Assets.
- Repository shape: one monorepo with `backend/`, `agent/`, `frontend/`, `probe-worker/`,
  `docs/`, and `scripts/`.
- Verification target: tests are run on the VPS at `185.99.135.224` over SSH.

The [frontend architecture and verification guide](docs/frontend.md) describes
the React/Ant Design workspaces and the independent read-only Probe build.
The administrator console, subscriber portal and public Probe use Simplified
Chinese, including Ant Design's built-in controls. Protocol names, configuration
keys, commands and user-provided content retain their original values.

## Administrator Access

Management APIs require a local administrator session. There is no default
password or activation key. Create the account with `open-node-admin create`
using the same database configuration as the backend, then sign in through
the React interface. [Administrator setup and recovery](docs/administrator-access.md)
also covers HTTPS cookies, local previews, session expiry, and API clients.
[Administrator MFA](docs/administrator-security.md) adds encrypted authenticator
enrollment, one-use recovery codes, mandatory enrollment and local recovery.
Configure the persistent TOTP encryption key before enabling this optional feature.

[Administrator Telegram notifications](docs/notifications.md), published in
`bf8eaa8`, provide one bot/chat
destination, saved-configuration previews, explicitly confirmed tests, durable
package-expiry reminders and Chinese delivery history. Reminders default off;
unknown send results require a risk-confirmed manual retry, not automatic replay.
Back up the notification key directory together with SQLite. User bot binding,
daily digests, other alert rules and renewal approval are not part of this slice;
its current release and verification status is recorded in the linked guide.

[Site text settings](docs/system-settings.md) add administrator-controlled browser
and page titles. This next slice is still under release verification; it is not
part of `bf8eaa8`. Names are public plain text, with versioned atomic saves and
no license gate. Probe-only titles and security settings remain independent.

Subscribers use the separate `/account` portal. Administrators provision their
login passwords directly or issue a high-entropy, single-use
[registration invitation](docs/registration-invitations.md) bound to an existing
plan; product-user roles never grant controller access. [Subscriber accounts](docs/subscriber-accounts.md)
covers subscription downloads, usage, device sessions, password recovery and
optional TOTP.
[Legacy MMWX identity migration](docs/legacy-mmwx-identities.md) can preserve
bcrypt logins, TOTP/recovery state and current per-user subscription keys through
a transactional preview/import workflow. It also imports active-main package
assignments and multi-file subscription profiles through explicit plan mappings,
including compatible legacy `/x` links.
Both workspaces include free [custom Clash and Surge templates](docs/subscription-templates.md)
with personal permissions, plan/system defaults, draft preview and catalog portability.

## Deployment

The root Dockerfile and [Compose deployment](docs/deployment.md) build a
single non-root image containing FastAPI, the React production frontend, and
pinned lego. The deployment guide covers HTTPS, administrator initialization,
private persistent storage, backup/restore, upgrades, and explicit rollback.
No development server is needed. The hardened `cb1eb0c` baseline now runs on
the VPS as the persistent Compose deployment, managed by an enabled and active
systemd unit. It binds only to `127.0.0.1:8000` and is accessed as an
SSH-tunneled Preview. Backup, restart, Compose down/up, and isolated restore
have been verified against the deployed state. This is not yet a public HTTPS
deployment: the production hostname, DNS, trusted certificate, and public
reverse-proxy configuration still require operator input. Remaining migration
boundaries still apply; this is not yet full MMWX parity.

The root installer is published on `main`. On 2026-08-30, the anonymous Raw
GitHub URL below was downloaded on the Debian 12 VPS and passed isolated fresh
installation, administrator API login, status, same-revision update, uninstall,
data-preservation, and cleanup checks. The maintainer smoke also passed backup
restore, interrupted update, unhealthy candidate, rollback identity, ownership
drift, and missing-volume scenarios.

A new Debian/Ubuntu Docker host can use the root installer. It uses Docker
Compose v2, clones the requested clean ref, builds a transaction-unique image,
creates a private environment and installer manifest, starts the service,
verifies the exact image, binding and `/healthz`, and can create the first
administrator.
The public command downloads the script completely before running it:

```bash
(
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  sudo bash "$installer"
)
```

The secure default binds the panel to `127.0.0.1:8080`; use an SSH tunnel for
the first login. Interactive account creation reads from the controlling
`/dev/tty`; unattended installs use a root-owned private password file. The
same script accepts `update`, `status`, `uninstall`, and `create-admin`.
Updates create a stopped-volume recovery bundle before starting a candidate;
an unhealthy candidate can leave recovery explicitly required rather than
restart an older image against possibly migrated data. Uninstall preserves the
named data volume, source, configuration, installer state, images, and backups.

The convenience URL above follows mutable `main`: it neither pins the bootstrap
script nor cryptographically binds it to the subsequently cloned ref. Review
and pin both inputs (for example, a commit-specific raw script URL and a reviewed
release ref) when that supply-chain property matters. Public HTTPS still
requires an operator-owned hostname, certificate, and edge proxy. See
[control-plane deployment](docs/deployment.md) for the exact commands, manifest
rules, non-interactive secret cleanup, update/recovery semantics, maintainer VPS
smoke prerequisites, and installer support boundary.

## Current Milestone

The production control-plane Preview remains on the hardened `cb1eb0c`
baseline. The newer [Agent 0.3.0a0 alpha](https://github.com/FengYuchen1314/open-node/releases/tag/agent-v0.3.0a0)
is published from exact `6ca84e2`, with a verified wheel, bootstrap archive,
source manifest and checksum list. It is a prerelease, not a stable/latest
release. The supported scope is a new installation or controlled migration on Debian 12
amd64, one control-plane process and worker, and non-root managed Agents/Xray.
Historical discovery of unrecorded private MMWX ownership and dependencies
remains important for a full replacement, but it is not a blocker for this
deliberately bounded Preview.

The `cb1eb0c` baseline defaults generated, custom, and legacy `/x` bearer
aliases off; rotates legacy subscription bearers when that compatibility mode
is disabled; suppresses request-path access logs; bounds container logs; and
enables SQLite foreign-key enforcement on every connection. These controls
passed the complete VPS regression, exact-image inspection, and persistent
Compose acceptance. They establish the current control-plane Preview baseline.
Both Agent 0.2.0 and 0.3.0a0 have passed anonymous asset verification and
WebSocket/HTTP download, pinned upgrade, forwarding and rollback gates. The
0.3.0a0 upgrade gate uses a synthetic previous-wheel fixture; it is not proof
of an in-place migration from every earlier release. See the
[0.3.0a0 release record](docs/releases/agent-0.3.0a0.md).

The [panel-issued Agent installer](docs/agent-bootstrap.md) follows the official
MMWX flow: create a server, generate a command, run it on the new host, and
observe the Agent connecting. It uses a ten-minute single-host ticket, checks
the installer and versioned release hashes, and installs a dedicated non-root
Agent with official Xray. Real WebSocket and HTTP installations, traffic and
replay/reinstallation refusal have passed on the VPS. Configure the canonical
HTTPS control-plane URL first; this does not provision DNS/TLS, migrate an
existing host, install fork-only protocols or add public proxy inbounds.
The root control-plane installer and the panel's remote-host command are
separate entry points. Neither makes the whole project feature-complete.

[Legacy MMWX identities](docs/legacy-mmwx-identities.md) have a mode-0600
SQLite exporter and administrator-only preview/import. Existing bcrypt hashes are
upgraded to Argon2id on successful login; TOTP seeds and unused recovery hashes are
preserved. The secure default rotates imported subscription bearers once; explicit
migration compatibility preserves all three legacy key forms. Administrators map
legacy packages to Open Node plans; multi-file assignments become selectable
subscription profiles. Direct and combined legacy `/x` links require the
migration-only short-link compatibility option and are unavailable by default.
Raw uploads and legacy templates/rules/scripts require managed reconfiguration.

[External subscriptions](docs/external-subscriptions.md) let administrators keep
HTTPS Clash/Mihomo YAML sources for a subscriber, preview changes, select nodes
and explicitly confirm a snapshot before merging it into that subscriber's main
link. Source URLs and upstream credentials are encrypted at rest; fetching or
parsing failures preserve the active snapshot. This first slice is manual and
administrator-only: automatic refresh, URI/Base64 input, subscriber self-service
and the wider provider/rule ecosystem are not implemented.

[Temporary subscription links](docs/temporary-subscriptions.md) let an
administrator share selected nodes from a subscriber's current plan with a
durable high-entropy URL, a 1-100 download limit and a 1-60 minute lifetime.
They support all six subscription formats, survive controller restarts and
recheck the source subscriber on every download. Expiry or revocation blocks
future downloads but does not revoke credentials already downloaded.

[Subscription IP access](docs/subscription-ip-access.md) optionally limits each
subscriber's long link and, when compatibility is explicitly enabled, short and
legacy `/x` links to normalized IPv4/IPv6 hosts or CIDR networks. Administrators
and the subscriber can edit the policy without rotating credentials; denied
sources receive the same response as unknown links.

[Custom subscription short codes](docs/subscription-links.md) are a migration-only
compatibility feature and default to disabled. The supported public bearer is the
long 256-bit token. When an operator deliberately enables compatibility on a
restricted endpoint, administrators and subscribers can edit short codes with
collision/revision protection; subscriber edits require password and second-factor
proof, and no Agent restart is needed.

[Subscriber limits](docs/user-limits.md) add per-user traffic quotas, bandwidth
and connection overrides, including per-node and parent inheritance. The
editor, subscription quotas and native provisioning share the same rules.
Explicit unlimited is distinct from inherited settings; edits retain usage,
credentials and assignment dates.

[Subscriber self-service](docs/subscriber-accounts.md) adds isolated login,
current-plan usage and downloads, password changes, session revocation and
TOTP with one-use recovery codes. Account disablement and removal invalidate
sessions; password recovery preserves existing subscriptions and traffic.

[Registration invitations](docs/registration-invitations.md) let an administrator
bind a one-time signup link to an existing plan. Only a SHA-256 digest is stored,
the bearer token stays in the `/account` URL fragment, and an atomic claim creates
the ordinary subscriber, password account, plan assignment and runtime access
intent. Open anonymous registration remains disabled.

User [editing and removal](docs/user-management.md) now preserve runtime identity
during profile edits and support confirmed disable/reactivation. Removal tracks
offline withdrawals before local cleanup, rejects old credential replays and
isolates traffic when a username is recreated.

Plan [editing, unassignment and removal](docs/plan-management.md) now preserve
credentials and charged usage while coordinating node membership and limits.
Revision guards protect concurrent edits, and the dialog tracks remote access
confirmation, including pending withdrawal from an offline Agent.
Per-plan node aliases apply across all six subscription formats, with saved
enable switches, catalog import/export and no runtime changes for alias-only edits.
Plans also support ordered sustained/burst speed rules, isolated by subscriber
credential with native enforcement, expiry and old-runtime rejection. Both the
current Agent and free runtime are required; see [native limits](docs/native-limits.md).
Plans can bind separate Clash and Surge templates. Personal defaults override
the plan, followed by system and built-in fallbacks; template-only edits never
restart the runtime or rotate subscription identity.

Server [editing and removal](docs/server-management.md) now include guarded
profile updates, selective node-address synchronization and an impact preview.
Removal retains user usage and change history while revoking controller access;
stopping or uninstalling remote services remains a separate explicit operation.

Server [traffic cycles](docs/server-traffic.md) now keep durable Xray/system
usage, per-direction quotas, manual resets and UTC monthly reset schedules.
The dashboard panel and public probe share the same billing totals.

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
Separately owned Xray services use the [external systemd mode](docs/external-systemd.md),
with non-root scoped authorization, verified config binding and independent
process ownership. Both modes have real forwarding coverage over HTTPS/WSS.
Opt-in [multifile takeover](docs/xray-takeover.md) uses the bound Xray's native
merge rules, read-only previews, private source backups and durable recovery.
It preserves the host-owned unit and binary.
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
Native [fork protocol user management](docs/fork-runtime.md) now supports
AnyTLS, Snell and Mieru through an optional source-pinned compatibility runtime,
including Mieru UDP targets over both TCP and UDP underlays. Direct runtime
edits can retain an empty Snell or Mieru listener that rejects all traffic;
[managed subscription access](docs/subscription-access.md) instead suspends the
final-user inbound and journals its private template for restoration.
[Subscription exports](docs/subscriptions.md) now filter incompatible nodes,
provide native free-client Xray/Snell v6 configurations, and enable Mieru UDP
only after a fresh Agent scan proves the versioned runtime capability. Public
WARP verification and further migration workflows remain incomplete.

[Managed subscription access](docs/subscription-access.md) now revokes actual
credentials on expiry, account disablement and traffic exhaustion, then restores
the same credentials after renewal or reset. Durable commands expose pending
and failed nodes and prevent stale unsent provisioning. Plan bandwidth and
connection caps use the free [native limiter](docs/native-limits.md).

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

The implemented product surface includes the project skeleton, the no-license contract,
persisted server/agent inventory, agent telemetry and command slices, initial
agent operation, maintenance, diagnostic, and config-preparation wrappers, and
Xray/nginx config plus agent setting wrappers, high-level runtime/site
operation wrappers, a subscription catalog with user-plan binding, optional
guarded Agent provisioning, public subscription links with generated
per-user credentials, Clash/Surge/sing-box/Xray/URI subscription rendering, durable
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
The React frontend now includes a config workspace, runtime/site operation
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
frontend/  React + Ant Design shell, server, config, change, subscription, command, and probe views
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
