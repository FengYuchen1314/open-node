# Agent 0.1.0 Preview

This is a preview release of the independent Open Node Linux Agent, not a claim
of full MMWX replacement. Source is in this public MIT monorepo. No activation
key, subscription, paid entitlement or license-server connection is required.

The verified host is Debian 12 x86-64 with Python 3.11, systemd and an
operator-provided official Xray runtime. The wheel is Python-only; it does not
bundle Xray, Nginx or the proprietary/reference Agent. Python dependencies are
installed into per-release virtual environments.

## Assets

- `open_node_agent-0.1.0-py3-none-any.whl`: Agent package.
- `open-node-agent-bootstrap-0.1.0.tar.gz`: standard-library host installer,
  privileged lifecycle helper sources and MIT license.
- `BUILD.json`: source commit and artifact hashes from the designated VPS build.
- `SHA256SUMS`: SHA-256 checksums for the downloadable artifacts.

Download these assets from the same release and verify them with
`sha256sum --check SHA256SUMS`. Extract the bootstrap archive into a new private
directory. Follow [Agent deployment](https://github.com/FengYuchen1314/open-node/blob/agent-v0.1.0/docs/agent-deployment.md), using its
`service.py` and the downloaded wheel. A controller-issued node token, valid
Xray configuration and trusted Xray binary are still required.

## Included

- Native verified HTTPS/WebSocket and HTTP control connections, durable command
  results, telemetry, configuration/client management and real Xray forwarding.
- Dedicated non-root systemd installation, package preflight, selected-package
  readiness, rollback and data-preserving removal/reinstallation.
- Explicit host-opt-in remote upgrade/rollback/uninstall with an approved HTTPS
  source, version/SHA pins, durable jobs and acknowledged final outcomes.
- Recovery from staging, service-switch and removal interruptions, with no
  premature success or repeated deployment after lease redelivery.
- Managed Xray release selection and owned Nginx/TLS/tunnel integration.

Builds and all tests run on `185.99.135.224` over SSH, not on the desktop or
GitHub-hosted runners. The repository contains the repeatable test procedures
and isolated real-runtime/browser smokes in [testing.md](https://github.com/FengYuchen1314/open-node/blob/agent-v0.1.0/docs/testing.md).

## Limits

WARP and remaining diagnostics, ACME HTTP-01/account/revocation workflows,
public DNS-provider staging, fork-only protocol migration and wider OS/runtime
coverage remain unfinished. External systemd Xray operation and arbitrary
future database downgrades are not established by the managed-runtime tests.
See [the migration map](https://github.com/FengYuchen1314/open-node/blob/agent-v0.1.0/docs/migration-map.md#remaining-runtime-gates).
