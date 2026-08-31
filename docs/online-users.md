# 在线用户与 IP

管理员进入“配置工作区”，选择服务器后打开“在线用户”。页面按 Xray 的
`email` 标识列出用户及 IPv4/IPv6，支持搜索和分页；同一 IP 在总数中只计一次。
页面每 30 秒读取最新报告，点击刷新只重新读取控制面，不触发远端扫描。
普通用户和公开探针不能读取这些 IP。

## 开启条件

- 控制面使用包含本功能的版本，远端 Agent 使用 **0.3.0a1 或更新版本**。
- Xray 开启 `stats: {}`，回环地址上的 API 包含 `StatsService`。
- 用户所属策略级别设置 `statsUserOnline: true`，用户本身有 `email` 标识。
  新建 Agent 的安装配置已开启级别 0；新的隧道配置也会开启。

已有服务器不会自动修改配置或重启。在“系统”页读取配置后，可在策略 JSON
中为相应级别启用 `statsUserOnline`，再明确应用修改。统计总开关从关闭切换到
开启时，会同步开启所有已有数字级别的在线统计；只修改其他字段时保留原策略。
更换配置需要按页面现有流程验证并应用。无 `email` 的连接不计入用户列表。

```json
{
  "stats": {},
  "api": {
    "tag": "api",
    "listen": "127.0.0.1:46736",
    "services": ["StatsService"]
  },
  "policy": {
    "levels": {
      "0": { "statsUserUplink": true, "statsUserDownlink": true, "statsUserOnline": true }
    }
  }
}
```

这是配置片段，不能直接替换含入站、出站和路由的完整配置。API 不应向公网开放。

## 数据含义和边界

采集调用官方 Xray 的 `statsgetallonlineusers` 和 `statsonlineiplist`，不清零
流量计数器。[固定 MMWX 分支](https://github.com/tajiaoyezi/Xray-core-mmwx/blob/d3fdae5833a92070414db588ee9893264147b789/app/stats/online_map.go)
和安装器配套的 [Xray v26.3.27](https://github.com/XTLS/Xray-core/blob/v26.3.27/app/stats/online_map.go)
均按活跃连接引用计数移除 IP。它不同于旧 mmw-agent 限制器的“报告间隔内见过的
IP”。使用其他内核时，应以该内核的统计实现为准。

IP 不是设备：NAT 下多人可能共用一个 IP，一台设备也可能使用多个 IP。
多个入站中相同的 `email` 在 Xray 统计中属于同一标识。查询是短时间窗口内的
采样，不是所有用户在同一时刻的事务快照，也不用于限额、封禁或计费判断。

界面分别显示正常、部分样本、未配置、Xray 已停止、内核不支持、采集失败、
未收到支持此功能的报告和数据过期。只有成功的完整空样本才显示“0 个在线用户”。
旧 Agent 即使提供空的 `online_users`，也不会被当作成功采样。

每次最多并发 4 个查询，单次 RPC 2 秒、进程 2.5 秒，整个采集最多 8 秒；
子进程输出沿用 256 KiB 上限。每份报告最多 256 个用户、每用户 64 个 IP、
合计 4096 个 IP 条目。超限或部分配置未开启时显示“部分样本”，不是完整人数。
查询中断或错误会取消其余查询并隐藏本轮 IP，不把 CLI 错误正文写入遥测。

控制面以接收时间判断过期：`max(90 秒, 3 × Agent 遥测间隔)`，最多 15 分钟。
读取接口和页面都会隐藏过期 IP，Agent 时钟超前不会阻挡后续空报告或错误报告。
IP 随私有遥测快照存入数据库，可进入管理员加密备份；本功能不另建 IP 历史页，
也不增加历史快照清理策略。不要将数据库或解密备份公开。

## 验证

在隔离 VPS 上，用固定 MMWX Xray 二进制建立真实 VLESS 连接：能采到连接来源，
连接关闭后恢复为空样本。另覆盖错误/不支持、空样本、IPv6、并发取消、上限、
权限、SQLite 升级、过期和页面切换。记录见 [testing.md](testing.md)。
这不代表全部 MMWX 功能完成，也没有升级现有生产服务器。
