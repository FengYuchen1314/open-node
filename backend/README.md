# Open Node Backend

FastAPI backend package for the Open Node MMWX refactor.

The backend provides server/Agent inventory, queued runtime operations,
subscriptions, public probe data, and an explicit no-activation-license contract.
Management APIs require a local administrator session. Create an account with
`open-node-admin create` before signing in; there is no default password.

See [administrator access](../docs/administrator-access.md) for database
configuration, account recovery, HTTPS deployment requirements, and API access.

[Certificate management](../docs/certificates.md) covers the optional pinned
lego runtime, DNS credentials, central issuance/renewal, private backups,
version history, account contact editing, version revocation, and deployment to
owned Agent certificate directories. Updating this package installs the pinned
Certbot ACME client used for account/revocation operations.

[Administrator Telegram notifications](../docs/notifications.md) are published
in `bf8eaa8` after isolated integration, exact-commit Docker and CI gates:
encrypted bot configuration, offline preview, durable test delivery and
package-expiry reminders. The first slice uses SQLite and a
separate private notification key directory; it does not implement user binding,
bot commands, payment or renewal approval.

[Backup v1 format tools](../docs/backup-format.md) provide a bounded manifest
parser, stored-ZIP validator, internal staging writer and the read-only
`open-node-backup validate PATH [--json]` command. The CLI checks an anonymous
private copy and never loads application settings, starts workers or restores
files. It reports database, key, provenance, snapshot and restoration checks as
not performed. This is not an online backup/download or recovery feature.

The CLI can encrypt an existing v1 package with
`open-node-backup encrypt PATH --recipient AGEKEY --output FILE`, or validate
an encrypted package with `open-node-backup validate PATH --identity KEYFILE`.
These operations use the pinned official age v1.3.2 binary bundled in the Docker
image, with exactly one native X25519 recipient. A Python-only installation does
not install that executable automatically. Output is private ciphertext created
without replacing an existing path; validation never publishes plaintext.
The identity file must belong to the caller, be a single-link regular file with
mode 0400 or 0600, and contain one native key. Neither operation reads application
settings or proves the database, stored credentials or snapshot are recoverable.
Temporary space can approach twice the input size; the default Compose `/tmp`
limit remains 64 MiB. See the format guide before processing larger files.
