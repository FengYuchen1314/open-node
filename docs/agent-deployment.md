# Agent Deployment

The repository includes a root-only deployment CLI for Linux hosts with Python
3.11+, `venv`, systemd, `useradd`, and `runuser`. It installs the independent
Open Node Agent with an operator-provided Xray binary. No activation code or
license service is used. Debian 12 x86-64 is the verified host configuration.

This deploys the Agent and its owned Xray child, not the FastAPI/Vue control
plane. The control plane must already be reachable and have issued this node's
token. Do not reuse a production token while its previous agent is still running.

Optional [Nginx and certificate management](nginx-management.md) runs another
owned child under this account. Set `nginx_binary` and optional `nginx_modules`
in the installation input to copy those runtime files, without replacing any
existing Nginx service. Its desired running state participates in readiness.

## Prepare

Use a trusted wheel built from this repository. The VPS test runner builds
`agent/dist/open_node_agent-0.1.0-py3-none-any.whl`. The bootstrap deployment
script uses only Python's standard library; package dependencies are installed
into a separate virtual environment for each release, never the system Python.

Prepare a private Agent input file, for example `/root/open-node-agent.yaml`:

```yaml
master_url: https://control.example.com
token: REPLACE_WITH_THE_NODE_TOKEN
connection_mode: auto
runtime_mode: managed
# Configure this only when the supplied Xray config enables StatsService:
# stats_address: 127.0.0.1:46736
```

Set that file's permissions to `0600`. Supply a working Xray JSON configuration
and a trusted, executable Xray binary appropriate for the host architecture.
The installer copies both into its own directory; it does not stop, overwrite,
or take ownership of a pre-existing MMWX/Xray installation. Resolve listener
port conflicts before installing. For geodata-dependent configs, provide
`--asset-dir` containing `geoip.dat` and/or `geosite.dat`.

TLS certificates and other external paths referenced by Xray must be readable
by the dedicated service account. Files under home directories are unavailable
to the hardened service. A configured Agent `ca_file` is copied automatically
into the private configuration directory. HTTPS certificate checks remain on;
`allow_insecure_http` is only for isolated testing or a trusted SSH tunnel.

## Install

Run from the repository checkout:

```bash
sudo python3 agent/app/open_node_agent/service.py install \
  --wheel agent/dist/open_node_agent-0.1.0-py3-none-any.whl \
  --config /root/open-node-agent.yaml \
  --xray-config /root/xray.json \
  --xray /usr/local/bin/xray
```

The default root is `/opt/open-node-agent` and the service is
`open-node-agent.service`. Both must be unused unless this installer already
owns them. Existing manual installations are not silently adopted. For a
separate instance, supply both global options before `install`:

```bash
sudo python3 agent/app/open_node_agent/service.py \
  --root /opt/open-node-agent-edge --unit open-node-agent-edge.service \
  install --wheel /path/to/open_node_agent-0.1.0-py3-none-any.whl \
  --config /root/edge.yaml --xray-config /root/edge-xray.json --xray /usr/local/bin/xray
```

The installer creates a matching non-login service account. It restricts
writes to the Agent's configuration/state directories and gives the service
only the capability needed for low-numbered listener ports. `KillMode=control-group`
contains the owned Xray child if the Agent exits abruptly. Agent program files,
the bootstrap runtime and installation metadata remain root-owned; tokens,
config files, and the execution journal remain private. Service definitions or
external systemd overrides that
do not match the recorded installation cause updates/removal to stop for review.

Remote [Xray release management](xray-releases.md) retains that root-owned
bootstrap binary and uses a separate checksum-verified release cache in the
Agent-owned state directory. It does not make the Agent package or installation
metadata writable by the Agent. Host upgrade preflight validates the selected
runtime and rejects unresolved runtime switches or incompatible older packages.

Before enabling the service, the installer validates the Agent configuration
and Xray config as the service account. Readiness then checks the systemd PID,
the executing package directory/version, fresh local health data, authenticated
control-plane contact, and the desired Xray running state. An old health file
cannot make a new process ready. The default readiness timeout is 45 seconds;
the global `--timeout` option accepts 3-300 seconds.

## Upgrade And Recover

```bash
sudo python3 agent/app/open_node_agent/service.py upgrade --wheel /path/to/new-agent.whl
sudo python3 agent/app/open_node_agent/service.py rollback
sudo python3 agent/app/open_node_agent/service.py status
```

Each release is identified by its wheel version and SHA-256 digest, so an
artifact cannot overwrite a different build with the same version. Its virtual
environment is created in its final directory, not moved after installation.
Upgrades prepare and validate the candidate while the old service continues
running. Only then is the service stopped and the release pointer switched.
If startup/readiness fails, the previous release is restored and restarted.
Configuration, Xray credentials, and the command journal are not reset.

An intentionally stopped Agent service stays stopped during upgrade/rollback;
only preflight validation runs in that case. A requested Xray stop recorded by
the Agent also survives service restarts. Upgrade does not change an existing
service's boot-enable preference. Normal install enables the new service.

Release-switch transactions are persisted before stopping the service. If the
deployment process is interrupted during a switch, inspect `status` and run:

```bash
sudo python3 agent/app/open_node_agent/service.py recover
sudo journalctl -u open-node-agent.service -n 100 --no-pager
```

Recovery restores the pre-switch release and running state. If rollback cannot
start either, the pending transaction remains available for another recovery
attempt after the underlying fault is corrected. An interruption during package
staging can leave an incomplete staging directory; the installer refuses to
reuse it and reports the condition for inspection. It does not guess that
partially installed packages are healthy.

On a failed *first* installation, the owned service is stopped and diagnostic
files remain. Correct the input and retry `install` with all three source
options, or edit the owned config and retry without source options. If `status`
shows a pending switch, run `recover` before retrying. Back up configuration and
state before upgrading across future releases with incompatible data formats;
this mechanism does not promise reversal of arbitrary schema migrations.

## Uninstall

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall
```

This disables/stops the owned service and removes its unit and Agent release
environments. Configuration, journal, logs, the copied Xray runtime/assets, and
the service account remain for recovery. It never follows an installation path
through a symlink or removes an unrelated service definition. Retain a checkout
or trusted copy of the bootstrap script so it is available after uninstall.

Reinstall with `install --wheel /path/to/agent.whl` and no source options to
reuse the preserved data. To explicitly remove the retained installation and
its dedicated account as well:

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall --purge
```

Original input files and the original Xray binary outside the installation root
are never removed. Purge does not call `userdel -r` or remove other home directories.

## Coverage Limits

The VPS lifecycle smoke exercises initial failure/retry, real non-root systemd
startup and forwarding, successful upgrade, explicit rollback, failed preflight,
failed startup rollback, interrupted switch recovery, process-group cleanup,
data-preserving uninstall/reinstall, and explicit purge. Unit tests additionally
cover path/ownership guards, stale health reports, and stopped-service behavior.

This installer uses `runtime_mode: managed` only. Control of an independently
managed external Xray systemd unit, broader OS/architecture coverage, remote
Agent-upgrade/uninstall command handlers and WARP remain separate work.
Managed Xray package installation, rollback and data-preserving removal now
have their own [release workflow](xray-releases.md) and real runtime smoke.
See [the migration map](migration-map.md) for the remaining scope.
Owned Nginx operation and certificate deployment are covered by their separate
runtime smokes; [central certificate issuance/renewal](certificates.md) does not
require adding an ACME client to the Agent installation.
