# Install 实现

控制面生命周期由三个根文件共同完成：

| 文件 | 作用 |
| --- | --- |
| [`install.sh`](../../install.sh) | fresh install、reinstall、update、status、setup、create-admin、保留卸载和内部 purge |
| [`uninstall.sh`](../../uninstall.sh) | 强制交互的保留卸载/默认彻底清除入口 |
| [`Dockerfile`](../../Dockerfile) | 构建 React、Python wheel、固定 lego/age 工具和非 root 运行镜像 |

`install.sh` 是有持久身份的事务执行器，不是通用 Compose wrapper。后续动作以首次安装写入
的 manifest 为准，拒绝接管手工项目、移动后的 checkout、未知卷或另一 Docker daemon。

## 动作入口

`main` 的动作表：

| 动作 | 前置条件 | 结果 |
| --- | --- | --- |
| `install` | 无 manifest 时必须是空的新项目；有 manifest 时按原身份 reinstall | 健康运行并完成公网/初始化配置 |
| `update` | 已安装、无 recovery marker、目标 revision 与可达 Git ref 一致 | stopped backup、候选镜像、健康提交或明确恢复状态 |
| `status` | manifest/env/checkout/卷身份有效 | 输出 Compose、健康、公网网关和恢复标记 |
| `setup` | 服务健康且尚未初始化 | 签发一次性浏览器初始化凭证 |
| `create-admin` | 服务健康、交互式终端或私有密码文件 | 调用容器内管理员 CLI |
| `uninstall` | 完整安装身份 | 删除运行资源，保留数据/配置/源码/备份 |
| `purge` | 只能由交互式 `uninstall.sh` 设置固定确认变量 | 删除项目数据卷与受管目录 |

任一成功动作最后输出 `ACTION_COMPLETE action=<action>`。安装日志中的局部“healthy”或某个
容器 running 不是完成标记。

## 安装身份

默认身份：

```text
repository       https://github.com/FengYuchen1314/open-node.git
ref              main
install dir      /opt/open-node
config dir       /etc/open-node
backup dir       /var/backups/open-node
project          open-node
app volume       open-node_data
postgres volume  open-node_postgres-data
caddy volume     open-node_caddy_data
```

`installer.manifest` 记录 manifest version、repository/ref、规范目录、project、image
repository、database backend、部署 revision、唯一 image tag、不可变 image ID、Docker daemon
identity、卷 fingerprint 和容器 target port。`open-node.env` 保存运行环境及 secret。两者均
要求 root-owned、非 symlink、不可由组/其他用户修改；环境和配置目录为私有。

受管公网安装还把 `OPEN_NODE_TRUSTED_AUTHORITIES` 写入私有环境：包含容器/宿主回环健康
检查 authority、可选域名和带公网端口的 IP。候选 Compose、正式容器和 Caddy 协调
都重新比较该列表，避免网关身份变化后继续接受旧 Host。

后续调用先执行 `load_manifest_defaults`。显式环境变量与 manifest 冲突时拒绝，不静默改用
新 project/目录。`OPEN_NODE_CONFIG_DIR` 是查找 manifest 的唯一外部 key：首次使用自定义
配置目录后，每次动作都必须再次传入。

路径检查要求绝对、规范、无空白/遍历/符号链接，祖先 root-owned 且不可组写；install、
config、backup 和 update-state 目录不得重叠。删除前会重新执行这些检查，不依赖安装时的
旧结论。

## 锁与中断状态

安装器同时取得 per-config lock 和 Docker daemon 级全局锁。前者防止同一安装并发动作，
后者防止两个 project 同时执行会影响共享 daemon 的候选/备份流程。

事务内存状态由 `TXN_KIND`、`TXN_PHASE`、候选 env/tag、backup path、helper container 和
候选是否已启动组成。持久的 `installer.recovery` 保存已进入风险区的 phase、旧/新 revision、
image 和 backup 身份。只要 marker 存在，install/update/setup 等动作就停止，`status` 只
显示证据，不自动猜测恢复方案。

退出 trap 按当前 phase 清理未启动候选、临时 env、worktree、验证卷和 helper container。
候选已经接触数据后，失败流程先尝试 quarantine；无法证明容器/网络被隔离时保留 marker，
不清理证据，也不重启旧镜像。

## Fresh install

`install_fresh` 的顺序：

1. `require_fresh_project` 确认 source/env/manifest、Compose project、卷、容器和网关没有未知
   现存资源。
2. 克隆 ref 到临时候选目录，解析完整 Git commit，并生成事务唯一
   `source-<revision>-<transaction>` image tag。
3. 创建候选 mode-0600 env，固定 image tag/revision、数据库后端、端口、public identity 和
   secret。
4. 静态展开 Compose，验证镜像引用、卷、绑定、target `62031`、安全选项和可选 PostgreSQL
   overlay。
5. 构建镜像并记录 image ID；fresh 公网模式先检查回环上游和 `443`；IP/dual
   模式还检查配置的 `OPEN_NODE_PUBLIC_HTTPS_PORT`（默认 `58090`）。
6. 启动数据库，再以 `--no-build --pull never` 启动应用。`wait_for_health` 同时核对容器、
   image ID、端口、HTTP health 和数据库角色。
7. 候选健康后原子提交 source 和 env，再从 canonical 路径强制 recreate，避免容器继续引用
   临时 checkout。
8. 写 manifest、再次验证 checkout/active identity，等待稳定健康观察，清除 recovery marker。
9. 先协调 Caddy 到已提交的公网状态，再安装网页 update helper，完成浏览器初始化并输出
   URL/完成标记。

候选构建或首次启动失败时，不写正式 manifest。源码提交之后的失败会留下恢复证据，不能
把目录删除后当作一台新主机重跑。

## Reinstall

同一 manifest 再次执行 `install` 进入 `reinstall_existing`。运行中且健康时只重建更新桥、
网关和初始化状态；容器 stopped/absent 时用 manifest 的原 image ID 和 tag 启动，不重新
解析远端 ref，也不重建镜像。失败候选被 quarantine，数据卷保留。

## Update 事务

`update_existing` 先验证当前 checkout、镜像、容器、卷和 daemon，再用 Git worktree 准备
目标 commit。若 revision 未变，不重建镜像。真正更新：

```text
构建并验证候选镜像
  → 停旧应用（PostgreSQL 可继续用于一致 dump）
  → 创建并验证 stopped-backup bundle
  → 若候选关闭公网，先验证并移除旧受管 Caddy
  → 启动候选并等待健康
  → fast-forward 正式 checkout
  → 提交候选 env
  → 从正式路径 recreate
  → 提交 manifest
  → post-commit 稳定健康
  → 清 marker、移除 worktree、协调网关、安装更新 helper
```

候选启动前一定先有完整备份。候选失败后安装器不会把旧镜像直接启动到可能已迁移的数据
上；它停止候选、保留旧 source/env/manifest/image 记录，并要求在隔离 project 验证恢复。
从受管公网切换到 `off` 时，旧 Caddy 在候选启动前即被移除。这样候选的私网空 authority
策略不会在后续 manifest、稳定性或 helper 步骤失败时意外继续接受旧公网入口。

网页更新桥额外传 `OPEN_NODE_EXPECTED_REVISION`。候选解析出的 commit 必须等于面板刚确认的
目标，防止检查与执行之间 main 前移。

## stopped-backup bundle

`backup_stopped_volume` 在旧应用停止后创建目录 bundle。共同文件：

```text
volume.tar.gz
open-node.env
installer.manifest
compose.yaml
deployment.meta
SHA256SUMS
```

PostgreSQL 模式再包含 `compose.postgresql.yaml` 与 custom-format `postgres.dump`。SQLite 数据库
位于 `volume.tar.gz`；PostgreSQL 的 tar 保存控制面文件状态，数据库本身单独 dump。

发布 bundle 前，安装器会：

- 用旧 image 只读扫描 state volume，拒绝 symlink/特殊文件并计算目录内容摘要；
- SQLite 执行 integrity check 并记录数据库 SHA；
- PostgreSQL 以 `PGPASSWORD` 环境调用 `pg_dump`，不会把密码放入命令参数；随后在临时
  verification database 执行 `pg_restore --single-transaction` 并检查 public table；
- 把 tar 解到新验证卷，重算 state 摘要；SQLite 还重算数据库摘要；
- 为旧 image 创建唯一 rollback tag，写 deployment metadata 和全部 artifact 的
  `SHA256SUMS`；
- fsync 文件与目录后，才把临时目录改名为最终 bundle。

这个 bundle 用于同 revision 灾备和更新回滚，不是长期跨 schema 迁移格式。恢复时先验证
`sha256sum -c SHA256SUMS`，使用独立 Compose project、空卷与未占用回环端口；PostgreSQL 要
先启动空 PG、restore custom dump，再恢复 state volume、启动应用并验证健康。完整可执行
步骤在[备份文档](../backups.md)。

## 数据库分支

fresh 默认 SQLite。`OPEN_NODE_DATABASE_BACKEND=postgresql` 只在首次安装接受；安装器生成
32–128 字符约束内的随机字母数字密码，写入 mode-0600 env。可传自有密码，但只允许符合
相同字符/长度策略。

`ensure_database_ready` 在 PostgreSQL service healthy 后验证容器 image/volume/network 和
数据库角色。应用角色必须非 superuser、无 CREATEROLE/replication/BYPASSRLS/异常成员，
只保留 staging database workflow 所需 CREATEDB。数据库不发布宿主端口。

后端选择写入 manifest，update/reinstall 不允许切换。安装器不实现 SQLite→PostgreSQL、
旧 MMWX→PostgreSQL 或任意 cross-schema dump 恢复。

## 受管公网网关

fresh 默认通过两个 HTTPS 服务一致探测公网 IPv4，使用固定 Caddy image digest。网关容器以
host network 运行：domain 模式在 `443` 提供域名 HTTPS，ip 模式在
`OPEN_NODE_PUBLIC_HTTPS_PORT`（默认 `58090`）提供 IP HTTPS，dual 同时开启两者。
三种模式都使用公网 TCP `443` 完成 TLS-ALPN-01；对 domain/dual，该端口也承载域名业务
HTTPS。所有入口都反代回环 `62031`。

`reconcile_public_gateway` 按 off/domain/ip/dual 选择固定 Caddy 模板，验证 Caddy 数据卷、
container labels/config hash、public IP/hostname/端口和上游。`wait_for_public_gateway` 要求
证书受系统信任、访问 canonical URL 成功且 `/healthz` 连续通过；失败不会降级到 HTTP、
自签名或仅私网成功。

移除网关时先重新验证 Docker daemon identity，再用锚定的精确容器名枚举确认存在；删除后
重复同一检查证明已消失。Docker API/daemon 检查失败不能解释为“容器不存在”。

安装器不修改 DNS、安全组、UFW 或其他防火墙。端口检查通过只说明本机未占用，不证明公网
路径已放行。

## 管理员初始化

fresh 默认调用容器内 `open-node-admin setup --json --if-unconfigured`，打印 30 分钟、一次性
浏览器凭证。`setup` 只能在尚无管理员时重新签发。`create-admin` 支持 `/dev/tty` 交互或
root-owned mode-0600 密码文件，密码不会作为命令行参数传入容器。

已有管理员时初始化命令拒绝覆盖；修改/恢复管理员身份使用 Backend 自己的安全协议，不由
安装器直接改数据库。

## 镜像构建

Dockerfile stages：

1. `frontend`：固定 Node 22 base，`npm ci` 后构建 Vite；
2. `backend`：固定 Python 3.11 base，在 venv 安装 Backend wheel；
3. `lego`、`age`：运行容器构建辅助脚本下载固定 release 并校验 checksum/许可；
4. `runtime`：创建 UID/GID 10001，安装 PostgreSQL client 15，复制 venv、前端、lego、age、
   entrypoint 和许可证。

最终镜像使用只读友好的非 root 用户，暴露 `62031`，healthcheck 只访问容器内 loopback。
`entrypoint.sh` 仅在浏览器恢复有待激活环境时加载经 owner/mode 校验的 `restore.env`，随后
`exec` Uvicorn。

## 卸载与彻底清除

`install.sh uninstall` 删除应用、可选 PostgreSQL、Caddy 容器、project network 和网页更新
unit，保留应用/PostgreSQL/Caddy 卷、source、env、manifest、backup、update state 和镜像。

独立 [`uninstall.sh`](../../uninstall.sh) 要求 root 及 stdin/stdout TTY，先验证实际安装器
root-owned 且不可组写，并列出 source/config/backup/卷范围。提示 `[Y/n]`：

- `n` 执行上述 data-preserving uninstall；
- 直接回车或 `y` 设置固定 `OPEN_NODE_PURGE_CONFIRMED=YES`，进入内部 `purge`；
- 其他输入不执行任何操作。

`purge_installation` 再次验证 manifest/env hardlink、目录、容器、网络和每个卷 fingerprint，
停完整 runtime 后删除项目应用卷、可选 PG 卷、实际存在的 Caddy 卷，以及精确的 update
state、backup、config 和 source 目录。它不删除 Docker 镜像/构建缓存、Docker/Git 主机
依赖、远端 Agent、外部 DNS/证书或已经下发的代理凭据。

## 关键函数地图

| 函数组 | 代表函数 | 作用 |
| --- | --- | --- |
| 输入与路径 | `validate_inputs`、`validate_absolute_path`、`validate_path_separation` | 规范化并收紧所有外部变量 |
| 身份 | `require_manifest`、`verify_checkout`、`verify_active_identity`、`volume_is_safe` | 证明 source/image/Compose/volume 属于本安装 |
| 候选 | `prepare_fresh_candidate`、`prepare_update_candidate`、`validate_candidate_compose` | 固定 commit 和 Compose contract |
| 运行 | `ensure_database_ready`、`wait_for_health`、`reconcile_public_gateway` | 启停并验证数据库、应用和 HTTPS |
| 恢复 | `write_recovery_marker`、`quarantine_candidate`、`cleanup_transaction_on_exit` | 保存风险状态并限制失败候选 |
| 数据 | `backup_stopped_volume`、`backup_verify_volume_is_safe` | 生成并恢复验证更新前 bundle |
| 生命周期 | `install_fresh`、`reinstall_existing`、`update_existing`、`purge_installation` | 完成顶层事务 |

完整 shell 函数表见[自动源码清单](source-inventory.md#install--镜像与生命周期)。

## 验证边界

安装器主要在 Linux、root、Docker Compose v2 环境运行。Bash 语法检查、Compose config 和
mock 测试只能覆盖静态契约；fresh/update/uninstall/public gateway/PostgreSQL 必须结合
`scripts/vps/smoke-control-plane*.py`、`smoke-installer-*.py` 和真实 Debian VPS 验收。

当前不支持旧 MMWX 整机迁移、手工 Compose 收养、数据库后端切换、rootless Docker、
Windows、任意多主机/多 worker 协调或自动修改公网基础设施。
