# 公网一键部署

Open Node 的根安装脚本可以在新 Debian/Ubuntu Docker 主机上同时安装控制面和
受管 HTTPS 网关。全新安装默认不要求域名：安装器以 `auto` 通过两个独立 HTTPS
服务确认并保存服务器的实际公网 IPv4，应用容器监听 `62031`，宿主只发布
`127.0.0.1:62031`，固定摘要的
官方 Caddy 2.11.4 网关则提供 `https://<公网 IP>:58090`。IPv6 URL 会自动使用
方括号形式，例如 `https://[2001:db8::10]:58090`。

网关使用 Let's Encrypt `shortlived` profile 为公网 IPv4/IPv6 取得可信短期证书，
有效期约 160 小时（约 6 天），并自动续期。ACME 验证固定使用 TCP 443 上的
TLS-ALPN-01；不使用 HTTP-01，
因此不要求开放 TCP 80，也不会因 80 不可达而降级。

## 准备工作

运行安装命令前完成以下事项：

- 服务器拥有能够由本机独占验证的公网 IPv4 或 IPv6；`auto` 也可以改成显式 IP。
- 放行公网入站 TCP 443 和面板 HTTPS 端口 58090，并确认主机上没有其他程序占用。
  若覆盖了 `OPEN_NODE_PUBLIC_HTTPS_PORT`，放行并检查覆盖后的端口。
- 服务器能够访问 GitHub、Docker Hub、npm、PyPI 和公共 ACME 服务。
- 如果使用可选域名，将它的 A/AAAA 记录正确指向本机，并使用不带协议、端口或路径
  的小写 ASCII/punycode 主机名，例如 `panel.example.com`。

TCP 443 是证书控制权验证入口；58090 是默认的 IP HTTPS 服务入口。TCP 80 不是
受管模式的前置条件。安装器不登录 DNS 服务商，也不修改云防火墙；域名解析、端口
放行和已有代理的清理由服务器所有者负责。

## 一条命令安装

在全新主机上直接运行：

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

脚本会完成以下工作：

1. 安装或检查 Git、Docker、Compose、curl、jq 等主机依赖；
2. 下载干净源码，构建带精确提交标识的控制面镜像；
3. 把容器和宿主应用端口固定为容器 `62031`、宿主 `127.0.0.1:62031`，创建私有
   应用数据卷和安装清单；
4. 解析并持久化实际公网 IP，启用安全 Cookie、精确代理信任和对应的 Agent 公网地址；
5. 拉取固定摘要的官方 Caddy 镜像，以只读文件系统、最小能力和受限日志启动网关；
6. 实时显示公网 IP、数据库、应用和公网 HTTPS 进度；经公网 IP 和 TLS-ALPN-01 取得
   可信证书，再检查证书、规范 URL 和 `/healthz`，连续通过后才报告成功；
7. 输出 30 分钟有效的一次性初始化凭证。

脚本只有在全部步骤完成后才退出，并输出 `ACTION_COMPLETE action=install`。看到这行后
再访问脚本显示的 `https://<公网 IP>:58090`。应用明文 HTTP 端口只在宿主回环可用，
不直接暴露到公网。

## 公网 IP、端口和可选域名

`OPEN_NODE_PUBLIC_IP` 的有效值为：

| 值 | 行为 |
| --- | --- |
| `auto` | 探测实际公网 IP，并把解析出的字面 IP 保存到私有环境；这是全新安装的默认值 |
| 公网 IPv4/IPv6 | 使用并验证操作者明确给出的地址，而不自动探测 |
| `off` | 不创建 IP 证书或 IP HTTPS 入口 |

`OPEN_NODE_PUBLIC_HTTPS_PORT` 是 IP HTTPS 入口，新的安装身份默认使用 `58090`。
需要固定地址或其他端口时，在第一次安装中明确传入：

```bash
sudo env OPEN_NODE_PUBLIC_IP=203.0.113.10 \
  OPEN_NODE_PUBLIC_HTTPS_PORT=58443 \
  bash /path/to/reviewed/install.sh
```

示例地址只是占位符，必须替换成服务器真实公网 IP。

域名是可选入口。提供 `OPEN_NODE_PUBLIC_HOSTNAME` 后，安装器同时保留 IP URL，并把
`https://panel.example.com` 设为控制面的 canonical URL，用于页面、Cookie 和新生成的
Agent 安装命令；未提供域名时，canonical URL 是带端口的 IP HTTPS URL：

```bash
sudo env OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com \
  bash /path/to/reviewed/install.sh
```

如果明确只需要域名入口，可以同时关闭 IP 入口：

```bash
sudo env OPEN_NODE_PUBLIC_IP=off \
  OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com \
  bash /path/to/reviewed/install.sh
```

无论是否配置域名，受管 ACME 都要求公网 TCP 443 可达。普通 HTTPS 页面和 IP 默认
入口不需要 TCP 80。

## 调整和关闭公网入口

调整已安装的公网设置时，运行同一份已下载、审阅过的脚本：

```bash
sudo bash /path/to/reviewed/install.sh update
sudo bash /path/to/reviewed/install.sh status
```

显式传入 `OPEN_NODE_PUBLIC_IP=auto` 会重新探测并保存当前实际 IP。
换域名前先更新解析，再提交新值：

```bash
sudo env OPEN_NODE_PUBLIC_HOSTNAME=new-panel.example.com \
  bash /path/to/reviewed/install.sh update
```

域名和 IP 是两个独立入口。只清空域名会继续保留 IP；只把 IP 设为 `off` 会继续保留
域名。关闭全部受管公网入口必须同时明确设置：

```bash
sudo env OPEN_NODE_PUBLIC_IP=off OPEN_NODE_PUBLIC_HOSTNAME= \
  bash /path/to/reviewed/install.sh update
```

关闭后应用仍只绑定宿主回环 `127.0.0.1:62031`。`uninstall` 删除控制面和
网关容器，但保留应用数据卷、Caddy 证书状态卷、源码、配置、安装清单和备份。
Caddy 卷只保存可重新签发的 ACME 状态，不代替应用数据库备份。

需要交互选择是否清除数据时运行：

```bash
sudo bash /opt/open-node/uninstall.sh
```

提示 `是否彻底清除以上数据？[Y/n]` 时，直接回车或输入 `y` 默认彻底清除；输入 `n`
才保留数据。脚本要求交互式 TTY，不能通过管道代答。

## 失败处理

安装器只在公网 IP/域名、TCP 443 TLS-ALPN-01、可信证书、规范 URL 和 `/healthz`
全部验证成功后提交受管公网设置。失败不会退回明文 HTTP、自签证书、仅回环模式或
静默关闭 IP，也不会签发一个看似成功但不可公网使用的初始化结果。修复端口、地址、
DNS 或出站网络后，使用相同显式设置重试。

检查默认端口和网关日志：

```bash
sudo bash /opt/open-node/install.sh status
sudo docker logs --tail 200 open-node-open-node-1
sudo docker logs --tail 100 open-node-public-gateway
sudo ss -ltnp | grep -E ':(443|58090|62031)\b'
curl -fsS https://PUBLIC_IP:58090/healthz
```

不要使用 `curl -k`，也不要手改 `/etc/open-node/open-node.env` 或伪造网关容器；安装器会核对镜像摘要、命令、
能力、挂载、网络、标签和数据卷身份。

## 官方依据

- [Let's Encrypt 公网 IP 与短期证书公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)：
  IP 证书必须使用 `shortlived` profile，支持 IPv4 和 IPv6，有效期为 160 小时。
- [Caddy `tls` 指令](https://caddyserver.com/docs/caddyfile/directives/tls) 与
  [Automatic HTTPS](https://caddyserver.com/docs/automatic-https#acme-challenges)：TLS-ALPN-01
  的公网验证端口固定为 TCP 443，`disable_http_challenge` 禁用 HTTP-01。
- [IANA 服务名与端口号登记表](https://www.iana.org/assignments/service-names-port-numbers/)：
  62031 和 58090 都在 49152–65535 的 Dynamic/Private 范围；安装器仍会检查本机实际占用。

## 支持边界

受管模式使用站点根路径，不把面板挂在 URL 子路径，也不接管已有 Nginx/Caddy。
占用 TCP 443 的现有边缘代理应继续使用[手动 HTTPS 配置](deployment.md#https-and-proxy-trust)，
不要同时启用受管网关。真实公网证书是否能签发取决于操作者提供的 IP、DNS 和网络
条件；源码测试通过不能代替某台生产主机的最终验收。
