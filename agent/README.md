# Open Node Agent

Independent Python 3.11+ Linux host agent for the Open Node control plane. Its source
lives in this monorepo and is distributed under the repository's MIT license.
It does not include MMWX Agent source, activation checks, paid feature flags,
license-server calls, or an embedded copy of the MMWX Xray fork.

This agent is under active development. The implemented runtime path uses an
operator-provided Xray binary, either as an owned subprocess or a configured
systemd service. It is not yet a full replacement for every MMWX host operation.

## Configuration

For a persistent host installation with a dedicated account, systemd service,
upgrade rollback, and data-preserving uninstall, use the
[deployment CLI](../docs/agent-deployment.md). The manual commands below are
for development or separately managed deployments.

Install the agent package and provide a node token created by the control plane:

```bash
python3 -m venv /opt/open-node-agent/venv
/opt/open-node-agent/venv/bin/pip install ./agent
chmod 600 /etc/open-node-agent/config.yaml
/opt/open-node-agent/venv/bin/open-node-agent --config /etc/open-node-agent/config.yaml --check
/opt/open-node-agent/venv/bin/open-node-agent --config /etc/open-node-agent/config.yaml
```

Configuration example:

```yaml
master_url: https://control.example.com
token: REPLACE_WITH_THIS_NODE_TOKEN
connection_mode: auto
state_dir: /var/lib/open-node-agent
xray_binary: /usr/local/bin/xray
xray_config: /etc/open-node-agent/xray.json
runtime_mode: managed
auto_start: true
# Optional existing Xray StatsService address:
# stats_address: 127.0.0.1:46736
```

Traffic accounting requires a configured Xray `StatsService` and corresponding
user/inbound policy counters, plus `stats_address` above. The agent does not
silently inject those settings into an operator's existing configuration.
[Xray API configuration](https://xtls.github.io/en/config/api.html) and
[Xray policy configuration](https://xtls.github.io/en/config/policy.html)
document these runtime settings. The smoke script contains a working loopback
example with per-user traffic accounting enabled.

Create the Xray configuration before starting. The agent never overwrites an
existing MMWX configuration or takes over a running process by discovery.
For an existing dedicated systemd service, select `runtime_mode: systemd` and
follow the [external systemd setup](../docs/external-systemd.md). Both processes
use a dedicated non-root account, verified unit/binary/config binding, and an
explicit host-installed polkit grant for only that canonical service.
In managed mode the agent stops its own child on graceful shutdown; use a
systemd service with `KillMode=control-group` to contain abrupt agent termination.

HTTPS verification is always enabled. `ca_file` can name a private CA bundle.
`allow_insecure_http: true` is for isolated test networks or trusted SSH tunnels
only, never for an Internet-facing control plane. No HTTP control listener is
opened by the agent. Legacy `master_public_key`/securechan settings are not
accepted silently; migrate explicitly to an HTTPS Open Node URL and a newly
issued Open Node token.

## Execution Contract

The agent serializes commands and persists execution records in a private
SQLite journal. Completed command IDs return their cached result after
redelivery. If the agent was interrupted after recording an execution but
before persisting its result, it refuses to repeat the operation automatically;
inspect the runtime and issue a new command after reconciliation. This is not
an exactly-once claim. Unacknowledged results are retried on reconnect.

[Remote Agent lifecycle](../docs/agent-lifecycle.md) is a deliberate exception
for host-approved package operations: its separate root-owned job journal
permits redelivery of the same deferred request without repeating deployment.
The host owner must opt in with a fixed HTTPS release source. The Agent remains
non-root, and queued work is not reported as completed before its final outcome.

Xray validates candidate JSON before any configuration write, even if a legacy
request carries `force=true`. Full config writes require an explicit runtime
restart. Inbound, outbound, routing, and batch changes restart a running runtime
unless `no_restart` is requested. If restart fails, the old file is restored and
its runtime is restarted. A requested stop persists across agent restarts.

Supported operations include config read/write/test, scan, Xray service control,
inbound/outbound/routing edits, VLESS/VMess/Trojan/Shadowsocks client edits,
batch client provisioning, host metrics/NICs, network speed, Xray stats, and
bounded Agent/Xray/Nginx logs. Network speed is bytes per second between successive speed
requests; the first sample returns zero and counter resets never produce
negative rates.
Native [diagnostics](../docs/agent-diagnostics.md) include concurrent TCP latency,
optional ICMP fallback, NextTrace return-route evidence, and ownership-scoped
log file listing/clearing. Raw network probes require explicit host permissions;
TCP latency works without them.
Native [WARP outbounds](../docs/warp.md) include explicitly consented free
registration, optional provider WARP+ updates, userspace WireGuard, durable
configuration recovery and retryable provider removal. Existing routing is
preserved; no host route or network-admin permission changes are needed.
Managed [Xray release operations](../docs/xray-releases.md) install explicit
official versions using archive SHA-256 pins, validate existing configuration,
retain a previous selection and recover failed/interrupted switches. Remote
removal disables the owned child while preserving config and release cache.
These commands do not overwrite the root-owned bootstrap runtime or external
systemd services.
Optional [owned Nginx management](../docs/nginx-management.md) includes service
control, configuration/site files, HTTP/TLS sites, reverse proxies, stream-port
cleanup, logs, and supplied certificate deployment/rotation with rollback.
Native tunnel deployment combines owned Nginx and official Xray configuration,
validates the current snapshot hash, and restores files and service intentions
after failures or interruption. The Agent can discover statistics from a
loopback Xray `api.listen`; explicit `stats_address` takes precedence.
ACME issuance and renewal are handled by the
[control plane](../docs/certificates.md), which sends certificate deployment
commands to this Agent. Host-opted-in HTTP-01 validation serves short-lived
public challenges through a standalone listener or owned Nginx webroot, with
durable cleanup and expiry. Account and issuance keys remain on the control
plane. See [remote validation setup](../docs/certificates.md#remote-validation-node).
No Agent-local ACME runtime is required.
Unsupported operations return 501 rather than reporting success. Public WARP
provider verification and further migration workflows remain release gates.
Native [fork protocol user management](../docs/fork-runtime.md) now edits
AnyTLS/Snell/Mieru user containers. Its optional MPL-2.0 runtime patch retains
empty listeners while rejecting traffic, so last-user revocation can persist.
Mieru UDP targets and stock-client subscription filtering remain unfinished.

## Verification

Run agent tests and build checks only on the designated VPS. The runtime smoke
uses an official Xray release with a pinned archive digest and disposable paths;
it must not use or modify the VPS's existing MMWX services.

The installed-wheel smoke covers both transports with real VLESS forwarding,
new client provisioning/revocation, Xray statistics reaching FastAPI, rejected invalid
writes, failed-restart rollback, ordered recovery, command deduplication across
process restarts, and persistent service-stop intent. This is not evidence for
every protocol. A separate [external systemd smoke](../docs/testing.md#external-systemd-smoke)
covers authorization, binding failures, live traffic and independent ownership
over HTTPS/WSS. A separate lifecycle smoke
verifies installation, upgrades, recovery, and removal of the owned systemd
service with a non-root account.
See [VPS test instructions](../docs/testing.md#independent-agent-smoke).
