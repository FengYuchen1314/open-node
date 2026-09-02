# 控制面部署

普通用户优先按本节或根目录 [README](../README.md) 部署；后面的英文段落保留完整的
Compose、代理信任、备份和恢复工程契约。

## 中文快速部署

支持全新的 Debian/Ubuntu 主机，需要 root 或 `sudo`。默认公网模式还需要可路由的公网
IPv4、空闲的 TCP `443` / `58090`，以及到 GitHub、Docker Hub、npm、PyPI 和 Let's
Encrypt 的出站网络。TCP `80` 无需开放；已有服务占用 `443` 时应改用本页的手动代理
方案，不能同时启动受管 Caddy。

```bash
(
  set -eu
  as_root() {
    if [ "$(id -u)" -eq 0 ]; then
      "$@"
    else
      command -v sudo >/dev/null 2>&1 || {
        echo "需要 root 权限或 sudo" >&2
        return 1
      }
      sudo "$@"
    fi
  }
  if ! command -v curl >/dev/null 2>&1; then
    as_root apt-get update
    as_root apt-get install -y --no-install-recommends ca-certificates curl
  fi
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  as_root bash "$installer"
)
```

默认安装使用 SQLite，两个独立 HTTPS 服务必须返回相同公网 IPv4。安装器随后为
`https://公网IP:58090` 申请受系统信任的 Let's Encrypt `shortlived` IP 证书，并把请求
转发到 `127.0.0.1:62031 → 容器 62031/tcp`。宿主 `62031` 只绑定回环，不需要也不应该
对公网开放。误输入 `http://公网IP:58090` 时，网关以 `308` 保留路径和查询参数并跳转
到同端口的 HTTPS 地址。

安装器会实时输出公网 IP、数据库、应用健康和 HTTPS 证书进度。只有证书、规范 URL 和
`/healthz` 连续通过后才退出，并输出：

```text
ACTION_COMPLETE action=install
```

IP 证书有效期约 160 小时（约 6 天），由 Caddy 自动续签。TCP `443` 用于
TLS-ALPN-01，首次签发和续签期间都必须从公网可达；`58090` 不能代替验证端口。安装器
不会在失败时降级到明文 HTTP、自签证书或 `curl -k`。

默认生命周期命令：

```bash
sudo bash /opt/open-node/install.sh status
sudo bash /opt/open-node/install.sh update
# 仅用于未初始化实例的浏览器备份恢复凭证
sudo bash /opt/open-node/install.sh setup
sudo bash /opt/open-node/install.sh create-admin
sudo bash /opt/open-node/install.sh uninstall
```

`update` 是一键更新入口；官方 `main`、默认 systemd 和完整安装身份通过校验时，也可在
“系统设置 → 应用更新”执行。`uninstall` 保留数据；需要决定是否清除数据时运行：

```bash
sudo bash /opt/open-node/uninstall.sh
```

提示 `是否彻底清除以上数据？[Y/n]` 时，直接回车默认清除，输入 `n` 才保留。脚本必须
连接交互式 TTY。

安装失败先运行 `status`，再看默认容器日志：

```bash
sudo bash /opt/open-node/install.sh status
sudo docker logs --tail 200 open-node-open-node-1
sudo docker logs --tail 200 open-node-public-gateway
sudo ss -ltnp | grep -E ':(443|58090|62031)\b'
curl -fsS https://PUBLIC_IP:58090/healthz
```

不要使用 `-k`。没有启用公网网关时不存在 `open-node-public-gateway`；自定义项目名、目录
或端口时，以 `status` 和私有安装清单显示的身份为准。

## 完整工程参考

The root Dockerfile builds React/Ant Design assets and installs FastAPI in one
image. FastAPI serves both the application and API on container port 62031; Node and Vite
are build tools, not production services. No activation or paid license is
required. This package is usable for the implemented workflows, not a claim
that every MMWX migration gate is complete. See [migration-map.md](migration-map.md).

## Requirements

- A Linux Docker host with Docker Compose v2, Git, and outbound access to
  Docker Hub, npm, PyPI, GitHub, and the selected ACME/DNS services.
- For the fresh managed-public default: an operator-controlled public IPv4 or
  IPv6, free inbound TCP 443 and the IP HTTPS service port (58090 by default),
  and outbound access to public ACME. TCP 443 is used by TLS-ALPN-01; TCP 80 is
  not required. A DNS hostname is optional. Hosts with an existing edge proxy
  should keep using the manual mode below.
- Backups on a private filesystem outside the application's volume.

The shipped setup uses one backend process, either one host-local SQLite database or one
installer-managed PostgreSQL 15 service, and one certificate worker. Do not scale the service
or add Uvicorn workers:
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

On a fresh installer identity, omitted `OPEN_NODE_PUBLIC_IP` means `auto`. The
installer discovers the actual public IPv4 or IPv6, persists the resolved
literal, obtains a trusted short-lived certificate, and reports
`https://IP:58090` (bracketing IPv6) only after the public gateway passes its
certificate and health checks. The application itself remains available only
at host loopback `127.0.0.1:62031`, mapped to container port `62031`.
For the managed IP endpoint, a plaintext request to `http://IP:58090` receives
a permanent `308` redirect to the same path and query on `https://IP:58090`.

The anonymous Raw URL and public `main` clone were validated on 2026-08-30 in an
unused Debian 12 VPS namespace. The release check covered fresh installation,
administrator API login, status, same-revision update, data-preserving uninstall,
and exact fixture cleanup. The separate maintainer smoke covers rollback and
failure injection as described below.

SQLite is the default. For a fresh PostgreSQL deployment, use the same fully downloaded
installer and select the backend on its first invocation:

```bash
sudo env OPEN_NODE_DATABASE_BACKEND=postgresql bash "$installer"
```

The installer generates a 256-bit alphanumeric PostgreSQL password, writes it only to the
mode-`0600` environment, starts the pinned official PostgreSQL 15 image on the private Compose
network, waits for database health, and publishes no database port. An operator-supplied
`OPEN_NODE_POSTGRES_PASSWORD` is allowed only at first install and must contain 32–128 ASCII
letters or digits. The selected backend is recorded in the installer manifest and cannot be
changed by `update` or `reinstall`. This is a fresh-deployment choice, not an SQLite-to-PostgreSQL
or old-MMWX migration path.

Before application SQL or a browser restore can run, Open Node verifies the dedicated
`open_node` role and self-demotes the official image's bootstrap role to non-superuser. It retains
only `CREATEDB`, which is required for the isolated staging-database create/drop/rename workflow;
`CREATEROLE`, replication, row-security bypass and inherited role memberships are refused. The
installer checks this role contract again after the application becomes healthy.

The supported PostgreSQL recovery contract is deliberately narrow: restore a backup made by the
same Open Node source revision with that revision's exact image and configuration. A normal,
reviewed update may run that release's own schema changes, but this does not make arbitrary older
or newer PostgreSQL dumps restore-compatible. No legacy MMWX import, cross-backend conversion or
cross-schema-version recovery is promised.

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
4. validates the candidate Compose image and data volume(s), builds a
   transaction-unique `source-<revision>-<transaction>` image, starts it without
   rebuilding, and verifies that the container uses the recorded image ID, has
   the requested published binding, and passes `/healthz`; and
5. for an enabled managed-public identity, provisions pinned Caddy, validates
   TLS-ALPN-01 on TCP 443 and the trusted canonical HTTPS health endpoint without
   falling back to HTTP, a self-signed certificate, or private-only success; and
6. by default, exposes [browser initialization](initial-setup.md) without a setup
   credential. The first visitor can create the administrator, so initialize the
   panel immediately after installation. An explicit private password file retains
   terminal provisioning. `OPEN_NODE_CREATE_ADMIN=1` retains the interactive
   `/dev/tty` password flow; `0` skips automatic initialization guidance.

The fresh public-IP default pins the official Caddy image by digest, configures
secure cookies, exact proxy trust and the canonical Agent public URL, and starts
a hardened host-network HTTPS gateway. Supplying an optional
`OPEN_NODE_PUBLIC_HOSTNAME` adds the hostname entry and makes
`https://hostname` canonical while retaining the IP URL. The complete operator
flow is in [公网一键部署](public-deployment.md).

The fresh application listener is `127.0.0.1:62031`. If managed public access
was explicitly disabled, open a tunnel from your workstation and then visit
`http://127.0.0.1:62031`:

```bash
ssh -L 62031:127.0.0.1:62031 root@SERVER_IP
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
script. Normal browser administrator creation needs no credential. `setup` issues
a credential only for the advanced pre-administrator backup-restore path;
`create-admin` creates the administrator through the terminal. Neither overwrites
an account:

```bash
(
  action=status # Change to update, uninstall, setup, or create-admin as required.
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
`OPEN_NODE_DATABASE_BACKEND` (fresh install only),
`OPEN_NODE_HTTP_PORT`, `OPEN_NODE_INSTALL_DIR`, `OPEN_NODE_CONFIG_DIR`,
`OPEN_NODE_BACKUP_DIR`, `OPEN_NODE_PROJECT_NAME`, and
`OPEN_NODE_PUBLIC_IP`, `OPEN_NODE_PUBLIC_HTTPS_PORT`, and
`OPEN_NODE_PUBLIC_HOSTNAME`. A public plain-HTTP bind
requires both `OPEN_NODE_BIND_ADDRESS=0.0.0.0` and the explicit
`OPEN_NODE_ALLOW_PUBLIC_HTTP=1` opt-in; it is not the recommended production
topology. For a custom proxy, set
`OPEN_NODE_SESSION_COOKIE_SECURE=true` and configure the exact trusted proxy as
described below.

### Managed public IP and optional hostname

`OPEN_NODE_PUBLIC_IP` accepts exactly `auto`, `off`, or a public IPv4/IPv6
literal. Fresh install defaults to `auto`, resolves it, and stores the literal
result. `OPEN_NODE_PUBLIC_HTTPS_PORT` controls the IP HTTPS endpoint and
defaults to 58090.

`OPEN_NODE_PUBLIC_HOSTNAME` remains optional and contains only a lowercase
ASCII/punycode hostname, without scheme, port, path, credentials, query or
fragment. If present, `https://hostname` is the canonical control-plane URL and
the IP URL remains a secondary entry. Without it, the canonical URL is
`https://IP:<public-https-port>`.

IP and hostname are independent entries. To close every managed public entry,
both values must be explicit:

```bash
sudo env OPEN_NODE_PUBLIC_IP=off OPEN_NODE_PUBLIC_HOSTNAME= \
  bash /path/to/reviewed/install.sh update
```

Managed issuance always requires reachable TCP 443 for TLS-ALPN-01. It does not
require TCP 80. The configured IP HTTPS service port (58090 by default) must
also be reachable for the IP URL. Discovery, challenge, certificate, canonical
URL or health failure aborts the operation; the installer does not silently
downgrade to plaintext, a local certificate, a different public identity or a
private-only success.

This installer deliberately targets a new or already installer-managed,
single-host Docker deployment using SQLite or the pinned managed PostgreSQL service. It does not adopt an existing manual
Compose installation, merge data, manage public DNS or take over an existing proxy,
restore a backup, prune retained images/backups, install or migrate remote
Agents, change database backends, or claim support for the upstream MMWX native binary,
embedded Nginx, Windows, rootless Docker, multi-host or multi-worker operation.

### Enable panel-issued Agent commands

Managed public installation sets this value automatically to its canonical
HTTPS URL: the hostname when configured, otherwise the IP URL with its port.
With a custom trusted HTTPS reverse proxy, supply the canonical control-plane
URL to the reviewed root installer. For an existing **installer-managed**
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
setting alone neither creates HTTPS infrastructure nor installs Agents automatically.
Managed Caddy is enabled by at least one of an active `OPEN_NODE_PUBLIC_IP` or a
non-empty `OPEN_NODE_PUBLIC_HOSTNAME`.
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

The public-gateway policy smoke creates a UUID-named Caddy container with
`docker create` but never starts it. It checks the installer's exact image,
command, capability, host-network, read-only, tmpfs, mount, label and volume
policy, including safe IP/hostname transitions and removal, then removes only the
verified fixture resources:

```bash
sudo python3 scripts/vps/smoke-installer-public-gateway.py \
  --repository "$PWD" --output /tmp/open-node-public-gateway-policy
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
curl --fail http://127.0.0.1:62031/healthz
```

For a manual PostgreSQL deployment, generate and store a private alphanumeric password, set
`OPEN_NODE_DATABASE_BACKEND=postgresql`, set
`OPEN_NODE_DATABASE_URL=postgresql+psycopg://open_node:PASSWORD@postgres:5432/open_node`, and
set the same `OPEN_NODE_POSTGRES_PASSWORD`. Add `-f deploy/compose.postgresql.yaml` to every
Compose command. The PostgreSQL service has no host port and its `postgres-data` volume is
separate from the application-state `data` volume. Prefer the installer unless you are prepared
to own these identities and the two-volume recovery procedure.

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

Proxy Provider GeoIP filtering is optional. To enable it, put a private IPinfo token in
`OPEN_NODE_GEOIP_IPINFO_TOKEN` in `deploy/.env`. The installer preserves the value across
updates and passes it only to the application container. Leave it empty if GeoIP filtering
is not used; ordinary name/protocol Provider filters do not need a third-party account.
Node IPs or resolved public addresses are sent to IPinfo only while a selected Provider has
a GeoIP condition. See [subscription customizations](subscription-customizations.md).

The port is bound to host loopback only. The service runs as UID/GID 10001,
with no capabilities, no privilege escalation, a read-only image filesystem,
a temporary `/tmp`, and a private named volume at `/var/lib/open-node`.
With SQLite, the database, certificate vault key, and lego account data live in that volume.
With PostgreSQL, application files and keys remain there while database pages live in the
separate private `postgres-data` volume.
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
`OPEN_NODE_TRUSTED_PROXIES` in `deploy/.env` to that exact address. Also set
`OPEN_NODE_TRUSTED_AUTHORITIES` to a JSON array containing the container health
authority and every exact browser-facing authority. For example:

```dotenv
OPEN_NODE_TRUSTED_AUTHORITIES=["127.0.0.1:62031","panel.example.com"]
```

Authority entries contain no scheme or path. Include a non-default public port
when one is used (`panel.example.com:58090`), and bracket an IPv6 literal
(`[2001:db8::10]:58090`). Never use a wildcard. The managed installer derives
this list automatically for its IP, domain, or dual-entry gateway; this manual
setting is required only when operating your own proxy.

Apply both settings:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-build --wait
```

Keep `OPEN_NODE_SESSION_COOKIE_SECURE=true`. Open your HTTPS hostname and
sign in. Test a direct page reload at `/servers?tab=egress`, and verify the Agent's
`wss://` endpoint if attaching nodes. HTTPS Origin validation requires the
original scheme and Host. A 400 response indicates that the request Host is
absent, malformed, or not in `OPEN_NODE_TRUSTED_AUTHORITIES`. A 403 after login
can indicate a missing proxy trust setting or an Origin that does not exactly
match the browser-facing authority. Do not fix either condition by trusting
wildcards, allowing arbitrary Origins, or disabling CSRF.

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

Back up the entire application-state volume while the service is stopped, not just the
database. The certificate, external-subscription, federation and notification vault keys are
required to decrypt their database records, and
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

That manual example is for SQLite. For PostgreSQL, a filesystem copy of a live `postgres-data`
volume is not the documented logical backup. Use the administrator v1 backup or the installer
update transaction, both of which run the image's official `pg_dump` custom format and verify it
by restoring into a temporary database. The installer recovery bundle contains both
`volume.tar.gz` for application state and `postgres.dump` for the database, plus the PostgreSQL
Compose overlay and hashes.

Check every command's exit status. If archive creation fails, restart the
service, retain the previous backup, and do not proceed with an upgrade.
Record the deployed image ID/tag, source revision, Compose configuration,
and proxy configuration alongside the backup. Prefer retaining the exact
image artifact (`docker image save`) to rebuilding an old source revision.

For restoration of that manual **SQLite** archive, use a fresh, uniquely named Compose project and an unused
loopback port. Create a separate `deploy/.env.restore` with the original image
tag, an explicitly selected `OPEN_NODE_HTTP_PORT`, and an initially empty
trusted proxy value. Set `OPEN_NODE_TRUSTED_AUTHORITIES=[]` while this disposable
instance remains reachable only through its private loopback URL. The restore
file must contain the port; do not rely on the
normal 62031 default or copy the production port. Do not change the running deployment's environment file.
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
Official `main` installations on a systemd host can request this same operation
from the Chinese administrator page. The fixed-function handoff, exact revision
binding and availability rules are documented in
[网页内应用更新](application-updates.md). Manual/custom deployments continue to
use the CLI and do not receive a Docker socket or general root command bridge.
Each real update builds and inspects a transaction-unique image while the old
container can continue running. If the fetched revision is already deployed,
the command is a no-op: it does not rebuild an image, stop a container, create a
backup, or replace the previously retained recovery artifacts. It checks health
only when the current container is running.

Before a candidate is started, the installer records the old container state,
stops it if necessary, and creates a new private bundle under
`OPEN_NODE_BACKUP_DIR`. The bundle contains:

- `volume.tar.gz`, a validated archive of the stopped data volume;
- for PostgreSQL, `postgres.dump`, validated by `pg_restore --list` and a complete restore into a
  temporary `template0` database, plus `compose.postgresql.yaml`;
- `open-node.env`, `installer.manifest`, and the deployed `compose.yaml`; and
- `deployment.meta`, including the old revision, tag, immutable image ID,
  Compose project/volume identity, creation time and a transaction-specific
  local rollback-image tag.

The old image is tagged in the local Docker store; its bytes are not embedded
in the bundle. Export that image separately for off-host or Docker-store-loss
recovery. Bundles are never silently overwritten or pruned.

#### Restore an installer PostgreSQL recovery bundle

This procedure is for a trusted bundle created by `backup_stopped_volume`, not a Web v1 package.
It deliberately creates a unique Compose project, two new empty volumes, a required operator-selected
unused loopback port
and an internal-only Docker network. It never edits the installed environment or names the
production project/volumes. Use the exact image ID and revision recorded in the bundle; if the
image is no longer in the Docker store, first load the separately retained `docker image save`
archive and verify that it produces that ID.

Run as root in Bash. Replace `DR_BUNDLE` and export a required, unused
`OPEN_NODE_RESTORE_HTTP_PORT`; there is intentionally no restore-port default.
Do not point `DR_BUNDLE` at a directory that is writable by untrusted users. `SHA256SUMS` detects damaged or
changed bundle members but is not a signature, so the bundle source must already be trusted.

```bash
set -euo pipefail
umask 077

DR_BUNDLE=/var/backups/open-node/open-node-REPLACE-ME
: "${OPEN_NODE_RESTORE_HTTP_PORT:?Select an unused loopback restore port}"
DR_PORT="$OPEN_NODE_RESTORE_HTTP_PORT"
DR_PROJECT="open-node-dr-$(date -u +%Y%m%d%H%M%S)-$$"
DR_ROOT="$(mktemp -d /var/tmp/open-node-dr.XXXXXX)"
DR_ENV="$DR_ROOT/open-node.env"
DR_ISOLATION="$DR_ROOT/compose.isolation.yaml"
[[ "$DR_PORT" =~ ^[0-9]{2,5}$ ]]
(( DR_PORT >= 1024 && DR_PORT <= 65535 ))

test -d "$DR_BUNDLE" && test ! -L "$DR_BUNDLE"
for artifact in SHA256SUMS compose.yaml compose.postgresql.yaml deployment.meta \
  installer.manifest open-node.env volume.tar.gz postgres.dump; do
  test -f "$DR_BUNDLE/$artifact" && test ! -L "$DR_BUNDLE/$artifact"
done
(cd "$DR_BUNDLE" && sha256sum --check SHA256SUMS)

read_meta() {
  local key="$1"
  awk -F= -v key="$key" '
    $1 == key { value = substr($0, length(key) + 2); found++ }
    END { if (found != 1) exit 1; print value }
  ' "$DR_BUNDLE/deployment.meta"
}

DR_REVISION="$(read_meta REVISION)"
DR_IMAGE_ID="$(read_meta IMAGE_ID)"
test "$(read_meta DATABASE_BACKEND)" = postgresql
[[ "$DR_REVISION" =~ ^[0-9a-f]{40}$ ]]
[[ "$DR_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "$DR_IMAGE_ID" >/dev/null
test "$(docker image inspect --format \
  '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$DR_IMAGE_ID")" = "$DR_REVISION"

DR_IMAGE_REPOSITORY=open-node-dr
DR_IMAGE_TAG="$DR_PROJECT"
! docker image inspect "$DR_IMAGE_REPOSITORY:$DR_IMAGE_TAG" >/dev/null 2>&1
docker image tag "$DR_IMAGE_ID" "$DR_IMAGE_REPOSITORY:$DR_IMAGE_TAG"

install -m 0600 "$DR_BUNDLE/open-node.env" "$DR_ENV"
mkdir -m 0700 "$DR_ROOT/maintenance"
{
  printf '\nOPEN_NODE_IMAGE_REPOSITORY=%s\n' "$DR_IMAGE_REPOSITORY"
  printf 'OPEN_NODE_IMAGE_TAG=%s\n' "$DR_IMAGE_TAG"
  printf 'OPEN_NODE_REVISION=%s\n' "$DR_REVISION"
  printf 'OPEN_NODE_BIND_ADDRESS=127.0.0.1\n'
  printf 'OPEN_NODE_HTTP_PORT=%s\n' "$DR_PORT"
  printf 'OPEN_NODE_TRUSTED_PROXIES=\n'
  printf 'OPEN_NODE_TRUSTED_AUTHORITIES=[]\n'
  printf 'OPEN_NODE_PUBLIC_IP=off\n'
  printf 'OPEN_NODE_PUBLIC_HTTPS_PORT=58090\n'
  printf 'OPEN_NODE_PUBLIC_HOSTNAME=\n'
  printf 'OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=\n'
  printf 'OPEN_NODE_APPLICATION_UPDATE_HOST_DIR=%s\n' "$DR_ROOT/maintenance"
} >> "$DR_ENV"
printf '%s\n' 'networks:' '  default:' '    internal: true' > "$DR_ISOLATION"

dr_compose() {
  docker compose --env-file "$DR_ENV" --project-name "$DR_PROJECT" \
    -f "$DR_BUNDLE/compose.yaml" \
    -f "$DR_BUNDLE/compose.postgresql.yaml" \
    -f "$DR_ISOLATION" "$@"
}

dr_compose config --quiet
DR_DATA_VOLUME="$(dr_compose config --format json | jq -er '.volumes.data.name')"
DR_POSTGRES_VOLUME="$(dr_compose config --format json | jq -er '.volumes["postgres-data"].name')"
test -z "$(docker ps -aq --filter "label=com.docker.compose.project=$DR_PROJECT")"
! docker volume inspect "$DR_DATA_VOLUME" >/dev/null 2>&1
! docker volume inspect "$DR_POSTGRES_VOLUME" >/dev/null 2>&1

dr_compose create --no-build postgres open-node
docker run --rm --network none --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=volume,src=$DR_DATA_VOLUME,dst=/var/lib/open-node" \
  --entrypoint python "$DR_IMAGE_ID" -c \
  'import pathlib; raise SystemExit(0 if not any(pathlib.Path("/var/lib/open-node").iterdir()) else 1)'

dr_compose up -d --no-build --wait postgres
dr_compose exec -T postgres sh -ceu '
  export PGPASSWORD="$POSTGRES_PASSWORD"
  test "$(psql --username open_node --dbname open_node --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --command \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()")" = 0
'
dr_compose exec -T postgres pg_restore --list < "$DR_BUNDLE/postgres.dump" >/dev/null
dr_compose exec -T postgres sh -ceu '
  export PGPASSWORD="$POSTGRES_PASSWORD"
  exec pg_restore --username open_node --dbname open_node --exit-on-error \
    --single-transaction --no-owner --no-privileges
' < "$DR_BUNDLE/postgres.dump"
dr_compose exec -T postgres sh -ceu '
  export PGPASSWORD="$POSTGRES_PASSWORD"
  test "$(psql --username open_node --dbname open_node --tuples-only --no-align \
    --set ON_ERROR_STOP=1 --command \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()")" -gt 0
'

docker run --rm -i --network none --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=volume,src=$DR_DATA_VOLUME,dst=/var/lib/open-node" \
  --entrypoint tar "$DR_IMAGE_ID" -C /var/lib/open-node -xzf - \
  < "$DR_BUNDLE/volume.tar.gz"

dr_compose up -d --no-build --wait open-node
DR_CONTAINER="$(dr_compose ps -q open-node)"
test -n "$DR_CONTAINER"
test "$(docker inspect --format '{{.Image}}' "$DR_CONTAINER")" = "$DR_IMAGE_ID"
curl --fail --show-error --silent "http://127.0.0.1:$DR_PORT/healthz"
```

The PostgreSQL password remains only in the private copied environment and the PostgreSQL
container environment; no command expands it into an argument or terminal output. The internal
network intentionally blocks Agent, ACME, notification and subscription egress during review.
Keep the original project stopped and unchanged. Verify the recovered revision, administrator
login, inventory, certificate-key access and database counts before planning a separate, explicit
traffic cutover. On failure, `dr_compose down` removes only this recovery project's containers and
network and preserves both recovery volumes for inspection; do not add `--volumes` until you have
decided the recovery copy is disposable.

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

While that marker exists, `install`, `update`, `setup`, and `create-admin` fail closed;
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
