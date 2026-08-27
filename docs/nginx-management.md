# Nginx And Certificates

Open Node can run a dedicated Nginx master under its non-root Agent account.
It does not discover, signal, edit, or uninstall the host's existing Nginx.
The host installer copies an operator-supplied binary and optional dynamic
modules into the root-owned runtime directory. No license or activation service
is involved. Debian 12 x86-64 with Nginx 1.22.1 is the verified configuration.

## Prepare The Host

Add these optional settings to the private input file used by the
[Agent deployment CLI](agent-deployment.md):

```yaml
nginx_binary: /usr/sbin/nginx
# Only modules built for this exact Nginx binary may be supplied:
nginx_modules:
  - /usr/lib/nginx/modules/ngx_stream_module.so
nginx_listen_address: 0.0.0.0
nginx_http_port: 80
nginx_https_port: 443
# Optional additional directories that Nginx may serve as static content:
nginx_site_roots:
  - /srv/open-node-sites
```

Leave `nginx_modules` empty when dynamic stream support is not needed. The
binary's shared libraries must already be installed on the host. This feature
does not run a distribution package manager remotely. Source binary/module
paths are copied during initial host installation; uninstall/reinstall preserves
the copies. Existing installations can add the optional settings and runtime
files as the host administrator, retaining the documented account ownership.

Nginx starts only after an explicit install/start command and retains its own
start/stop intent independently of Xray. Use the provided systemd installation;
Nginx execution through a root-running manual Agent is rejected. Static content
must be readable by the dedicated account, and the hardened service cannot
read home directories. Existing listener ports are never taken over.

For the default installation, the owned paths are:

- Configuration: `/opt/open-node-agent/config/nginx/nginx.conf`.
- Site files: `/opt/open-node-agent/config/nginx/servers/*.conf`.
- Stream files: `/opt/open-node-agent/config/nginx/stream_servers/*.conf`.
- Certificates: `/opt/open-node-agent/config/certificates/`.
- Default content: `/opt/open-node-agent/state/nginx/html/`.
- Effective runtime configuration: `/opt/open-node-agent/state/nginx/effective.conf`.
- Rotated runtime log: `/opt/open-node-agent/state/nginx.log`.

Custom installation roots use the corresponding paths. Service status,
install results, website listings, and the latest private scan report expose
the actual paths. These directories are not the legacy MMWX Nginx paths.

## Operator Commands

The existing authenticated `/api/v1/servers/{id}/operations/` endpoints queue
the following commands; inspect the completed command result before proceeding:

- `nginx/install`: initialize an HTTP site using the optional `domain`, then
  start the owned runtime. Existing configuration and content are preserved.
- `nginx/config/read`, `nginx/config/write`: read or validate/write the main
  configuration. Writes do not reload a running service automatically.
- `nginx/config-files/list`, `read`, `write`: use `file` for reads and
  `path`/`content` for writes. Paths can be relative to the owned config directory.
- `nginx/setup-ssl`: takes `domain` and optional `nginx_config`/`domain_config`.
  With no custom site config it uses `<domain>.pem` and `<domain>.key` from the
  certificate directory and enables TLS 1.2/1.3. Supply certificates first.
- `nginx/websites/list`, `nginx/servers-list`, `nginx/websites/delete`: list
  owned site files or delete one domain file and reload. Certificates and site
  content remain untouched by site deletion.
- `nginx/clear-stream-port`: remove matching server blocks from owned
  `stream_servers` files while preserving other server blocks and exact ports.
- `validate-site`: check a static `index.html` inside an allowed content root,
  or HTTP(S) reachability of a proxy target. TLS verification remains enabled.
- `nginx/remove`: stop/deactivate the runtime, retaining configuration,
  certificates, logs, binaries, and content. The host deployment CLI handles
  Agent uninstall or explicit purge, not unrelated system packages.

The common service-control endpoint accepts `service: nginx` with `start`,
`stop`, `restart`, or `reload`. Log reads accept `service=nginx`. Stream and
legacy install/remove RPC paths invoke the same implementation.

## Certificate Deployment

Queue `cert/deploy` with a domain, PEM certificate chain, unencrypted PEM private
key, two destination paths, and `reload: nginx|xray|both|none`. Relative paths
are rooted in the owned certificate directory, for example:

```json
{
  "domain": "example.com",
  "cert_pem": "REPLACE_WITH_CERTIFICATE_PEM",
  "key_pem": "REPLACE_WITH_PRIVATE_KEY_PEM",
  "cert_path": "example.com.pem",
  "key_path": "example.com.key",
  "reload": "nginx"
}
```

Deployment checks the leaf public key against the private key, SAN coverage
(including single-label DNS wildcards), validity dates, and TLS server key usage
when present. Files stay private (`0600`); returned results contain certificate
metadata and paths, never the private key. Xray configs can reference these same
absolute paths. Reloading preserves intentionally stopped services. Public-CA
issuance, ACME challenges, automatic renewal, and trust-chain issuance policy
are not implemented by this endpoint. It deploys supplied certificates.

## Failure Behavior

Configuration parsing uses [NGINX crossplane](https://github.com/nginxinc/crossplane).
Includes are expanded only from the owned directory, with cycle/count/size
limits and symlink/hardlink rejection. The Agent owns daemon/master settings,
PID, modules, logs, and temporary paths. Static `root`/`alias` and certificate
paths have their own boundaries. Do not paste a legacy global configuration
without adapting its paths and removing Agent-controlled directives.
The existing high-level tunnel-deployment templates still target the MMWX
fork and its global Nginx layout; they are not an independently verified
Open Node tunnel migration path. Use the owned site/config commands above
for the Nginx functionality described here.

Changes have a private, fsynced undo record before any file is replaced. The
Agent runs `nginx -t` against the effective config and observes new workers
after [HUP reload](https://nginx.org/en/docs/control.html), including rejection
when a new port is occupied. Failed validation/reload restores the previous
files; affected running services are restored after certificate failures.
Command cancellation also awaits rollback. An interrupted file transaction
is undone before runtimes start on the next Agent process.

The default systemd cgroup contains master and worker processes. The Agent also
cleans its own process group when a Nginx master dies, before restarting it.
The [VPS smoke](testing.md#nginx-and-certificate-smoke) verifies real HTTP/TLS,
certificate rotation, static/proxy/stream traffic, failure recovery, both
transports, and data-preserving removal without touching existing services.
