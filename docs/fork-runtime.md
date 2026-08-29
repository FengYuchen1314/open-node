# Fork Protocol Runtime

The independent MIT Agent can manage AnyTLS, Snell and Mieru users in an
operator-supplied Xray compatibility runtime. It does not embed the old Agent,
contact an activation service, or require a paid license. The runtime is a
separate MPL-2.0 component, not code relicensed under the Agent's MIT license.

## Source And Build

The build helper pins
[`FengYuchen1314/Xray-core-mmwx`](https://github.com/FengYuchen1314/Xray-core-mmwx)
to commit `d3fdae5833a92070414db588ee9893264147b789`. The runtime applies
[`empty-users.patch`](../runtime/xray/empty-users.patch): empty Snell and Mieru
inbounds can start, but reject every connection before protocol processing.
Without this change, removing the last user fails runtime validation/startup
and the Agent correctly retains the previous, still-authorized configuration.
AnyTLS already supports empty user lists. A second
[`anytls-udp-address.patch`](../runtime/xray/anytls-udp-address.patch) preserves
IP/domain address families in native AnyTLS UDP requests. The original client
puts IP strings into a domain-only field, which its serializer rejects.
The third [`mieru-udp-target.patch`](../runtime/xray/mieru-udp-target.patch)
implements Mieru UDP target forwarding independently from the public protocol
description and RFC 1928. Each frame is `0x00`, a two-byte big-endian length,
one SOCKS5 UDP datagram, and `0xff`. The bounded incremental reader accepts
split and coalesced frames, limits the SOCKS5 datagram to 8192 bytes, supports
IPv4, IPv6 and domain targets, and rejects fragmented datagrams (`FRAG != 0`).
Each authenticated association creates one dispatcher UDP link lazily, carries
the authenticated user context into traffic accounting and limits, and returns
each response with its actual source address. Each underlay tracks at most 1024
sessions. An association that never receives its first valid frame expires at
the authenticated handshake deadline capped at one minute, and shutdown removes
its session and dispatcher resources under concurrent close paths.
The fourth [limiter patch](../runtime/xray/limiter.patch) installs the independent
[native limiter overlay](../runtime/xray/overlay), providing per-user bandwidth,
concurrent-connection and automatic speed policies through a private Unix
socket. See [native limits](native-limits.md) for setup and enforcement semantics.

`xray open-node-capabilities` reports the versioned integer
`mieru_udp_target: 1` together with the limiter capabilities. Consumers must
not treat booleans, strings, other integers, or a command failure as support.

Run all builds and tests on the designated VPS. With Git and a verified Go
1.26.7 toolchain available, from this checkout:

```bash
python3 scripts/vps/build-protocol-runtime.py \
  --work-dir /tmp/open-node-runtime-build \
  --go /absolute/path/to/go/bin/go \
  --reference-binary
```

The work directory must not exist. The helper fetches the exact revision,
builds the optional unmodified reference executable, applies all patches, runs
the AnyTLS, Snell, Mieru, limiter and dispatcher package tests, builds the
compatibility executable and verifies the Go modules. It does not change the
system Go installation or a running service. Outputs include:

- `xray`: the patched runtime; `xray-reference`: the optional original runtime.
- `build.json`: source revision, Go/platform identity and SHA-256 digests.
- `matching-source.tar.gz`: the upstream source, patches and added overlay source.
- `LICENSE-Xray-MPL-2.0` and all four patch files: runtime license and changes.

Keep these files together when distributing a runtime build, including the
matching source and notices required by its license. The patches and overlay are explicitly
[MPL-2.0](../runtime/xray/LICENSE). No runtime binary is bundled in the Agent
wheel or silently downloaded by this helper's consumers.

## Installation And Migration

Use the [host deployment CLI](agent-deployment.md) with `--xray` pointing to
the verified compatibility binary and `--xray-config` to a copy of the original
single-file JSON configuration. The installer preserves the input files and
owns its separate non-root runtime. Stop or relocate the original listener
before using its public port; do not run two owners against the same config.
Existing separately owned services use the [systemd binding](external-systemd.md)
contract instead. Its separate [multifile takeover](xray-takeover.md) workflow
can consolidate explicitly authorized JSON/JSONC inputs. This has real coverage
with official Xray; the fork's multifile/runtime combinations still need verification.

The Agent edits `settings.users` for AnyTLS, Snell and Mieru, and
`settings.clients` for VLESS, VMess, Trojan, Shadowsocks and Hysteria. An email
identifies one managed user; replacement retains its position and removal
preserves other users and unrelated configuration. The runtime validates each
result before application. An incompatible official Xray release is refused
before stopping the current fork process.

The empty-user patch applies to explicit runtime edits: an empty Snell or Mieru
inbound remains present but rejects traffic before protocol processing. Managed
subscription access does not use that listener state for final-user withdrawal.
It suspends the entire inbound and stores its position and empty template in the
private Agent journal, then restores it only with matching configuration
evidence. The two recovery contracts must not be treated as interchangeable.

Scan the runtime, import its available node drafts, assign a plan and explicitly
queue provisioning with runtime restart. Import derives public transport fields,
not existing users' passwords. New users receive the catalog's own stable
credentials. Existing user migration still requires explicit catalog/credential
mapping; importing an inbound alone does not transfer its users to a plan.
For Mieru, UDP is enabled only when the latest scan says Xray is running, is no
more than ten minutes old, and contains the strict integer capability
`mieru_udp_target: 1`. Missing or stale evidence imports `udp: false`.
The Mieru `transport` and `udp` fields participate in runtime/catalog drift and
guarded public-field synchronization.

Snell takes its version, obfuscation and v6 mode from the **first user**. Agent
edits require shared transport options, retain first-user order during rotation,
and store non-secret transport metadata at the settings level when empty.
Re-adding a user consequently retains those options. Runtime-node import uses
the same source. Mixed user transport settings and v6 `unsafe-raw` cannot be
automatically imported/managed; review and normalize the complete configuration.
The unauthenticated mode cannot provide per-user revocation guarantees.

## Verified Client Boundaries

The VPS smoke uses unmodified Mihomo v1.19.30 for AnyTLS, Snell v4/v5 and Mieru.
The original pinned fork executable is the free Snell v6 test client.

| Runtime | Client / Transport | Target Traffic |
| --- | --- | --- |
| AnyTLS | Mihomo, certificate-verified TLS | TCP and UDP |
| Snell v4 | Mihomo, plain or HTTP obfuscation | TCP and UDP |
| Snell v5 | Mihomo, TLS obfuscation | TCP and UDP |
| Snell v6 | Pinned fork, default or unshaped mode | TCP and UDP |
| Mieru | Mihomo, TCP or UDP underlay | TCP and UDP |

The unmodified reference runtime remains a negative control: its Mieru server
ignores SOCKS5 UDP ASSOCIATE over both underlays. The compatibility runtime's
versioned capability distinguishes the patched behavior. The Agent caches that
probe by bound binary identity and reports no capability when probing or binding
fails. The control plane additionally requires a running, fresh scan and
overwrites Mieru subscription `udp` from that evidence, so a forged catalog node
configuration cannot enable unsupported target traffic.

The [subscription exporters](subscriptions.md) now filter incompatible entries
for their pinned client versions. Snell v6 is available through native
`format=xray` export, with an optional plan-scoped node selection. A separate
full-export smoke starts real Mihomo v1.19.30, sing-box v1.13.19 and the patched
Xray client, checks every included node and imports the URI/Base64 payloads.
The lifecycle smoke also consumes native Snell v6 outbounds when testing
assigned subscriptions. Version-specific limitations and unverified
combinations remain explicit; this is not universal client compatibility.

See [testing.md](testing.md#fork-protocol-smoke) for exact reproduction commands
and [migration-map.md](migration-map.md) for the remaining replacement gates.
