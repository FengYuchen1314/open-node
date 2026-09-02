# MMWX Source Parity Matrix

This matrix replaces percentage-based completion estimates. A feature is marked
complete only when the pinned reference behavior has a corresponding Open Node
implementation and an executable release gate. Similar labels or a queued Agent
path do not count as implementation.

## Current requested scope (2026-09-01)

The user now explicitly targets **fresh deployments only**, with **no legacy
MMWX migration and no Bot integration**. Legacy import/takeover, Telegram user
binding, Bot/Mini App and Bot-only workflows are excluded from completion
requirements. Historical implemented features stay available; their existence
does not create an obligation to extend these excluded modules. Web renewal
review, Web announcements, native backup/restore and new-host installation
remain in scope. Historical migration/Bot priorities below must not override
this user instruction.

Fresh control-plane installation now means either the default SQLite backend or
the installer-selected PostgreSQL backend. Backend conversion is not implied:
an installed backend is fixed, SQLite/PostgreSQL backups are type-specific, and
neither path accepts a legacy MMWX database.

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

Node speed testing is distributed separately from the four primary repositories. The supplementary
official `MMWOrg/mmwX-plugins` repository was audited at
`94cff8af327a9ef7957dede3eb654bef6c525e5b`; the `speedtest-v0.1.5` source and installers are pinned
to tag commit `7457360b40fb2045a7eeee4b9c68358cdbaf94e4`. This source is used only for the speed-tester
feature contract and does not replace the four primary pins above.

## Requested frontend rewrite

The administrator console, subscriber portal and independent public Probe use
React with project-owned semantic components. The current source has no third-party
component-library runtime and defaults to Simplified Chinese on all three surfaces;
see [frontend.md](frontend.md). Historical release evidence remains in
[testing.md](testing.md); it is not evidence that every remaining parity item is complete.

## Release blockers

| Priority | Area | Source-confirmed result | Release condition |
| --- | --- | --- | --- |
| P0 | GitHub installer | The published SQLite path passed anonymous Raw-URL installation, administrator login, status, no-op update, data-preserving uninstall, rollback and injected-failure checks. The current candidate adds fresh-only PostgreSQL selection, a generated private password, a pinned official PostgreSQL 15 image with no host database port, exact Compose identities and database-aware stopped-volume recovery bundles. | SQLite release gate passed. PostgreSQL requires the same clean-host one-command, health, backup/restore and failure-isolation gate before this candidate is promoted; public DNS/TLS and remote Agent installation remain separate operational work. |
| P0 | Public probe Worker | Probe-only bundle, credential-stripping Worker boundary, interval polling and WebSocket reconnect implemented. The actual dry-run Worker bundle and production assets passed anonymous HTTP/WebSocket/browser acceptance in the locked Miniflare/workerd runtime on the VPS. | Local gate passed on 2026-08-31, including desktop/mobile, idle-socket polling, rejected reconnect and secret-free requests/responses. Real Cloudflare account deployment and public HTTPS remain separate operational checks. |
| P0/P1 | Package accounting | Each telemetry delta is frozen using the reporting node multiplier and reference-compatible package factors: `oneway` x1, `twoway` x2, both over upload plus download. Raw directions remain separately auditable. | Passed ledger epoch/reset, plan-edit freeze, SQLite backfill, archive preservation and public-header tests on the isolated VPS. |
| P1 | Agent settings | All four high-level routes are capability-gated. Authenticated master probe/update is implemented; unsupported inbound-port and embedded-runtime switching stay disabled. | Passed Agent tests and release gates for the implemented capability surface. |
| P1 | Administrator MFA | Published in `main` at `6ca84e2`: encrypted TOTP, one-use recovery codes, password-bound challenges, mandatory enrollment and local reset. Documented safety differences from the pinned source are explicit. | Passed the 955-test backend suite at `45515b6`, 26 focused authentication tests at `58b33af`, frontend tests/builds and isolated production-browser workflows on 2026-08-30/31. Configure the persistent encryption key before use; multi-admin RBAC remains separate. |
| P1 | Panel-issued Agent installation | New-host workflow implemented with short-lived single-host tickets, pinned public Agent 0.3.0a2 and official Xray, private inputs and dedicated non-root systemd ownership. | Agent 0.3.0a2 retains the tested online collection and IPv6 certificate validation and adds bounded Nginx version reporting. Its exact release passed byte verification, real-systemd installation/lifecycle, anonymous GitHub upgrade, traffic and rollback over WebSocket/HTTP. HTTPS infrastructure, existing-host migration, fork-only runtimes and general OS/architecture support remain separate. |
| P1 | External subscriptions | Administrator management and subscriber-owned self-service for HTTPS YAML/URI/Base64; encrypted snapshots, manual preview/confirmation, primary-link merging and opt-in persistent scheduled refresh (`saved_only` / `all`). | Scheduled refresh has a shared atomic merge, source-revision and lease fences, bounded fetches, failure backoff and Chinese controls. See [testing](testing.md) for the current targeted gate. The provider/rule ecosystem remains separate. |
| P1 | Server sharing/federation | Free owner and consumer workspaces implement one-time hashed tokens, limited/full scopes, inbound ownership, revocation cleanup, encrypted imported secrets, bounded HTTPS transport and durable commands. Imported servers are ordinary inventory, traffic, DDNS, probe and node-catalog assets; five-second refresh, user-credential relay, Nginx version projection, official X25519/HMAC/HKDF/AES-GCM negotiation, plaintext downgrade and reverse `/api/federation/*` interop are implemented; see [server sharing](server-sharing.md). The v1 backup now carries the federation vault key/marker and checks each stored token's server, owner URL and purpose binding. | Passed the focused owner/consumer, official wire-format, subscription-access, backup-coordination, role-isolation and secure-channel gates. Remote owner trust/availability is still an operator review item. Commercial Reality pools and license enforcement are intentionally absent. |
| P1 | Dynamic DNS | [DDNS](ddns.md) implements six official provider families, encrypted credential reuse, A/AAAA drift updates, explicit/automatic provider selection, federated-server projection, durable leases, manual sync, status and retries. | Targeted request-contract tests pass. Real writes with operator-owned provider accounts remain an operational acceptance boundary. |
| P1 | Custom rules, Proxy Providers and scripts | [Subscription customizations](subscription-customizations.md) implement owner-scoped DNS/rules/rule-providers CRUD, replace/prepend/append rendering, profile selection, missing policy groups, snapshot-backed Mihomo Providers, server-side `mmw` groups, optional IPinfo GeoIP filtering, official Mihomo request headers and isolated QuickJS `post_fetch` / `pre_save_nodes` hooks with administrator and subscriber Chinese UI. | The pinned source's on-disk `custom_rule_applications` history remains intentionally different because Open Node renders from managed templates on every request. Real IPinfo use requires an operator token. |
| P1 | Node speed tests | [Node speed tests](node-speedtests.md) implement the official master/home-tester workflow: real proxy download, three-sample Cloudflare latency, egress IP, one/eight threads, serial source queues, asynchronous polling/history, one-time tester pairing and the official reverse WebSocket path. Mihomo v1.19.30 and sing-box v1.14.0 are fixed by URL, size and SHA-256. | Six focused Linux tests, frontend type/build gates, actual pinned Mihomo execution and actual pinned sing-box execution passed in the isolated candidate. A real residential tester and public WSS endpoint remain an operator-environment acceptance check. |

## Control-plane modules

| Module | Current Open Node status | Remaining reference behavior | Priority |
| --- | --- | --- | --- |
| Server/Agent inventory and telemetry | Implemented for local and federated servers. [DDNS](ddns.md) covers both; [server sharing](server-sharing.md) projects owner status, traffic, Xray/Nginx scan state and version, plus CPU/memory/disk/network telemetry into the ordinary inventory and node catalog. [Node speed tests](node-speedtests.md) add master and official-compatible home-tester measurements. | The commercial Reality resource pool remains absent by design. | P2 |
| Durable Agent commands | Implemented and stronger than the reference in lease recovery, command journals, dependencies and reviewable change sets | Complete compatibility still depends on every advertised child route being executable. | P1 gate |
| Xray/Nginx/WARP lifecycle | Implemented with explicit host ownership and rollback workflows; [panel-issued Agent bootstrap](agent-bootstrap.md) adds verified new-host Agent/official-Xray installation | Automatic existing-host takeover, embedded runtime and installation of Nginx/WARP/fork-only protocols are not part of panel bootstrap. | P1/P2 by migration scope |
| Managed subscriptions | Users, plans, nodes, credentials, quota/reset, templates, profiles, temporary links, access reconciliation and source-compatible frozen accounting; [external sources](external-subscriptions.md) support administrator/subscriber manual and scheduled YAML/URI/Base64 workflows; [subscription customizations](subscription-customizations.md) cover owner-scoped rule injection, client/server Provider processing and isolated JavaScript hooks; [manual renewals](renewals.md) provide atomic review and extension | Official on-disk application history is intentionally replaced by request-time managed rendering. Renewal is not payment-provider integration. | P1/P2 |
| Subscriber feature permissions | [Global optional-page policy and per-user quotas](subscriber-permissions.md) implemented for subscription customizations, external sources, private routes and renewal requests. Direct account APIs enforce the policy; source creation uses the original write transaction. Rule/Provider/script selection and federated node/user assignment use the same managed catalog. | Private-route count/daily policy remains a separate existing control. | P2 |
| Subscription formats | Clash, sing-box, Xray, URI list, Base64, Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern implemented with explicit compatibility filtering and UA selection; see [client guide](subscription-clients.md) | Native import/traffic acceptance in all six added clients and broader client-specific options remain unverified/unsupported as documented. | P2 individually, P1 as migration set |
| Certificates | ACME account/EAB, DNS-01, HTTP-01, encrypted vault, versions, deployment and revocation implemented; [self-signed generation](certificates.md) adds P-256/serverAuth, DNS/IP SANs and Chinese confirmation. The root installer now offers [pinned Caddy public HTTPS](public-deployment.md) after operator DNS preparation. | Managed public mode does not edit DNS/firewalls or take over an existing edge. Self-signed is not public CA trust. IPv6-literal Agent validation requires 0.3.0a1; old hosts are not automatically upgraded. | Public installer implemented; operator-domain acceptance remains |
| Public probe data plane | Public servers/settings/series/targets/WebSocket, scheduled tasks, return routes, probe-only build and resilient polling/reconnect implemented; anonymous Worker browser gate passed | Reference theme/view parity remains; real Cloudflare deployment needs operator inputs. Online IPs are intentionally restricted to the administrator workspace, not public probe payloads. | P1/P2 |
| Account security | Subscriber sessions, TOTP, recovery codes and device revocation implemented; [administrator MFA](administrator-security.md), mandatory enrollment, replay protection and local recovery published. [Security events and IP bans](security-management.md) add append-only login/probe/ban/unban history, persistent automatic/manual bans, official-default subscription probe thresholds and a Chinese administrator console. | Multi-administrator RBAC, Turnstile, unknown-UA policy, editable general login/subscription rate settings and ban-notification controls remain. | P1 |
| Setup/backup/database | [Browser first-run setup](initial-setup.md) creates the first administrator, optional nickname/email/HTTPS avatar and site titles without a setup credential; a separately issued 30-minute credential remains only for pre-administrator v1 backup restore. Profile updates are revision-bound in the Chinese system settings page. [Public deployment](public-deployment.md) adds domain-driven Caddy/TLS/proxy setup. CLI setup, installer-level backend-aware stopped-volume backups, age tools, [SQLite/PostgreSQL consistent snapshots, Web backup jobs/downloads, browser v1 restore and isolated review](backups.md) are implemented. SQLite activates a validated private tree before opening the database; PostgreSQL validates a custom dump in a `template0` staging database, switches by journaled database rename only after success, retains the previous database for rollback, and switches the filesystem state tree in the same startup activation. The fresh installer supports default SQLite or a pinned official PostgreSQL 15 service. | Legacy conversion and cross-backend migration are out of scope. PostgreSQL CLI restore is intentionally not exposed; its configured browser path is the supported v1 restore. DNS-account automation, source authenticity and remote Agent state/trust still require operator review. Final PostgreSQL clean-install/browser gate remains a release condition. | P1 |
| Notifications/renewals | Existing administrator Telegram notices remain available, without new Bot development. [User renewal requests and Web administrator review](renewals.md), frozen cycle days, cancellation and atomic extension are implemented. [Web announcements](announcements.md) now cover administrator publish/list/delete, expiry and active-package user display. | Bot/Mini App, Telegram binding, automatic blocked-node announcement broadcast and Bot-only alerts are excluded by the current request. No payment integration or automatic renewal is claimed. | Web flow implemented; Bot out of scope |
| Dynamic system settings | Two-field site text, database-backed Logo/login-background uploads, project semantic light/dark/system themes and Chinese [Web announcement](announcements.md) controls are implemented without a license gate. The services accept the supported SQLite and PostgreSQL dialects. See the [site settings](system-settings.md), [appearance guide](appearance.md) and [pinned-source design](system-settings-plan.md). | Silent mode, update channels, arbitrary feature flags and executable custom HTML/CSS/JavaScript remain absent. Probe settings stay independent. | P1/P2 |
| Legacy migration | Historical identity/package/profile subset preview/import remains available | Not being extended: the user explicitly excluded old MMWX migration and existing-host takeover. | Out of scope |

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
| Online users/IPs | Implemented in Agent 0.3.0a1 using the pinned fork's `GetAllOnlineUsers` and `GetStatsOnlineIpList`; new configs enable `statsUserOnline`. Bounded collection, explicit unavailable/partial states, controller-receipt freshness and administrator-only Chinese search/table are implemented. Real VLESS connect/disconnect and focused checks passed; see [online users](online-users.md). Existing hosts require explicit Agent/config upgrades. |

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

The current root installer targets a fresh Debian/Ubuntu host with Docker
Compose. SQLite remains the default; setting
`OPEN_NODE_DATABASE_BACKEND=postgresql` on the initial run selects a pinned
official PostgreSQL 15 service, generates a private password, publishes no
database port and stores database pages in a separate managed volume. The
backend choice is immutable after installation. Update backups use an SQLite
integrity-checked state archive or, for PostgreSQL, a custom dump that is listed
and fully restored into a temporary `template0` database before publication.

Given a prepared public DNS hostname and open ports 80/443, the installer can
also run a pinned official Caddy gateway, obtain trusted TLS, verify local SNI
health and configure the Agent URL. It does not edit DNS/firewalls, take over an
existing edge, migrate a complete MMWX installation or convert between database
backends. The panel's **separate** Debian 12 amd64 Agent/official-Xray command
remains a new-host step after the control plane is initialized. The existing
bootstrap gate used the HTTPS root URL and forced each transport separately;
reverse-proxy subpaths and Auto fallback are supported by code/unit coverage,
not claimed as new end-to-end deployment evidence. PostgreSQL promotion still
depends on the clean isolated installer and browser restore release gate above.
