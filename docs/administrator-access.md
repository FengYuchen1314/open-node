# Administrator Access

Open Node has one local administrator. This is access control for your own
installation, not activation or licensing. There are no paid roles, remote
entitlement calls, default passwords, or anonymous administrator registration.
An installation without an administrator rejects management requests.

## Create or Recover the Administrator

Use the same database URL and working directory as the backend service:

```bash
cd /opt/open-node
export OPEN_NODE_DATABASE_URL=sqlite:////opt/open-node/data/open-node.db
backend/.venv/bin/open-node-admin create --username admin
```

The command prompts for a password and confirmation. Passwords must have
12-1024 characters. Usernames use 1-64 ASCII letters, digits, or `_.@-`.
Credentials are stored locally using Argon2id, through `pwdlib`.
Protect the database and its backups as sensitive application data.

The command refuses to overwrite an existing account. To recover access:

```bash
backend/.venv/bin/open-node-admin reset-password --username admin
```

Resetting revokes every existing session. The signed-in administrator can
also change their password from Access; that requires the current password
and signs out all sessions. For noninteractive provisioning, `--password-stdin`
reads one password line from standard input, never a command-line argument.

## HTTP and Browser Deployment

Use HTTPS and serve the frontend and API under the same origin in production.
Preserve the original Host header at the reverse proxy, proxy `/api` to
FastAPI, and restrict trusted forwarded headers to your own proxy addresses.
Keep the backend listener private. The proxy should apply request-size limits,
additional login rate limits, and normal TLS hardening. Use the shipped
[Compose and HTTPS deployment guide](deployment.md). The current VPS Preview
runs the hardened `cb1eb0c` baseline in persistent Compose under an enabled and
active systemd unit. It listens only on `127.0.0.1:8000` and is reached through
an SSH tunnel. Backup, restart, Compose down/up, and isolated restore have been
verified. This is operational persistence, not public HTTPS acceptance: a
production hostname, DNS, trusted certificate, and public reverse-proxy
configuration still require operator input.

Subscription and temporary-link bearer credentials can appear in request paths.
The hardened baseline therefore disables Uvicorn and edge-proxy access logs and
bounds retained container logs. These controls passed VPS regression and
persistent-Compose acceptance. Do not compensate with an access-log format that
records the request URI.

`OPEN_NODE_SESSION_COOKIE_SECURE` defaults to `true`. The session cookie is
HttpOnly, SameSite=Strict, host-only, and scoped to `/`. The random session
secret is stored only as a SHA-256 hash in the database, never in browser
localStorage or API response bodies. Sessions have a 12-hour absolute lifetime
and expire after 30 minutes without authenticated requests. These limits use
`OPEN_NODE_SESSION_LIFETIME_SECONDS` and `OPEN_NODE_SESSION_IDLE_SECONDS`.
Sessions and their expiry survive backend restarts.

For HTTP development over a loopback listener or an SSH tunnel only:

```bash
export OPEN_NODE_SESSION_COOKIE_SECURE=false
backend/.venv/bin/uvicorn open_node.main:app --host 127.0.0.1 --port 8000
```

The Vite development proxy preserves the browser origin and defaults to
`http://127.0.0.1:8000`. `OPEN_NODE_DEV_API_TARGET` can select another backend
listener. Leave the secure-cookie setting enabled on public deployments.
Do not expose the Vite development server publicly.

Human-readable generated/custom subscription aliases and legacy `/x` routes are
disabled by default with `OPEN_NODE_SHORT_LINKS_ENABLED=false`. Keep that setting
for a normal public deployment; only the long 256-bit subscription token is then
accepted. Enable aliases only for a controlled legacy migration on a restricted
endpoint. The `cb1eb0c` hardened baseline also rotates legacy subscription
bearers when compatibility remains disabled; the deployed persistent Compose
database passed the upgrade and regression gates.

The same baseline enables SQLite foreign-key enforcement for every application
connection. The persistent Compose startup verified `PRAGMA foreign_keys` is
`1` and ran `PRAGMA foreign_key_check`; backup, restart, down/up, and isolated
restore were also exercised. Retain the pre-upgrade volume backup for future
upgrades. A healthy process alone does not prove database integrity.

## API Contract

- `GET /api/v1/auth/session`: returns initialization state and, when signed in,
  the username and session-bound CSRF token. It is never cacheable.
- `POST /api/v1/auth/login`: accepts JSON `username` and `password`, requires
  `X-Open-Node-Client: browser`, rotates the session cookie, and returns the
  session metadata. Login requests with a foreign Origin are rejected.
- `POST /api/v1/auth/logout`: revokes the current session.
- `POST /api/v1/auth/password`: accepts `current_password` and `new_password`,
  changes the credential, and revokes all sessions.

Authenticated writes also require the session's `X-CSRF-Token` and a valid
Origin when supplied. `OPEN_NODE_CORS_ORIGINS` accepts an explicit JSON origin
list; wildcards are rejected. CLI API clients retain the login cookie and send
the CSRF token for writes. Neither an Agent bootstrap token nor a subscription
token grants administrator access.

Login and password-change attempts share a persistent limit of ten attempts
per source IP per minute, including successful attempts. Excess attempts return
429 and `Retry-After`. The application uses the ASGI client address, not a raw
caller-supplied forwarding header. Correct proxy trust configuration matters.

Management APIs, the Agent inventory list, credential exports, commands, and
both probe-settings write aliases require an administrator session. Agent
registration, heartbeat, telemetry, leasing, results, and WebSockets retain
their own bootstrap-token authentication. Health/license metadata, subscription
links, and read-only public probe routes retain their existing access rules.
The control-plane Probe view requires sign-in; the standalone probe Worker
remains public according to its configured token policy.

The implementation follows the [FastAPI password-hashing guidance](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
and the [OWASP session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
and [CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
