# Registration Invitations

Open Node supports administrator-issued subscriber registration without enabling
open anonymous signup. In **Subscriptions > Registration**, an administrator
chooses an existing plan and creates a one-time link valid for one hour, one day,
three days or seven days. The same dialog lists active, used, revoked and expired
invitations and can revoke an active link.

Opening the link switches `/account` from sign-in to account creation. The
subscriber chooses a username, a 12-1024 character password and optional display
name/contact email. A successful claim creates only the ordinary `user` role,
assigns the invitation's exact plan and dates, provisions subscriber login state,
creates stable node credentials and queues the normal durable subscription-access
command. The browser then signs in through the existing isolated subscriber realm.

## Bearer Token Safety

The generated token has 256 bits of entropy. It is returned only by the create
response and placed after `#invite=` in the registration URL, so normal HTTP
requests and access logs do not receive it while the page loads. The database
stores only its SHA-256 digest and an eight-character display hint. List and
revoke responses never contain the token or registration URL.

Claiming runs under the inventory's coordinated transaction. The invitation is
locked and rechecked before the username, password account, plan assignment,
credentials, access intent and used marker are committed together. Two concurrent
claims can produce only one account. A case-insensitive username collision rolls
back without consuming the invitation, allowing the recipient to choose another
name.

Unknown, expired, revoked and already-used tokens all return the same `404
Invitation unavailable` response. Password hashing uses Argon2id. Validation
responses omit request values, all `/account` responses are no-store with a
no-referrer policy, and the public claim route retains the browser-header,
same-origin and persistent peer/username attempt limits used by subscriber login.
Deleting a plan deletes its invitation history.

## API

Administrator session and CSRF protection are required for:

- `GET /api/v1/registration-invitations`
- `POST /api/v1/registration-invitations`
- `DELETE /api/v1/registration-invitations/{id}`

`POST /api/v1/account/register` is unauthenticated but accepts only a valid
one-time bearer token. Its body contains `token`, `username`, `password` and
optional `email`/`display_name`. There is no endpoint that registers without an
invitation, chooses an arbitrary plan or creates an administrator.

SQLite startup creates the additive `registration_invitations` table and indexes.
Existing tables and rows are unchanged.

## Verification

`backend/tests/test_registration_invitations.py` covers digest-only persistence,
working subscriber login, exact plan/runtime enrollment, generic unavailable
responses, revocation/expiry, case-insensitive username conflicts, atomic
concurrent claims, plan cleanup and administrator/public route boundaries.
Frontend service tests cover list/create/revoke and public claim requests.
Desktop, 390px mobile and 320px narrow layouts are checked against the production
frontend build on the designated VPS. The completed milestone passes all 883
backend tests and all 216 frontend tests. Its WebSocket smoke installs a temporary
non-root Agent, claims an invitation, waits for Xray access to apply and forwards
32 KiB through the exported client configuration. An old-database copy retained
all 48 existing table counts while adding an empty invitation table with no
foreign-key errors.
