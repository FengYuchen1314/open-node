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

## Release blockers

| Priority | Area | Source-confirmed result | Release condition |
| --- | --- | --- | --- |
| P0 | GitHub installer | Published on public `main`; the anonymous Raw URL passed isolated installation, administrator login, status, no-op update, data-preserving uninstall and cleanup on 2026-08-30. The maintainer smoke passed rollback and injected-failure scenarios. | Passed for the documented single-host Docker/SQLite scope. Public DNS/TLS and remote Agent installation remain separate work. |
| P0 | Public probe Worker | Probe-only bundle, credential-stripping Worker boundary, interval polling and WebSocket reconnect implemented. The actual dry-run Worker bundle and production assets passed anonymous HTTP/WebSocket/browser acceptance in the locked Miniflare/workerd runtime on the VPS. | Local gate passed on 2026-08-31, including desktop/mobile, idle-socket polling, rejected reconnect and secret-free requests/responses. Real Cloudflare account deployment and public HTTPS remain separate operational checks. |
| P0/P1 | Package accounting | Each telemetry delta is frozen using the reporting node multiplier and reference-compatible package factors: `oneway` x1, `twoway` x2, both over upload plus download. Raw directions remain separately auditable. | Passed ledger epoch/reset, plan-edit freeze, SQLite backfill, archive preservation and public-header tests on the isolated VPS. |
| P1 | Agent settings | All four high-level routes are capability-gated. Authenticated master probe/update is implemented; unsupported inbound-port and embedded-runtime switching stay disabled. | Passed Agent tests and release gates for the implemented capability surface. |
| P1 | Administrator MFA | Published in `main` at `6ca84e2`: encrypted TOTP, one-use recovery codes, password-bound challenges, mandatory enrollment and local reset. Documented safety differences from the pinned source are explicit. | Passed the 955-test backend suite at `45515b6`, 26 focused authentication tests at `58b33af`, frontend tests/builds and isolated production-browser workflows on 2026-08-30/31. Configure the persistent encryption key before use; multi-admin RBAC remains separate. |
| P1 | Panel-issued Agent installation | New-host workflow implemented with short-lived single-host tickets, pinned public Agent 0.3.0a0 and official Xray, private inputs and dedicated non-root systemd ownership. | Production-browser and real root-URL WebSocket/HTTP bootstrap, VLESS traffic, replay refusal, repeat-install refusal and cleanup passed on the isolated VPS. HTTPS infrastructure, existing-host migration, fork-only runtimes and general OS/architecture support remain separate. |

## Control-plane modules

| Module | Current Open Node status | Remaining reference behavior | Priority |
| --- | --- | --- | --- |
| Server/Agent inventory and telemetry | Implemented | DDNS, federation/server sharing, home speed tester and Reality sharing are absent. | P2, P1 for dependent migrations |
| Durable Agent commands | Implemented and stronger than the reference in lease recovery, command journals, dependencies and reviewable change sets | Complete compatibility still depends on every advertised child route being executable. | P1 gate |
| Xray/Nginx/WARP lifecycle | Implemented with explicit host ownership and rollback workflows; [panel-issued Agent bootstrap](agent-bootstrap.md) adds verified new-host Agent/official-Xray installation | Automatic existing-host takeover, embedded runtime and installation of Nginx/WARP/fork-only protocols are not part of panel bootstrap. | P1/P2 by migration scope |
| Managed subscriptions | Users, plans, nodes, credentials, quota/reset, templates, profiles, temporary links, access reconciliation and source-compatible frozen accounting implemented | External subscription/provider/rule/script ecosystem absent. | P1/P2 |
| Subscription formats | Clash, Surge, sing-box, Xray, URI list and Base64 implemented | Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern-specific output absent. | P2 individually, P1 as migration set |
| Certificates | ACME account/EAB, DNS-01, HTTP-01, encrypted vault, versions, deployment and revocation implemented | One-click self-signed certificate and automatic control-plane Nginx/HTTPS takeover absent. | P2/P1 for public one-click scope |
| Public probe data plane | Public servers/settings/series/targets/WebSocket, scheduled tasks, return routes, probe-only build and resilient polling/reconnect implemented; anonymous Worker browser gate passed | Reference theme/view parity and online IP data remain; real Cloudflare deployment needs operator inputs. | P1/P2 |
| Account security | Subscriber sessions, TOTP, recovery codes and device revocation implemented; [administrator MFA](administrator-security.md), mandatory enrollment, replay protection and local recovery published | Multi-administrator RBAC, security events and IP-ban console absent. | P1 |
| Setup/backup/database | CLI administrator creation and installer-level stopped-volume backup implemented | Browser setup, downloadable backup/restore, SQLite-to-PostgreSQL migration and application update UI absent. | P1 |
| Notifications/renewals | Renewal metadata is displayed | Telegram, notification rules, renewal requests, threshold alerts and scheduler absent. | P1 |
| Dynamic system settings | Environment settings plus focused probe/template settings implemented | General settings store/UI, branding, announcements, silent mode, update channels and feature flags absent. | P1/P2 |
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
