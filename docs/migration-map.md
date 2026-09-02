# Migration Map

This document records the starting source map for the Open Node refactor.

## Included Sources

| Source repository | Role in Open Node |
| --- | --- |
| `tajiaoyezi/miaomiaowuX` | Control plane behavior and product workflows |
| `tajiaoyezi/mmw-agent` | Remote agent protocol and host-management workflows |
| `tajiaoyezi/mmwx-probe` | Public probe API and UI behavior |
| `tajiaoyezi/Xray-core-mmwx` | Runtime integration details used by the agent |

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
   - Done: legacy `securechan-v1` WebSocket exchange and encrypted auth/RPC/stream
     messages, replay protection, explicit private identity CLI and public-key
     display/copy. Wrong and malformed pins are rejected; independent native
     transports remain available. Legacy HTTP/pull Agents have an explicit
     WebSocket or independent-Agent migration procedure.
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
   - Done: public long-token subscription links, stable per-user per-node
     credentials, Clash YAML rendering, and subscription userinfo headers from
     latest telemetry. Compatibility-only custom short codes and legacy `/x`
     routes are implemented, but the hardened release baseline defaults them
     off. Final regression now covers the default-off path and legacy bearer
     rotation.
   - Done: coordinated multi-server change sets with atomic node reservations,
     persisted cross-node dependencies, prior-work draining, automatic failure
     compensation, in-flight rollback barriers, retry history and explicit
     partial-state acceptance. The Vue workspace exposes these states and
     actions. SQLite upgrades pause legacy executions for operator review.
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
   - Done: scans persist strict versioned Xray runtime capabilities. Mieru UDP
     import and subscription rendering require a running scan no more than ten
     minutes old with integer `mieru_udp_target: 1`; missing or invalid evidence
     degrades to `udp: false`, regardless of catalog node claims.
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
   - Done: the verified Agent prerelease is published and its anonymous GitHub
     download and real-release smoke passed. Public HTTPS remains an
     operator-supplied deployment step.
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
   - This slice inherits the same first-Preview release gates; broader Xray
     runtime parity is deferred.
5. Revisit broader Xray integration after the restricted Preview is accepted.

## First Preview Release Boundary

The fastest supportable first release is an explicit prerelease for Debian 12
`amd64`, one control-plane process/worker, managed non-root Agent/Xray, and new
installations or controlled migrations. It does not claim seamless replacement
of every historical private MMWX resource.

The hardened `cb1eb0c` baseline has completed the local release gates:

- final regression covers aliases disabled by default, legacy bearer rotation,
  access-log suppression, bounded container logs, and SQLite foreign keys on
  every connection;
- the four Agent 0.2.0 candidate assets were rebuilt twice from independent
  archives of the final clean commit. Their byte-for-byte comparison, wheel
  metadata/RECORD, five-file bootstrap structure, checksums, Agent Ruff and all
  544 Agent tests passed; and
- the VPS now runs the hardened image through persistent Compose under an
  enabled/active systemd unit, bound only to `127.0.0.1:8000` with its private
  volume. Backup, container/service restart, Compose down/up and an isolated
  restore have been exercised.

The restricted release P0 is complete. The annotated `agent-v0.2.0` tag points
to final release commit `3bf30c0b488efe6575927d01acca07f6dc0b3662`, the
`BUILD.json` revision matches, and the GitHub prerelease contains exactly the
four specified assets. All four were downloaded again without GitHub
credentials, `SHA256SUMS` passed, and the downloaded wheel passed the real
release WebSocket/HTTP download, upgrade, VLESS forwarding, and rollback smoke.

The current control plane remains an SSH-tunnel Preview. Public HTTPS requires
operator-provided domain/DNS, a client-trusted certificate and the external
reverse-proxy configuration. The local backup/isolated-restore workflow is
verified, but an encrypted off-host recovery copy additionally requires an
operator-selected destination, recipient public key and recovery owner. These
operational inputs are not software implementation gaps and are not yet
deployed facts. Historical private ownership/provider/relay-group discovery and
arbitrary legacy host import remain full-parity work, not first-Preview P0.

## Remaining Runtime Gates

These passing command and snapshot checks do not prove a complete replacement.
The gates below define broader or in-place replacement boundaries; they are not
all blockers for the restricted first-Preview support matrix above:

- Remote Agent upgrade, rollback and uninstall now have an explicit host-opt-in
  [lifecycle helper](agent-lifecycle.md), fixed HTTPS release source, wheel pins,
  durable jobs and delayed final callbacks. This is separate from the host CLI
  and requires compatible native Agents. Independent managed Xray package installation,
  version switching, explicit rollback, durable failure recovery and
  data-preserving removal are implemented separately in
  [xray-releases.md](xray-releases.md), without changing root-owned bootstrap files.
- Legacy `securechan-v1` WebSocket compatibility is implemented and verified
  with the unmodified pinned Agent. Legacy HTTP/pull callbacks remain distinct
  from the independent HTTP lease API and require the explicit migration in
  [legacy-agent-migration.md](legacy-agent-migration.md).
- Owned Nginx HTTP/TLS/configuration/sites, reverse proxy, stream cleanup,
  supplied certificate deployment/rotation, and crash/transaction recovery are
  implemented in the independent agent. Native [diagnostics](agent-diagnostics.md)
  now include TCP/ICMP latency, structured NextTrace routes and scoped log
  maintenance. Root-only [host policy updates](agent-host-policy.md) now change
  diagnostics permissions and verified NextTrace binaries in place, with
  private rollback snapshots, crash recovery and unchanged old lifecycle
  helpers. Broader tracing-tool/OS coverage remains outstanding.
  Native [WARP](warp.md) now supports free
  registration, owned userspace outbounds, optional provider credentials and
  recoverable removal. Public Cloudflare registration/forwarding and the
  remaining host operations still require verification.
  Unimplemented operations return 501.
- Independent [native limits](native-limits.md) add persisted per-user bandwidth,
  shared concurrent-connection admission, automatic speed policies and a
  Vue/Vuetify control surface. Plan provisioning sends enforced caps before
  enabling credentials and rejects incapable Agents. External service opt-in
  and the failure/reconciliation contract remain explicit.
- Server [traffic cycles](server-traffic.md) now persist per-source cumulative
  and daily ledgers, selected-direction quotas, manual resets and UTC monthly
  reset markers. Existing telemetry is replayed once during SQLite upgrade.
  The dashboard and public probe use the configured billing total. These
  server thresholds are informational and do not replace user access controls.
- Server [editing/removal](server-management.md) now preserves identity during
  profile edits, synchronizes matching advertised node addresses and uses a
  revision-guarded impact confirmation. Removal preserves already charged
  user usage and settled change history, cleans server-owned records and
  rejects old Agent connections. It is not remote uninstall or credential
  revocation; active changes and certificate challenges must be resolved first.
- Plan [editing/unassignment/removal](plan-management.md) now retain credentials,
  tokens and charged usage. Edits preserve existing assignment dates and reset
  settings while updating node membership and native limits. Unassignment/removal keep durable
  withdrawal intents, including for offline Agents and stored preview-only
  credentials. Revision guards, transaction rollback and per-user deployment
  status protect concurrent changes. Per-plan node aliases now support a saved
  enable switch, validation, all six subscription formats, catalog remapping
  and removal cleanup without changing runtime identity. Ordered automatic
  speed rules bind to each subscriber's credentials, preserve other plans and
  inbound rules, and reject incapable Agents/runtimes. Custom
  [Global Clash templates](subscription-templates.md) now include plan/system
  defaults, draft preview, catalog remapping and guarded deletion.
- Native [node resource cleanup](node-cleanup.md) now supplies revision-guarded
  remote removal, suspended-template and limiter cleanup, local outbound
  dependency closure, shared-rule preservation and recoverable receipts.
  [Managed-node settings/removal](node-management.md) now adds guarded editing,
  parent/target relationships, explicit cross-server dependency closure,
  shared-inbound retention, persistent deletion jobs and the Vue dialog.
  Credential withdrawal precedes native cleanup and row deletion. Retired
  resource replay, conflicting imports/sync and server deletion are guarded.
  Subscriber accounts, links, dates and charged traffic remain intact.
  Private ownership, historical/unrecorded dependency discovery, external
  provider/relay-group records and Nginx/tunnel cleanup still require migration.
  This prevents a seamless in-place full-replacement claim, but does not block a
  new installation or controlled migration in the first-Preview support scope.
- User [editing/removal](user-management.md) adds profile/remark changes,
  protected administrator-role subscribers, revision guards and durable
  two-phase removal. Pending users cannot be re-enabled, reassigned or restored
  through catalog import. Runtime work drains before confirmed withdrawal and
  local cleanup; retired identity fingerprints reject structured command replay.
  Same-name recreation uses fresh traffic labels. [User limit overrides](user-limits.md)
  now cover quota, default/per-node bandwidth and connections, explicit unlimited,
  direct-parent inheritance and shared credentials. Catalog remapping, node/server
  removal and subscriber displays use these settings. [Custom user short codes](subscription-links.md)
  support operator/self-service editing, clearing and complete reset with
  collision and revision guards. Active-main direct and combined `/x` URLs can
  resolve through imported profiles and explicit package mappings. These alias
  paths are compatibility-only and default off in the hardened release baseline;
  the long high-entropy token remains the supported public bearer. Final
  regression covers the default-off behavior and legacy bearer rotation.
  Privately owned routed-node cleanup still requires migration.
  [Temporary subscription links](temporary-subscriptions.md)
  replace active-main's in-memory eight-character Clash-only shares with durable
  high-entropy records, all six formats, selected current-plan nodes, atomic
  access limits, administrator listing/copy/revocation and restart persistence.
  Link expiry does not revoke credentials already downloaded.
- [Subscriber accounts](subscriber-accounts.md) now have a separate login and
  session realm, password provisioning/recovery, own-plan usage and downloads,
  device revocation, encrypted TOTP enrollment and one-use recovery codes.
  [Registration invitations](registration-invitations.md) add administrator-created,
  plan-bound, high-entropy one-time signup links with digest-only storage,
  atomic account/plan/runtime enrollment and automatic subscriber sign-in.
  Product-user roles do not grant controller access. Disablement, removal and
  password reset revoke old sessions; reactivation cannot revive them. The
  subscriber UI is available at `/account`. [Legacy identity migration](legacy-mmwx-identities.md)
  now exports/imports active-main bcrypt hashes, TOTP seeds, recovery hashes and
  current long/generated/custom user keys with transactional preview, collision
  checks and Argon2id upgrade on login. Package assignments and dates are mapped
  explicitly to existing plans; multi-file assignments become editable subscriber
  profiles and legacy `/x` file/package combinations remain usable only when the
  operator explicitly enables migration compatibility. Source
  administrators are demoted and old sessions/API tokens are not imported. Raw
  uploads and legacy templates/rules/scripts require managed reconfiguration;
  open anonymous registration and third-party identity providers remain
  unimplemented.
- Central DNS-01 issuance/renewal, EAB, private credentials, PEM imports,
  version activation and Agent deployment are implemented and verified with
  real lego/Pebble, short-lived automatic renewal and trusted TLS on both
  transports. HTTP-01 standalone/webroot modes now use host-selected listeners
  or owned public challenge directories, with interruption cleanup and the
  same renewal/deployment flow. Account contact/EAB editing and exact-version
  revocation now include durable receipts, retry and persistent duplicate protection.
  Remote-node HTTP-01 now keeps ACME accounts and keys centrally while an
  opted-in Agent serves expiring challenges. Durable order and cleanup records
  support controller restart and node reconnect, verified with real EAB/Pebble,
  non-root Nginx, HTTPS/WSS, automatic renewal and trusted TLS.
  Public CA/provider staging remains unverified;
  validate supported DNS adapters with operator-owned staging accounts before
  claiming public-provider production coverage. See [certificates.md](certificates.md).
- Native high-level tunnel deployment now uses the independent Nginx ownership
  contract and official Xray TLS-SNI forwarding, with one conditional command,
  configurable listeners, actual static/proxy/fallback traffic, two-service
  rollback, and restart recovery verified over WebSocket and HTTP. This does
  not prove every fork-only protocol or arbitrary existing-config migration.
- Native [fork protocol migration](fork-runtime.md) covers user containers,
  Snell first-user transport settings, the MPL-2.0 empty-user and AnyTLS address
  patches, Mieru UDP targets, and the independent native limiter. Direct
  empty-user edits retain a rejecting Snell/Mieru inbound, while managed access
  suspends the final-user inbound and journals its private recovery template.
  The real-client smoke covers original configs, runtime-node import, assigned
  subscription credentials, statistics, rotation, last-user revocation,
  restart persistence and reactivation. [Client-format filtering](subscriptions.md)
  and native free-client Snell v6 export now have full-configuration tests in
  pinned Mihomo, sing-box and the patched Xray client, including real target
  traffic and URI/Base64 imports. The AnyTLS native-client UDP address bug is
  patched separately. Mieru TCP and UDP underlays now carry both TCP and UDP
  targets when a fresh scan proves the strict runtime capability; forged node
  configuration cannot bypass that gate. Wider client, transport, OS and
  architecture combinations remain open, and pinned-client results do not
  prove universal compatibility.
- External [systemd runtime mode](external-systemd.md) now verifies a dedicated
  non-root service's binary/config binding and uses scoped host-installed
  polkit authorization. Both transports have real forwarding, provisioning,
  stop-intent, independent ownership and failure recovery coverage. Opted-in
  [multifile takeover](xray-takeover.md) now adds native merge previews, exact
  backups and durable recovery, including real process kills and occupied-port
  failures on both transports. Wider OS/runtime combinations, cross-account
  migration and arbitrary process adoption remain unverified; the managed
  installer still owns a separate child.
- Multi-server change-set coordination now includes already-started dependency
  sequences, new-work reservations and reverse compensation. It coordinates
  the Open Node command queue, not external shell edits or other host managers.
  See [change-sets.md](change-sets.md) for the explicit failure/review contract.
- Single-image FastAPI/Vue packaging, non-root read-only Compose deployment,
  HTTPS reverse proxy, administrator initialization/recovery, persistent-volume
  backup/restore, and explicit image rollback are implemented. The hardened VPS
  baseline now runs through persistent Compose under systemd with a loopback-only
  listener and private volume; backup, restart, Compose down/up and isolated
  restore checks passed. Access is still through an SSH tunnel. Public HTTPS
  remains dependent on operator-supplied domain/DNS, trusted certificate and
  reverse-proxy configuration, while an encrypted off-host recovery copy needs
  an operator-selected destination, recipient key and recovery owner. Complete
  broader operator workflows and the remaining runtime gates before calling the
  product a full public replacement. Multi-host scaling and arbitrary future
  database downgrades are not covered. See [deployment.md](deployment.md).
