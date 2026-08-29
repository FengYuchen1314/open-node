# Open Node 重构交接记录

更新时间：2026-08-29

这份文件供后续聊天直接接手。开始工作前，以当前 Git、GitHub 和 VPS 状态为准，先核对本文件中的快照，不要只依赖聊天记录。

Mieru UDP 目标转发已经在本轮完成。建议在新聊天中直接说明：

> 请先阅读 `docs/refactor-handoff.md` 和 `docs/migration-map.md`，然后从历史私有资源迁移继续。只处理 `open-node` 主仓库和 `miaomiaowuX` 默认主线，所有测试都放在 `185.99.135.224` 上运行。

## 固定目标和边界

- 项目名为 `open-node`，GitHub 仓库为 <https://github.com/FengYuchen1314/open-node>。
- 只使用一个仓库，后端、Agent、前端、探针、运行时补丁、文档和测试脚本都在本仓库。
- 后端使用 FastAPI，前端使用 Vue 3、Vuetify、Vite 和 TypeScript。
- 软件对所有人免费，不需要许可证、激活码、付费权限检查或商业许可证服务器。
- `FengYuchen1314/miaomiaowuX` 默认分支是控制面业务的主要参考。
- `mmw-agent`、`mmwx-probe`、`Xray-core-mmwx` 只作为 Agent、探针和运行时兼容参考。
- `miaomiaowu` 和 `NodeControll` 是无用旁支，明确不处理。
- 所有测试、构建和真实流量烟测都在 VPS `185.99.135.224` 上完成，通过 SSH key 连接。不要在本机运行测试。
- 不复制旧项目的许可证校验，也不依赖旧许可证服务。

按功能面和实机验证门槛粗略估算，当前约完成 **93%**。Mieru UDP
运行时门槛已经关闭，核心产品链路已经形成；历史私有资源发现、部分旧
Agent 路径和更广外部环境仍未闭环，不能宣布完整替代 MMWX。

## 当前权威状态

| 项目 | 当前状态 |
| --- | --- |
| 本地主分支 | `main`，包含 Mieru UDP 里程碑；具体提交以 `git rev-parse HEAD` 为准 |
| GitHub `main` | 应与本地主分支一致；接手时用 `git ls-remote` 复核 |
| 已部署功能基线 | 应与 GitHub `main` 一致；接手时核对 `/opt/open-node` 的提交和 clean 状态 |
| VPS 源码 | `/opt/open-node`，使用干净的 `main` checkout |
| VPS 后端 | `127.0.0.1:8000`；接手时重新检查 `/healthz`、根页面和账户入口 |
| VPS 进程快照 | PID 会变化，应以 `pgrep`、`/proc/<pid>/cwd`、命令行和健康检查为准 |
| 后端解释器 | `/tmp/open-node-preview.49YeIB/backend-ca-admin-venv/bin/python` |
| 最近部署备份 | `/tmp/open-node-preview.49YeIB/before-mieru-udp-b6267f8` |
| 最近功能提交 | `feat: add Mieru UDP target forwarding`；具体 SHA 以 `git log -1` 为准 |

后端当前是测试预览进程，解释器和备份位于 `/tmp`，主机重启后不能视为持久生产部署。正式部署能力已经写入 Dockerfile、Compose 和部署文档，当前预览状态与正式容器交付是两件事。

## 已完成并进入主线

### 基础、授权和安全

- 单仓库工程结构、FastAPI/Vue/Vuetify 基础、Docker 单镜像和 Compose 部署已完成。
- 无许可证约束有独立测试覆盖，不存在激活 key、付费功能开关或许可证服务器调用。
- 管理员创建、恢复、Argon2id 密码、持久会话、CSRF、Origin、登录限流和私有管理 API 已完成。
- 订阅用户使用独立账户域，普通产品用户不会获得控制台管理员权限。

### Server、Agent 和命令通道

- Server/Agent 注册、令牌、心跳、遥测、流量、扫描结果和命令队列已经持久化。
- 独立 Python Agent 支持 HTTP 与 WebSocket、命令租约、去重、重连、结果和流式帧。
- 兼容旧 `securechan-v1` WebSocket，并已用未修改的固定版本旧 Agent 做过真实验证。
- 非 root systemd 安装、升级、回滚、卸载、数据保留和故障恢复已完成。
- Xray、Nginx、WARP、日志、诊断、NextTrace、配置文件和 Agent 生命周期操作已有原生实现或明确的兼容包装。

### Xray 运行时和协议

- 独立托管 Xray、外部 systemd 绑定、多文件接管、配置验证、写入、重启和回滚已完成。
- VLESS、VMess、Trojan、Shadowsocks、Hysteria、AnyTLS、Snell 和 Mieru 的用户增删与持久恢复已接入。
- AnyTLS UDP 地址修复、Snell/Mieru 空用户启动、Mieru UDP 目标转发和独立原生限速补丁已纳入可重现构建。
- Mieru TCP/UDP underlay 都支持 TCP 与 UDP 目标；Agent 通过按二进制身份缓存的严格版本能力上报，后端只在十分钟内、运行中的 `mieru_udp_target: 1` 扫描证据下为导入和订阅启用 UDP。
- Xray 版本安装、切换、回滚、恢复和数据保留已完成。
- 运行时扫描、节点导入、目录对账、凭据修复、额外用户清理和配置漂移恢复已完成。
- 多节点变更集、依赖顺序、失败补偿、回滚屏障和人工接受部分状态已完成。
- 单节点及多节点 tunnel、Nginx/Xray 高级 tunnel 部署和真实 TCP 流量验证已完成。

### 订阅、用户和套餐

- 用户、节点、套餐、套餐分配、有效期、流量周期、配额、限速和连接数限制已完成。
- Clash、Surge、sing-box、Xray、URI list 和 Base64 订阅格式已完成，支持不兼容节点过滤。
- 订阅 token、短码、自定义短码、旧 `/x` 链接、临时分享链接和链接重置已完成。
- 套餐节点别名、自动速度规则、用户级配额/速度/连接覆盖和原生执行已完成。
- Clash/Surge 公共及个人模板、默认模板、草稿预览、导入导出和删除保护已完成。
- 用户、套餐、Server 和节点的编辑、撤销、两阶段删除、运行时清理和历史保留已完成。
- 当前系统创建的私有 routed node 已支持订阅用户自助创建、变更集部署、删除和用户删除联动。
- 订阅 IP 访问策略已经完成。

### 订阅账户和迁移

- `/account` 用户门户、登录、密码管理、设备会话撤销、TOTP 和一次性恢复码已完成。
- 管理员可创建绑定套餐的一次性注册邀请。系统只存 token 的 SHA-256 摘要，领取时原子创建用户、账户、套餐分配和运行时授权。
- 邀请 token 放在 `/account#invite=...` 片段中，不进入首个 HTTP 请求和访问日志。
- 旧 MMWX bcrypt 密码、TOTP、恢复码、订阅 key、套餐分配、多文件 profile 和兼容 `/x` 链接支持预览后事务导入。
- 源系统管理员会降级为普通产品用户，旧 session 和 API token 不导入。

### 证书、探针和界面

- DNS-01、EAB、HTTP-01 standalone/webroot、远端 Agent challenge、PEM 导入、续期、吊销和 Agent 部署已完成。
- 已用 Pebble/EAB、真实 Nginx、HTTPS/WSS 和短周期自动续期完成实机验证。
- 公共 probe API、WebSocket、历史序列、筛选、比较、流量热点、健康评分、回程路由和 Vue 可视化已完成。
- 管理控制台和订阅门户已做桌面与窄屏浏览器检查，主要工作流可用。

更细的功能清单在 [`migration-map.md`](migration-map.md)，各功能的安全边界和复现方式在同目录专题文档中。

## 最近一次完整验证

Mieru UDP 里程碑在 Debian 12 x86-64 VPS 上完成了以下验证：

- 固定源 `d3fdae5833a92070414db588ee9893264147b789`、Go 1.26.7 的四补丁运行时构建、包测试、三个 race 包、`go mod verify` 和 matching-source 归档通过。
- 运行时 SHA-256 为 `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`；matching-source SHA-256 为 `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`。
- WebSocket 与 HTTP 两份完整协议烟测分别退出 0。真实 Mihomo 覆盖 Mieru TCP/UDP underlay 的 UDP echo、DNS、4096 字节、多目标、统计归属、轮换、直接零用户、托管 suspension、Agent 重启和恢复。
- 未修改参考运行时在两种 Mieru underlay 上拒绝 UDP 目标；官方 Xray 迁移失败时配置字节和当前 fork PID 均不变。
- 订阅客户端烟测使用 Mihomo v1.19.30、sing-box v1.13.19 和固定 Xray，完整 18 变体、URI/Base64、模板 API 与浏览器流程通过。
- 原生限速烟测的 18 个 TCP 与 18 个 UDP 变体全部通过，包含两种 Mieru underlay、Vision TLS、热更新、连接名额、自动规则、重启持久性和三种视口。
- 后端全量回归 904 项、Agent 544 项、前端 32 文件/216 项通过；前端类型检查与生产构建、probe-worker 类型检查、Ruff 和目标格式检查通过。
- 重建 Agent wheel SHA-256 为 `a049c7b76a34341b01c3de6705edd8fa888011054330bb42b9133e371ed552f2`。

已有的 Starlette/httpx 弃用提示、npm install-script 审批提示和前端 bundle 大小提示不是本轮回归。

## 还没完成

### 尚未实现或尚未闭环

1. **历史私有资源迁移**：当前系统自己创建的私有 routed node 有完整生命周期，但旧 MMWX 中未记录的私有 ownership、provider/relay-group 关系、Nginx/tunnel 资源和跨对象历史依赖还不能自动发现并清理。这是下一项优先工作。
2. **部分旧 Agent 迁移**：旧 `securechan-v1` WebSocket 已兼容；旧 HTTP/pull 回调没有伪装成新租约协议，只提供明确迁移路径。
3. **剩余 host 操作**：独立 Agent 对未实现操作返回 501。更广的 tracing 工具、Linux 发行版和任意现有进程接管仍需逐项实现或验证。
4. **更广的协议组合**：固定客户端版本覆盖了主要组合，但不能代表所有传输包装、插件、OS 和架构。
5. **多主机控制面扩展和任意数据库降级**：当前单控制面部署足够运行，但没有把多主机水平扩展和未来任意 schema downgrade 作为已完成能力。

### 已有实现，但缺少外部环境证明

- 公共 CA 和真实 DNS provider staging 尚未使用操作者自有账号逐个验证；当前权威实机证据来自 Pebble/EAB。
- 原生 WARP 已支持免费注册、凭据和恢复，公共 Cloudflare 注册/转发仍缺少完整外部验证。
- native limiter 的托管模式已实机通过；外部 systemd runtime 的 limiter opt-in 仍需单独验证。
- Surge 导出经过服务端解析和固定 fixture 检查，原生 Apple Surge 应用导入没有 Apple 环境实测。
- 外部 Xray 接管已覆盖当前 VPS 的两种 Agent transport，其他发行版、不同账户和任意进程归属仍未证明。

### 当前刻意不提供

- 开放匿名注册。现有模式是管理员建用户或签发一次性套餐邀请。
- 第三方身份提供商登录。
- 自动导入旧系统中的任意上传文件、脚本、模板和规则；这些内容需要在新系统内受管重建。

这些项目是否进入最终发布范围，应按产品需求决定，不能因为“所有人免费使用”就自动等同于“任何人都能在公开实例匿名注册”。

## 本轮完成：Mieru UDP 目标转发

- `runtime/xray/mieru-udp-target.patch` 是依据公开 Mieru 协议和 RFC 1928 独立实现的 MPL-2.0 补丁，没有复制 GPL-3.0 服务端实现。它使用 `0x00`、两字节大端长度、SOCKS5 UDP 数据报和 `0xff` 封装，限制单个 SOCKS 数据报为 8192 字节。
- IPv4、IPv6 和域名目标均受支持；只接受 `FRAG=0`，非法帧、超长数据、空域名和提前 EOF 都会失败关闭。
- TCP 与 UDP underlay 共用认证 UDP association。每个 association 懒建立一条 dispatcher UDP link，逐包保留目标和认证用户上下文；会话数量、首包等待和并发关闭都有边界。
- Agent 从运行时版本命令读取严格整数 `mieru_udp_target: 1`，按二进制身份缓存；扫描或绑定失败不沿用旧能力。后端还要求扫描不超过十分钟且运行时正在运行，才允许草稿、导入和订阅把 Mieru 标记为 UDP 可用。
- 固定源构建、单元和 race 测试、matching source、两种 Agent transport、参考运行时负控、两种 Mieru underlay 的真实 UDP 流量、订阅客户端、原生限速、撤权与重启恢复均已通过；摘要和数量见上方验证记录及 [`testing.md`](testing.md)。

官方协议说明：<https://github.com/enfein/mieru/blob/main/docs/protocol.md#udp-associate-encapsulation>

下一轮建议从**历史私有资源迁移**开始：先建立旧 ownership、provider/relay-group、Nginx/tunnel 和跨对象依赖的只读发现清单与不可变 fixture，再设计预览、冲突处理、事务导入和可恢复清理。不要在没有来源证据时猜测资源归属。

## 接手续查

开始下一轮前执行：

```powershell
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/main
ssh root@185.99.135.224 "git -C /opt/open-node status --short; git -C /opt/open-node rev-parse HEAD; curl -fsS http://127.0.0.1:8000/healthz"
```

随后阅读：

- [`migration-map.md`](migration-map.md)
- [`testing.md`](testing.md)
- [`fork-runtime.md`](fork-runtime.md)
- [`subscriptions.md`](subscriptions.md)
- [`native-limits.md`](native-limits.md)
- [`deployment.md`](deployment.md)

工作约束：

- 先读现有代码和测试，再做补丁。
- 文件修改使用 `apply_patch`，不覆盖用户已有改动。
- 测试与构建只在 VPS 运行。
- 每个运行时能力都要有真实 Agent、真实 Xray 和真实流量证据。
- 部署前备份数据库、源码、前端产物和环境；部署后核对 Git 提交、数据库升级、进程、日志和健康检查。
- 只推送 `open-node/main`，不要处理已排除的旁支。

## 最近主线提交

从新到旧的主要里程碑：

```text
1d46cf0 feat: add plan-bound subscriber invitations
fa1029e Add subscription IP access policies
1f98ee4 Add subscriber-private routed nodes
7c8f043 Add durable temporary subscription links
1d23854 Migrate MMWX subscription profiles and legacy links
d69d733 feat: import legacy MMWX subscriber identities
751d873 Add custom Clash and Surge subscription templates
b29ad1c Add per-plan automatic speed rules with native enforcement
fceeeaf feat: add per-plan subscription node aliases
2f46ab6 feat: add custom subscription short codes
fcf6cb3 feat: add subscriber quota and native limit overrides
0ae97a2 feat: add subscriber account portal and two-factor authentication
6236e71 Add managed node lifecycle and recoverable removal workflows
d9ca75d Add recoverable native node resource cleanup
92cfcc6 Add guarded user editing and durable access-aware removal
72e8767 Add guarded plan editing and durable subscription removal
cf7782b Add guarded server editing and removal with retained history
146fa53 Add durable server traffic cycles and quota controls
f9aec52 Enforce subscription access and automatic recovery
b7ae38e Add native rate limits and enforced plan provisioning
557df13 Add native Xray multifile takeover with durable recovery
2a34db1 Verify native subscription formats with real clients
e9d4889 Support fork protocol migration and persistent user revocation
ed0d401 Verify external Xray systemd binding and scoped control
```

完整历史以 `git log --oneline` 为准。
