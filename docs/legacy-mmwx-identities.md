# Legacy MMWX Identity Migration

Open Node can import subscriber identities from the active `miaomiaowuX` main-line
SQLite schema without a license key or activation service. The migration preserves
the current bcrypt password hash, TOTP seed, unused recovery-code hashes, long
subscription token, generated user short code and custom user short code.

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

The source database is opened read-only. The exporter verifies the `users` and
`user_tokens` tables, tolerates optional profile/security columns from older schemas,
writes atomically and sets the result to mode `0600`. It refuses to replace an
existing output unless `--force` is supplied. An old schema without a generated
short-code column receives a deterministic migration-only generated code; its long
token is still preserved.

The JSON contains password hashes, authenticator seeds and subscription bearer
tokens. Do not print it, email it, commit it or place it in a web-served directory.
Transfer it over an authenticated encrypted channel and delete every temporary copy
after verification.

## Preview And Import

In **Subscriptions > Catalog import/export**, open **MMWX identities**, select the
JSON and run **Preview**. The response reports counts, blockers, warnings and a
revision, but never returns source secrets. Enter the exact displayed user count to
enable **Import**.

The default preserves an existing Open Node login account or subscription-token row
and imports only missing state. **Replace existing logins and links** replaces both
when present and revokes that subscriber's current sessions and login challenges.
Existing Open Node profile metadata and plan assignments are not overwritten.

Import rechecks the source, target revision, confirmation count, TOTP key and every
subscription-key collision inside one coordinated transaction. A stale preview,
collision or invalid account rolls back the complete batch. Routes are administrator
only, use normal CSRF protection and return `Cache-Control: no-store` with sanitized
validation errors:

- `POST /api/v1/migrations/mmwx/identities/preview`
- `POST /api/v1/migrations/mmwx/identities/import`

## Resulting Identity

- Missing product users are created as ordinary subscribers with no assigned plan.
  A source `admin` is deliberately demoted: MMWX roles never grant Open Node
  controller access. Existing user names, email/display data, roles and assignments
  remain unchanged.
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
- The long token, generated short code and custom short code resolve through Open
  Node's `/api/v1/subscribe/{key}` renderer after a plan is assigned. Replacing
  existing links can invalidate links already issued by Open Node.

## Deliberate Boundary

MMWX can assign multiple subscription files to one user and exposes combined
`/x/<file-short-code><user-short-code>` links. Open Node currently assigns one plan
to a user, so this importer does not guess a file-to-plan mapping and does not claim
those combined URLs. Subscription files, plan assignment, private routed-node
ownership and reverse-proxy path compatibility remain separate migration work.

Keep the original MMWX database and a pre-import Open Node backup until users have
logged in, TOTP has been checked, new recovery codes have been issued and every
required subscription file has an explicit Open Node plan mapping.

## Verification

`backend/tests/test_legacy_mmwx.py` covers transactional preview/import, stale state,
collisions, replacement, session revocation, TOTP encryption, recovery consumption,
bcrypt upgrade, role isolation and response redaction.
`backend/tests/test_export_mmwx_identities.py` covers real SQLite export, exact fields,
mode `0600`, overwrite refusal and missing schema.

On the designated VPS, `scripts/vps/smoke-legacy-mmwx.py` creates a real legacy
database, exports it, uploads it through the Vue dialog, imports it, logs in with the
old password and TOTP, consumes an old recovery code and forwards traffic through
Xray using the imported long, generated and custom keys. Browser bounds and
screenshots cover 1440, 390 and 320 pixel widths.
