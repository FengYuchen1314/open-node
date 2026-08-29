# Custom Subscription Short Codes

Operators can edit a user's short code from the link-edit action in the
Subscriptions user list. Subscribers can edit their own code from the
Subscription section at `/account`. The Full/Short selector controls which
link is copied or downloaded. All formats and node-selection parameters keep
their existing behavior. No license or activation is required.

## Identity And Lifetime

Each subscriber retains a random long token and a generated short code. An
optional custom short code becomes the displayed short URL; it does not
replace either original key. The generated short URL keeps working while a
custom code is present. Clearing the custom field restores that generated
code in the interface.

Replacing or clearing a custom code stops that custom URL from resolving.
The existing Reset links operation rotates the long token and generated code
and clears the custom code in the same transaction. This deliberately makes
all previously issued links stop working, including a custom link. Short-code
editing is not a reset: it preserves the long token, generated code, node
credentials, assignment dates, quota settings and charged traffic.

These operations do not queue Agent provisioning or restart Xray. Downloaded
client configurations remain usable until their runtime access is revoked
through the normal user/plan/node controls. A link reset does not revoke
already-downloaded node credentials.

Links are bearer credentials: anyone who knows a working link can download
its configuration. A custom code is guessable and is not a password. Avoid
publishing links unintentionally. Released custom codes can be claimed again;
update distributed links before releasing a code that clients still use.

## Validation And Ownership

- Custom values contain 2-16 ASCII letters, digits, underscores or hyphens.
  Surrounding whitespace is trimmed; an empty string clears the custom value.
- Reserved route/system names are rejected, case-insensitively. Other users'
  existing usernames, generated codes, custom codes and legacy token values
  cannot be claimed as custom codes, including case variants.
- Download lookup remains case-sensitive. The spelling returned by the API
  is the spelling to use in a URL. The uniqueness check is stricter so two
  users cannot register confusing case variants.
- Generated-key allocation also checks the custom namespace. A hidden
  generated code remains reserved even when its owner has a custom code.
- Saves require the revision from the current token response. A stale save
  returns 409 without changing any link. An unchanged value is idempotent.
- SQLite updates are serialized across controllers; a database unique index
  additionally protects custom values against case-variant duplicates.

Operators use their existing administrator session and CSRF protection.
Subscriber edits use the isolated subscriber session, origin/CSRF checks,
current password and an authenticator or recovery code when TOTP is enabled.
The request cannot name a different subscriber. Existing security-attempt
limits apply. A failed revision/collision check does not consume a recovery
code. Revoked sessions cannot edit links.

Disabled, expired, quota-exhausted, unassigned and removing users do not gain
access through a custom URL. The same subscription renderer and availability
checks handle all three key forms. Pending removal prevents edits; completed
removal deletes the token record, including the custom code.

## API And Upgrade

Existing token GET/POST/reset responses add:

- `generated_short_code`: the unchanged automatic code.
- `custom_short_code`: the saved custom code, or `null`.
- `revision`: the token-state revision used by edits.

`short_code` and `short_url` describe the effective code, custom when present.
`token` and `subscription_url` continue to describe the long link.

Administrator requests use
`PUT /api/v1/users/{username}/subscription-short-code` or the query alias
`PUT /api/v1/user-subscription-short-code?username=...` for names containing
slashes or equal to `.` or `..`:

```json
{
  "custom_short_code": "Reader_Link",
  "expected_revision": "<revision from the current token response>"
}
```

Subscriber requests use `PUT /api/v1/account/subscription-short-code` with
the same fields plus `password` and, when required, `code`. Responses use the
existing `{ "subscription": ..., "license_required": false }` envelope and
`Cache-Control: no-store`. Validation errors return 422, conflicts return 409,
and existing session/CSRF/proof failure behavior is unchanged.

SQLite upgrades add a nullable `custom_short_code` column, a unique
case-insensitive index and an exact-lookup index to `product_user_subscription_tokens`.
The lookup index keeps all three public key forms off a table scan. Existing long
and generated keys are not rotated. Database backups preserve the custom
codes; subscription catalog export intentionally excludes bearer links, and
catalog imports for existing users leave their token records unchanged.
Back up the database before upgrading or rolling back.

The [legacy identity importer](legacy-mmwx-identities.md) preserves a user's long,
generated and custom key values; those keys resolve through Open Node's renderer
after plan assignment. This is still not an implementation of the old MMWX
`/x/...` combined file/plan/user URL layout. File-to-plan mapping, combined-path
compatibility and private-node ownership need separate migration; see
[migration-map.md](migration-map.md).

## Verification

`backend/tests/test_subscription_links.py` covers editable/cleared codes,
original-key preservation, all client formats, availability restrictions,
case conflicts, legacy-token collisions, stale revisions, concurrent claims,
schema upgrades, restart persistence and both authentication realms.

On the designated VPS, `scripts/vps/smoke-subscription-links.py` installs a
real non-root Agent, exercises operator and subscriber browser edits, downloads
an Xray configuration through the custom URL and verifies real forwarding.
It also checks password/TOTP proof, collision recovery, reset of every old
link, unchanged runtime PID/credentials and another user's forwarding.
Run with both `--transport websocket` and `--transport http`. Screenshots cover
1440px, 390px and 320px layouts. All tests and builds run on the VPS.

Both final transport runs passed. Full regression covered 765 backend tests,
522 Agent tests and 177 frontend tests plus the production build; the final
lookup-index addition then passed 84 focused backend tests, including its new
query-plan and upgrade checks. The query plan uses all three key indexes
instead of scanning the subscription-token table.
