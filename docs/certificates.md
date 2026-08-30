# Certificate Management

The FastAPI control plane owns certificate profiles, DNS credentials, ACME
accounts, jobs and versions. The React **Certificates** workspace creates DNS
providers and DNS-01 or HTTP-01 profiles, imports PEM pairs, issues/renews certificates,
edits CA account contacts, revokes individual versions, exports material and
assigns node deployment targets. No activation key, subscription
payment or commercial entitlement is required.

## Host Setup

ACME execution requires Linux. DNS-01 and control-plane HTTP-01 use the
[lego v4.35.2 executable](https://github.com/go-acme/lego/releases/tag/v4.35.2).
Open Node delegates issuance and provider protocols to this MIT-licensed client.
Remote-node HTTP-01, account contact and revocation operations use the Apache-2.0-licensed
[Certbot ACME client 5.7.0](https://github.com/certbot/certbot/releases/tag/v5.7.0),
installed with the backend dependencies. It does not copy the reference product's
implementation. Install a trusted,
version-pinned executable outside the repository, readable/executable by the
backend service account. The verified Linux amd64 release archive is:

```text
https://github.com/go-acme/lego/releases/download/v4.35.2/lego_v4.35.2_linux_amd64.tar.gz
SHA256 ee5be4bf457de8e3efa86a51651c75c87f0ee0e4e9f3ae14f6034d68365770f3
```

Check that digest before extraction and check `lego --version`. Other
architectures require their own upstream release and verified checksum.
Do not substitute lego v5: the CLI contract differs. Configure the same
service account, database and certificate state directory on backend restarts:

```bash
export OPEN_NODE_CERTIFICATE_LEGO_BINARY=/opt/open-node-tools/lego-4.35.2/lego
export OPEN_NODE_CERTIFICATE_STATE_DIR=/var/lib/open-node/certificates
```

Create the state directory with mode `0700`, owned by the backend account.
Its files must be private, not symlinked or hard-linked. The application
creates private state lazily when needed. All backend workers sharing a
database must share this directory on the same host; multi-host leader
election and network-filesystem locks are not supported. A file lock elects
one worker and is inherited by the ACME child, preventing another worker
from issuing while a surviving child still owns the lock.

Without a configured lego executable, remote-node HTTP-01 issuance/renewal,
PEM import/export, account editing, revocation and automatic/manual node deployment
remain available on Linux. Central DNS-01 and HTTP-01 issuance are unavailable. Deployments
require an installed, scanned Open Node Agent with an owned certificate
directory. Agent credentials and management authentication remain separate.

## Providers And Accounts

Supported DNS-01 adapters and required credentials:

| Provider | Required fields |
| --- | --- |
| Cloudflare | `CF_DNS_API_TOKEN` |
| AliDNS | `ALICLOUD_ACCESS_KEY`, `ALICLOUD_SECRET_KEY` |
| Tencent Cloud | `TENCENTCLOUD_SECRET_ID`, `TENCENTCLOUD_SECRET_KEY` |
| DNSPod | `DNSPOD_API_KEY` |
| GoDaddy | `GODADDY_API_KEY`, `GODADDY_API_SECRET` |
| NameSilo | `NAMESILO_API_KEY` |
| HTTP webhook | `HTTPREQ_ENDPOINT` |

Cloudflare optionally accepts `CF_ZONE_API_TOKEN`; the webhook optionally
accepts `HTTPREQ_USERNAME` and `HTTPREQ_PASSWORD`. Use narrowly scoped DNS
credentials. Updating a provider replaces its credentials; supply all required
fields again. Credentials are never returned by list/detail APIs or retained
in browser storage. Profiles referencing a provider prevent its deletion.

The webhook implements lego's default
[JSON present/cleanup protocol](https://go-acme.github.io/lego/dns/httpreq/).
Production webhook URLs require HTTPS. The explicit
`OPEN_NODE_CERTIFICATE_ALLOW_LOOPBACK_HTTP=true` option permits literal
loopback HTTP endpoints only, for test fixtures. Keep it disabled in production.
Provider-specific file paths, arbitrary environment variables and executable
hooks cannot be supplied through credentials.

The default directory allowlist contains Let's Encrypt production and staging.
Use `OPEN_NODE_CERTIFICATE_ACME_DIRECTORIES` as a JSON array to configure
other trusted HTTPS directories. Operators must explicitly accept the selected
CA's terms when creating a profile; creating a profile does not issue it.
Optional EAB key ID and HMAC key must be supplied together. Accounts and
private keys are retained in the profile's private lego directory.

### Account Edits

The account section shows its configured contact, local registration state,
EAB credential presence and any unresolved requested email. Editing a registered
account updates the contact at the CA before committing the new email to the
database. The original account key, account URI and private storage alias remain
unchanged; later lego renewals use the updated account file.

Before registration, the email and EAB credentials may be replaced or removed.
Replacing EAB requires both fields. A key left by a failed registration is looked
up at the CA and preserved, never overwritten with a different account/key.
An established CA EAB binding cannot be changed by editing this profile. Use
another profile when another account is required.

Account updates are serialized with issuance, renewal and revocation. If a
response is lost, the requested email remains visible with a retry action.
Only the latest failed account update can be retried; it retains encrypted EAB
parameters without returning them to the browser. A retry queries the CA first
and does not resend a contact change already accepted there.

## HTTP-01 Validation

The validation host is either the **control plane** or an explicitly enabled
**Open Node Agent**. Every requested hostname must route its public HTTP port **80**
challenge requests to the selected host. Check all published A/AAAA addresses and any
CDN, firewall or reverse proxy. HTTP-01 cannot issue wildcard certificates;
use DNS-01 for those. The panel neither changes DNS nor stops existing listeners.
HTTP modes are disabled until explicitly configured by the host administrator.

### Control-Plane Standalone Listener

For a non-root backend behind an existing web server:

```bash
export OPEN_NODE_CERTIFICATE_HTTP_ADDRESS=127.0.0.1:8082
```

Route the challenge path on the public port-80 virtual host to this listener:

```nginx
location ^~ /.well-known/acme-challenge/ {
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8082;
}
```

lego owns the listener only during a job; it closes after success, failure or
cancellation. An occupied port fails the job without terminating the process
that owns it. A direct listener such as `:80` is also supported if that port is
free and the service account already has binding permission. Do not run the
whole backend as root merely to bind port 80. In containers, configure the
actual reachable interface and proxy network rather than assuming host
loopback reaches the container. The API cannot choose addresses or ports.

### Control-Plane Webroot

Configure a host-side allowlist; the browser selects an ID, never a disk path:

```bash
export OPEN_NODE_CERTIFICATE_WEBROOTS='{"site":"/srv/open-node-http01"}'
```

Create that directory beforehand, readable/traversable by the web server and
writable by the backend account, for example mode `0755`. Serve its challenge
directory from the public port-80 virtual host:

```nginx
location ^~ /.well-known/acme-challenge/ {
    root /srv/open-node-http01;
    try_files $uri =404;
}
```

The allowed path may instead be the existing site's webroot. Open Node owns
only `.well-known/acme-challenge` underneath it and leaves other website content
alone. That challenge directory must be absent or empty on first use, owned by
the backend account, and reserved exclusively for Open Node afterward. Do not
share it with certbot or another ACME writer. Use a dedicated directory and the
location above if the existing site's challenge directory is already occupied.
Keep redirects in another location, not a server-level `return` that overrides
the challenge route.

Webroot paths and their components cannot be symlinks. The root and challenge
directories cannot be group/world-writable, and a webroot cannot contain or sit
inside the private certificate vault. Directory identity is recorded in the
private vault before lego starts. Startup, failed-job and cancellation cleanup
remove only valid challenge responses inside this registered directory;
unexpected content, replaced directories, links and special files fail closed.
No website files are deleted to make room for challenges.

Challenge responses are public `0644` files; newly created challenge directories
are `0755`. The webroot client uses umask `022`, but its account and certificate
work remains behind a `0700` profile directory. lego-created private files are
hardened to `0600` after the process exits and before reuse after interruption.
No private keys are copied to the public webroot. If a configured site is removed
or moved, finish outstanding jobs first; retained ownership records may need
host-side review. A cleanup warning for an unavailable old site does not block
unrelated certificates or node deployments.

### Remote Validation Node

On the node, enable one or both modes in its host-managed Agent configuration
and restart the owned Agent service:

```json
{
  "certificate_http_address": "127.0.0.1:8082",
  "certificate_webroots": ["site"]
}
```

These are optional fields in the existing configuration, not a replacement for
its master URL, token or runtime settings. Standalone requires a literal IP and
port, including bracketed IPv6 where appropriate. Route public port 80 to the
listener using the proxy location above. An occupied port causes failure; the
Agent does not stop its owner. Direct low-port binding requires host permission.

Node webroot IDs map only to `<agent-state-dir>/nginx/html/<id>`. With the normal
service installation, configure the relevant public port-80 Nginx server with:

```nginx
location ^~ /.well-known/acme-challenge/ {
    root /opt/open-node-agent/state/nginx/html/site;
    try_files $uri =404;
}
```

Use the actual owned state directory if installation paths differ. Validate and
reload Nginx after changing its configuration, and retain this HTTP location
when enabling TLS or redirects. The managed Nginx process runs as the Agent user
and can traverse its private state. Arbitrary external document roots are not
accepted by node commands; standalone proxying works with an independent web
server without granting that server access to private Agent state.

The challenge directory must initially be absent or empty and cannot be shared
with another ACME writer. The Agent records directory identity, checks ownership,
links and exact file contents, and leaves other site files unchanged. Responses
are public `0644` files, never account keys or certificate private keys.

After the next Agent scan, choose **Validation host** in the certificate form.
The API stores `validation_server_id`; null retains control-plane behavior.
Legacy Agents without an HTTP-01 capability report are not selectable. Creating
and starting a job both check the node's allowed mode and webroot IDs. A stale
scan cannot override the live Agent's host policy.

The central Certbot client creates the account, CSR and CA order. Only public
challenge tokens and key authorizations are sent over the authenticated Agent
command channel. EAB credentials and account/certificate keys stay in the
central private vault. Subsequent certificate deployment is a separate action.
No ACME client, CA account or DNS-provider secret is installed on the node.

Every presentation has an immutable, expiring lease. Keep controller and node
clocks synchronized. Cleanup is independent of
presentation success, survives controller restart and records a node acknowledgment.
A release received before an old presentation prevents that presentation from
resurrecting the challenge. Standalone responses stop at expiry; running Agents
also clean owned webroot files on expiry. An offline Agent cannot remove files
until restart. The supported managed Nginx service stops with its Agent.
Unexpected file replacements are preserved and reported for host attention.

The job displays **Node challenge cleanup pending** until the node confirms
cleanup, even when issuance succeeded. Retry runs automatically after reconnect;
profile deletion is blocked while cleanup is unresolved. Do not discard the
Agent journal or its owned challenge directory to bypass this check. Resolve
host-side ownership/content errors first. One damaged challenge directory does
not prevent expiration cleanup of other leases.

On controller interruption, remote jobs retain their job ID, certificate key,
CSR and CA order URL. Recovery first cleans older leases, then reconciles that
order with a fresh presentation lease when necessary. Already-finalized orders
are fetched without another finalization. A new-order request whose response was
lost before its URL was saved is explicitly unconfirmed and is not silently
resubmitted on resume. Inspect CA state before initiating a new job.

## Renewal And Deployment

The worker checks for due certificates every 30 seconds by default. Renewal
starts during the last third of validity, capped at 30 days, or the last half
for certificates valid for ten days or less. Successful/not-due checks are
scheduled at most an hour apart, shortened for short-lived certificates.
For central validation, lego also applies its renewal and ARI decisions.
Remote validation uses the same controller age policy without lego's ARI override.
A forced manual renewal
bypasses the normal age/ARI decision; use it sparingly because CA limits still
apply. Only one queued/running job per certificate is allowed.

Failed or interrupted issuance/renewal jobs retain the last active version and record a safe
error. Automatic retry is delayed an hour after failure; operators can retry
manually sooner. A restarted worker marks unfinished running central-validation jobs interrupted
and resumes queued jobs. A valid newly written certificate left by an
interrupted job can be recovered on a normal retry, avoiding an extra CA order.
Remote-node jobs instead reconcile their durable order as described above.
The worker bounds execution time and output and terminates its process group
on cancellation. Private `last-job.log` files aid diagnosis and may contain
provider responses: do not publish these logs.

Add a target with a concrete covered hostname, server, certificate filename,
reload mode and automatic-deployment preference. A filename on one node can
belong to only one certificate profile. Automatic deployment queues once per
new active version, including version activation. The target tracks the last
queued version and command status; a queued/failed command is not proof of a
successful deployment. Inspect its result and explicitly retry failed commands.
Pending commands block duplicate deployment and removal of that target.
Deleting a profile does not cancel already queued commands.

The control plane uses the directory from the Agent's current owned scan.
The Agent validates SAN coverage, key matching and dates, installs the PEM
pair with its existing durable file transaction, and reloads the selected
runtime. Failures restore old files and runtime intent. Certificate deployment
does not create a TLS site; configure one in the Nginx/config workspace.
The filename must match the site's configured certificate paths. Version
activation requires a matching, still-valid version; it does not revoke other
versions at the CA.

## Version Revocation

Revocation is irreversible. Select the exact version, reason and confirmation
in its dialog. Managed profiles use their original CA directory; imported
certificates require an explicit issuing CA from the host allowlist. The worker
proves possession with that certificate's private key using the ACME client.
It does not send the private key to the CA.

A persistent SHA-256 leaf-certificate ledger covers every matching stored copy,
including historical versions and legacy rows. Pending, unconfirmed and confirmed
revocations block version activation, managed deployment and reimport of that
same certificate. The ledger survives profile deletion. These are local records,
not an OCSP/CRL monitor or a guarantee that a certificate without a record has
never been revoked externally.

Active jobs on matching copies and unfinished certificate deployments block
revocation. Retained queued deployment commands are checked even when an older
release removed their target. Revoking a current certificate disables automatic
renewal for affected profiles; revoking only an older version does not disable
renewal of a different current certificate.

A timeout or lost response is **unconfirmed**, never presumed successful or
presumed safe to reuse. Retry the version's revocation with confirmation; a CA
`alreadyRevoked` response reconciles it as revoked. Administration helpers retain
the worker lock across a backend crash and write private, durable result receipts.
On restart, interrupted administration jobs resume with the same ID and reconcile
their receipt or CA state. Temporary request files are removed after execution.

Revocation does **not** delete node files or reload/stop Nginx/Xray. Already
deployed copies may still be served until the operator replaces them. A managed
profile can explicitly reissue: renewal is forced, uses a new certificate key,
and cannot recover the revoked PEM left on lego's disk. Automatic deployment
targets may then receive the new version; auto-renew remains off until enabled
again. Retained private-key exports remain available for operator recovery.

## Secrets And Backups

DNS credentials, EAB credentials and stored certificate material are encrypted
with a randomly generated Fernet key outside the database. Back up both the
database and the entire private state directory, including `vault.key`,
`vault.initialized`, HTTP webroot ownership records, administration/issuance receipts,
saved orders and account files. Back up each validation Agent's state and command
journal with its owned runtime configuration. Stop the backend while taking a
consistent filesystem backup, or use an equivalent coordinated snapshot.
Losing the vault key makes encrypted data unreadable. A missing key beside an
existing initialization marker or encrypted database rows fails closed instead
of silently replacing it, including after a backend restart.

This is not encryption of the entire database. Existing Agent command payloads
carry the certificate/private key for deployment, and lego keeps account/key
files in its protected directory. Secure the database, backups, host access
and transport accordingly. Export APIs hide the private key unless explicitly
requested; UI private-key downloads require confirmation. Responses use
`Cache-Control: no-store`.

Deleting a profile removes its catalog, jobs, encrypted versions and targets.
It does not revoke certificates, delete node files, cancel existing commands,
erase revocation records or erase its private lego state. Retained account material requires deliberate
host-side cleanup after checking backups and pending work.

Existing SQLite certificate catalogs are upgraded additively on startup.
DNS profiles keep their provider and default to DNS-01; imported PEM pairs
remain non-renewable. Back up the database and vault together before upgrading.
Webroot ownership records include filesystem identity: restoring to a different
filesystem or recreating a challenge directory requires deliberate host-side
revalidation of an empty directory before removing its old ownership record.

## Verification Limits

The [VPS ACME smoke](testing.md#acme-lifecycle-smoke) uses real lego and Pebble
DNS-01/EAB with a private authoritative DNS fixture, checks actual renewal,
and deploys certificates to real non-root Nginx instances over both Agent
transports. It does not consume public-CA orders or real DNS-provider accounts.
Cloudflare/AliDNS/Tencent/DNSPod/GoDaddy/NameSilo production credentials and
external CA behavior still require operator staging validation. HTTP-01
standalone/webroot coverage has a separate
[HTTP-01 lifecycle smoke](testing.md#http-01-lifecycle-smoke), including a real
non-root web server, forced interruption and actual automatic renewal.
The [administration smoke](testing.md#certificate-administration-smoke) additionally
checks actual CA account contacts, exact-version revocation, orphaned account keys,
EAB edits, lost responses, hard restart and duplicate/import protection.
Remote HTTP-01 has a separate [node-validation smoke](testing.md#remote-http-01-smoke).
Public CA/provider staging and multi-host scheduling remain migration gates.
