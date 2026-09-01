# 应用内备份：官方依据与实施边界

状态：当前源码已经接入 v1 清单与受限 ZIP、官方 age 加密、运行时停写协调、
SQLite/PostgreSQL 一致快照、管理员网页创建/下载、浏览器上传恢复、无管理员首次初始化
恢复，以及隔离复核。格式细节见[备份包 v1 格式](backup-format.md)，操作方法见
[备份与恢复](backups.md)，运行时合同见[一致快照说明](backup-runtime.md)。

本阶段只服务**全新 Open Node 部署**。SQLite 是默认数据库；安装器可在新主机上选择
PostgreSQL。两种后端不能在安装后互换，备份也不是数据库转换工具。本项目不导入原版
MMWX ZIP、不迁移旧 MMWX 数据库、不接管旧主机，也不因参考备份功能加入许可证或 Bot。

早期发布的 `2a28103` 只包含格式校验，`a29345b` 增加固定官方 age v1.3.2；不能用这些
历史提交的能力范围判断当前源码。浏览器 SQLite 恢复曾在 `776b36f`/`ee167cc` 发布，
PostgreSQL、联邦状态和安装器新装支持属于后续当前候选。生产实例没有升级，最终发布
状态以对应提交及 GitHub CI 为准。

## 固定官方依据

主参考固定为 `tajiaoyezi/miaomiaowuX` 的
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`，不随主分支静默变化。

- [路由注册](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/cmd/server/main.go#L422)
  提供管理员下载、上传恢复；初始化还另有无用户时可调用的恢复入口。
- [下载处理](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/backup.go#L30)
  在 SQLite checkpoint/健康检查后打包 `data/`、`subscribes/`，也能按选择生成
  PostgreSQL custom dump。新导出为普通 ZIP，旧加密包只在导入时按原口令解密。
- [恢复处理](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/backup.go#L425)
  的 PostgreSQL 分支先运行 `pg_restore --list`，在 `template0` 新建的暂存数据库中
  恢复和验证，成功后才切换；失败则删除暂存库，不触碰当前库。SQLite 分支直接替换
  当前目录文件，返回“正在重启”本身并不能证明切换已经发生。
- [解压循环](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/backup.go#L805)
  虽有路径检查和单文件临时替换，但请求体 1 GiB 上限不等于总展开资源上限，也不能
  使多文件替换自动成为整体事务。
- [SQLite Online Backup helper](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/storage/database_backup.go#L56)
  使用 SQLite Online Backup API、验证新快照并在失败时保留旧备份；官方下载 handler
  没有调用它，数据库一致性本身也不覆盖独立密钥文件。

2026-08-31 查阅的[官方备份文档](https://miaomiaowux.com/docs/en/backup-restore/)
仍将 SQLite ZIP 与 PostgreSQL 操作分开说明；这里以固定源码的实际控制流为准，不把
移动网页文档当作该提交的执行结果。MMW 的远程抓取、上传和合并导入属于另一条迁移
流程，本项目明确不实现这条旧迁移路径。

## 当前创建流程

1. 实例写入屏障暂停新操作，并等待已有 HTTP 写入、Agent 消息、后台任务、子进程和
   受支持管理员 CLI 完成。超时返回忙碌，不终止任务来制造成功结果。
2. SQLite 通过 Online Backup API 生成独立快照并检查完整性/外键；PostgreSQL 通过镜像
   中的 `pg_dump --format=custom --compress=6 --no-owner --no-privileges` 写入私有文件，
   检查 `PGDMP` 头、`pg_restore --list` 和模式指纹。
3. 在同一许可内复制证书/ACME、外部订阅、联邦共享、通知和 Agent 身份状态。状态树
   逐层拒绝符号链接、硬链接和特殊文件，不能从上传清单取得任意主机路径。
4. 对 SQLite 或 PostgreSQL 的受支持表投影执行密钥依赖核对，再生成受限 v1 ZIP 并由
   独立验证器复核。正式创建器不允许 TOTP 依赖处于 `not_checked`，也不允许有数据库
   密钥依赖的状态角色被标成缺失。
5. 用固定官方 age 对完整 ZIP 加密。管理员网页只下载已完成的匿名只读密文；任务失败、
   超时或调用方退出不会发布半成品，也不会覆盖上一份制品。

屏障是本项目进程间的协作协议，不能强制阻止未知程序直接写数据库或状态目录。当前
支持 Linux、受支持本地文件系统、普通 SQLite 文件 URL 和严格的
`postgresql+psycopg` URL；其他驱动、内存库和 SQLite URI 会被拒绝。创建器可以读取
配置中明确指定且通过布局检查的状态目录；网页原位激活只支持默认同根状态布局。

## 当前网页恢复流程

上传入口只接受本项目 v1 ZIP 或对应的 age 密文。它先在私有上传区完成身份、Origin/
CSRF、大小、摘要、归档和密钥依赖检查，不向现有数据目录逐文件覆盖。包中任何
`coverage=unknown`、缺少所需 TOTP 环境密钥、数据库类型与当前部署不匹配，都会在激活
前失败。原版 MMWX ZIP、安装器 tar、裸 SQLite 文件和裸 PostgreSQL dump 都不接受。

SQLite 恢复在新的私有目录打开副本，检查模式边界、完整性、外键、恰好一个管理员和
已知密钥依赖，再注销旧会话、撤销一次性票据并暂停外发任务。应用重启前通过持久事务
日志把旧顶层状态移动到 rollback 目录，再安装完整新树；中断后按日志继续，不把混合
目录当成成功结果。

PostgreSQL 恢复遵循固定官方核心语义：

1. 先以 `pg_restore --list` 验证 custom dump；
2. 随机生成暂存库名，以 `CREATE DATABASE ... TEMPLATE template0` 创建；
3. 使用 `--exit-on-error --single-transaction --no-owner --no-privileges` 完整恢复；
4. 在暂存库检查受支持表、管理员、TOTP 与证书/外部订阅/联邦/通知密钥依赖，并执行
   会话失效和任务暂停策略；
5. 只有上述步骤全部成功，才发布恢复日志并请求重启；启动时先把当前库改名为 rollback
   库，再把暂存库改成配置中的当前库名，然后切换应用状态树；
6. 准备阶段任何失败都强制删除暂存库且不改当前库。启动切换可根据持久阶段日志恢复，
   不用“已经返回正在重启”冒充完成。

PostgreSQL 浏览器恢复要求数据库账户能创建、改名、终止连接和删除数据库。命令行
`open-node-backup restore` 目前仍只面向 SQLite 新目录；PostgreSQL v1 包使用配置已知的
浏览器入口恢复，避免让通用 CLI 猜测目标连接或数据库权限。

恢复会保留历史记录，但默认清除管理员/订阅用户会话和挑战、取消未完成 TOTP 绑定、
撤销 Agent 安装票据，将在途 Agent 命令置为失败、变更集置为待复核、证书任务置为
失败，并关闭自动续签、部署、通知和外部订阅定时刷新。旧证书任务及 HTTP-01 webroot
进入隔离区；恢复实例不会借旧记录自动清理原主机目录。

## 持久化范围

| 角色 | 默认来源 | 必须保留或核对的内容 |
| --- | --- | --- |
| 控制面数据库 | SQLite 为 `/var/lib/open-node/open-node.db`；PostgreSQL 为独立 `postgres-data` 卷 | 整库，包括认证、会话、订阅、Probe、Agent 凭据及待办状态；PG 使用逻辑 custom dump，不复制在线数据目录 |
| 证书与 ACME | `/var/lib/open-node/certificates` | vault 密钥和初始化标记、账户、证书及私钥、未完成任务状态 |
| 外部订阅密钥 | `/var/lib/open-node/external-subscriptions` | `vault.key`、`vault.initialized`，与库内 source/node/preview 密文配套 |
| 联邦共享密钥 | `/var/lib/open-node/federation` | `vault.key`、`vault.initialized`，与库内共享服务器 Token 密文及 owner URL 绑定配套 |
| 通知密钥 | `/var/lib/open-node/notifications` | `telegram.key`、`telegram.initialized`；禁用通知不表示可删除已有密钥 |
| 控制面 Agent 身份 | `OPEN_NODE_AGENT_IDENTITY_FILE` 指定文件 | 原 32 字节 seed 与当前运行公钥匹配；不包含远端 Agent 文件 |

数据库 URL、上述自定义目录、`OPEN_NODE_SUBSCRIBER_TOTP_KEY`、Cookie/CORS、ACME、公网
地址、Compose 端口和代理信任仍属于外部部署配置。证书 webroot 绑定中的宿主路径和
device/inode 只能保留供人工核对，不能在新主机上自动取得权限。

## 安装器停服包

`install.sh` 的 `backup_stopped_volume` 是另一种只供安装器更新/回滚使用的格式，不是
网页 v1 包。SQLite 分支在停服后归档应用状态并检查数据库；PostgreSQL 分支归档应用
状态，同时生成 `postgres.dump`，先运行 `pg_restore --list`，再完整恢复到临时
`template0` 数据库验证，并将 PG Compose overlay、摘要和部署身份写入恢复包。

Web 进程不会因此取得 Docker socket。安装器包也不能上传到网页入口；必须按部署文档
在隔离项目中恢复，核对登录、密钥和远端身份后再切换流量。

## 明确不实现

- 原版 MMWX ZIP、旧数据库、旧主机或 Agent 的自动迁移/接管；
- SQLite 与 PostgreSQL 之间的转换，或安装后切换数据库后端；
- 远端 Agent 文件、信任和在线状态的自动恢复；
- 远程 URL 导入、自动异地上传、许可证服务、Bot/Mini App；
- 自动修改生产反向代理、DNS、防火墙，或自动删除 rollback 数据。

## 发布与验收边界

- 管理员身份、重新验证、Origin/CSRF、禁止缓存、限时下载与撤销检查；匿名和订阅用户
  不能创建、查看或下载管理员备份。
- 并发写入时的一致快照；活跃任务、超时、中断、磁盘满、坏库和 PostgreSQL 子进程
  失败均不能破坏当前实例或发布半成品。
- 缺失/错误密钥、篡改清单、恶意归档、不支持版本和数据库类型不匹配，在激活前拒绝。
- PostgreSQL 必须实际验证 custom dump、临时恢复、失败删库和带中断恢复的改名切换；
  SQLite 必须验证独立数据库和整个状态树切换。
- 在新的隔离实例核对用户、订阅、证书、联邦共享、通知和远端身份后，才允许操作者
  切换访问。备份成功或数据库切换成功都不等于远端系统已经恢复信任。
- 最终候选仍需中文真实浏览器、相关回归、精确 Git 镜像、全新 SQLite/PostgreSQL
  一键安装及指定 VPS 隔离验收；生产实例不作为试验目标。
