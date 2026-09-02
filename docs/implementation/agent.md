# Agent 实现

## 进程入口与对象图

Agent 是独立 Python 3.11+ 包 `open_node_agent`，不嵌入控制面，也不把 Xray 编译进 Python
进程。systemd 最终执行 `open-node-agent --config <private-json>`，入口链如下：

```text
__main__.main
  → load_config
  → CommandJournal（私有 SQLite + 进程锁）
  → XrayRuntime（managed 或 systemd）
  → Operations（主机能力门面）
  → Agent（WebSocket/HTTP、心跳、遥测、命令与结果重放）
```

[`__main__.py`](../../agent/app/open_node_agent/__main__.py) 同时配置私有轮转日志、处理信号、
恢复 Xray release 事务并关闭所有子组件。配置由
[`AgentConfig`](../../agent/app/open_node_agent/config.py) 校验；`master_url` 默认要求 HTTPS，
自定义 CA 必须是明确文件，路径字段必须绝对且不允许把敏感运行目录指向不受控位置。

## 传输与认证

[`client.Agent`](../../agent/app/open_node_agent/client.py) 支持三种 `connection_mode`：`auto`、
`websocket`、`http`。auto 优先 WebSocket，失败后使用 HTTP polling/fallback。两种 transport
共享注册内容、命令执行器和本地 journal：

- 注册只发送当前 Agent Token、主机名、版本、连接模式、运行时状态和显式 capability；
- WebSocket 收发 auth、heartbeat、telemetry/scan、RPC 和 stream frame；
- HTTP 使用 register/heartbeat、command lease、result 和 telemetry 接口；
- 控制面确认 result 后，Agent 才把本地结果标为 acknowledged；未确认结果在重连后重放；
- 内部 queue 有界，命令通过 `execution_lock` 串行进入主机操作。

`health.json` 每秒原子刷新，包含进程/package 身份、最近控制面联系、节点清理状态和 Xray/
Nginx desired state。主机安装器的 readiness 同时检查这个文件、systemd PID、运行 release、
认证联系和运行时状态；旧 health 文件不能证明新进程成功。

## 命令日志簿

[`CommandJournal`](../../agent/app/open_node_agent/journal.py) 在 state 目录创建 `agent.lock` 和
`commands.sqlite`：

1. `begin` 用 request ID 和 method/path/query/body/stream 的 SHA-256 fingerprint 建档。
2. 同 ID、不同 fingerprint 返回 409；已有结果直接重放。
3. 普通命令只有记录却没有结果，表示上次执行被中断，默认返回冲突，不盲目再执行。
4. `finish` 只填充一次 result；`pending_results` 按创建时间重传未确认结果。
5. Xray/Nginx 的 desired running state 也写入 journal，与当前进程存活分开。

只有具备自身恢复状态机的 lifecycle、HTTP-01 和 node cleanup 命令可以带 `resume` 再进入
处理器。新增可重试命令必须先设计持久化 intent、幂等身份和中断恢复，不能只把路径加入
resume 白名单。

## 命令分派

[`Operations`](../../agent/app/open_node_agent/operations.py) 构造各能力对象，并由 `handle`
按固定 method/path 分派。未实现操作抛 `NotImplementedError`，Agent 返回 501，不伪造成功。

主要能力组：

| 路径或能力 | 实现对象 | 写入边界 |
| --- | --- | --- |
| Xray config/inbounds/outbounds/routing/batch | `XrayRuntime` 与 operations 纯编辑函数 | 运行时锁、候选校验、expected SHA、原子替换和回滚 |
| Xray release install/rollback/remove | `XrayReleases` | 固定 checksum、release 目录、selection 与事务文件 |
| Nginx、证书、站点、隧道 | `NginxRuntime`、`FileTransaction` | 只处理配置中声明的 owned 文件和进程 |
| subscription access | `SubscriptionAccess`、`NativeLimiter` | 按 credential 身份修改客户端与限速策略，保存恢复状态 |
| node cleanup | `NodeCleanup` | preview/receipt、依赖闭包和恢复 journal |
| 外部 Xray 接管 | `XrayTakeover`、`SystemdRuntime` | 显式 opt-in、多文件 receipt、目标 unit 身份 |
| HTTP-01 | `HttpChallenges` | 固定监听地址或 webroot、lease 和精确 token 文件 |
| WARP | `Warp` | 私有账号状态与显式 outbound 合并 |
| 诊断/日志/在线用户 | `Diagnostics`、`OwnedLogs`、`online` | 有界子进程、路径白名单和脱敏输出 |
| Agent 地址与生命周期 | `AgentManagement`、`HostLifecycle` | 配置源路径摘要或 root helper socket |

所有会改 Xray/Nginx/runtime 的普通命令在 `runtime.lock` 下执行，并先调用
`node_cleanup.recover()`。这保证未完成清理不会和新的配置写入交错。纯日志、地址探测和
HTTP-01 使用各自锁/lease，不扩大 runtime 临界区。

## 运行时模式

### Managed

[`XrayRuntime`](../../agent/app/open_node_agent/runtime.py) 在 Agent 账号下启动 operator 提供的
Xray binary，并持有子进程。`KillMode=control-group` 使 systemd 停 Agent 时一并清理 owned
子进程。配置写入流程是：读取旧内容与 fingerprint，生成候选，执行 Xray test-config，
写临时文件并原子交换；restart/readiness 失败则恢复旧文件和旧运行意图。

`GuardedAtomicWrite` 使用目录 fd、`O_NOFOLLOW`、inode/fingerprint 和 Linux rename exchange
防止检查后替换目标的竞争。外部编辑导致摘要变化时拒绝覆盖。

### External systemd

[`SystemdRuntime`](../../agent/app/open_node_agent/systemd_runtime.py) 不接管任意服务。它验证
canonical unit、root-owned config、专用非 root 账号、ExecStart 参数、运行 PID 和目标配置
布局。主机所有者通过
[`systemd_access.py`](../../agent/app/open_node_agent/systemd_access.py) 安装固定 polkit 规则，
只允许 Agent 账号控制记录的 unit。Agent 关闭不会顺带停止外部 Xray。

多文件接管由 `XrayTakeover` 另行启用。它记录原始文件 receipt 和恢复 journal，校验 native
Xray merge 结果；没有 opt-in 时，外部多文件配置保持只读。

## 运行模块清单

| 文件 | 主要职责 |
| --- | --- |
| [`agent_management.py`](../../agent/app/open_node_agent/agent_management.py) | 探测并在 expected-hash 保护下更新控制面 URL |
| [`certificates.py`](../../agent/app/open_node_agent/certificates.py) | 证书/私钥匹配、SAN 和有效期校验 |
| [`diagnostics.py`](../../agent/app/open_node_agent/diagnostics.py) | TCP/ICMP 延迟、域名与回程诊断，有界子进程 |
| [`host_files.py`](../../agent/app/open_node_agent/host_files.py) | owned path 检查和多文件事务恢复 |
| [`http01.py`](../../agent/app/open_node_agent/http01.py) | standalone/webroot HTTP-01 challenge lease |
| [`lifecycle.py`](../../agent/app/open_node_agent/lifecycle.py) | 非 root Agent 到本机 root helper 的 Unix socket 客户端 |
| [`lifecycle_host.py`](../../agent/app/open_node_agent/lifecycle_host.py) | root helper unit、job store、固定 release 下载、部署与恢复 |
| [`lifecycle_protocol.py`](../../agent/app/open_node_agent/lifecycle_protocol.py) | 允许的 lifecycle command 与 fingerprint |
| [`lifecycle_report.py`](../../agent/app/open_node_agent/lifecycle_report.py) | 卸载后仍可运行的最小结果回报器 |
| [`limiter.py`](../../agent/app/open_node_agent/limiter.py) | 原生限速文档、credential 绑定和 fork capability 校验 |
| [`logs.py`](../../agent/app/open_node_agent/logs.py) | 私有轮转 Agent 日志和受限文件查看/删除 |
| [`nginx.py`](../../agent/app/open_node_agent/nginx.py) | owned Nginx 配置、证书、网站和隧道事务 |
| [`node_cleanup.py`](../../agent/app/open_node_agent/node_cleanup.py) | 节点资源依赖分析、预览、执行和中断恢复 |
| [`online.py`](../../agent/app/open_node_agent/online.py) | Xray Stats API 在线用户/IP 采集与有界结果 |
| [`route_trace.py`](../../agent/app/open_node_agent/route_trace.py) | NextTrace 输出解析和回程证据规范化 |
| [`subscription_access.py`](../../agent/app/open_node_agent/subscription_access.py) | 托管 credential 下发、撤销、listener suspend/restore |
| [`warp.py`](../../agent/app/open_node_agent/warp.py) | Cloudflare WARP 注册状态和 outbound 管理 |
| [`xray_releases.py`](../../agent/app/open_node_agent/xray_releases.py) | Xray 固定 release 下载、切换、回滚和移除 |
| [`xray_takeover.py`](../../agent/app/open_node_agent/xray_takeover.py) | 外部多文件 Xray 显式接管与恢复 |

静态清单还列出每个类的公开方法与模块依赖：
[Agent 运行代码](source-inventory.md#agent--运行代码)。

## 主机安装与 release 状态

[`service.py`](../../agent/app/open_node_agent/service.py) 是 root-only systemd deployment CLI，
不是常驻 Agent 的一部分。`Deployment` 以 root、unit 和 unit 对应的专用账号确定安装身份：

- `install`：验证 wheel/version/digest，创建专用账号、私有目录和 hardened unit，staging 后
  激活 release；
- `upgrade`：在旧进程继续运行时准备候选，写 pending transaction 后切换；readiness 失败
  恢复旧 release；
- `rollback`：切回 manifest 记录的 previous release；
- `recover`：处理 staging、release switch、policy 或 interrupted removal；
- `uninstall`：删除 unit/current/release，保留 config/state/runtime、manifest 和账号；
- `uninstall --purge`：验证身份后删除专用账号和整个安装根，不使用 `userdel -r`。

manifest 状态为 `preparing`、`installed`、`failed`、`removing` 或 `removed`。`staging`、
`pending`、`policy_restore` 和 release map 共同描述尚未完成的事务。unit 内容、root、账号
UID/GID、release 名称和目录所有权任一不匹配都会停止操作。

面板一键安装由 Backend 生成固定 installer/release metadata，默认 root 与 unit 带服务器
UUID 前 12 位。清单只包含同源 `/api/v1/agents/bootstrap/artifacts/` 路径、精确字节数和
SHA-256；子机安装时的 wheel、bootstrap、BUILD 和 Xray 文件全部从面板下载，不访问
GitHub。面板仅代理代码内固定的上游并在发布私有缓存前校验重定向域名、长度和摘要。
私有 bootstrap job 保存在 `/var/lib/open-node-agent-bootstrap/`，其中配置含长期 Agent
Token。它不自动启用 remote lifecycle；主机所有者必须运行 `enable-remote` 并批准固定
HTTPS release source。

## 一键卸载

[`agent/uninstall.sh`](../../agent/uninstall.sh) 是独立交互入口。它要求 root 和 stdin/stdout/
stderr TTY，验证 manifest、root、unit、账号及可信 `service.py`。一个安装自动选择，多个
安装必须编号选择，也可显式传 `--root` 与 `--unit`。

确认提示 `[Y/n]` 默认 purge；`n` 调用 data-preserving uninstall。purge 成功后，脚本只
删除与所选 root/unit 精确匹配的 bootstrap job。外部 Xray/Nginx、其他 systemd unit、
原始输入文件、控制面 server record 和数据库中的 Token 不会被本机脚本删除。

## 权限与秘密

- Agent config 中的 Token 使用 Pydantic SecretStr，配置和 journal 为专用账号私有；日志
  formatter 额外清理常见 credential 字段。
- 常驻 Agent 不以 root 运行。低端口只授予 `CAP_NET_BIND_SERVICE`；ICMP/回程诊断的
  `CAP_NET_RAW` 需要主机策略显式开启。
- lifecycle helper 是独立 root 进程，只接受 Unix socket 上固定 schema、release base 和
  checksum；Agent 不能提供任意命令行。
- 文件写入拒绝 symlink、hardlink、非预期 owner/mode、路径逃逸和 TOCTOU 摘要变化。
- 子进程输出有长度/超时限制，异常返回类型而非原始 secret；未实现能力返回 501。

## 测试

`agent/tests/` 覆盖传输、journal、Xray/Nginx 配置、systemd、lifecycle、HTTP-01、限速、
WARP、节点清理、外部接管和卸载。涉及真实 ownership 的 CI 以 root pytest 运行测试目录，
随后构建 wheel：

```bash
python -m ruff check agent
python -m pytest agent/tests
python -m build --wheel --outdir agent/dist agent
```

主机级行为还需对应 `scripts/vps/smoke-agent-*`、`smoke-xray-*`、`smoke-nginx.py` 等真实
systemd/VPS 验收。mock systemctl 的单元测试不能替代进程组、权限和重启恢复验证。
