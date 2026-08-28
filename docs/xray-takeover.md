# Xray Multifile Takeover

The independent Agent can consolidate a bound external Xray service's JSON
configuration without activation or a paid runtime. This is an explicit
extension of [external systemd mode](external-systemd.md), not discovery and
modification of arbitrary host processes.

## Host Opt-In

Keep the external mode's dedicated non-root account, trusted unit/binary,
matching live process, scoped polkit rule and supported execution context.
The Agent and Xray must have the same primary account/group. All input files
and writable parents belong to that account. Inputs must be single-link regular
JSON/JSONC files with mode `0600` or `0640`; symlinks, hard links, traversal,
unsupported flags and inherited configuration directories are rejected.

Set this in the private Agent configuration:

```yaml
runtime_mode: systemd
allow_xray_takeover: true
xray_service: open-node-external-xray.service
xray_binary: /usr/local/bin/xray
xray_config: /etc/open-node-external/base.json
```

The existing service can use repeated `-config`/`-c` arguments followed by an
explicit `-confdir`, or a configuration directory alone. `xray_config` must
identify an existing input file that will become the consolidated target.
Directory inputs are read in the core's filename order. At most 128 distinct
files and 2 MiB of combined input are accepted. YAML/TOML input, stdin/URL
configuration and environment-selected directories require host-side migration
before this workflow. An unsupported input is not silently ignored.

When granting the initial scoped permission for such a unit, include
`--allow-takeover`:

```bash
sudo /opt/open-node-external-agent/venv/bin/python -I -m open_node_agent.systemd_access grant \
  --user open-node-xray --service open-node-external-xray.service \
  --xray-binary /usr/local/bin/xray \
  --xray-config /etc/open-node-external/base.json \
  --allow-takeover
```

The grant still allows only start/stop/restart of this exact service. No unit
editing, daemon reload, package replacement, account ownership change or
arbitrary command execution is added. The managed-child installer is separate.

## Preview And Apply

In Config, select the server and open **Takeover external**. The dialog requests
a read-only preview, displays source paths, target, runtime state and the full
source checksum, and requires explicit acknowledgment before applying it.
Changing the source invalidates the preview. Neither preview nor command results
include source configuration, user credentials or backup contents.

The management endpoint is
`POST /api/v1/servers/{server_id}/operations/xray/takeover-external`:

```json
{"preview": true}
```

This queues **GET** `/api/child/external-xray/takeover`. A historical Agent that
does not support preview will reject GET; it cannot mistake the request for its
mutating POST operation. Actual takeover requires:

```json
{"confirm": true, "expected_sha256": "64-character-checksum-from-preview"}
```

The checksum is optional for explicitly confirmed API callers; the UI always
supplies it. Requests without confirmation or preview return 422. The normal
Agent command history reports the eventual result.

The Agent invokes the bound executable's `run -dump`, validates both the original
layout and candidate, and requires a successful native JSON round trip.
[Xray's merge rules](https://xtls.github.io/en/config/features/multiple.html)
include tag replacement, top-level object replacement, and filename-dependent
outbound ordering; they are not reproduced as a generic recursive JSON merge.
Comments and formatting are normalized. The original bytes remain in backups.

After rechecking the binding and all input bytes, the Agent records a private
transaction and stops a running Xray. It writes the merged target and replaces
the other inputs with empty JSON objects. Keeping those filenames lets the
unchanged unit continue to use its explicit arguments and directory. It checks
the resulting native merge again, restarts only a previously running service,
and preserves a stopped service's intent. A repeated, already consolidated
request does not restart Xray.

Subsequent normal config edits operate on the target. Active secondary fragments,
a newly added active matching file or a changed binding block normal writes until
reviewed/consolidated again. Unrelated files and the root-owned unit/binary are
not changed. Other host managers are outside the Agent's lock: do not run them
against the same files concurrently.

## Recovery And Backups

The private `state_dir/xray-takeover.json` journal is persisted before stopping
or writing. Before committing a successful or rolled-back transaction, it
retains a `0600` receipt under
`state_dir/xray-takeover-backups/{backup_id}.json`. These files contain
Base64-encoded original configurations and credentials: Base64 is not encryption.
Protect and back them up as secrets. They are not downloadable from the preview.

Failed activation restores exact source bytes and attempts to restore the
original running state. If startup remains blocked, for example by an occupied
listener, the transaction stays pending and the Agent retries recovery. It stays
connected with an unready runtime rather than reporting a successful takeover.
An Agent killed during stopping, partial writes or activation recovers from the
durable journal after restart.

Recovery checks the same service identity, source paths and expected before/after
bytes. Independent edits are never overwritten. Review the private backups and
host changes, restore the expected binding/files if appropriate, and let recovery
retry. Do not delete the journal to bypass a pending recovery.

The interrupted command itself retains the normal 409 conflict-on-replay
contract. Recovery of files is not a fabricated successful RPC result. Inspect
the recovered runtime and issue a new confirmed request when ready.

## Verification Scope

The [VPS smoke](testing.md#xray-multifile-takeover-smoke) uses real systemd,
polkit, non-root Agents, trusted HTTPS/WSS and official Xray 26.3.27.
Broader OS/runtime versions, cross-account ownership changes, arbitrary wrappers
and embedded-process adoption remain outside this verified workflow.
