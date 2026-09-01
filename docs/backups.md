# 备份与恢复

管理员可在“备份与恢复”页面创建本控制面的加密备份、查看进度、下载或删除临时文件。
支持在管理员页面或尚未创建管理员的新实例上传 v1 备份，也可离线恢复到新目录，
再登录网页复核并重启。它不是远端 Agent 的整机备份。“生成成功”仍不代表某份备份
已经做过恢复演练。

## 创建和下载

1. 在自己保管的设备上安装[官方 age](https://github.com/FiloSottile/age)，生成密钥：

   ```sh
   age-keygen -o backup-identity.txt
   age-keygen -y backup-identity.txt
   ```

2. 将第二条命令输出的 `age1…` 公钥填入页面。不要上传或粘贴私钥。
3. 输入当前管理员密码；已开启两步验证时，还需未使用的验证码或恢复码。
4. 点击创建。完成后下载 `.zip.age` 文件，并核对页面显示的大小和 SHA-256。

私钥遗失后无法解密。备份含数据库、凭据和密钥，即使已经加密，也应妥善保管。
数据库与已配置的证书、外部订阅、联邦共享、通知密钥及 Agent 身份在同一停写范围内复制；
随后恢复业务写入，再检查依赖、打包和加密。
SQLite 使用 Online Backup API；PostgreSQL 使用镜像内官方 `pg_dump` custom 格式、压缩级别 6、
`--no-owner` 和 `--no-privileges`，并以 `pg_restore --list` 固定模式指纹。数据库
导出/恢复最长 30 分钟，包内数据库与全部状态文件仍受 v1 合计 1 GiB 上限约束。

创建回执丢失时，页面只查询原请求编号，不会自动再创建一次。下载不会重新执行备份。
浏览器直接流式下载，不会先把整包读入网页内存。不支持断点续传。

## 生命周期和部署限制

- 同时最多生成一份备份，最多保留两份完整文件，最多一个下载。
- 从提交起最多保留 15 分钟；会话更早到期时，以会话到期时间为准。
- 删除、退出登录、会话失效、修改密码或两步验证等安全设置后，旧授权不能继续下载。
  已经发送给浏览器的字节无法收回。
- 重启服务后，内存任务列表与临时文件不保留。
- 网页任务使用默认的单 Web 进程部署。额外进程不会接管另一进程的备份任务。
- 默认容器 `/tmp` 只有 64 MiB，小实例可以直接使用；较大实例需预留足够临时空间。
  可通过 `OPEN_NODE_BACKUP_TEMPORARY_DIRECTORY` 指向明确配置、可写的绝对目录。
  该目录不能与备份源状态目录重叠。空间不足会失败，不会发布部分文件。

配置和密钥需与备份一起妥善保存，包括数据库/状态目录映射、TOTP 加密密钥、HTTPS、
代理和公网地址等。它们不会因为数据库已经备份就自动完整保留。

PostgreSQL 当前只支持安装器选择的全新部署，并按**相同 Open Node 源码 revision、相同
数据库后端和兼容配置**恢复该 revision 生成的备份。请与备份一起保留精确镜像 ID、源码
revision 和私有环境。普通版本升级可以执行该版本自带的 schema 变更，但这不构成任意
跨 schema 版本恢复承诺；v1 格式校验也不会替操作者证明 schema 兼容。原版 MMWX、旧
MMWX 数据库、SQLite/PostgreSQL 互转及旧主机接管均不支持。

## 浏览器上传恢复

默认 Docker Compose 的 SQLite 和 PostgreSQL 数据布局都支持此入口；来源包必须与目标
部署使用相同数据库后端，并遵守上面的同 revision 恢复合同。管理员打开“备份与恢复”，选择本项目
生成的 `.zip.age` 或明文 v1 ZIP，填写当前管理员密码和已启用的 MFA，再明确确认替换实例
及备份来源可信。age 包还需粘贴对应身份文件内容；它只随本次同源请求进入临时解密流程，
不会写入恢复树。备份若含订阅 TOTP 数据，还须填写原来的 44 字符
`OPEN_NODE_SUBSCRIBER_TOTP_KEY`，不是六位验证码。

尚未创建管理员的新部署，可先用安装器 `setup`（或容器内
`open-node-admin prepare-setup`）取得 30 分钟初始化凭证，在初始化页选择“从备份恢复现有
实例”。该凭证只证明本次新实例的本地控制权，不会进入恢复后的数据库。已经初始化的实例
不能调用匿名恢复入口。

上传使用原始二进制流并写入数据卷内的私有暂存目录，不占用 64 MiB `/tmp`。单个请求仍受
v1/加密包约 1 GiB 上限约束；最多保留两个上传，30 分钟后清理。服务器在当前数据库之外
完成解密、归档/摘要、数据库结构、管理员和密钥依赖检查，并清除旧会话、撤销安装
票据及隔离自动任务。任何准备失败都不会覆盖当前实例。

准备成功后会写入可恢复的激活日志。安装器管理的 Compose 容器在响应返回后自动重启，
入口脚本在应用打开数据库前执行激活。SQLite 会把旧顶层状态移动到
`.open-node-restore-rollback-<上传请求 UUID>/`，再安装已经验证的恢复树；PostgreSQL 则先把
当前库改名为带请求标识的 rollback 库，再把已完整验证的 staging 库改为当前库名，然后切换
文件状态。每一步都写入可重入日志，中途退出会继续，而不是混用两套数据库。自定义部署默认
不自动终止进程，须由操作者重启。不要在服务
运行时删除等待激活目录、激活日志或回滚目录；确认恢复实例稳定并另有外部备份后，才可在
停服状态人工归档/清理旧回滚树。

重启后仍进入下文“首次启动复核”隔离。浏览器入口只支持默认同一私有数据根；自定义分散
目录会显示不支持。PostgreSQL 账户必须具备创建、改名和删除数据库的权限；安装器管理的
`open_node` 角色满足这一条件。切换前的数据库会保留为 rollback 库，确认恢复稳定且另有
备份后再由管理员停服清理。

## 本地只读检查

在具备固定官方 age 的本项目环境中：

```sh
open-node-backup validate backup.zip.age --identity backup-identity.txt --json
```

该命令验证完整解密认证和包结构，不执行恢复，也不能证明备份发送者身份。
新网页包不是安装器的停服整卷 tar 包，不能直接代入后者的解包命令。
现有停服整卷备份的用法仍见[部署说明](deployment.md)。

## 离线恢复到新目录

恢复会关闭外部订阅的定时刷新并清除在途租约。完成复核、重启后，来源和已保存节点仍在，
但不会自动抓取上游；需在来源详情重新确认开启。

参考官方[管理员恢复及首次初始化恢复处理器](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/backup.go#L342)。
本项目采用新目录/暂存数据库导入和首次启动复核，不直接覆盖在线数据库。此入口只接受
本项目 v1 包，不接受原版 mmwx ZIP、安装器 tar 或裸 PostgreSQL dump；数据库之间的迁移
明确不支持，也不承诺跨 schema 版本恢复。命令行 `restore` 目前用于 SQLite 新目录；
PostgreSQL v1 包通过上述浏览器入口恢复，
因为它需要当前部署的受控数据库地址和重启切换日志。
包需具备完整的已知覆盖范围和兼容的应用表结构；仅通过通用 v1 格式校验的手工包不一定可恢复。

先停止原实例，并保留原数据目录/卷、镜像和配置用于回退。备份必须来自可信来源：age
认证能发现密文损坏，但不能证明发送者身份，也不能保证数据符合您的业务预期。

在**包含本轮恢复代码**且配有固定官方 age 的 Linux 环境运行：

```sh
open-node-backup restore backup.zip.age \
  --identity backup-identity.txt \
  --totp-key-file original-totp.key \
  --output /srv/private-restore/new-data \
  --confirm-stopped --confirm-trusted-source --json
```

`/srv/private-restore` 须事先存在、属于运行命令的用户且权限为 `0700`；`new-data` 必须不存在。
age 私钥和 TOTP 密钥文件需属于同一用户，权限为 `0400` 或 `0600`，不可为符号链接或硬链接。
TOTP 文件只存原 `OPEN_NODE_SUBSCRIBER_TOTP_KEY` 的值，不是六位验证码、恢复码或新生成的密钥。
备份中没有 TOTP 数据时可以省略该参数；有已启用或待绑定的 TOTP 时，缺失/错误密钥会拒绝导入。
如已有未加密的 v1 ZIP，可省略 `--identity`，但须特别注意明文文件保管。

SQLite 命令行导入会检查归档结构、文件摘要、SQLite 完整性/外键及已知加密依赖。成功后新目录包含：

- 数据库、证书/外部订阅/联邦共享/通知密钥，及已配置的 Agent 控制面身份。
- `restore.env`：默认容器路径和提供的 TOTP 密钥，权限 `0600`，不要提交到 Git 或贴到日志。
- `.open-node-restore.json`：首次启动复核记录，不要删除或手工修改来跳过复核。
- `.restore-quarantine/`：旧证书任务和 HTTP-01 webroot 文件，只留作人工核对，不会自动执行清理。

不会覆盖已有目录。发布失败时只清理本次随机暂存目录；若发布后的磁盘同步或终端输出失败，
可能已经存在完整新目录，请先检查恢复记录，不能把非零退出码当作“完全没导入”。
上限仍受 v1 格式（合计 1 GiB）和检查时间限制；临时空间与新数据一起应预留至少三倍包大小，
另留数据库日志余量。CLI 使用 `/tmp`，不会自动扩大原容器的 64 MiB tmpfs。

## Docker 使用方式

使用您已经从对应源码构建的镜像标记，不要使用尚不含恢复代码的旧生产镜像。
镜像运行用户是 `10001:10001`。可以事先准备三个属于该用户的 `0700` 目录：
`/srv/open-node-restore/input`、`output`、`tmp`。把备份和所需密钥放入 input，密钥设为 `0600`；
其中 tmp 使用磁盘空间，不使用小容量内存盘。确认原实例停止后执行：

```sh
export OPEN_NODE_RESTORE_IMAGE=open-node:local # 替换为实际构建的标记
docker run --rm --network none --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount type=bind,src=/srv/open-node-restore/input,dst=/input,readonly \
  --mount type=bind,src=/srv/open-node-restore/output,dst=/output \
  --mount type=bind,src=/srv/open-node-restore/tmp,dst=/tmp \
  --entrypoint open-node-backup "$OPEN_NODE_RESTORE_IMAGE" \
  restore /input/backup.zip.age --identity /input/backup-identity.txt \
  --totp-key-file /input/original-totp.key --output /output/new-data \
  --confirm-stopped --confirm-trusted-source
```

从项目根目录使用单独的[恢复实例 Compose 模板](../deploy/compose.restore.example.yaml)：

```sh
export OPEN_NODE_RESTORE_DATA_DIR=/srv/open-node-restore/output/new-data
export OPEN_NODE_RESTORE_HTTP_PORT=62032 # 必须显式选择一个未占用的回环端口
docker compose -f deploy/compose.restore.example.yaml up -d
```

模板不会继承生产端口，也没有恢复端口默认值；缺少 `OPEN_NODE_RESTORE_HTTP_PORT` 时会
直接拒绝渲染。它不会构建/拉取镜像，也不会自动创建源目录，仅监听回环地址。仍须接好原来的 HTTPS
反向代理、可信代理、公开地址等部署配置；如需临时 HTTP 登录，请按部署文档配置 Cookie，
不要把明文管理接口直接暴露到公网。单 Web 进程是支持的运行方式。

## 首次启动复核与回退

打开 `/backups`（隔离时首页也跳转到此处），用备份中的管理员账户登录。
旧管理员/用户会话和登录挑战已清除，未完成的 TOTP 绑定已取消，原 Agent 安装票据已撤销。
未完成的 Agent 命令与证书任务已终止，执行中的变更集转为待复核；原有执行历史保留。
通知发送中的结果记为未知，未发队列取消。证书自动续签/部署和通知开关均关闭，需另行开启。

此时只放行管理员认证、备份状态/复核和所需页面资源；Agent、用户订阅和所有自动任务都暂停。
核对原实例已停止、部署配置/密钥、用户套餐与节点权限、远端状态和来源后，重新输入当前管理员密码
及启用的两步验证码/恢复码保存复核。页面回执丢失可刷新状态，不会自动重复提交。

保存复核**不会**启用当前进程，须显式重启：

```sh
docker compose -f deploy/compose.restore.example.yaml restart open-node
```

重启后按恢复的数据重新协调订阅权限、配额和节点状态，可能生成新的 Agent 命令，包括继续处理
未完成的用户/节点移除流程；这不是对旧远端状态的证明。需要核对远端 Agent 的控制面身份、地址、
已执行但未回报的命令以及残留 DNS/HTTP-01 挑战资源。关闭的证书/通知自动功能仍需管理员分别开启。

若发现问题，先停止新实例，再用原镜像和原目录/卷启动原实例，不能两套同时控制同一批 Agent。
启用新实例后发生的数据库写入和远端操作不会随着切回原数据库自动回滚。原目录与隔离文件不会被此流程删除。

官方依据、快照范围和未覆盖的恢复语义见[备份设计](backup-plan.md)及
[一致快照说明](backup-runtime.md)。本功能免费，不需要许可证。
