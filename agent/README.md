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
set `xray_service` to its unit name. Only that configured service is controlled.
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

Xray validates candidate JSON before any configuration write, even if a legacy
request carries `force=true`. Full config writes require an explicit runtime
restart. Inbound, outbound, routing, and batch changes restart a running runtime
unless `no_restart` is requested. If restart fails, the old file is restored and
its runtime is restarted. A requested stop persists across agent restarts.

Supported operations include config read/write/test, scan, Xray service control,
inbound/outbound/routing edits, VLESS/VMess/Trojan/Shadowsocks client edits,
batch client provisioning, host metrics/NICs, network speed, Xray stats, and
bounded Xray logs. Network speed is bytes per second between successive speed
requests; the first sample returns zero and counter resets never produce
negative rates.
Optional [owned Nginx management](../docs/nginx-management.md) includes service
control, configuration/site files, HTTP/TLS sites, reverse proxies, stream-port
cleanup, logs, and supplied certificate deployment/rotation with rollback.
Unsupported operations return 501 rather than reporting success. WARP, ACME
issuance/renewal, remote upgrade/removal handlers, fork-only protocols, and
further migration workflows remain release gates.

## Verification

Run agent tests and build checks only on the designated VPS. The runtime smoke
uses an official Xray release with a pinned archive digest and disposable paths;
it must not use or modify the VPS's existing MMWX services.

The installed-wheel smoke covers both transports with real VLESS forwarding,
new client provisioning/revocation, Xray statistics reaching FastAPI, rejected invalid
writes, failed-restart rollback, ordered recovery, command deduplication across
process restarts, and persistent service-stop intent. This is not evidence for
every protocol or external systemd runtime mode. A separate lifecycle smoke
verifies installation, upgrades, recovery, and removal of the owned systemd
service with a non-root account.
See [VPS test instructions](../docs/testing.md#independent-agent-smoke).
