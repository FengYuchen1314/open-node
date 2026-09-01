# 实现说明

这里记录当前仓库的实现结构，服务对象是维护者、审阅者和准备修改代码的人。产品能力、
部署步骤和兼容范围仍以项目根目录的 [README](../../README.md) 及各专题文档为准；本目录
不把测试名称或已有源码推断成额外承诺。

## 阅读顺序

1. [总体架构](architecture.md) 说明进程、信任边界、数据位置和主要调用链。
2. 按修改范围进入模块说明：

   | 模块 | 文档 | 主要入口 |
   | --- | --- | --- |
   | Backend | [backend.md](backend.md) | FastAPI、领域模型、服务与持久化 |
   | Frontend | [frontend.md](frontend.md) | React、路由、API 客户端和公开 Probe 页面 |
   | Agent | [agent.md](agent.md) | 主机 Agent、命令日志簿、运行时与 systemd 部署 |
   | Deploy | [deploy.md](deploy.md) | Compose、Caddy/Nginx、PostgreSQL 和更新桥 |
   | Install | [install.md](install.md) | 根安装器、事务、恢复、卸载与镜像构建 |
   | Scripts | [scripts.md](scripts.md) | CI 分片、构建工具、迁移工具和 VPS 验收 |

3. [源码清单](source-inventory.md) 用于定位每个维护中源码文件、顶层符号和本地依赖。

## 文档边界

- `docs/implementation/` 描述代码怎样工作；[部署文档](../deployment.md)、
  [Agent 部署](../agent-deployment.md)和[测试指南](../testing.md)描述怎样运行与验收。
- Backend、Frontend 和 Agent 的逐文件表由
  [generate_inventory.py](generate_inventory.py) 静态生成。清单不截断顶层函数、类及其直接方法；
  表中的“职责”由目录规则生成，人工说明仍是设计依据。
- `data/` 下保存上游参考源码、构建输入或本地运行数据，不作为 Open Node 直接维护的源码
  逐文件解释。项目自己的 Xray 改动位于 `runtime/xray/`，单列在自动清单中。
- 自动测试说明已有契约和回归门槛，不表示所有操作系统、旧数据库或第三方服务组合均已
  获得生产验证。

## 更新方式

新增、移动或删除实现文件后执行：

```bash
python docs/implementation/generate_inventory.py
python docs/implementation/generate_inventory.py --check --check-links
```

改动跨越模块边界时，还应同步更新总体架构和相应模块文档。以下变化不能只靠重新生成
清单完成：

- 新增进程、监听端口、外部服务或特权 helper；
- 改变数据库、文件、队列、恢复标记或事务状态；
- 改变认证、Token、CSRF、路径所有权、密钥或日志脱敏边界；
- 改变 Backend API、Frontend 调用或 Agent 命令之间的契约；
- 改变 fresh deploy、更新、备份恢复、卸载和旧数据兼容范围。

## 相关权威文档

- [项目架构与产品边界](../architecture.md)
- [MMWX 固定源码参考与差异](../mmwx-source-parity.md)
- [控制面部署](../deployment.md)
- [备份与恢复](../backups.md)
- [Agent 一键安装](../agent-bootstrap.md)
- [Agent 生命周期](../agent-lifecycle.md)
- [安全模型](../administrator-security.md)
