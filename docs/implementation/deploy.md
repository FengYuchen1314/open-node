# Deploy 实现

`deploy/` 保存容器编排、受管公网网关、手工 Nginx 示例和网页更新桥。它们既可被根安装器
调用，也可供人工 Compose 部署参考；两种模式的身份与生命周期不能混用。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| [`.env.example`](../../deploy/.env.example) | 手工 Compose 的镜像、数据库、绑定、可信代理和可选能力变量样例 |
| [`compose.yaml`](../../deploy/compose.yaml) | 单应用容器、数据卷、维护目录 bind、回环端口和加固参数 |
| [`compose.postgresql.yaml`](../../deploy/compose.postgresql.yaml) | 可选 PostgreSQL 15 服务、健康检查和私有数据卷 |
| [`compose.restore.example.yaml`](../../deploy/compose.restore.example.yaml) | 使用新目录、新 project 和未占用回环端口启动隔离恢复实例 |
| [`Caddyfile`](../../deploy/Caddyfile) | 仅域名的受管 HTTPS 配置 |
| [`Caddyfile.ip`](../../deploy/Caddyfile.ip) | 仅公网 IP 的短期证书 HTTPS 配置 |
| [`Caddyfile.dual`](../../deploy/Caddyfile.dual) | 域名为 canonical、同时保留公网 IP 入口 |
| [`nginx.conf.example`](../../deploy/nginx.conf.example) | 外部 Nginx TLS 反代、防短链回退和无访问日志示例 |
| [`application_update_helper.py`](../../deploy/application_update_helper.py) | 固定官方 main 的 root 更新 helper |

自动清单提供这些文件的行数与符号：
[Deploy 清单](source-inventory.md#deploy--编排与网关)。

## 应用 Compose

`compose.yaml` 的 project 默认名为 `open-node`，只有一个 `open-node` service：

- 镜像由 `OPEN_NODE_IMAGE_REPOSITORY:OPEN_NODE_IMAGE_TAG` 确定，`pull_policy: never`，避免
  运行时从 registry 换掉本地已验证镜像；
- Dockerfile 内的 `USER 10001:10001` 运行应用；Compose 使用只读 root filesystem、
  `cap_drop: ALL`、`no-new-privileges` 和 `/tmp` tmpfs；
- `data` 命名卷挂到 `/var/lib/open-node`，保存 SQLite 或控制面文件状态；
- 宿主维护目录只 bind 到 `/run/open-node-maintenance`，让容器用户写固定 update request，
  不提供 Docker socket 或 root shell；
- 默认端口为 `127.0.0.1:62031:62031`。公开 HTTP 绑定需要安装器的双重 opt-in，Compose
  样例本身不配置 TLS；
- 容器不输出 Uvicorn access log，Compose local logging 仍限制文件大小和数量。

环境把 certificates、external subscriptions、federation、notifications 和 speedtests 状态
放到应用卷内。`OPEN_NODE_AGENT_IDENTITY_FILE`、subscriber TOTP key、IPinfo Token 与 Agent
bootstrap public URL 为空时，对应能力关闭或受限，不会自动生成外部身份。

`FORWARDED_ALLOW_IPS` 取自 `OPEN_NODE_TRUSTED_PROXIES`。手工代理必须填写容器实际看到的
精确代理地址；通配符只由安装器在应用绑定回环且网关同主机时使用。不能仅依赖浏览器
`X-Forwarded-*` 头判断安全来源。

`OPEN_NODE_TRUSTED_AUTHORITIES` 是独立的 Host 白名单。受管网关模式由安装器写入回环
健康检查 authority、域名和/或带端口的公网 IP，并在网关协调时重新核对。手工
Compose 的空列表会关闭该门禁；投产前应把直达健康检查与对外代理实际保留的
`Host`/`:authority` 字面值全部列出。

## PostgreSQL overlay

`compose.postgresql.yaml` 与 base compose 叠加。PostgreSQL 使用固定的官方
`postgres:15.18-bookworm` digest，数据库、角色都为 `open_node`，密码由私有环境注入。
服务只有 Compose 内网地址和 healthcheck，不发布 `5432`。

`postgres-data` 是独立命名卷。应用卷仍保存证书、联邦密钥、通知、备份/恢复 marker 等
非数据库状态，因此备份和彻底清除必须同时处理两个卷。安装器在 PostgreSQL ready 后验证
并收紧应用角色权限；单独运行 overlay 不会替人工完成这一安全流程。

数据库后端在 fresh install 时写入环境与 manifest，后续不能从 SQLite 原地切换。当前支持
同 revision/同 schema 契约备份恢复，不承诺旧 MMWX、跨后端或跨 schema dump。

## 隔离恢复 Compose

`compose.restore.example.yaml` 固定 project `open-node-restored`，要求操作者显式提供：

- `OPEN_NODE_RESTORE_IMAGE`：包含对应恢复代码的本地镜像；
- `OPEN_NODE_RESTORE_DATA_DIR`：全新的恢复目录，不能指向生产数据；
- `OPEN_NODE_RESTORE_HTTP_PORT`：未占用的宿主回环端口。

示例只 bind 新目录，并把容器 target 固定为 `62031`。它不会启动 PostgreSQL overlay、Caddy
或生产 project，也不会自动替换原实例。PostgreSQL stopped-backup bundle 的灾备恢复需要
先在独立 Compose project/空卷启动 PG、执行 custom dump `pg_restore`、恢复 state volume，
再启动应用并验证健康，具体命令见[备份文档](../backups.md)。

## Caddy 网关

三个 Caddy 模板共同关闭 admin API 和 Caddy 自动生成的 HTTP 站点，只启用 ACME issuer、
压缩、HSTS 和反向代理健康检查。IP 与 dual 模板在公网 IP 端口上显式按 `http_redirect`
再 `tls` 的顺序包装监听器，因此误用 `http://IP:58090` 时会以 `308` 跳转到同路径、同端口
的 HTTPS 地址；domain 模板的 `443` 监听器不受影响：

- 域名入口由 `OPEN_NODE_PUBLIC_HOSTNAME` 提供，使用 Let's Encrypt 标准证书；
- IP 入口由 `OPEN_NODE_PUBLIC_IP_AUTHORITY` 与 `OPEN_NODE_PUBLIC_HTTPS_PORT` 提供，申请
  `shortlived` profile；
- HTTP challenge 被禁用，受管网关使用 TLS-ALPN-01，所以三种模式的公网 TCP
  `443` 都必须可达；domain/dual 模式的域名业务 HTTPS 也直接使用该端口；
- upstream 固定为 host network 的 `127.0.0.1:$OPEN_NODE_UPSTREAM_PORT`；fresh 默认即
  `62031`；
- Caddy 重写 `X-Real-IP`，应用只信任安装器声明的代理边界。

安装器用 host-network Caddy 容器承载公网入口：domain 为 `443`，ip 为配置端口
（默认 `58090`），dual 同时承载两者。它还验证容器 identity、Caddy 数据卷、证书信任、
canonical URL 和 `/healthz`。模板文件本身不创建防火墙规则、DNS 或安全组。

## 外部 Nginx 示例

`nginx.conf.example` 面向已经有可信域名和证书的主机。它：

- 关闭 access log，避免订阅 bearer 出现在路径日志；
- 把 HTTP 80 重定向到 HTTPS，限制 TLS 版本并设置 HSTS；
- 丢弃客户端伪造的 forwarded chain，以 `$remote_addr` 重建边界；
- 明确拒绝 `/x` 和不符合 43 字符 bearer 的 public subscribe 路径，防止回滚到旧镜像后
  短链重新暴露；
- 把 WebSocket 和普通 HTTP 都转发到 `127.0.0.1:62031`。

它是示例，不会被 Compose 或安装器自动安装。实际 hostname、证书路径、trusted proxy 和
client body limit 需由操作者审阅。

## 网页更新桥

控制面容器没有 Docker socket。网页更新经过一个窄的文件协议：

```text
ApplicationUpdateStore
  → 在 /run/open-node-maintenance 写 mode 0600 request.json
  → host systemd .path 唤醒 application_update_helper.py
  → helper 重新校验 config、installer manifest、目录 owner/mode 和请求 schema
  → check: git ls-remote official/main
  → apply: 带 expected revision 调用 install.sh update
  → root 写 mode 0640 state.json
  → 面板只读并显示状态
```

安装器只在官方 repository + `main`、systemd host 上配置这组 per-project unit。共享目录为
root:`10001`、mode `1770`；request 必须由 runtime UID/GID 写入且 mode `0600`。helper 只接受
`check`/`apply`，apply 必须携带刚检查的 40 字符 revision，目标变化时拒绝。

helper 的 root 配置与 installer manifest 必须逐字段一致，installed `install.sh` 还需通过
root-owned、不可被组/其他用户修改的文件检查。失败时只返回固定中文状态；如果 installer
留下 recovery marker，状态变为 `recovery_required`，不会继续自动尝试。

## 手工部署与安装器部署

手工 Compose 可以复制 `.env.example` 并自行管理镜像、卷、反代和升级。根安装器部署则把
repository/ref、目录、project、镜像 tag/ID、卷 fingerprint 和端口写入 manifest。不要：

- 手工编辑 installer-managed `.env` 或 manifest；
- 用手工 Compose 以相同 project/volume 启动另一份容器；
- 把网页更新桥接到非官方 repository/ref；
- 把 restore example 指向生产卷或生产 state 目录；
- 认为 `docker compose config` 通过就等于 HTTPS、数据库权限和数据恢复已经验收。

## 验证

CI 至少渲染 base Compose、校验所有 Caddy 模板，并运行 installer/gateway smoke。可在本地先
做静态展开：

```bash
docker compose --env-file deploy/.env.example \
  --file deploy/compose.yaml config --quiet
```

PostgreSQL 模式需同时加入 `--file deploy/compose.postgresql.yaml` 并使用非生产测试密码。
网关、更新 helper 和恢复路径仍需对应测试/VPS smoke，不能只看 YAML。
