# Agent Deployment

For a new Debian 12 amd64 host, the [panel-issued installation command](agent-bootstrap.md)
downloads and verifies the pinned Agent/Xray artifacts, prepares private inputs
and invokes this host installer. The manual workflow below remains available
for operator-supplied runtimes and explicitly reviewed configurations.

The repository includes a root-only deployment CLI for Linux hosts with Python
3.11+, `venv`, systemd, `useradd`, and `runuser`. It installs the independent
Open Node Agent with an operator-provided Xray binary. No activation code or
license service is used. Debian 12 x86-64 is the verified host configuration.

This deploys the Agent and its owned Xray child, not the FastAPI/React control
plane. The control plane must already be reachable and have issued this node's
token. Do not reuse a production token while its previous agent is still running.
For a separately owned existing Xray service, use the
[external systemd setup](external-systemd.md), not this managed-child installer.

使用控制面生成的 Agent 命令前，先等控制面安装器真正完成。在默认公网模式中，受管
Caddy 自动把公网 `https://IP:58090` 反向代理到宿主回环 `127.0.0.1:62031`，再进入
应用容器的 `62031/tcp`。控制面安装器会等待可信 HTTPS 证书、规范 URL 和 `/healthz`
连续通过，最后才输出 `ACTION_COMPLETE action=install`；在出现该标记前，不应生成或
执行远端 Agent 安装命令。

Optional [Nginx and certificate management](nginx-management.md) runs another
owned child under this account. Set `nginx_binary` and optional `nginx_modules`
in the installation input to copy those runtime files, without replacing any
existing Nginx service. Its desired running state participates in readiness.

## Prepare

Use a trusted wheel built from this repository or the verified
[0.3.0a2 prerelease](releases/agent-0.3.0a2.md). The current VPS test runner builds
`agent/dist/open_node_agent-0.3.0a2-py3-none-any.whl`. The bootstrap deployment
script uses only Python's standard library; package dependencies are installed
into a separate virtual environment for each release, never the system Python.

Prepare a private Agent input file, for example `/root/open-node-agent.yaml`:

```yaml
master_url: https://control.example.com
token: REPLACE_WITH_THE_NODE_TOKEN
connection_mode: auto
runtime_mode: managed
# Configure this only when the supplied Xray config enables StatsService:
# stats_address: 127.0.0.1:46736
```

Set that file's permissions to `0600`. Supply a working Xray JSON configuration
and a trusted, executable Xray binary appropriate for the host architecture.
The installer copies both into its own directory; it does not stop, overwrite,
or take ownership of a pre-existing MMWX/Xray installation. Resolve listener
port conflicts before installing. For geodata-dependent configs, provide
`--asset-dir` containing `geoip.dat` and/or `geosite.dat`.

TLS certificates and other external paths referenced by Xray must be readable
by the dedicated service account. Files under home directories are unavailable
to the hardened service. A configured Agent `ca_file` is copied automatically
into the private configuration directory. HTTPS certificate checks remain on;
`allow_insecure_http` is only for isolated testing or a trusted SSH tunnel.

## Install

Run from the repository checkout:

```bash
sudo python3 agent/app/open_node_agent/service.py install \
  --wheel agent/dist/open_node_agent-0.3.0a2-py3-none-any.whl \
  --config /root/open-node-agent.yaml \
  --xray-config /root/xray.json \
  --xray /usr/local/bin/xray
```

The default root is `/opt/open-node-agent` and the service is
`open-node-agent.service`. Both must be unused unless this installer already
owns them. Existing manual installations are not silently adopted. For a
separate instance, supply both global options before `install`:

```bash
sudo python3 agent/app/open_node_agent/service.py \
  --root /opt/open-node-agent-edge --unit open-node-agent-edge.service \
  install --wheel /path/to/open_node_agent-0.3.0a2-py3-none-any.whl \
  --config /root/edge.yaml --xray-config /root/edge-xray.json --xray /usr/local/bin/xray
```

The installer creates a matching non-login service account. It restricts
writes to the Agent's configuration/state directories and gives the service
only the capability needed for low-numbered listener ports by default.
The optional initial-install `--network-diagnostics` flag adds `CAP_NET_RAW`
for [ICMP and return-route diagnostics](agent-diagnostics.md); it does not
grant root or network administration privileges. Existing installations use
the root-only [host policy command](agent-host-policy.md) to change this setting
without reinstalling. `KillMode=control-group`
contains the owned Xray child if the Agent exits abruptly. Agent program files,
the bootstrap runtime and installation metadata remain root-owned; tokens,
config files, and the execution journal remain private. Service definitions or
external systemd overrides that
do not match the recorded installation cause updates/removal to stop for review.

Remote [Xray release management](xray-releases.md) retains that root-owned
bootstrap binary and uses a separate checksum-verified release cache in the
Agent-owned state directory. It does not make the Agent package or installation
metadata writable by the Agent. Host upgrade preflight validates the selected
runtime and rejects unresolved runtime switches or incompatible older packages.

Before enabling the service, the installer validates the Agent configuration
and Xray config as the service account. Readiness then checks the systemd PID,
the executing package directory/version, fresh local health data, authenticated
control-plane contact, and the desired Xray running state. An old health file
cannot make a new process ready. The default readiness timeout is 45 seconds;
the global `--timeout` option accepts 3-300 seconds.

## Upgrade And Recover

```bash
sudo python3 agent/app/open_node_agent/service.py upgrade --wheel /path/to/new-agent.whl
sudo python3 agent/app/open_node_agent/service.py rollback
sudo python3 agent/app/open_node_agent/service.py status
```

Each release is identified by its wheel version and SHA-256 digest, so an
artifact cannot overwrite a different build with the same version. Its virtual
environment is created in its final directory, not moved after installation.
Upgrades prepare and validate the candidate while the old service continues
running. Only then is the service stopped and the release pointer switched.
If startup/readiness fails, the previous release is restored and restarted.
Configuration, Xray credentials, and the command journal are not reset.

An intentionally stopped Agent service stays stopped during upgrade/rollback;
only preflight validation runs in that case. A requested Xray stop recorded by
the Agent also survives service restarts. Upgrade does not change an existing
service's boot-enable preference. Normal install enables the new service.

Release-switch transactions are persisted before stopping the service. If the
deployment process is interrupted during a switch, inspect `status` and run:

```bash
sudo python3 agent/app/open_node_agent/service.py recover
sudo journalctl -u open-node-agent.service -n 100 --no-pager
```

Recovery restores the pre-switch release and running state. If rollback cannot
start either, the pending transaction remains available for another recovery
attempt after the underlying fault is corrected. Package staging has a durable
marker too: recovery removes an incomplete owned staging directory, while a
fully recorded release is retained. Partially installed packages are never
treated as healthy. Interrupted removal resumes data-preserving cleanup.

On a failed *first* installation, the owned service is stopped and diagnostic
files remain. Correct the input and retry `install` with all three source
options, or edit the owned config and retry without source options. If `status`
shows a pending switch, run `recover` before retrying. Back up configuration and
state before upgrading across future releases with incompatible data formats;
this mechanism does not promise reversal of arbitrary schema migrations.

## Uninstall

### 中文一键卸载

面板一键安装会保留可校验的 bootstrap helper。可以从官方 `main` 完整下载脚本到临时
文件，再以 root 身份执行：

```bash
(
  uninstaller="$(mktemp)" || exit 1
  trap 'rm -f -- "$uninstaller"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/agent/uninstall.sh -o "$uninstaller" || exit 1
  sudo bash "$uninstaller"
)
```

也可以在已审阅的仓库 checkout 中运行 `sudo bash agent/uninstall.sh`。直接通过下文
Host deployment CLI 安装、没有面板 bootstrap helper 的实例应使用 checkout 中的脚本。
脚本要求标准输入、标准输出和标准错误都连接 TTY，并且只接受身份完整的受管 Agent
安装。发现一个安装时自动选择；发现多个安装时会列出 unit、安装根和状态，并要求输入
编号只选择一个。没有找到候选、选择无效，或安装根、unit、manifest 身份不一致时会拒绝
继续；不会扫描后按通配符删除 `/opt` 下的目录。已知自定义身份也可以显式指定：

```bash
sudo bash agent/uninstall.sh \
  --root /opt/open-node-agent-edge \
  --unit open-node-agent-edge.service
```

完成身份检查后，脚本会显示所选安装根、systemd unit、状态和精确匹配的私有 bootstrap
任务数量，再询问 `是否彻底清除以上数据？[Y/n]`。直接回车或输入 `y` 是默认的彻底
清除；只有输入 `n` 才执行保留数据卸载。其他输入、EOF 或非交互调用均停止，不要用管道
自动回答确认。

- 输入 `n`：停用并删除 Agent unit、当前版本指针和 Agent release 虚拟环境；保留
  `config`、Token、命令 journal、日志、状态、复制的 Xray/Nginx/NextTrace 运行文件、
  安装清单、专用账号、本机 lifecycle helper 和匹配的私有 bootstrap 任务，以便用原
  身份恢复或重装。
- 直接回车：除上述运行资源外，还删除精确受管安装根、专用账号、lifecycle helper
  单元/文件，以及与该安装身份绑定的 `/var/lib/open-node-agent-bootstrap/...` 私有恢复
  目录。原始输入文件、受管根之外的 Xray/Nginx 文件、其他用户 home 和无关 systemd
  服务不在删除范围；删除账号不会使用 `userdel -r`。

本机彻底清除不会登录控制面删除服务器记录或撤销数据库里的 Agent Token，也不会停止
外部 systemd Xray、删除外部 DNS/证书资源或使客户端已经取得的代理凭据失效。先按实际
运行模式撤销公网入站和凭据，再卸载主机 Agent。可变的 Raw `main` URL 也不等于固定
供应链；有严格要求时，应使用已审阅提交的 Raw URL。

### Host deployment CLI

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall
```

This disables/stops the owned service and removes its unit and Agent release
environments. Configuration, journal, logs, the copied Xray runtime/assets, and
the service account remain for recovery. It never follows an installation path
through a symlink or removes an unrelated service definition. Retain a checkout
or trusted copy of the bootstrap script so it is available after uninstall.

Reinstall with `install --wheel /path/to/agent.whl` and no source options to
reuse the preserved data. To explicitly remove the retained installation and
its dedicated account as well:

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall --purge
```

Original input files and the original Xray binary outside the installation root
are never removed. Purge does not call `userdel -r` or remove other home directories.

## Remote Management

The host owner can explicitly enable [remote Agent lifecycle](agent-lifecycle.md)
with `enable-remote`. It uses a root-owned Unix-socket helper and a fixed HTTPS
release source while the Agent itself remains non-root. Remote operations are
checksum-pinned and report completion only after the actual deployment outcome.
Uninstall retains that helper until its final result is acknowledged.

## Coverage Limits

The VPS lifecycle smoke exercises initial failure/retry, real non-root systemd
startup and forwarding, successful upgrade, explicit rollback, failed preflight,
failed startup rollback, interrupted switch recovery, process-group cleanup,
data-preserving uninstall/reinstall, and explicit purge. Unit tests additionally
cover path/ownership guards, stale health reports, and stopped-service behavior.

This installer uses `runtime_mode: managed` only. Control of an independently
managed external Xray systemd unit, broader OS/architecture coverage and WARP
remain separate work. Remote lifecycle has its own opt-in and VPS smoke.
Managed Xray package installation, rollback and data-preserving removal now
have their own [release workflow](xray-releases.md) and real runtime smoke.
See [the migration map](migration-map.md) for the remaining scope.
Owned Nginx operation and certificate deployment are covered by their separate
runtime smokes; [central certificate issuance/renewal](certificates.md) does not
require adding an ACME client to the Agent installation.
