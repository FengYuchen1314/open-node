# Remote Agent Lifecycle

The independent Agent supports operator-requested package upgrades, rollback,
and data-preserving removal. These operations require a separate, explicit
host-owner opt-in. An ordinary Agent token is not a root shell, and the Agent
continues to run under its dedicated non-root systemd account.

## Host Opt-In

First install a compatible wheel using [the host deployment CLI](agent-deployment.md).
From the matching trusted repository checkout, run on that host:

```bash
sudo python3 agent/app/open_node_agent/service.py enable-remote
```

For a named installation, supply the same global `--root` and `--unit` options
used during installation. The command validates the existing installation,
copies the standard-library maintenance code into its root-owned `lifecycle/`
directory, creates a private Unix socket and systemd worker, and restarts the
Agent with its socket configured. No public maintenance port is opened.

The default release source is this repository's GitHub Releases base URL:

```text
https://github.com/FengYuchen1314/open-node/releases/download
```

The host owner may instead authorize an HTTPS mirror when enabling the helper:

```bash
sudo python3 agent/app/open_node_agent/service.py enable-remote \
  --release-base-url https://releases.example.com/open-node \
  --release-ca /root/release-source-ca.pem
```

Omit `--release-ca` to use the system trust store. It does not disable certificate
or hostname verification. The mirror must serve this exact path layout:

```text
BASE/agent-vVERSION/open_node_agent-VERSION-py3-none-any.whl
```

The operator cannot override that source through an Agent command. Cross-origin
redirects are rejected except for GitHub's official release-asset hosts when
GitHub is the approved source. Downloads are bounded to 32 MiB and verified
against the supplied SHA-256, wheel distribution name and package version.
The installer also checks the package's reported runtime version.

Opt-in trusts the host-approved release publisher to supply executable code.
A checksum pins an artifact; it does not make an untrusted publisher safe.
The helper and installer are privileged code, stored separately from remotely
selected Agent releases. Remote upgrade does not replace that helper code.
Its code hashes, installation identity and unit definitions are checked before
host operations. Host-owner changes or overrides require local review.

## Operator Workflow

In the server dashboard, select the target and choose **Upgrade Agent**,
**Roll back Agent**, or **Uninstall Agent**. The dialog checks the host status
before offering a mutation. Upgrade requires an exact version, lowercase wheel
SHA-256 and restart confirmation. Rollback selects the recorded previous
package. Uninstall requires explicit confirmation and does not purge data.

The authenticated management API accepts the same payloads:

```text
POST /api/v1/servers/{id}/operations/agent/upgrade
{"version":"0.1.0","sha256":"REPLACE_WITH_THE_RELEASE_WHEEL_SHA256"}

POST /api/v1/servers/{id}/operations/agent/rollback
{"confirm":true}

POST /api/v1/servers/{id}/operations/agent/uninstall
{"confirm":true}

POST /api/v1/servers/{id}/operations/agent/lifecycle
```

These endpoints queue commands; HTTP 201 means queued, not installed or removed.
The native helper requires the explicit upgrade/confirmation payloads even
though legacy upgrade/uninstall wrappers retain their optional-body contract.
The status command is read-only. A host without opt-in reports that remote
lifecycle is disabled; it is never silently enabled by the controller.

## Completion And Recovery

The helper persists the request identity and payload fingerprint before doing
work. At most one package operation runs at a time. Redelivery of the same
request resumes its existing job or returns its immutable result; changing
the payload under that identity is rejected. Merely accepting a job does not
complete the controller command or release dependent commands.

An upgrade stages and validates the package while the current Agent is running.
The service is then switched, and successful completion requires fresh
readiness from the selected package and its desired managed runtimes. Failed
preflight preserves the current process. Failed startup restores the previous
selection. Configurations, node credentials, Xray state and the command journal
are retained.

Package staging, release switching and removal have persisted recovery state.
On a worker crash, systemd terminates its helper subprocess group. The restarted
worker recovers an interrupted operation and reports an explicit interrupted
outcome with the actual resulting selection. It does not pretend the original
request completed normally. A staging crash removes an incomplete owned release;
a switching crash restores the prior selection. Interrupted removal completes
the data-preserving cleanup. Inspect the result before explicitly retrying.

The host job budget is 15 minutes, including dependency installation. Controller
leases may expire earlier and be redelivered; that is not a second installation.
Recovery failures retain the transaction for local correction and `recover`.
Readiness cannot establish compatibility with arbitrary future data migrations.

Final reports run as the Agent account, not root. The reporter reads that
account's private configuration and uses the normal node token and verified
controller HTTPS connection. Both WebSocket and HTTP Agents also replay durable
results from their command journal. The controller's request-ID callback enforces
the same node-token boundary and immutable terminal outcomes as its ID callback.

After uninstall, the Agent itself is gone but the root maintenance worker remains
until the controller acknowledges the final result. Network errors keep the
command unresolved and are retried across worker restarts. Closing/reopening
the dialog resumes that existing command; it does not issue another removal.
After acknowledgment the worker disables/stops its own service and socket.

Retained helper files, private job history, configuration, runtime files and the
service account are intentional. Reinstall locally with `install --wheel ...`
without source options to preserve them and reactivate remote management.
Host-local `uninstall --purge` removes the owned helper units and retained
installation too. There is no remote purge command.

## Verification Scope

The dedicated [VPS smoke](testing.md#remote-agent-lifecycle) exercises real
systemd units, non-root Agents, trusted fixture HTTPS release/controller
endpoints, both transports and actual VLESS forwarding. Fixture wheels are
not published packages. The tests use isolated roots, accounts and ports;
they do not change an existing MMWX installation.

The supported, verified deployment target remains Debian 12 x86-64 with Python
3.11 and managed Xray. This feature does not establish support for every OS,
external runtime service, fork-specific protocol or future schema downgrade.
