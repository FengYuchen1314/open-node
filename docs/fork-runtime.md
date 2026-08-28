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

Run all builds and tests on the designated VPS. With Git and a verified Go
1.26.7 toolchain available, from this checkout:

```bash
python3 scripts/vps/build-protocol-runtime.py \
  --work-dir /tmp/open-node-runtime-build \
  --go /absolute/path/to/go/bin/go \
  --reference-binary
```

The work directory must not exist. The helper fetches the exact revision,
builds the optional unmodified reference executable, applies the patch, runs
the three protocol package tests, builds the compatibility executable and
verifies the Go modules. It does not change the system Go installation or a
running service. Outputs include:

- `xray`: the patched runtime; `xray-reference`: the optional original runtime.
- `build.json`: source revision, Go/platform identity and SHA-256 digests.
- `matching-source.tar.gz`: the tracked source with the applied changes.
- `LICENSE-Xray-MPL-2.0` and both patch files: runtime license and changes.

Keep these files together when distributing a runtime build, including the
matching source and notices required by its license. Both patches are explicitly
[MPL-2.0](../runtime/xray/LICENSE). No runtime binary is bundled in the Agent
wheel or silently downloaded by this helper's consumers.

## Installation And Migration

Use the [host deployment CLI](agent-deployment.md) with `--xray` pointing to
the verified compatibility binary and `--xray-config` to a copy of the original
single-file JSON configuration. The installer preserves the input files and
owns its separate non-root runtime. Stop or relocate the original listener
before using its public port; do not run two owners against the same config.
Existing separately owned services use the [systemd binding](external-systemd.md)
contract instead. Automatic `-confdir` consolidation is not implemented here.

The Agent edits `settings.users` for AnyTLS, Snell and Mieru, and
`settings.clients` for VLESS, VMess, Trojan, Shadowsocks and Hysteria. An email
identifies one managed user; replacement retains its position and removal
preserves other users and unrelated configuration. The runtime validates each
result before application. An incompatible official Xray release is refused
before stopping the current fork process.

Scan the runtime, import its available node drafts, assign a plan and explicitly
queue provisioning with runtime restart. Import derives public transport fields,
not existing users' passwords. New users receive the catalog's own stable
credentials. Existing user migration still requires explicit catalog/credential
mapping; importing an inbound alone does not transfer its users to a plan.

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
| Mieru | Mihomo, TCP or UDP underlay | TCP only |

The pinned Mieru implementation ignores SOCKS UDP-associate requests. UDP
**underlay** support does not imply UDP **target** forwarding; imported Mieru
nodes therefore set `udp: false`. UDP target support remains unfinished.

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
