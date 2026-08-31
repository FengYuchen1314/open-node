# Open Node 重构交接记录

更新时间：2026-08-31

这份文件供后续聊天直接接手。开始工作前，以当前 Git、GitHub 和 VPS 状态为准，先核对本文件中的快照，不要只依赖聊天记录。

当前已从受限 Preview 首发转入四个固定官方仓库的功能对等工作。Mieru UDP、
首发安全加固、持久 Compose 和 Agent 0.2.0 发布不要重做；后续补齐的 GitHub
控制面安装器、套餐计费、Agent 设置和管理员 MFA 已进入公开主线。管理员 MFA
已通过后端和浏览器验收，公共探针 Worker 的匿名浏览器门槛也已通过。本轮面板远程
Agent 安装已完成 VPS 验收，Agent 0.3.0a0 已公开发布；功能代码 `1515a7b` 和
托管 CI 夹具修正 `a677280` 已进入主线。用户要求的标准 Ant Design 前端重写
已在 `50897f9` 进入主线：管理控制台、订阅门户和公共探针均已迁入 React，
完整 CI、VPS 浏览器及精确提交的 Docker 验收通过。生产实例保持原镜像，本轮
只发布源码，没有升级生产。外部订阅首期现已进入主线：管理员维护 HTTPS
来源、手动预览确认、保留上游凭据并合入用户主订阅。英文候选的完整回归、
真实浏览器、客户端及隔离 Docker 验收已完成。用户随后明确要求
界面使用中文，管理后台、用户中心、公共探针及 Ant Design 内置文案现已完成
中文化。首轮中文 R2 前端 762 项、后端 1927 项（另验 6 个真实 TLS 用例）、
Agent 605 项、Worker 5 项通过；中文浏览器和隔离 Docker 多项验收也已通过。
截图复核发现证书成功回执被误译成失败，现已在 R4 补齐固定消息并新增 34 项测试，
187 项专项检查、类型检查和双产物构建通过，证书和全客户端实机验收也已通过。
早先一次 gRPC 转发失败在新完整运行中未复现，原因仍未知，不称为已定位修复。
R4 完整前端 796 项和精确 `998839b` 干净源码的 Docker 验收已通过。四项 GitHub CI
全部通过后，外部订阅和中文界面已以 `998839b` 发布到 `main`；生产保持原镜像。
随后加入管理员 Telegram 配置、预览/测试、持久投递和套餐临期提醒。
后端 2298 项、前端 890 项/72 文件、双构建、浏览器及工作树镜像验收已通过，
精确 `bf8eaa8` Git 镜像的 16 阶段验收和四项 CI 也已通过，通知首期已发布到
`main`；不能归入上述 `998839b` 的验收结果。生产没有升级，也没有真实 Telegram
投递验收。随后参考固定官方 `branding.go` 实现两项站点文字：管理员修改浏览器
标题及页面品牌文字，登录前公开读取，中文 Ant Design 表单，免费使用。首期现已
发布。专项后端 159 项、前端 175 项、完整前端 1013 项/75 文件、双构建、浏览器
14 阶段/27 图及工作树 Docker 10 阶段已通过。后端首轮 2498 通过、2 个旧公开路由
权限断言失败；仅修正测试后完整 R3 的 2500 项全部通过，零跳过。候选提交为
`f0ed515`；精确 Git 镜像 10 阶段及独立后核、四项 CI 均通过后，已同步 `main`。
生产仍未升级。上一份文档提交一次 CI 的测试清理异常被单独保留。后续单文件
test-only 修正 `100d93f` 已完成 7 例定向验证、计时器负控、完整前端 1013 项/75 文件、
严格类型检查、双构建、精确 Git Docker 10 阶段及四项 CI，并同步到 `main`。
它没有改变产品、依赖或超时，也不归入 `f0ed515` 的功能验收。历史两个 CI 回调的
具体创建用例仍未知，不能把此次控制实验写成历史竞态的完整复现。
文档提交 `6799fe9` 的候选与主线 CI（`33372235757`、`33372235764`）也已四项全过。
随后按官方备份代码推进 v1 格式层：清单 358 项、归档 34 项、独立恶意 ZIP 237 项和
生成器 79 项专项检查通过；真实 1 GiB / 4096 文件的读取与生成后复核也通过。
中文只读 CLI 的 77 项独立检查和真实 1 GiB 上限检查也已通过。提交 `2a28103` 已发布主线，
完整后端 3285 项全部通过，零跳过；全树后核发现 8 个新增运行文件，原有 558 项源码
摘要均不变，原 runner 非零结果保留。独立后核已确认依赖未变、隔离进程全部退出；
四项候选 CI（`33377433229`）和精确 Git 镜像验收也已通过，root 已独立核验全部
224 项最终证据。默认 Compose 的临时空间仅 64 MiB，不能把格式的 1 GiB 上限
当成默认容器可处理的容量；详细运行边界见 `backup-format.md`。
格式、生成器和 CLI 都不提供应用在线快照、加密下载或恢复，不能把它们算成网页
备份完成。生产没有升级。
`2a28103` 的主线 CI 四项随后全部通过，文档提交 `9602217` 也已在候选四项通过后
同步主线；`9602217` 的候选与主线 CI 也均四项通过。备份加密随后以 `a29345b` 发布主线：
官方 age v1.3.2、私钥只读检查和安全文件
发布已完成专项验证；163 项服务、365 项独立恶意输入、154 项新旧 CLI、158 项
获取器测试通过。CLI 的加密服务替身不计作密码学证据，原生测试单独计数。
564 文件 R1 的完整后端 4048 项、真实 1 GiB 包加密与解密校验、工作树 Docker
27 项 CLI/守卫与两项容量检查均已通过。原完整 runner 因误合并 CLI 的 77+77 分组
而汇总为 1，实际测试和独立后核均通过，原失败记录保留。精确 `a29345b` 镜像已正常
重建并通过全部 27+2 CLI/容量检查和独立应用检查；root 核验了 379 项最终证据。
候选四项 CI（`33384255225`）全部通过后，已非强制快进 `main`；生产没有升级。
主线四项 CI（`33385668793`）随后也全部通过。
文档提交 `b506dbe` 的候选与主线 CI（`33389137495`、`33392138308`）均已四项通过。
开发候选已接入应用初始化、HTTP、Agent、后台线程、证书子进程和受支持 CLI 的写入
协调，完整后端 4412 项通过；上一轮单个 bootstrap 直接调用测试失败原样保留，
仅修正其工作上下文夹具。新增一致数据库/状态文件快照、密钥依赖检查和内部加密
创建器的 410 项联动测试也已通过，零跳过。源码范围、证据及限制见
[`backup-runtime.md`](backup-runtime.md) 和 `testing.md`。包含新快照的完整后端回归
正在 VPS 新私有目录执行，精确 Git 镜像与发布 CI 尚待完成；这些代码还没有发布。
网页创建/下载和受控恢复仍未实现，生产没有升级。
通知的其他规则、完整通用设置、应用内备份和完整迁移仍未补齐。
建议在新聊天中直接说明：

> 请先阅读 `docs/refactor-handoff.md`、`docs/mmwx-source-parity.md` 和 `docs/testing.md`，核对公开主线、候选分支、VPS 隔离测试 checkout 和生产镜像四者的实际 revision。继续参考用户提供的四个固定官方仓库，不要把受限 Preview 首发等同于完整 MMWX 替代。测试、构建、浏览器和真实流量验收都在 `185.99.135.224` 的隔离候选环境运行，不动生产服务和数据库。公开 HTTPS、Cloudflare 账户部署与异地加密备份需操作者的实际输入，不能用本地通过替代外部验证。

## 固定目标和边界

- 项目名为 `open-node`，GitHub 仓库为 <https://github.com/FengYuchen1314/open-node>。
- 只使用一个仓库，后端、Agent、前端、探针、运行时补丁、文档和测试脚本都在本仓库。
- 后端保留 FastAPI。前端已按用户要求重写为 React、官方 Ant Design、Vite 和
  TypeScript；已有 API、会话契约和 Docker 部署架构不变。
- 界面统一使用简体中文，包括菜单、表单、提示、状态、无障碍名称和 Ant Design
  内置文案。协议名、命令、配置键、URL、用户自定义内容及真实诊断日志保持原样；
  不以修改 API 枚举或持久化用户数据的方式翻译界面。
- 软件对所有人免费，不需要许可证、激活码、付费权限检查或商业许可证服务器。
- `tajiaoyezi/miaomiaowuX` 固定提交
  `c12ce653bc07fe30426b7dfcb85076974b7be0e0` 是控制面业务的主要参考。
- `mmw-agent`、`mmwx-probe`、`Xray-core-mmwx` 只作为 Agent、探针和运行时兼容参考。
- `miaomiaowu` 和 `NodeControll` 是无用旁支，明确不处理。
- 所有测试、构建和真实流量烟测都在 VPS `185.99.135.224` 上完成，通过 SSH key 连接。不要在本机运行测试。
- 不复制旧项目的许可证校验，也不依赖旧许可证服务。

旧的百分比估算已经作废。当前状态必须以
[`mmwx-source-parity.md`](mmwx-source-parity.md) 的固定源码矩阵和可执行发布门槛为准；
套餐计费已按官方语义修正并验证，面板生成远程 Agent 安装命令也已实现。
外部订阅已有管理员手动 YAML 导入，但 URI/Base64 输入、定时同步、用户自助和
规则/provider 生态仍有缺口。首期用法和密钥恢复见
[`external-subscriptions.md`](external-subscriptions.md)，官方源码接点与原设计记录见
[`external-subscriptions-plan.md`](external-subscriptions-plan.md)。通知、完整迁移和
应用内备份恢复也未完成。管理员 Telegram 配置、测试和套餐到期提醒已在 `bf8eaa8`
发布，用法见 [`notifications.md`](notifications.md)，官方源码接点、安全边界
和验收设计见 [`notifications-plan.md`](notifications-plan.md)。隔离整体验收、
精确提交镜像和 CI 已通过。没有向真实 Telegram 聊天发送消息，也没有升级生产。
站点文字首期的功能和限制见 [`system-settings.md`](system-settings.md)，官方源码
依据见 [`system-settings-plan.md`](system-settings-plan.md)。它已在 `f0ed515` 独立
验收并发布，不能借用通知版本的结果。名称公开可见；本期不做 Logo、任意键值
或安全开关。应用内备份仍未完成；[`backup-format.md`](backup-format.md) 说明本轮
格式工具、只读 CLI 及单接收者 age 加密；这些通用格式工具本身不验证数据库、密钥
配对或快照。开发候选的实际快照与依赖检查见 [`backup-runtime.md`](backup-runtime.md)，
它仍不证明发送者身份、远端 Agent 信任或恢复就绪。
[`backup-plan.md`](backup-plan.md) 保留已核对的官方差异、全部写入路径和后续恢复
边界，接下来完成新代码的完整发布验收，再接管理员创建/下载与受控恢复，不要重复既有冷备。
这不是首发完成度：受限 Preview 可以明确只支持 Debian 12 amd64、单控制面/
单 worker、managed Agent/Xray 和新装或受控迁移。历史私有资源发现、部分旧
Agent 路径和更广外部环境仍未闭环，因此不能宣布完整替代 MMWX，但它们不应
阻止上述范围的首发。

## 当前权威状态

| 项目 | 当前状态 |
| --- | --- |
| GitHub `main` | 已发布命令行备份加密 `a29345b7e58417d1089c349f6f9cca878830817e`，包括此前的 `2a28103` 格式层/只读 CLI；站点文字 `f0ed515`、通知首期 `bf8eaa8`、外部订阅/中文界面 `998839b`、React/Ant Design、面板 Agent 安装等功能保持。文档或 test-only 后续提交不改变功能基线 |
| 备份格式发布 | `2a28103` 的 785 项专项、真实大包和完整后端 3285 项通过；精确 Git 镜像 `sha256:642bc4a8ee1069aaa87aed00ff8291fde34c3e38a66acd03c5678ac641cb9d43`、18 项实际 CLI/守卫检查、独立新应用及候选四项 CI 全部通过。完整后核和原失败记录见 `testing.md`。网页备份、在线快照、受控恢复和完整迁移未实现 |
| 备份加密发布 | `a29345b`：固定官方 age v1.3.2，已有 v1 ZIP 加密、私钥只读解密校验、拒绝覆盖的文件发布。完整后端 4048 项零跳过，真实 1 GiB 正文往返通过。精确镜像 `sha256:40868d23f5961f8731b59c8a41c210485d32469cc89086884a09af371b666d66`、27+2 CLI/容量检查、独立应用及候选四项 CI 通过；379 项证据已由 root 核对。默认 64 MiB tmpfs 的容量限制保留，不宣称应用快照或恢复就绪 |
| 测试清理独立门槛 | 干净 `100d93f` 前端源 `/tmp/open-node-fe-testonly-f0.hE5fRW3Q/source-r1`：1013/75 files、零跳过/未处理异常、946.51 秒，双构建 43 项资产与站点文字 R2 逐字节一致。精确 Docker 源 `/root/open-node-editor-timers-commit-100d93f.b5j3J2No/source`，新镜像 ID `sha256:6464d21218abfe3969208d776740d744edb6b1f5a2754a1846e6be54784ae69d`，10 阶段通过；4 容器/3 卷清理完成。547 文件和独立依赖清单前后不变，生产、旧源与旧证据均保留；封口记录见 `testing.md` |
| 站点文字工作源 | `/tmp/open-node-branding-integration.ChNDrkyo/source-r2`，544 文件归档 SHA-256 `25d899648c457ec13b158067ffa1564af6c07434422e4b22ca4508c0c297c3ec`。完整前端 1013/75 files、类型检查和双构建通过；浏览器 14 阶段及 root 逐张 27 图复核、工作树 Docker 10 阶段/重启/两次整卷冷复制均通过。`source-r3` 仅改订阅用户测试对公开 GET 的精确字段断言，完整后端 2500/零跳过通过；原失败记录保留，见 `testing.md` |
| 站点文字精确提交镜像 | `/root/open-node-branding-commit-f0ed515.u29eElRY/source` 是干净 `f0ed515`，547 个跟踪文件前后不变；全部 300 项 Docker 构建输入与 R2 一致。正常重建镜像 ID `sha256:e1e62f68663a7f1e95423c5267cf0cf6bedbeafb2ceec3e59e7fd812cf92fae9`、完整 OCI revision、106 Python 文件/40 前端资产和 10 阶段完整容器验收通过；4 个临时容器、3 个卷清理完成，生产不变 |
| 通知统一工作源 | `/tmp/open-node-notifications-integration.v2sQeZ5w/source-r5`，531 文件归档 SHA-256 `9e0ea5c8ecd4c637fc0d7a900a3b5fa1e678588becaf32e12a793c7404676916`。后端与已验 R4 逐字节一致：2298 passed、0 skip；前端 890/72 files、类型检查、双构建通过。浏览器 12 图、真实 40 秒恢复/人工重试、工作树镜像 16 阶段及冷恢复通过，见 `testing.md`；没有真实 Telegram canary |
| 通知精确提交镜像 | `/root/open-node-notifications-commit-bf8eaa8.JFudzeQC/source` 是干净 `bf8eaa8`，531 个跟踪文件保持不变。正常 Dockerfile 重建的镜像 ID `sha256:fc0feccc66b9a2ea5877dcfb99e7e37fcdef70cd129ca4825064521c316eee5f`、OCI 完整 revision、39 项前端资产及 16 阶段隔离容器验收通过；7 个临时容器、3 个卷已清理。`backend/README.md` 是相对 R5 的文档变化，也是 wheel 元数据构建输入，详见 `testing.md`，不能声称所有构建输入完全一致 |
| 外部订阅英文基线 | 在公开 `0ffc072` 上新增，后续随中文版发布；冻结 VPS 源为 `/tmp/open-node-external-integration.YG95YRYU/source`。后端 1927 passed / 6 opt-in skipped（6 个真实 TLS 用例另行全部通过），Agent 605、前端 570/65 files、Worker 5，以及浏览器/客户端/隔离 Docker 均通过；该目录不再覆盖 |
| 中文工作源 | 当前产品冻结于 `/tmp/open-node-zh-release.fp33Igbt/source-r4`，源码归档 SHA-256 `5c8d6008d20c692710e9e4718b935e87a3558c2172f400c5dbb6d9ccf6fdec04`。R4 全量前端 796/70 files、双构建及受影响实机流程通过；后端与已验 1927 + 6 opt-in skips（230 项 fetcher 含 6 项真实 TLS 全过）的源码相同，Agent 605、Worker 5 代码亦未变。客户端两次完整验收通过，初次 gRPC 失败原因仍未知。R2 和所有失败记录保留，详见 `testing.md` |
| 中文精确提交镜像 | `/root/open-node-zh-commit-998839b.c3ycWOn7/source` 是干净 `998839b`；非文档源码与已验 R4 一致。精确 OCI label、38 项打包前端资产、中文浏览器、重启保留、外部密钥及 HTTPS 抓取通过。镜像 ID `sha256:77b0d0faed6aa4f3e2195eebb44be8c506c6a62bc624363c3ab4cb2f2eba8b04`；临时容器和卷已清理，不是生产镜像 |
| VPS 隔离候选 | 共享 `/opt/open-node/mmwx-parity-candidate` 保留 clean `6ca84e2`；最终 React 完整回归在 `/tmp/open-node-react-release.xaSu8WDc/source`。GitHub 精确 `50897f9` 的干净构建及 Docker 复验在 `/root/open-node-react-commit-50897.0MDZIwd3/source`，两份前端产物逐字节一致；不混用生产数据库 |
| VPS 生产源码 | `/opt/open-node`，`27ad431dd97670076d532efa461745ac9576ee2a`；源码、公开主线和运行镜像不是同一个 revision，不要混为一谈 |
| 持久控制面 | Compose 服务 `open-node-open-node-1` healthy，绑定 `127.0.0.1:8000 -> 8080`；数据卷为 `open-node_data` |
| 精确部署镜像 | `open-node:cb1eb0c`；image ID `sha256:2d5f340b6c84eedf2d0f0aa64938d5560ee11da444b9e5e917748a575ecfb0d3`；OCI revision label 为完整 `cb1eb0c` SHA |
| 开机管理 | `open-node-compose.service` 为 `enabled`、`active`，从 `/opt/open-node/deploy/.env` 启动持久 Compose |
| 最近部署备份 | `/var/backups/open-node/20260829T123138Z-cb1eb0c`，目录及文件均为 root 私有权限；含状态、配置、源码和精确新旧镜像 |
| 隔离恢复验收 | 独立 project `open-node-restore-cb1eb0c`、独立 volume、`127.0.0.1:18081`；健康、管理员认证、数据库完整性/计数和 identity seed 连续性通过，验收资源已按 project label 清理 |
| Agent 0.2.0 发布 | annotated tag `agent-v0.2.0` 指向 `3bf30c0b488efe6575927d01acca07f6dc0b3662`；GitHub prerelease 恰好包含四项制品，匿名校验及 WebSocket/HTTP 实下载、升级、流量和回滚烟测均通过 |
| Agent 0.3.0a0 发布 | annotated tag `agent-v0.3.0a0` 指向 `6ca84e21202950bf5ee4754a8ae20e28dbde42ed`；release `379344539` 为非 latest 的 alpha。四项公开制品、匿名下载、双通道升级/流量/回滚已验证；面板安装器在更新的控制面提交 `1515a7b`/`a677280` 中，发布资产没有改动 |

生产服务已不再使用 `/tmp` Uvicorn 进程。控制面由持久 Compose 和 systemd 管理，已经
通过备份、服务重启、Compose down/up 和隔离恢复验收。当前访问方式仍是 SSH 隧道
下的 loopback Preview，不是公开 HTTPS 部署。

`cb1eb0c` 已完成并部署默认关闭短码及旧 `/x` 兼容、旧 bearer 一次性轮换、
请求路径 access log 抑制、容器日志上限和 SQLite 每连接外键启用。完整 VPS
回归、旧库迁移和 `foreign_key_check` 均已通过；不得把这些项目重新列为待办。

## 已完成并进入主线

### 基础、授权和安全

- 单仓库工程结构、FastAPI 后端、React/官方 Ant Design 前端、Docker 单镜像和
  Compose 部署已完成；Vue/Vuetify 代码和依赖已经移除。
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
- 面板可为从未连接的新服务器签发十分钟安装命令，下载并校验固定的 Agent 和
  官方 Xray，安装为独立非 root systemd 服务。票据首次领取后不能再次签发；
  领取、注册和安装就绪分别判断，已有目录、账户或服务不会被自动接管。
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
- 公共 probe API、WebSocket、历史序列、筛选、比较、流量热点、健康评分、回程路由和 React 可视化已完成。
- 管理控制台和订阅门户已做桌面与窄屏浏览器检查，主要工作流可用。

更细的功能清单在 [`migration-map.md`](migration-map.md)，各功能的安全边界和复现方式在同目录专题文档中。

## 最新候选验证（2026-08-30/31）

### 外部订阅首期：中文化之前的英文工作源验收

新功能严格区分外部节点和本地托管节点，不伪造 Server，不重发上游凭据，不向
Agent 下发命令，不把上游流量计入本地账单。只有用户主订阅显式合入已确认的
来源；套餐、到期、停用、配额和 IP 检查继续生效，临时链接和命名 profile 不变。
URL、自定义 UA、节点和预览配置加密存储，普通 API 不读回秘密；密钥默认位于
SQLite 数据库旁的 `external-subscriptions`，现有 Docker 数据卷覆盖此目录。

预览只读取上游，不改变在用节点。确认事务同时应用选中的新节点、现有节点
更新和缺失状态，并保存可重复读取的回执。预览 15 分钟过期，每来源最多三个；
已确认回执七天后不可再读或重放。来源/节点修改后旧版本不能覆盖新版本；删除
用户后，迟到的请求不能把旧凭据转给同名新用户。

官方 Mihomo v1.19.30 源码复核补齐了 UDP 默认值和各协议 TLS 字段边界，Snell
版本、Mieru transport、HTTP 认证不明确时不自动猜测。解析器 **402** 项、抓取器
**230** 项和先前的后台集成 **48** 项分别通过；抓取器的新一轮真实 TLS 测试在
独立 network namespace 内完成，没有宿主公网监听。随后组合源完整回归得到
后端 1927 passed / 6 opt-in skipped（该六项真实 TLS 已另外全部通过）、Agent
605、前端 570/65 files、Worker 5；类型检查、双构建及 Ruff 通过。不把专项
计数相加成另一套全量，也不将这份英文源码的通过结果归给后续中文修改。

最终浏览器/流量报告 SHA-256 为
`925896e5ff2f07daa5bd9f4dd61cbc506b5ea2e397dacf712db6c6fb406aae84`。
真实 Ant Design 的创建、编辑、预览、确认和回执恢复通过，1440/390/320 三种
宽度的来源/节点表单及底部操作均可见；15 张截图中的秘密已遮罩。Mihomo 加载
完整主订阅，官方 Xray 完成托管及外部 VLESS 转发，直接访问的负控被拒绝。
凭据轮换、错误输入保留旧配置、缺失密钥拒绝访问、不生成替代密钥、冷备份
恢复和原管理员会话也通过。其他输入协议的解析测试不等于本轮全部协议流量实测。

英文工作树 Docker 镜像也已通过独立数据卷、非 root、双重重启、HTTPS 抓取、
密钥持久化和生产未变检查；其 OCI revision 是工作树标签，不是精确 Git 提交。
该工作源未升级生产。中文复验、完整 CI、精确提交镜像和发布结论要在各自完成后记录；
详细范围与复现见 [`testing.md`](testing.md#external-subscriptions)。

### 上一项 React 功能的精确提交 CI

精确功能提交 `50897f928226c9fef2ab7d0f68de0c3aad46156a` 的
[GitHub run 33330624705](https://github.com/FengYuchen1314/open-node/actions/runs/33330624705)
四个 job 全部成功：后端 **1,253 passed**（618.73 秒）、Agent **605 passed**
（10.67 秒）、前端 **509 tests / 63 files**（535.20 秒）、Worker **5 tests**
（164.53 毫秒）。Ruff、Agent wheel、前端类型检查和双产物、Worker 类型检查
均通过；只有已知的 Starlette/httpx 弃用警告。后续文档提交不是这次 CI 的运行对象。

### React / Ant Design 已发布源码

所有页面已迁入 `frontend/src/react`，入口为 `src/main.tsx` 和
`public-probe/main.tsx`。主线已移除 Vue/Vuetify 代码及依赖，旧版可从 Git
恢复；保留 FastAPI、API/会话契约和 Docker 单镜像。依赖为 React 19.2.7、
Ant Design 6.6.2 和 icons 6.3.2，架构及验收边界见 [`frontend.md`](frontend.md)。

数值输入已统一使用标准 Ant `InputNumber` 的小型适配器，避免失焦或 Enter
把非法配额、连接数、端口改成有效值。输入为空、NaN、下溢、未完成的负号/指数，
以及合法的小数 GiB 分别处理。Probe 设置加载失败时禁止写入；令牌轮换仍需确认。
真实浏览器发现的 loading 按钮名称、上传 File 残留、窄屏弹窗高度、栅格溢出和
探针任务标题被挤掉均已修正。所有业务修改保持既有 API 和计费语义。

第一轮完整 React 回归为 **509 tests / 63 files，全部通过，644.30 秒**，
报告 `/tmp/open-node-react-accessible.OTOVWliF/frontend-tests.json`，无未处理错误。
最终工作源在 `/tmp/open-node-react-release.xaSu8WDc/source`，双产物构建通过，
资产包 SHA-256 为
`da85b9cc62b5d78dfae10dbb2f85d3d4ff79e935514f894c67761eabfc64fb4c`。
相对第一轮全量测试，产品仅追加四个组件的布局修正，分别有专项单测或实机回归；
最终源随后完成第二次全量回归：同样 **509/509、63 个文件，638.05 秒**，
无未处理错误。提交后的 GitHub CI 与 VPS 全量回归是独立运行，不能把测试数
相加，也不能用旧 Vue CI 代替。

最终源的管理员操作、MFA 和订阅账户双通道浏览器流程已通过。Docker 另验证了
39 个文件的三方哈希、深链/404、三种屏宽登录及重启后原会话和数据保留，报告为
`/root/open-node-react-bootstrap-browser.gEUopOkd/r5-docker-4/report.json`。
仅清理测试所属容器和卷，生产未改变。订阅/用户管理的十组完整门禁
也已通过；套餐、别名、自动限速和用户限额使用第四版隔离包复验，原生限速
使用第三版验证 18 协议及 1440/390/320 控件。旧 MMWX 导入另用官方 Xray
26.3.27 验证标准 VLESS，不能据此将扩展协议算作官方内核支持。精确目录和
不同构建的归属见 [`testing.md`](testing.md)，不要把分批测试数相加。

随后从 GitHub 干净检出精确 `50897f9`，重新安装依赖并构建，两份产物共
**42 个文件**与最终包逐字节一致。使用纯 Git 归档另建 Docker 镜像，完整 OCI
revision 为 `50897f928226c9fef2ab7d0f68de0c3aad46156a`，再次通过资源、路由、
三种屏宽登录、原会话及数据重启保留和八项清理安全负控。最终归属报告：
`/root/open-node-react-commit-50897.0MDZIwd3/source-proof.json`，SHA-256 为
`ff6cc9c18e7507f7311493ee94776b9489d7c1aea4357654fa48c8a7a4004a04`。
镜像仅留作验证；测试容器和卷已清理，未用于升级生产。

最终公共 Probe 的 JS/CSS 与已通过真实 Worker、匿名请求、断线重连和三种
主题的包逐字相同，报告仍在
`/tmp/open-node-react-browser.NyIq0V6p/public-probe-theme/report.json`。
独立只读复核未发现本轮会话、MFA、异步 scope 或公共请求边界的新安全阻断。
生产仍是 `open-node:cb1eb0c`，共享候选仍保留 clean `6ca84e2`；重写源码已进入
主线，但这不代表四个官方仓库的全部功能已经对等。

### 前一批 Agent 安装器验证（历史记录）

`a677280` 已通过独立 clean-checkout GitHub CI：后端 **1,253 passed**、Agent
**605 passed**、当时的 Vue 前端 **268 tests / 37 files**、Worker **5 tests**
及双产物构建。首个安装功能提交的 CI 在 host 安装夹具中误用 runner 的 `/opt`，
导致权限预检失败；修正只将测试安装基目录隔离并明确 fixture 权限，生产默认仍为
`/opt`，路径安全检查未放宽。VPS 另有 **323** 项专项测试、
`nobody`/`umask 0002` 下 **124** 项安装器测试，以及精确修正版安装字节的真实
WebSocket/HTTP 安装复验通过。这些历史结果不能替代上方 React 验收。

本轮安装功能的完整后端回归为 **1,250 passed**（694.29 秒）。提交后核对了
`1515a7b` 的全部 142 个后端 tracked 文件，共 2,201,738 字节，与该回归源树和
Git blob 一致。精确提交又通过前端 **268** 项、Worker **5** 项、两种前端构建、
桌面/手机浏览器、53 项 Compose 预检和两项测试隔离负控。

安装链路另外使用公开 Agent 0.3.0a0 资产，分别完成 WebSocket、HTTP 真正安装、
非 root 进程与运行时就绪、VLESS/HTTP 转发、流量上报、重放拒绝和重复安装拒绝。
根安装脚本完成同 SHA 的启用、关闭与无变化更新，保留管理员、库存及两份不可变
备份；镜像内资源和 HTTP 接口在三种状态下均通过。真实安装验证使用根 HTTPS URL
和逐项指定的 transport，不能据此宣称反代子路径或 Auto 降级已端到端实测。

生产容器 `c2594ea5…`、镜像、启动时间 `2026-08-29T12:59:02.442246035Z`、
重启次数 0、环境摘要、网络和数据卷均未改变。临时服务、账户、容器、卷和依赖
链接已按所属测试清理，脱敏报告保留。复现与精确路径见 [`testing.md`](testing.md)。
以下 MFA 和公共探针记录保留为前一批验收证据，不能与新安装功能的测试计数相加。

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
git ls-remote --tags origin "refs/tags/agent-v0.3.0a0*"
ssh root@185.99.135.224 "git -C /opt/open-node status --short; git -C /opt/open-node rev-parse HEAD; systemctl is-enabled open-node-compose.service; systemctl is-active open-node-compose.service; curl -fsS http://127.0.0.1:8000/healthz"
```

随后阅读：

- [`migration-map.md`](migration-map.md)
- [`testing.md`](testing.md)
- [`fork-runtime.md`](fork-runtime.md)
- [`subscriptions.md`](subscriptions.md)
- [`native-limits.md`](native-limits.md)
- [`deployment.md`](deployment.md)
- [`agent-bootstrap.md`](agent-bootstrap.md)
- [`external-subscriptions-plan.md`](external-subscriptions-plan.md)
- [`external-subscriptions.md`](external-subscriptions.md)
- [`notifications-plan.md`](notifications-plan.md)（首批实现边界与官方源码接点）
- [`notifications.md`](notifications.md)（已发布的首批通知功能用法与真实投递边界）

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
caf016c docs: record verified Chinese and external subscription publication
998839b feat: add encrypted external subscriptions and Simplified Chinese UI
50897f9 feat(frontend): migrate console and portal to React and Ant Design
a677280 Isolate Agent bootstrap ownership fixtures from hosted runner paths
1515a7b Add pinned panel-issued Agent bootstrap with single-host tickets
4749a12 Record verified MFA and Probe publication in project handoff
6ca84e2 Verify public Probe browser gate with native Cloudflare runtime
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
