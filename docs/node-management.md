# Managed Node Lifecycle

Administrator node settings and removal are implemented in the FastAPI/Vue
monorepo without a license service. The Subscriptions catalog has edit, remove
and pending-removal controls. These operate on managed nodes, not subscriber
account ownership or arbitrary external provider records.

## Editing And Relationships

- `GET /api/v1/nodes/{id}/settings` returns the node, an opaque revision,
  affected nodes/plans, shared-resource retention and subscription access state.
- `PUT /api/v1/nodes/{id}/settings` accepts name, tags, enabled state, public
  config, client template, parent and target relationships. It requires the
  displayed revision and explicit runtime-restart acknowledgment.
- Server, protocol, inbound and routing resource identities are not moved by
  this editor. Create and provision a replacement node to move endpoints.
  Changing a display name does not overwrite a custom public config name.
- A routed node's `parent_id` must have the same server, authenticated inbound
  and protocol. `target_node_id` records a routed destination, including one
  on another server. Cycles and links to removing nodes are rejected.
  These fields record existing relationships; setting them does not deploy a
  tunnel or infer its host-side configuration.
- Catalog export/import preserves links by node name. Existing catalogs with
  no relationship fields remain valid. Imports cannot overwrite the runtime
  fields of a node with stored credentials; use the guarded editor instead.

Disabling a node withdraws its stored credentials, including previously exported
preview credentials, while retaining their identity for reactivation. Template
edits update existing managed bindings but do not automatically enroll unrelated
preview-only subscribers. Matching physical aliases reuse an existing credential;
different routed destinations receive separate identities when labels collide.

## Removal

`POST /api/v1/nodes/{id}/remove` requires `expected_revision`, the exact
`confirm_name`, and `acknowledge_runtime_restart: true`. Missing inbound or
outbound ownership produces warnings that require
`acknowledge_unmanaged_resources: true`; those external resources remain the
operator's responsibility. Removal cannot be cancelled.

The deletion closure includes explicit descendants, routed destinations that
depend on a selected target, and legacy routed nodes sharing the last removed
physical inbound. A physical alias that remains in the catalog protects its
shared inbound; a remaining outbound alias protects that outbound. Native
outbound dependency expansion cannot consume another retained catalog node.

The controller persists a job before changing runtime state:

1. Mark selected nodes as removing/disabled. Remove their plan membership and
   per-node multiplier, speed and device overrides. Retain accounts, plan dates,
   subscription links and charged traffic.
2. Track credential withdrawal, drain previously started runtime mutations and
   stop queued commands that would restore retired identities or resources.
3. After confirmed withdrawal, obtain a fresh native resource preview and apply
   its exact revision with a durable operation UUID. The native operation removes
   selected listeners, suspended templates, limit policies and dependent Xray
   outbound/routing resources.
4. Reconcile affected access again, then remove selected node/credential rows
   and prune their bindings. Keep the completed job and its revocation evidence.

Offline or unsupported Agents leave the job pending or failed. A request
accepted by the controller is not proof that a client stopped forwarding.
Only a confirmed job is complete. Existing command history and backups are
retained; this is not host-wide secret erasure.

## Recovery And Coordination

- `GET /api/v1/node-removals/{id}` reads progress.
- `POST /api/v1/node-removals/{id}/retry` retries failed withdrawal or cleanup.
- A failed apply is inspected using its original operation UUID. A prepared
  operation retries the identical body; a confirmed absent operation permits
  a new preview. A completed receipt is checked against the original revision
  and impact before it can finish the job.
- Jobs, node markers and cleanup identities survive controller/Agent restart.
  One removal per affected server reserves runtime mutation work. Read-only
  operations and managed subscription access continue to run.
- Pending jobs block node creation/edit/import/sync on affected servers,
  server removal and new change-set reservations. Queued unrelated mutations
  resume after cleanup; retired credential and resource replays are rejected.
- If a preview needs corrective work, unrelated administrator runtime writes
  are available before retrying the preview. A prepared native job with host
  drift requires restoring its recorded old/intended host state first.
- Retired resource tags and credential labels cannot be reused by catalog
  import. Use new resource tags for replacement nodes.

The native [cleanup contract](node-cleanup.md) owns crash recovery. Independent
host administrators can still replace files or restore backups outside this
controller's coordination.

## Verification And Scope

`backend/tests/test_node_management.py` covers guarded editing, shared
credentials, relationship closure/cycles, imported links, schema migration,
controller restart, pending guards, native confirmation, retry, retired replay
and preserved user traffic/links. Frontend service tests cover payloads and
validation; the VPS-only `scripts/vps/smoke-node-management.py` exercises the
actual dialog at desktop, mobile and narrow widths with real Xray clients,
shared aliases, routed descendants, a paused/killed Agent and unrelated traffic.
Run it with both `--transport websocket` and `--transport http`.

Private subscriber ownership, historical mmwx relationship discovery,
provider/relay-group models, and Nginx/tunnel resource cleanup are separate
migration work. The explicit relationship model does not claim to discover
unrecorded cross-server dependencies or provide those remaining workflows.
