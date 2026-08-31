# Panel-issued Agent installation

The panel follows the official MMWX workflow: create a server, generate its
installation command, run that command on the remote host, and observe the
Agent connecting. The behavioral references are the
[official Agent deployment guide](https://miaomiaowux.com/docs/en/install-agent/)
and `GetRemoteInstallScript` in the pinned control-plane repository
`tajiaoyezi/miaomiaowuX` at `c12ce653bc07fe30426b7dfcb85076974b7be0e0`.
Open Node does not copy the reference's licensing checks or its automatic
takeover of an existing service.

This is a new-host installer for **Debian 12 amd64, Python 3.11+, curl and
systemd**. It installs a dedicated non-root Agent and a separately managed
official Xray. It is not a whole-server migration tool, does not configure
DNS/public TLS, and does not install the control plane itself.

## Before generating a command

The remote host must reach the control plane through a trusted HTTPS URL. Set
`OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL` to that canonical control-plane URL, for
example `https://control.example.com`. Do not use the public Probe Worker URL.
The default `/api/v1` API prefix is required. A canonical reverse-proxy path
prefix is supported; encoded paths, dot segments, query strings, fragments and
embedded credentials are rejected. Request `Host`/forwarded headers never
choose the URL embedded in a command.

For a manually managed Compose deployment, persist this setting in the private
environment file used by that deployment and recreate its container using the
same project, volume and reviewed image. For an installer-managed deployment,
use the reviewed root installer's explicit setting override; do not hand-edit
its identity-tracked environment or manifest. See [control-plane
deployment](deployment.md) for the guarded update procedure.

The panel remains disabled until a canonical HTTPS URL and bundled, verified
release metadata are available. This setting does not create DNS records,
certificates, proxy rules or a Cloudflare account deployment. The remote host
also needs outbound HTTPS access to GitHub release assets and the package
repositories used by pip. If Python's venv support or the system CA bundle is
missing, the generated command permits installation of the fixed Debian
packages; it does not run a general operating-system upgrade.

## Install from the panel

1. In the control-plane Overview, create a new server. Do not reuse a server
   that has already registered an Agent or reported a heartbeat.
2. Select **Install Agent** in the creation result or the server's console
   action. Opening the dialog only reads status; it does not issue a ticket.
3. Choose Auto, WebSocket or HTTP polling. Confirm that this is a new Debian
   12 amd64 host dedicated to this server record, then generate the command.
4. Copy and run the complete command in a root shell on that host. Keep it
   private: the command contains a short-lived installation ticket.
5. Wait for `Agent installed and ready` from the installer. Check the Agent
   version, heartbeat and runtime telemetry in the panel before provisioning
   users or proxy inbounds.

The command downloads the installer over HTTPS into a private temporary file,
checks its SHA-256 against the bytes pinned when the command was generated,
and only then executes it. The installer downloads versioned Agent artifacts,
checks their pinned hashes and `BUILD.json` source/version identity, and safely
extracts the host installer. It separately verifies the pinned official Xray
archive. Neither an unpinned `latest` download nor `curl | bash` is used.

The bundled selection is Agent `0.3.0a1` (an alpha/Preview prerelease) and
official Xray `v26.3.27` for x86-64. The exact artifact identities are in
`backend/app/open_node/resources/agent-release.json` and the
[Agent release record](releases/agent-0.3.0a1.md). New installation configs enable
online-user statistics on policy level 0; existing hosts are not changed.

The initial Xray configuration has a loopback-only StatsService, a direct
outbound and **no public proxy inbound**. Create the intended nodes/inbounds
separately. Nginx, WARP, raw network capabilities, remote privileged Agent
lifecycle, and fork-only protocols are not enabled by this entry point. A
separately reviewed runtime is still needed for protocols absent from the
official Xray binary. See [fork runtime](fork-runtime.md) and
[manual Agent deployment](agent-deployment.md).

## Tickets and installation state

- A ticket lasts ten minutes and is stored as a hash, not plaintext, in the
  control-plane ticket table. The command never contains the long-lived Agent
  credential; that credential is delivered in an HTTPS POST response.
- Generating a replacement invalidates an **unclaimed** command. Once claimed,
  this server cannot receive another installation ticket, even after expiration
  or revocation. This prevents accidentally placing the same long-lived
  credential on two machines.
- The installer persists a private claim nonce before redeeming. After the
  first claim, only that same nonce can retry for at most two minutes and never
  beyond the ticket's original expiry. A different nonce is rejected. Once
  saved locally, the credential is not subject to that retry deadline.
- Ticket revocation stops future redemption, including retry. It does not
  retract an already-delivered Agent credential or stop an installed Agent.
  Use the explicit server/host lifecycle procedures for those operations.
- **Ticket claimed** and **Agent registered** are distinct observations.
  Neither alone proves installation succeeded. The host installer requires the
  expected package version, non-root process identity, authenticated connection
  and ready runtime before reporting success.
- Status never returns a ticket or long-lived credential. The dialog clears its
  command when closed, when switching servers, on replacement/expiration/claim,
  and after an authentication/status failure. It never persists the command in
  browser storage. An explicitly copied command can remain in your clipboard or
  shell history; the panel cannot erase those copies.

Private management routes require administrator authentication and CSRF/Origin
checks. Public redemption rejects cross-origin browser requests, bounds JSON
input, rejects duplicate/unknown fields and applies a persistent per-peer rate
limit in a namespace separate from administrator login. Errors never repeat
submitted secrets. Installation responses carry `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`. None of these bootstrap endpoints are allowed
through the anonymous public Probe Worker.

## Recovery and ownership

The installation root and unit use the first twelve hexadecimal digits of the
server UUID: `/opt/open-node-agent-<suffix>` and
`open-node-agent-<suffix>.service`. An existing root, account, group, unit or
drop-in with that identity is rejected, not adopted. The installer neither stops
nor rewrites unrelated MMWX, Xray or Nginx services.

Private inputs are retained under
`/var/lib/open-node-agent-bootstrap/<server-uuid-hex>-<ticket-hash-prefix>`.
The directory is root-owned mode `0700`; claim/configuration files are `0600`.
There is no raw ticket in the saved job. The local claim/configuration **does**
contain the long-lived credential: do not publish it as a diagnostic artifact.

If download fails before claiming, an unexpired command can be retried. If
claiming succeeded but no host resources were created, retry the same command
on the same host; the saved inputs preserve its identity. Partial host
installation is deliberately not auto-adopted. Follow the existing host
installer's status/recovery workflow using its retained, verified `service.py`,
the exact root and unit. Do not work around a failure by issuing another server
record against an unexplained existing installation or copying its credential
elsewhere. A control-plane upgrade may change the installer checksum and cause
an older copied command to fail closed.

For a deliberately private PKI, use a root-owned trusted CA file and set
`CURL_CA_BUNDLE` for the initial control-plane download and
`OPEN_NODE_AGENT_CA_FILE` for the Python control-plane client. The CA is copied
into the owned Agent configuration. GitHub downloads continue to use the Debian
system CA bundle; a custom control-plane CA does not authorize release assets.
Do not disable TLS verification.

The [testing guide](testing.md) distinguishes security/unit tests, production
browser acceptance, real systemd bootstrap over both transports, and the
published Agent download/upgrade/rollback gate. A synthetic registration in a
browser fixture is not counted as a real installation.
