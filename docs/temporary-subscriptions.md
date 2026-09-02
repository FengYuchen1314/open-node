# Temporary Subscription Links

Administrators can share a subscriber's current nodes through a short-lived,
download-limited URL. Open the subscriber in **Subscriptions**, select **Share**,
choose nodes from the subscriber's current plan, then set a label, expiry and
download count. Creation, listing, copying and revocation require an administrator
session. No license, activation or payment service is involved.

## Lifetime And Access

Temporary links are random bearer credentials generated with 24 bytes of
entropy. They are stored in SQLite, survive controller restarts and expire after
60 to 3600 seconds. A link permits 1 to 100 successful downloads. Failed format
or node selections do not consume a download. The counter update is serialized,
so concurrent requests cannot exceed the configured limit.

Expiry, exhaustion and administrator revocation stop future subscription
downloads. They do **not** revoke credentials already downloaded into a client.
Use the existing subscriber disablement, plan/node changes, quota controls or
managed-access withdrawal when already issued runtime access must stop.

Every download rechecks the source subscriber's active state, plan dates, quota,
current plan membership, node availability, templates and credentials. Later
subscriber or plan changes therefore affect the next download immediately.
Removing the source subscriber deletes its temporary links.

## Public Rendering

The public endpoint is `/t/{code}`. It supports the same eleven formats as a normal
subscription:

- `clash` (default)
- `sing-box`
- `xray`
- `uri-list`
- `base64`
- `loon`
- `quantumult-x`
- `shadowrocket`
- `stash`
- `surfboard`
- `egern`

Use `?format=xray&node_id={managed-node-uuid}` to select one allowed node. The
selected node must be in both the share and the subscriber's current plan.
Unsupported or unavailable selections return 404 without consuming access.
Responses use `Cache-Control: no-store` and intentionally omit
`subscription-userinfo`, so a shared URL does not disclose the source user's
traffic quota.

Temporary URLs have no user-agent restriction. Anyone with the URL can consume
its remaining downloads, so distribute it as a secret and revoke it when it is
no longer needed. Expired and exhausted records remain visible to administrators
until deleted.

## API And Upgrade

Administrator endpoints are:

- `GET /api/v1/temporary-subscriptions`
- `POST /api/v1/temporary-subscriptions`
- `DELETE /api/v1/temporary-subscriptions/{id}`

Creation accepts `username`, `label`, `node_ids`, `max_access` and
`expires_in_seconds`. Nodes must already belong to the subscriber's current
plan. Validation errors return 422, missing nodes return 404 and unavailable
subscriber/plan selections return 409.

SQLite startup migration adds the `temporary_subscriptions` table and indexes.
The change is additive; existing users, plans, credentials and subscription
links are not changed. Back up the database before upgrade or rollback.

## Verification

`backend/tests/test_temporary_subscriptions.py` covers restart persistence,
expiry, plan membership, six-format rendering helpers, successful-download
counting and concurrent exhaustion. Frontend service tests cover authenticated
list/create/delete requests.

On the designated VPS, `scripts/vps/smoke-subscription-links.py` creates a share
through the administrator UI, copies it, downloads Xray and URI-list formats,
proves real VLESS forwarding, verifies exhaustion and revokes the record. The
browser run checks 1440px, 390px and 320px layouts.
