# Agent 0.3.0a2 Preview

本预发布版在 0.3.0a1 的在线用户/IP 与 IPv6 字面地址证书修正基础上，新增有界的
Nginx 版本上报。Agent 只对已配置、由自身管理的 Nginx 二进制执行五秒超时的 `-v`，
只接受有界的标准版本行；失败或旧 Agent 均返回未知，不伪造版本。

构建源码：`72b38c068053ba081a1b437158e914e470e072ba`。
构建目录：`/tmp/open-node-agent-a2-release.8VMimo4S/assets`，共 29 个包内源码文件。

| 资产 | SHA-256 |
| --- | --- |
| `open_node_agent-0.3.0a2-py3-none-any.whl` | `cd072ace55839feb083a8ec99b6fc360162bf20ba9c8fabe3fdc01b06314ea0c` |
| `open-node-agent-bootstrap-0.3.0a2.tar.gz` | `9bc36c9c36b169fe1dcc67269eb11a4a12f0602ab31fc8aac493b8510dfe1310` |
| `BUILD.json` | `106fec027591cdf05976efeaf8eef0cc395409f5aaf7b137189f27c0ab4bf49b` |
| `SHA256SUMS` | `d928f4fd23b68c3667e16ce19e48a8200f67f8177b477396ec89962d56db636f` |

[GitHub Release](https://github.com/FengYuchen1314/open-node/releases/tag/agent-v0.3.0a2)
是 Preview 预发布，不是 stable/latest。带注释标签对象为 `0bf340277cc09ea264f99ee60b0db95aca246aeb`，
指向上述精确源码提交。四个资产包含 wheel、标准库 bootstrap、构建身份和校验清单；
旧版资产没有覆盖或重打包。

隔离 VPS 使用 Python 3.11.2、pip 23.0.1、hatchling 1.32.0 从已提交 Git 对象离线构建，
构建器核对元数据、RECORD、29 个源码字节、bootstrap 成员、`BUILD.json` 和
`SHA256SUMS`。发布前真实 systemd 门通过失败安装回收、非 root 新装/加固、真实 VLESS
转发、升级、显式回滚、失败启动恢复、中断事务恢复、systemd 重启、无孤儿进程、保留
状态卸载/重装和最终清理。发布后从匿名 GitHub 地址分别通过 WebSocket 与 HTTP 的固定
摘要升级、真实流量和回滚。

控制面清单固定这个版本和三个执行资产的地址/摘要。新服务器使用面板生成的安装命令；
已有服务器不会自动升级，需要管理员显式执行 Agent 升级。支持范围仍为 Debian 12
amd64、Python 3.11 和 systemd；生产 `/opt/open-node` 未升级。
