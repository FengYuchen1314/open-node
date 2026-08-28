# Agent Host Policy

The root-only `policy` command changes diagnostics permissions on an existing
managed systemd installation without reinstalling the Agent or resetting its
configuration. It belongs to the current source bootstrap, not the immutable
published Agent 0.1.0 bootstrap. Use a trusted current checkout and retain it for
recovery; installations with a lifecycle helper need the sibling bootstrap
modules too. The installed Agent must support diagnostics (source 0.2.0 or
newer); upgrade its wheel first when necessary.

## Change Permissions

Enable ICMP permissions without installing a tracing tool:

```bash
sudo python3 agent/app/open_node_agent/service.py policy --network-diagnostics on
```

Install a trusted, locally downloaded NextTrace binary at the same time:

```bash
sudo python3 agent/app/open_node_agent/service.py policy \
  --network-diagnostics on \
  --nexttrace /root/verified-nexttrace \
  --nexttrace-sha256 093849f1012b065c29d307b8e47fedec667206829c14e105f83a852f60c628d1 \
  --nexttrace-geoip off
```

This digest is for the Linux x86-64 Tiny 1.7.1 binary documented in
[agent-diagnostics.md](agent-diagnostics.md). Other binaries require their own
trusted digest. Both binary path and SHA256 are required together. Symlinks,
hard links, special files, set-ID files, files writable by others, and files
larger than 64 MiB are rejected. The installed copy is root-owned, mode 0755;
the version check runs as the Agent account, never root.

The first NextTrace installation requires an explicit `--nexttrace-geoip on`
or `off`. `on` permits third-party IP/ASN lookups; `off` keeps numeric hop/RTT
measurements without those lookups. Subsequent tool updates retain this setting
unless explicitly changed. See the diagnostics document for provider details.
Installing `ping` remains a separate host package-management operation.

```bash
sudo python3 agent/app/open_node_agent/service.py policy --network-diagnostics off
sudo python3 agent/app/open_node_agent/service.py policy --network-diagnostics on
sudo python3 agent/app/open_node_agent/service.py status
```

Disabling removes only the additional raw-socket capability. It retains the
NextTrace binary and settings for later re-enabling. TCP latency and log access
remain available; raw-socket probes report permission failures. An identical
request checks the loaded policy without restarting the Agent. This is not a
firewall: hosts permitting unprivileged ICMP through
[`ping_group_range`](https://github.com/torvalds/linux/blob/v6.1/Documentation/networking/ip-sysctl.rst)
may still answer echo probes. The command does not change global sysctls.
Permission or tool changes restart an active Agent and its managed children,
causing a brief service interruption. An intentionally stopped Agent stays
stopped. Neither Agent nor helper boot-enable preferences are changed.

For named installations, pass both global `--root` and `--unit` before `policy`,
`status`, or `recover`, as with other host commands. There is no remote policy
command: the controller cannot grant capabilities, install arbitrary tools,
choose executable paths, or change GeoIP consent.

## Recovery Contract

The command changes only the two recorded systemd capability directives,
the NextTrace config fields when requested, and the owned NextTrace executable.
Other unit settings, node token, Xray/Nginx configuration, command journals,
runtime selections and desired running states are retained. It verifies loaded
capabilities and, for an active Agent, actual non-root process privileges and
fresh authenticated readiness. The capability meanings come from
[Linux UAPI](https://github.com/torvalds/linux/blob/v6.1/include/uapi/linux/capability.h)
and [systemd](https://github.com/systemd/systemd/blob/v252/man/systemd.exec.xml).

Before stopping anything, a root-private directory records the exact previous
and candidate files. The manifest contains only non-secret recovery metadata.
The pending transaction temporarily uses schema 2 so older bootstraps refuse
to reinterpret it as a release switch. Existing remote lifecycle jobs must
finish first; helpers are paused during the update and their recorded source
files are not replaced.

On update/startup failure, recovery restores the previous files, policy and
active/stopped state. If the CLI or host is interrupted, use the same current
bootstrap:

```bash
sudo python3 agent/app/open_node_agent/service.py status
sudo python3 agent/app/open_node_agent/service.py recover
```

Recovery validates snapshot hashes, ownership, installation identity, file
contents and external systemd overrides before overwriting anything. Foreign
edits or unsafe paths stop recovery and retain its marker. Resolve the reported
conflict after inspecting/backing up the affected files; do not delete the
manifest, snapshots, or change schema numbers to bypass recovery. A failed
restart retains the transaction for another attempt after the fault is fixed.

After committing or restoring the policy, the manifest returns to schema 1
before previously active helpers restart. If that restart fails, status reports
`policy_restore_pending: true`. `recover` then retries only helper restoration;
it does not undo the already committed policy or restart the Agent again.
New-bootstrap mutations refuse to proceed until recovery finishes. A crash
before publishing a transaction or after clearing its final marker can leave
an unreferenced root-private `policy-*` snapshot directory; this does not select
another policy. Retain it for inspection rather than deleting pending snapshots.

The verified target is Debian 12 x86-64, managed Xray, and the pinned NextTrace
binary. Other operating systems, architectures, arbitrary external units and
fork-specific runtimes remain outside this evidence.
