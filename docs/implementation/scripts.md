# Scripts 实现

`scripts/` 放构建辅助、CI 调度、显式迁移工具和 VPS/容器验收。这里的脚本不是产品后台
worker，也不应在生产数据目录随意执行。每个脚本的输入、目标主机和清理范围各自独立。

## 目录

```text
scripts/ci/          CI 内部测试分片
scripts/container/   最终镜像入口和构建时固定工具下载
scripts/migrations/  离线、显式、窄范围的数据导出
scripts/vps/         VPS bootstrap、同步、release 构建和端到端 smoke
```

全部 60 余个文件、顶层函数和本地依赖见
[Scripts 自动清单](source-inventory.md#scripts--构建迁移与验收)。

## CI 分片

[`run_backend_test_shard.py`](../../scripts/ci/run_backend_test_shard.py) 按测试文件行数做确定性
greedy balance：先按权重降序，再放进当前总行数最小的 shard，路径用于稳定 tie-break。
它会验证每个 `test_*.py` 恰好分配一次、没有空 shard。

脚本默认 12 shard；当前 GitHub workflow 也显式传 12，matrix 索引为 `0..11`。修改 workflow 数量时必须同步 matrix
和 `--shard-count`，不能依赖脚本默认值。`--check-only` 可在不运行 pytest 时查看分配：

```bash
python scripts/ci/run_backend_test_shard.py --shard-count 12 --check-only
```

## 容器脚本

### entrypoint

[`entrypoint.sh`](../../scripts/container/entrypoint.sh) 以 `set -eu` 和私有 umask 运行。它先调用
`python -m open_node.browser_restore_activate`。若返回 pending restore root，只接受当前容器
用户所有、mode `0600`、非 symlink 的 `restore.env`，导入固定恢复环境后 `exec` 最终命令。
普通启动时不读取其他 shell 配置。

### 固定工具下载

[`fetch-lego.py`](../../scripts/container/fetch-lego.py) 和
[`fetch-age.py`](../../scripts/container/fetch-age.py) 只在 Docker build stage 运行。它们按
`TARGETARCH` 选择固定 release URL/checksum，限制 archive member，输出单个 binary 和对应
license。最终容器不在启动时下载这些工具。

升级 lego/age 时需要同时更新版本、架构 checksum、许可证检查、Docker build 与证书/备份
测试；只改 URL 会让构建失败或移除供应链固定。

## 迁移工具

[`export-mmwx-identities.py`](../../scripts/migrations/export-mmwx-identities.py) 从 active MMWX
SQLite schema 只读导出登录身份、套餐、Token 和订阅 profile bundle。它：

- 以 SQLite `mode=ro` 打开明确数据库；
- 检查必需表/列，对可选旧列提供固定缺省值；
- 验证 JSON、TOTP recovery hash、短码和关联；
- 计算内容 fingerprint，原子写入私有输出；已有文件需要显式 `--force`。

输出包含 password hash、TOTP secret、recovery hash 和 subscription Token，属于高敏数据。
它只服务受限 identity import，不迁移服务器、Agent、Xray、证书、流量历史或整机状态，也
不把旧 SQLite 转成 PostgreSQL。操作流程以[旧身份迁移文档](../legacy-mmwx-identities.md)和
相关 API 文档为准，不能把脚本名称理解成完整 MMWX 迁移。

## VPS 基础设施

| 脚本 | 作用 |
| --- | --- |
| [`bootstrap-debian.sh`](../../scripts/vps/bootstrap-debian.sh) | 在隔离 Debian VPS 安装项目测试依赖 |
| [`run-tests.sh`](../../scripts/vps/run-tests.sh) | 顺序创建 Backend/Agent venv，跑 Ruff/pytest，构建 Agent wheel，再跑 Frontend/Probe 测试与构建 |
| [`sync-and-test.ps1`](../../scripts/vps/sync-and-test.ps1) | 本地 Windows 端准备固定 revision 参数并调用远端 runner |
| [`sync-and-test.py`](../../scripts/vps/sync-and-test.py) | 远端只允许 `/opt/open-node[/child]`，验证 clean checkout、origin/branch/full commit 后 fast-forward 并运行测试 |
| [`smoke-sync-and-test.py`](../../scripts/vps/smoke-sync-and-test.py) | 验证同步器拒绝脏目录、移动 ref、错误 origin 和冲突文件 |

远端同步器不会 `git reset --hard`、删除脏工作树或切换 branch。新 commit 中将开始 track 的
路径若已存在为 ignored 文件，也会在 merge 前拒绝，避免 Git 覆盖 VPS 本地状态。

`run-tests.sh` 是 VPS 总回归，不等于 production installer。它会在 checkout 内创建 venv、
安装 npm 包并覆盖测试构建输出，不应对生产 checkout 运行。

## 发布物构建

### Agent release

[`build-agent-release.py`](../../scripts/vps/build-agent-release.py) 从指定完整 Git commit 导出
`agent/` 与 LICENSE，不读取工作树未提交修改。它在离线/no-index 环境构建 wheel，验证
wheel metadata、文件列表、版本、RECORD、权限与大小，并打包 root host bootstrap 文件和
BUILD metadata。

输出目录必须是 repository 外的新/空绝对目录。脚本只构建并校验资产，不创建 Git tag、
GitHub Release 或上传文件。

### Protocol runtime

[`build-protocol-runtime.py`](../../scripts/vps/build-protocol-runtime.py) 固定
`FengYuchen1314/Xray-core-mmwx` commit，依次应用 `runtime/xray/*.patch` 和 overlay：

1. 在未使用的 work dir 初始化 detached source；
2. `git apply --check` 后应用所有补丁；overlay 与上游已有路径冲突时拒绝；
3. gofmt，并跑 AnyTLS、Snell、Mieru、native limiter 和 dispatcher 目标测试；
4. 以 `-mod=readonly -trimpath -buildvcs=false` 构建 binary；
5. 执行 `go mod verify`，输出 binary SHA、patch/overlay SHA、Go 平台、license 和 matching
   source archive。

`--reference-binary` 可同时构建补丁前 reference binary，用于差异 smoke。构建脚本没有
license activation 逻辑；runtime capability 仍需二进制命令和真实流量测试证明。

## Smoke 分组

`scripts/vps/smoke-*.py` 多数创建临时目录、容器、虚拟 systemd 或真实 VPS 服务，成功/失败
后执行自己的清理。按所验证的边界分组：

| 分组 | 代表脚本 | 覆盖内容 |
| --- | --- | --- |
| 控制面部署 | `smoke-control-plane.py`、`smoke-control-plane-installer.py`、`smoke-installer-public-gateway.py` | 镜像、Compose、fresh/update、端口、Caddy、回滚和身份拒绝 |
| 容器 UI | `smoke-branding-docker.py`、`smoke-notifications-docker.py`、`smoke-operator-ui.py` | 真实 Backend+Frontend 资源和 API/DOM 合同 |
| Browser | `smoke-*-browser.py` | Playwright 下的初始化、品牌、通知和 Agent bootstrap 流程 |
| Agent 进程 | `smoke-open-node-agent.py`、`smoke-agent-service.py`、`smoke-agent-bootstrap.py`、`smoke-agent-lifecycle.py` | 非 root 运行、systemd、安装/升级/卸载、面板一键安装和 remote lifecycle |
| Xray/Nginx | `smoke-xray-releases.py`、`smoke-xray-takeover.py`、`smoke-external-systemd.py`、`smoke-nginx.py` | release、接管、polkit、配置事务与恢复 |
| 协议与限速 | `smoke-protocol-runtime.py`、`smoke-reference-agent.py`、`smoke-native-limiter.py` | 固定 fork、参考 Agent 兼容和真实限速/协议流量 |
| 业务闭环 | `smoke-user-management.py`、`smoke-plan-management.py`、`smoke-subscription-*`、`smoke-node-*` | 用户、套餐、节点、订阅输出、访问撤销和清理 |
| 网络运维 | `smoke-diagnostics.py`、`smoke-tunnel.py`、`smoke-warp.py`、`smoke-server-traffic.py` | 诊断、隧道、WARP 与流量聚合 |
| 证书 | `smoke-certificates.py`、`smoke-certificate-http.py`、`smoke-certificate-remote.py`、`smoke-certificate-administration.py` | vault、ACME、HTTP-01、远端部署和管理员安全 |
| Probe | `smoke-public-probe-worker.py`、`run-probe-worker-runtime.mjs` | Worker 路由白名单、header 清洗、静态资产与 WebSocket |

具体 smoke 的前置软件、环境变量和 destructive 范围以文件头/参数为准。文件名相近不代表可
在同一台生产主机安全串行运行。

## 编写脚本的约束

- 目标路径先要求绝对、规范、限定前缀，并检查 symlink/owner/mode；删除使用精确临时目录或
  测试 identity。
- 外部 revision、release URL、image 和 archive 尽量固定完整 commit/digest/checksum。
- subprocess 使用 argv，不拼 shell；敏感值放私有文件、stdin 或环境，不进入命令行和
  普通日志。
- 网络失败、进程超时和清理失败要有不同结果。清理失败不能改写成测试成功。
- production identity 和 test identity 分离；smoke 不复用 `open-node` 生产 project、卷、
  unit 或端口。
- 真实浏览器、systemd、Docker、ACME 和网络测试不能被无条件 skip 后仍计为功能通过。

## CI 关系

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) 当前包含 Backend Ruff、12 个 Backend
pytest shard、PostgreSQL、Agent、3 个 Frontend shard、Probe Worker 和 deployment smoke。
VPS scripts 提供更重的真实系统验收，不会在每次普通 CI 中全部执行。

修改一个跨栈功能时，最低检查通常包含模块单元测试、相关 smoke 和静态配置展开。例如改
Agent 安装命令不能只跑 Frontend dialog test，还要跑 Backend bootstrap contract、host
installer 测试和真实 Agent bootstrap smoke。
