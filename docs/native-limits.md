# Native Limits

The independent Agent and optional [free Xray runtime](fork-runtime.md) implement
bandwidth and concurrent-connection limits without activation or license-server
requests. The control plane remains MIT; the runtime extension is MPL-2.0.

## Runtime Setup

Build the pinned compatibility runtime with the included limiter patch and
overlay. `xray open-node-capabilities` reports `limiter: 1` and
`user_auto_speed_rules: 1`, plus `mieru_udp_target: 1` for the compatibility
runtime's UDP target path. These are versioned integer capabilities; an actual
integer equal to one is required. Install that
binary through the host deployment CLI. Managed mode supplies
`OPEN_NODE_LIMITER_DIR=<agent state directory>/limits` to Xray automatically.
Official Xray continues to work for unlimited configurations.

Host-owned systemd services must use the compatible binary, run as the Agent's
service account and explicitly set the same absolute `OPEN_NODE_LIMITER_DIR`.
The directory path must be at most 90 characters to fit a Unix socket path.
The Agent does not modify an external service's environment or ownership.
Its systemd sandbox must also permit writes to that limiter directory. The
native end-to-end smoke covers managed mode; external limiter opt-in needs
separate host-specific verification.

The runtime owns a 0700 directory, a 0600 `policy.json` and a 0600
`control.sock`. No limiter HTTP port is exposed. The Agent verifies the bound
runtime PID, capability, socket ownership and permissions. Invalid policy
files prevent startup. Independently edited policy files block subsequent
updates; stop the owning runtime and reconcile the file before retrying.
Do not edit the file while its runtime is running.

## Semantics

- Rates are bytes per second in the Agent protocol, decimal Mbps in the UI and
  plan API: `1 Mbps = 125000 bytes/second`. Zero means unlimited; a positive
  rate smaller than one byte per second is rejected.
- For each inbound/email pair, the smallest positive node, user or active
  automatic cap applies. `node_limit` is a **per-user ceiling**, not a shared
  aggregate ceiling for the entire inbound.
- One token bucket is shared by that user's concurrent streams and both traffic
  directions. Small bounded bursts are allowed. Limits are not exact packet
  pacing or a guarantee of application throughput.
- The compatibility field `device_limit` counts concurrent dispatched
  connections, not unique IPs, phones or physical devices. `conn_group`
  shares admission quota across credential aliases; absent groups use email.
  The smallest positive limit among group members applies.
- Lowering a connection cap prevents additional connections until the count
  drops below it; it does not arbitrarily select existing connections to kill.
  Rate changes affect existing connections without restarting Xray, including
  authenticated Vision paths.
- User email must match an authentication credential. Inbound-wide limits need
  an email on every credential. Unauthenticated/raw modes cannot identify a
  user and are not a substitute for authenticated admission.
- Removing a user from an existing limiter policy interrupts its current
  streams. Future authentication is controlled by the Xray credential config.
  Remove the credential to revoke access, or keep a user with zero limits to
  lift its caps. Removing the whole policy lifts its limits.

Automatic rules sample combined traffic once per second. `sustained` requires
continuous threshold exceedance for `sustained_seconds`. `burst` counts
completed above-threshold periods of that length within `window_seconds`;
it triggers after `burst_count` occurrences. The first matching rule applies
`limit_mbps` for `limit_duration` seconds without relaxing a stricter static
cap. Expiry restores the static cap. An unchanged policy refresh preserves an
active automatic cap. Inbound-wide rules run before the user's ordered rules.
Changing another user's rules preserves this user's active timer; changing
the user's effective rules resets its timer. Automatic timers and live
counters restart with Xray.

## Operations

Config > Limits reads native state, edits user and per-user inbound ceilings,
manages inbound-wide automatic rules and confirms policy removal. It preserves
per-user rules supplied by plans when saving static or inbound-wide settings.
Save/remove include the
observed revision. A conflicting edit fails and requires a refresh.

- `POST /api/v1/servers/{id}/operations/limiter/status` queues a GET of native
  state through the Agent.
- `POST /api/v1/servers/{id}/operations/limiter` queues a sync policy or
  `action: remove`, with an optional `expected_revision`.
- Native state reports policy revision, runtime PID, connection counts,
  rejection counts, observed speeds and active automatic caps.

The compatibility Agent path is `/api/child/limiter`. An unavailable native
runtime is reported as unavailable, never as an applied policy. An old Agent
without `native_limiter` capability cannot receive limited provisioning
commands. A successful limited batch must return native enforcement
confirmation; an ordinary `success: true` is insufficient.

## Plans And Failure Recovery

Queued plan provisioning sends per-user caps alongside credentials.
[Subscriber overrides](user-limits.md) take precedence over plan values,
including direct-parent and explicit-unlimited handling. Aliases of the same account
and parent inbound share a connection group. Conflicting limits for the same
credential use their smallest positive values.

Plans also carry an ordered `auto_speed_rules` list, edited in their create
and settings forms. Rules bind to each subscriber credential, not every user
of a shared inbound. An empty list disables package rules; explicit zero
static overrides do not disable them. Inbound-wide administrator rules remain
independent and take precedence when both match. Plan changes use the managed
access reconciliation contract and may restart Xray; they are not guaranteed
to be hot updates like the direct limiter operation.

Upgrade both the Agent and the free runtime before enabling package rules.
The Agent advertises `user_auto_speed_rules`, and the runtime must return the
integer capability `user_auto_speed_rules: 1`. An older Agent cannot lease
commands containing per-user rules. A current Agent rejects an older runtime
before writing rules or credentials. Stored rules also block a downgrade to
a runtime that cannot enforce them. Empty per-user rule fields are omitted
from the wire so static-only policies still work with the earlier free core.

The Agent validates the candidate Xray configuration, then persists the native
caps before writing credentials. A config-write/restart failure can therefore
leave the requested caps in place while the credential change fails. Inspect
both runtime state and command results before reconciling. This is deliberately
not a cross-file atomic transaction. Interrupted commands preserve the Agent's
409 replay contract rather than repeating an uncertain mutation.

Existing policy files also prevent switching to an incompatible official
runtime. Remove policies explicitly before such a switch. Unlimited batches
with no existing policies retain ordinary official-Xray compatibility.

Tests and builds run only on the designated VPS. The real limiter smoke uses
an installed non-root Agent, trusted HTTPS/WSS, actual proxy clients and real
traffic; see [testing](testing.md). Mieru UDP target packets over both TCP and
UDP underlays retain the authenticated user context on each association's
single dispatcher link, so the same per-user traffic accounting and native
limiter policies apply.
