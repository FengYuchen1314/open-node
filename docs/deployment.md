# Control Plane Deployment

The root Dockerfile builds React/Ant Design assets and installs FastAPI in one
image. FastAPI serves both the application and API on port 8080; Node and Vite
are build tools, not production services. No activation or paid license is
required. This package is usable for the implemented workflows, not a claim
that every MMWX migration gate is complete. See [migration-map.md](migration-map.md).

## Requirements

- A Linux Docker host with Docker Compose v2, Git, and outbound access to
  Docker Hub, npm, PyPI, GitHub, and the selected ACME/DNS services.
- For public access: a hostname, an existing host HTTPS reverse proxy, and a
  certificate trusted by clients. Bootstrap the panel certificate outside the
  panel; do not depend on an unauthenticated panel to provision its own TLS.
- Backups on a private filesystem outside the application's volume.

The shipped setup uses one backend process, one host-local SQLite database,
and one certificate worker. Do not scale the service or add Uvicorn workers:
the active Agent connections and public streams are process-local. Multi-host
operation is not supported. Linux amd64 is tested; the lego downloader also
has an arm64 checksum, but an arm64 image has not been validated.

## GitHub One-Command Install

On a new Debian or Ubuntu Docker host, run:

```bash
(
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  sudo bash "$installer"
)
```

The anonymous Raw URL and public `main` clone were validated on 2026-08-30 in an
unused Debian 12 VPS namespace. The release check covered fresh installation,
administrator API login, status, same-revision update, data-preserving uninstall,
and exact fixture cleanup. The separate maintainer smoke covers rollback and
failure injection as described below.

Downloading to a temporary file first prevents a partial transfer from being
executed as a shell program and leaves the terminal on standard input. It does
not authenticate the download. The URL follows mutable `main`, and the script
then resolves the requested repository ref in a separate Git operation; those
two inputs are not cryptographically bound. For a controlled rollout, review a
commit-specific `install.sh` URL and set `OPEN_NODE_REF` to a reviewed release
branch or tag that identifies the intended source. Retain the tested image; a
remote branch or tag can later move, and the installer does not verify Git
signatures or release attestations.

The installer performs these bounded actions:

1. verifies root-owned, non-symlink install/configuration/backup paths and
   installs Git, curl, Docker and Compose v2 through `apt-get` when missing;
2. clones the configured ref into a temporary candidate checkout and refuses a
   pre-existing unmanaged Compose project, data volume, environment or source;
3. creates mode-`0600` environment and manifest files. The manifest records the
   repository/ref, directory and Compose identities, deployed revision, unique
   image tag, and immutable image ID;
4. validates the candidate Compose image and data volume, builds a
   transaction-unique `source-<revision>-<transaction>` image, starts it without
   rebuilding, and verifies that the container uses the recorded image ID, has
   the requested published binding, and passes `/healthz`; and
5. when a usable controlling terminal is present, prompts through `/dev/tty`
   for the initial administrator without putting the password in command-line
   arguments or environment variables. Otherwise automatic account creation is
   skipped unless unattended creation was explicitly required.

The default listener is `127.0.0.1:8080` and the initial loopback/SSH-tunnel
cookie setting is HTTP-compatible. From your workstation, open a tunnel and
then visit `http://127.0.0.1:8080`:

```bash
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
```

For unattended installation, put the administrator password in a root-owned
file with no group/other permissions and pass its absolute canonical path. The
first line must contain 12-1024 characters. Use a trap so the delivered secret
is removed on success, error, or interruption:

```bash
(
  installer=""
  password_file=""
  cleanup() {
    [[ -z "$installer" ]] || rm -f -- "$installer"
    [[ -z "$password_file" ]] || sudo rm -f -- "$password_file"
  }
  trap cleanup EXIT
  trap 'exit 1' HUP INT TERM
  installer="$(mktemp)" || exit 1
  password_file="$(sudo mktemp /root/open-node-admin-password.XXXXXX)" || exit 1
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  sudo bash -c '
    umask 077
    printf "Administrator password: " >/dev/tty
    IFS= read -r -s password </dev/tty
    printf "\nConfirm administrator password: " >/dev/tty
    IFS= read -r -s confirmation </dev/tty
    printf "\n" >/dev/tty
    [[ "$password" == "$confirmation" ]] || exit 1
    printf "%s\n" "$password" > "$1"
    chmod 0600 "$1"
  ' _ "$password_file" || exit 1
  sudo env OPEN_NODE_CREATE_ADMIN=1 \
    OPEN_NODE_ADMIN_PASSWORD_FILE="$password_file" \
    bash "$installer"
)
```

Both reads above use the controlling terminal, so the password is not included
in shell history. Secret-manager users can deliver the same private root-owned
file without using `/dev/tty`. The installer reads the secret but deliberately
does not delete an operator-owned file; the caller's trap owns that cleanup.

Common lifecycle commands can use the same completely downloaded, reviewed
script. The explicit `create-admin` action is also available after installation:

```bash
(
  action=status # Change to update, uninstall, or create-admin as required.
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  sudo bash "$installer" "$action"
)
```

On the first successful install, `/etc/open-node/installer.manifest` becomes the
installer's source of identity. Later invocations load the saved repository,
ref, install directory, backup directory, project name and image repository
unless the operator explicitly supplies them; an explicit conflicting value is
rejected. `OPEN_NODE_CONFIG_DIR` is the lookup key and cannot be discovered from
the manifest itself. If the first install used a custom configuration directory,
set that same `OPEN_NODE_CONFIG_DIR` on every later action. Do not move, copy or
hand-edit the checkout, environment, manifest or named volume as an adoption
mechanism; the installer fails closed on identity, ownership or cleanliness
mismatches.

For example, an installation whose manifest is under `/srv/open-node-config`
must be addressed this way even though its other saved overrides are loaded:

```bash
sudo env OPEN_NODE_CONFIG_DIR=/srv/open-node-config bash /path/to/reviewed/install.sh status
```

Useful unattended overrides include `OPEN_NODE_REF`,
`OPEN_NODE_HTTP_PORT`, `OPEN_NODE_INSTALL_DIR`, `OPEN_NODE_CONFIG_DIR`,
`OPEN_NODE_BACKUP_DIR`, and `OPEN_NODE_PROJECT_NAME`. A public plain-HTTP bind
requires both `OPEN_NODE_BIND_ADDRESS=0.0.0.0` and the explicit
`OPEN_NODE_ALLOW_PUBLIC_HTTP=1` opt-in; it is not the recommended production
topology. Before putting the loopback listener behind HTTPS, set
`OPEN_NODE_SESSION_COOKIE_SECURE=true` and configure the exact trusted proxy as
described below.

This installer deliberately targets a new or already installer-managed,
single-host Docker/SQLite deployment. It does not adopt an existing manual
Compose installation, merge data, configure a reverse proxy or public DNS/TLS,
restore a backup, prune retained images/backups, install or migrate remote
Agents, or claim support for the upstream MMWX native binary, PostgreSQL,
embedded Nginx, Windows, rootless Docker, multi-host or multi-worker operation.

### Enable panel-issued Agent commands

After configuring a trusted HTTPS reverse proxy, supply the canonical control-
plane URL to the reviewed root installer. For an existing **installer-managed**
deployment:

```bash
sudo env OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=https://control.example.com \
  bash /path/to/reviewed/install.sh update
```

Use the same `OPEN_NODE_CONFIG_DIR` override if the original installation used
a non-default directory. For a fresh deployment, the same setting can be passed
to `install`. It must identify the control plane, not the anonymous Probe Worker.
The selected source ref must include the bootstrap feature. The installer
accepts an empty value or a canonical HTTPS URL with a lowercase ASCII/punycode
host, optional port and clean path prefix; omit a trailing slash. It rejects
credentials, query/fragment components, whitespace and dotenv interpolation.
The backend performs its own URL validation before becoming healthy.

An explicit changed value uses the normal update transaction **even when the
source commit is unchanged**: stopped-volume backup, a new transaction image,
candidate health/identity checks and publication of the private environment.
This is a service update with the usual interruption/recovery semantics, not a
live setting toggle. An omitted or identical value retains the same-revision
no-op behavior. To disable new commands deliberately:

```bash
sudo env OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL= \
  bash /path/to/reviewed/install.sh update
```

Do not hand-edit the installer's environment or manifest. Manual Compose
deployments may set the same key in their own private environment and recreate
the reviewed service; the root installer still refuses to adopt them. The
setting neither creates HTTPS infrastructure nor installs Agents automatically.
See [panel-issued Agent installation](agent-bootstrap.md) for its new-host
scope, secret handling and recovery rules.

### Maintainer installer acceptance

The maintainer-only installer smoke is destructive and must run as root on the
disposable project VPS from a Git checkout. It disables the installer's automatic
package installation, so the host must already provide the commands checked by
`install.sh` (including `curl`, `jq`, GNU core utilities, Git and `flock`) plus a running
Docker daemon and Docker Compose v2. It builds several real images and creates a
private temporary Git remote under `/root`. Every source/configuration/backup
directory, loopback port, image repository, Compose project, network and named
volume receives a random fixture identity. It exercises fresh install, real
administrator login, same-revision no-op, locking, stopped and volume-only
updates, backup and interruption failures, unhealthy-candidate recovery,
data-preserving uninstall/reinstall, and missing-volume refusal. Do not run it
on a production host, and inspect/remove its uniquely named resources manually
if mandatory cleanup reports a failure:

```bash
sudo python3 scripts/vps/smoke-control-plane-installer.py
```

The separate bootstrap-setting gate checks the old/new Compose environment
allowlists, inherited-shell isolation and explicit setting transitions. Its
default mode does not start containers; `--safety-negative-controls` checks Git
environment isolation and refusal to clean an unowned namespace, while
`--guarded-update` adds the isolated fresh/same-source enable/no-op/disable
transaction and cleanup. It has the same
root-only disposable-host requirement:

```bash
sudo python3 scripts/vps/smoke-installer-bootstrap-setting.py \
  --safety-negative-controls --guarded-update \
  --output /tmp/open-node-installer-bootstrap-reviewed-revision
```

## Manual Install

On the deployment host:

```bash
git clone https://github.com/FengYuchen1314/open-node.git
cd open-node
install -m 600 deploy/.env.example deploy/.env
git rev-parse HEAD
```

Set `OPEN_NODE_IMAGE_TAG` and `OPEN_NODE_REVISION` in `deploy/.env` to that
commit hash. Use a new tag for each build you intend to deploy. Keep this
file private and outside version control. Then run from the repository root:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait
docker compose --env-file deploy/.env -f deploy/compose.yaml exec open-node open-node-admin create
curl --fail http://127.0.0.1:8080/healthz
```

The administrator command prompts for a password without echoing it. There
is no default account, and another `create` cannot overwrite an existing
administrator. For recovery, use the same command with `reset-password`;
all existing sessions are revoked. `--password-stdin` is available for
secret-manager integration. Never place passwords in command arguments.

Subscriber login is provisioned separately in Subscriptions and is available
at `/account`. To offer authenticator enrollment, set a private
`OPEN_NODE_SUBSCRIBER_TOTP_KEY` in `deploy/.env`; retain that key with the
private configuration backup, not in Git or a database export. See
[subscriber accounts](subscriber-accounts.md) for key generation and recovery.
An empty value disables new authenticator enrollment, not password login.

The port is bound to host loopback only. The service runs as UID/GID 10001,
with no capabilities, no privilege escalation, a read-only image filesystem,
a temporary `/tmp`, and a private named volume at `/var/lib/open-node`.
SQLite, the certificate vault key, and lego account data live in that volume.
Do not mount the Docker socket, host configuration directories, or a public
download directory into the service. For bind mounts, provision the directory
for UID/GID 10001 with mode 0700 yourself; an arbitrary empty bind mount does
not inherit the image's ownership as a new named volume does.

## HTTPS And Proxy Trust

Use [deploy/nginx.conf.example](../deploy/nginx.conf.example) inside the host
Nginx `http` block. Replace `panel.example.com`, the two TLS file paths, and
the loopback upstream port if it differs. Check the host configuration with
`nginx -t` before reloading. The example preserves Host, forwards WebSocket
upgrades, and replaces incoming forwarding headers at the edge proxy.

The production image disables Uvicorn request access logs, and the sample
Nginx virtual hosts disable access logs on both the HTTP redirect and HTTPS
listener. Subscription and temporary-link bearer credentials are part of the
request path, so a conventional request-line log would disclose them. This
also removes ordinary access-audit and traffic records for this virtual host.
The sample limits this virtual host's error log to `crit` in a dedicated file,
because ordinary upstream errors can also include the raw request URI. Create
that file as root with mode `0600`, rotate it privately, and do not forward it
to a shared collector. Apply an equivalent rule to every CDN, load balancer
or custom reverse proxy in front of Open Node. If access metrics
are required, record only an explicit safe allowlist such as status and timing,
without the raw URI, query string, authorization headers or cookies.

Keep `OPEN_NODE_SHORT_LINKS_ENABLED=false` for production. In that secure
default, `/api/v1/subscribe/{key}` accepts only the long 256-bit bearer token,
legacy `/x/...` links return 404, and the UI does not offer generated or custom
short links. The `/t/...` temporary-share endpoint uses its own 192-bit random
token and remains available. Setting the option to `true` is a migration-only
compatibility mode for existing MMWX short links; human-selected aliases can
be guessed and are not suitable as public bearer credentials.

The production Nginx example also returns 404 for `/x` and for subscription
paths that are not a 43-character base64url bearer. Keep those edge rules in
secure-default deployments. They are defense in depth for the current image and
prevent a break-glass rollback to an older image from silently re-enabling short
aliases. An operator who deliberately enables migration compatibility must use a
separate restricted proxy policy instead of removing the protection on a public
endpoint.

On the first startup with compatibility mode disabled, token rows created by
an older schema are treated as unverified. Open Node replaces their long token
with a new 256-bit bearer, replaces the generated alias, clears the custom
alias, and records the secure generation so later restarts do not rotate it
again. The legacy MMWX importer applies the same rule. Export or redistribute
the new long URLs after upgrade; enable compatibility mode before the upgrade
only when preserving the old values is an explicit migration requirement.

Find the application container's bridge gateway:

```bash
CID="$(docker compose --env-file deploy/.env -f deploy/compose.yaml ps -q open-node)"
docker inspect "$CID" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'
```

For this host-proxy-to-published-loopback-port topology, set
`OPEN_NODE_TRUSTED_PROXIES` in `deploy/.env` to that exact address. Apply it:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait
```

Keep `OPEN_NODE_SESSION_COOKIE_SECURE=true`. Open your HTTPS hostname and
sign in. Test a direct page reload at `/config`, and verify the Agent's
`wss://` endpoint if attaching nodes. HTTPS Origin validation requires the
original scheme and Host. A 403 after login can indicate a missing proxy
trust setting; do not fix it by allowing arbitrary Origins or disabling CSRF.

Trust only the actual proxy address, never `*` or a whole shared Docker
subnet. Check the address again after network recreation. Other proxy
topologies, rootless Docker, additional proxy hops, and CDN headers need their
own peer-address verification. The sample is an edge proxy, not a generic
multi-hop forwarded-header configuration. This follows the trust model in
[FastAPI's proxy documentation](https://fastapi.tiangolo.com/advanced/behind-a-proxy/).

For a temporary SSH-tunneled loopback HTTP preview only, set
`OPEN_NODE_SESSION_COOKIE_SECURE=false` and recreate the service. Never use
that setting for public deployment. No alternate CORS origin is needed for
the bundled same-origin frontend.

## Back Up And Restore

Back up the entire data volume while the service is stopped, not just the
SQLite file. The vault key is required to decrypt certificate versions and
DNS credentials; a database-only restore is incomplete. The backup also
contains administrator state, Agent tokens, subscription secrets, ACME
accounts, and queued deployment keys. Store it privately and encrypt it for
off-host storage. Never commit it or place it under the frontend directory.

Example from the repository root, in Bash:

```bash
umask 077
BACKUP="$HOME/open-node-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
docker compose --env-file deploy/.env -f deploy/compose.yaml stop
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm -T --no-deps \
  --entrypoint tar open-node -C /var/lib/open-node -czf - . > "$BACKUP"
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait
tar -tzf "$BACKUP"
```

Check every command's exit status. If archive creation fails, restart the
service, retain the previous backup, and do not proceed with an upgrade.
Record the deployed image ID/tag, source revision, Compose configuration,
and proxy configuration alongside the backup. Prefer retaining the exact
image artifact (`docker image save`) to rebuilding an old source revision.

For restoration, use a fresh, uniquely named Compose project and an unused
loopback port. Create a separate `deploy/.env.restore` with the original
image tag, a different `OPEN_NODE_HTTP_PORT`, and an initially empty trusted
proxy value. Do not change the running deployment's environment file.
Create the new project's empty volume without starting the service, then
restore your trusted archive:

```bash
docker compose --env-file deploy/.env.restore -p open-node-restored -f deploy/compose.yaml create --no-build
docker compose --env-file deploy/.env.restore -p open-node-restored -f deploy/compose.yaml \
  run --rm -T --no-deps --entrypoint tar open-node -C /var/lib/open-node -xzpf - < "$BACKUP"
docker compose --env-file deploy/.env.restore -p open-node-restored -f deploy/compose.yaml up -d --no-build --wait
```

Restore only into an empty volume you just created, with the service stopped.
Do not merge a backup into a running or newer database. Verify login,
inventory and certificate private-key retrieval before changing proxy
traffic to the restored instance; configure its new trusted proxy address.
Remote host files and Agent state are not in the control-plane backup.

## Upgrade And Roll Back

### Installer-managed updates

`install.sh update` requires the installed checkout, manifest, private
environment, named volume and recorded image ID to agree exactly. It fetches the
saved ref and accepts only a fast-forward descendant of the deployed revision.
Each real update builds and inspects a transaction-unique image while the old
container can continue running. If the fetched revision is already deployed,
the command is a no-op: it does not rebuild an image, stop a container, create a
backup, or replace the previously retained recovery artifacts. It checks health
only when the current container is running.

Before a candidate is started, the installer records the old container state,
stops it if necessary, and creates a new private bundle under
`OPEN_NODE_BACKUP_DIR`. The bundle contains:

- `volume.tar.gz`, a validated archive of the stopped data volume;
- `open-node.env`, `installer.manifest`, and the deployed `compose.yaml`; and
- `deployment.meta`, including the old revision, tag, immutable image ID,
  Compose project/volume identity, creation time and a transaction-specific
  local rollback-image tag.

The old image is tagged in the local Docker store; its bytes are not embedded
in the bundle. Export that image separately for off-host or Docker-store-loss
recovery. Bundles are never silently overwritten or pruned.

Failure handling depends on how far the transaction progressed:

- A checkout, Compose-validation or image-build failure occurs before the old
  container is stopped and cleans up the temporary candidate.
- If stopped-volume backup creation or validation fails, no candidate is
  started. A previously running old container is restarted and health-checked;
  a deployment that was already stopped remains stopped.
- Once the candidate has started against the real volume, it may have migrated
  persistent data. If candidate startup/health or the later source/environment
  commit fails, the candidate is stopped, the old source/environment/manifest
  identity remains recorded, and a private `installer.recovery` marker names the
  candidate and backup. The installer deliberately does **not** restart the old
  image against the possibly migrated volume.
- An interrupt during a state-changing phase also attempts to stop the
  candidate, remove temporary worktrees/files, and record the interrupted phase
  and available backup in `installer.recovery`.

While that marker exists, `install`, `update`, and `create-admin` fail closed;
`status` displays it. Restore the named bundle into a fresh, uniquely named
Compose project and verify the old image and data together using the procedure
above. Only after recovery has been completed and recorded state reconciled
should an operator remove the marker. The installer does not automate that
restore decision. A failed fresh candidate is stopped and no installation is
committed, though its recovery marker can remain if failure occurred after
candidate activation began.

Legacy MMWX Agents that require a signing identity need the explicit setup in
[legacy-agent-migration.md](legacy-agent-migration.md). Keep that private seed
inside the persistent volume so the ordinary whole-volume backup includes it.
Setting `OPEN_NODE_AGENT_IDENTITY_FILE` to a missing or invalid file deliberately
prevents startup rather than changing the identity trusted by existing Agents.

1. Back up the stopped volume and retain the current image and configuration.
2. Start from a clean Git checkout and update with `git fetch origin` followed
   by `git merge --ff-only origin/main`. Do not reset or discard local edits.
3. Set a new image tag/revision in `deploy/.env`, build the image, then run
   `up -d --no-build --wait` using the same project name and volume.
4. Verify health, HTTPS login, an existing node, and certificate retrieval.
   A green health check proves process readiness, not every workflow.
5. If the new image fails, restore a security-compatible old image tag and
   configuration and run `up -d --no-build --wait` again. This is an explicit
   operator rollback, not automatic release rollback.

Images built before the secure short-link default are **not** security-compatible
rollbacks: they ignore `OPEN_NODE_SHORT_LINKS_ENABLED` and accept generated,
custom and legacy `/x` aliases again. The production Nginx deny rules above must
remain active throughout such a rollback. Without an equivalent edge proxy,
keep the old image bound to loopback, block subscriber traffic, restore the
pre-upgrade backup for diagnosis, and return to a hardened image before exposing
the service. Never expose a pre-hardening image directly as a routine rollback.

The smoke test verifies data-compatible image rollback; it does not make a
pre-hardening image security-compatible. Arbitrary future
database downgrades are not guaranteed: if a release has incompatible data
changes, restore its pre-upgrade backup into a fresh volume with the old
image, verify it, and then switch traffic. Do not run `down --volumes` during
upgrades, and do not prune the previous images until recovery is verified.
Ordinary `down` retains data but may recreate the bridge network later.

The coordinated change-set upgrade adds execution states that older builds
cannot read. For this upgrade, returning to the older three-state build
requires its pre-upgrade database backup, not only an image change. Stop new
dispatches and resolve outstanding Agent work before recovery; restoring a
control-plane database does not undo commands already executed on nodes.
Review [legacy change-set migration](change-sets.md#upgrade-from-earlier-open-node-builds)
before upgrading a deployment with existing change runs.

The Node/Python base manifests and lego release are pinned; frontend
dependencies use `package-lock.json`. Python dependencies currently resolve
within the constraints in `backend/pyproject.toml`. Builds are therefore not
claimed bit-for-bit reproducible. Keep the tested image artifact for rollout
and rollback. Third-party licenses are not activation requirements; the image
includes the project MIT license and bundled lego license.

## Verification

See [the VPS deployment smoke](testing.md#control-plane-deployment-smoke).
It uses the actual Compose file and proxy template, separate projects
and private volumes, a fixture-only TLS identity, and desktop/mobile
Chromium. It does not install a public TLS certificate or alter host DNS.
