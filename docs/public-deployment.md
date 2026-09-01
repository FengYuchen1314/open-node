# 公网一键部署

Open Node 的根安装脚本可以在新 Debian/Ubuntu Docker 主机上同时安装控制面和
受管 HTTPS 网关。流程遵循 MMWX [新手教程](https://miaomiaowux.com/docs/tutorial/)
的顺序：先准备域名解析，再安装面板、申请可信证书并接入反向代理。网关使用固定
摘要的官方 Caddy 2.11.4 镜像；Caddy 自动处理 HTTP 跳转、证书申请与续期、
WebSocket 转发、压缩和 HSTS。

## 准备工作

运行安装命令前完成以下事项：

- 将域名的 A 记录指向服务器公网 IPv4；若存在 AAAA 记录，它也必须指向这台服务器。
- 放行公网入站 TCP 80、443，并确认主机上没有其他程序占用这两个端口。
- 服务器能够访问 GitHub、Docker Hub、npm、PyPI 和公共 ACME 服务。
- 使用不带协议、端口或路径的小写域名，例如 `panel.example.com`。

安装器不登录 DNS 服务商，也不修改云防火墙。域名解析和端口放行仍由服务器所有者
完成。保留错误的 AAAA 记录是常见的签发失败原因。

## 一条命令安装

把示例域名替换成实际域名后运行：

```bash
(
  installer="$(mktemp)" || exit 1
  trap 'rm -f -- "$installer"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/install.sh -o "$installer" || exit 1
  sudo env OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com bash "$installer"
)
```

脚本会完成以下工作：

1. 安装或检查 Git、Docker、Compose、curl、jq 等主机依赖；
2. 下载干净源码，构建带精确提交标识的控制面镜像；
3. 把应用端口限制在 `127.0.0.1`，创建私有 SQLite 数据卷和安装清单；
4. 自动启用安全 Cookie、代理信任和同域名 Agent 安装地址；
5. 拉取固定摘要的官方 Caddy 镜像，以只读文件系统、最小能力和受限日志启动网关；
6. 从本机使用真实域名和 SNI 检查可信证书及 `/healthz`，连续通过后才报告成功；
7. 输出 30 分钟有效的一次性初始化凭证。

安装成功后访问 `https://panel.example.com`，在中文初始化页设置管理员账户和站点名称。
应用的明文 HTTP 端口不会直接暴露到公网。

## 更新、换域名和关闭网关

后续运行同一份已下载、审阅过的脚本。普通更新不传域名，安装器会保留当前公网设置：

```bash
sudo bash /path/to/reviewed/install.sh update
sudo bash /path/to/reviewed/install.sh status
```

换域名前先把新域名解析到服务器，再显式提交新值：

```bash
sudo env OPEN_NODE_PUBLIC_HOSTNAME=new-panel.example.com \
  bash /path/to/reviewed/install.sh update
```

关闭受管网关使用空值。网关容器会删除，应用重新保持仅回环 HTTP；安全 Cookie、
代理信任和安装器自动设置的 Agent 地址会一起恢复为本地默认值。Caddy 数据卷保留：

```bash
sudo env OPEN_NODE_PUBLIC_HOSTNAME= \
  bash /path/to/reviewed/install.sh update
```

`uninstall` 删除控制面和网关容器，但保留应用数据卷、Caddy 证书状态卷、源码、配置、
安装清单和备份。Caddy 卷只保存可重新签发的 ACME 状态，不代替应用数据库备份。

## 失败处理

安装器最多等待约三分钟取得可信证书。失败时先检查域名 A/AAAA、入站 80/443、
端口占用和服务器出站网络。默认项目可查看：

```bash
sudo docker logs --tail 100 open-node-public-gateway
sudo ss -ltnp | grep -E ':(80|443)\b'
```

控制面可能已经健康落盘，但脚本不会在 HTTPS 未通过时签发浏览器初始化凭证。修好外部
条件后重新运行 `install`，随后执行 `setup`。不要手改 `/etc/open-node/open-node.env`
或伪造网关容器；安装器会核对镜像摘要、命令、能力、挂载、网络、标签和数据卷身份。

## 支持边界

受管模式固定使用域名根路径和标准 80/443 端口，不接管已有 Nginx/Caddy，也不支持
把面板挂在 URL 子路径。已有边缘代理的主机继续使用
[手动 HTTPS 配置](deployment.md#https-and-proxy-trust)，不要同时启用受管网关。
真实公网证书是否能签发取决于操作者提供的 DNS 和网络条件；源码测试通过不能代替
某个生产域名的最终验收。
