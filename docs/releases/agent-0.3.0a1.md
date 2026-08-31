# Agent 0.3.0a1 Preview

本预发布版增加 [在线用户/IP 采集](../online-users.md)，并包含上一批源码中的
IPv6 字面地址证书校验修正。支持范围仍为 Debian 12 amd64、Python 3.11 和
systemd；它不是稳定版，也不表示已完成全部 MMWX 功能。

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
