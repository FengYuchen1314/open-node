# 安全事件与 IP 封禁

管理员可在“系统设置 → 安全事件与 IP 封禁”查看登录失败、登录限流、订阅令牌探测、
自动封禁、手工封禁和解封历史，并调整订阅暴力探测阈值。实现依据固定在官方
`miaomiaowuX` 提交 `c12ce653bc07fe30426b7dfcb85076974b7be0e0` 的
[`logs_tables.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/storage/logs_tables.go)、
[`security_logs.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/security_logs.go) 和
[`brute_force.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/brute_force.go)。

## 默认行为

- 订阅短码、订阅 Token、旧 `/x/{code}` 和 Proxy Provider 地址使用同一来源 IP 计数；
- 默认 24 小时内失败 5 次后临时封禁 24 小时；成功解析有效订阅会清除该 IP 的在途计数；
- 回环、私有、链路本地和未指定地址默认不计数，也不执行应用层封禁；
- 自动和手工封禁持久保存在 SQLite，重启后仍有效；未达到阈值的在途计数只在内存保存；
- 登录失败和既有限流产生独立的 `login_fail` / `login_locked` 事件，不自动混入订阅
  Token 封禁计数；
- 解封只结束当前封禁，不删除历史事件。到期的临时封禁不会出现在“当前封禁”表中。

管理员可修改失败次数、统计窗口、临时封禁时长和是否跳过本地 IP。设置使用修订号保存；
旧页面不能覆盖更新后的值。保存回执不确定、手工封禁或解封完成后，页面只重新读取，
不会自动重放写请求。

## 安全边界

封禁发生在公开订阅入口，不修改 `iptables`、云防火墙或 Caddy，也不阻断 Agent 管理连接。
关闭“启用订阅探测封禁”会停止计数并停用应用内 IP 封禁判断。若需要主机级封禁，应在
确认反向代理传递的真实 IP 可信后，另行配置网关或防火墙。

事件只记录固定路由模板，例如 `/api/v1/subscribe/{key}`；不会保存实际订阅 Token、短码、
密码或请求正文。安全 API 只允许管理员访问，响应禁止缓存并使用固定错误，不回显数据库
异常或非法输入。事件、封禁和设置位于控制面 SQLite，随现有一致快照和 Web 备份保存。

## 当前范围

本页完成官方安全事件、订阅暴力探测和 IP 封禁控制台。官方安全设置中的 Turnstile、
未知 User-Agent 策略、可编辑的通用登录/订阅请求速率及封禁通知开关尚未并入这一页；
现有管理员和订阅用户登录限流继续独立工作。多管理员 RBAC 也是后续账户安全批次，
不能把本页称为全部官方安全设置已经完成。
