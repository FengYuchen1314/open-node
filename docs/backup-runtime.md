# 一致快照与加密备份创建器

状态：写入协调、SQLite/PostgreSQL 数据库与状态快照、依赖检查、加密创建器及网页任务
均已接入当前源码。历史回归和修正记录见[测试说明](testing.md)；发布检查以对应提交的
GitHub CI 为准，生产实例没有升级。

本页描述内部服务；网页任务、身份验证、限时下载与限制见[网页备份用法](backups.md)。
现有 `open-node-backup encrypt` 仍只加密已经完成的 v1 ZIP，不能代替这里的快照流程。
管理员创建任务、重新验证身份和下载期间的撤销检查已接通。
离线新目录恢复与首次启动复核见[备份与恢复](backups.md)，不属于本创建器接口。

## 官方依据

参考固定官方控制面 `c12ce653bc07fe30426b7dfcb85076974b7be0e0` 的
[下载处理](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/backup.go)
和 [SQLite Online Backup helper](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/storage/database_backup.go)。
两者是不同代码路径，官方下载处理并没有调用这个 helper。
本项目的 SQLite 分支复用 Online Backup 思路；PostgreSQL 分支按固定代码中的做法使用
custom-format `pg_dump`，恢复前以 `pg_restore --list` 检查并还原到 `template0` 新建的
暂存数据库，验证成功后才切换。两条分支都要协调本项目自己的连接池、后台任务及
独立密钥文件；不能把官方的目录名称或明文 ZIP 直接套到本项目。
更多代码与文档差异保留在[备份计划](backup-plan.md)。本期不引入许可证服务。

## 已实现的处理过程

1. 通过实例的写入屏障等待在途操作结束。HTTP 响应清理、Agent 消息、实际工作线程、
   证书子进程、应用初始化和受支持的管理员 CLI 都参与协调。
2. 在短暂的独占阶段，SQLite 用 Online Backup API 复制已提交视图；PostgreSQL 用
   `pg_dump --format=custom --compress=6 --no-owner --no-privileges` 生成逻辑快照并检查
   `PGDMP` 头与 `pg_restore --list`。随后复制证书、ACME、外部订阅、联邦共享、通知及
   Agent 身份文件；数据库与文件共用同一份许可。
3. 释放独占许可，应用可以继续处理操作。后续步骤只读取已完成的私有副本。
4. SQLite 检查完整性和外键；两种数据库都检查已知表投影、密钥标记和实际密文依赖，
   生成并独立校验 v1 ZIP。
5. 调用固定官方 age 加密。全部明文文件和连接关闭后，才交出一个匿名、只读的密文
   文件句柄。失败或调用方退出时关闭句柄，不发布半成品。

写入屏障仅约束遵守协议的本项目进程，不能阻止任意外部程序直接修改数据库或文件。
当前仅支持 Linux、受支持的本地文件系统，以及普通 SQLite 文件 URL 或完整的
`postgresql+psycopg` URL。内存库、SQLite URI、其他数据库驱动和任意外部直写均不在
支持范围；PostgreSQL 还要求镜像中的固定 `pg_dump`/`pg_restore` 工具与具备相应权限的账户。

实现入口：
[运行时协调](../backend/app/open_node/services/backup_runtime.py)、
[HTTP 协调](../backend/app/open_node/api/backup.py)、
[整体快照](../backend/app/open_node/services/backup_snapshot.py)、
[PostgreSQL 快照](../backend/app/open_node/services/backup_postgres.py)、
[加密创建器](../backend/app/open_node/services/backup_creation.py)。
创建器必须在自己的实际工作线程中调用，不能放进已经持有普通工作租约的请求或
线程包装器里，否则会与自身冲突并返回忙碌。它也不是可直接挂到 FastAPI
`BackgroundTasks` 的作业队列。

## 数据库和文件副本

SQLite 源连接只读，先固定已提交视图，再分页复制；不对活库使用 `immutable=1`，
不 checkpoint、VACUUM 或发出写入 SQL。SQLite 仍可能维护自身的 WAL 共享内存信息，
因此这里不承诺源文件系统完全没有写入。

复制完成后关闭所有可写数据库句柄，以独立只读连接检查新数据库，再移除临时文件名。
结果保留只读文件流与已经打开的只读连接，后者只能在创建线程内借用。
VPS 实测不能通过匿名文件的 `/proc/self/fd` 路径重新打开 SQLite，因此没有采用
重新开库、`deserialize` 或额外复制整库的回退方案。

PostgreSQL 不复制数据卷页面。`pg_dump` 输出直接流入私有暂存文件，限制大小与执行时间，
随后用目录清单生成模式指纹；数据库依赖则在独立的 repeatable-read 只读事务中按受支持
表与列抓取。网页恢复会先创建随机暂存库，完整还原并复核依赖、管理员数量和暂停策略；
任一阶段失败就强制删除暂存库，不改当前数据库。启动激活时通过带持久日志的数据库改名
切换，并保留旧库为 rollback 数据库，避免把半恢复数据库暴露给应用。

状态目录逐层检查权限和链接，拒绝符号链接、硬链接、特殊文件及与暂存目录重叠的
布局。证书目录保留未完成任务和 ACME 状态，不把整个 jobs 目录当缓存删除。
允许忽略的仅是空的指定运行锁文件。其他角色只接受 v1 定义的密钥和标记名称。
状态文件写入同一个匿名暂存文件，每个条目有独立只读视图；4095 个状态文件不需要
同时打开 4095 个文件句柄。

## 依赖检查能证明什么

| 数据 | 已检查 | 仍不证明 |
| --- | --- | --- |
| 证书 | vault 密钥及标记、已存密文、证书与私钥配对、已知记录引用；保留过期材料 | 现有密文格式没有记录身份或用途绑定，不能据解密成功证明没有发生行间交换；不验证 CA 端账户状态 |
| 外部订阅 | 密钥及标记、source/node/preview 密文的用途和归属绑定、URL 摘要 | 上游服务当前可访问或凭据仍然有效 |
| 联邦共享 | vault 密钥及标记、共享服务器 Token 密文的服务器、owner URL 与用途绑定 | 远端控制面仍信任该 Token、当前在线或远端状态已备份 |
| 通知 | 密钥及标记、持久指纹和 Token 密文；禁用状态也检查已有依赖 | Telegram 实际投递成功 |
| TOTP | 对所有已有活跃、待确认及挑战密文用当前配置密钥校验用户名绑定 | 现有格式不区分 active/pending 用途；加密密钥不会随包自动恢复 |
| Agent 身份 | 配置的 32 字节 seed 与当前运行身份公钥匹配 | 远端 Agent 当前信任、连接状态或远端文件已备份 |

仅存在空闲证书配置或排队任务，不代表已经产生密钥依赖。报告将“模块存在业务记录”
与“数据库确实依赖密钥”分开；完整布局中没有该角色文件且没有密钥依赖，才能标为
`not_configured`。存在文件则按实际内容检查，不能因为功能被禁用而忽略。

单独的依赖检查器允许报告 TOTP `not_checked`；正式创建器在此状态下拒绝生成备份。
显式配置了 Agent 身份文件时，也必须验证它与运行身份匹配。部署设置始终是外部依赖，
存在 TOTP 密文时另声明 `subscriber_totp_key`。来源 Git/镜像信息可以为空；提供的信息
仍只是部署方声明，不是经认证的发送者身份。所有结果的 `restoration_ready` 仍为 false。

## 资源边界与验收范围

数据库与状态文件合计最多 1 GiB、4096 个条目，数据库占其中一个。每一步另有有界
读写和元数据限制；依赖检查最多 100000 行、单份密文 8 MiB，总密文、明文和状态检查
各 64 MiB。超限表示当前检查器不支持该输入规模，不能直接称为数据损坏。

入场等待最多 15 秒，复制、校验等步骤分别有操作间软期限；官方 age 子进程另有
超时终止与回收。取消等待线程的异步任务并不等于线程已停止，后续作业管理仍须管理
实际线程和文件句柄的寿命。

完整快照、ZIP、age 的 ZIP 副本和密文会在生成期间同时占用空间。创建器在已复制快照
后检查后续空间需求，并保留清理路径；配额或并发占用仍可能造成 ENOSPC。
默认容器 64 MiB tmpfs 不具备处理 1 GiB 实例的能力。旧加密层真实 1 GiB 验收不等于
当前 SQLite/PostgreSQL 全实例创建器已做实际 1 GiB 验收。状态复制实测了 4095 文件及低
文件句柄上限；容量拒绝分支使用受控较小输入，结论不混用。
