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
3. Port agent heartbeat, telemetry, and command execution contracts.
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
   - Done: Clash, sing-box, URI-list, and base64 subscription rendering plus
     durable per-user traffic ledgering from agent telemetry.
   - Done: richer subscription template presets and import/export workflows.
   - Done: quota status, over-limit subscription blocking, manual traffic
     reset, and idempotent monthly reset-due automation.
   - Done: external Xray takeover command wrapper for merging an existing
     `-config` plus `-confdir` runtime into the MMWX-managed single config.
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
   - Next: deeper Xray runtime integration.
5. Revisit Xray integration once the agent protocol surface is stable.
