# External Subscriptions

Administrators can save a subscriber's external HTTPS subscription, preview its
nodes and explicitly confirm changes. Confirmed nodes are merged into that
subscriber's existing **primary subscription link**, using the upstream
credentials. They are not added to the managed-node catalog or provisioned by
an Agent.

This first slice accepts Clash/Mihomo YAML with a `proxies` list. It does not
accept URI lists, Base64 or Surge input, run automatic refreshes, fetch during
downloads, or provide subscriber self-service. Rules, scripts, proxy providers
and whole-client settings are not imported. The source review and remaining
scope are in [external-subscriptions-plan.md](external-subscriptions-plan.md).

## Administrator workflow

The interface is in Simplified Chinese. Open **订阅管理 → 管理外部订阅**. The panel is collapsed
by default; opening it only reads saved state.

1. Add a source for an existing subscriber. Supply a name and private HTTPS
   subscription URL. The default user agent is `clash-meta/2.4.0`; a custom
   printable-ASCII value of up to 256 characters is optional. Saving does not
   contact the provider.
2. Open **预览 / 恢复回执**, then click **抓取预览**. Inspect
   newly discovered, updated, unchanged, missing and unavailable nodes. The
   preview shows changed field names, never credential values.
3. Select the new nodes to import and acknowledge the displayed changes.
   Confirmation applies updates to existing nodes and missing-node states as
   well as the selected new nodes in one transaction. Fetching a preview alone
   does not change the active subscription.
4. Download the subscriber's existing primary link. Compatible saved external
   nodes appear alongside eligible managed nodes. Downloads use the confirmed
   snapshot; they do not contact the source URL.

The source owner cannot be changed. Source URLs and custom user agents are
write-only: editing does not reveal the previous values. An empty URL field in
the editor preserves the saved URL; resetting the user agent is an explicit
choice. Changing a URL does not replace the active nodes until a new preview is
confirmed.

Source details allow renaming or disabling individual saved nodes. Operator
names and enabled states survive later refreshes. Matching uses the normalized
upstream name within the immutable source ID, never just an address or a name
from another source. An upstream rename therefore appears as one missing node
and one new node; it is not automatically treated as the same identity.

A missing node stops appearing in exports only after confirmation. Its record
is retained so a later confirmed refresh can restore it. An unsupported update
also becomes unavailable after confirmation. Failed fetches or invalid input
leave the last confirmed snapshot unchanged. An explicit `proxies: []` is a
valid empty snapshot and previews every saved node as missing; an empty HTTP
body, HTML page or document without `proxies` is invalid.

## Confirmation, conflicts and limits

Previews expire after 15 minutes, with at most three pending previews per
source. Cancel an unwanted preview before preparing another. A source or node
edit changes the source revision; a stale preview cannot overwrite that edit.
Deleting a source or subscriber invalidates its previews, including fetches
that finish after deletion. Confirmation is rejected while subscriber removal
is in progress.

If a confirmation response is lost, use **查询确认结果** before changing
the selection. The same preview and payload return the original receipt,
without importing twice or fetching again. A different payload is rejected.
Receipts remain readable for seven days after application; after that they
return 404 and cannot be reapplied. Expired database records are physically
purged when the next preview is prepared for that source, not by a scheduler.
Closing a dialog cannot cancel a request that the server has already received.

Current limits are 100 sources per subscriber, 1,000 input nodes per refresh,
and 2,000 saved nodes per source, including missing-node records. There is no
individual permanent-node deletion flow in this slice. A source deletion
removes its saved nodes and previews; disable the source when preservation is
needed instead.

## Export and accounting boundaries

The existing subscriber activity, active-plan, expiry, quota, removal and
subscription-IP checks still apply. A confirmed external source does not grant
access to an otherwise ineligible subscriber. Named subscription profiles and
temporary links do not automatically include external nodes.

The six existing output formats apply their normal compatibility checks. A
protocol being accepted as input does not mean every output format can express
its settings. Incompatible nodes remain visible in the administrator's format
preview and are excluded from that output. Public export still returns 404
when no compatible selected node remains. `node_id` can select an eligible
external-node ID in the primary subscription, but never one belonging to
another subscriber.

Managed credentials are generated before the external snapshot is merged.
An external name collision cannot retarget a managed node's dialer reference.
No fake Server, managed credential, Agent command or local traffic-ledger entry
is created for an external node. Provider `upload`, `download`, `total` and
`expire` values are display metadata only; they do not change local billing or
the primary subscription's local usage header. The UI omits values outside
JavaScript's safe integer range rather than rounding them.

Disabling or deleting a source stops future local exports. It cannot revoke an
upstream credential already downloaded by a client. Open Node's managed-node
speed and connection enforcement do not control a third-party server.

## Input semantics

The parser independently validates a conservative subset of VLESS, VMess,
Trojan, Shadowsocks, Hysteria2, AnyTLS, Snell, Mieru, HTTP and SOCKS5. Unknown
protocols or unsupported options are shown as unavailable rather than silently
discarded. Malformed required fields, duplicate names or ambiguous YAML reject
the entire preview. Credentials are not trimmed or regenerated.

Input defaults follow the pinned **Mihomo v1.19.30** parser, not defaults chosen
by another output converter:

| Input | Omitted `udp` | Additional boundary |
| --- | --- | --- |
| VLESS, VMess, Shadowsocks, Trojan, AnyTLS, SOCKS5, Mieru | Explicit `false` | Only independently supported fields are accepted. |
| Snell v4/v5 | Explicit `false` | `version` must be explicit; the source's omitted version means v1, not v4. |
| HTTP | `false` | `udp: true` is unavailable. Username-only or empty-password authentication is unavailable. |
| Hysteria2 | `true` | `udp: false` is unavailable because the pinned source enables UDP unconditionally. |
| Snell v6 | Unavailable | The pinned Mihomo does not establish a v6 default. Explicit UDP values still pass through per-output compatibility checks; this does not add Mihomo v6 support. |

These defaults follow the official [adapter parser](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/parser.go),
[outbound base](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/outbound/base.go),
[Hysteria2 constructor](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/outbound/hysteria2.go)
and [Snell version constants](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/transport/snell/snell.go).

TLS names and options are protocol-specific. For example, HTTP accepts
`tls`, `sni` and `skip-cert-verify`, whereas SOCKS5 accepts only `tls` and
`skip-cert-verify`; an ignored source field is not assigned a new meaning by
another exporter. VLESS/VMess use `servername`, while Trojan/AnyTLS/Hysteria2
use `sni`. Native-TLS protocols cannot disable TLS. These checks follow the
official [HTTP options](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/outbound/http.go)
and [SOCKS5 options](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/outbound/socks5.go).
Mieru requires explicit `transport: TCP` or `UDP`, following its
[constructor](https://github.com/MetaCubeX/mihomo/blob/v1.19.30/adapter/outbound/mieru.go).

The separate runtime-scan requirement for **managed** Mieru nodes is unchanged.
External nodes use the validated upstream configuration; there is no local
Agent whose scan could attest to a third-party server.

## Fetching and secret storage

Refresh requires HTTPS with certificate and hostname verification. Redirects,
URL user-info and fragments are rejected. All DNS answers must be public;
private, reserved, link-local, metadata and mixed public/private results are
rejected. The connection uses the checked numeric address while retaining the
original TLS hostname. Environment proxy settings are ignored.

Each fetch has a 30-second total deadline and 2 MiB compressed and decompressed
body limits. A bounded child process contains DNS, connect, TLS and read work;
at most four fetch workers run per backend process. The fetcher accepts a
successful HTTP 200 response with identity or single-member gzip encoding,
including ordinary chunked transfer. Ambiguous framing, chunk extensions,
nonempty trailers and concatenated gzip are rejected. Provider metadata is
restricted to the four known nonnegative integer fields; malformed values are
not used.
These intentionally strict rules may reject a provider endpoint that needs a
redirect, private-network access or other unsupported HTTP behavior.

YAML is data only. Parsing limits nesting, node count and scalar size, rejects
aliases, anchors, merge keys and duplicate keys, and does not follow external
references. Ordinary API responses and failure messages do not contain source
URLs, user agents, credentials or raw provider bodies. New URL/user-agent input
exists temporarily in the form's memory and is cleared on submission or close;
it is never read back from the server or persisted to browser storage. Mutation
requests require the existing administrator session, Origin and CSRF checks,
and are limited to 64 KiB of JSON.

URLs, user agents, saved proxy configurations and pending preview configurations
are encrypted at rest. For a file-backed SQLite database, the default key
directory is `external-subscriptions` beside that database. The standard
Docker/Compose data volume already covers this location. No new environment
setting is needed for that layout.

To override the location, set `OPEN_NODE_EXTERNAL_SUBSCRIPTIONS_STATE_DIR` to
an absolute private persistent directory. In-memory and non-SQLite databases
require an explicit directory before external sources can be used. Keep it
separate from certificate and TOTP key storage. The directory is mode `0700`
and `vault.key` is `0600`; the database does not contain this encryption key.

Back up the database **and the complete external-subscriptions key directory**
together while the application is stopped, using the existing private-volume
backup procedure. Copying only the database cannot restore external nodes.
When existing ciphertext has a missing, replaced or invalid key, the service
fails closed without generating a replacement. Restore the original directory;
do not delete the database or create a new key as a recovery step.

## Verification

[testing.md](testing.md#external-subscriptions) records the exact tested source
and release gates. The end-to-end fixture exercises real HTTPS input, the
production Ant Design bundle, managed and external VLESS forwarding through
Mihomo and official Xray, credential rotation, failure preservation, original
sessions and cold database/key restoration. Parser coverage of other protocol
families is not a claim that this new fixture forwards all of them. The existing
[managed protocol gates](subscriptions.md#verified-protocols) remain separate.

The verified deployment scope remains single-host Docker/SQLite. This feature
does not establish PostgreSQL concurrency, automatic backups, public DNS/TLS
or a full MMWX migration.
