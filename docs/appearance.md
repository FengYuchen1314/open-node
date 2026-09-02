# 站点外观设置

管理员登录后打开“系统设置”，可以设置站点 Logo、登录背景和默认页面主题。
这组设置与站点文字使用独立版本号，均可单独读取和保存，不需要许可证、激活码或
PRO 权限。它只改变主站管理端、管理员登录页和用户中心，不改变独立 Probe 页面、
订阅内容、TOTP issuer、通知正文或 Agent 配置。

## 主题

站点默认主题支持“浅色”“深色”和“跟随系统”，由项目内语义组件和 CSS 变量统一实现。
原版 MMWX 的 `flat`、`pixel`、`anime` 皮肤没有照搬，以保持操作密度、响应式布局和
可访问行为一致。

访客和已登录用户都可以在页头或登录页选择：

- 站点默认：采用管理员保存的默认主题。
- 浅色或深色：固定当前浏览器的显示方式。
- 跟随系统：响应操作系统的深浅色设置。

个人选择只保存在当前浏览器的 `open-node-theme-preference`，值限定为
`site`、`light`、`dark` 或 `system`，不包含账号、Token 或其他秘密。管理员以后修改
站点默认值时，仍选择“站点默认”的浏览器会跟随变化。

## Logo 和登录背景

每个图片位置可以使用公开 HTTPS 地址，也可以上传文件。外部地址必须是 ASCII 编码
的 HTTPS URL，使用标准 443 端口，不得包含用户名、密码、片段、反斜杠、`localhost`
或 `.local` 主机。浏览器以匿名 CORS、无 Referer 方式加载；外部图片服务器没有允许
跨域读取时，图片会隐藏，登录和导航仍可使用。

上传限制如下：

| 位置 | 最大文件 | 支持格式 |
| --- | ---: | --- |
| Logo | 2 MiB | PNG、JPEG、WebP、GIF、ICO、SVG |
| 登录背景 | 10 MiB | PNG、JPEG、WebP、GIF、ICO、SVG |

后端根据内容解码，不信任文件名或请求的媒体类型；文件名不会进入数据库或公开地址。
栅格图单边不超过 8192 像素，总像素不超过 2500 万；动图不超过 120 帧和 5000 万
帧像素。SVG 拒绝脚本、事件处理器、外部资源、`DOCTYPE`、实体和非 SVG 命名空间，
并使用隔离响应策略提供。图片仍是公开内容，不要上传包含密码、订阅地址、二维码、
内部拓扑或其他秘密的文件。

上传成功后，系统保存内容摘要形成的不可变公开地址。替换或清空图片会删除旧的活动
内容，旧地址随后返回不存在。Logo、背景二进制、媒体类型和外观版本均存放在现有
SQLite 数据库中，因此会进入正常的一致数据库快照；不要用复制运行中 SQLite 主文件
代替项目提供的备份流程。

## 保存、并发和失败

“保存外观设置”只保存主题和两个地址；选择文件后还要单独点击对应的“上传并启用”。
文件选择会在请求发出前从页面状态清除，超时或回执不确定时不会自动重复上传。

管理读取返回 `revision`，保存和上传必须带上当前版本。另一个标签页先修改后，旧版本
操作返回冲突，不覆盖新配置。页面会重新读取一次供管理员核对；重新读取失败时保持
写入关闭，直到手动读取成功。公开读取失败或图片加载失败不会阻止登录，页面回退到
默认浅色主题和文字标识。

## API

| 接口 | 权限和用途 |
| --- | --- |
| `GET /api/v1/appearance` | 匿名读取公开主题、Logo、背景和固定免费标志 |
| `GET /api/v1/appearance/assets/{slot}/{digest}` | 读取当前活动上传图片 |
| `GET /api/v1/system-settings/appearance` | 管理员读取外观和 `revision` |
| `PUT /api/v1/system-settings/appearance` | 管理员按 `expected_revision` 保存主题和地址 |
| `POST /api/v1/system-settings/appearance/{slot}` | 管理员上传原始文件，版本在 `X-Appearance-Revision` |

管理接口继续使用现有管理员会话、Origin 和 CSRF 保护。公开响应不包含版本、会话、
文件名、数据库位置或密钥；所有外观响应禁止缓存并使用固定安全错误，不回显非法输入。

行为参考固定的官方
[branding.go](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/branding.go)、
[system_settings.go](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/system_settings.go)
和[系统设置文档](https://miaomiaowux.com/docs/system-settings/)。SQLite 二进制保存、
严格图片检查、版本冲突处理和项目内语义主题是 Open Node 的实现差异。
