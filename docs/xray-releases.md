# Managed Xray Releases

The independent Open Node Agent can install, upgrade and roll back official
Xray packages without activation or a licensing service. This controls the
Agent-owned `runtime_mode: managed` child. An external systemd Xray service is
not replaced by these commands.

## Select A Release

In the Overview command workspace, select the target server and open
**Install Xray**. Choose the version, optional archive SHA-256 and
running/stopped preference. The dialog retains the selected server for that
request. **Xray release** returns the selected version, checksum, enabled state
and whether an earlier selection is available. **Roll back Xray** requires
confirmation and returns to that selection, retaining the current running
preference.

The default is `v26.3.27`. The Agent includes archive checksums for that version
and `v26.2.6` on Linux amd64 and arm64; leave SHA-256 empty to use those pins.
For another version, provide its complete lowercase archive SHA-256 from a
trusted official release. A supplied checksum always takes precedence, so an
incorrect pin fails rather than falling back to an unverified package. The
version must be an explicit `vYEAR.MONTH.DAY`-style tag, not `latest` or a URL.
An amd64 checksum must not be used for the arm64 archive.

Packages come only from the matching tag in
[XTLS/Xray-core releases](https://github.com/XTLS/Xray-core/releases). Downloads
use HTTPS with normal certificate verification, bounded size and redirects
restricted to GitHub's release hosts. Neither a shell installer nor an
operator-supplied executable URL is evaluated. The archive checksum, allowed
regular-file entries, size limits and reported binary version are checked
before selection. The archive's license and accompanying data files remain
with the extracted release.

The authenticated control-plane operation is:

```http
POST /api/v1/servers/{server_id}/operations/xray/install
Content-Type: application/json

{"version":"v26.3.27","start":true}
```

It queues `/api/child/xray/install-stream` with a five-minute command deadline.
The independent Agent also accepts the non-stream `/api/child/xray/install`.
Omitting the request body preserves the default pinned version. Omit `start`
to keep the persisted running preference; an explicitly stopped runtime stays
stopped unless `start: true` is requested. The release-status, rollback and
remove operations use `/operations/xray/release`, `/operations/xray/rollback`
and `/operations/xray/remove` respectively. These are queued operations, not
synchronous assertions that a node changed successfully; inspect the command
result and fresh scan afterward.

## Files And Permissions

The original `xray_binary` supplied during host installation is retained as
the bootstrap binary. It is never overwritten or removed by remote commands.
The systemd installer continues to keep the Agent package, installation
metadata and bootstrap runtime root-owned. The Agent does not gain root
privileges to update Xray.

Downloaded releases live under `state_dir/xray-releases/{archive_sha256}` and
are owned by the existing Agent account. A private release manifest records
the version and file digests. `state_dir/xray-release.json` records the active
and previous selections. File identity and integrity are verified when a
cached selection is loaded; symlinks, hard links and modified files are not
accepted. Do not place other applications' files in these directories.

The selected binary is used for configuration validation, process launch,
version reporting and statistics queries. Existing Xray JSON and user
credentials are preserved. Only when the configured JSON file is missing does
initial installation create a minimal empty-inbound/direct-outbound config.
Certificates and other external paths must remain readable by the service
account. Archive data files sit beside the selected executable.

**Remove Xray** is data-preserving runtime removal: it stops the owned child
and persists a disabled selection so monitoring and Agent restarts do not
start the bootstrap binary again. It retains configuration, downloaded release
cache and the preceding selection for rollback/reinstallation. Reinstall with
`start: true` or subsequently start the enabled runtime to resume forwarding.
It does not uninstall unrelated host services. The host Agent deployment CLI's
explicit `uninstall --purge` removes the retained owned installation and cache.

## Failure And Recovery

The candidate must validate the current configuration before the running child
is stopped. Download, checksum or configuration failures leave that child
alone. A successful switch includes a bounded startup observation; this is
not a substitute for checking actual node traffic.

Before switching, a private undo record captures the prior selection,
configuration when needed, and service intentions. A failed start or command
timeout restores that state and restarts the prior child if it was running.
Recovery failures are reported for operator review, not marked successful.
On an abrupt Agent restart, unfinished file/selection transactions are
recovered before monitoring can start Xray. Completed request IDs are still
deduplicated; a command interrupted before its result was persisted returns
an unresolved-execution error when redelivered instead of repeating the
upgrade. Compare the release-status result, scan and forwarding before
issuing a fresh command.

Repeating an already-selected release with unchanged running state is a
no-op and preserves the preceding rollback selection. Changing the running
preference does not discard that earlier version either.

Agent wheel upgrades validate the selected runtime, not just the old bootstrap
binary, and refuse to proceed during an unfinished Xray switch. A removed
runtime does not need to pass a start check. Older Agent packages that cannot
read release-selection state cannot be rolled back over it silently; restore
an appropriate pre-upgrade backup or use a compatible package.

## Coverage Boundaries

The VPS smoke uses two official amd64 releases, installed non-root systemd
Agents, both native transports and real VLESS traffic. Deterministic port
conflict and interruption cases use a clearly marked fixture-only Agent wheel;
the Xray binaries remain unchanged. The ordinary package is exercised before
those faults and restored afterward. Browser checks cover version/checksum
requests and confirmed rollback on desktop and mobile.

Linux arm64 pins are included, but execution on arm64, external systemd
runtime control, fork-only protocols and arbitrary future release/config
compatibility need their own verification. This is Xray package management,
not a remote Agent self-upgrade/uninstall handler. See
[testing.md](testing.md) and [migration-map.md](migration-map.md).
