# Open Node

[![CI](https://github.com/FengYuchen1314/open-node/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/FengYuchen1314/open-node/actions/workflows/ci.yml)
[![许可证：MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Open Node 是 MMWX 活跃技术线的开源重构项目，用于管理服务器、Agent、Xray 运行时、
订阅用户、套餐、流量、证书和公开探针。项目不需要激活码，不连接商业许可证服务器，
也没有付费功能开关。管理端、用户中心和 Probe 页面以简体中文为主。

实现以 `miaomiaowuX`、`mmw-agent`、`mmwx-probe` 和 `Xray-core-mmwx` 四个官方仓库
的固定版本为参考，差异与验收情况记录在
[MMWX 源码对齐表](docs/mmwx-source-parity.md)。

> [!IMPORTANT]
> 当前版本仍处于 Preview 阶段，面向全新部署，不代表已经完整替代所有历史 MMWX 环境。
> 旧 MMWX 整机迁移、既有主机自动接管、Bot/Mini App 和商业 Reality 资源池不在当前
> 交付范围。准备投入生产前，请先阅读[使用边界](#使用边界)和
> [部署文档](docs/deployment.md)。

## 快速部署

### 准备条件

- 一台全新的 Debian 或 Ubuntu Linux 主机；目前重点验证 Debian 12 `amd64`。
- `root` 权限或可用的 `sudo`。引导命令会在缺少 `curl` 时先安装它，安装器随后检查并
  安装 Git、Docker 和 Docker Compose v2。
- 服务器能够访问 GitHub、Docker Hub、npm、PyPI 和 Let's Encrypt ACME 服务。
- 默认公网模式需要一个可路由的公网 IPv4，并放行入站 TCP `443` 和 `58090`。
  TCP `80` 不需要开放。
- 默认 IP 公网模式下，TCP `443`、`58090` 和宿主回环端口 `62031` 不能被其他程序占用。
  仅启用域名时不使用 `58090`；关闭全部公网入口时只要求宿主回环上游端口可用。

安装器不会修改云安全组、UFW、防火墙或 DNS。主机已有 Nginx、Caddy 等服务占用
TCP `443` 时，请使用[手动反向代理方案](docs/deployment.md#https-and-proxy-trust)，
不要直接运行下面的默认命令，也不要同时启动受管公网网关。

### 一键安装

在新主机上完整复制并执行下面的命令：

```bash
(
  set -eu
  as_root() {
    if [ "$(id -u)" -eq 0 ]; then
      "$@"
    else
      command -v sudo >/dev/null 2>&1 || {
        echo "需要 root 权限或 sudo" >&2
        return 1
      }
      sudo "$@"
    fi
  }
  if ! command -v curl >/dev/null 2>&1; then
    as_root apt-get update
    as_root apt-get install -y --no-install-recommends ca-certificates curl
  fi
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  as_root bash "$installer"
)
```

这条命令会先把脚本完整下载到临时文件，再交给 `sudo bash` 执行。全新安装默认：

- 使用 SQLite；
- 通过两个独立 HTTPS 服务确认公网 IPv4；
- 使用 Let's Encrypt `shortlived` profile 申请可信的公网 IP 证书；
- 提供 `https://公网IP:58090`；
- 把应用限制在宿主回环地址 `127.0.0.1:62031`；
- 输出一个 30 分钟有效、只能使用一次的浏览器初始化凭证。

安装完成后，打开脚本显示的 HTTPS 地址，填写初始化凭证并创建首个管理员。项目没有
默认管理员密码。首次初始化尚未完成且凭证过期时，可运行下文完整的
`install.sh setup` 命令重新签发；已有管理员时会拒绝，不会重置账号。

默认网络关系如下：

```text
https://公网 IP:58090
          │
       Caddy
          │
127.0.0.1:62031 ──> 容器 62031/tcp

公网 TCP 443：ACME TLS-ALPN-01 验证
公网 TCP 58090：IP HTTPS 访问
宿主 TCP 62031：仅回环上游，不应对公网放行
```

受管 Caddy 会自动把公网 `58090` 的可信 HTTPS 请求反向代理到宿主回环
`127.0.0.1:62031`，再进入容器的 `62031/tcp`。安装器会持续显示数据库、应用和公网
HTTPS 的等待进度；只有可信证书、规范访问地址和 `/healthz` 连续通过后，才会输出
`ACTION_COMPLETE action=install`。看到该完成标记前，不要把服务视为安装成功。

### 常用安装选项

下面各行用于替换“一键安装”命令块中的最后一行；`as_root` 和 `$installer` 只在那个
括号块内有效，不要单独复制执行。多个选项可以放在同一个 `env` 命令中。

```bash
# 首次安装使用 PostgreSQL 15；安装后不能切换数据库后端
as_root env OPEN_NODE_DATABASE_BACKEND=postgresql bash "$installer"

# 同时启用域名和 IP 入口；域名成为主地址，IP:58090 仍可登录
as_root env OPEN_NODE_PUBLIC_HOSTNAME=panel.your-domain.com bash "$installer"

# 只使用域名，不申请 IP 证书，也不开放 IP:58090
as_root env OPEN_NODE_PUBLIC_IP=off \
  OPEN_NODE_PUBLIC_HOSTNAME=panel.your-domain.com \
  bash "$installer"

# 指定其他 IP HTTPS 端口；须放行该端口，范围为 1024–65535，且不能与 443 或上游端口相同
as_root env OPEN_NODE_PUBLIC_HTTPS_PORT=58443 bash "$installer"

# 关闭全部受管公网入口，仅保留宿主回环访问
as_root env OPEN_NODE_PUBLIC_IP=off OPEN_NODE_PUBLIC_HOSTNAME= \
  bash "$installer"
```

使用域名前，先配置适用的 A 和/或 AAAA 记录，并确保所有记录都能到达本机。变量只接受
小写 ASCII/punycode 主机名，不能包含协议、端口、路径、账号或密码。

`OPEN_NODE_PUBLIC_IP` 支持三类值：

| 值 | 作用 |
| --- | --- |
| `auto` | 由两个独立 HTTPS 服务确认公网 IPv4；这是全新安装默认值 |
| 公网 IPv4/IPv6 字面量 | 使用明确指定的地址；IPv6 不要加方括号 |
| `off` | 不申请 IP 证书，不创建 IP HTTPS 入口 |

自动探测目前只支持 IPv4。公网 IPv6 可以显式传给 `OPEN_NODE_PUBLIC_IP`，安装器会在
最终 URL 中自动添加方括号。

### 仅通过 SSH 访问

关闭全部公网入口后，应用仍监听宿主回环 `127.0.0.1:62031`。在本地电脑建立隧道：

```bash
ssh -L 62031:127.0.0.1:62031 root@SERVER_IP
```

把 `root` 和 `SERVER_IP` 换成实际 SSH 用户和地址，随后打开 `http://127.0.0.1:62031`。
本地 `62031` 已占用时可以修改 `-L` 左侧端口。如果初装时覆盖了
`OPEN_NODE_HTTP_PORT`，隧道目标也要改成保存的宿主端口；容器内部仍固定为 `62031`。

## 安装后的常用命令

默认安装目录是 `/opt/open-node`。以下命令按普通用户使用 `sudo` 编写；已经登录 root 时
删去 `sudo` 即可。

```bash
# 查看服务、镜像、端口、数据卷和恢复状态
sudo bash /opt/open-node/install.sh status

# 按安装清单记录的仓库/ref 检查并执行更新；默认是官方 main
sudo bash /opt/open-node/install.sh update

# 仅在首次初始化尚未完成时重新签发浏览器凭证；已有管理员时拒绝
sudo bash /opt/open-node/install.sh setup

# 在终端交互创建管理员；不会覆盖已有账号
sudo bash /opt/open-node/install.sh create-admin

# 移除应用、可选 PostgreSQL、网关容器和项目网络
sudo bash /opt/open-node/install.sh uninstall
```

更改现有公网入口也使用 `update`：

```bash
# 重新探测并保存当前公网 IPv4
sudo env OPEN_NODE_PUBLIC_IP=auto \
  bash /opt/open-node/install.sh update

# 更换域名；运行前先修改 DNS
sudo env OPEN_NODE_PUBLIC_HOSTNAME=panel.your-domain.com \
  bash /opt/open-node/install.sh update

# 关闭全部受管公网入口
sudo env OPEN_NODE_PUBLIC_IP=off OPEN_NODE_PUBLIC_HOSTNAME= \
  bash /opt/open-node/install.sh update
```

`uninstall` 会保留应用数据卷、可选 PostgreSQL 数据卷、Caddy 状态卷、源码、私有配置、
安装清单、镜像和备份，不能把它当作彻底清除数据的命令。更新和恢复的事务边界见
[部署文档](docs/deployment.md)。使用官方 `main`、默认安装器和 systemd 的实例，也可以在
“系统设置 → 应用更新”中检查和执行更新。

如果首次安装改过 `OPEN_NODE_CONFIG_DIR`，以后每个动作都要继续传入同一个目录：

```bash
sudo env OPEN_NODE_CONFIG_DIR=/srv/open-node-config \
  bash /opt/open-node/install.sh status
```

默认保存位置：

| 内容 | 位置 |
| --- | --- |
| 源码与安装脚本 | `/opt/open-node` |
| 私有环境和安装清单 | `/etc/open-node` |
| 安装器备份 | `/var/backups/open-node` |
| 应用数据 | Docker 命名卷 `open-node_data` |
| PostgreSQL 数据 | Docker 命名卷 `open-node_postgres-data`，仅 PostgreSQL 模式 |
| Caddy 证书状态 | Docker 命名卷 `open-node_caddy_data`，仅启用受管公网网关时 |
| 网页更新请求与状态 | `/var/lib/open-node-maintenance-open-node` |

覆盖 `OPEN_NODE_INSTALL_DIR` 后，生命周期脚本路径也会改变；覆盖
`OPEN_NODE_PROJECT_NAME` 后，数据卷和网页更新状态目录会使用新的项目名。

## 一键卸载

两份卸载脚本都必须直接在交互式终端中运行。脚本会先核对安装身份并列出目标，再显示
`[Y/n]` 确认提示：直接回车或输入 `y` 会彻底清除，只有输入 `n` 才保留数据。TTY
不完整、身份不匹配或输入其他内容时会停止，不会执行卸载；不要通过管道自动回答提示。

### 卸载控制面

新版本受管安装可以直接运行已安装的脚本：

```bash
sudo bash /opt/open-node/uninstall.sh
```

选择 `n` 时，只停止并删除控制面、可选 PostgreSQL 和公网网关容器、项目网络及网页更新
单元；应用/PostgreSQL/Caddy 数据卷、源码、私有配置、安装清单、备份、镜像和维护状态
仍然保留，可以按原安装身份重新安装。直接回车确认彻底清除时，还会删除三个项目专属
数据卷中实际存在的卷，以及精确的源码、配置、备份和网页更新状态目录。Docker/Git 等
主机依赖、Docker 镜像与构建缓存、远端 Agent、外部 DNS/证书资源和已经下载的代理凭据
不在删除范围内。

如果首次安装覆盖过 `OPEN_NODE_INSTALL_DIR`、`OPEN_NODE_CONFIG_DIR` 或
`OPEN_NODE_BACKUP_DIR`，应从实际安装目录运行 `uninstall.sh`，并继续传入相同值。脚本不
扫描其他目录，也不会接管身份不明的 Compose 项目。

### 卸载 Agent

面板一键安装的 Agent 会保留可校验的 bootstrap helper。可从官方 `main` 完整下载卸载
脚本到临时文件，再交给 root 执行：

```bash
(
  uninstaller="$(mktemp)" || exit 1
  trap 'rm -f -- "$uninstaller"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/agent/uninstall.sh -o "$uninstaller" || exit 1
  sudo bash "$uninstaller"
)
```

也可以在已经审阅的仓库 checkout 中运行 `sudo bash agent/uninstall.sh`；直接通过 Host
deployment CLI 安装、没有面板 bootstrap helper 的实例应使用这种方式。脚本会自动采用
唯一、身份完整的受管 Agent；发现多个安装时会列出它们，并要求输入编号只选择一个。
没有找到候选、选择无效或 unit/manifest/path 身份异常时会拒绝继续，不会按目录通配
删除。

脚本会显示所选 unit、安装根、当前状态和精确匹配的私有 bootstrap 任务数量。选择 `n`
时会停用并删除 Agent unit、当前版本指针和版本环境，保留配置、Token、命令日志、状态、
复制的 Xray/Nginx 运行文件、安装清单、专用账号、本机生命周期辅助文件和 bootstrap
任务，用于恢复或重装。直接回车确认彻底清除时，还会删除精确受管安装根、专用账号、
生命周期辅助单元及与该安装绑定的私有 bootstrap 恢复目录。原始输入文件、受管根目录
之外的 Xray/Nginx 文件、无关 systemd 服务和其他用户目录不会删除；外部 systemd Xray
或已下发的公网入站也不会自动停止或撤销，须由操作者另行处理。控制面中的服务器记录和
Agent Token 也不会因本机脚本自动删除。

两种卸载方式的完整身份规则、数据保留和中断恢复边界见
[控制面部署](docs/deployment.md)与[Agent 部署](docs/agent-deployment.md)。

## 功能概览

### 服务器、Agent 与运行时

- 管理服务器资料、在线状态、系统指标、流量、Xray/Nginx 扫描结果和命令历史。
- 管理员可以查看 Agent 上报的在线用户与 IP；该功能要求 Agent 0.3.0a1 或更高版本，
  已有主机还需要显式启用对应统计配置。
- 在面板创建服务器后生成一次性安装命令，为新 Debian 12 主机安装非 root Agent 和官方
  Xray；控制连接支持 WebSocket 与 HTTP 回退。
- Agent 命令带持久化日志、租约恢复、依赖关系和结果重放，可处理配置、用户、诊断、日志、
  Nginx、WARP 和运行时生命周期。
- 支持自管 Xray、外部 systemd Xray、显式多文件接管，以及校验和固定的 Xray
  安装、升级、回滚和移除。
- 可选兼容运行时覆盖 AnyTLS、Snell 和 Mieru，包括 Mieru UDP 目标转发。

详见 [Agent 一键安装](docs/agent-bootstrap.md)、[Agent 部署](docs/agent-deployment.md)、
[Xray 版本管理](docs/xray-releases.md)和[外部 systemd 模式](docs/external-systemd.md)。

### 用户、套餐与订阅

- 管理用户、套餐、节点、到期时间、流量额度、月度周期、带宽和并发连接限制。
- 独立的 `/account` 用户中心支持密码登录、设备会话、修改密码、TOTP、恢复码、流量查看
  和订阅下载；管理员也可以签发一次性注册邀请。
- 支持 Clash/Mihomo、Surge、sing-box、Xray、URI、Base64、Loon、Quantumult X、
  Shadowrocket、Stash、Surfboard 和 Egern 输出，并按客户端能力过滤不兼容节点。
- 支持临时订阅链接、下载次数和有效期、IP/CIDR 访问限制、订阅档案以及迁移兼容短码。
- 支持 Clash/Surge 模板的个人、套餐、系统三级选择，模板导入导出不会重启运行时或轮换
  订阅凭据。
- 管理员可以统一控制个人模板、外部来源、私有路由和续费入口，并为模板与外部来源设置
  每用户数量上限；后端 API 会执行同样的权限和配额检查。

详见 [订阅系统](docs/subscriptions.md)、[客户端格式](docs/subscription-clients.md)、
[用户账户](docs/subscriber-accounts.md)、[套餐管理](docs/plan-management.md)和
[原生限速](docs/native-limits.md)。

### 外部订阅、规则与 Providers

- 管理员和获准用户可以添加 HTTPS YAML、URI 或 Base64 外部来源，先预览、再确认快照；
  可选定时刷新不会在普通客户端下载时临时抓取上游。
- 支持 DNS、规则、Rule Providers 的替换、前置和追加，支持 Mihomo Providers、请求头、
  服务端 `mmw` 组及可选 IPinfo GeoIP 国家过滤。
- 官方兼容的 `post_fetch`、`pre_save_nodes` 和 `produce()` Hook 在受限 QuickJS 子进程中
  执行，错误脚本不会覆盖已确认快照。

详见 [外部订阅](docs/external-subscriptions.md)、
[订阅规则、Providers 与脚本](docs/subscription-customizations.md)和
[订阅功能权限](docs/subscriber-permissions.md)。

### 流量、探针与节点测速

- 托管凭据可随套餐到期、账号停用或流量耗尽自动撤销，恢复资格后重新下发；离线节点会
  保留待处理状态。
- 保存 Xray 和系统流量，支持上下行额度、手工重置、UTC 月度周期和按用户/节点汇总。
- 管理端与公开 Probe 提供健康、延迟、地区、运营商、回程、流量趋势和跨节点对比。
- 节点测速支持主控或家庭测速端、真实代理下载、连接延迟、出口 IP、单线程/八线程、批量
  队列和历史结果。
- 跨服务器变更集支持审核、依赖、执行和回滚，隧道与路由可以先规划再下发。

详见 [公开 Probe 与架构](docs/architecture.md)、[节点测速](docs/node-speedtests.md)、
[服务器流量](docs/server-traffic.md)和[变更集](docs/change-sets.md)。

### 证书、DDNS 与服务器共享

- 集中管理 ACME 账户、EAB、DNS-01、HTTP-01、证书导入导出、自签名证书、版本历史、
  自动续期和 Agent 部署。
- DDNS 支持 Cloudflare、阿里云、腾讯云 DNSPod、DNSPod Token、GoDaddy 和 NameSilo，
  可跟随服务器 IPv4/IPv6 变化更新 A/AAAA 记录。
- 服务器共享/联邦使用一次性令牌、权限范围、加密传输和可撤销入站所有权；接入的共享
  服务器可以进入普通清单、流量、DDNS、Probe、节点和订阅流程。
- Agent 可管理 Nginx HTTP/TLS 站点、证书轮换和 Nginx/Xray 隧道，并保留失败恢复状态。

详见 [证书管理](docs/certificates.md)、[DDNS](docs/ddns.md)、
[服务器共享](docs/server-sharing.md)和[Nginx 管理](docs/nginx-management.md)。

### 安全、备份与系统设置

- 管理员无默认密码；浏览器初始化凭证只显示在服务器终端。管理员和订阅用户均支持独立
  会话策略，管理员可启用 TOTP、恢复码和强制二次验证。
- 安全控制台记录登录失败、订阅探测、自动或手工 IP 封禁及解封。封禁发生在应用层，
  不等同于主机防火墙规则。
- Web 提供 SQLite/PostgreSQL 一致快照、age 加密下载和浏览器恢复；
  `open-node-backup` CLI 提供 v1 包格式校验、age 加密/解密校验和 SQLite 新目录恢复。
  PostgreSQL v1 恢复只走受控浏览器入口。恢复不会在校验完成前覆盖当前数据，离线复核
  使用独立 Compose 项目。
- 系统设置支持站点文字、Logo、登录背景、浅色/深色/跟随系统主题、公告、管理员通知、
  手工续费审核和网页内应用更新。通知目前限一个 Telegram bot/chat 的管理员提醒；网页
  更新只在官方 `main`、根安装器、systemd 和部署身份校验全部满足时启用。

详见 [管理员安全](docs/administrator-security.md)、[安全事件与 IP 封禁](docs/security-management.md)、
[备份与恢复](docs/backups.md)、[外观设置](docs/appearance.md)、[公告](docs/announcements.md)、
[通知](docs/notifications.md)和[续费审核](docs/renewals.md)。

## 技术架构

| 目录 | 技术与职责 |
| --- | --- |
| `backend/` | Python 3.11+、FastAPI、SQLAlchemy；API、认证、任务、证书、备份和订阅逻辑 |
| `frontend/` | React、官方 Ant Design、Vite、TypeScript；管理端、用户中心和 Probe 页面 |
| `agent/` | 独立 Linux Agent；持久命令、主机遥测和 Xray/Nginx 生命周期 |
| `probe-worker/` | Cloudflare Worker 与静态 Probe 资源 |
| `deploy/` | Docker Compose、Caddy 和手动代理配置 |
| `scripts/` | CI 分片、VPS smoke、发行制品与迁移辅助脚本 |
| `docs/` | 架构、功能、安全、部署、测试和源码对齐记录 |

生产镜像包含 FastAPI 后端和 React 静态资源，不需要在服务器上运行 Vite 开发服务。
应用容器以 UID/GID `10001:10001` 运行，丢弃 Linux capabilities，禁止提权，根文件系统
只读。业务数据库与状态写入命名卷，网页更新只向受限的宿主维护目录写入固定请求。
当前支持一个控制面进程和一个证书工作线程，不要横向扩容或增加 Uvicorn workers。

默认数据库是 SQLite。首次安装可以选择安装器管理的 PostgreSQL 15；数据库不发布宿主
`5432` 端口。数据库后端写入安装清单后不能切换，也不支持 SQLite 与 PostgreSQL 互转。

## 当前状态

| 项目 | 状态 |
| --- | --- |
| 控制面 | `main` 提供 Preview 源码和一键安装器，常规回归由 GitHub Actions 执行 |
| Agent | [0.3.0a2 Preview](https://github.com/FengYuchen1314/open-node/releases/tag/agent-v0.3.0a2)，不是稳定版，也不是 latest 发行版 |
| 部署范围 | 全新 Debian/Ubuntu 单机部署；重点验证 Debian 12 `amd64` |
| 数据库 | 默认 SQLite；首次安装可选择固定的 PostgreSQL 15。自动化集成已通过，PostgreSQL 干净主机一键安装及浏览器恢复的最终实机门仍待完成 |
| MMWX 对齐 | 当前范围见[源码对齐表](docs/mmwx-source-parity.md)，不使用主观完成百分比 |
| 验收记录 | 当前门槛见[源码对齐表](docs/mmwx-source-parity.md)和[测试记录](docs/testing.md)；[交接记录](docs/refactor-handoff.md)包含历史批次，不作为当前发布状态的唯一依据 |

## 使用边界

- 受管安装器面向新主机，不接管现有手工 Compose 项目、已有反向代理或历史 MMWX 数据库。
- 当前发布范围不包含旧 MMWX 整机迁移。仓库里保留的身份导入和旧 Agent 迁移工具属于
  显式、受控流程，不代表安装器会自动迁移旧环境。
- 面板生成的 Agent 命令用于新 Debian 12 `amd64` 主机，不会自动接管已有服务、安装全部
  fork-only 协议或创建公网代理入站。
- 受管公网 HTTPS 依赖操作者拥有公网地址，并保证 TCP `443` 及配置的 HTTPS 端口可达。
  证书、地址或健康检查失败时，安装器会终止，不会降级到明文 HTTP、自签证书或仅回环成功。
- PostgreSQL 恢复只承诺同一数据库后端、同一 Open Node 源码修订及该修订的精确镜像和
  配置；不承诺跨后端、任意跨数据库模式版本或旧 MMWX 数据库恢复。
- DDNS、公共 CA、Cloudflare Worker、IPinfo、Telegram 等外部服务需要操作者自己的账号、
  凭据和真实环境验收。代码测试不能替代生产 DNS、证书、网络和恢复演练。
- 续费是管理员人工审核流程，不含支付；Telegram 只保留管理员通知，不开发用户 Bot 绑定、
  Mini App 或 Bot 专属工作流。
- IP 封禁是应用层控制。已下载的代理凭据也不会因为临时链接到期或外部来源撤销而自动失效。
- 备份覆盖控制面数据库、密钥和受管状态，不是远端 Agent 主机的整机备份。备份生成或格式
  校验成功不等于已经完成恢复演练，也不能证明外部 Agent 的实时状态与信任仍然有效。
- WARP 的本地夹具和真实 Xray 转发已经验证；Cloudflare 公共注册、生产网络和 WARP+
  仍需要操作者实网验收。

## 安装脚本的供应链说明

README 中的一键命令跟随可变的 `main`。它避免执行下载不完整的脚本，但不会把 Raw 下载
和后续 Git clone 自动绑定到同一提交。要求固定供应链时，应先审阅目标提交，使用提交
专属的 Raw URL，把 `OPEN_NODE_REF` 设为已审阅的分支或标签，并用
`OPEN_NODE_EXPECTED_REVISION` 指定预期的 40 位提交；ref 移动时安装器会拒绝继续。
保留已经验收的镜像，不要把移动分支当作可重复构建的发行制品。具体方法见
[部署文档](docs/deployment.md)。

## 本地开发

### 后端

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
open-node-admin create --username admin
export OPEN_NODE_SESSION_COOKIE_SECURE=false  # 仅限本机回环 HTTP 开发
uvicorn open_node.main:app --reload
```

### 前端

```bash
cd frontend
npm ci
npm run dev
```

### Agent 开发

```bash
cd agent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
pytest
```

推送分支后，GitHub Actions 会运行分片后端测试、PostgreSQL 集成、Agent、前端测试与构建、
Probe Worker 和部署 smoke。特权宿主机、真实浏览器及 VPS 验收命令见
[测试文档](docs/testing.md)。维护者也可以在 Windows PowerShell 中运行：

```powershell
.\scripts\vps\sync-and-test.ps1
```

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [公网一键部署](docs/public-deployment.md) | IP/域名 HTTPS、端口、防火墙和 Caddy 边界 |
| [完整部署指南](docs/deployment.md) | 安装器、Compose、更新、备份、恢复和手动代理 |
| [首次初始化](docs/initial-setup.md) | 一次性凭证、浏览器创建管理员和恢复入口 |
| [Agent 安装](docs/agent-bootstrap.md) | 面板签发命令、新主机安装和安全边界 |
| [订阅客户端](docs/subscription-clients.md) | 输出格式、客户端识别和兼容过滤 |
| [备份与恢复](docs/backups.md) | Web/CLI 备份、age、SQLite/PostgreSQL 和复核流程 |
| [系统架构](docs/architecture.md) | 组件职责、数据流和信任边界 |
| [源码对齐表](docs/mmwx-source-parity.md) | 四个官方仓库的固定参考版本与功能差异 |
| [测试记录](docs/testing.md) | CI、VPS、浏览器、容器和真实运行时证据 |
| [重构交接记录](docs/refactor-handoff.md) | 历史工作入口、批次记录和边界变化 |

## 官方参考源码

- [`tajiaoyezi/miaomiaowuX`](https://github.com/tajiaoyezi/miaomiaowuX)：控制面行为参考。
- [`tajiaoyezi/mmw-agent`](https://github.com/tajiaoyezi/mmw-agent)：主机 Agent 行为参考。
- [`tajiaoyezi/mmwx-probe`](https://github.com/tajiaoyezi/mmwx-probe)：公开 Probe 行为参考。
- [`tajiaoyezi/Xray-core-mmwx`](https://github.com/tajiaoyezi/Xray-core-mmwx)：协议运行时参考。

项目按固定提交审计，不静默追随参考仓库的移动分支。精确提交见
[MMWX 源码对齐表](docs/mmwx-source-parity.md)。

## 许可证

Open Node 使用 [MIT License](LICENSE)。第三方组件继续适用各自许可证；它们不是项目的
激活或付费授权机制。
