# 自定义规则、Proxy Provider 与覆写脚本

Open Node 参考固定的官方 `miaomiaowuX` 源码实现用户隔离的订阅规则与
Proxy Provider 和 JavaScript 覆写脚本。管理员入口是“订阅自定义”；用户在管理员开放
“订阅自定义”或“外部订阅”权限后，也可以从用户中心管理自己的资源。功能免费，不检查许可证。

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
- `internal/handler/proxy_provider_configs.go`：Provider 字段、所有者隔离及
  `client` / `mmw` 处理模式；
- `internal/handler/proxy_provider_serve.go`：名称、排除和 GeoIP 的匹配优先级；
- `internal/handler/subscription.go`：客户端 `use` 与服务端同名代理组引用语义；
- `internal/scriptengine/engine.go`：`post_fetch`、`pre_save_nodes`、`main` 返回值、
  `produce`、5 秒预算和日志接口；
- `internal/handler/override_scripts.go`：脚本保存时检查语法和用户所有权。

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

当前支持以下官方字段：

- HTTP Provider、客户端刷新间隔、拉取代理和大小限制；
- 健康检查 URL、间隔、超时、惰性检查和期望状态码；
- 名称包含/排除正则、协议排除；
- GeoIP 两位国家代码过滤；名称包含正则与 GeoIP 取并集，排除正则最先执行；
- Mihomo 官方 `header: map[string][]string` 客户端请求头；
- Mihomo `override` 节点覆写；
- 客户端 `client` 与服务端 `mmw` 两种处理模式；
- Provider 启用/停用及订阅档案选择。

Provider 会在模板渲染前写入 `proxy-providers`，因此现有
`__PROXY_PROVIDERS__` 占位符可以直接使用。如果模板没有引用任何 Provider，系统会把
选中的 Provider 通过 `use` 接入第一个代理组，避免生成“存在但不可选择”的无效配置。

`mmw` 模式不生成公开 Provider URL。服务器从已确认快照执行相同过滤和覆写，然后把命中
节点内联到主订阅，并创建或填充与 Provider 同名的 `select` 代理组。没有命中节点时该组
显式回退到 `REJECT`；同名内置策略或代理会拒绝渲染，不会产生含义不确定的配置。官方固定
源码只定义了 `ListMMWProxyProviderConfigs` 和引用识别，没有完整的定时同步调用点；这里
补成每次订阅请求都可执行的受管快照语义。

GeoIP 沿用官方对节点 `server`（IP 或域名）查询国家代码的行为，但不复制源码中的硬编码
第三方 token。部署者必须在私有 `deploy/.env` 设置：

```dotenv
OPEN_NODE_GEOIP_IPINFO_TOKEN=自己的_IPinfo_token
```

DNS 解析在可强制终止的隔离进程中执行，只接受公网地址；IPinfo HTTPS 请求复用外部订阅的
拒绝私网、拒绝重定向、有界响应和总超时边界。单个 Provider 最多查询 64 个唯一服务器，
成功结果在当前 Web 进程缓存。没有 token 时仍可使用名称/协议过滤，但保存 GeoIP 条件会
返回固定错误。节点地址会发送给 IPinfo，介意这一外部依赖时不要启用 GeoIP。

客户端 `header` 只写入 Mihomo 拉取 Open Node 快照的配置，不用于重新请求外部订阅，也不
改变外部来源自己的 User-Agent。请求头会出现在用户下载的订阅正文中，因此界面明确禁止
把密码、Cookie 或其他秘密放进去。

删除规则或 Provider 会同步移除订阅档案中的 ID。删除外部订阅来源会在同一写事务中
删除引用它的 Provider，并清理档案引用。

## JavaScript 覆写脚本

每个脚本属于一个用户，名称在该用户范围内唯一。保存时先检查 JavaScript 语法，启用后
按排序值、创建时间和 ID 稳定执行；订阅档案留空选择表示应用该所有者的全部已启用脚本。
支持官方两个 Hook：

- `pre_save_nodes` 调用 `main(proxies)`，在最终节点已经筛选完成、尚未按客户端格式生成时
  修改节点数组；
- `post_fetch` 调用 `main(config)`。Clash/Stash 会在自定义规则应用后把完整 YAML 配置交给
  脚本，并再次校验配置与节点；其他格式以 `{proxies, proxy-groups, rules}` 调用，节点改动
  会重新渲染为目标格式，无法无损表达的规则或策略组改动会被丢弃并附加警告。

`main` 返回 `null` 或不返回值时保留原输入。脚本可以调用
`produce(proxies, targetFormat)`，复用 Open Node 已支持的订阅格式渲染器。`console.log`、
`console.warn` 和 `console.error` 可调用但不写入服务日志，避免把节点凭据带入日志。

脚本不在 Web 服务进程内运行，而是进入一次性的 QuickJS 子进程。子进程没有 Node.js、
浏览器、网络、文件系统或环境变量 API；父进程只通过标准输入传入有界 JSON，标准错误被
丢弃。脚本最多 256 KiB，输入/输出最多 8 MiB，节点最多 10,000 个，QuickJS 堆上限
64 MiB。超过官方 5 秒执行预算时，父进程最迟在包含启动开销的 6.5 秒墙钟上限强制终止
整个进程组。Linux 额外设置地址空间、CPU、文件大小、打开文件数和 `no_new_privs` 限制。

脚本抛错、超时、返回错误类型或生成无效节点/Clash 配置时，只跳过该脚本并返回固定警告；
错误不会回显脚本正文、节点内容或输入秘密。该能力是受限订阅转换器，不是通用 JavaScript
任务执行器。

固定官方源码定义并完整实现了 `pre_save_nodes` 引擎，但在本次固定提交中没有发现调用点。
Open Node 明确把它接到每次订阅的最终节点渲染阶段，不拦截外部订阅的预览/确认写入；这是
可执行语义补全，不声称复刻一个官方源码中未接线的保存流程。

## 管理员与用户操作

管理员：

1. 在“订阅管理 → 外部订阅”创建来源，预览并确认快照；
2. 在“订阅自定义”创建规则、Provider 或覆写脚本；
3. 在“订阅管理 → 订阅配置”开启自定义规则、代理集合或覆写脚本，可留空使用全部已启用资源，
   也可选择固定集合；
4. 用 Clash/Stash 下载地址检查生成结果。

普通用户只能看到自己的资源。规则与脚本接口要求管理员开放“个人订阅模板”，Provider 接口
要求开放“外部订阅来源”。用户请求体不发送所有者字段，后端始终从登录会话确定所有者。
管理员仍负责在分配给该用户的订阅档案上首次开启相应开关；开关开启且选择留空后，用户
后续新建或停用资源会在下次客户端下载时自动生效。

## 校验与边界

- YAML 拒绝重复映射键、非字符串键、别名/重复节点、过深或过大的结构；
- 规则正文最多 512 KiB，解析节点最多 20,000 个；
- Provider 正则在保存时编译检查，公开快照最多 8 MiB；
- Provider 请求头最多 32 项、每项最多 8 个值、合计最多 16 KiB，拒绝控制字符；
- GeoIP 国家代码最多 64 个，单次渲染最多解析 64 个唯一服务器；
- 覆写脚本的 QuickJS 运行时和返回结构按上述资源、时间与格式边界检查；
- 资源 CRUD 使用修订号比较，冲突时必须重新读取；
- 错误响应不会回显 YAML、上游 URL、正则原文或订阅密钥。

尚未实现的官方存储细节：

- 官方按落盘订阅文件维护的 `custom_rule_applications` 历史。Open Node 每次请求从受管
  模板重新渲染并去重，因此不需要用历史记录从旧文件中撤销上一次注入，但不把这一点
  称为官方落盘文件的一比一实现。
