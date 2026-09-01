# 总体架构

## 仓库边界

Open Node 使用单仓库维护控制面、浏览器前端、主机 Agent、部署资产和验收工具。功能修改
可以在一次变更中同时调整 HTTP 契约、前端调用、Agent 命令和部署门槛。

```text
backend/       FastAPI 控制面、数据库模型、业务服务和测试
frontend/      React 管理端、用户中心、公开 Probe 页面和测试
agent/         独立 Linux Agent、主机部署工具和测试
deploy/        Compose、Caddy/Nginx 模板与 root 更新桥
install.sh     控制面安装、更新、状态、卸载和恢复事务
scripts/       容器构建辅助、CI 分片、迁移工具和 VPS 验收
probe-worker/  Cloudflare Worker 公开探针入口
runtime/       Open Node 维护的 Xray overlay 与补丁
data/          固定上游参考源码、构建输入或本地数据；不直接维护
```

`data/sources/` 和 `data/mieru-base/` 体量很大，但它们不属于日常修改面。上游版本和固定
提交见 [MMWX 源码对照](../mmwx-source-parity.md)。需要修改 fork 行为时，应改
`runtime/xray/` 的 overlay 或补丁，再由发布脚本应用到固定上游，而不是直接把参考快照
当作项目源码编辑。

## 运行拓扑

默认单机部署只有一个控制面应用进程。PostgreSQL、Caddy 和 Probe Worker 均为可选组件，
Agent 运行在远端受管主机。

```text
管理员/订阅用户浏览器
          │ HTTPS
          ▼
  Caddy :443 / IP 端口 58090（可选，host network）
          │ HTTP 127.0.0.1:62031
          ▼
  FastAPI + React 静态文件 :62031
          │
          ├── SQLite 数据卷
          ├── PostgreSQL 15 私有 Compose 网络（可选）
          ├── 控制面状态目录、备份/恢复状态和后台任务
          └── HTTPS/WSS 或 HTTP lease
                         │
                         ▼
              非 root Open Node Agent
                         │
                         ├── Xray/Nginx/NextTrace 受限主机操作
                         └── root lifecycle helper（显式启用时）

Cloudflare Probe Worker ──只代理公开 Probe GET/WS──> FastAPI
```

受管公网入口把域名 `https://hostname:443` 或 `https://公网 IP:58090`（IP 模式默认端口）
代理到宿主回环 `127.0.0.1:62031`，应用容器内部也监听 `62031/tcp`。Caddy 在三种受管模式中
都需要 TCP `443` 完成 TLS-ALPN-01；PostgreSQL 不发布宿主端口。
手工 Nginx/Caddy 部署必须自行提供可信 HTTPS，并把实际代理地址加入精确的 trusted proxy
配置。

## 组件与所有权

| 组件 | 组合入口 | 持有的状态 | 不负责的范围 |
| --- | --- | --- | --- |
| FastAPI | `backend/app/open_node/main.py:create_app` | 业务数据库、控制面文件状态、后台任务 | 主机防火墙、DNS、远端 systemd 所有权 |
| React | `frontend/src/main.tsx`、`frontend/src/react/App.tsx` | 内存会话快照；少量非敏感显示偏好 | 密码、Token、私钥的长期保存 |
| Agent | `agent/app/open_node_agent/__main__.py:main` | 私有配置、命令日志簿、运行时事务 | 未声明的主机服务、任意路径、控制面账号 |
| Agent host deployer | `agent/app/open_node_agent/service.py:main` | 安装 manifest、release、systemd unit、专用账号 | 其他 Agent/Xray/Nginx 安装的接管 |
| Compose | `deploy/compose.yaml` | 应用数据卷和只读容器运行边界 | 公网 TLS、主机包升级 |
| 根安装器 | `install.sh:main` | 安装 manifest、私有环境、镜像/卷身份、恢复标记 | 旧 MMWX 迁移、跨数据库后端切换、任意项目收养 |
| Probe Worker | `probe-worker/src/index.ts` | Cloudflare 变量和静态资产 | 管理 API、Cookie 透传、数据持久化 |

## 控制面请求链

典型管理请求沿以下路径执行：

```text
React view/component
  → frontend/src/services/*
  → TrustedAuthorityMiddleware（配置后先精确校验 Host/:authority）
  → /api/v1/* route
  → FastAPI 依赖（管理员会话、订阅用户、Agent Token 或公开凭据）
  → domain 模型做输入校验
  → services/* 完成数据库事务、文件事务或外部调用
  → 经过响应模型/显式清洗后返回浏览器
```

`api/router.py` 明确区分私有管理路由、管理员会话路由、订阅用户路由、Agent 路由和公开
路由。`main.py` 负责装配共享存储、错误响应、中间件和后台 worker。路由文件应保持薄层：
解析 HTTP、执行依赖和映射错误；持久化与跨资源事务放在 service/store 中。

## Agent 命令链

控制面不会直接登录远端主机。命令先写入数据库，再由已认证 Agent 通过 WebSocket RPC 或
HTTP lease 接收：

```text
管理 API
  → InventoryStore 创建 pending/waiting 命令
  → AgentConnectionManager 尝试 WS 分发，或 Agent HTTP lease
  → Agent.CommandJournal 记录 request_id + payload fingerprint
  → Operations.handle 串行分派
  → Xray/Nginx/诊断/生命周期实现
  → 结果先写 Agent journal，再回传控制面
  → 控制面条件更新终态并推进 depends_on 后继命令
```

这个链路是持久化、至少一次投递。普通中断命令不会被 Agent 盲目重做；相同 request ID 与
不同内容冲突，已完成结果可以重放。只有带专门恢复协议的 lifecycle、HTTP-01 和节点清理
任务允许按其状态机继续。

## 后台任务

FastAPI lifespan 在恢复门没有阻断时启动证书、订阅访问、服务器流量、通知、外部订阅
刷新、DDNS、联邦刷新和备份任务。每轮写入单独进入 backup write barrier，避免后台循环
长期占住快照许可。关机时先取消任务、关闭测速连接，再等待备份生产者收尾。

浏览器恢复处于待审阅或待激活状态时，`RestoreState.blocked` 会让应用不构造这些 worker；
只保留恢复所需的受限接口。容器入口先读取经校验的 `restore.env`，再启动 Uvicorn。

## 持久状态

| 状态 | 默认位置或后端 | 关键约束 |
| --- | --- | --- |
| 控制面业务数据 | SQLite `/var/lib/open-node/open-node.db`，或私有 PostgreSQL 15 | 后端在首次安装确定；不能原地切换 |
| 控制面文件状态 | `/var/lib/open-node/` 下的 certificates、federation、notifications、speedtests 等 | 容器用户 `10001:10001`，目录随应用卷备份 |
| 安装身份 | `/etc/open-node/open-node.env`、`installer.manifest` 和 recovery marker | root、私有、规范路径；后续动作必须匹配 |
| 安装器备份 | `/var/backups/open-node/` | update 前 stopped-volume bundle；不等于跨 schema 迁移格式 |
| 网页更新桥 | `/var/lib/open-node-maintenance-<project>` 与 per-project systemd unit | 容器只写固定请求，root helper 重新验证身份 |
| Agent 安装 | `/opt/open-node-agent[-suffix]/` | root 持有 manifest/runtime/releases；专用非 root 账号运行 |
| Agent 配置与状态 | 安装根下 `config/`、`state/` | 私有配置、SQLite 命令 journal、事务文件和日志 |
| 面板 Agent bootstrap | `/var/lib/open-node-agent-bootstrap/<job>` | root `0700`；保存的配置含长期 Agent credential |

SQLite 与 PostgreSQL 共享业务模型，但并非任意备份互换。PostgreSQL 目前支持全新部署，及
同一 Open Node revision/相同 schema 契约下的备份恢复；不承诺旧 MMWX、跨后端或跨 schema
版本恢复。完整规则见[备份与恢复](../backups.md)。

## 安全边界

### 浏览器与控制面

- `trusted_authorities` 配置后，最外层 ASGI 中间件在路由、CORS、恢复隔离和备份写屏障
  之前检查唯一的 `Host` 或 `:authority`；匹配仅忽略 ASCII 大小写，不合并缺省端口、
  尾随点或 IPv6 等价写法。HTTP 失败返回 `400` 且 `no-store`，WebSocket 以 `1008`
  关闭。空列表只为开发和测试兼容而停用该门禁。
- 管理员使用 opaque Cookie 会话；数据库只保存会话 secret 的哈希。非安全方法附带 CSRF
  Token，并校验 Origin/代理边界。
- 订阅链接、临时链接、Agent Token、bootstrap ticket 和 Probe Token 是不同命名空间，
  不能互相替代。
- 敏感响应统一使用 `Cache-Control: no-store`；公开短链默认关闭。
- 应用容器只读、drop all capabilities、`no-new-privileges`，仅数据卷和受控维护目录可写。

### Agent 与主机

- Agent 默认非 root，配置与 state 目录只对专用账号开放；同一 state 目录由文件锁保证单
  实例。
- 路径变更要求绝对路径、所有权、权限、链接类型和旧内容摘要符合预期。配置候选先调用
  运行时校验，再原子替换；失败时保留或恢复旧文件。
- 外部 systemd Xray 必须显式绑定，并由固定 polkit 规则只授权目标 unit。远程 lifecycle
  需要主机所有者单独启用 root helper。
- 卸载只处理 manifest 证明归属的 unit、账号、目录和 bootstrap job；控制面记录和 Token
  仍需在面板撤销。

### 安装器

- 根安装器以 manifest、环境文件、Git revision、镜像 ID、Compose project、容器、网络和
  卷 fingerprint 共同确定身份。
- fresh/update 先在候选 checkout 和唯一镜像标签中构建，健康后才提交源码、环境和
  manifest。中断用 recovery marker 阻断后续动作，避免旧镜像直接接触可能已迁移的数据。
- `uninstall` 保留数据；独立 `uninstall.sh` 的回车默认 purge 仍要再次执行全部身份检查。

## 当前部署边界

- 控制面面向全新 Debian/Ubuntu 单机 Docker 部署，重点验证 Debian 12 `amd64`。
- 面板一键 Agent 面向全新 Debian 12 `amd64` 主机；不会自动接管旧 MMWX Agent 或现有
  Xray/Nginx。
- 一个控制面 Compose 项目运行一个应用进程。文件锁、后台任务和浏览器恢复流程没有宣称
  支持任意多 worker 或多主机共享同一状态目录。
- 旧 MMWX 相关模块提供受限兼容接口或显式数据导入工具，不构成整机迁移承诺。
