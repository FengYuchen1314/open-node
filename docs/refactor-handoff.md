# Open Node 重构交接记录

更新时间：2026-08-31

这份文件供后续聊天直接接手。开始工作前，以当前 Git、GitHub 和 VPS 状态为准，先核对本文件中的快照，不要只依赖聊天记录。

当前已从受限 Preview 首发转入四个固定官方仓库的功能对等工作。Mieru UDP、
首发安全加固、持久 Compose 和 Agent 0.2.0 发布不要重做；后续补齐的 GitHub
控制面安装器、套餐计费、Agent 设置和管理员 MFA 已进入公开主线。管理员 MFA
已通过后端和浏览器验收，公共探针 Worker 的匿名浏览器门槛也已通过。生产实例保持
原镜像，本轮没有升级生产。建议在新聊天中直接说明：

> 请先阅读 `docs/refactor-handoff.md`、`docs/mmwx-source-parity.md` 和 `docs/testing.md`，核对公开主线、候选分支、VPS 隔离测试 checkout 和生产镜像四者的实际 revision。继续参考用户提供的四个固定官方仓库，不要把受限 Preview 首发等同于完整 MMWX 替代。测试、构建、浏览器和真实流量验收都在 `185.99.135.224` 的隔离候选环境运行，不动生产服务和数据库。公开 HTTPS、Cloudflare 账户部署与异地加密备份需操作者的实际输入，不能用本地通过替代外部验证。

## 固定目标和边界

- 项目名为 `open-node`，GitHub 仓库为 <https://github.com/FengYuchen1314/open-node>。
- 只使用一个仓库，后端、Agent、前端、探针、运行时补丁、文档和测试脚本都在本仓库。
- 后端使用 FastAPI，前端使用 Vue 3、Vuetify、Vite 和 TypeScript。
- 软件对所有人免费，不需要许可证、激活码、付费权限检查或商业许可证服务器。
- `tajiaoyezi/miaomiaowuX` 固定提交
  `c12ce653bc07fe30426b7dfcb85076974b7be0e0` 是控制面业务的主要参考。
- `mmw-agent`、`mmwx-probe`、`Xray-core-mmwx` 只作为 Agent、探针和运行时兼容参考。
- `miaomiaowu` 和 `NodeControll` 是无用旁支，明确不处理。
- 所有测试、构建和真实流量烟测都在 VPS `185.99.135.224` 上完成，通过 SSH key 连接。不要在本机运行测试。
- 不复制旧项目的许可证校验，也不依赖旧许可证服务。

旧的百分比估算已经作废。当前状态必须以
[`mmwx-source-parity.md`](mmwx-source-parity.md) 的固定源码矩阵和可执行发布门槛为准；
套餐计费已按官方语义修正并验证；外部订阅、通知、完整迁移、应用内备份恢复和
面板生成远程 Agent 安装命令仍有明确缺口。
这不是首发完成度：受限 Preview 可以明确只支持 Debian 12 amd64、单控制面/
单 worker、managed Agent/Xray 和新装或受控迁移。历史私有资源发现、部分旧
Agent 路径和更广外部环境仍未闭环，因此不能宣布完整替代 MMWX，但它们不应
阻止上述范围的首发。

## 当前权威状态

| 项目 | 当前状态 |
| --- | --- |
| GitHub `main` | 已发布 `6ca84e21202950bf5ee4754a8ae20e28dbde42ed`，含管理员 MFA 与公开 Probe 验收；匿名 Raw 控制面安装脚本的完整隔离安装链路已通过 |
| 当前候选 | `codex/mmwx-parity-release-candidate`；最近完整 GitHub 四项检查通过的代码提交为 `6ca84e21202950bf5ee4754a8ae20e28dbde42ed`。面板远程 Agent 安装正在实现，未提交改动以 `git status` 为准 |
| VPS 隔离候选 | `/opt/open-node/mmwx-parity-candidate`，公开 Probe 的精确提交验收为 `6ca84e2`；测试不得复用生产数据库或服务 |
| VPS 生产源码 | `/opt/open-node`，`27ad431dd97670076d532efa461745ac9576ee2a`；源码、公开主线和运行镜像不是同一个 revision，不要混为一谈 |
| 持久控制面 | Compose 服务 `open-node-open-node-1` healthy，绑定 `127.0.0.1:8000 -> 8080`；数据卷为 `open-node_data` |
| 精确部署镜像 | `open-node:cb1eb0c`；image ID `sha256:2d5f340b6c84eedf2d0f0aa64938d5560ee11da444b9e5e917748a575ecfb0d3`；OCI revision label 为完整 `cb1eb0c` SHA |
| 开机管理 | `open-node-compose.service` 为 `enabled`、`active`，从 `/opt/open-node/deploy/.env` 启动持久 Compose |
| 最近部署备份 | `/var/backups/open-node/20260829T123138Z-cb1eb0c`，目录及文件均为 root 私有权限；含状态、配置、源码和精确新旧镜像 |
| 隔离恢复验收 | 独立 project `open-node-restore-cb1eb0c`、独立 volume、`127.0.0.1:18081`；健康、管理员认证、数据库完整性/计数和 identity seed 连续性通过，验收资源已按 project label 清理 |
| Agent 0.2.0 发布 | annotated tag `agent-v0.2.0` 指向 `3bf30c0b488efe6575927d01acca07f6dc0b3662`；GitHub prerelease 恰好包含四项制品，匿名校验及 WebSocket/HTTP 实下载、升级、流量和回滚烟测均通过 |

VPS 已不再使用 `/tmp` Uvicorn 进程。控制面由持久 Compose 和 systemd 管理，已经
通过备份、服务重启、Compose down/up 和隔离恢复验收。当前访问方式仍是 SSH 隧道
下的 loopback Preview，不是公开 HTTPS 部署。

`cb1eb0c` 已完成并部署默认关闭短码及旧 `/x` 兼容、旧 bearer 一次性轮换、
请求路径 access log 抑制、容器日志上限和 SQLite 每连接外键启用。完整 VPS
回归、旧库迁移和 `foreign_key_check` 均已通过；不得把这些项目重新列为待办。

## 已完成并进入主线

### 基础、授权和安全

- 单仓库工程结构、FastAPI/Vue/Vuetify 基础、Docker 单镜像和 Compose 部署已完成。
- `cb1eb0c` 精确镜像已经切换到私有持久卷和 loopback 端口，由 enabled/active 的
  `open-node-compose.service` 托管；backup、restart、Compose down/up 和隔离恢复
  均已验收。
- 无许可证约束有独立测试覆盖，不存在激活 key、付费功能开关或许可证服务器调用。
- 管理员创建、恢复、Argon2id 密码、持久会话、CSRF、Origin、登录限流和私有管理 API 已完成。
- 订阅用户使用独立账户域，普通产品用户不会获得控制台管理员权限。
- 生产默认只接受 256-bit 长订阅 bearer，生成/自定义短码和旧 `/x` 均关闭；
  未标记的旧 token 会一次性轮换，显式迁移兼容开关才保留旧值。
- Uvicorn 和示例 Nginx 不记录 bearer path，Compose 日志有容量上限；SQLite 每个
  连接强制启用外键，迁移前后都运行 `foreign_key_check`，发现违例会拒绝启动。

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
- 长订阅 token、临时分享链接和链接重置已完成。短码、自定义短码和旧 `/x`
  只在显式迁移兼容模式可用；安全默认、旧 bearer 轮换和边缘代理回退保护已经验证。
- 套餐节点别名、自动速度规则、用户级配额/速度/连接覆盖和原生执行已完成。
- Clash/Surge 公共及个人模板、默认模板、草稿预览、导入导出和删除保护已完成。
- 用户、套餐、Server 和节点的编辑、撤销、两阶段删除、运行时清理和历史保留已完成。
- 当前系统创建的私有 routed node 已支持订阅用户自助创建、变更集部署、删除和用户删除联动。
- 订阅 IP 访问策略已经完成。

### 订阅账户和迁移

- `/account` 用户门户、登录、密码管理、设备会话撤销、TOTP 和一次性恢复码已完成。
- 管理员可创建绑定套餐的一次性注册邀请。系统只存 token 的 SHA-256 摘要，领取时原子创建用户、账户、套餐分配和运行时授权。
- 邀请 token 放在 `/account#invite=...` 片段中，不进入首个 HTTP 请求和访问日志。
- 旧 MMWX bcrypt 密码、TOTP、恢复码、订阅 key、套餐分配和多文件 profile
  支持预览后事务导入；兼容 `/x` 链接只属于显式启用的受控迁移模式。
- 源系统管理员会降级为普通产品用户，旧 session 和 API token 不导入。

### 证书、探针和界面

- DNS-01、EAB、HTTP-01 standalone/webroot、远端 Agent challenge、PEM 导入、续期、吊销和 Agent 部署已完成。
- 已用 Pebble/EAB、真实 Nginx、HTTPS/WSS 和短周期自动续期完成实机验证。
- 公共 probe API、WebSocket、历史序列、筛选、比较、流量热点、健康评分、回程路由和 Vue 可视化已完成。
- 管理控制台和订阅门户已做桌面与窄屏浏览器检查，主要工作流可用。

更细的功能清单在 [`migration-map.md`](migration-map.md)，各功能的安全边界和复现方式在同目录专题文档中。

## 最新候选验证（2026-08-30/31）

- 管理员 MFA：TOTP、一次性恢复码、登录 challenge、强制绑定、会话撤销、
  本地恢复和跨 IP/新 challenge 的持久账号级限流已实现。默认安装不自动启用；
  必须先保存稳定的 `OPEN_NODE_SUBSCRIBER_TOTP_KEY`。与官方恢复码登录和强制
  绑定来源的差异见 [`administrator-security.md`](administrator-security.md)。
- VPS 在 `45515b6` 的完整后端 **955 passed**；`58b33af` 新增并发预算边界后，
  认证专项 **26 passed**。GitHub 同一 `58b33af` 的完整后端 **956 passed**，
  四个独立 CI job 均通过。不要把不同提交、不同环境的计数写成同一次执行。
- VPS 的 Agent **605 passed**、前端 **239 passed**、主应用和探针产物构建、
  Worker **3 tests** 及类型检查通过。CI 的 Agent 测试使用独立托管 runner 的
  `sudo` 权限验证真实 `chown`，不是放宽断言或在生产执行测试。
- MFA 真实生产前端浏览器脚本在 `fb1aaaf` 和 `45515b6` 都通过：绑定、恢复码
  确认、强制策略、密码后验证、替换/禁用及 CLI 恢复后的重新绑定；1440/390px
  截图已经逐张检查，秘密被遮罩，临时数据库和监听进程已清理。
- 公共探针 Worker 的真实产物、匿名 API/WebSocket、双向凭据隔离、静默 socket
  下的持续轮询、断线重连与桌面/手机图表均已通过。验收使用 Wrangler dry-run
  的真实 bundle 和官方 Miniflare/workerd，绕过已记录故障的 Wrangler 开发代理；
  没有修改应用轮询或替换为 mock fetch。截图已检查，Cloudflare 实际账户部署
  和公网 HTTPS 仍未验证，不能混同于这次本地运行时门槛。

复现命令、证据范围和后续结果以 [`testing.md`](testing.md) 为准。

## 受限 Preview 历史基线验证（2026-08-29）

当时的安全基线 `cb1eb0ca936bcb46099ac972d4d7b46d800e9a54` 已在 Debian 12
x86-64 VPS 的精确 clean worktree 上完成门禁和持久部署验收：

- 本地、GitHub `main`、VPS checkout、镜像 OCI revision label 和部署环境中的
  `OPEN_NODE_REVISION` 均指向同一完整 SHA。
- 后端 Ruff 全量检查通过；pytest 收集 **913** 项并 **913 passed**。Agent 全量
  **544** 项通过；前端 **32** 个测试文件/**216** 项、类型检查和生产构建通过，
  probe-worker 类型检查及目标格式检查也通过。
- 短链安全默认、旧 bearer 惰性轮换与兼容保真、访问日志泄密防护、容器日志上限、
  SQLite 每连接外键、旧库迁移和迁移前后 `foreign_key_check` 均有回归覆盖并通过。
- 部署镜像为 `open-node:cb1eb0c`，image ID 为
  `sha256:2d5f340b6c84eedf2d0f0aa64938d5560ee11da444b9e5e917748a575ecfb0d3`；
  容器以 `127.0.0.1:8000 -> 8080` healthy 运行。
- `open-node-compose.service` 的 enabled/active 状态、服务 restart、Compose down/up
  和数据连续性已经检查。私有备份位于
  `/var/backups/open-node/20260829T123138Z-cb1eb0c`，保留完整状态、配置、源码及
  精确的新旧镜像。
- 从停止态备份恢复到独立 `open-node-restore-cb1eb0c` project、独立 volume 和
  `127.0.0.1:18081` 后，健康、管理员认证、数据库完整性/记录计数和 identity seed
  连续性均通过；随后只清理该测试 project 的资源，生产 Compose 全程保持 healthy。
- Agent 0.2.0 的 annotated tag、公开 GitHub prerelease 和四项制品已创建；四项
  资产从未认证地址下载后通过 `SHA256SUMS`、`BUILD.json`/tag revision、wheel
  metadata 和 bootstrap 结构校验，下载 wheel 的 WebSocket/HTTP 实发布烟测退出 0。

该提交也包含并保留 Mieru UDP 里程碑的以下运行时证据：

- 固定源 `d3fdae5833a92070414db588ee9893264147b789`、Go 1.26.7 的四补丁运行时构建、包测试、三个 race 包、`go mod verify` 和 matching-source 归档通过。
- 运行时 SHA-256 为 `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`；matching-source SHA-256 为 `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`。
- WebSocket 与 HTTP 两份完整协议烟测分别退出 0。真实 Mihomo 覆盖 Mieru TCP/UDP underlay 的 UDP echo、DNS、4096 字节、多目标、统计归属、轮换、直接零用户、托管 suspension、Agent 重启和恢复。
- 未修改参考运行时在两种 Mieru underlay 上拒绝 UDP 目标；官方 Xray 迁移失败时配置字节和当前 fork PID 均不变。
- 订阅客户端烟测使用 Mihomo v1.19.30、sing-box v1.13.19 和固定 Xray，完整 18 变体、URI/Base64、模板 API 与浏览器流程通过。
- 原生限速烟测的 18 个 TCP 与 18 个 UDP 变体全部通过，包含两种 Mieru underlay、Vision TLS、热更新、连接名额、自动规则、重启持久性和三种视口。

后端全量门禁仅有已知的 Starlette/httpx 弃用提示；npm install-script 审批提示和
前端 bundle 大小提示也没有造成失败。它们不是当前首发阻断。

## 剩余边界

### 受限 Preview 首发 P0 已完成

包含 `cb1eb0ca936bcb46099ac972d4d7b46d800e9a54` 安全代码基线及文档收尾的
`3bf30c0b488efe6575927d01acca07f6dc0b3662` 已成为 `agent-v0.2.0` annotated tag
目标。GitHub prerelease **恰好**包含以下四项制品：

- `open_node_agent-0.2.0-py3-none-any.whl`
- `open-node-agent-bootstrap-0.2.0.tar.gz`
- `BUILD.json`
- `SHA256SUMS`

四项制品已经从未认证的 GitHub 下载路径取回；tag、`BUILD.json`、`SHA256SUMS`、
wheel metadata 和 bootstrap 结构校验通过。下载 wheel 随后在 WebSocket/HTTP 两种
模式完成默认 GitHub 再下载、固定哈希升级、真实 VLESS 流量、回滚和 systemd
生命周期烟测。受限 Preview 首发没有剩余代码 P0。

### 依赖运营输入，不是代码 P0

- **公开 HTTPS**：当前只在 `127.0.0.1:8000` 上提供 SSH 隧道 Preview。公开部署
  仍需要操作者提供正式 hostname、DNS、受信证书/ACME 账户和最终反向代理配置；
  在这些输入到位前不能宣称公网 HTTPS 已验收。
- **异地加密备份**：同机 root 私有备份及隔离恢复已经验证。异地副本仍需要
  操作者指定远端存储、加密密钥、保留策略和恢复责任人；这些信息不能由代码仓库
  代替或猜测。

### 首发明确排除，不是 P0

- **历史私有资源迁移**：当前系统自己创建的私有 routed node 有完整生命周期，
  但旧 MMWX 中未记录的 ownership、provider/relay-group、Nginx/tunnel 和跨对象
  历史依赖仍不能自动发现并清理。受限 Preview 只承诺新装或逐项审核的受控迁移；
  要宣称无缝完整替代时再关闭此门槛。
- **部分旧 Agent 迁移**：旧 `securechan-v1` WebSocket 已兼容；旧 HTTP/pull
  回调通过切换 WebSocket 或安装独立 Agent 迁移，不属于首发支持路径。
- **剩余 host 操作和更广组合**：未实现操作返回 501。其他 tracing 工具、Linux
  发行版、架构、任意进程接管、传输包装和客户端组合不在受限首发支持矩阵。
- **多主机和任意降级**：首发只支持单控制面/单 worker，不承诺水平扩展、零停机
  升级或任意未来 schema downgrade；回退依赖已验证镜像和升级前完整备份。

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

受限 Preview 首发已经闭环。下一轮不要重新打开已经验收的安全加固、持久 Compose、
同机备份、隔离恢复和 Agent 0.2.0 发布烟测。只有在操作者提供域名、证书、远端
存储和密钥后，才分别开展公开 HTTPS 与异地加密备份验收。历史私有资源发现与导入
仍是完整原地替换所需工作，但不影响新装或受控迁移范围内的 Preview；后续开展时
仍不得在没有来源证据时猜测资源归属。

## 接手续查

开始下一轮前执行：

```powershell
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/main
git ls-remote --tags origin "refs/tags/agent-v0.2.0*"
ssh root@185.99.135.224 "git -C /opt/open-node status --short; git -C /opt/open-node rev-parse HEAD; systemctl is-enabled open-node-compose.service; systemctl is-active open-node-compose.service; curl -fsS http://127.0.0.1:8000/healthz"
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
- 只修改 `open-node` 主仓库。候选分支通过隔离验收后再进入公开 `main`；不要
  修改四个只读参考仓库，也不要处理已排除的旧项目旁支。

## 最近主线提交

从新到旧的主要里程碑：

```text
cb1eb0c fix: harden preview release deployment
66b6319 feat: add Mieru UDP target forwarding
b6267f8 docs: distinguish deployed feature baseline
774ffbb docs: add refactor handoff
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
