# 通用设置：站点文字首期

状态：首期代码已实现，统一回归及发布验收进行中；尚未发布，
也不是通知提交 `bf8eaa8` 的功能。用法见[站点文字说明](system-settings.md)。
本期只补站点文字配置，不能据此宣称完整系统设置已完成。

## 官方依据

主参考仍为 `tajiaoyezi/miaomiaowuX` 的固定提交
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`：

- [branding.go](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/branding.go)
  定义 `branding_site_title`、`branding_brand_title`，区分管理员保存与登录前公开读取。
- [main.go](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/cmd/server/main.go#L655)
  为管理员接口加 `RequireAdmin`；公开品牌读取不要求登录。
- [traffic.go](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/storage/traffic.go#L2298)
  使用系统设置 KV。这里采用独立类型化表，不开放任意键值写入。

当前[官方系统设置文档](https://miaomiaowux.com/docs/en/system-settings/#current-settings-overview)
把自定义品牌列在“外观”中。该固定仓库的前端已移到私有仓库，见
[build.sh](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/build.sh#L42)；
没有据此声称看过对应提交的前端表单源码。

官方的 PRO 门控、Logo 地址和上传不移植。本项目文字配置免费，始终可用，
无激活或商业许可证调用。原实现逐字段保存且忽略部分写入错误，这里必须原子保存，
失败不能显示成功。

## 字段与作用

| 字段 | 作用 | 本项目限制 |
| --- | --- | --- |
| `site_title` | 管理员登录、后台、用户中心的浏览器标题后缀 | 1–80 个 Unicode 码点 |
| `brand_title` | 登录、侧栏、移动导航、后台和用户中心的界面名称 | 1–40 个 Unicode 码点 |

默认均为 `Open Node`。长度为本项目限制，不是官方约定。
只收字符串，先检查原文，再去除首尾空白并校验长度；原文中的 `Cc`、`Cs`、`Zl`、
`Zp` 类字符及除 ZWJ/ZWNJ 外的 `Cf` 类字符均拒绝，不能靠 trim 隐去非法控制字符。
名称至少含一个字母、数字、标点或符号，拒绝空值、纯空白和仅由连接符组成的
不可见名称。ZWJ/ZWNJ 可用于正常文字或 emoji；其他合法 Unicode 保持原样，
不做 NFC 归一化，不执行其中的 HTML。

表单明确提示名称会公开出现在登录页，不应填密码、Token 或其他秘密。
“恢复默认”只填入默认草稿，仍需点击保存。未保存的草稿不修改全局标题。
折叠标识、技术说明中的 Open Node 产品名、TOTP issuer 和健康检查服务身份不改。

公共 Probe 保持已有独立标题、外观和设置，Probe-only 入口不请求品牌配置。
模板、通知内容、Agent、订阅、环境配置和安全开关均不受影响。

## 数据与 API 合同

在现有 SQLite 数据库中新建独立 `site_branding_settings` 单行表，使用自己的
SQLAlchemy metadata；不向通知表或任意通用 KV 写数据。保存两个文本字段和
递增 `revision`，默认版本为 0。短事务内按版本原子更新两个值；并发修改返回冲突。
每次成功保存均递增版本。数据库失败或损坏的存储值返回固定错误，不能回显 SQL。
不新增密钥、后台任务、文件上传、出站连接或 Agent 命令。

| 接口 | 合同 |
| --- | --- |
| `GET /api/v1/branding` | 匿名可读；仅返回 `site_title`、`brand_title` 和固定 `license_required: false` |
| `GET /api/v1/system-settings/branding` | 仅管理员；返回上述字段及 `revision` |
| `PUT /api/v1/system-settings/branding` | 仅管理员，要求现有 Origin/CSRF；提交 `expected_revision` 和两个文本字段，返回已保存版本 |

版本必须为 0–9007199254740991 的严格整数。写入只接受有限大小的 UTF-8 JSON，
拒绝未知/重复字段、非有限数字和类型转换。错误使用固定 `branding_*` 代码及
安全说明；响应 `no-store`、`no-referrer`，错误不回显提交内容。
没有任意 KV 接口，也不能从公开接口拿到版本、路径、会话或其他系统配置。
SQLite 之外暂不承诺持久化支持；不能因为品牌存储不可用而阻断其余应用启动。

## 前端一致性

新增中文“系统设置”页及导航，只显示本期可用字段，不放未实现的开关。
公开读取使用无凭据的同源请求；失败或无效响应回退默认名称，不阻断登录。
页面刷新时重读，成功保存后当前页面同步更新。旧的公开读取或管理员读取响应
不能覆盖较新的保存结果；卸载、换身份和双击保存均须有生命周期保护。
提交回执丢失时先重新读取已保存设置，不自动重放写入或假报成功。
多标签页可在刷新/重新读取后同步，本期不声称跨标签页实时推送。

所有名称用 React 文本节点和 `document.title`，不插入 HTML、不生成 URL、
不写持久浏览器存储。长名称在 1440、390、320 像素视口中可读，不遮住导航、
登录或退出按钮。异常响应和未知错误不能作为界面文案直接展示。

## 发布门槛

- 默认值、重启与数据库整卷冷恢复；原会话和其他表记录不变。
- 管理员/普通用户/匿名权限与 CSRF；非法正文及非回显错误；并发版本冲突和事务失败。
- Unicode 边界、恶意 HTML 纯文本、公开配置故障回退、晚到响应和双击保护。
- 管理员登录、后台、用户中心的真实构建浏览器验收，三种视口；Probe 独立行为不变。
- 受影响完整回归、双构建、精确 Git 镜像和 CI。仍只在指定 VPS 验收，不动生产。

通知提交的精确版本与 CI 检查独立完成；后续站点文字工作不能改变或冒用它的结果。
