# Agent 0.3.0a1 Preview

本预发布版增加 [在线用户/IP 采集](../online-users.md)，并包含上一批源码中的
IPv6 字面地址证书校验修正。支持范围仍为 Debian 12 amd64、Python 3.11 和
systemd；它不是稳定版，也不表示已完成全部 MMWX 功能。

构建源码：`1484aeb1dce610b127e52eb83a562d4d65f96124`。
构建目录：`/tmp/open-node-online.JUz2LGwv/assets`，共 29 个包内源码文件。

| 资产 | SHA-256 |
| --- | --- |
| `open_node_agent-0.3.0a1-py3-none-any.whl` | `11a566eb9a064f84dd022afe0a5baf97a0d20b1a6e564c7eafb2402665e6f2a3` |
| `open-node-agent-bootstrap-0.3.0a1.tar.gz` | `9bc36c9c36b169fe1dcc67269eb11a4a12f0602ab31fc8aac493b8510dfe1310` |
| `BUILD.json` | `b503249361d1307b8d7caf194b1607c1e54cc1424f257a140131b32e58aae4a3` |
| `SHA256SUMS` | `61c814294011cb2f722361fb0b8cab9874fa4d9e94fdd63ed9fce870a8a0de6b` |

bootstrap 内容未变，故其字节校验和与 0.3.0a0 相同；wheel 和构建记录是新版本。

发布资产见 [GitHub Release](https://github.com/FengYuchen1314/open-node/releases/tag/agent-v0.3.0a1)。
该版本提供 wheel、标准库 bootstrap 包、`BUILD.json` 和 `SHA256SUMS` 四个文件。
`BUILD.json` 记录实际构建源码提交，控制面内置清单固定资源地址和 SHA-256。
从已测试的精确 Git 提交在隔离 VPS 构建，构建器核对 wheel 元数据、RECORD、
源码字节、bootstrap 内容及最终校验和。没有重打包或替换旧版 0.3.0a0 资产。

新建服务器使用面板生成的安装命令。已有服务器由管理员按
[Agent 部署流程](../agent-deployment.md)明确升级；升级不会自动开启原来关闭的
Xray 在线策略，也不会开启特权生命周期 helper。原有生产服务器未升级。

新增采集不修改 Xray 用户或流量计数器，只访问回环地址。支持旧控制面时，
额外遥测字段会被忽略；旧 Agent 连接新控制面时，页面显示未收到支持此功能的报告。
具体超时、容量、隐私和统计含义见功能说明。
