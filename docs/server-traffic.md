# Server Traffic Cycles

Server traffic is separate from subscription-user quotas. No license, paid
service, or third-party account is needed. The dashboard's Server traffic
section reads and edits these settings:

- Source: Xray nodes or system network.
- Direction: upload, download, both, or the larger direction.
- Quota: bytes in the API, GiB in the dashboard; zero means unlimited.
- Monthly reset day: zero disables automatic resets; 1-31 selects a UTC day.
  Short months use their last day, including leap years.

## Accounting

Xray accounting follows the original node-traffic scope: inbound and outbound
counter totals are added together. They represent both proxy legs, not a NIC
bill; user counters are not added a second time. Use system network accounting
for host-network consumption. User-plan multipliers never affect either source.

Both sources have independent durable totals and cycle baselines. Source or
direction changes preserve them. A reset sets both source baselines to their
current totals; changing sources afterward does not bring a previous cycle back.
For `max`, the larger **cycle direction total** is used, not a sum of per-sample
maxima. Changing the quota does not reset consumption.

Xray deltas are computed per counter, so one restarted inbound cannot hide its
new traffic behind an increasing counter on another inbound. A lower counter
starts a new observed counter interval. An absent stats report leaves the
baseline untouched; an explicit empty stats snapshot retires those counters.
Initial Xray counters contribute to cumulative consumption, but cannot be dated
and therefore do not appear in daily history. The first report of a previously
unobserved source after a reset establishes its cycle baseline.

System counters begin with a baseline, excluding consumption before enrollment.
Host reboot, or either NIC total moving backward, establishes a new baseline
without charging that interval, matching the original system-meter behavior.
Upload means host TX and download means host RX for this source. Public raw
`cumulative_up/down` fields still describe the latest host counters.

Duplicate and older reports do not book a second delta. Each source has its own
timestamp, so a missing source does not discard a later report from that source.
Ingestion, reset markers, and baseline updates use serialized database
transactions. A pre-reset report arriving late updates history without charging
the new cycle. Daily history uses UTC and both directions of the selected
source; resetting does not erase it.

These are observed counters, not an audited provider invoice. Unreported bytes
lost during a restart cannot be recovered. Without an Xray counter epoch in the
telemetry contract, a restart that already exceeds every previous counter before
the next observation cannot be distinguished from continuous traffic. Intervals
spanning a billing boundary are assigned when reported, without speculative
time-based prorating. Agent clocks should be synchronized.

## Reset Scheduling

The backend lifespan worker checks every 60 seconds. Set
`OPEN_NODE_SERVER_TRAFFIC_POLL_SECONDS` to 1-300 to change that interval. It runs
independently of certificate issuance and user access enforcement and retries
after database failures.

Scheduled resets become eligible at 00:05 UTC on the effective billing day.
An overdue reset is applied on the next scan during that month's reset window.
A durable marker prevents repeated scans or multiple backend workers from
resetting twice. Creation and manual reset on the billing day also satisfy that
day's reset. Offline servers do not need an Agent command to reset their ledger.

The server quota is a displayed accounting threshold. It does not stop Xray,
disconnect users, change subscription quotas, or issue runtime commands. User
quota enforcement is documented separately in [subscription access](subscription-access.md).

## API And Upgrade

All endpoints require an administrator session; writes require CSRF protection:

- `GET /api/v1/servers/{server_id}/traffic`: settings, cycle and cumulative
  counters, last report/reset, and next scheduled reset.
- `PUT /api/v1/servers/{server_id}/traffic`: complete settings update containing
  `traffic_limit`, `traffic_reset_day`, `traffic_source`, `traffic_stats_mode`.
- `POST /api/v1/servers/{server_id}/traffic/reset`: start a new cycle now.

The public probe uses `traffic_used` for the configured billing direction.
`traffic_used_up`, `traffic_used_down`, and `traffic_used_total` retain the two
cycle directions and their sum. Consumers must prefer `traffic_used` for quotas.

SQLite upgrades add the schedule fields and replay existing telemetry into the
new source and daily ledgers once, atomically. Original telemetry is retained.
Subsequent startup does not replay or overwrite saved cycle baselines. Automatic
reset remains disabled until configured; existing source/direction settings stay
intact. An upgrade may correct previously displayed usage because older previews
showed only the latest inbound snapshot and ignored the selected source/mode.

## VPS Verification

`backend/tests/test_server_traffic.py` covers source/mode semantics, duplicate and
out-of-order samples, per-counter resets, host reboot/drop baselines, source
switching, UTC/leap-year boundaries, manual reset, concurrent ingestion/reset,
schema upgrades, worker retries, authentication, and input validation.

`scripts/vps/smoke-server-traffic.py` runs with the existing isolated VPS service
fixtures. It installs a dedicated non-root Agent, sends actual Xray proxy bytes,
checks manual and automatic reset, preserves totals through Xray/Agent restart,
and exercises dashboard save/reset/cancel at desktop and 390/320px widths. Pass
`--transport websocket` or `--transport http`; both use the fixture's trusted
TLS gateway. All tests and builds run on the configured VPS, never locally.
