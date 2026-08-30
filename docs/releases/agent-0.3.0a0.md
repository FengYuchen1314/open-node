# Agent 0.3.0a0 Preview

This alpha prerelease packages the Agent contracts used by the current Open
Node control plane. It is not a stable release or a claim of full MMWX parity.
The source commit is `6ca84e21202950bf5ee4754a8ae20e28dbde42ed`; the annotated
`agent-v0.3.0a0` tag and `BUILD.json` must both identify that commit.

The supported target remains Debian 12 amd64, Python 3.11 and systemd, with one
control-plane process/worker and dedicated non-root managed Agent/Xray
installations. Open Node is free under MIT with no activation keys, commercial
license server or paid feature checks.

## Changes since 0.2.0

- Authenticated master-URL probing and guarded persistence of a new master URL
  to the exact loaded private Agent configuration, including recovery-only
  behavior and reconnect after the result is delivered.
- Expanded native runtime controls and Xray system-configuration persistence
  used by the newer control-plane forms.
- Explicit capability boundaries: no inbound Agent management listener and no
  switch to an embedded Xray process are advertised when unsupported.
- Accumulated Agent, transport, runtime and failure-path regression coverage.

The existing dedicated-account installation, SHA-pinned upgrade/rollback,
interruption recovery, result replay, owned runtime management and explicit
privileged lifecycle opt-in remain in place. See the
[0.2.0 record](https://github.com/FengYuchen1314/open-node/blob/6ca84e21202950bf5ee4754a8ae20e28dbde42ed/docs/releases/agent-0.2.0.md)
for their original supported boundaries.

The panel-issued installer is supplied by a newer **control-plane** build,
not by this Agent tag. It uses this release's packages; it does not automatically
enable the remote privileged lifecycle helper. Fork-only runtimes are not
bundled in these assets. The panel's default Xray selection is a separately
checksum-pinned official release.

## Exact assets

The prerelease contains exactly four assets:

| Asset | SHA-256 |
| --- | --- |
| `open_node_agent-0.3.0a0-py3-none-any.whl` | `ef7267b062db6a4128a63b6a5e0228e40f749b61fd3a076c09dfd826f8c691fd` |
| `open-node-agent-bootstrap-0.3.0a0.tar.gz` | `9bc36c9c36b169fe1dcc67269eb11a4a12f0602ab31fc8aac493b8510dfe1310` |
| `BUILD.json` | `3240a9a05b3507a4614b6cba91b560aaa1095acb79b599ad8a2cccf67e683d8f` |
| `SHA256SUMS` | `c51edd409a222d8389415ed047545edf9b99e64faf0c6a75f1636b1627f86099` |

The bootstrap archive contains exactly `service.py`, `lifecycle_protocol.py`,
`lifecycle_host.py`, `lifecycle_report.py` and `LICENSE`, as flat regular files.
Its executable source bytes and all 28 Agent Python source files match the
exact commit. Wheel metadata and all 33 RECORD entries are verified.

Use `scripts/vps/build-agent-release.py` on the isolated VPS with an existing
Python/pip/hatchling build environment. It exports committed Git objects,
builds without fetching build dependencies, pins wheel timestamps to the
commit time, and generates the deterministic bootstrap archive, manifest and
checksum list. It never overwrites existing assets or publishes a release.
Build reproducibility requires matching the recorded build-tool versions:
Python `3.11.2`, pip `23.0.1` and hatchling `1.32.0` for these artifacts.

## Release gates

Before accepting a release, verify the source/tag, public asset set and hashes,
run `sha256sum --check SHA256SUMS`, then run the downloaded wheel through
`smoke-agent-release.py` with a verified Nginx and Xray. That gate must exercise
the default anonymous GitHub download, SHA-pinned upgrade, live VLESS forwarding
and rollback over both WebSocket and HTTP.

The new panel installer additionally requires its own real-systemd bootstrap
gate and production browser tests. A successful build or unit suite alone is
not evidence that the one-command installation works. Public DNS/TLS,
Cloudflare account deployment, wider architectures/distributions and complete
MMWX migration remain outside this bounded Preview.

Pre-upload checks on the isolated VPS passed for these exact bytes: the real
service gate (134.02 s) and complete WebSocket/HTTP remote-lifecycle gate
(640.09 s), including non-root readiness, VLESS traffic, failed-start recovery,
interrupted upgrade, rollback, durable completion and cleanup. The production
container/image/start identity and restart count remained unchanged and healthy.

After publication on 2026-08-30 UTC / 2026-08-31 Asia/Shanghai, the anonymous
GitHub API and `git ls-remote` confirmed annotated tag
`4328fdacbaa194edb45caa5920d0518f20eeaca0` and release `379344539` with exactly
the four pinned assets. Fresh anonymous downloads passed all hashes, BUILD/tag
identity, wheel source/RECORD and bootstrap structure checks. The downloaded
wheel then passed the default-GitHub-source WebSocket and HTTP release gate in
104.95 seconds: real download, pinned upgrade, readiness, VLESS forwarding and
rollback. Both uniquely owned fixtures were removed with no residual roots,
units, accounts or processes. The production deployment remained unchanged.

The previous wheel in that release gate is a synthetic fixture version, not
the published 0.2.0 wheel; the result does not certify every historical migration.
The alpha is explicitly not latest (`/releases/latest` currently returns 404).
VPS evidence is retained under
`/tmp/open-node-agent-published-0.3.0a0.72yMxP/final-report.json` and
`artifact-verification.json`; reports contain hashes and identities, not secrets.
