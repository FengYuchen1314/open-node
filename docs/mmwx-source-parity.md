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
| P1 | Panel-issued Agent installation | New-host workflow implemented with short-lived single-host tickets, pinned public Agent 0.3.0a1 and official Xray, private inputs and dedicated non-root systemd ownership. | The prior 0.3.0a0 bootstrap/browser/traffic gate remains the installation baseline; 0.3.0a1 adds tested online collection and IPv6 certificate validation with a byte-verified release build. HTTPS infrastructure, existing-host migration, fork-only runtimes and general OS/architecture support remain separate. |
| P1 | External subscriptions | Administrator management and subscriber-owned self-service for HTTPS YAML/URI/Base64; encrypted snapshots, manual preview/confirmation, primary-link merging and opt-in persistent scheduled refresh (`saved_only` / `all`). | Scheduled refresh has a shared atomic merge, source-revision and lease fences, bounded fetches, failure backoff and Chinese controls. See [testing](testing.md) for the current targeted gate. The provider/rule ecosystem remains separate. |
| P1 | Server sharing/federation | Free owner and consumer workspaces implement one-time hashed tokens, limited/full scopes, inbound ownership, revocation cleanup, encrypted imported secrets, bounded HTTPS transport and durable command polling; see [server sharing](server-sharing.md). | First slice only: imported servers are not ordinary inventory/catalog entries; optional official message encryption, reverse official-client interop, scheduled refresh and Reality sharing remain absent. |
| P1 | Dynamic DNS | [DDNS](ddns.md) implements six official provider families, encrypted credential reuse, A/AAAA drift updates, explicit/automatic provider selection, durable leases, manual sync, status and retries. | Targeted request-contract tests pass. Real writes with operator-owned provider accounts and federated-server DDNS remain operational/unimplemented boundaries. |
| P1 | Custom rules and Proxy Providers | [Subscription customizations](subscription-customizations.md) implement owner-scoped DNS/rules/rule-providers CRUD, replace/prepend/append rendering, profile selection, missing policy groups and snapshot-backed Mihomo Providers with administrator and subscriber Chinese UI. | JavaScript hooks, server-side `mmw` processing, GeoIP filters and custom upstream headers remain. |

## Control-plane modules

| Module | Current Open Node status | Remaining reference behavior | Priority |
| --- | --- | --- | --- |
| Server/Agent inventory and telemetry | Implemented; [DDNS](ddns.md) covers local Agent servers and [server sharing](server-sharing.md) has a separate owner/consumer first slice | Federated-server integration into ordinary inventory/node catalog and DDNS, optional official federation crypto/reverse interop, home speed tester and Reality sharing remain absent. | P2, P1 for dependent workflows |
| Durable Agent commands | Implemented and stronger than the reference in lease recovery, command journals, dependencies and reviewable change sets | Complete compatibility still depends on every advertised child route being executable. | P1 gate |
| Xray/Nginx/WARP lifecycle | Implemented with explicit host ownership and rollback workflows; [panel-issued Agent bootstrap](agent-bootstrap.md) adds verified new-host Agent/official-Xray installation | Automatic existing-host takeover, embedded runtime and installation of Nginx/WARP/fork-only protocols are not part of panel bootstrap. | P1/P2 by migration scope |
| Managed subscriptions | Users, plans, nodes, credentials, quota/reset, templates, profiles, temporary links, access reconciliation and source-compatible frozen accounting; [external sources](external-subscriptions.md) support administrator/subscriber manual and scheduled YAML/URI/Base64 workflows; [custom rules and Proxy Providers](subscription-customizations.md) cover owner-scoped rule injection and safe Provider snapshots; [manual renewals](renewals.md) provide atomic review and extension | The JavaScript hook and advanced server-side Provider ecosystem remains incomplete. Renewal is not payment-provider integration. | P1/P2 |
| Subscriber feature permissions | [Global optional-page policy and per-user quotas](subscriber-permissions.md) implemented for personal templates, external sources, private routes and renewal requests. Direct account APIs enforce the policy; template/source creation uses the original write transaction. | Official generator, subscribe-file, custom-rule and node-page semantics depend on the remaining rule/Provider and sharing modules. Private-route count/daily policy remains a separate existing control. | P1/P2 |
| Subscription formats | Clash, Surge, sing-box, Xray, URI list, Base64, Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern implemented with explicit compatibility filtering and UA selection; see [client guide](subscription-clients.md) | Native import/traffic acceptance in all six added clients and broader client-specific options remain unverified/unsupported as documented. | P2 individually, P1 as migration set |
| Certificates | ACME account/EAB, DNS-01, HTTP-01, encrypted vault, versions, deployment and revocation implemented; [self-signed generation](certificates.md) adds P-256/serverAuth, DNS/IP SANs and Chinese confirmation. The root installer now offers [pinned Caddy public HTTPS](public-deployment.md) after operator DNS preparation. | Managed public mode does not edit DNS/firewalls or take over an existing edge. Self-signed is not public CA trust. IPv6-literal Agent validation requires 0.3.0a1; old hosts are not automatically upgraded. | Public installer implemented; operator-domain acceptance remains |
| Public probe data plane | Public servers/settings/series/targets/WebSocket, scheduled tasks, return routes, probe-only build and resilient polling/reconnect implemented; anonymous Worker browser gate passed | Reference theme/view parity remains; real Cloudflare deployment needs operator inputs. Online IPs are intentionally restricted to the administrator workspace, not public probe payloads. | P1/P2 |
| Account security | Subscriber sessions, TOTP, recovery codes and device revocation implemented; [administrator MFA](administrator-security.md), mandatory enrollment, replay protection and local recovery published | Multi-administrator RBAC, security events and IP-ban console absent. | P1 |
| Setup/backup/database | [Browser first-run setup](initial-setup.md) creates the first administrator and site titles with a locally issued 30-minute credential; [public deployment](public-deployment.md) adds domain-driven Caddy/TLS/proxy setup. CLI setup, installer-level stopped-volume backups, age tools, [consistent snapshots](backup-runtime.md), [Web backup jobs/downloads plus offline v1 restore](backups.md) are implemented. Installer-managed official `main` deployments add a fixed-function [application update panel](application-updates.md), exact target binding and the same backup/health/recovery transaction. | Setup profile/avatar and PostgreSQL options, DNS-account automation, browser upload restore and no-user initialization restore remain absent. Legacy conversion is out of scope. Source authenticity and remote Agent state/trust still require operator review. The browser setup has not had a new public-domain browser gate. | P1 |
| Notifications/renewals | Existing administrator Telegram notices remain available, without new Bot development. [User renewal requests and Web administrator review](renewals.md), frozen cycle days, cancellation and atomic extension are implemented. [Web announcements](announcements.md) now cover administrator publish/list/delete, expiry and active-package user display. | Bot/Mini App, Telegram binding, automatic blocked-node announcement broadcast and Bot-only alerts are excluded by the current request. No payment integration or automatic renewal is claimed. | Web flow implemented; Bot out of scope |
| Dynamic system settings | Two-field site text, SQLite-backed Logo/login-background uploads, standard Ant Design light/dark/system themes and Chinese [Web announcement](announcements.md) controls are implemented without a license gate. See the [site settings](system-settings.md), [appearance guide](appearance.md) and [pinned-source design](system-settings-plan.md). Targeted backend/frontend gates, type checks and the production build passed; production was not upgraded. | Silent mode, update channels, arbitrary feature flags and executable custom HTML/CSS/JavaScript remain absent. Probe settings stay independent. | P1/P2 |
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

The published root installer targets a fresh Debian/Ubuntu host with Docker
Compose and a single-node SQLite control plane. Given a prepared public DNS
hostname and open ports 80/443, it now runs a pinned official Caddy gateway,
obtains trusted TLS, verifies local SNI health and configures the Agent URL.
It does not edit DNS/firewalls, take over an existing edge, migrate a complete
MMWX installation or provide PostgreSQL operations. The panel's **separate**
Debian 12 amd64 Agent/official-Xray command remains a new-host step after the
control plane is initialized. The real bootstrap gate used the HTTPS root URL and forced each
transport separately; reverse-proxy subpaths and Auto fallback are supported
by code/unit coverage, not claimed as new end-to-end deployment evidence.
