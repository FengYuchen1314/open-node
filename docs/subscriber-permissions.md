# 用户功能权限与配额

管理员可以在“系统设置”统一控制普通用户可见、可调用的四项可选功能：订阅自定义、
外部订阅来源、个人路由节点和续费申请。全局模板只由管理员维护；订阅下载、套餐信息和账户安全始终
保留，管理员管理接口不受这项策略影响。

新安装默认开放四项功能。外部订阅来源的数量上限默认是 `0`，表示不限制。关闭功能
不会删除已有数据；重新开放后，用户可以继续使用原有数据。

## 后端强制执行

权限不是前端导航开关。普通用户直接调用以下账户接口时，后端会在认证和 CSRF
检查之后再次读取当前策略；未开放的功能固定返回 `403` 和
`subscriber_feature_disabled`。

| 功能 | 账户接口 |
| --- | --- |
| 订阅自定义 | `/api/v1/account/subscription-customizations`、`/api/v1/account/subscription-scripts` |
| 外部订阅来源 | `/api/v1/account/external-subscriptions` |
| 个人路由节点 | `/api/v1/account/private-routed-nodes` |
| 续费申请 | `/api/v1/account/renewals` |

外部订阅来源的创建数量在原有 SQLite 写事务内统计。并发创建到达上限
时只允许一个事务成功，其余请求固定返回 `409` 和
`subscriber_quota_exceeded`。管理员可以代用户维护资源，不受普通用户配额限制；
因此管理员添加后，用户中心可能显示“已用数量”大于当前上限，用户不能继续新增，
但已有资源不会被自动删除。

## 用户中心

用户中心读取 `/api/v1/account/permissions`，只显示已经开放的路由、订阅自定义、外部订阅
和续费入口，同时显示外部订阅来源的已用数量及上限。权限读取失败时，
页面保留订阅和安全设置，对四项可选功能采用关闭显示；不会根据旧缓存猜测权限。

## 管理接口与保存

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/subscriber-permissions` | 管理员读取策略、配额和版本 |
| `PUT /api/v1/subscriber-permissions` | 管理员以 `expected_revision` 原子保存 |
| `GET /api/v1/account/permissions` | 当前普通用户读取开放页面和个人用量 |

保存采用版本比较。旧版本提交返回 `409`，不会覆盖另一管理员已经保存的结果。
管理写请求最多 8192 字节，只接受 UTF-8 JSON，拒绝重复字段、未知字段、非有限数字、
隐式类型转换和非规范页面顺序。正常及错误响应均禁止缓存，不回显无效输入。

策略保存在现有 SQLite 数据库的独立 `subscriber_permissions_policy` 单行表中，随
数据库一致性备份和恢复。存储损坏不会被自动重置为全开放；接口会返回固定的
暂不可用错误。

## 与官方实现的关系

实现依据固定的官方
[`user_permissions.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/user_permissions.go)。
官方页面名与 Open Node 的产品结构不同：本项目把已经具备独立账户接口的订阅自定义、
外部订阅、私有路由和续费纳入统一策略；私有路由的启用、节点数量和每日操作次数
仍由既有私有路由策略管理，不在这里重复配置。官方 `generator`、`subscribe-files`、
`custom-rules` 和 `nodes` 的剩余能力随规则/Provider 和共享功能继续实现。

本功能免费，不连接官方许可证服务，也不复制依赖商业许可证的 Reality 公共池。
