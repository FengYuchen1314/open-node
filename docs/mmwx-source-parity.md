# MMWX Source Parity Matrix

This matrix replaces percentage-based completion estimates. A feature is marked
complete only when the pinned reference behavior has a corresponding Open Node
implementation and an executable release gate. Similar labels or a queued Agent
path do not count as implementation.

## Pinned reference sources

Audited on 2026-08-30 from the four repositories published by
[`tajiaoyezi`](https://github.com/tajiaoyezi):

| Role | Repository | Pinned commit |
| --- | --- | --- |
| Control plane | `tajiaoyezi/miaomiaowuX` | `c12ce653bc07fe30426b7dfcb85076974b7be0e0` |
| Host Agent | `tajiaoyezi/mmw-agent` | `f2ba522b08d8839b3eaea94f0745e3ab2af71b84` |
| Public probe | `tajiaoyezi/mmwx-probe` | `8d82a8bc344ab3e6a1b08478f05cf653158a2b65` |
| Protocol runtime | `tajiaoyezi/Xray-core-mmwx` | `d3fdae5833a92070414db588ee9893264147b789` |

The local read-only clones live outside this repository. Future audits must
record new commit IDs instead of silently comparing moving branches.

## Requested frontend rewrite

The React/official Ant Design rewrite is published in feature commit
`50897f928226c9fef2ab7d0f68de0c3aad46156a`. It covers the administrator console,
subscriber portal and independent public Probe bundle without changing backend
contracts. Exact-commit CI, the 509-test frontend suite, real-browser workflows
and a clean-source Docker gate passed; see [frontend.md](frontend.md) and
[testing.md](testing.md). This satisfies the requested frontend architecture,
not the remaining backend parity items below, and does not upgrade the running
production instance.

The user subsequently required a Simplified Chinese interface. Chinese labels,
safe feedback, page metadata and the official Ant Design Chinese locale are now
implemented on all three surfaces. The R2 Chinese source passed 762 frontend
tests in 70 files, both builds, backend/Agent/Worker regression and the Chinese
browser/Docker gates recorded in [testing.md](testing.md). R4 corrects misleading
certificate-task messages, adds 34 tests and passes the 187-test focused gate,
both builds, full native clients/templates and real certificate administration.
The final **796-test/70-file** frontend run and clean exact-`998839b` Docker
gate passed. All four clean-checkout CI jobs passed in
[run 33359846368](https://github.com/FengYuchen1314/open-node/actions/runs/33359846368),
and feature commit `998839b` is published on `main`.
This is not evidence that every remaining parity item is complete.

## Release blockers

| Priority | Area | Source-confirmed result | Release condition |
| --- | --- | --- | --- |
| P0 | GitHub installer | Published on public `main`; the anonymous Raw URL passed isolated installation, administrator login, status, no-op update, data-preserving uninstall and cleanup on 2026-08-30. The maintainer smoke passed rollback and injected-failure scenarios. | Passed for the documented single-host Docker/SQLite scope. Public DNS/TLS and remote Agent installation remain separate work. |
| P0 | Public probe Worker | Probe-only bundle, credential-stripping Worker boundary, interval polling and WebSocket reconnect implemented. The actual dry-run Worker bundle and production assets passed anonymous HTTP/WebSocket/browser acceptance in the locked Miniflare/workerd runtime on the VPS. | Local gate passed on 2026-08-31, including desktop/mobile, idle-socket polling, rejected reconnect and secret-free requests/responses. Real Cloudflare account deployment and public HTTPS remain separate operational checks. |
| P0/P1 | Package accounting | Each telemetry delta is frozen using the reporting node multiplier and reference-compatible package factors: `oneway` x1, `twoway` x2, both over upload plus download. Raw directions remain separately auditable. | Passed ledger epoch/reset, plan-edit freeze, SQLite backfill, archive preservation and public-header tests on the isolated VPS. |
| P1 | Agent settings | All four high-level routes are capability-gated. Authenticated master probe/update is implemented; unsupported inbound-port and embedded-runtime switching stay disabled. | Passed Agent tests and release gates for the implemented capability surface. |
| P1 | Administrator MFA | Published in `main` at `6ca84e2`: encrypted TOTP, one-use recovery codes, password-bound challenges, mandatory enrollment and local reset. Documented safety differences from the pinned source are explicit. | Passed the 955-test backend suite at `45515b6`, 26 focused authentication tests at `58b33af`, frontend tests/builds and isolated production-browser workflows on 2026-08-30/31. Configure the persistent encryption key before use; multi-admin RBAC remains separate. |
| P1 | Panel-issued Agent installation | New-host workflow implemented with short-lived single-host tickets, pinned public Agent 0.3.0a0 and official Xray, private inputs and dedicated non-root systemd ownership. | Production-browser and real root-URL WebSocket/HTTP bootstrap, VLESS traffic, replay refusal, repeat-install refusal and cleanup passed on the isolated VPS. HTTPS infrastructure, existing-host migration, fork-only runtimes and general OS/architecture support remain separate. |
| P1 | External subscriptions | Administrator management and subscriber-owned self-service implemented for HTTPS YAML, URI and Base64 sources, encrypted snapshots, explicit preview/confirmation and primary-link merging. | The original administrator/YAML slice passed its published `998839b` gates. The additional input/self-service slice passed 14 backend and 8 frontend focused checks plus the combined builds; see [testing](testing.md). Scheduling and the provider/rule ecosystem remain separate. |

## Control-plane modules

| Module | Current Open Node status | Remaining reference behavior | Priority |
| --- | --- | --- | --- |
| Server/Agent inventory and telemetry | Implemented | DDNS, federation/server sharing, home speed tester and Reality sharing are absent. | P2, P1 for dependent migrations |
| Durable Agent commands | Implemented and stronger than the reference in lease recovery, command journals, dependencies and reviewable change sets | Complete compatibility still depends on every advertised child route being executable. | P1 gate |
| Xray/Nginx/WARP lifecycle | Implemented with explicit host ownership and rollback workflows; [panel-issued Agent bootstrap](agent-bootstrap.md) adds verified new-host Agent/official-Xray installation | Automatic existing-host takeover, embedded runtime and installation of Nginx/WARP/fork-only protocols are not part of panel bootstrap. | P1/P2 by migration scope |
| Managed subscriptions | Users, plans, nodes, credentials, quota/reset, templates, profiles, temporary links, access reconciliation and source-compatible frozen accounting implemented; [external sources](external-subscriptions.md) now support administrator/subscriber manual YAML/URI/Base64 workflows; [manual renewals](renewals.md) provide atomic review and extension | Automatic external refresh and the provider/rule/script ecosystem remain absent. Renewal is not payment-provider integration. | P1/P2 |
| Subscription formats | Clash, Surge, sing-box, Xray, URI list, Base64, Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern implemented with explicit compatibility filtering and UA selection; see [client guide](subscription-clients.md) | Native import/traffic acceptance in all six added clients and broader client-specific options remain unverified/unsupported as documented. | P2 individually, P1 as migration set |
| Certificates | ACME account/EAB, DNS-01, HTTP-01, encrypted vault, versions, deployment and revocation implemented; [self-signed generation](certificates.md) adds P-256/serverAuth, DNS/IP SANs and Chinese confirmation | Automatic control-plane Nginx/HTTPS takeover absent. Self-signed is not public CA trust. IPv6-literal Agent validation requires a build containing the new source; the public 0.3.0a0 package is unchanged. | P2/P1 for public one-click scope |
| Public probe data plane | Public servers/settings/series/targets/WebSocket, scheduled tasks, return routes, probe-only build and resilient polling/reconnect implemented; anonymous Worker browser gate passed | Reference theme/view parity and online IP data remain; real Cloudflare deployment needs operator inputs. | P1/P2 |
| Account security | Subscriber sessions, TOTP, recovery codes and device revocation implemented; [administrator MFA](administrator-security.md), mandatory enrollment, replay protection and local recovery published | Multi-administrator RBAC, security events and IP-ban console absent. | P1 |
| Setup/backup/database | CLI setup, installer-level stopped-volume backups and age tools implemented. Current source includes [consistent snapshots](backup-runtime.md) and [administrator Web backup jobs/downloads](backups.md), with fresh password/MFA proof, revocation, bounded retention and Chinese UI. Its targeted gate passed 512 backend and 17 frontend tests, including real HTTP creation/download and independent official-age decryption. | Browser setup, controlled restore, SQLite-to-PostgreSQL migration and application update UI remain absent. Snapshot/key checks do not establish remote Agent trust, source authenticity or restorability. | P1 |
| Notifications/renewals | Administrator Telegram configuration, preview/test, durable package-expiry reminders and Chinese delivery history were published at `bf8eaa8`; see [operator guide](notifications.md). Current source additionally provides [user renewal requests and Web administrator review](renewals.md), frozen cycle days, hashed references, cancellation and atomic/idempotent extension; 16 backend and 10 frontend focused checks passed. Production was not upgraded and no real Telegram canary was run. | User binding, Bot/Mini App, daily digest, threshold/server/security alerts and announcements remain absent. No payment integration or automatic renewal; Web review does not require Telegram. | P1 |
| Dynamic system settings | Two-field site text **published on `main` at `f0ed515`**: typed SQLite store, public projection, administrator CAS saves and Chinese Ant Design UI, alongside existing environment/probe/template settings. [Operator guide](system-settings.md) and [pinned-source design](system-settings-plan.md) record the scope. Backend 2500/zero skips, frontend 1013/75 files, both builds, browser 14 phases/27 reviewed PNGs, working-tree and exact-Git Docker 10 phases, independent postchecks and all four CI jobs passed. Production was not upgraded. | Complete general settings, Logo/background/upload, announcements, silent mode, update channels and arbitrary feature flags remain absent. This slice does not change Probe settings or add license gating. | P1/P2 |
| Legacy migration | Identity/package/profile subset preview/import implemented | Backup fetch/upload, complete node/server import, rules/scripts/external subscriptions and automated Agent takeover absent. | P1 |

## Agent parity

| Behavior | Status |
| --- | --- |
| Outbound WebSocket and HTTP fallback, durable result replay | Implemented |
| New-host installation command from the panel | Implemented; follows the official create-server/install/connect flow. Ten-minute ticket and same-host claim nonce replace putting the long-lived credential in a download URL. Dedicated non-root systemd installation and real traffic passed over WebSocket and HTTP. |
| Xray/Nginx operations, diagnostics, logs, WARP, lifecycle and config workspace | Implemented with capability gates |
| `/api/child/agent/probe-master-url` | Published: a 12-second authenticated WebSocket probe using the existing token, CA and HTTPS policy. Business failure returns HTTP-command status 200 with `success: false`, matching the reference contract. |
| `/api/child/agent/update-master-url` | Published: validates and atomically persists the exact loaded private config, honors `only_if_recovery`, replies before reconnect and never stores a plaintext token elsewhere. |
| `/api/child/agent/switch-listen-port` | Intentionally unsupported: Open Node Agent opens no inbound management listener. Capability is false and the control plane must reject before queuing. |
| `/api/child/agent/switch-xray-mode` | Intentionally unsupported: both Open Node runtime modes use a separate Xray process; neither is the reference's statically embedded runtime. Capability is false rather than misreporting a switch. |
| Online users/IPs | Not wired. The pinned Xray fork supports `GetAllOnlineUsers` and `GetStatsOnlineIpList`, but Open Node does not enable `statsUserOnline` or report those results. |

Pinned legacy `mmw-agent` 0.4.7 implements all four Agent-settings paths but
advertises only the older `rpc`, `stream` and `return_route_test` capability set.
Compatibility inference is therefore limited to version 0.4.7 or newer on the
legacy encrypted `/api/remote/ws` transport. Native Open Node transport never
infers missing capabilities.

## Probe/runtime notes

- The pinned Xray fork already provides AnyTLS, Snell v4-v6, Mieru and Hysteria
  user attribution. Open Node's empty-user, Mieru UDP-target and AnyTLS UDP
  patches are deliberate compatibility/safety enhancements, not missing parity.
- Native `device_limit` counts concurrent dispatcher flows, not unique IPs or
  physical devices. Product copy must not call it a physical-device limit.
- The limiter overlay currently disables Vision splice for all authenticated
  managed flows, including unlimited users. Correctness is preserved, but the
  performance difference remains P2.
- The public probe must retain interval polling even while WebSocket is open;
  the reference does this to survive proxies that keep a socket open while no
  longer forwarding frames.

## Deployment truth

The published root installer targets a fresh Debian/Ubuntu host with Docker
Compose and a single-node SQLite control plane. The panel now provides a
**separate** Debian 12 amd64 Agent/official-Xray command after an operator
configures the canonical HTTPS URL. Neither installer configures DNS, obtains
a public TLS certificate, takes over an edge reverse proxy, migrates a complete
MMWX installation or provides PostgreSQL operations. Those remain separate
gates. The real bootstrap gate used the HTTPS root URL and forced each
transport separately; reverse-proxy subpaths and Auto fallback are supported
by code/unit coverage, not claimed as new end-to-end deployment evidence.
