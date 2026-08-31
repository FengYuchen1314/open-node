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

[Administrator Telegram notifications](../docs/notifications.md) have passed
isolated integration gates and await publication: encrypted bot configuration,
offline preview, durable test delivery and package-expiry reminders. The first slice uses SQLite and a
separate private notification key directory; it does not implement user binding,
bot commands, payment or renewal approval.
