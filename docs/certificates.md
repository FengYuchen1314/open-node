# Certificate Management

The FastAPI control plane owns certificate profiles, DNS credentials, ACME
accounts, jobs and versions. The Vue **Certificates** workspace creates DNS
providers and profiles, imports PEM pairs, issues/renews certificates, exports
material and assigns node deployment targets. No activation key, subscription
payment or commercial entitlement is required.

## Host Setup

ACME execution currently requires Linux and the
[lego v4.35.2 executable](https://github.com/go-acme/lego/releases/tag/v4.35.2).
Open Node delegates ACME and provider protocols to this MIT-licensed client;
it does not copy the reference product's implementation. Install a trusted,
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

Without a configured executable, PEM import/export and automatic/manual node
deployment remain available; ACME issuance/renewal is unavailable. Deployments
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

## Renewal And Deployment

The worker checks for due certificates every 30 seconds by default. Renewal
starts during the last third of validity, capped at 30 days, or the last half
for certificates valid for ten days or less. Successful/not-due checks are
scheduled at most an hour apart, shortened for short-lived certificates.
lego also applies its renewal and ARI decisions. A forced manual renewal
bypasses the normal age/ARI decision; use it sparingly because CA limits still
apply. Only one queued/running job per certificate is allowed.

Failed or interrupted jobs retain the last active version and record a safe
error. Automatic retry is delayed an hour after failure; operators can retry
manually sooner. A restarted worker marks unfinished running jobs interrupted
and resumes queued jobs. A valid newly written certificate left by an
interrupted job can be recovered on a normal retry, avoiding an extra CA order.
The worker bounds execution time and output and terminates its process group
on cancellation. Private `last-job.log` files aid diagnosis and may contain
provider responses: do not publish these logs.

Add a target with a concrete covered hostname, server, certificate filename,
reload mode and automatic-deployment preference. A filename on one node can
belong to only one certificate profile. Automatic deployment queues once per
new active version, including version activation. The target tracks the last
queued version and command status; a queued/failed command is not proof of a
successful deployment. Inspect its result and explicitly retry failed commands.
Pending commands block duplicate deployment. Removing a target does not cancel
already queued commands.

The control plane uses the directory from the Agent's current owned scan.
The Agent validates SAN coverage, key matching and dates, installs the PEM
pair with its existing durable file transaction, and reloads the selected
runtime. Failures restore old files and runtime intent. Certificate deployment
does not create a TLS site; configure one in the Nginx/config workspace.
The filename must match the site's configured certificate paths. Version
activation requires a matching, still-valid version; it does not revoke other
versions at the CA.

## Secrets And Backups

DNS credentials, EAB credentials and stored certificate material are encrypted
with a randomly generated Fernet key outside the database. Back up both the
database and the entire private state directory, including `vault.key`,
`vault.initialized` and lego account files. Stop the backend while taking a
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
or erase its private lego state. Retained account material requires deliberate
host-side cleanup after checking backups and pending work.

## Verification Limits

The [VPS ACME smoke](testing.md#acme-lifecycle-smoke) uses real lego and Pebble
DNS-01/EAB with a private authoritative DNS fixture, checks actual renewal,
and deploys certificates to real non-root Nginx instances over both Agent
transports. It does not consume public-CA orders or real DNS-provider accounts.
Cloudflare/AliDNS/Tencent/DNSPod/GoDaddy/NameSilo production credentials and
external CA behavior still require operator staging validation. HTTP-01,
webroot issuance, account editing/revocation and multi-host scheduling remain
outside this implementation and are recorded as migration gates.
