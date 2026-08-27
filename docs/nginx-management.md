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

Nginx starts only after an explicit install/start or tunnel-deploy command and retains its own
start/stop intent independently of Xray. Use the provided systemd installation;
Nginx execution through a root-running manual Agent is rejected. Static content
must be readable by the dedicated account, and the hardened service cannot
read home directories. Unrelated host listener ports are never taken over.

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

## Atomic Tunnel Deployment

The Config workspace's Runtime tab uses
`POST /api/v1/servers/{id}/xray/runtime/tunnel-deploy`. A current Agent scan
with `nginx.tunnel_deploy: 1` selects the `open-node` runtime profile. The
Agent must have an operator-supplied Nginx binary and managed Xray runtime.
Older managed Agents must be upgraded; an absent native scan retains the
separate legacy MMWX template path. Preview responses expose `runtime_profile`.

Deploy the certificate/key into the owned certificate directory first, then
read the current Xray configuration. A minimal queued request is:

```json
{
  "domain": "example.com",
  "queue_agent_commands": true,
  "queue_scan_after_apply": true
}
```

Existing non-template Xray entries require explicit `force: true`. This is a
replacement Xray template, not a merge of arbitrary proxy protocols. Force
does not bypass certificate/config validation or the canonical snapshot hash:
if the node config changes before execution, refresh its snapshot and plan again.

The template uses the official Xray v26.3.27 `tunnel` schema (`address`, `port`,
`network`), TLS sniffing with `routeOnly`, exact-domain SNI routing, and a
PROXY-protocol connection to Nginx. Defaults are:

- Public Xray listener: `listen_address: 0.0.0.0`, `listen_port: 443`.
- Internal Nginx TLS/PROXY listener: loopback `nginx_port: 8001`.
- Unmatched traffic: fixed loopback `forward_port: 46174`.
- Xray API and metrics: loopback `api_port: 46736`, `metrics_port: 38889`.

All five ports are configurable, distinct, and within 1-65535. The operator
must provide a service on the fallback port; deployment does not invent one.
The Agent discovers a loopback `api.listen` for statistics when `stats_address`
is unset. An explicit `stats_address` must match the generated API listener.
The legacy profile rejects customized listener settings.

`site_type: static` uses the scanned owned HTML directory when `site_value`
is omitted, initializing `index.html` only when absent. Additional static roots
must be allowed in the Agent settings. `site_type: proxy` requires a URL in
`site_value`; upstream HTTPS verification uses Debian's system CA bundle.
`cert_name` defaults to the domain and names the existing `.pem`/`.key` pair.
`proxy_domain` remains legacy response metadata, not another native SNI route.

One `/api/child/tunnel/deploy` command merges the required HTTP map/include
into the owned Nginx main config, preserving unrelated sites and stream blocks.
`clear_stream_port: true` removes only the matching owned stream listener.
Nginx and Xray configs are validated before runtime activation. Default
`restart_xray: true` starts both services even if previously stopped. With
`restart_xray: false`, Nginx is activated but Xray is not restarted and the
result reports `restart_required`; the new Xray file takes effect on a later start.

Files and previous service-start intentions share a durable undo record. An
activation failure restores both configs and their prior running/stopped state;
interrupted transactions recover before either service is started again.
Successful deployment refreshes the control-plane Xray snapshot. This is a
recoverable configuration transaction, not zero-downtime deployment or atomic
network traffic switching. Existing sessions can be interrupted during restart.

## Failure Behavior

Certificate issuance, renewal and version management live in the
[control-plane certificate workspace](certificates.md). The Agent receives
validated deployment commands; it does not run a separate ACME client.


Configuration parsing uses [NGINX crossplane](https://github.com/nginxinc/crossplane).
Includes are expanded only from the owned directory, with cycle/count/size
limits and symlink/hardlink rejection. The Agent owns daemon/master settings,
PID, modules, logs, and temporary paths. Static `root`/`alias` and certificate
paths have their own boundaries; dynamic variables in those paths are rejected.
Do not paste a legacy global configuration
without adapting its paths and removing Agent-controlled directives.

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
