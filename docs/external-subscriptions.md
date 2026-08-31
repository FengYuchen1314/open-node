# External Subscriptions

Administrators can save a subscriber's external HTTPS subscription; subscribers
can manage their own sources from the user center. The default workflow uses a
node preview and explicit confirmation; an opt-in schedule can apply future
refreshes automatically. Saved nodes are merged into that
subscriber's existing **primary subscription link**, using the upstream
credentials. They are not added to the managed-node catalog or provisioned by
an Agent.

The HTTPS response may contain Clash/Mihomo YAML with a `proxies` list, a URI
list, or one Base64-encoded layer containing either format. The form still
accepts a private HTTPS source URL, not pasted node credentials. Surge input
and fetching during downloads are not supported. Rules,
scripts, proxy providers and whole-client settings are not imported. The
original source review is in
[external-subscriptions-plan.md](external-subscriptions-plan.md).

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
choice. Changing a URL disables its schedule and does not immediately replace
active nodes. Confirm a new preview or explicitly enable the schedule again.

## 定时刷新

管理员和用户都可在来源详情打开“配置定时刷新”。默认关闭；开启时须确认允许自动变更。
间隔可设为 15 分钟至 7 天，保存后经过一个间隔首次执行，保存操作本身不会抓取上游。

- **只更新已保存节点**：更新凭据和上游信息，将缺失或不再支持的节点标为不可用。
  新节点不自动导入，详情显示待手动导入数量，可通过原有预览选择。
- **更新已保存节点，并自动加入新节点**：额外导入此次发现的可用节点，并加入用户主订阅。
  首次抓取也可导入，无须先手动确认。无法表示的协议和配置不自动导入。

两种方式都保留本地节点改名和停用状态，仍按同一来源中的上游名称匹配。上游改名会被
识别为旧节点缺失和新节点出现。有效的空 `proxies` 列表会把所有已保存节点标为缺失；
空正文、HTML、格式错误等则失败，保留上次节点和上游信息。

详情显示计划状态、下次执行、上次开始/结束、最近成功时间及新增/更新/缺失/待导入数量。
连续失败会把间隔按 1、2、4……倍延长，最多 64 倍且不超过 7 天；成功后恢复所设间隔。
密钥缺失、节点数量超限、抓取失败和解析失败各有固定提示，不显示上游链接或异常正文。
当前保留最近一次执行状态，不提供逐次历史日志。

停用来源或账户会暂停计划，重新启用后到期的计划可恢复执行；更换来源 URL 则关闭计划，
须重新确认。关闭计划或修改来源不会强行终止已经发出的网络请求，但旧结果不再应用。
自动刷新成功会更新来源版本，因此之前打开的手动预览可能过期，需重新读取后预览。
本地配额/套餐判断仍由订阅出口执行；自动刷新不会增加配额或改变本地账单。

调度使用数据库中的独占租约和来源版本校验，单进程逐个执行。重启保留计划和下次时间，
不补跑每一个错过的周期。中断租约到期后记录失败并退避，再抓取新内容，不回放旧正文。
同一实例内有较多慢来源时，实际开始时间可能晚于计划时间。首次恢复备份仍暂停所有
后台任务，且恢复时将这些计划关闭；完成复核并重启后也需重新开启。

管理 API 为 `PUT /api/v1/external-subscriptions/{id}/refresh-schedule`；用户 API 为
`PUT /api/v1/account/external-subscriptions/{id}/refresh-schedule`。请求包含
`expected_revision`、`enabled`、`interval_minutes`、`scope`（`saved_only` 或 `all`）和
`accept_changes`。启用时最后一项必须为 `true`。权限、CSRF、Origin 和所有权边界
与现有来源编辑相同。普通来源响应中的 `refresh` 字段提供安全状态，不含凭据。

参考固定官方 `external_sync.go` 的 `saved_only` / `all` 语义，抓取触发改为持久化定时器；
不会复刻官方在订阅下载请求中同步抓取的方式。下载仍只读取最近一次成功保存的快照。

## Subscriber workflow and ownership

Open **用户中心 → 外部订阅**, or `/account/external-subscriptions`. The same
save, preview, confirmation, rename, disable and delete controls apply, but
only to the signed-in subscriber's sources. The owner is taken from the
subscriber session; the API rejects an owner field in a create or update
request. It is not a client-selectable username.

Source, node, preview and receipt operations check ownership in the database,
including a second check after an upstream fetch finishes. Missing and foreign
resources return 404. Source URLs and credentials remain write-only, and a
session change discards the old page's pending replies. Subscriber cookies
cannot access administrator source routes, and administrator cookies are not
accepted as subscriber sessions. Mutation requests require the corresponding
session, Origin and CSRF validation.

Source details allow renaming or disabling individual saved nodes. Operator
names and enabled states survive later refreshes. Matching uses the normalized
upstream name within the immutable source ID, never just an address or a name
from another source. An upstream rename therefore appears as one missing node
and one new node; it is not automatically treated as the same identity.

A missing node stops appearing in exports after confirmation or a successful
scheduled refresh. Its record is retained so a later refresh can restore it.
An unsupported update also becomes unavailable when applied. Failed fetches or invalid input
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
purged when the next preview is prepared or a scheduled refresh succeeds.
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

The twelve output formats—Clash/Mihomo, Surge, sing-box, Xray, URI list, Base64,
Loon, Quantumult X, Shadowrocket, Stash, Surfboard and Egern—apply their own
compatibility checks. A
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

YAML input defaults follow the pinned **Mihomo v1.19.30** parser, not defaults
chosen by another output converter:

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

URI preprocessing follows the fixed MMWX source's **proxyparser v0.1.7**
dependency. Supported families are VLESS, VMess, Trojan, Shadowsocks,
Hysteria2, AnyTLS, Snell, Mieru, SOCKS5 and HTTP/HTTPS; support remains limited
to settings the existing node model can preserve. Shadowsocks accepts SIP002
and legacy whole-URI Base64 credentials, while VMess uses its Base64 JSON
form. Percent-encoded credentials are decoded exactly once. Duplicate URI
parameters, ambiguous JSON fields, malformed encoding and duplicate node
names reject the preview. Unknown protocols and unrepresentable options remain
visible as unavailable rather than being silently removed.

URI defaults are not substituted for YAML defaults. For example, the pinned
URI parser defaults Snell to version 4 and Mieru to TCP. URI conversion also
preserves the pinned parser's TLS-name fallback and protocol-specific UDP
defaults, subject to the same strict node validation. Base64 accepts canonical
standard or URL-safe encoding, including CRLF-wrapped subscription bodies;
mixed alphabets, noncanonical padding and nested outer encoding are rejected.
The 2 MiB input and 1,000-node limits still apply, with a 16 KiB limit per URI
or decoded VMess JSON object.

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
requests require the appropriate administrator or subscriber session, Origin
and CSRF checks, and are limited to 64 KiB of JSON.

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

The URI/Base64 and subscriber-source extension has 14 focused backend tests
and eight frontend service/component tests. These cover bounded parsing,
owner isolation, real session and CSRF guards, concurrent source changes,
explicit confirmation and primary-link merging, plus secret clearing and late
responses in the subscriber page. This focused extension is separate from the
earlier browser/forwarding gate; it does not claim a new browser or forwarding
run for every input and output combination.
