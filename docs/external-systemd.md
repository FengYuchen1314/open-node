# External Xray Systemd Service

`runtime_mode: systemd` connects a separately deployed Open Node Agent to one
host-owned Xray service. It does not import a running MMWX installation, replace
its binary, or change its service definition. Single-file binding is the default;
an explicit [multifile takeover](xray-takeover.md) opt-in can consolidate existing
JSON/JSONC inputs with private backups and durable recovery.
The [managed deployment CLI](agent-deployment.md) remains separate. No activation,
license server, or paid account is required.

## Host Requirements

Verified: Debian 12 x86-64, systemd 252, polkit, Python 3.11 and official Xray
26.3.27. `busctl --system --json=short` and the manager's `ExecStartEx` properties
must be available. Other versions need verification; missing metadata fails closed.

- Xray and the Agent use the same dedicated, static non-root account and primary
  group. Do not reuse `nobody`, an administrator, or a shared application account.
  The authorization helper requires a `nologin` or `false` login shell.
- The canonical unit, drop-ins, executable and their parent directories must be
  root-owned and not group/world writable. Set-id executables, transient units,
  aliases and pending `daemon-reload` changes are rejected.
- The unit executes `/absolute/xray run -config /absolute/xray.json` in a
  `Type=simple` or `Type=exec` foreground service. `-c`, `--config`, `=value`
  syntax and explicit JSON format are accepted. Multiple files and `-confdir`
  require the separate takeover opt-in. Shell wrappers, variable-dependent
  arguments, command prefixes, start/stop
  hooks and `RemainAfterExit` are not accepted.
- The single JSON file and its writable parent belong to this user. Use a private
  directory and file mode `0600` or `0640`. Symlinks, hard links and traversal
  paths are rejected. The Agent replaces this file atomically and never changes
  ownership of an existing host installation.
- Validation uses the unit's explicit `Environment` and `WorkingDirectory`,
  without inheriting the Agent's environment. Environment files, inherited/unset
  entries, extra supplementary groups, PAM, credential injection, chroots,
  images and bind/temporary mount remapping are not supported. Validation does
  not emulate every systemd sandbox/network restriction; startup must also succeed.
- The Agent must be able to inspect the live executable and command line under
  `/proc`. If Xray needs `CAP_NET_BIND_SERVICE`, configure that capability for
  both services. Restrictive `/proc` or LSM policies need host review, not a
  blanket sudo grant.

Review unit dependencies before granting access: normal systemd dependency
propagation still applies. Root changes and other host managers are not
serialized by the Agent's command queue.

## Configuration

Use unused paths and unit names, or deliberately adapt an existing dedicated
installation after backing it up. This example uses account `open-node-xray`
and a trusted Agent wheel installed in root-owned virtual environment
`/opt/open-node-external-agent/venv`, not the system Python.

Example root-owned `/etc/systemd/system/open-node-external-xray.service`:

```ini
[Unit]
Description=Host-owned Xray
After=network-online.target

[Service]
Type=exec
User=open-node-xray
Group=open-node-xray
WorkingDirectory=/etc/open-node-external
ExecStart=/usr/local/bin/xray run -config /etc/open-node-external/xray.json
NoNewPrivileges=true
Restart=on-failure
RestartSec=3
```

Prepare a working Xray JSON file and private Agent YAML in
`/etc/open-node-external`, owned by `open-node-xray`. Create that directory and
`/var/lib/open-node-external` with the same owner and mode `0700`. Keep the
executable and virtual environment root-owned.

Example `/etc/open-node-external/agent.yaml`, mode `0600`:

```yaml
master_url: https://control.example.com
token: REPLACE_WITH_A_NEW_OPEN_NODE_TOKEN
connection_mode: auto
state_dir: /var/lib/open-node-external
runtime_mode: systemd
xray_service: open-node-external-xray.service
xray_binary: /usr/local/bin/xray
xray_config: /etc/open-node-external/xray.json
auto_start: true
# ca_file: /etc/open-node-external/control-ca.pem
```

HTTPS verification stays enabled. A private control plane can supply a readable
CA bundle. Do not run old and new Agents with the same node token simultaneously.

Example root-owned `/etc/systemd/system/open-node-external-agent.service`:

```ini
[Unit]
Description=Open Node external-runtime Agent
After=network-online.target

[Service]
Type=exec
User=open-node-xray
Group=open-node-xray
ExecStart=/opt/open-node-external-agent/venv/bin/open-node-agent --config /etc/open-node-external/agent.yaml
NoNewPrivileges=true
KillMode=control-group
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

For low-numbered Xray listeners, add these to **both** service definitions:

```ini
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

Do not add `PartOf`, `BindsTo` or other stop propagation between the services
if Xray must remain available when the Agent restarts or is removed.

## Grant And Revoke

After installing the package, account, configuration and units, run as host
administrator:

```bash
sudo systemctl daemon-reload
sudo /opt/open-node-external-agent/venv/bin/python -I -m open_node_agent.systemd_access grant \
  --user open-node-xray --service open-node-external-xray.service \
  --xray-binary /usr/local/bin/xray \
  --xray-config /etc/open-node-external/xray.json
sudo systemctl start open-node-external-agent.service
```

The helper verifies the binding, then creates a root-owned `0644` rule under
`/etc/polkit-1/rules.d`. It grants only `start`, `stop` and `restart` for that
exact service, not manager reload, unit editing/enablement, arbitrary services,
shell execution or root access. Polkit reloads rules automatically. Control
commands disable interactive authorization and password prompts.

This grant applies to the **Unix account**, including its other processes,
not exclusively the Agent process. It does not remove permissions granted by
other host policies. Keep the account dedicated and unit definitions trusted.
Identical grants are repeatable; modified or unsafe rule files are never
overwritten or removed. Revoke before deleting or reusing the account:

```bash
sudo systemctl stop open-node-external-agent.service
sudo /opt/open-node-external-agent/venv/bin/python -I -m open_node_agent.systemd_access revoke \
  --user open-node-xray --service open-node-external-xray.service
```

Revocation does not stop Xray or delete its configuration, unit, account or
package. Those remain separate host-administrator actions.

## Operation And Recovery

Config read/test/write, inbound/outbound/routing edits, batch provisioning,
StatsService telemetry and Xray start/stop/restart use the bound service.
Full config writes report when a separate restart is needed. Edit-and-restart
failures restore the old file and attempt to restart it. Check the unit journal
if host restrictions or start-rate limits also prevent rollback.

Binding changes are checked again before mutations. A mismatch produces an
explanatory scan/service-status message and `runtime_ready: false` in local
health, while the Agent stays connected. It does not stop an unrelated service
or report its inbounds as this runtime. Correct the host config, reload the
manager, and restart Xray on the host if its live executable/arguments are
stale. The next scan rechecks without requiring an Agent restart.

An Agent-requested stop is remembered across Agent restarts. It does not override
an administrator, a boot-enabled Xray unit or another manager starting Xray.
Agent shutdown and termination leave the independent Xray process alone; do not
put external Xray in the Agent's cgroup.

The normal [command journal contract](../agent/README.md#execution-contract)
applies. A process crash during a config mutation is not a durable exactly-once
rollback guarantee; reconcile interrupted commands before issuing new mutations.
The opted-in [takeover transaction](xray-takeover.md#recovery-and-backups) has
its own durable file-recovery journal; that does not change the ordinary config
mutation contract. Host package upgrades/removal remain separate. Managed release
APIs refuse to replace/remove the external binary.
Xray logs remain in the host journal; the Agent's owned `xray.log` is not that journal.

See the [VPS fixture](testing.md#external-systemd-smoke). The implementation uses
the official [systemd D-Bus interface](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html)
and [polkit rules API](https://polkit.pages.freedesktop.org/polkit/polkit.8.html).
