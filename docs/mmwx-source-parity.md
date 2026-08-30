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
| P0 | GitHub installer | `install.sh` is only a local candidate; the documented `raw.githubusercontent.com/.../main/install.sh` URL returns 404 at the audited public `main` commit. | Commit and publish the installer, then install from the anonymous public URL on a fresh VPS and pass rollback/uninstall checks. |
| P0 | Public probe Worker | Published code ships the authenticated administrator SPA. This candidate adds a probe-only bundle, credential-stripping Worker boundary, interval polling and WebSocket reconnect, but it has not passed the release smoke yet. | Publish the probe-only bundle and pass anonymous Worker asset/API/WebSocket browser smoke. |
| P0/P1 | Package accounting | Reference node multipliers affect charged traffic and reference `oneway`/`twoway` semantics differ from Open Node. The current names are therefore migration-incompatible. | Implement frozen per-node attribution and reference charging semantics, or introduce an explicit non-compatible accounting mode and block silent migration. |
| P1 | Agent settings | Four high-level routes were queueable while Open Node Agent returned 501. | Capability-gate all four routes; implement authenticated master probe/update; keep unsupported inbound-port and embedded-runtime switching disabled. |

## Control-plane modules

| Module | Current Open Node status | Remaining reference behavior | Priority |
| --- | --- | --- | --- |
| Server/Agent inventory and telemetry | Implemented | DDNS, federation/server sharing, home speed tester and Reality sharing are absent. | P2, P1 for dependent migrations |
| Durable Agent commands | Implemented and stronger than the reference in lease recovery, command journals, dependencies and reviewable change sets | Complete compatibility still depends on every advertised child route being executable. | P1 gate |
| Xray/Nginx/WARP lifecycle | Implemented with explicit host ownership and rollback workflows | Remote Agent bootstrap from the panel is absent. | P1 |
| Managed subscriptions | Users, plans, nodes, credentials, quota/reset, templates, profiles, temporary links and access reconciliation implemented | Accounting semantics differ; external subscription/provider/rule/script ecosystem absent. | P0/P1 |
| Subscription formats | Clash, Surge, sing-box, Xray, URI list and Base64 implemented | Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern-specific output absent. | P2 individually, P1 as migration set |
| Certificates | ACME account/EAB, DNS-01, HTTP-01, encrypted vault, versions, deployment and revocation implemented | One-click self-signed certificate and automatic control-plane Nginx/HTTPS takeover absent. | P2/P1 for public one-click scope |
| Public probe data plane | Public servers/settings/series/targets/WebSocket, scheduled tasks and return routes implemented | Probe-only deployment, resilient polling/reconnect, theme/view parity and online IP data remain. | P0/P1 |
| Subscriber security | Subscriber sessions, TOTP, recovery codes and device revocation implemented | Administrator MFA, multi-administrator RBAC, security events and IP-ban console absent. | P1 |
| Setup/backup/database | CLI administrator creation and installer-level stopped-volume backup implemented | Browser setup, downloadable backup/restore, SQLite-to-PostgreSQL migration and application update UI absent. | P1 |
| Notifications/renewals | Renewal metadata is displayed | Telegram, notification rules, renewal requests, threshold alerts and scheduler absent. | P1 |
| Dynamic system settings | Environment settings plus focused probe/template settings implemented | General settings store/UI, branding, announcements, silent mode, update channels and feature flags absent. | P1/P2 |
| Legacy migration | Identity/package/profile subset preview/import implemented | Backup fetch/upload, complete node/server import, rules/scripts/external subscriptions and automated Agent takeover absent. | P1 |

## Agent parity

| Behavior | Status |
| --- | --- |
| Outbound WebSocket and HTTP fallback, durable result replay | Implemented |
| Xray/Nginx operations, diagnostics, logs, WARP, lifecycle and config workspace | Implemented with capability gates |
| `/api/child/agent/probe-master-url` | Candidate implements a 12-second authenticated WebSocket probe using the existing token, CA and HTTPS policy. Business failure returns HTTP-command status 200 with `success: false`, matching the reference contract. |
| `/api/child/agent/update-master-url` | Candidate validates and atomically persists the exact loaded private config, honors `only_if_recovery`, replies before reconnect and never stores a plaintext token elsewhere. |
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

The candidate root installer targets a fresh Debian/Ubuntu host with Docker
Compose and a single-node SQLite control plane. It does **not** install remote
Agents/Xray, configure DNS, obtain a public TLS certificate, configure an edge
reverse proxy, migrate a complete MMWX installation or provide PostgreSQL
operations. Those are separate gates; publishing the script does not make them
implicitly complete.
