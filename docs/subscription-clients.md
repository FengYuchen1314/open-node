# 客户端订阅使用说明

用户登录 `/account`，在“订阅”页选择“客户端格式”，复制生成的订阅地址，或点击“下载”。管理员在订阅管理的用户详情中选择同一格式，可以同时查看可用节点数和被排除的原因。切换格式只改变导出内容，不会重置链接、改套餐或重启节点。

订阅地址包含访问凭据，请勿贴到公开工单、截图或在线转换网站。账号、套餐、配额和节点权限仍按原有规则检查；格式识别不会绕过这些限制。

## 六种新增格式

| 客户端 | `format` 参数 | 导出内容 | 主要不支持项 |
| --- | --- | --- | --- |
| Loon | `loon` | Loon 原生节点文本 | Snell、Mieru、gRPC；自定义指纹和高级传输选项 |
| Quantumult X | `quantumult-x`，兼容 `qx` | QX 原生节点文本 | Hysteria2、Snell、Mieru、gRPC；多个 ALPN 值 |
| Shadowrocket | `shadowrocket` | `proxies` 列表的节点 YAML | Snell v6、Mieru、HTTPUpgrade、XHTTP |
| Stash | `stash` | 节点和兼容模板组成的 YAML 配置 | 当前管理的 Snell v4/v5/v6、Mieru；不兼容的 Clash 模板扩展 |
| Surfboard | `surfboard` | Surfboard 原生节点文本 | VLESS、Snell v6、Mieru、gRPC；非 `auto` 的 VMess 加密设置；Hysteria2 混淆 |
| Egern | `egern` | 按协议分组的节点 YAML，例如 `proxies: [{trojan: ...}]` | Snell v6、Mieru；HTTP/SOCKS 的 TLS 包装；自定义 ALPN/指纹 |

Shadowrocket 此处导出 **YAML，不是 Base64**，遵循固定版官方转换器的输出行为。需要 URI 或 Base64 节点列表时，应明确选择现有的“URI 列表”或“Base64”格式；它们的协议支持范围与 Shadowrocket YAML 不相同。

除 Stash 外，上表导出均为节点订阅，不携带 Clash/Mihomo 的分流规则、脚本或完整应用设置。应使用客户端适合该内容类型的导入入口，不能把节点列表直接当成完整配置文件。具体版本是否接受该入口，仍需在目标客户端确认。

各格式支持列表中的协议，也有参数限制。不能据此把任意同名协议配置视为可转换：

- VLESS、VMess、Trojan 的基础 TCP/WebSocket 可按客户端转换；Stash、Shadowrocket 还支持 gRPC，Egern 的 gRPC 仅支持启用 TLS 的 VLESS/VMess。六种新增格式均不转换 HTTPUpgrade、XHTTP、HTTP/2 或 HTTP 伪装。
- VLESS REALITY 仅转换 TCP；Surfboard 不支持 VLESS。自定义 uTLS/证书指纹不能在 Loon、QX、Surfboard、Egern 中保留时，会明确排除。支持的 SNI、证书校验开关及 REALITY 公钥/短 ID 不会被换成不安全的默认值。
- 原生文本格式仅接受能明确表示的凭据和节点参数。含换行、逗号等配置分隔符的凭据会被拒绝；节点名称会规范化并补唯一后缀。WebSocket 自定义头和 early-data 扩展不做猜测转换。
- Shadowsocks 支持 AES-128/256-GCM、ChaCha20-IETF-Poly1305，以及 2022 AES-128/256-GCM；插件只处理支持的 simple-obfs HTTP/TLS 选项。Loon 不组合 2022 加密与 obfs；附加 TLS 仅在 Shadowrocket YAML 中保留。
- Shadowrocket、Surfboard、Egern 可转换当前支持参数范围内的 Snell v4/v5，均不转换 v6。VMess 的非零 `alterId` 仅由 Stash/Shadowrocket YAML 保留。
- Hysteria2 的端口跳跃只在 Stash/Shadowrocket YAML 中保留；Loon/Surfboard 不转换上传带宽参数，Egern 不转换下载带宽参数。AnyTLS 的空闲会话参数只在 Stash/Shadowrocket YAML 中保留。

## 自动识别与显式选择

直接使用已有订阅链接，省略 `format` 或设置 `format=auto`，服务端会根据请求的 User-Agent 选择格式。显式指定 `format` 时以该参数为准。例如，在已复制的长链接后使用 `?format=loon`、`?format=quantumult-x` 或 `?format=stash`；原链接已有查询参数时使用 `&format=...`，并删除旧的同名参数。

识别不区分大小写，按下列顺序匹配；浏览器下载通常应显式选择格式。

| User-Agent 中的标记 | 自动格式 |
| --- | --- |
| `Stash` | `stash`，优先于其中同时出现的 `Clash` |
| `Shadowrocket` | `shadowrocket` YAML |
| `Loon` | `loon` |
| `Quantumult%20X`、`Quantumult X`、`QuantumultX` | `quantumult-x` |
| `Egern` | `egern` |
| `Surfboard` | `surfboard` |
| `sing-box`、`SFI/`、`SFA/`、`SFM/`、`SFT/` | `sing-box` |
| `v2rayn`、`v2rayng`、`v2box` | `base64` |
| 未识别的标记 | `clash` |

同一选择逻辑用于普通订阅、已启用的兼容短链接和临时订阅。兼容 `/x/...` 仍保留已知 `t=loon`、`t=qx` 等旧参数的优先行为，请不要同时混用 `t` 和 `format`。短链接默认关闭，新增格式不会开启该功能；临时链接的次数和过期限制也不变。

## Stash 模板范围

Stash 复用当前全局 Clash 模板选择，包括套餐绑定、系统默认及内置模板，不新增独立的 Stash 模板库。支持范围内的节点组、规则、规则来源和 DNS 配置会保留；Hysteria2 等节点字段按 Stash 的字段名转换。

当前模板检查只允许节点、节点组、节点来源、规则、规则来源、DNS、hosts，以及 mode/log-level/ipv6/端口/allow-lan 等基础字段。`tun` 等额外顶层设置会被拒绝。MRs 规则来源或以 `.mrs` 结尾的 URL/路径也会被拒绝；不会靠改后缀猜测另一个下载地址。

DNS 只处理默认解析器、nameserver、单目标 nameserver-policy、direct/proxy-server nameserver、fake-ip-filter 和显式证书校验设置。direct/proxy-server nameserver 会合并至 nameserver 并去重；单元素策略列表转为单个值。`geosite:`、`rule-set:` 策略键及多目标策略暂不转换。服务端不会隐式关闭 DNS TLS 校验。

模板不兼容时，该次 Stash 导出不可用，管理员预览会显示原因。可修改或另选兼容模板；不通过丢弃规则来生成一个貌似成功的配置。其他五种新增节点格式不使用这些模板。

## 节点缺失或下载失败

管理员在格式预览中可查看每个节点的可用状态和原因，预览不返回密码。订阅响应的 `X-Open-Node-Included-Nodes` 和 `X-Open-Node-Excluded-Nodes` 分别记录实际包含、排除的节点数；不兼容节点不会进入下载内容。所有节点均不兼容时返回 404，不生成空订阅或直连兜底。未知格式返回 422。

遇到 404 还应检查账号是否启用、套餐是否到期、流量是否用尽，以及选定节点是否仍在套餐内。外部来源节点沿用已有确认和可用性检查。下载响应仍为 `Cache-Control: no-store`，临时链接不返回用户流量汇总头。

## 依据与验证范围

格式字段参考固定 `miaomiaowuX` 提交 `c12ce653bc07fe30426b7dfcb85076974b7be0e0` 的 `internal/handler/subscription.go`、`client_ua.go`，以及其 `go.mod` 固定的 `proxyparser/substore v0.1.7`。源码入口见 [官方转换模块](https://github.com/MMWOrg/mmwX-plugins/tree/proxyparser/v0.1.7/proxyparser/substore)。本实现限制不能安全保留的选项，也不沿用隐式关闭 TLS 校验或猜测规则 URL 的处理。

本轮覆盖了六格式真实订阅 API、UA/显式选择、兼容性预览、TLS/REALITY 字段、模板、临时链接和私密字段边界的 18 个定向用例，前端类型检查通过。尚未声称六个商业/移动客户端都做过原生导入或真实流量验收；原有 Mihomo、sing-box、Xray 的验收范围见[订阅与协议说明](subscriptions.md)。
