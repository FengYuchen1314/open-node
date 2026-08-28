# Subscriber Limit Overrides

Subscriptions > Edit user > Limits manages each subscriber's traffic quota,
default bandwidth and concurrent-connection caps, and per-node bandwidth and
connection overrides. This requires administrator authentication and CSRF
protection. All of these controls are free, with no license or activation gate.
Subscribers can view their own resolved limits at `/account` but cannot edit them.

## Inheritance

Each account-level value has three states: `null` inherits the plan, `0` is
explicitly unlimited, and a positive value supplies a cap. A missing node-map
entry inherits; a present entry with value `0` is explicitly unlimited.

Bandwidth and connection limits resolve independently, in this order:

1. The user's override for the selected node.
2. The user's override for that node's direct parent.
3. The user's account-wide override.
4. The plan's override for the selected node.
5. The plan's override for the direct parent.
6. The plan default, or unlimited when no plan exists.

Only one parent hop is consulted. A routed node's target is not its parent for
limits. Existing catalog nodes outside the current plan may have saved
overrides, which take effect when the node is included in a later assignment.
An override does not grant node access or create a plan assignment.

Traffic quota uses the user's value when present and otherwise the plan quota.
The API's `traffic_limit_gb` uses GiB, matching plan storage: one GiB is
`1024**3` bytes. It is persisted as an integer byte count. Positive quotas
smaller than one byte are rejected. Values must stay within the browser's safe
integer byte range. Non-finite, negative and malformed values are rejected.

Bandwidth uses decimal Mbps: `1 Mbps = 125000 bytes/second`. Positive values
smaller than one byte/second are rejected. `device_limit` retains its protocol
name but counts **concurrent connections**, not unique devices or IP addresses.
See [native-limits.md](native-limits.md) for shared buckets and burst behavior.

Aliases sharing an inbound and credential cannot receive independent caps.
Their smallest positive resolved cap applies; zero lifts that alias's override
but does not remove another alias's positive cap. Saved-node and subscriber
views report this combined value with the `shared` source where appropriate.
Credential aliases in the same account and inbound also share connection
admission. Native inbound ceilings and automatic policies may impose stricter
caps than the subscriber's catalog settings.

## Application And Recovery

The same resolver feeds assignment previews, persistent managed-access intent,
public quota checks and subscription headers, and subscriber displays.
Edits preserve credentials, subscription tokens, assignment dates, reset
preferences and all already charged usage. Plan edits preserve user overrides.
Changing a cap does not reset traffic or extend an expired plan.

Managed users receive updated commands through the existing Agent queue. A
successful save is not proof of remote application: inspect Agent status.
Unsupported Agents cannot confirm native limits. An offline runtime may keep
its previous access until the Agent reconnects and applies the new intent.
Unsent superseded access commands and legacy provisioning batches with stale
limits cannot overwrite the current intent. Already-started work drains before
the latest state is reconciled.

Exhausting a quota blocks subscription downloads and withdraws tracked managed
credentials. Saving a newly exhausted quota also records withdrawal for stored
preview credentials. Speed/connection changes alone do not enroll preview-only
users; their settings view warns that managed provisioning is still needed.
Metadata-only saves with unchanged overrides do not enroll them either.

Raising the quota, selecting unlimited, or resetting usage can restore managed
access using the existing identities, provided the user, plan and node remain
available. Unlimited does not bypass disablement, expiration or removal. These
changes do not revoke subscriber login sessions, so an exhausted user can still
inspect their own account status.

Catalog-managed updates use the existing subscription-access transaction,
including Xray validation, limiter provisioning and a runtime restart. They can
disconnect active clients. This differs from direct native-policy edits, which
can update rates in place without restarting Xray.

## API And Upgrade

`GET /api/v1/users/{username}/settings` returns stored `user.limit_overrides`,
resolved `limits`, and a revision. The revision covers user settings, the plan,
relevant nodes and credential bindings; ordinary telemetry does not invalidate
it. `PUT` accepts the existing profile fields and an optional nested object:

```json
{
  "limit_overrides": {
    "traffic_limit_gb": 200,
    "speed_limit_mbps": null,
    "device_limit": 4,
    "node_speed_limits": {
      "00000000-0000-4000-8000-000000000001": 0
    },
    "node_device_limits": {}
  }
}
```

This object replaces all five override fields. Omitting the object, or setting
the whole object to `null`, preserves existing overrides for older clients.
An empty object clears all overrides. The normal revision and runtime-restart
acknowledgment are still required. Invalid node references roll back the whole
profile update.

Names containing `/`, or equal to `.` or `..`, use query aliases, for example
`GET/PUT /api/v1/user-settings?username=alice%2Fplan` and
`POST /api/v1/user-remove?username=alice%2Fplan`. The subscription APIs likewise
provide `/user-access`, `/user-access/sync`, `/user-active`,
`/user-subscription-token`, `/user-subscription-token/reset`, `/user-credentials`,
`/user-traffic`, `/user-traffic/reset`, `/user-quota`, `/user-subscription-preview`,
`/user-plan`, `/user-plan/removal` and `/user-plan/remove`, all with a required
`username` query parameter. The frontend selects these aliases automatically.
Existing simple-name paths remain unchanged; usernames never consume operation
path segments.

SQLite upgrades add three nullable scalar columns and two JSON maps to
`product_users`, with inherited defaults for existing users. Back up the
database before upgrading. Older versions do not enforce these fields, so
application rollback needs the corresponding database backup and runtime review.

Catalog export/import preserves the nested overrides, mapping node UUIDs to
catalog node names and back. Referenced names must be unique and present in the
imported catalog. Missing or ambiguous references fail atomically. Older
catalogs with no override object preserve existing values; `{}` clears them.
Node/server removal prunes only references to removed nodes, retaining account
defaults and unrelated node overrides. User removal deletes its overrides with
the profile; same-name recreation starts with inherited defaults.

## Verification

`backend/tests/test_user_limits.py` covers precedence and explicit zero, parent
inheritance, independent cap resolution, shared aliases, unchanged identities
and usage, quota headers/withdrawal/reset, stale revisions, invalid input,
preview-only behavior, in-flight and stale commands, unavailable capabilities,
catalog rollback/remapping, schema upgrades, query-name isolation and subscriber
authorization. Server-removal tests also cover override pruning.

On the designated VPS, run `scripts/vps/smoke-user-limits.py` with the current
wheel, free Xray binary, Nginx and built frontend. It tests real bandwidth,
connection denial, explicit unlimited, restored plan defaults, persisted caps
after Agent restart, offline quota withdrawal and real forwarding after
restoration. Browser coverage includes revision conflicts, numeric validation,
subscriber visibility and desktop/mobile/narrow layouts. Run separately with
`--transport websocket` and `--transport http`.

Both transport runs passed on the designated VPS, with 725 backend tests,
522 Agent tests, 153 frontend tests and a production build. Each run measured
about two seconds for a 64 KiB echo at the 0.5 Mbps user cap; the explicit
unlimited override completed the same transfer in under one second. Actual
second-connection denial, quota withdrawal/restoration and restart persistence
were checked independently of displayed status.

This does not implement custom short codes, private routed-node ownership or
plan-bound automatic-speed rules. Those remain separate migration work.
