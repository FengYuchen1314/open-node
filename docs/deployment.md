# Control Plane Deployment

The root Dockerfile builds Vue 3/Vuetify assets and installs FastAPI in one
image. FastAPI serves both the application and API on port 8080; Node and Vite
are build tools, not production services. No activation or paid license is
required. This package is usable for the implemented workflows, not a claim
that every MMWX migration gate is complete. See [migration-map.md](migration-map.md).

## Requirements

- A Linux Docker host with Compose, Git, and outbound access to
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

## Install

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

1. Back up the stopped volume and retain the current image and configuration.
2. Start from a clean Git checkout and update with `git fetch origin` followed
   by `git merge --ff-only origin/main`. Do not reset or discard local edits.
3. Set a new image tag/revision in `deploy/.env`, build the image, then run
   `up -d --no-build --wait` using the same project name and volume.
4. Verify health, HTTPS login, an existing node, and certificate retrieval.
   A green health check proves process readiness, not every workflow.
5. If the new image fails, restore the old image tag/configuration and run
   `up -d --no-build --wait` again. This is an explicit operator rollback,
   not automatic release rollback.

The smoke test verifies data-compatible image rollback. Arbitrary future
database downgrades are not guaranteed: if a release has incompatible data
changes, restore its pre-upgrade backup into a fresh volume with the old
image, verify it, and then switch traffic. Do not run `down --volumes` during
upgrades, and do not prune the previous images until recovery is verified.
Ordinary `down` retains data but may recreate the bridge network later.

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
