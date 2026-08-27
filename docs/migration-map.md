# Migration Map

This document records the starting source map for the Open Node refactor.

## Included Sources

| Source repository | Role in Open Node |
| --- | --- |
| `FengYuchen1314/miaomiaowuX` | Control plane behavior and product workflows |
| `FengYuchen1314/mmw-agent` | Remote agent protocol and host-management workflows |
| `FengYuchen1314/mmwx-probe` | Public probe API and UI behavior |
| `FengYuchen1314/Xray-core-mmwx` | Runtime integration details used by the agent |

## Excluded Sources

| Source repository | Reason |
| --- | --- |
| `FengYuchen1314/miaomiaowu` | Older base project, outside the current MMWX refactor line |
| `FengYuchen1314/NodeControll` | Archived rebuild/research branch, explicitly out of scope |

## First Migration Slices

1. Keep the no-license contract permanent and test-covered.
2. Port control-plane identity, server inventory, and agent registration models.
   - Done: local administrator creation/recovery, Argon2id password storage,
     persistent cookie sessions with expiry/revocation, CSRF and Origin checks,
     and persistent login rate limits. Management APIs require sign-in; Agent
     tokens and public subscription/probe access remain separate.
   - Done: Vue sign-in, sign-out, password change, and responsive navigation.
3. Port agent heartbeat, telemetry, and command execution contracts.
   - Done: an independently implemented Linux agent in `agent/`, distributed
     as a Python wheel without activation checks or the reference Agent source.
     It supports WebSocket/HTTP, persistent deduplication, host telemetry,
     Xray config validation/edits, service control, and client provisioning.
   - Done: host deployment CLI for a dedicated non-root systemd service,
     version/digest-specific environments, upgrade preflight/readiness,
     failed-start rollback, interrupted-switch recovery, and data-preserving
     uninstall/reinstall or explicit purge. The real VPS lifecycle smoke
     verifies service ownership, data preservation, and actual forwarding.
   - Done: HTTP `/api/v1/agents/scan` accepts node-token-authenticated scan
     reports without requiring an operator session.
   - Done: the installed independent wheel has exercised real VLESS forwarding,
     new-user provisioning, user traffic reporting, failed-restart rollback,
     ordered recovery, and restart persistence over both transports on the VPS.
   - Done: HTTP heartbeat plus telemetry/traffic reports with Xray stats,
     system counters, sysmetrics, latency samples, user speeds, and connection
     counts.
   - Done: transport-neutral command queue with master-created commands,
     agent leasing, and agent result submission.
   - Done: WebSocket agent auth, heartbeat, traffic ingestion, immediate
     `rpc_call` dispatch, and `rpc_reply` command completion.
   - Done: streaming `rpc_stream_data` frame persistence for `stream=true`
     WebSocket RPC commands.
   - Done: specialized child operation wrappers for system info, traffic,
     speed, and domain latency probes.
   - Done: maintenance wrappers for Xray/nginx install and remove, WARP
     install/status/remove, and agent upgrade/uninstall.
   - Done: diagnostic/config-preparation wrappers for service status/control,
     service logs, system NICs, agent-side scan, and Xray config validation.
   - Done: control-plane wrappers and frontend client mappings for Xray/nginx
     config read/write, config-file list/read/write, WARP credential updates,
     and agent xray-mode/listen-port/master-URL setting updates.
   - Done: dedicated Vue config workspace plus expandable command-result
     rendering for request bodies, agent result bodies, errors, and stream
     frames.
   - Done: higher-level inbound, outbound, routing, batch apply, certificate,
     nginx SSL/website, return-route, site validation, and limiter operation
     wrappers from the active agent.
   - Done: open subscription catalog tables and APIs for product users,
     managed nodes, plans, user-plan binding, provisioning-batch previews, and
     optional agent `batch-apply` command dispatch.
   - Done: Vue subscription workspace for catalog entry, plan assignment, and
     latest provisioning batch inspection.
   - Done: public subscription token/short-code links, stable per-user
     per-node credentials, Clash YAML rendering, and subscription userinfo
     headers from latest telemetry.
   - Done: rollback-friendly multi-server change sets with forward command
     dispatch, reverse rollback command queuing, and a Vue change workspace.
   - Done: routed-outbound change-set planner and Vue form that recreate the
     active MMWX add-client, add-outbound, add-rule workflow without license
     checks.
   - Done: Clash, sing-box, URI-list, and base64 subscription rendering plus
     durable per-user traffic ledgering from agent telemetry.
   - Done: richer subscription template presets and import/export workflows.
   - Done: quota status, over-limit subscription blocking, manual traffic
     reset, and idempotent monthly reset-due automation.
   - Done: external Xray takeover command wrapper for merging an existing
     `-config` plus `-confdir` runtime into the MMWX-managed single config.
   - Done: latest agent scan-result persistence from WebSocket `scan_result`
     and successful `/api/child/scan` command results, exposed to the Vue
     inventory table as Xray runtime status.
   - Done: scan-derived Xray runtime inventory summaries with protocol/client
     counts, sanitized user labels, sniffing state, and Vue Runtime tab
     display.
   - Done: Xray runtime inventory is enriched with latest telemetry
     `stats.inbound` and `stats.user` traffic counters, matched only by runtime
     inbound tags and already exposed client email labels.
   - Done: current Xray config snapshots now derive sanitized runtime tunnel
     inventory for `protocol=tunnel` inbounds, `tunnel-*` routed forwarding
     rules, and grouped chain hops without exposing outbound secrets.
   - Done: tunnel inventory entries can preview or queue delete commands:
     inbound and chain hops remove tunnel inbounds, while routed tunnel cleanup
     removes the routing rule before removing the matching outbound.
   - Done: ordered runtime tunnel chains can be planned across existing
     servers, with conflict-free hop ports, `/api/child/inbounds` previews,
     queued add commands, follow-up scans, and Vue Runtime tab controls.
   - Done: tunnel-mode deployment can render and queue the active MMWX tunnel
     Nginx/Xray baseline for a single server, with explicit `force` required
     before overwriting snapshots that already contain user runtime content.
   - Done: scan-derived managed-node drafts and one-click catalog node creation
     from runtime inbounds, without importing UUIDs, passwords, PSKs, or account
     secrets from existing Xray config.
   - Done: bulk import for all available missing runtime inbounds into managed
     nodes, with created/existing/skipped accounting in the API and Vue Runtime
     tab.
   - Done: runtime/catalog reconciliation for managed, unmanaged, unavailable,
     stale, missing-runtime, and catalog-only nodes, with sanitized public-field
     drift details in the API and Vue Runtime tab.
   - Done: stale physical managed nodes can sync public runtime fields back
     from scan-derived Xray inbounds, without importing existing UUIDs,
     passwords, PSKs, private keys, account emails, or other runtime secrets.
   - Done: runtime credential reconciliation compares generated catalog client
     emails against scan-derived runtime client labels, surfacing missing and
     extra runtime users without exposing credential secrets.
   - Done: missing runtime clients can be planned or queued as agent
     `batch-apply` repair commands for current active subscriptions, while
     extra runtime clients remain report-only.
   - Done: extra runtime clients can be planned or explicitly queued as
     per-email agent `remove-client` cleanup commands against matching
     inbounds.
   - Done: runtime credential repair and cleanup can queue a follow-up
     `/api/child/scan` command after changes, so reconciliation can refresh
     from the next runtime snapshot.
   - Done: Xray config snapshot recovery now keeps agent-reported drift as
     `pending_recovery`, lets operators accept the agent config as current, or
     queue the master current snapshot back to the agent with test/write/restart
     commands after preserving agent-only inbound/outbound entries from the
     pending config.
   - Done: successful Xray-mutating child command results automatically queue a
     deduplicated config refresh, so snapshots and runtime inventory catch up
     after inbound, outbound, routing, batch, config-file, system-config, and
     external-takeover changes.
   - Done: first registration and reconnect queue deduplicated Xray config
     reads. WebSocket authentication, heartbeats, and results dispatch queued
     work and expired leases, including automatic post-write refreshes. HTTP
     and WebSocket transports claim leases atomically to avoid duplicate pushes.
   - Done: the reference agent's `/api/remote/ws` address and non-registering
     `probe` authentication are supported. A pinned, unmodified `mmw-agent`
     container has exercised initial sync, validated config writes, pushed
     refreshes, reconnect drift, and manual recovery acceptance on the VPS.
   - Done: recovery test/write/restart, tunnel deployment/deletion, credential
     repair/cleanup, and their follow-up scans now have persisted per-server
     command dependencies. Failed steps skip their successors, including
     HTTP-200 `ok=false` Xray validation. SQLite upgrades preserve history,
     and duplicate/concurrent results cannot replace a terminal outcome.
   - Done: Vue command inspection displays waiting/skipped status and prerequisite
     IDs. The unmodified reference-agent smoke verifies ordered recovery and
     proves failed validation leaves a healthy on-disk config untouched with
     zero write/restart attempts.
   - Done: active agent log-file list/delete wrappers and nginx stream-port
     cleanup wrapper, with Vue command controls and path/port validation.
   - Done: compatibility wrappers for the active non-stream Xray/nginx
     install/remove child endpoints while keeping stream wrappers as the
     default UI operations.
   - Next: deeper Xray runtime integration.
4. Port probe read-only API and public status UI.
   - Done: HTTP public probe payload and series endpoints plus the Vue Probe
     view.
   - Done: public probe WebSocket stream compatible with the `mmwx-probe`
     Worker `/api/stream` mapping.
   - Done: richer probe appearance/settings data with local settings
     persistence and Vue controls.
   - Done: richer probe region/provider/renewal metadata with a license-free
     management API, sanitized public payload fields, and Vue display/edit
     controls.
   - Done: public probe visualization parity slice with seven-day traffic
     aggregation, status/region filters, health scoring, latency history
     buckets, quota meters, and live traffic hotspots.
   - Done: return-route result persistence from agent command completions,
     public `return_routes` summaries, settings toggle, and Vue three-carrier
     badges.
   - Done: interactive Vue probe drill-down drawer backed by public 1h/6h/24h
     ping and system series, with SVG latency, loss, resource, and throughput
     charts.
   - Done: private probe task schedules with due dispatch into the existing
     agent command queue plus domain-latency command result ingestion into
     public probe series.
   - Done: public cross-node target comparison endpoint and Vue comparison
     rows using public server indexes and sanitized latency buckets.
   - Done: external Xray takeover command wrapper for migrating public nodes
     that still run multi-file external Xray configs.
   - Done: latest agent scan-result ingestion for Xray running state, version,
     API port, inbound inventory, and config-repair metadata.
   - Done: log-file management and nginx stream-port cleanup wrappers for
     parity with the active agent operational surface.
   - Done: all active `mmw-agent` child route constants now have an Open Node
     command wrapper or documented stream/default equivalent.
   - Next: deeper Xray runtime integration.
5. Revisit Xray integration once the agent protocol surface is stable.

## Remaining Runtime Gates

These passing command and snapshot checks do not prove a complete replacement:

- Complete remote upgrade/uninstall command handlers and Xray binary upgrade
  automation. The host CLI's managed systemd install/upgrade/rollback/removal
  path is implemented and tested; it is not a remote lifecycle handler.
- Support or explicitly migrate existing agents that use MMWX `securechan`
  key exchange; JSON RPC compatibility alone does not cover them.
- Owned Nginx HTTP/TLS/configuration/sites, reverse proxy, stream cleanup,
  supplied certificate deployment/rotation, and crash/transaction recovery are
  implemented in the independent agent. Complete WARP, ACME issuance/renewal,
  diagnostics, and remaining host operations. Unimplemented operations return 501.
- Adapt the high-level legacy tunnel-deploy templates to the independent
  Nginx directory/ownership contract and supported runtime protocols; existing
  MMWX/fork templates are not evidence for this migration path.
- Verify the fork-specific runtime protocols and migration from fork-only
  configurations. Actual VLESS traffic is verified using official Xray with
  the independent agent; the reference-agent smoke still uses external mode
  without a running Xray. Neither proves full fork protocol compatibility.
- Verify external `runtime_mode: systemd` and wider OS/architecture deployment
  coverage. The verified non-root systemd installation owns a managed Xray child.
- Extend dependency coordination to multi-server change sets and rollback,
  including interactions with already-running commands. The new dependencies
  cover the server recovery/runtime workflows, not arbitrary concurrent jobs.
- Complete deployable packaging and broader end-to-end operator workflows
  before calling the product ready for public deployment. Local administrator
  authentication is implemented; this alone is not a complete deployment audit.
