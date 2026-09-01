# 网页内应用更新

管理员可以在“系统设置 → 应用更新”检查官方 `main` 的最新提交，并在核对目标提交后
执行更新。更新仍由根安装器完成，不由 Web 容器直接控制 Docker 或修改宿主机配置。

## 使用条件

网页更新只在同时满足以下条件时启用：

- 使用根目录 `install.sh` 安装；
- 源仓库是 `https://github.com/FengYuchen1314/open-node.git`；
- 安装引用是 `main`；
- 宿主机使用 systemd；
- 安装目录、私有环境、安装器清单、数据卷和当前容器仍通过安装器身份校验。

手工 Compose、自定义仓库、其他分支或非 systemd 主机会显示“不可用”。这些部署没有
隐式降级路径，仍需在宿主机运行：

```bash
sudo bash /opt/open-node/install.sh update
```

## 操作流程

1. 管理员打开“系统设置”，点击“检查更新”。宿主机只查询官方 GitHub `main` 的完整
   40 位提交，不下载或执行页面提供的地址。
2. 页面显示当前提交、目标提交和固定的 GitHub 提交链接。目标提交变化后，旧确认失效，
   必须重新检查。
3. 勾选短暂停机确认，再点击“立即更新”。请求只包含固定动作、请求 ID、目标提交和时间。
4. 宿主机重新查询官方提交；只有结果仍等于页面确认的提交时才调用安装器。
5. 页面在服务重启期间继续轮询。完成后显示新提交；失败时显示保留旧部署或需要人工恢复。

更新期间不要重复提交。浏览器无法确认写请求结果时只重新读取状态，不会自动重发。

## 宿主机边界

安装器为每个 Compose 项目建立独立目录：

```text
/var/lib/open-node-maintenance-<project>
```

目录属于 `root:10001`，权限为 `1770`。容器只能在这里以运行用户写入一个 `0600` 的
`request.json`；根拥有的 `state.json` 为 `0640`。目录使用 sticky 位，容器不能删除或
替换根拥有的状态文件。Web 容器没有以下挂载或权限：

- Docker socket；
- `/opt/open-node` 源码；
- `/etc/open-node` 私有配置；
- `/var/backups/open-node` 备份；
- 任意 shell 命令、仓库地址、分支、路径或环境变量输入。

systemd path unit 只监视固定的 `request.json`。根助手检查目录、配置、安装器清单和请求
的文件类型、所有者、权限、硬链接数、字段集合及长度，然后执行固定参数：

```text
bash /opt/open-node/install.sh update
```

目标提交通过 `OPEN_NODE_EXPECTED_REVISION` 绑定。安装器 fetch 后若提交已经变化，会在
停止容器或创建备份前拒绝更新。

## 备份、失败与恢复

网页更新复用[部署说明](deployment.md#installer-managed-updates)中的完整事务：

- 只接受当前部署提交的 fast-forward 后代；
- 为每次真实更新构建唯一候选镜像；
- 启动候选前停止并归档数据卷，同时记录旧配置、清单和不可变镜像 ID；
- 候选必须通过 Compose 白名单、容器身份和连续健康检查；
- 候选接触真实数据后发生失败时不直接让旧镜像读取可能已经迁移的数据；
- 需要人工判断的情况写入私有 `installer.recovery`，页面显示“需要人工恢复”。

出现恢复标记时先运行：

```bash
sudo bash /opt/open-node/install.sh status
```

不要删除标记来绕过检查，也不要直接用旧镜像启动当前数据卷。按照状态中记录的备份，
在独立项目和新数据卷中验证旧镜像与旧数据后再决定切换。

`uninstall` 会关闭并删除该项目的 systemd 更新单元和根配置，但保留应用数据、备份、
安装源码以及维护状态目录。重新安装后，安装器重新核对并初始化助手。

## 与固定官方源码的关系

实现参考固定的 `tajiaoyezi/miaomiaowuX` 提交
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`：

- [`internal/handler/update.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/update.go)
- [`internal/handler/update_cdn.go`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/update_cdn.go)
- [`docker-entrypoint.sh`](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/docker-entrypoint.sh)

官方后端下载并替换容器内 `/app/data/server`，随后执行新二进制。本项目使用 Python、
React 和不可变 Docker 镜像，不能安全照搬二进制自替换。因此保留官方的检查、确认、
进度和重启体验，把宿主操作收敛到已验证的安装器事务。网页不能选择更新 CDN、任意
版本或自定义仓库。

## API

三个接口都要求管理员会话，写接口还要求既有 Origin 和 CSRF 检查：

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/application-update` | 读取宿主机助手状态和当前/目标提交 |
| `POST /api/v1/application-update/check` | 请求宿主机检查官方 `main` |
| `POST /api/v1/application-update/apply` | 提交已确认的完整目标提交 |

请求和响应禁止缓存。接口拒绝重复字段、未知字段、隐式类型转换、超长正文和非完整提交；
普通订阅用户不能读取或提交应用更新。
