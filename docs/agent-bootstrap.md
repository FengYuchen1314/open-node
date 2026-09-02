# 从面板一键安装 Agent

Open Node 沿用官方 MMWX 的操作习惯：先在控制面创建服务器，再由面板生成只属于该记录的
安装命令，最后到远端主机执行并等待 Agent 上线。当前自动安装面向全新的
**Debian 12 amd64** 主机，要求 root、systemd、Python 3.11+ 和可信 HTTPS；它不会迁移
整机、自动接管已有 Xray/systemd 服务，也不会安装控制面。

## 安装前检查

远端服务器必须能通过可信 HTTPS 访问控制面的规范地址。使用根一键安装器部署控制面时，
该地址会自动设为域名入口，或默认的 `https://公网IP:58090`。必须等控制面安装终端输出：

```text
ACTION_COMPLETE action=install
```

再生成 Agent 命令。公网 IP 模式需要让 TCP `443` 持续可达，以便控制面 IP 证书签发和
续期；`58090` 是面板日常 HTTPS 端口。不要把 Probe Worker 地址当作控制面地址。

自定义可信反向代理时，可在受管更新中明确设置：

```bash
sudo env OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=https://panel.example.com \
  bash /opt/open-node/install.sh update
```

地址只能是规范的 HTTPS URL，可带固定路径前缀；账号密码、查询参数、片段、点路径和编码
绕过会被拒绝。不要手改安装器跟踪的 `/etc/open-node/open-node.env` 或安装清单。

## 面板操作

1. 在“服务器”中新建一条记录，不要复用已经注册或仍在心跳的 Agent 身份。
2. 打开“安装 Agent”。打开窗口只读取状态，不会立即签发票据。
3. 选择自动、WebSocket 或 HTTP 轮询，确认目标是新的 Debian 12 amd64 主机。
4. 生成并完整复制命令，在目标主机的 root shell 中执行。命令含短期、一次性安装票据，
   不要发到聊天、工单或公开日志。
5. 保持终端打开。只有出现 `Agent installed and ready: ...` 才算完成；随后回到面板核对
   Agent 版本、心跳、运行时和遥测。

安装失败不会伪装成成功，也不会只因为 systemd 进程启动就退出。安装器会等待执行中的
release、最新本机健康状态、与控制面的认证连接以及受管运行时的期望状态。失败时会保留
私有输入和诊断信息，供同一安装身份修复和重试。

## 文件从哪里下载

远端 Agent 主机的所有项目文件和固定运行时制品都只从控制面同源端点获取：

```text
/api/v1/agents/bootstrap/manifest
/api/v1/agents/bootstrap/installer.py
/api/v1/agents/bootstrap/artifacts/<固定文件名>
/api/v1/agents/bootstrap/redeem
```

当前 manifest schema 2 包含 Agent `0.3.0a2` wheel、bootstrap 包、`BUILD.json`、官方
Xray `v26.3.27` 归档和固定的 Mihomo `v1.19.30` 制品。面板生成命令时会把 installer
SHA-256 固定下来；installer 再逐项校验 manifest 中的精确路径、文件大小、SHA-256、
Agent 版本和源码身份。Agent 主机拒绝制品重定向，不使用 `latest`，也不执行
`curl | bash`。

控制面自身只代理 manifest 中固定的第三方版本，限制允许的源地址和重定向主机，下载后
验证字节数和 SHA-256，再写入私有缓存。因而 Agent 主机无需访问 GitHub、项目 Release
或第三方制品站。若系统缺少 `python3-venv` 或 CA 包，生成命令可以从该主机已经配置的
Debian APT 仓库安装这两个固定依赖；这不等于所有操作系统软件包都由面板分发，也不会
执行系统大升级。

## 安装结果和安全边界

- Agent 使用无登录专用账号和 systemd 运行，不以 root 常驻。
- Token、配置、命令 journal、状态和缓存使用私有目录；程序、bootstrap 与安装元数据由
  root 拥有。
- 受管 Xray 和 Mihomo 文件进入安装根，不覆盖同机未知服务。初始运行时不会自动创建公网
  代理入站；节点配置由后续受管命令生成。
- 自动模式优先 WebSocket，必要时回退到 HTTP 轮询。两种方式共享同一认证、命令租约、
  幂等重放和结果确认规则。
- 复制过的旧命令可能因票据过期、已兑换、服务器身份变化或控制面更新后 installer
  checksum 改变而失败。这些情况应回面板重新读取状态并签发命令，不要绕过校验。

私有 CA 场景可为初始 `curl` 设置 root 管理的 `CURL_CA_BUNDLE`，并通过
`OPEN_NODE_AGENT_CA_FILE` 让 Python 客户端使用同一受信 CA。CA 会复制到 Agent 私有
配置；因为安装制品全部来自控制面同源，它也验证这些下载。禁止使用 `-k`、关闭证书验证
或把私有 CA 扩展为对任意外部下载站的信任。

## 状态和日志

默认 unit 是 `open-node-agent.service`：

```bash
sudo systemctl status open-node-agent.service --no-pager
sudo journalctl -u open-node-agent.service -n 200 --no-pager
```

自定义实例名使用 `open-node-agent-<instance>.service`，把命令里的 unit 替换为安装器实际
显示的名称。面板中的心跳、版本和命令结果仍是判断认证连接是否完成的依据；本机 unit
显示 active 不能替代面板确认。

## 卸载

一键安装会留下可校验的 bootstrap helper。也可完整下载仓库里的交互式卸载脚本：

```bash
(
  uninstaller="$(mktemp)" || exit 1
  trap 'rm -f -- "$uninstaller"' EXIT
  trap 'exit 1' HUP INT TERM
  curl -fsSL https://raw.githubusercontent.com/FengYuchen1314/open-node/main/agent/uninstall.sh -o "$uninstaller" || exit 1
  sudo bash "$uninstaller"
)
```

脚本必须在交互式 TTY 中运行。它先验证 unit、安装根和 manifest，并在多实例时要求选择
一个，然后询问 `是否彻底清除以上数据？[Y/n]`。直接回车或输入 `y` 默认彻底清除；输入
`n` 只删除运行单元和 release 环境，保留配置、Token、journal、日志、运行时和恢复文件。
非 TTY、EOF、无效输入或身份不一致都会停止。

自定义安装可在已审阅 checkout 中明确指定：

```bash
sudo bash agent/uninstall.sh \
  --root /opt/open-node-agent-edge \
  --unit open-node-agent-edge.service
```

本机卸载不会自动删除控制面中的服务器记录或 Agent Token，也不会撤销已经下发的公网
入站、外部 DNS/证书和客户端凭据。应先按实际运行模式撤销这些资源，再卸载 Agent。
更完整的手工安装、升级、回滚和恢复说明见[Agent 部署](agent-deployment.md)。
