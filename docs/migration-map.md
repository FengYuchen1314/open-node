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
   - Next: higher-level inbound, outbound, routing, certificate, and website
     workflows from the active control plane.
4. Port probe read-only API and public status UI.
   - Done: HTTP public probe payload and series endpoints plus the Vue Probe
     view.
   - Done: public probe WebSocket stream compatible with the `mmwx-probe`
     Worker `/api/stream` mapping.
   - Next: richer probe appearance/settings data.
5. Revisit Xray integration once the agent protocol surface is stable.
