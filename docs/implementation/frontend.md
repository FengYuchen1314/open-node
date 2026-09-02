# Frontend 实现

## 运行入口

Frontend 使用 React 19、TypeScript、Vite、React Router 和项目内语义组件。主站入口
[`src/main.tsx`](../../frontend/src/main.tsx) 创建 `BrowserRouter` 并渲染
[`react/App.tsx`](../../frontend/src/react/App.tsx)。生产构建由 Backend 以同源静态文件
提供；开发环境可用 `VITE_API_BASE_URL` 指向独立 Backend。

公开 Probe 页面是第二个 Vite 入口：
[`public-probe/main.tsx`](../../frontend/public-probe/main.tsx)。它只渲染 `ProbeView
publicOnly`，构建到独立 `dist-probe`，交给 Probe Worker 托管，不包含管理导航和会话初始化。

## 目录分工

```text
src/domain/             TypeScript 类型、枚举、纯校验和显示模型
src/services/           HTTP 客户端、严格响应解析、内存共享状态
src/react/views/        路由页面与页面级请求编排
src/react/components/   功能对话框、面板和编辑器
src/ui/                 原生语义元素封装与图标
src/react/hooks/        会话、品牌、外观和异步生命周期
src/i18n/               已知服务端消息到中文文案的受控映射
src/routes.ts           管理端与订阅用户页面的懒加载路由表
public-probe/           独立公开 Probe 前端
```

`.test.ts`/`.test.tsx` 与被测文件就近放置。全部源码、组件测试、导出符号和相对 import 见
[Frontend 自动清单](source-inventory.md#frontend--运行代码与组件测试)。

## 应用壳与路由

[`routes.ts`](../../frontend/src/routes.ts) 是页面注册表。页面用 `lazy()` 拆包；带
`meta.subscriber` 的 `/account*` 路由跳过管理员壳，由页面自己的 subscriber session
流程处理。其余页面只有 `loadSession` 完成后才渲染：

```text
main.tsx
  → App
  → AppearanceProvider
  → BrandingProvider
  → ApplicationLayout
      ├── subscriber route → WorkspaceRoutes
      ├── session 未就绪   → loading
      ├── 未认证           → SignInView
      └── 已认证           → Layout + navigation + WorkspaceRoutes
```

`WorkspaceBoundary` 以当前 pathname 为 key，某个懒加载页面抛错时只显示工作区错误页。它
不会自动重放尚未确认结果的写请求。导航在桌面使用 Sider，在小屏使用 Drawer；页面标题、
站点名和主题来自 branding/appearance provider。

新增管理页面通常需要同时修改 `routes.ts` 和 `App.tsx` 的 `navigation`。新增 subscriber
页面还要显式设置 `meta.subscriber`，否则它会错误进入管理员会话门。

## API 客户端层

`src/services/` 不只是 fetch 包装。每个 feature client 应承担以下边界：

1. 从同源或 `VITE_API_BASE_URL` 构造固定 API 路径；动态值用 URL/URLSearchParams 编码。
2. 非 GET 请求使用
   [`authenticatedFetch`](../../frontend/src/services/auth.ts)，附带 Cookie 和当前 CSRF Token。
3. 对可疑或高风险响应做字段、类型、长度、枚举和 ID 关联校验，再交给组件。
4. 把已知错误码映射为固定中文；网络异常和未知第三方文字使用本地 fallback。
5. 对“请求结果未知”的操作保留原 request ID 或要求刷新，不擅自重提。

不同功能的严格程度按风险调整。备份客户端
[`services/backups.ts`](../../frontend/src/services/backups.ts) 会限制 JSON content type、响应
大小、超时、重定向、状态码、时间戳和 job ID；普通列表客户端通常依赖较轻的 shape check。
不能把一个宽松读取函数直接复用于凭据、恢复或删除操作。

### 会话状态

[`services/auth.ts`](../../frontend/src/services/auth.ts) 用
[`createObservableState`](../../frontend/src/services/observable-state.ts) 保存内存会话快照，
React hook 通过 `useSyncExternalStore` 订阅：

- `loadSession` 获取配置/认证状态和 CSRF Token；
- `authenticatedFetch` 对非安全方法添加 CSRF，并在 401 时清空内存会话；
- 登录可能先返回 TOTP challenge，只有验证完成后才接受管理员 session；
- 修改密码后立即清空本地 session，因为服务端已撤销全部会话。

管理员密码、TOTP secret、恢复码、订阅 Token 和 Agent bootstrap 命令不写入 localStorage 或
sessionStorage。当前 localStorage 只保存主题偏好，以及测速页的 source/threads 这类非敏感
显示选择；相关测试会检查敏感值未落入浏览器存储。

### 错误文字

[`request-error.ts`](../../frontend/src/services/request-error.ts) 只保留本地定义的 error 和
已知服务端消息。Pydantic validation path 只允许固定字段名与有界数组下标，未知 provider
字段或异常对象不会直接拼到 UI。各 feature 仍需保留自己的响应 shape 校验；统一错误函数
不验证业务数据。

## Domain 与 Service 的对应关系

大多数功能采用 `domain/<name>.ts` + `services/<name>.ts`：

| 层 | 放置内容 | 不应放置 |
| --- | --- | --- |
| domain | 接口、联合类型、常量、纯转换、输入本地校验 | fetch、React state、DOM |
| services | URL、请求 body 投影、响应校验、共享内存状态 | 页面布局、大量 JSX |
| components | 可复用表单、对话框、确认提示、局部异步状态 | 顶级路由决定 |
| views | 页面加载、跨组件协调、route-specific 状态 | 通用 API 解析 |

类型名称与 Backend Pydantic model 相近，但没有自动代码生成。Backend 字段变化必须手工修改
domain、service parser、组件和测试；只放宽为 `unknown as Type` 会移除浏览器端防线。

功能组大致如下：

- 服务器与 Agent：`inventory`、`agent-bootstrap`、`node-management`、`server-management`、
  `changes`、`diagnostics`、`auto-speed`。
- 用户与订阅：`subscriptions`、`subscription-*`、`user-management`、`user-limits`、
  `plan-management`、`temporary-subscriptions`、`registration-invitations`。
- 外部与联邦：`external-subscriptions`、`private-routed-nodes`、`server-sharing`、`renewals`。
- 运维：`backups`、`certificates`、`ddns`、`probe`、`speedtests`、`security`、
  `notifications`、`application-updates`。
- 站点壳：`auth`、`appearance`、`branding`、`initial-setup`。

## 异步状态与写操作

组件普遍把 busy、error、当前 selection 和服务端 snapshot 放在局部 state。对于关闭对话框、
切换记录或连续点击可能产生的旧 promise，使用
[`useAsyncScope`](../../frontend/src/react/hooks/useAsyncScope.ts) 的 generation 判断，避免旧
结果覆盖新页面。AbortController 只负责取消网络等待；不能把 abort 当作服务端写入失败的
证明。

涉及删除、恢复、覆盖配置、生命周期和凭据的 UI 应保持两段式：先加载/预览准确对象和
revision，再提交含 expected revision、确认字段或 request ID 的操作。服务端返回冲突时
重新加载，不用前端快照强行覆盖。

## 关键页面

| 页面 | 主要职责 |
| --- | --- |
| [`DashboardView`](../../frontend/src/react/views/DashboardView.tsx) | 服务器清单、Agent 安装、遥测、运行时动作入口 |
| [`ConfigView`](../../frontend/src/react/views/ConfigView.tsx) | Xray 配置快照、文件工作区、恢复和主机操作 |
| [`SubscriptionsView`](../../frontend/src/react/views/SubscriptionsView.tsx) | 用户、套餐、节点、凭据和订阅管理 |
| [`TemplatesView`](../../frontend/src/react/views/TemplatesView.tsx) | 全局 Clash 模板与套餐分配工作区 |
| [`CertificatesView`](../../frontend/src/react/views/CertificatesView.tsx) | provider、证书、版本、job 和部署 target |
| [`BackupsView`](../../frontend/src/react/views/BackupsView.tsx) | 备份授权、job、一次下载、上传、prepare 与恢复 review |
| [`SystemSettingsView`](../../frontend/src/react/views/SystemSettingsView.tsx) | 管理员资料、安全、品牌、外观、更新与兼容设置 |
| [`AccountView`](../../frontend/src/react/views/AccountView.tsx) | 订阅用户登录、安全、流量、订阅档案和下载 |
| [`ProbeView`](../../frontend/src/react/views/ProbeView.tsx) | 管理 Probe 与公开只读 Probe 的共享页面 |

大型页面把复杂表单下沉到 `react/components/`。例如 Dashboard 复用 Agent bootstrap、server
management、node management、traffic 和 limiter 组件；System Settings 复用管理员资料/
安全、外观、更新和通知面板。组件仍通过 service 层访问 Backend，不直接拼接非固定 API。

## 公开 Probe

`frontend/public-probe` 与管理端共享 `ProbeView` 和公开 Probe service。构建后由
`probe-worker/src/index.ts` 提供静态资产，并只把白名单 GET/WS 路径代理到 Backend。
Worker 删除浏览器 Authorization、Cookie、Host 和上游 Set-Cookie，自己添加 Probe Token。
因此公开页面不能调用管理员或 subscriber account service。

## 样式、品牌与本地化

全局样式集中在 [`react/styles.css`](../../frontend/src/react/styles.css)。主题变量由
AppearanceProvider 选择；品牌标题、Logo 和公开外观由 Backend 公共配置加载。外部图片 URL
与用户提交的品牌内容必须经过对应 service/domain 校验，组件不使用 `dangerouslySetInnerHTML`。

`i18n/zh-CN.ts` 存放 UI 文案与已知错误映射，`i18n/messages.ts` 只翻译固定服务端字符串。
不要用任意服务端 exception 文本作为最终中文提示。

## 测试与构建

Vitest + Testing Library 覆盖 domain、service、hook、component 和 view。CI 使用 12 个
前端分片，并分别构建管理端与 Probe：

```bash
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run build:probe
```

涉及敏感输入的测试应检查 DOM、localStorage、sessionStorage 和错误文案中没有秘密；涉及
异步写入的测试应覆盖关闭、切换、重复提交、请求结果未知和服务端冲突。
