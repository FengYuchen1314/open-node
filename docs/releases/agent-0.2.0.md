# Agent 0.2.0 Preview

This is a prerelease of the independent Open Node Linux Agent, not a claim of
complete MMWX replacement. The exact release source commit is recorded in
`BUILD.json`; the `agent-v0.2.0` tag must resolve to the same commit. The
published asset digests and `SHA256SUMS` are authoritative for each download.

Open Node and this Agent remain free to use under the repository's MIT license.
They do not require an activation key, paid entitlement, commercial license
server, or feature subscription.

## Supported Preview Scope

The verified target is Debian 12 amd64 with Python 3.11 and systemd. This
prerelease supports one single-process Open Node control plane with one worker,
and independently installed non-root Agents using `runtime_mode: managed` with
an operator-supplied Xray binary. It is intended for new installations or a
controlled migration in which ownership, configuration, credentials, ports,
and rollback are reviewed explicitly.

Arbitrary legacy process adoption, unrecorded private-resource discovery,
multi-control-plane operation, additional backend workers, other Linux
distributions or architectures, and universal client/runtime compatibility are
outside this prerelease's supported scope.

## Release Assets

The `agent-v0.2.0` GitHub prerelease must contain exactly these four assets:

- `open_node_agent-0.2.0-py3-none-any.whl` — Agent package.
- `open-node-agent-bootstrap-0.2.0.tar.gz` — standard-library host installer,
  privileged lifecycle helper sources, and MIT license.
- `BUILD.json` — exact source commit, version, Python/platform identity, and the
  two executable artifact hashes.
- `SHA256SUMS` — checksums for the wheel, bootstrap archive, and `BUILD.json`.

Download all four assets from the same release and run:

```bash
sha256sum --check SHA256SUMS
```

Also verify that `BUILD.json` records version `0.2.0` and that its source commit
equals the exact commit resolved by the `agent-v0.2.0` tag.
Extract the bootstrap into a new private directory and follow
[Agent deployment](https://github.com/FengYuchen1314/open-node/blob/agent-v0.2.0/docs/agent-deployment.md)
using its `service.py` and the verified wheel. A controller-issued node token,
valid Xray configuration, and trusted Xray executable are still required.

## Included In 0.2.0

- TLS-verified WebSocket and HTTPS polling transports with durable command
  leasing, deduplication, result replay, telemetry, runtime scans, and bounded
  streaming output.
- Dedicated non-root systemd installation, package and runtime preflight,
  readiness checks, data-preserving upgrade and rollback, interrupted-switch
  recovery, uninstall/reinstall, and explicit purge of owned resources.
- Explicit host opt-in for remote Agent upgrade, rollback, and data-preserving
  uninstall from a fixed HTTPS release source. Every upgrade requires an exact
  version and wheel SHA-256, and the controller waits for the durable final
  outcome rather than treating queue acceptance as success.
- Managed Xray configuration validation, guarded writes, service recovery,
  client provisioning and revocation, traffic/statistics reporting, official
  Xray release switching, and persistent subscription-access reconciliation.
- Native managed-mode user limits and automatic speed rules, recoverable node
  cleanup, owned Nginx/TLS/tunnel operations, diagnostics, certificate
  deployment, and host-approved lifecycle policy updates.
- Agent-side AnyTLS, Snell, and Mieru user management when the operator supplies
  a compatible runtime. Versioned runtime capabilities fail closed; unsupported
  or stale capability evidence is not treated as feature support.

Both WebSocket and HTTP modes have release gates for real forwarding, package
switching, rollback, interruption recovery, restart persistence, and final
result acknowledgement.

## Compatibility And Security Boundaries

The optional Xray compatibility runtime is **not** bundled in the wheel,
bootstrap archive, or this GitHub release. Open Node does not download it
automatically. An operator who needs fork-only protocols must separately build
or obtain a verified runtime and retain its matching MPL-2.0 source, patches,
licenses, and build manifest as documented in
[Fork Protocol Runtime](https://github.com/FengYuchen1314/open-node/blob/agent-v0.2.0/docs/fork-runtime.md).

Human-readable generated/custom subscription aliases and legacy `/x` links are
a migration-only compatibility feature and are disabled by default. Production
deployments keep `OPEN_NODE_SHORT_LINKS_ENABLED=false`; the long 256-bit
subscription token remains the supported public bearer URL. Enable compatibility
aliases only for a controlled legacy migration on a restricted endpoint.

A control-plane image built before this hardening ignores that setting and is
not a security-compatible public rollback. Keep the production edge rules that
deny `/x` and non-43-character subscription bearers, or isolate subscriber
traffic until the hardened image is restored. Startup also fails closed when
SQLite reports an existing foreign-key violation; repair the database offline or
restore the pre-upgrade backup rather than bypassing the check.

This prerelease does not claim public Cloudflare WARP provider compatibility,
production DNS-provider account coverage, Apple Surge application import,
external-systemd limiter support, arbitrary existing-process takeover, wider
OS/architecture coverage, zero-downtime upgrades, or arbitrary database
downgrades. Review the
[migration map](https://github.com/FengYuchen1314/open-node/blob/agent-v0.2.0/docs/migration-map.md)
before migrating an existing installation.

## Final Release Verification

All builds and tests run on the designated Debian 12 amd64 VPS. The final clean
source artifacts must pass the Agent unit suite, wheel/RECORD and bootstrap
structure checks, the real systemd service lifecycle smoke, and the remote
lifecycle smoke over both transports before upload.

Publishing the assets is not the final gate. Download the exact public wheel
from the completed `agent-v0.2.0` GitHub prerelease and run:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-release.py \
  --wheel /path/to/downloaded/open_node_agent-0.2.0-py3-none-any.whl \
  --nginx /path/to/verified/nginx
```

This smoke must fetch the same wheel again through the default GitHub Releases
URL, verify its version and SHA pin, install it through the opted-in root helper,
forward real VLESS traffic, and roll back successfully over both WebSocket and
HTTP. If the public-download smoke, release-asset digests, tag target, or
`BUILD.json` revision do not match, the prerelease is not accepted and must not
be presented as verified.

## Acceptance Record

The prerelease was accepted on 2026-08-29 UTC. The annotated
`agent-v0.2.0` tag and `BUILD.json` both resolve to
`3bf30c0b488efe6575927d01acca07f6dc0b3662`. GitHub exposes exactly the four
specified assets with these SHA-256 digests:

- wheel: `07c0a582b009d1a9eee07eb17e4dd39fa6c5a7b8db986db0cf8543fedc2feb8a`
- bootstrap: `4584ef44ceaef72d89c145ecd98c2f755600ff6050603d0e1bb934d977e67ac3`
- `BUILD.json`: `73ee3ed0029c17ee8c9954572eceff6f9d6a333e14dbdda0aa44a775c5715c16`
- `SHA256SUMS`: `0ef57a0f6c5e48d5f0648fe5288b598c5554d7e10d2b557c5a8077ee3c66838c`

All assets were downloaded again without GitHub credentials. Checksums,
revision, wheel metadata, and bootstrap structure passed. The downloaded wheel
then passed the real public-release smoke over both WebSocket and HTTP,
including the default GitHub download, SHA-pinned upgrade, live VLESS traffic,
rollback, and systemd lifecycle cleanup.
