# Backend 实现

## 入口与装配

Backend 是 Python 3.11+ 的 FastAPI 应用，包名为 `open_node`。容器以
`uvicorn open_node.main:app` 启动；本地或测试可调用
[`create_app`](../../backend/app/open_node/main.py) 注入独立 `Settings`。

| 入口 | 职责 |
| --- | --- |
| [`main.py`](../../backend/app/open_node/main.py) | 创建应用、装配 store/service、注册中间件与异常处理、管理 lifespan worker |
| [`core/config.py`](../../backend/app/open_node/core/config.py) | 从 `OPEN_NODE_*` 读取配置，校验数据库 URL、状态路径、可信来源和安全默认值 |
| [`core/authority.py`](../../backend/app/open_node/core/authority.py) | 规范化精确 HTTP authority，并在路由前拒绝缺失、重复、非法或未信任的 Host |
| [`api/router.py`](../../backend/app/open_node/api/router.py) | 汇总管理、账号、Agent、公开与兼容路由 |
| [`web.py`](../../backend/app/open_node/web.py) | 生产静态前端、SPA fallback、缓存与范围请求 |
| [`admin.py`](../../backend/app/open_node/admin.py) | `open-node-admin` 本地管理员和首次初始化 CLI |
| [`backup_cli.py`](../../backend/app/open_node/backup_cli.py) | `open-node-backup` 本地备份 CLI |
| [`browser_restore_activate.py`](../../backend/app/open_node/browser_restore_activate.py) | 容器启动前激活已确认恢复并输出恢复根 |
| [`script_worker.py`](../../backend/app/open_node/script_worker.py) | 隔离执行订阅 QuickJS hook 的子进程入口 |

`create_app` 先限制 PostgreSQL 应用角色，再创建跨进程 backup write barrier。构造 store 和
执行 schema 初始化也进入这个 barrier，避免应用启动迁移与备份快照并发。`_create_app`
把对象挂到 `app.state`，路由通过 dependencies 取得，不使用模块级可变单例。

主要 `app.state` 对象包括 `AuthStore`、`InventoryStore`、`SecurityStore`、
`CertificateStore`、`ServerSharingStore`、`BackupJobManager`、`BrowserRestoreStore`、
`AgentConnectionManager` 和测速/通知/外部订阅等协调器。测试创建多个 app 时，每个实例有
独立状态和关闭流程。

## 分层约定

```text
api/routes/*       HTTP 路径、依赖、状态码和错误映射
api/*.py           跨路由协议边界，如认证、备份中间件、上传流
domain/*           Pydantic 请求/响应、枚举、值对象和纯校验
services/*         SQLAlchemy store、业务事务、文件事务、后台 worker、外部适配
resources/*        固定发布元数据和面板 Agent 安装器资源
main.py            组合根
```

这个划分不是严格的 DDD：部分大型 store 同时定义 SQLAlchemy model 和业务方法，尤其是
[`services/inventory.py`](../../backend/app/open_node/services/inventory.py)。新增代码应先沿用
所属功能已有边界，避免在 route 中另建数据库连接或直接改状态文件。

## API 路由

[`api/router.py`](../../backend/app/open_node/api/router.py) 的装配顺序表达访问边界：

- `private_router` 统一依赖 `require_administrator`，包含服务器、备份、证书、DDNS、变更集、
  用户、套餐、通知和系统设置等管理接口。
- `auth`、`initial_setup` 和 subscriber account 路由使用各自会话协议，不挂到管理员私有
  router 下。
- `agents` 与 Agent bootstrap 公共兑换接口使用服务器 Token 或一次性 ticket；它们不是
  匿名管理接口。
- branding、appearance、subscription、temporary link 和 Probe 的明确子集允许公开读。
- `/api/remote/ws` 等兼容入口单独注册，仍需 Agent 身份和 TLS，不绕过命令持久化。

路由文件按功能成对对应 `domain/<feature>.py` 与 `services/<feature>.py`。自动清单列出
[全部 route 文件及符号](source-inventory.md#backend--运行代码)；维护时可按下表快速定位。

| 功能组 | Route | 主要 service/store |
| --- | --- | --- |
| 身份与设置 | `auth`、`initial_setup`、`system`、`branding`、`appearance`、`security` | `AuthStore`、`InitialSetupStore`、`BrandingStore`、`AppearanceStore`、`SecurityStore` |
| 服务器与 Agent | `servers`、`agents`、`agent_bootstrap`、`server_management`、`node_management`、`changes` | `InventoryStore`、`AgentConnectionManager`、`AgentBootstrapStore`、`ServerManagement`、`ChangeSetCoordinator` |
| 订阅与用户 | `subscriptions`、`user_management`、`plan_management`、templates/profiles/customizations/scripts | `InventoryStore` 及订阅渲染、权限、访问协调服务 |
| 运维能力 | `backups`、`certificates`、`ddns`、`speedtests`、`probe`、`notifications` | 对应 store、worker 和外部适配器 |
| 联邦与外部数据 | `server_sharing`、`external_subscriptions`、`private_routed_nodes`、`renewals` | `ServerSharingStore`、外部订阅快照/刷新、续费与私有路由服务 |

## 数据库与事务

### InventoryStore

[`InventoryStore`](../../backend/app/open_node/services/inventory.py) 是服务器、Agent、命令、
扫描、流量、用户、套餐、节点、订阅凭据、Probe 与变更集的主要持久化门面。文件中同时
定义这些表的 SQLAlchemy models。关键入口包括：

- `create_inventory_engine`：创建 SQLite 或 `postgresql+psycopg` engine；SQLite 连接启用
  foreign keys。
- `create_schema`：创建所有 feature metadata，执行受支持的 SQLite 增量迁移、变更集旧
  数据迁移和流量回填，并在迁移前后检查外键完整性。
- `begin_serialized_write`：SQLite 使用即时写事务，PostgreSQL 使用 namespace advisory
  lock，为需要串行化的复合更新提供共同语义。
- 命令方法用条件更新完成 lease、终态提交和依赖推进，防止 WebSocket 与 HTTP transport
  同时领取同一命令。

部分功能在独立 service 文件中定义自己的 metadata 和 store，但复用 InventoryStore 的
engine 或同一个数据库 URL。`create_schema` 显式导入这些 models 后统一建表，不能只运行
某个 route 并假设表会延迟创建。

### SQLite 与 PostgreSQL

`Settings.supported_database_url` 只接受 SQLite 和 `postgresql+psycopg`。SQLite 是默认
后端，包含仓库内的增量列迁移；PostgreSQL 面向 fresh deploy，schema 来自当前 revision
的 metadata 创建。应用启动会调用
[`restrict_postgres_application_role`](../../backend/app/open_node/services/postgres_security.py)
验证专用角色没有 superuser、CREATEROLE、replication、BYPASSRLS 或异常成员关系。

这不是通用迁移框架。选择 PostgreSQL 不表示旧 SQLite/MMWX 数据可以自动转换，也不表示
任意 revision 的 dump 都可恢复到当前 schema。

## 身份与响应安全

[`AuthStore`](../../backend/app/open_node/services/auth.py) 保存管理员、Argon2id 密码哈希、
TOTP、恢复码、登录窗口、challenge 和 opaque session 哈希。FastAPI 依赖
[`require_administrator`](../../backend/app/open_node/api/auth.py) 在每次管理请求中校验
会话、绝对/空闲过期、credential version 与 CSRF/Origin；密码修改会撤销全部会话。

订阅用户身份由 `subscriber_auth.py` 独立实现，Agent 使用服务器 bootstrap Token，公开
订阅使用专用 bearer，Probe Worker 使用 Probe Token。代码不能把一种 token 传入另一种
store 作为便捷认证。

`main.py` 的响应中间件对登录、备份、恢复、bootstrap、订阅账号、通知、联邦等敏感路径
设置 `no-store` 和 `no-referrer`。错误处理对这些路径只返回受控字段，避免 Pydantic 输入
或外部命令的秘密出现在响应。业务响应中的 `license_required` 兼容字段固定为
false；`/meta` 则原样报告 `Settings.license_required`，不应把它当作已实现的授权门禁。

[`TrustedAuthorityMiddleware`](../../backend/app/open_node/core/authority.py) 是最外层的 HTTP/
WebSocket 主机头门禁。`Settings.trusted_authorities` 非空时，每个 scope 必须只有一个
`Host` 或 `:authority`，并与配置的 DNS、IPv4 或带方括号 IPv6 authority 精确
匹配。规范化只折叠 ASCII 大小写；端口、IPv6 括号和其他字面形式都是信任边界的
一部分。空配置会跳过检查，用于本地开发和使用临时 host 的测试，不应被视为一项
开启中的生产保护。

## Agent 连接与命令

[`AgentConnectionManager`](../../backend/app/open_node/services/agent_ws.py) 保存当前进程内的
活动 WebSocket，不承担命令持久化。命令事实来源仍是 `InventoryStore`：

1. 管理 route 或协调器创建 `pending` 命令；有前置步骤的后继命令为 `waiting`。
2. 活动 RPC socket 尝试领取并推送；无 socket 时由 HTTP lease 接口领取。
3. `rpc_reply` 或 HTTP result 通过条件更新写入终态。成功推进直接后继，失败把剩余后继
   标记为 `skipped`。
4. stream frame 按序持久化，最终 reply 与普通命令使用相同终态规则。

`secure_channel.py` 只为配置了固定 Agent identity 的兼容连接增加 X25519/Ed25519/HKDF/
AES-GCM 帧；TLS、Token 认证和重放检查仍存在。`federation_crypto.py` 与这一连接身份不是
同一密钥域。

## 备份与浏览器恢复

备份子系统刻意拆成较小的边界：

| 文件 | 职责 |
| --- | --- |
| [`backup_coordination.py`](../../backend/app/open_node/services/backup_coordination.py) | 跨线程/进程写 lease 与 snapshot permit；备份期间阻止新写入 |
| [`backup_snapshot.py`](../../backend/app/open_node/services/backup_snapshot.py) | 根据 Settings 组装数据库与文件状态 layout，协调一致性快照 |
| [`backup_sqlite.py`](../../backend/app/open_node/services/backup_sqlite.py) | 有预算的 SQLite backup、integrity/FK 检查与源 inode 校验 |
| [`backup_postgres.py`](../../backend/app/open_node/services/backup_postgres.py) | 以无密码命令行参数调用固定 `pg_dump` custom format，并计算指纹 |
| [`backup_state.py`](../../backend/app/open_node/services/backup_state.py) | certificates、federation、notifications 等状态切片 |
| [`backup_archive.py`](../../backend/app/open_node/services/backup_archive.py) | 确定性 ZIP manifest 和有界归档读写 |
| [`backup_encryption.py`](../../backend/app/open_node/services/backup_encryption.py) | 调用 age 加密/解密并验证 recipient/输出 |
| [`backup_creation.py`](../../backend/app/open_node/services/backup_creation.py) | 组合快照、依赖检查、manifest、归档和密文保留 |
| [`backup_jobs.py`](../../backend/app/open_node/services/backup_jobs.py) | Web 备份任务、一次下载和关闭语义 |
| [`browser_restore.py`](../../backend/app/open_node/services/browser_restore.py) | 上传、审阅、准备、pending marker、启动时激活和恢复清理 |

`BackupHTTPMiddleware` 让普通写请求取得 write lease；Agent 长连接按实际消息/操作取 lease，
不会因一个空闲 socket 永久阻塞备份。创建备份时只把完整密文交给下载层，明文归档和临时
数据库在 context 退出前关闭。

浏览器恢复先写隔离 staging，验证 archive、数据库和状态，再要求操作者确认。激活期间
`RestoreState` 阻断普通 worker 和写路径。PostgreSQL 使用独立 staging database 的
create/restore/rename 流程；SQLite 替换受控数据文件。恢复契约见
[备份文档](../backups.md)，不能从这些内部函数推导更宽的版本兼容性。

## 外部进程与网络适配

- 证书 worker 调用固定 lego binary；子进程有锁、超时和进程组清理。DNS provider 凭据与
  证书 vault 分开保存。
- `external_fetch.py` 限制 URL、响应大小、重定向和内容类型；刷新先生成 snapshot，再由
  显式确认或 worker 更新。
- `script_runtime.py` 把 QuickJS hook 放入受限子进程，限制输入、时间、内存和输出。脚本
  失败不会覆盖已确认外部订阅快照。
- DDNS、Telegram、IPinfo、ACME、联邦 transport 均为出站适配；route 不应把第三方原始
  错误或 credential 直接返回客户端。

## 后台 worker 与关闭

lifespan 启动 Certificate、SubscriptionAccess、ServerTraffic、Notification、
ExternalRefresh、DDNS 和 FederationRefresh worker，并启动可选 `BackupJobManager`。每个
实际写周期自己进入 backup barrier。关闭时取消循环、关闭测速协调器与测速端连接，再等待
备份生产线程释放资源。

新增 worker 时需要同时回答四件事：写操作怎样进入 barrier，是否支持取消，持有的文件/
网络资源怎样关闭，浏览器恢复阻断期间是否允许构造和运行。

## 测试与修改检查

Backend 测试位于 `backend/tests/`，自动清单逐文件列出测试函数。CI 使用 12 个确定性
pytest shard，另有独立 Ruff 和真实 PostgreSQL job。常用本地门槛：

```bash
python -m ruff check backend
python -m pytest backend/tests
```

数据库、备份、认证和路径所有权修改至少应增加失败路径测试；只验证成功响应不足以覆盖
并发 lease、进程中断、符号链接/硬链接、外部文件变化和秘密泄露边界。
