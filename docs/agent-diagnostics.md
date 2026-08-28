# Agent Diagnostics

The independent Agent implements domain latency, TCP return-route tracing,
and bounded log viewing/listing/clearing. These operations use the authenticated
command queue over either HTTPS leases or WSS. No activation key is required.
Source builds beginning with Agent 0.2.0 contain this implementation. The
published 0.1.0 artifacts are immutable and do not contain it.

## Latency

The Dashboard accepts DNS names, IPv4, IPv6, and explicit ports. Bare hosts
default to port 443, including HTTP(S) URLs; use an explicit port for port 80.
IPv6 ports require brackets, for example `[::1]:443`. URLs are parsed with
the standard URL parser. Credential-bearing URLs, option-like hostnames,
multicast/unspecified addresses, and out-of-range ports are rejected.

Up to 200 unique targets run with 16 concurrent probes. Each TCP attempt
includes DNS resolution and has the requested 200-10000 ms timeout. TCP
measures connection establishment, not TLS validation or application health.
The result preserves the concrete target (including port) as its history key.

With ICMP fallback enabled, failed TCP attempts may run the host's `ping`
tool. A successful echo is labeled `method: icmp` and preserves `tcp_error`;
it does not prove the TCP port is open. Missing ping, denied permissions,
DNS errors and lost echoes remain failures with separate diagnostic details.
The command's overall deadline still applies. Dashboard/scheduled-task
requests budget the batches and possible fallback; direct API callers should
set `command_timeout_ms` appropriately, up to 300000 ms.

Samples enter the existing FastAPI probe history and cross-node comparisons.
The Dashboard distinguishes TCP/ICMP results, and the Probe view schedules
domain, return-route, and system probes.

## Return Routes

Provide a trusted [NextTrace](https://github.com/nxtrace/NTrace-core/tree/v1.7.1)
binary in the host's private installation input. NextTrace is an external
open-source executable, not copied into the Python package. The tested
Linux x86-64 Tiny release is:

- Version: `v1.7.1`
- Asset: `nexttrace-tiny_linux_amd64`
- SHA-256: `093849f1012b065c29d307b8e47fedec667206829c14e105f83a852f60c628d1`
- [Upstream release](https://github.com/nxtrace/NTrace-core/releases/tag/v1.7.1)

Verify the downloaded binary against this digest before installation. Other
architectures require their corresponding trusted upstream binaries and are
not covered by the current VPS evidence.

```yaml
nexttrace_binary: /root/verified-nexttrace
# False disables third-party IP location/ASN lookups.
nexttrace_geoip: true
```

Add `--network-diagnostics` to the initial
[host installation](agent-deployment.md). This explicit host opt-in adds only
`CAP_NET_RAW` alongside the existing low-port capability. The Agent still
runs under its dedicated non-root account with `NoNewPrivileges=true`.
The installer copies NextTrace into its root-owned runtime directory.
ICMP fallback also needs the host's `ping` package, typically `iputils-ping`.
TCP latency does not require this additional permission.

Existing installations retain their recorded service policy during upgrades.
This release does not provide in-place permission expansion for an existing
service; do not edit its managed unit or purge its state to force an upgrade.
Deploy a separately named diagnostic instance where needed. A host policy
update workflow is still outstanding. Remote commands cannot add capabilities,
install arbitrary tools, select executable paths, or pass shell arguments.

Trace requests contain one to three carrier targets, IPv4/IPv6 selection,
and a 10-45 second budget per target including DNS. NextTrace runs TCP SYN
traces with 30 maximum hops and two attempts per hop. Timeout, cancellation,
and oversized output terminate the owned process group. Numeric hops, RTTs,
ASN path, target reachability, and geolocation are parsed from structured JSON.
The non-root advisory emitted before JSON by NextTrace 1.7.1 is tolerated.

With `nexttrace_geoip: true`, NextTrace uses its LeoMoeAPI provider; queried
hop addresses leave the host. Availability, rate limits and classification
accuracy depend on that external provider. `false` retains real hop/RTT
measurements without external GeoIP requests; ASN/location remain unknown.
No remote command can change the host's provider-consent setting.

An observed AS4809 is labeled CN2, not CN2 GIA. An observed AS23764 is labeled
CTG, not CTG GIA. ASN labels describe observed backbone evidence and do not
prove a paid service tier. Mainland entry requires geolocation evidence;
Hong Kong, Macau and Taiwan are not treated as mainland entry points.
Partial responding routes may have `success: true` and `reached: false`.
An all-timeout trace or absent tool/permission is an explicit failed result.

## Logs

The API only accesses `agent.log`, `xray.log`, `nginx.log` and their
`.1`/`.2` rotations in the private Agent state directory. It never enumerates
or deletes credentials, runtime configuration, command journals, certificates,
system journals, or arbitrary files. Symlinks, hard links, non-regular files
and files owned by a different UID are rejected. Batch clearing reports
per-file failures and partial totals.

Active files are truncated without unlinking the writer's inode. Rotated
files are removed. The CLI writes rotating Agent logs (5 MB, two backups);
known Agent tokens are redacted in those file logs and all API log reads.
This is not a general-purpose scrubber for arbitrary secrets written by
third-party runtimes. Treat log access as an administrator operation.

Log reads are capped at 128 KB and 2000 lines. Missing logs return an empty
result. The Dashboard requires explicit confirmation before log deletion.
