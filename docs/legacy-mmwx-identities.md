# Legacy MMWX Identity Migration

Open Node can import subscriber identities from the active `miaomiaowuX` main-line
SQLite schema without a license key or activation service. The migration preserves
the current bcrypt password hash, TOTP seed and unused recovery-code hashes. It
preserves the long subscription token and user short codes only when
`OPEN_NODE_SHORT_LINKS_ENABLED=true`; the secure default replaces those unverified
bearers once. When the active-main package and subscription-file tables are present,
it also exports package assignments, assignment dates/reset state, subscription
profiles and legacy `/x` codes.

This workflow does not use the catalog JSON importer. It has a separate guarded API
and administrator dialog because its input contains login and bearer secrets.

## Before Export

Back up both databases. Export from a stopped MMWX service or a consistent SQLite
backup so the user and token tables describe the same point in time. Configure and
back up `OPEN_NODE_SUBSCRIBER_TOTP_KEY` before importing any TOTP-enabled account;
see [subscriber accounts](subscriber-accounts.md#authenticator-key). Losing that key
later prevents authenticator verification.

Run the standard-library exporter on the MMWX host:

```bash
python scripts/migrations/export-mmwx-identities.py \
  /private/path/mmwx.db \
  /private/path/mmwx-identities.json
```

The source database is opened read-only. The exporter verifies the required `users`
and `user_tokens` tables, tolerates optional profile/security and package/file tables
from older schemas, writes atomically and sets the result to mode `0600`. It refuses
to replace an existing output unless `--force` is supplied. An old schema without a
generated short-code column receives a deterministic migration-only generated code.
Those values are preserved only in short-link compatibility mode.

The JSON contains password hashes, authenticator seeds and subscription bearer
tokens. Do not print it, email it, commit it or place it in a web-served directory.
Transfer it over an authenticated encrypted channel and delete every temporary copy
after verification.

## Preview And Import

In **Subscriptions > Catalog import/export**, open **MMWX identities** and select the
JSON. Map every in-use legacy package to an existing Open Node plan, then run
**Preview**. The response reports users, package mappings, profiles, blockers,
warnings and a revision, but never returns source secrets. Enter the exact displayed
user count to enable **Import**. A target user already assigned to a different plan
blocks the import instead of being moved silently.

The default preserves an existing Open Node login account or subscription-token row
and imports only missing state. **Replace existing logins and links** replaces both
when present, replaces previously imported profiles and revokes that subscriber's
current sessions and login challenges. Package mappings apply the source package
start, expiry and reset fields to the selected Open Node plan.

Import rechecks the source, target revision, confirmation count, TOTP key and every
subscription-key collision inside one coordinated transaction. A stale preview,
collision or invalid account rolls back the complete batch. Routes are administrator
only, use normal CSRF protection and return `Cache-Control: no-store` with sanitized
validation errors:

- `POST /api/v1/migrations/mmwx/identities/preview`
- `POST /api/v1/migrations/mmwx/identities/import`

## Resulting Identity

- Missing product users are created as ordinary subscribers and receive the plan
  selected for their source package. A source `admin` is deliberately demoted: MMWX
  roles never grant Open Node controller access. Existing user names, email/display
  data and roles remain unchanged.
- MMWX bcrypt hashes are accepted only for the imported subscriber account. The
  first successful password check atomically upgrades the stored hash to Argon2id.
- Enabled TOTP seeds are encrypted with the account-bound Fernet scheme. Imported
  SHA-256 recovery hashes remain one-use. Unlike the old recovery handler, using one
  does not disable TOTP.
- Old MMWX recovery codes contain only 32 bits of randomness. After the first login,
  replace them from **Account > Security > New recovery codes** to receive Open
  Node's 80-bit account-bound codes.
- MMWX sessions, API tokens, Telegram bindings, controller privileges and password
  reset state are not imported.
- With `OPEN_NODE_SHORT_LINKS_ENABLED=true`, the long token, generated short code and
  custom short code resolve through Open Node's renderer after a plan is assigned.
  With the secure default, import generates a new 256-bit long bearer, replaces the
  generated code and clears the custom alias; redistribute the new long URL.
  Replacing existing links can invalidate links already issued by Open Node.
- Plan assignment creates the desired catalog state. Use the normal plan/access sync
  after import to provision Open Node credentials onto managed Agents; the identity
  transaction does not execute remote Agent commands.

## Subscription Files And `/x`

Each imported MMWX subscription file becomes a named Open Node subscription profile
with its original owner, user assignments, sort order, expiry and short codes. A
subscriber can switch between assigned profiles in `/account`; an administrator can
edit profile users, Open Node node selection, the plan's global Clash template and enabled state
from Subscriptions. Profiles remain views over each assigned user's current plan,
quota, runtime credentials and availability checks. They do not grant a second plan.

When short-link compatibility mode is enabled, `GET /x/{code}` preserves active-main
lookup order: a direct file code renders for the file owner, then combined codes are
split right-to-left into file+user or package+user. User generated and custom codes
are accepted. Package codes act as compatibility selectors and render the matched
user's current mapped plan. The old `t` client selector and Open Node's
`format`/`node_id` parameters are supported. In the secure default mode `/x/...`
returns 404.

Generated profiles use current plan nodes by default. The exporter retains old node
IDs/tags for audit, but cannot infer their Open Node catalog IDs; administrators can
select the intended Open Node nodes after import. Legacy raw uploaded content is
imported disabled. Old template files, custom rules and override scripts are recorded
as warnings and are never executed; rebuild them with managed Open Node nodes and
templates before enabling the profile. Private routed-node ownership and unrelated
reverse-proxy paths remain outside this migration.

Keep the original MMWX database and a pre-import Open Node backup until users have
logged in, TOTP has been checked, new recovery codes have been issued and every
required subscription profile has been checked and normal managed access sync has
completed.

## Verification

`backend/tests/test_legacy_mmwx.py` covers transactional preview/import, explicit
package mappings, profile assignments and edits, direct/combined resolution, stale
state, collisions, replacement, session revocation, TOTP encryption, recovery
consumption, bcrypt upgrade, role isolation and response redaction.
`backend/tests/test_export_mmwx_identities.py` covers real SQLite export, exact fields,
mode `0600`, overwrite refusal and missing schema.

On the designated VPS, `scripts/vps/smoke-legacy-mmwx.py` creates a real legacy
database, exports it, uploads it through the Vue dialog, imports it, logs in with the
old password and TOTP, consumes an old recovery code and forwards traffic through
Xray using the imported long/generated/custom keys plus direct file, file+user and
package+user `/x` links. The subscriber browser selects an imported profile before a
real VLESS forwarding check. Browser bounds and screenshots cover 1440, 390 and 320
pixel widths.
