# Subscriber Accounts

The `/account` page is a subscriber portal, separate from the controller
administrator interface. It provides current-plan usage, expiration/reset
dates, limits, subscription links/downloads, password changes, session
revocation and optional authenticator-based two-factor authentication.
No activation, entitlement lookup or paid feature gate is involved.

## Provisioning

Create the product user and assign a plan from Subscriptions. The key icon
beside that user opens **User login**. Set a password of 12-1024 characters
and confirm session revocation. Existing catalog users have no login password
until one is explicitly provisioned. There is no public registration endpoint
and no default subscriber password. Even a product user with `role=admin`
cannot use controller management APIs.

Password reset preserves the plan, assigned credentials, subscription tokens,
dates and charged traffic. Existing sessions and pending login challenges are
revoked. Enabled TOTP remains enabled unless the administrator explicitly
selects **Reset two-factor authentication and recovery codes**. This provides
recovery when both the authenticator and recovery codes have been lost.

Login settings use a revision guard. Stale dialogs must reload before they
can reset a password. The administrative API uses a `username` query parameter
so names containing `/` are supported without changing older inventory URLs.
Legacy MMWX password hashes and authenticator seeds are not imported by the
catalog importer. Provision new passwords during migration.

## Authenticator Key

Passwords are Argon2id hashes. Authenticator seeds must be recoverable for
verification, so they are encrypted with a controller-held Fernet key, bound
to the account name. Neither the key nor plaintext seeds appear in catalog
exports. Generate one key in the installed backend environment:

```bash
backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store it as `OPEN_NODE_SUBSCRIBER_TOTP_KEY` in the backend's private environment
or secret manager. For Compose, set it in the mode-0600 `deploy/.env` file and
recreate the service. Keep a separate private backup of this key together
with the deployment configuration. Do not commit it, log it or regenerate it
at every start. Key rotation/re-encryption is not automated.

An empty setting leaves password login working but makes new TOTP enrollment
unavailable. Losing or replacing a configured key prevents authenticator-code
verification for previously enrolled accounts; it never falls back to
password-only login. Existing unused recovery codes still work, or an
administrator can explicitly reset TOTP. Restoring the original key restores
verification unless the account's TOTP settings have since been reset.

## Two-Factor Workflow

Enrollment requires the current password and an active subscriber session.
The pending secret expires after ten minutes and can be confirmed only by
the session that started it. The QR image is generated locally in the browser
with `qrcode`; no seed is sent to an external QR service. Confirmation of an
authenticator code enables TOTP and returns ten recovery codes once.

TOTP uses [PyOTP](https://pyauth.github.io/pyotp/), a 30-second interval and a
one-step clock-skew window. The last accepted counter is stored in the
database and cannot be reused, including across concurrent requests or
backend restarts. A code used to finish enrollment cannot immediately be
used to log in again. Recovery codes have 80 bits of randomness and are
stored as account-bound SHA-256 hashes, with atomic one-time consumption.

After a correct password, an enrolled account receives only a five-minute,
single-use challenge, not a logged-in session. Each challenge allows five
verification attempts. Starting another password login replaces that account's
older challenge. Correct TOTP or an unused recovery code completes login.
Using a recovery code does **not** disable TOTP automatically; this differs
from the legacy recovery handler.

Changing a password, replacing recovery codes, disabling TOTP and rotating
subscription links require the current password and, when enabled, a fresh
TOTP or unused recovery code. Enabling/disabling TOTP and replacing recovery
codes revoke other sessions and pending login challenges. Password changes
revoke every session, including the current one. Recovery codes are never
stored in localStorage; the UI can download or copy them and clears them when
the dialog is closed.

## Sessions And Access

The `open_node_subscriber` cookie is independent of `open_node_session`, Agent
tokens and public subscription tokens. Session bearer tokens are stored only
as SHA-256 hashes. Cookies are HttpOnly, SameSite=Strict and Secure by default;
subscriber and administrator cookies share the deployment's lifetime/idle
timeout settings. Use HTTPS in production.

Writes require a subscriber-specific CSRF token and an allowed Origin. Login
also requires `X-Open-Node-Client: browser`. Subscriber API responses, including
validation errors, are `Cache-Control: no-store`; validation errors omit raw
request values. No browser-storage bearer token is used.

Login and credential-management attempts each have a persistent ten-per-minute
account limit, with an additional sixty-per-minute peer limit across both.
Peer identity comes from the server's trusted connection/proxy configuration,
not an arbitrary `X-Forwarded-For` request header. Administrator limits remain
unchanged. A subscriber can keep at most twenty sessions; the oldest is
revoked when a new session exceeds that cap.

Subscribers see only their own plan/quota profile, device sessions and exported
client configuration, not administrator remarks, other users' credentials or
management-only node/server inventory. They cannot
assign plans, reset usage, change limits, queue Agent commands, manage users
or acquire administrator privileges. An expired or exhausted plan can still
sign in to view its status; the public subscription renderer continues to
enforce plan availability.

Disabling or removing a user revokes sessions in the same database transaction,
including the legacy active-status endpoint and catalog import. Reactivation
does not revive old cookies. Deletion cascades through subscriber credentials,
sessions and challenges, and recreating the username requires new login
credentials. Remote traffic withdrawal retains the existing durable Agent
confirmation workflow; session revocation itself does not claim that an
offline Agent has already stopped traffic.

## API

All paths below are relative to `/api/v1/account`:

- `GET /session`, `POST /login`, `POST /login/verify`: session discovery and login.
- `POST /logout`, `POST /password`: logout and password changes.
- `GET /me`: own display name, contact, current quota and plan limits.
- `POST /subscription-token`: get/create own public subscription links.
- `POST /subscription-token/reset`: credential-confirmed link rotation.
- `GET /sessions`, `DELETE /sessions`: list devices or revoke other devices.
- `DELETE /sessions/{id}`: revoke one own session, including the current one.
- `GET /security`: enrollment availability, TOTP status and recovery-code count.
- `POST /totp/setup`, `POST /totp/confirm`: session-bound enrollment.
- `POST /totp/disable`, `POST /totp/recovery-codes`: credential-confirmed changes.

The administrator-only `GET` and `PUT /api/v1/subscriber-accounts?username=...`
read login status and provision/reset the password. No subscriber cookie can
authorize those routes.

## VPS Verification

All verification runs on `185.99.135.224`, not the development workstation.
The subscriber milestone passes 666 backend tests, 522 Agent tests and 131
frontend tests. The authentication-focused subset contains 46 tests, including
same-name account recreation, password-reset races, session limits, concurrent
recovery-code consumption and persistent TOTP replay rejection.

`scripts/vps/smoke-subscriber-account.py` uses an isolated backend, a non-root
installed Agent, trusted TLS and the free Xray build. It runs with both
`--transport websocket` and `--transport http`. The browser provisions a
password, downloads a subscription and forwards real traffic with that exact
Xray configuration, then verifies usage, TOTP enrollment, local QR pixels,
one-use recovery codes, password/link rotation, device revocation, explicit
administrator MFA recovery and account disable/reactivation. Other users'
traffic and the original plan dates remain intact.

Screenshots and bounds checks cover 1440x1000, 390x844 and 320x740 viewports.
The smoke accepts `--wheel`, `--xray`, `--nginx` and `--output` paths; it creates
and removes its own temporary services and accounts. The existing
Starlette/httpx deprecation and frontend large-bundle warnings remain.
