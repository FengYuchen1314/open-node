# 自定义规则与 Proxy Provider

Open Node 参考固定的官方 `miaomiaowuX` 源码实现用户隔离的订阅规则与
Proxy Provider。管理员入口是“规则与代理集合”，用户在管理员开放个人模板或
外部订阅权限后，也可以从用户中心管理自己的资源。功能免费，不检查许可证。

本模块只改变 Clash/Mihomo 与 Stash YAML。其他订阅格式仍按原格式生成，并返回明确
提示，不会把 Clash 规则静默翻译成含义不同的客户端配置。

## 官方源码接点

实现以 `tajiaoyezi/miaomiaowuX` 的固定提交
`c12ce653bc07fe30426b7dfcb85076974b7be0e0` 为准，主要参考：

- `internal/handler/apply_custom_rules.go`：DNS、rules、rule-providers 的应用顺序、
  替换/前置/追加及缺失策略组处理；
- `internal/handler/custom_rules.go`：规则 CRUD 与用户所有权；
- `internal/storage/traffic.go`：`CustomRule`、`ProxyProviderConfig` 和订阅文件选择字段；
- `internal/storage/subscribe_files.go`：空选择表示全部已启用资源；
- `internal/storage/nodes.go`：删除外部来源时级联清理 Provider。

官方 `internal/scriptengine/engine.go` 中的 `post_fetch`、`pre_save_nodes` JavaScript
脚本不属于本批实现，见下方边界。

## 自定义规则

每条规则属于一个用户，名称在该用户范围内唯一，支持三种类型：

- `dns`：内容必须是 YAML 映射，可直接填写 DNS 映射，也可使用顶层 `dns`；
- `rules`：内容必须是非空字符串列表，可直接填写列表，也可使用顶层 `rules`；
- `rule-providers`：内容必须是非空 Provider 映射，可直接填写映射，也可使用顶层
  `rule-providers`。

应用方式为替换、前置或追加。规则按保存时间倒序读取，然后按订阅档案选择过滤。
订阅档案开启规则但不选择具体 ID 时，应用该档案所有者的全部已启用规则；显式选择时
只应用仍存在、仍启用且属于同一所有者的条目。

`rules` 会按规则前两个逗号字段去重，`MATCH` 只保留一条。替换模式保留模板中已有的
`RULE-SET`，避免替换规则时同时丢掉规则集依赖。规则引用模板中不存在的策略组时，
渲染器会补一个 `select` 组，并以现有代理组或 `DIRECT` 作为安全回退。

## Proxy Provider

Provider 必须引用同一用户已经保存的外部订阅来源。客户端配置只得到 Open Node 的公开
快照地址，例如：

```text
/api/v1/proxy-provider/<订阅短码>/<provider-id>
```

公开地址复用订阅档案短码、档案启用/到期状态和用户订阅 IP 策略。响应禁止缓存，不包含
上游订阅 URL、User-Agent 或其他来源凭据。客户端下载 Provider 时，服务器只读取已经
确认并加密保存的节点快照，不会借一次客户端下载触发上游网络请求。

当前支持官方客户端处理模式中的以下字段：

- HTTP Provider、客户端刷新间隔、拉取代理和大小限制；
- 健康检查 URL、间隔、超时、惰性检查和期望状态码；
- 名称包含/排除正则、协议排除；
- Mihomo `override` 节点覆写；
- Provider 启用/停用及订阅档案选择。

Provider 会在模板渲染前写入 `proxy-providers`，因此现有
`__PROXY_PROVIDERS__` 占位符可以直接使用。如果模板没有引用任何 Provider，系统会把
选中的 Provider 通过 `use` 接入第一个代理组，避免生成“存在但不可选择”的无效配置。

删除规则或 Provider 会同步移除订阅档案中的 ID。删除外部订阅来源会在同一写事务中
删除引用它的 Provider，并清理档案引用。

## 管理员与用户操作

管理员：

1. 在“订阅管理 → 外部订阅”创建来源，预览并确认快照；
2. 在“规则与代理集合”创建规则或 Provider；
3. 在“订阅管理 → 订阅配置”开启自定义规则或代理集合，可留空使用全部已启用资源，
   也可选择固定集合；
4. 用 Clash/Stash 下载地址检查生成结果。

普通用户只能看到自己的资源。规则接口要求管理员开放“个人订阅模板”，Provider 接口
要求开放“外部订阅来源”。用户请求体不发送所有者字段，后端始终从登录会话确定所有者。
管理员仍负责在分配给该用户的订阅档案上首次开启相应开关；开关开启且选择留空后，用户
后续新建或停用资源会在下次客户端下载时自动生效。

## 校验与边界

- YAML 拒绝重复映射键、非字符串键、别名/重复节点、过深或过大的结构；
- 规则正文最多 512 KiB，解析节点最多 20,000 个；
- Provider 正则在保存时编译检查，公开快照最多 8 MiB；
- 资源 CRUD 使用修订号比较，冲突时必须重新读取；
- 错误响应不会回显 YAML、上游 URL、正则原文或订阅密钥。

尚未实现的官方能力：

- `post_fetch`、`pre_save_nodes` JavaScript 覆写脚本；
- Provider 的服务端 `mmw` 处理模式、GeoIP 过滤和自定义上游请求头；
- 官方按落盘订阅文件维护的 `custom_rule_applications` 历史。Open Node 每次请求从受管
  模板重新渲染并去重，因此不需要用历史记录从旧文件中撤销上一次注入，但不把这一点
  称为官方脚本/落盘文件的一比一实现。

