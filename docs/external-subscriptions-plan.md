# External subscriptions: first-slice design record

The first administrator/manual-YAML slice is now implemented. See
[external-subscriptions.md](external-subscriptions.md) for the actual workflow,
limits and key recovery, and [testing.md](testing.md#external-subscriptions)
for release evidence. The original source review and implementation requirements
below are retained as the design record; their future tense does not describe
the current implementation status. The wider subscription ecosystem remains
unfinished.

Update on 2026-09-01: URI/Base64 and subscriber self-service are already present.
The current slice adds opt-in persistent scheduling with the pinned reference's
`saved_only` / `all` selection semantics. Scheduling uses the same encrypted
snapshot merge as manual confirmation, a 120-second durable source lease,
source-revision fencing, exponential failure backoff, and restore-time disable.
Unlike the reference's download-time force-sync, subscription downloads remain
network-free. Current behavior is documented in the operator guide; the original
first-slice requirements below are historical, not a fresh task list.

## Reference and intended workflow

The control-plane reference is `tajiaoyezi/miaomiaowuX` at
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`. The other three pinned repositories
do not own external-subscription fetching or management.

Relevant source entry points:

- [Route registration](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/cmd/server/main.go):
  `/api/user/external-subscriptions`, node/filter previews, user/admin sync and
  `/api/user/sync-external-subscriptions/confirm`.
- [Source CRUD](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/external_subscriptions.go):
  owner, name, URL, user agent and upstream traffic/expiry metadata.
- [Refresh and selection](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/external_sync.go):
  update existing nodes and confirm newly discovered candidates.
- [Connection-time SSRF checks](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/ssrf_safe_fetch.go):
  validate resolved addresses and dial the selected IP without resolving again.

The first Open Node slice should let an administrator maintain a source for one
subscriber, explicitly fetch a preview, select/confirm nodes and expose those
saved nodes through that subscriber's existing primary subscription link.
Support Clash/Mihomo YAML `proxies` as input first. Existing output converters
may export compatible nodes to their supported formats; unavailable protocols
must remain visible as unavailable rather than being silently mistranslated.

Do not claim URI/Base64/Surge input parity from this first slice. The official
preprocessor delegates some parsing to `MMWOrg/mmwX-plugins/proxyparser v0.1.7`,
outside the four reference repositories. Its behavior and licensing require
separate review before reuse. Rules, scripts, provider groups, automatic refresh,
download-triggered fetching and subscriber self-service are later slices.

## Keep external nodes separate from managed nodes

`ManagedNodeCreate.server_id` is required, and the existing
`InventoryStore._subscription_proxy_configs()` injects locally issued user
credentials. Importing an upstream proxy through managed catalog/import would
misrepresent ownership and overwrite its credential.

Use independent owner-scoped `ExternalSource`, `ExternalNode` and expiring
preview records. Never create a fake Server or queue Agent reconciliation for
an external node. Merge explicitly authorized saved external configurations
after managed credential generation, before the existing
`_prepare_subscription_format()` naming and compatibility checks.

Preserve current subscriber activity/plan/expiry/access checks. The initial
scope is the primary subscription only; named profiles and temporary links
must not automatically gain external nodes. Upstream traffic information is
display metadata, not a mutation of Open Node's traffic ledger or billing.
Stopping local export cannot revoke credentials already delivered upstream.

## Confirmed integration points

The follow-up read-only review checked the same pinned control-plane commit;
it did not implement these changes.

- Add administrator CRUD/preview/confirm through `api/router.py`'s existing
  private router. The administrator identity is not a ProductUser owner: require
  an explicit existing subscriber and keep source ownership immutable.
- Add an explicit primary-subscription opt-in to the shared rendering path.
  `subscription_profiles.py` and `temporary_subscriptions.py` also call that
  path; a named profile with no node subset can pass `None`, so `node_ids is
  None` cannot distinguish a primary subscription. Default shared rendering to
  excluding external nodes.
- Validate upstream YAML separately from `subscription_clients.py`'s output
  compatibility helpers. Those helpers assume trusted local configurations,
  can preserve arbitrary fields, and normalize `hysteria` to `hysteria2`.
  Untrusted input must distinguish protocol versions and reject cross-source
  references such as `dialer-proxy` before conversion.
- Use immutable source IDs and owner/source foreign keys for every node,
  preview and confirmation receipt. Deleting a source or subscriber must
  invalidate all of them. Reject confirmation while owner deletion is pending;
  a late fetch or replay must not transfer credentials to a same-name recreated
  subscriber. Do not upsert a deleted source from an old network response.
- Use a source-specific revision/CAS transaction. The existing
  `_coordinated_session()` coordinates Server operations, including Server locks
  in its PostgreSQL branch; it does not serialize unrelated external-source
  mutations. Preserve the documented SQLite deployment boundary until any
  additional database-specific behavior has its own verification.

Before implementation, settle the encryption and confirmation contracts:

- Source URLs, saved upstream credentials and preview configurations need
  encryption at rest with a defined key/backup/restore boundary. Keep this key
  separate from subscriber TOTP secrets. Missing keys for existing ciphertext
  must fail closed without generating a replacement or overwriting the data;
  `certificate_vault.py` provides an existing missing-key safety pattern.
- Preview is read-only for the usable node snapshot. Its diff must describe
  updates to existing credentials and missing-node availability as well as new
  nodes. Confirm applies the explicitly accepted diff atomically and stores a
  repeatable receipt. The official refresh updates existing nodes before its
  confirmation step; that behavior must not leak into a read-only preview here.

## Fetching and confirmation requirements

- Use one hardened fetch path for every refresh. Require HTTPS, reject
  redirects, ignore inherited proxy settings and validate **all** resolved IPs
  before connecting directly to an approved public address. Preserve hostname
  certificate validation/SNI. Reject private, link-local, metadata, multicast,
  reserved and mixed public/private results, including IPv6 forms.
- Bound DNS/connect/TLS/read work and total elapsed time. Proposed first-slice
  limits: 30 seconds, 2 MiB actual response body and 1,000 nodes; enforce limits
  for chunked/compressed input too. Confirm implementation feasibility before
  treating those proposed numbers as an API contract.
- Treat the source URL, its query and every node credential as secrets. Never
  log raw request/response bodies, URLs, parser snippets or provider exceptions.
  Return only safe metadata and differences in ordinary lists/previews. Do not
  reuse bootstrap's query-forbidding URL normalizer: subscription URLs commonly
  carry a required private query.
- Parse data only. Bound YAML depth/aliases/field sizes; reject ambiguous or
  unsupported node configuration. Do not import whole-client DNS/rules/scripts,
  executable content or uncontrolled cross-source proxy references.
- Complete fetch/parse before opening the short write transaction. Bind the
  preview to owner, immutable source ID, source revision, content identity and
  expiry. Apply explicitly selected new nodes, protect against stale revisions
  and make repeat confirmation deterministic.
- Match only within the same source and owner. A failed refresh must preserve
  the last usable snapshot. Missing nodes should become unavailable without
  deleting unrelated data; ambiguous matches must require review.

The pinned source does not apply its safe HTTP client consistently to refresh;
some refresh paths use an ordinary client, an unbounded `io.ReadAll` and full
URL logging. Its name/address matching can also cross source boundaries for
the same user. These are concrete reasons to reuse the workflow, not copy the
fetching and merge implementation unchanged.

## Acceptance before publication

Run only on the isolated VPS. Cover source create/preview/select/apply, correct
upstream credentials in the original subscriber link and real Mihomo loading.
Refresh tests must include credential changes, new/missing nodes, empty/HTML/
malformed input, timeout/oversize, persistence after restart and atomic failure.

Security tests must cover DNS rebinding, mixed results, private IPv4/IPv6,
redirects and decompression limits; authentication/CSRF/Origin; cross-user and
cross-source collisions; stale/replayed previews and concurrent source changes.
Assert that managed nodes, Agent commands, local credential records and billing
are unchanged, and that logs/UI/storage do not disclose source secrets. Browser
opening and subscription downloads must not silently trigger an external fetch.
