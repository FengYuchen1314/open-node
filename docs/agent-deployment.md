# Agent 部署、升级与恢复

新 Debian 12 amd64 主机应优先使用[面板签发的一键命令](agent-bootstrap.md)。它从控制面
取得并校验固定的 Agent、Xray 和 Mihomo 制品，准备私有输入，安装后等待认证就绪。本页
的 Host deployment CLI 适用于已经审阅仓库和运行时、需要手工指定文件的高级场景。

Agent 是独立的主机服务，不是 FastAPI/React 控制面。控制面必须已经通过可信 HTTPS
完成安装并为这台服务器签发 Token；旧 Agent 仍在运行时不能复用其生产 Token。外部
systemd Xray 应使用[外部 systemd 模式](external-systemd.md)，不要交给 managed-child
安装器静默接管。

> **Preview 迁移边界：** `0.3.0a3` 是首个包含官方 Mihomo 私有运行时的 Agent 制品。
> 尚未公开发布的早期 `0.3.0a2` 测试实例不支持通过仅替换 wheel 补装该运行时，也不承诺
> 原地迁移；应先卸载旧测试实例，再从面板重新签发命令并执行 fresh bootstrap。此限制
> 不影响 `0.3.0a3` fresh bootstrap 后同一制品布局内的正常运行。

默认公网控制面会把 `https://IP:58090` 反向代理到 `127.0.0.1:62031` 和容器
`62031/tcp`。控制面终端出现 `ACTION_COMPLETE action=install` 后，才可生成远端
Agent 命令。

## 手工安装要求

- Linux、Python 3.11+、`venv`、systemd、`useradd` 和 `runuser`；当前验证目标为
  Debian 12 x86-64。
- 来自本仓库的可信 wheel，例如
  `agent/dist/open_node_agent-0.3.0a2-py3-none-any.whl`。
- 与主机架构匹配、已经审阅的 Xray 可执行文件和有效 JSON 配置；需要 geodata 时另备
  `geoip.dat` / `geosite.dat`。
- 与主机架构匹配、已经核对官方 pin 的 Mihomo `v1.19.30` 可执行文件，以及初始的受限
  Mihomo 配置。它们会复制到 Agent 私有安装根，不接管系统 Mihomo。
- 控制面签发的独立 Token。不要把 Token 放进命令行、shell 历史或可被其他用户读取的
  文件。

准备权限为 `0600` 的输入文件，例如 `/root/open-node-agent.yaml`：

```yaml
master_url: https://control.example.com
token: REPLACE_WITH_THE_NODE_TOKEN
connection_mode: auto
runtime_mode: managed
# 仅当 Xray 配置启用 StatsService 时填写：
# stats_address: 127.0.0.1:46736
```

TLS 证书等 Xray 外部路径必须能被专用服务账号读取；硬化服务通常不能访问用户 home。
`ca_file` 会复制进私有配置。生产环境不得启用不安全 HTTP；`allow_insecure_http` 只用于
隔离测试或受信 SSH 隧道。

## 安装

在仓库 checkout 中运行：

```bash
sudo python3 agent/app/open_node_agent/service.py install \
  --wheel agent/dist/open_node_agent-0.3.0a4-py3-none-any.whl \
  --config /root/open-node-agent.yaml \
  --xray-config /root/xray.json \
  --xray /usr/local/bin/xray \
  --mihomo-config /root/mihomo.yaml \
  --mihomo /usr/local/bin/mihomo
```

默认安装根为 `/opt/open-node-agent`，unit 为 `open-node-agent.service`。已有未知目录或
服务不会被自动采用。第二个实例必须同时指定全局 `--root` 和 `--unit`，并把它们放在
动作 `install` 之前：

```bash
sudo python3 agent/app/open_node_agent/service.py \
  --root /opt/open-node-agent-edge \
  --unit open-node-agent-edge.service \
  install \
  --wheel /path/to/open_node_agent-0.3.0a4-py3-none-any.whl \
  --config /root/edge.yaml \
  --xray-config /root/edge-xray.json \
  --xray /usr/local/bin/xray \
  --mihomo-config /root/edge-mihomo.yaml \
  --mihomo /usr/local/bin/mihomo
```

安装器会创建无登录服务账号，把 Xray、Mihomo 和配置复制进自有根目录，不停止或覆盖已有
MMWX/Xray/Mihomo。先处理监听端口冲突。可选 `--asset-dir` 复制 geodata；可选的首次安装参数
`--network-diagnostics` 只增加 ICMP/回程诊断所需的 `CAP_NET_RAW`，不会授予 root 或
网络管理权限。

systemd 使用 `KillMode=control-group` 约束受管 Xray 与 Mihomo 子进程。程序、bootstrap 和安装
元数据由 root 拥有；Token、配置和命令 journal 保持私有。unit 定义、override、安装根
或 manifest 与记录不一致时，升级和删除会停止等待人工检查。

启用服务前，安装器会以专用账号验证 Agent、Xray 和 Mihomo 配置。就绪检查同时核对 systemd PID、
实际执行的 release/version、最新本机健康、控制面认证连接和 Xray 期望状态，旧健康文件
不能让新进程通过。默认超时 45 秒；全局 `--timeout` 支持 3–300 秒。

## 状态、升级和回滚

```bash
sudo python3 agent/app/open_node_agent/service.py status
sudo python3 agent/app/open_node_agent/service.py upgrade --wheel /path/to/new-agent.whl
sudo python3 agent/app/open_node_agent/service.py rollback
```

release 由 wheel 版本和 SHA-256 共同标识，同版本不同内容不能相互覆盖。候选虚拟环境先在
最终目录构建和校验，旧服务此时仍运行；切换失败会恢复并重启上一个 release。配置、Xray
凭据和命令 journal 不会重置。原本主动停止的服务在升级/回滚后仍保持停止，Xray 的显式
停止意图和 systemd enable 状态也会保留。

切换事务会在停服务前落盘。部署进程中断时，先看状态并执行：

```bash
sudo python3 agent/app/open_node_agent/service.py status
sudo python3 agent/app/open_node_agent/service.py recover
sudo journalctl -u open-node-agent.service -n 200 --no-pager
```

`recover` 恢复切换前的 release 和运行状态。若新旧 release 都无法启动，事务标记会保留，
修复底层故障后仍可再次恢复。首次安装失败则停止自有服务并保留诊断文件；修正输入后带
全部 Agent、Xray、Mihomo 源参数重试，或修改已复制的配置后不带源参数重试。有 pending switch 时必须先
`recover`。跨不兼容数据格式升级前应自行备份；release 回滚不承诺逆转任意 schema 迁移。

## 远程生命周期

主机所有者可按[Agent 生命周期](agent-lifecycle.md)显式启用 `enable-remote`。它使用 root
拥有的 Unix socket helper 和固定 HTTPS release 源，Agent 本身仍为非 root。远程升级、
回滚和卸载必须校验制品并等待实际部署结果；卸载结果得到控制面确认前会保留 helper。

受管 Xray 的安装、回滚和保留数据移除见[Xray 版本管理](xray-releases.md)。独立 Nginx、
证书、WARP 和外部 Xray 各自有单独的能力开关与恢复边界。

## 交互式一键卸载

面板安装的 Agent 推荐使用根目录脚本：

```bash
(
  uninstaller="$(mktemp)" || exit 1
  trap 'rm -f -- "$uninstaller"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/agent/uninstall.sh -o "$uninstaller" || exit 1
  sudo bash "$uninstaller"
)
```

也可在已审阅 checkout 中运行 `sudo bash agent/uninstall.sh`。脚本要求 stdin/stdout/stderr
都连接 TTY，只接受身份完整的受管安装：只有一个时自动选择；多个时列出 unit、安装根和
状态，要求输入编号。已知自定义身份可以明确指定：

```bash
sudo bash agent/uninstall.sh \
  --root /opt/open-node-agent-edge \
  --unit open-node-agent-edge.service
```

确认提示为 `是否彻底清除以上数据？[Y/n]`：

- 直接回车或输入 `y`：默认彻底清除。除运行资源外，还删除精确受管安装根、专用账号、
  lifecycle helper 和与该身份绑定的私有 bootstrap 恢复目录。
- 输入 `n`：停用并删除 Agent unit、当前版本指针和 release 虚拟环境，保留配置、Token、
  journal、日志、状态、复制的 Xray/Nginx/NextTrace 文件、安装清单、专用账号、helper 和
  bootstrap 任务，以便恢复或重装。

其他输入、EOF、非交互调用或身份不一致都会停止；脚本不会用目录通配符删除 `/opt`。
原始输入、受管根以外的运行时文件、无关 systemd 服务和其他用户 home 不会删除。

## Host deployment CLI 卸载

手工安装也可执行：

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall
```

该动作保留配置、journal、日志、复制的 Xray 和专用账号。保留可信 `service.py` 后，可用
`install --wheel /path/to/agent.whl` 且不传源参数恢复。明确删除保留数据和专用账号时：

```bash
sudo python3 agent/app/open_node_agent/service.py uninstall --purge
```

两种卸载都不会登录控制面删除服务器记录/Token，不会停止外部 systemd Xray，也不会撤销
外部 DNS、证书、公网入站或已经分发给客户端的凭据。卸载前应单独处理这些资源。
