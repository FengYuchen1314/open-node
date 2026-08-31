# Subscription Clients

Open Node issues per-user subscription links without activation, payment or
license-server calls. Treat the URL as a credential. Resetting a link revokes
its previous token; plan expiry, disabled users and exhausted quotas also
prevent export. Client compatibility does not prove that a remote node is
currently reachable or provisioned.

[External sources](external-subscriptions.md) can add explicitly confirmed
upstream nodes to the primary link without replacing their credentials. These
nodes are not Agent-managed, are not fetched during downloads, and do not enter
named profiles or temporary links automatically. Local plan and quota checks
still apply; provider traffic is display metadata, not local billing.

[Managed access](subscription-access.md) also revokes enrolled runtime
credentials and restores them after renewal or traffic reset. Node application
requires a capable Agent and restarts Xray. Metadata-only previews never enroll
new credentials; use the per-server access status to verify actual enforcement.

Queued provisioning enforces plan bandwidth and concurrent-connection caps
through the [native limiter](native-limits.md), including per-node overrides.
The compatibility `device_limit` field counts connections, not physical
devices. Limited batches require a capable Agent and compatible free runtime;
unsupported Agents do not receive credentials without the requested limits.

## Formats And Selection

The public endpoint is `/api/v1/subscribe/{token}?format=...`:

| Format | Verified Client |
| --- | --- |
| `clash` (fallback for unrecognized clients) | Mihomo v1.19.30 |
| `surge` | Server-side profile validation; Apple client gate remains |
| `sing-box` | sing-box v1.13.19 |
| `xray` | Pinned, patched [compatibility runtime](fork-runtime.md) |
| `uri-list`, `base64` | Mihomo v1.19.30 file-provider URI importer |
| `loon`, `quantumult-x`, `shadowrocket`, `stash`, `surfboard`, `egern` | Pinned-schema and real subscription API tests; native application import is not claimed |

An explicit format overrides User-Agent selection. Omit `format` or use
`format=auto` to select by the requesting client. See the
[client usage guide](subscription-clients.md) for mappings, the six additional
export schemas, unsupported options and Stash template limits. Shadowrocket
exports node YAML; select `base64` explicitly when that representation is needed.

Clash and sing-box export complete configurations with a loopback mixed
listener on port 7890 and a `Proxy` selector. Xray exports a loopback SOCKS
listener on port 1080 and native outbounds. Its **first outbound is the default**;
listing more outbounds does not create an interactive selector. Use the Xray
node control in the Subscriptions view, or add `&node_id={managed-node-uuid}`,
to export a specific managed node. The same parameter can select an available,
confirmed external node owned by the token's subscriber. Selection still
requires the subscriber's active plan and cannot cross subscriber boundaries.
Local listener ports can be changed in the downloaded client configuration.

Administrators can also create [temporary subscription links](temporary-subscriptions.md)
at `/t/{code}` for selected nodes from one subscriber's current plan. These
links use the same renderer and supported formats, but have a 1-100 successful
download limit and expire after 1-60 minutes. They recheck the source user,
plan, quota, templates and credentials on every request and omit the
`subscription-userinfo` header. Expiry, exhaustion or deletion blocks future
downloads; it does not invalidate node credentials already downloaded.

Unsupported nodes are excluded without breaking compatible entries. The
authenticated `GET /api/v1/users/{username}/subscription-preview?format=...`
returns node IDs, names, protocols, availability, reasons and catalog warnings,
but no passwords or private configuration. The Subscriptions view shows this
report alongside the selected URL. The public response includes
`X-Open-Node-Included-Nodes`, `X-Open-Node-Excluded-Nodes` and
`Cache-Control: no-store`. The excluded count covers format incompatibility and
currently unavailable external nodes, including disabled sources/nodes and
confirmed missing nodes. It does not count nodes omitted by explicit selection.

When nothing compatible remains, export returns 404, not an empty configuration
or a direct-connection fallback. Unknown, out-of-plan or incompatible selected
nodes also return 404; malformed UUIDs and unknown formats return 422.
Reserved and duplicate proxy names receive stable unique suffixes.
Clash and Surge can use [custom templates](subscription-templates.md) selected
per subscriber, plan or system. These files affect only rendered subscriptions.

Managed Mieru UDP target advertisement is derived from runtime evidence, not trusted
catalog JSON. The latest Agent scan must report Xray running, be no more than
ten minutes old, and contain the actual integer `mieru_udp_target: 1`. The
backend overwrites the Mieru proxy's `udp` field from that evidence even when a
node config claims support. A missing, stale or invalid report keeps
`udp: false` and adds a catalog warning, so the node remains usable for TCP
targets without advertising an unsupported UDP path. Runtime-node drafts and
imports use the same gate. External Mieru nodes instead use the independently
validated upstream configuration; a local Agent scan cannot establish the
capabilities of a third-party server.

## Verified Protocols

The VPS full-export fixture provisions 18 inbound variants through the installed
non-root Agent, imports nodes, assigns a plan and consumes the resulting links:

| Protocol | Clash | Surge profile | sing-box | Xray | URI / Base64 |
| --- | --- | --- | --- | --- | --- |
| VLESS TCP/TLS (including Vision), WebSocket, gRPC | Yes | Excluded | Yes | Yes | Yes |
| VLESS HTTPUpgrade | Yes | Excluded | Yes | Yes | Excluded |
| VMess TCP, Trojan TLS | Yes | Yes | Yes | Yes | Yes |
| Shadowsocks AES-GCM, Shadowsocks 2022 AES-GCM | Yes | Yes | Yes | Yes | Yes |
| Hysteria2 TLS | Yes | Yes | Yes | Yes | Yes |
| AnyTLS TLS | Yes | Yes | Yes | Yes | Excluded |
| Snell v4 plain/HTTP | Yes | Yes | Excluded | Yes | Excluded |
| Snell v5 TLS obfuscation | Yes | Excluded | Excluded | Yes | Excluded |
| Snell v6 default/unshaped | Excluded | Yes | Excluded | Yes | Excluded |
| Mieru TCP/UDP underlay | TCP and UDP targets | Excluded | Excluded | Excluded | Excluded |

All affirmative entries are exercised with TCP and UDP target traffic. The
Xray tests use the full export and each selected-node export;
Mihomo and sing-box use the complete configuration and switch its selector.
URI and Base64 payloads are passed unchanged to Mihomo's own parser.
Surge entries are produced by the public endpoint and checked against the exact
fixture node set and an independent profile parser. They are not claimed as
real-client traffic tests because the proprietary app cannot run on the VPS.

These are version-specific boundaries. In particular, sing-box v1.13.19 has no
registered Snell outbound; newer documentation is not evidence for that binary.
Mihomo v1.19.30's URI parser retains `network: httpupgrade` without enabling
its WebSocket upgrade option, so these URIs are deliberately excluded for
the pinned import target. All three native formats retain working HTTPUpgrade.
The native Xray AnyTLS client requires the included UDP address-family patch.

TLS SNI, ALPN, certificate-verification flags, supported uTLS/REALITY parameters,
WebSocket path/headers/early data and gRPC service names are converted explicitly.
HTTP/2 and HTTP camouflage remain distinct. Additional conversion boundaries
are unit-tested, not all real-network-tested: unsupported wrappers, custom
certificate settings, Shadowsocks plugins and Hysteria2 options must use a
format that preserves them. Native sing-box TLS dictionaries are retained;
unmapped TLS options are rejected by other exporters rather than discarded.
Do not interpret this table as support for arbitrary extension fields or all
configurations of a listed protocol.

Imported Shadowsocks 2022 nodes contain a non-secret `server-key-source: runtime`
reference. Export obtains the shared server key from the matching private scan
record and combines it with the assigned user's key. The shared key is not
copied into node drafts, compatibility reports or credential-free catalog
exports. A missing/ambiguous scan or changed cipher prevents that node's export;
restoring a catalog therefore requires a fresh scan before these links work.

Broader protocol/transport combinations and other OS/architecture coverage
remain separate [migration gates](migration-map.md). Reproduction commands are
in [testing.md](testing.md#subscription-client-smoke).
