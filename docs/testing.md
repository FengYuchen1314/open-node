# Testing

## Subscription clients, self-service sources, certificates and renewals — 2026-08-31

Following the request to prioritize functional delivery, these changes use
focused VPS checks and one combined frontend build, not repeated historical
full-suite/image audits:

- Six additional client formats: **18 backend tests**, Ruff and both frontend
  type checks passed. Evidence: `/tmp/open-node-client-exports-r2.4MTeuMkn/evidence`.
  This exercises actual subscription APIs and serialization; it is not native
  acceptance in all six commercial/mobile applications.
- Self-signed certificates: **62 backend, 30 Agent and 28 frontend tests**, Ruff
  and type checks passed in one run at
  `/tmp/open-node-self-signed.yRbHwiv5/gate-r1`. The published Agent 0.3.0a0
  wheel and bootstrap pin were not changed; IPv6-literal certificate validation
  requires an Agent build containing the new source change.
- External URI/Base64 input and subscriber-owned sources: **14 backend and
  8 frontend tests** passed. One account cancellation check was subsequently
  rerun after making missing/foreign preview IDs uniformly 404. Existing
  administrator cancellation idempotency is preserved.
- Web renewal requests/review: **16 backend tests** and Ruff passed at
  `/tmp/open-node-renewals-root.Z7MQVWeH/evidence-r2`, including concurrent
  duplicate approval, durable Agent commands, owner separation, expiry math,
  rollback and restart. **10 frontend tests** passed across the combined run
  and one corrected UI-test rerun.
- Combined app/node type checks and the **main and Probe production builds**
  passed at `/tmp/open-node-external-inputs.UtDfJ2on/evidence-r3`; artifacts are
  in the adjacent `runtime-r3/dist` and `runtime-r3/dist-probe` directories.

Original failures are retained: the client REALITY check initially treated a
generated default uTLS value as an explicit user option; it was corrected.
A dependency symlink caused TS2742 and was replaced only in the private test
environment. The first combined type check caught a missing JSX closing brace.
The first renewal fixture omitted required `traffic_limit_gb` (15 fixture
failures, one real concurrency test passed); the fixture was corrected. The
renewal double-click test then failed only because Ant Design's loading icon
changed the button's accessible name; retaining the same DOM button reference
preserved the double-click/secret-clearing assertions and passed. Only affected
checks were rerun. These records are not a new full-project or Docker gate.

Production and the shared candidate were not upgraded. The release commit's
GitHub CI is the independent complete repository check; use that exact run's
status rather than projecting any older gate onto this source.

## Web backup jobs — 2026-08-31

The administrator Web creation/download slice passed one VPS integration run:
**512 backend tests, zero skips**, including the corrected 419 snapshot/creator
cases, fresh authorization, security-epoch revocation, bounded job lifecycle,
HTTP protections, real HTTP creation/download and independent official-age
decryption. Ruff passed. Evidence:
`/tmp/open-node-web-backups-root.N22zbI9f/evidence-r1`.
The Chinese Ant Design page passed **17 focused frontend tests**, both TypeScript
checks and the production main build at
`/tmp/open-node-backups-ui.bvumaW2W/evidence-r1`.
This is targeted integration evidence, not a new full-project or restore gate.

Earlier failures remain recorded rather than relabelled as successes:

- Candidate `ef93676` CI `33398241764`: backend 4718 passed, 103 skipped, one
  state-fixture permission failure; the other three jobs passed. Under umask022,
  `mkdir(parents=True)` left intermediate job directories 0755. The fixture now
  creates each missing parent as 0700 without repairing existing directories.
  A separate old-fixture reproduction failed as expected, then all 64 corrected
  state cases passed, including three umasks and unchanged-public-directory rejection.
- Full candidate VPS run: 4820 passed, two SQLite-test failures. The SIGINT test
  assumed a default handler, but the reproduced background launch inherited
  SIG_IGN. The corrected test installs/restores the intended handler and exercises
  an actual interrupt during paginated copying. Descriptor cleanup now identifies
  new descriptors rather than treating an unrelated descriptor closing as a leak.
- The next 419-case run was 418/1: its remaining descriptor belonged to the
  previous snapshot test's database. Two fixture writers committed but did not
  close their SQLite connections. Explicit closing fixed the fixture; the later
  512-case integration passed without relaxing the creator cleanup assertion.

The exact **ef93676** Docker image also passed its installed-code/native pipeline
gate (27 CLI checks, 25 HTTP checks, actual creation/decryption and restart):
`sha256:1098d6b1db1a9c00f1404d6a1f39dce0a377706ab40ba47ed242e6ca6cc710a1`.
Its report is `/tmp/open-node-backup-creator-image.KQNJMTD5/evidence-gate-r1/report.json`.
That image predates the Web routes and is not presented as a Web-feature image.
Production was not upgraded. Following the user's request to prioritize delivery,
passed checks are not repeatedly rerun; the remaining features will share a final
integration/release run.

All tests for Open Node run on the VPS at `185.99.135.224` over SSH. Local work
is limited to editing and static inspection.

GitHub also runs the same repository-level language gates on every push and
pull request through `.github/workflows/ci.yml`: backend Ruff/pytest, Agent
Ruff/pytest/wheel build, frontend Vitest plus both the administrator and public
Probe production bundles, and Probe Worker behavior tests/type checking.
Actions are pinned to immutable revisions and receive only read access to
repository contents. This hosted CI is an independent clean checkout check; it
uses the configured Python interpreter under `sudo` for the Agent tests because
host-policy fixtures exercise real file ownership changes to a service UID.
Dependency installation, linting and wheel building remain unprivileged. The
privilege applies only to GitHub's disposable hosted test runner, not deployment.
Hosted CI does not replace the root-only Docker installer smoke, systemd lifecycle
smokes, protocol-runtime builds, or real forwarding checks on the designated
VPS.

## Remote Test Command

### 一致快照与创建器：开发候选

实现范围及容量限制见[一致快照说明](backup-runtime.md)。这些结果尚不表示网页创建、
下载或恢复完成，也不表示已经发布或升级生产。

写入协调冻结 572 个文件，VPS 完整后端 **4412 项全部通过，零跳过**，另有 75 项
bootstrap 专项。Ruff、编译、收集、专项、全量、namespace、runner 全部为 0。
官方 age 自有 19 项、独立 49 项、真实 TLS 6 项都启用并通过；原 CLI 和加密 CLI
分别为 77 项。根目录 `/tmp/open-node-writer-full-backend-r3.nJm3ryxu`，
82 项 `final-evidence.sha256` 为
`e812293d2aa7641d80291a59c84b243abda2e25a309491c16567415448c9db58`；
原始 JUnit `evidence/pytest.xml` 为
`c9b2ee5e85805ff66f69035e9befd174003b934de0c5b6aec06968cbbde9b0a4`。
root 已逐项核验清单、最终链和原始 JUnit。

前轮全量 `/tmp/open-node-writer-full-backend.w0W6eH1P` 为 4411 通过、1 失败：
旧 bootstrap 测试直接调用 endpoint，绕过 HTTP 中间件，未建立工作上下文。
只给直接调用补上实际租约，原两次数据库操作及“不阻塞事件循环”的断言未削弱。
R2 的 runner=1、SSH 断连及 81 项证据完整保留；后核通过不改写原失败。

新增十个服务/测试文件后，582 文件 R2 的 **410 项联动测试全部通过、零跳过**：
SQLite 83、状态文件 58、依赖检查 216、整体快照 19、创建器 34。
全 app/tests Ruff、编译和隔离运行全部为 0。实际调用官方 age，并以独立官方进程
解密核对生成包；测试确认在交出密文前所有明文连接/文件已关闭、写入已恢复，
以及复制密文 FD 后清理失败也不交出结果。这是内部服务联动，不是 Web 验收。
目录为 `/tmp/open-node-backup-integration-root.UiQGUE78/creation-evidence-r2`。
源输入为 `full-source-r3.tar` 加 `creation-overlay-r2.tar`，后者 SHA-256 为
`e83712f9b8876d85fe4238d6f32d11dc55c25d650e5692d1a322c799be46751e`，
十文件清单为 `f664528a13d1fcd11d5ede5bd9571f2e2aa7b1af9f94f37de09dfdec31e71fa9`。
原始 R2 JUnit 为 `a147596a398e8628157daa5059a64e43c45e3ba851bb8bfe15b91190c5a6cffb`。

原联动 R1 为 402 通过、3 失败，另有三项测试 lint 问题，原记录没有覆盖。
两项测试把 22 字节 age 标头只读了 21 字节，另一项提供互相矛盾的 Git/镜像 revision；
修订均在测试中，产品代码未变。R2 另增五项负控/清理用例。
原始 R1 JUnit 为 `2abce246013910cb4952121f8371afc7d1a266db6bf0af2653d8773d55f6bb12`。

单独的 SQLite 借用连接门位于 `/tmp/open-node-backup-sqlite.aBQ7bQxy/gate-r5`，
83 项全部通过；98 项最终清单为
`a08d7032954203cc9be0cf6107028428bd498d3b20eeed0d2b974cc366c4c329`。
依赖门 `/tmp/open-node-backup-dependencies-r4.ZUrs1Z5Z` 的 216 项全部通过，
817 项清单为 `39324d0f9e08b405cd83eee53d5138a8f2f83c7effcccfdc8f549c1e082c47f9`。
其先前 lint/测试失败以及匿名 `/proc/fd` 重新开 SQLite 的真实失败均保留，
最终采用的是先打开完成目标的只读连接再 unlink。允许的是 SQLite 自身的空 temp
schema，不是额外 ATTACH 的数据库。

另在 `/tmp/open-node-state-creation-audit.Aj5C7lzh/r2` 对状态复制与创建联动的四轮原始
结果完成 108 项独立后核和 14 项封存复读。2434 项跨目录输入清单
`original-cross-directory.sha256` 为
`57abe272c660e02a98b1e55bf5be44199cae3ca97a61a908dcc018ce55aa81ec`，
12 项最终链为 `48505eafea7c428fd80c555dcfc02526128d8e483a2267c4231c601a9f655673`。
root 再次逐项校验了两份清单及原始 R2 JUnit。审计自己的首轮日志解析误判也原样保留。
state 轮次没有单独的历史链接清单或外层 shell 退出文件；实际记录的是 namespace
退出码，不能把它改称未采集的外层退出码。

所有执行只在 VPS 的私有 mount/net/PID 环境进行，源码和 6344 个依赖文件只读，
四个依赖链接不变；运行目录、数据库、证书和缓存独立。生产及共享候选前后身份一致。
包含新增服务的完整后端回归正在独立目录进行，精确 Git 镜像和发布 CI 尚待完成。

### 备份加密：专项、完整回归与精确提交发布

此切片在已发布的 `2a28103` 格式层之上新增官方 age 加密和带私钥的只读验证，
不创建应用快照，也不提供网页备份或恢复。完整工作树 R1 冻结为 564 个文件：
归档 SHA-256 为
`f503f0f0130584380c0dde7dbc21815bdd4b797d9426841b5a0926c323b6e546`，
逐文件清单为
`9501bbd6b7b43faf977ce140f198ff156ef1ff39713632b1504a8b981e6e30db`。
它来自 `9602217` 加候选修改，不能冒充一个精确 Git 提交。本节记录已经完成的
专项、完整后端、真实大包与工作树镜像；后续精确 Git 验收也已完成，见本节末尾。

| 专项 | 实际结果与边界 |
| --- | --- |
| 加密服务 | `/tmp/open-node-backup-encryption.deNYrJ9w/source-r6`：163 项通过，零跳过；144 项边界/受控执行器测试，19 项实际调用固定官方 age。Ruff、编译均通过。包括关闭标准 FD 后的真实加解密、错误密钥与损坏密文拒绝、非主线程运行及只读匿名输出。 |
| 独立恶意输入 | `/tmp/open-node-encryption-adversarial.EL3RQEgF/gate-r2`：316 项替身/输入边界和 49 项原生专项通过，零跳过；Ruff、编译、runner 均为 0。49 项中，40 项调用了真实官方进程，2 项用官方 scrypt 向量验证预检拒绝，7 项验证可执行文件拒绝；不能把 49 项全部称为密码学往返。 |
| 命令行与文件发布 | `/tmp/open-node-backup-encryption-root.SF18OwaR/evidence-cli-r4`：154 项通过，零跳过，Ruff 通过；其中原只读 CLI 77 项、新入口 77 项。新测试只替换加密服务，真实执行参数、权限、FD、短写、磁盘错误和发布逻辑，不作为密码学证据。 |
| 容器 age 获取器 | `/tmp/open-node-age-fetch-v132.3bj2SJWK/source-r3`：158 项离线测试通过，零跳过；Ruff、编译均通过。另通过真实官方 HTTPS 分别取得 amd64/arm64 归档，核对摘要、ELF 和完整许可，只发布 `age` 与 `LICENSE`。这不是 arm64 容器运行验收。 |

服务文件 SHA-256 为
`5f44494b9b68685b5b0c57001094096aebde18e44b08b1b3ea4475d0715a916f`，
命令行文件为
`f6ef2c6e0f026808c4c02771f194fc275c5f6a85a0059ae722f2fa8a1543cacd`。
root 独立核对了服务的 1201 项证据和独立测试的 121 项证据，并直接读取原始 JUnit：
服务 163 项 JUnit 为
`f1ed257a51d9d7940607e82c203abbbf0183321cb55d611be4ff99c75e52bf5e`；
独立原生 49 项 JUnit 为
`f6683968946f105e798bf0d30870b76fb8b0d359d89da43600684dc170a7eda0`；
CLI 154 项 JUnit 为
`f3730bd6065f94e24597fc7931f328debbc567d7275f17f526313116466e9099`。
自有进程和 namespace 已退出，源文件、6344 个依赖文件及四个依赖链接未变。

独立超时测试在真实官方 age 已 exec 后暂停它，保留产品的 30 秒硬期限；实际
用例耗时 30.074 秒，随后真实 kill/reap。取消测试只注入一次取消异常，子进程
终止和回收不使用替身。它不证明取消外层 `asyncio.to_thread` 就会停止工作线程。
默认托管 CI 没有设置原生工具路径时，服务 19 项与独立 49 项会显式跳过；VPS
专项使用 `OPEN_NODE_BACKUP_AGE_TEST_BINARY`，独立组另设置
`OPEN_NODE_BACKUP_AGE_VECTOR_DIRECTORY`，因此本轮没有跳过。

CLI 的失败测试确认：加密失败不覆盖已有目标，源和私钥 FD 会关闭；完整密文
发布后的目录同步或终端输出失败可能返回非零并留下完整新文件。此时不回滚公共
目标名，其他进程随后放入的替换文件也不会被删除。随机私有临时文件的清理是
可信目录内的尽力清理，不承诺对同 UID 恶意进程实现原子的比较后删除。

所有初轮失败保留。独立 R1 的 365 项测试虽通过，但测试文件有两项 Ruff 问题，
且默认 Ruff 缓存向其私有 source 新增四个文件，runner 因而为 1；R2 只修测试格式、
将缓存放到自有运行目录，重新全过。CLI R2 为 149 通过、1 失败：测试把一次快速
等长改写当作必然可观察的 stat 变化；原失败瞬间没有单独采集 stat，不能声称已
记录其确切时序。另做的真实文件控制实验中，256 次快速等长改写有 252 次 stat
九元组不变；显式推进 mtime 的 256 次均被拒绝。R3/R4 仅把测试限定为可观察的
修改，没有将 stat 检查包装成快照锁。输入仍须是已经完成、独占的备份包。

此前还单独运行过官方 age v1.3.2 互操作实验，根目录为
`/tmp/open-node-age-interop.spzVuJIj`。66 次小输入调用与 6 次大包阶段调用验证了
流式认证的边界：某些尾部损坏会先输出部分明文；恰好两块的明文在追加/拼接后
甚至会全部输出，官方进程最后仍失败。因此服务必须等官方进程成功退出并完成
独立 ZIP 校验，才向调用者提供明文。该实验只测试官方工具，不冒充产品封装或
安装后命令行验收。age 的认证也不证明发送者身份、数据库有效性或恢复就绪。

正常 Dockerfile 已构建工作树镜像
`sha256:6c363723a8de092106950da59712c853cd4795c6f710c213b5ad75ce0ef2a83c`，
OCI revision 明确为 `working-tree-9602217-backup-encryption-r1`。
`/tmp/open-node-backup-encryption-image.wG1oyvkm/evidence/report.json`
通过四个阶段：已安装文件比较、64 MiB CLI 与负例、128 MiB 同输入往返、独立新应用。
111 个 Python 文件匹配冻结源；相对精确 `2a28103` 镜像，只有 `backup_cli.py`
变化并新增加密服务，其余 109 个模块和 40 个前端文件逐字节相同。wheel 仅
`METADATA`、`RECORD` 变化；本轮修改的 `backend/README.md` 是元数据构建输入。
固定官方 amd64 age 为 6,977,014 字节、root 所有、0755，完整 2,975 字节 LICENSE
也在镜像中；没有安装 age-keygen 或插件。

root 读取了原始 27 项 CLI/守卫记录与另外 2 项容量记录。正常 console/module
加解密成功，旧纯 ZIP 输出保持 13 字段，新加密输出为 17 字段；错误私钥、截断、
追加和拼接均失败。另一个输入的 age 进程成功解密，但内部 ZIP 非法，产品仍拒绝。
这批检查运行在 UID/GID 10001、无网络、只读根文件系统、零 capability 的容器内，
使用默认 entrypoint。观察器的导入/环境阻断检查有真实负控；未改产品执行、限额
或输出。没有在 CLI 容器创建应用数据库或密钥。

工作树镜像的 453 项最终证据已由 root 逐项核验。清单 SHA-256 为
`cd8fb2cf503772d7c8cdb978b85aa5bd9a96fee79a4bc636dbf745499215f1ce`，
报告为 `f23fe8de4c4d34c97205fa4a40df17b606b4397a8413ebd93a0f30e7b59f484c`，
独立后核为 `febb4e833827ee4035eaafe89821a88029f412e1e127aa21066902ea1e44a7a0`。

40 MiB 内容生成的 ZIP 为 41,943,887 字节，SHA-256 为
`bfe2efc2f52c7d0b73d30aa6cf40ba90c5bf58eeabdae5ef62af089279541af3`。
它在 64 MiB tmpfs 中通过纯 ZIP 校验，但加密时官方 age 退出 `1`，只读观察器在
实际 wait 返回时记录可用空间为零。CLI 返回固定安全错误，未发布新输出，临时
空间和 FD 恢复到基线。没有捕获 age 那次 write 的 errno，因此不把这个结果写成
已追踪到具体 `ENOSPC` 系统调用。另一个 128 MiB 容器对同一输入完成加密与验证，
密文为 41,954,327 字节，明文摘要相同，仍不宣称恢复就绪。默认 Compose 不变。

独立应用容器通过 health、meta、branding 和 HTML SPA 检查；非 HTML 深链接仍为
404。四个自有容器均正常退出 `0`，没有 OOM 或剩余容器/卷；既有比较镜像保留。
原 R1 只因 helper 把 entrypoint 配置误认作绝对路径而失败，尚未调用 CLI；实际
配置是 `open-node-entrypoint`。仅修 helper 后运行 R2，产品和镜像没有修改或 retag。
此前 helper 静态行宽失败也原样保留。后续精确 Git 镜像须另行构建，不能用这个
工作树 revision 代替；独立精确提交验收记录在下文。

完整后端在 `/tmp/open-node-encryption-full-backend.ZgoSHNdy` 使用同一份 564 文件
冻结源，只运行一次完整 pytest：**4048 项通过，零失败、错误和跳过，967.14 秒**。
Ruff、编译、收集和 pytest 均退出 `0`。19 项服务原生、49 项独立原生专项和
6 项真实 TLS 用例全部启用；TLS 地址仅配置在私有 namespace 的 loopback 上。
root 直接解析了全部 4048 个原始 JUnit testcase，核对日志及独立后核。JUnit 为
`7317b447f721e13471c8eb5f65b19a73be80a50b20d80a30d48262324c8d85fa`，
日志为 `248942e7cd620539d0d54360706444453ecbd2c8929886efe004b193032cb0f4`。

原 runner 的汇总结果是 `1`，不能写成全部退出码为零：helper 错把 CLI 的 154 项
都计入 `test_backup_cli`，实际是原模块 77 项、新加密模块 77 项。原 runner、报告
和退出码均保留，没有重跑测试或改产品。新独立后核正确按两组核验，通过且 SHA-256
为 `a3ec2af5c49c45243d45a4663a397bec64e07af7f3b6243d7e2b21ca29cb215a`。
564 个原文件不变，另外产生的是旧证书测试的六个 `.pyc`、私有测试数据库及
`worker.lock` 共八项工件，均单独列出并保留。6344 个依赖文件、四个依赖链接、
五个原生工具/向量输入及原冻结归档未变，测试进程和 namespace 已无持有者。
root 还逐项复核了 4254 项最终工件和 27 项封存链；清单分别为
`f2819dba217c4685ba91b735722eff847e7c1e1d70c28d7a60c9b272e0acd8cb`、
`ccccc804b1767f243367d3f8aa5914ddcb6260f8f4b0bf14c1ac64b1f83d2e99`，
`final-seal.json` 为
`2570c1e1fc64fddf0932c013d6ca36f7a5a3f8ef6c3ab61f53dc351dafbfa44a`。

真实大包验收位于 `/tmp/open-node-encryption-max.LtwXeRuR`，使用此前保留的
**1 GiB 正文、4096 文件、1,075,004,637 字节 ZIP**，不是替身流，也没有修改产品
字节、时间或文件数限制。实际模块 CLI 加密退出 `0`，耗时 24.825 秒；实际
`validate --identity` 退出 `0`，耗时 13.538 秒。密文为 1,075,267,285 字节，SHA-256
为 `c5ad22cf9fac1b1621973a650fdc45b7926f28644c4618d938986cf42964c836`。
两份报告均为 17 字段，解密验证的 `authenticated_decryption` 为真，
`restoration_ready` 仍为假。

另一次独立库解密在 15.398 秒内完成，并直接读回只读、无缓冲的匿名 FileIO：
起始内核/流位置同为 0，中间位置 17，最终位置 1,075,004,637；写入被内核拒绝。
解密后重新计算的完整 ZIP 摘要为
`d8504e8916eab114c499a3e4ef924100916d1e64ff8c7b33e3c4ea8ef5f9dbc3`，
与保留输入完全一致。三阶段 `wait4` 峰值 RSS 分别为 30,832、30,968、31,496 KiB；
这是子进程及其已回收后代的统计，不是 age 堆内存或主机缓存大小。

该门使用私有挂载/网络 namespace 和磁盘暂存目录，不是默认 64 MiB 容器。
采样观察到自有文件最多占用 3,225,612,288 个分配字节；这不是绝对峰值保证。
官方工具、输入和源码只读绑定，主机没有留下 `/usr/local/bin/age` 或传播挂载。
仅在复核 inode、大小、摘要后删除本门新生成的密文，原 1 GiB ZIP 没有改动；
暂存、输出目录、匿名 FD 和自有进程均清空。root 直接读取了原始 CLI 输出、
三份资源记录和清理/主机后核，并验证了 68 项工件及 12 项最终链：清单分别为
`582178c2c002a2a49ceacf7f1a80616a5f4a0dc93f8f54ff0d93e9ddccfa478a`、
`a0d49e6b2f3d34aee9fea175d9c089ba420418f9f8e89ad0c5a583307f560c90`，
最终封存为 `918cb00aab5d2d263b9f54144ee95dd5d7fa5a642baa182bde2b26a956407825`。
初次准备目录 `/tmp/open-node-encryption-max.1dKQIpLZ` 因缺少 GNU time、helper
误要求清单路径带 `./` 而停止，均未运行产品；失败记录保留。

精确 Git 提交 `a29345b7e58417d1089c349f6f9cca878830817e` 已在
`/tmp/open-node-backup-encryption-commit-a29345b.E5BnhrHc` 使用干净 GitHub checkout
正常重建，未将工作树镜像 retag 充数。564 文件归档 SHA-256 为
`d10b780cdfb1c2bd7a2240d64c20c21e899f92c67ea4f0227e92b12dbce4c265`，
逐文件清单为
`cadcd7f0f019a1beb552b4066ac2fd0d507a7d2d7f8c797fdf431f7d88f4eb87`。
相对已验 R1 只有四份不参与 Docker 构建的文档变化；111 个安装后 Python 文件、
全部 wheel 元数据及 40 个前端文件与工作树镜像逐字节一致。

新镜像为
`sha256:40868d23f5961f8731b59c8a41c210485d32469cc89086884a09af371b666d66`，
OCI revision 是完整 `a29345b` SHA。原有三个验收 helper 保持原字节，新 driver
仅调整源码身份、revision 与输入清单断言；27 项实际 CLI/守卫、两项 128 MiB
容量检查及独立新应用全部通过。所有原生调用均读取实际退出状态，不把完成
解密但内部 ZIP 无效、临时空间不足或拒绝覆盖写成成功。UID/GID 10001、只读根、
无网络、零 capability 及默认 entrypoint 保持不变；没有修改 Compose 限额。

root 独立读取完整报告、29 项原始 CLI/容量记录、应用检查和后核，并逐项验证
379 项最终证据。`evidence/final-evidence.sha256` 的 SHA-256 为
`8e2244a40f810502a7253c3e76a45544306c117e3079d0bfe3f7a9b75727ba74`，
报告为 `a447fbe22358e5d7089c8eee9be5ac936df099cd59393099f946d0636a0d85f4`，
后核为 `7ac3531684bbcd43f693d42a73fbd7f3c8396604ca1d9653e77cec83041a6f25`。
五个自有容器全部正常退出，无 OOM，无剩余自有容器、卷或 PID；生产、共享候选、
依赖、旧镜像及旧证据均不变。driver 首轮四个 E501 格式失败也保留，修正后
Ruff/编译通过；原三份 helper 及产品代码没有因此修改。

此精确提交的候选 [CI 33384255225](https://github.com/FengYuchen1314/open-node/actions/runs/33384255225)
四项全部通过后，root 已非强制快进公开 `main` 到 `a29345b`，未升级生产。
随后主线 [CI 33385668793](https://github.com/FengYuchen1314/open-node/actions/runs/33385668793)
的 Backend、Agent、Frontend、Probe Worker 四项也全部通过；它与候选 CI 是独立运行。
这次发布只增加已完成包的命令行加密与只读验证，不提供在线快照、网页创建/下载
或受控恢复；不能据此将应用内备份或全部 MMWX 功能标为完成。

### Backup v1 format — structure checks, not recovery

The `2a28103` [format and CLI contract](backup-format.md) follows the pinned-source
[backup plan](backup-plan.md). It does not implement a consistent online snapshot,
authenticated encryption, a downloadable Web backup, extraction or recovery.
The existing installer-level stopped-volume backup is unchanged. All fixtures
below contain synthetic bytes; a successful report deliberately leaves database,
key, source-authentication, snapshot and restoration checks as `not_checked` and
`restoration_ready: false`.

| Focused gate | Actual result |
| --- | --- |
| Manifest | 358 passed, zero failures/errors/skips, strict Ruff and compile passed in `/tmp/open-node-backup-manifest-6799fe9.OTearUiK`. |
| Root archive checks | 34 archive tests plus the 358 manifest tests passed together in `/tmp/open-node-backup-root-6799.W9sPvzsR/source-r3`; 392 passed, 1.52 s, strict Ruff/compile passed. |
| Independent hostile ZIPs | 237 passed, zero failures/errors/skips, 0.80 s, strict Ruff/compile passed in `/tmp/open-node-backup-adversarial.uFEWVpUg/source-r2`. Includes real stdlib ZIPs and binary mutations, constructor-before-preflight observation with a positive control, short reads, ordinary embedded ZIP signatures and reordered central directories. |
| Staging writer | 79 passed, zero failures/errors/skips, strict Ruff/compile passed in `/tmp/open-node-backup-writer-6799fe9.2KSxJine/source-r2`. Includes real streams, short writes/reads, exact membership, source growth around EOF, immutable declaration shapes, deterministic ZIP metadata and a real independent reader after finalization. |
| Read-only CLI | 77 passed, zero failures/errors/skips, strict Ruff/compile passed in `/tmp/open-node-backup-cli.2LoZqVh4/source-r3`. Real module subprocesses cover human/JSON/help/error output, ASCII streams, ordinary-file restrictions, private anonymous staging, unchanged inputs and no application initialization. |

These focused runs each retained the existing FastAPI/Starlette `httpx`
deprecation warning; no warning filter was added. Initial import-group or
line-length Ruff failures remain alongside the corrected sources. The root's
first namespace preflight inspected shared sysfs and did not start tests; the
corrected runner checks the actual netlink interfaces. An initial evidence
verification used the wrong working directory for relative hash entries; its
failed output is retained, and the corrected root check verified all 32 manifest,
33 final hostile-ZIP and 34 original hostile-ZIP evidence entries. Original JUnit
files were independently parsed to confirm every testcase and absence of failure,
error or skipped elements.

Reader code SHA-256 is
`79ca4d00dc716f5bb75b47e27f10a382ef904069db69574b0cf849239b165a1d`;
manifest code is
`ef737a3d1e062e16eabb3a2e9a4246e41397226d76dc49b45b202c601488d1e1`;
writer code is
`69ba8b64c06dfbb1f99a6c73a3f3ddb97762536516ced5106c9c4fdecb4e92ab`.
The root's R3 JUnit hash is
`ff9979d21cc1455d6408e3dfbeb58a66267991ee85485274faf8f78c00662c8e`;
the independent 237-case JUnit is
`19dc12aceaef0b6517e86442c81a153693da5f6616b38c451f73f5353dc5657e`;
the 79-case writer JUnit is
`04c47a6ae80b5bcd6fa07f8632be82d3f24af8eee0e9b52900d9393269c1a675`.

The root also exercised the real, unmodified resource limits on disk: exactly
**1 GiB of payload across 4096 files**, including 4093 files of 65,537 bytes,
two one-byte key placeholders and an 805,498,881-byte database placeholder.
The initial I/O-check budget could reject this valid layout; the corrected
524,288-check budget passed in **6.663 seconds**, peak RSS **35,792 KiB**, with
the full archive digest independently reread and matched. No byte, file or time
limit was patched for this gate. Evidence is
`/tmp/open-node-backup-root-6799.W9sPvzsR/evidence-r3/large-boundary.json`.

The same retained ordinary file supplied actual `pread` ranges to the writer,
not generated read results. Writing the full 1 GiB and performing the independent
archive validation took **12.152 seconds**, peak RSS **38,928 KiB**. The 4096
source views made 24,575 bounded reads totaling exactly 1,073,741,824 bytes.
The output's independently reread SHA-256 is
`d8504e8916eab114c499a3e4ef924100916d1e64ff8c7b33e3c4ea8ef5f9dbc3`;
source size, inode, modification and change timestamps stayed unchanged. Evidence
is `evidence-writer-boundary-r1/writer-boundary.json` under the same root.

The actual module CLI then validated that retained 1 GiB / 4096-file output,
including its own private-copy stage, in **11.582 seconds**, peak RSS
**29,892 KiB**, exit `0`. Its 13-field JSON matched the independently checked
archive and manifest hashes; stderr was empty. Input metadata and SHA-256,
all 558 unified source files and 6,344 dependency files stayed unchanged.
The import observer recorded no application import attempts or loaded application
modules, and no anonymous regular-file descriptor remained at normal exit.
The root independently verified all 23 evidence hashes and read the native
report, child resource record and external cleanup audit in
`/tmp/open-node-backup-cli-max-r2.bf27ZWBl`; its `evidence.sha256` hash is
`752cc3bbbff3c813bfce74ec90454977129c6b73596a259f35df8b32f057edab`.
The first wrapper could not find `/usr/bin/time` and did not launch the CLI;
that failure remains in `/tmp/open-node-backup-cli-max.sw02yMbE`. R2 measured
the real child with POSIX `wait4`, without installing tools or changing budgets.

These runs used private loopback-only namespaces and the existing backend venv
read-only. Frozen source files and all 6,344 dependency files remained unchanged;
the agents also checked the four dependency symlinks. Neither the production
container nor the shared candidate checkout was upgraded.

The unified full-backend run used the 558-file R4 snapshot copied into
`/tmp/open-node-backup-backend-full.GsvPgZu8/source-r1`. Strict Ruff and compile
passed; the original full pytest execution passed **3285 tests**, zero
failures/errors/skips, **924.67 seconds**, including all 785 new backup cases
and the six opt-in real external-fetch TLS cases. The root independently parsed
the native JUnit and rechecked every frozen file against the R4 manifest. JUnit
SHA-256 is `9e7019a500cc11572c27b5a312f59028211867883aed32d3bcb05f4ba98a55cc`;
the log is `74687a90d035ebf3e8a5f052c12fedb06dda49df71e61df71b8b5b852510a081`.

The original full-tree postcheck exited `1`: the private test tree gained six
certificate `.pyc` files, `data/open-node.db` and `data/certificates/worker.lock`.
None of the 558 frozen files changed or disappeared. This is not reported as an
all-zero runner result, and no generated artifact or original exit record was
deleted. A separate read-only post-audit exited `0`: all original source and
dependency content, four dependency symlinks and the original R4 reference
matched; PID 2887753 and its namespace had no remaining holders. All eight
additions are private root-owned mode `0600`. Their timestamps and source
paths match existing certificate subprocess bytecode generation and default
application/test state creation; no filesystem syscall trace was taken, so an
exact first-writer PID is not claimed. `post-audit-notes.md` retains that
attribution and its limits. The root verified all 71 evidence hashes and all
13 final-chain entries; the manifests are respectively
`c19d9d8abea9800f0c1eed651ea69cce9ea8bbdbca0515be462b763d7b4f29c8` and
`83a4a0bddfaa3c1368fdb87ddee091abdf16189c3f8621bbab107281842cd231`.
This post-audit does not turn the original whole-tree runner failure into a pass.
The installed console and exact-Git package gates were still pending when
the initial candidate commit `2a28103` was created; their later results follow.

The working-tree image was then built with the normal Dockerfile as image
`sha256:76eac71df12a1973ab56e89fa0ed7743cdee0ecdf5c55cf2d1bde50751c81c94`,
with the explicit non-Git label `working-tree-6799fe9-backup-v1-r4`.
`/tmp/open-node-backup-image-r4.S2FuV1DN/evidence/report.json` passed all three
phases: installed source/package comparison, 18 actual CLI/guard cases and a
separate fresh default application. All 110 installed Python files match the
frozen source; the 106 pre-existing modules and 40 frontend files match the old
exact `100d93f` image byte-for-byte. Only package `METADATA`, `RECORD` and
`entry_points.txt` intentionally differ. The root read the native CLI and app
results: guards have negative controls, invalid files fail with fixed errors,
valid synthetic bytes leave database/key/recovery checks unverified, and CLI
calls create no application state. The separate app passes health/meta/branding
and HTML SPA routing, while non-HTML deep links still return `404`.

These containers used UID/GID 10001, no network, a read-only root, dropped
capabilities and private tmpfs. All three stopped normally with exit `0`, no
OOM kill and no remaining owned containers or volumes. R1–R3 remain failed
fixture runs: help wrapping, CPython's ASCII stderr backslash escaping, and a
non-HTML request wrongly expecting the HTML-only SPA fallback. Corrections were
limited to the external test helpers; product code was not changed or retagged.
R1's two idle keepers required daemon termination with exit `137`; later helpers
handle SIGTERM and require normal exits. The exact-Git build is separate.

The clean detached 558-file checkout at
`/tmp/open-node-backup-commit-2a281032.HIzydE1B/source` then produced image
`sha256:642bc4a8ee1069aaa87aed00ff8291fde34c3e38a66acd03c5678ac641cb9d43`
with the full `2a2810325e092b760481e427ea500245d01a3884` OCI revision.
This is a fresh normal Dockerfile build, not a retag. Only two non-build-input
documents differ from frozen R4; all Docker inputs and all installed Python,
frontend and package metadata bytes match the working image. The same frozen
helpers passed all 18 actual CLI/guard cases and the separate fresh app checks.
All three containers stopped with exit `0`, no OOM, and no owned containers,
volumes or processes remained. Source, fixtures, dependencies, production,
the shared checkout and both older comparison images stayed unchanged.

The root independently checked every entry in both final evidence manifests:
184 entries at `open-node-backup-image-r4.S2FuV1DN/evidence/final-evidence.sha256`
hash to `45baf9dbac7134bc2921026eefd5ec5c6675627643e980cb38527b3a5e9bff61`;
224 entries at `open-node-backup-commit-2a281032.HIzydE1B/evidence/final-evidence.sha256`
hash to `49e0ebfd34482616f275107931488f10b32f3dd76b9e030ba465b5f8469ddd20`.
Both paths are under `/tmp`. The exact gate's native `report.json` hashes to
`823daea25a1c0cd013a85e06ea009c02734509e65b28826e93e92dba5e7fa8c5`.
An initial exact-wrapper Ruff line-length failure was corrected only in the
external helper and remains in the evidence; it did not run product checks.

Candidate commit `2a2810325e092b760481e427ea500245d01a3884` also passed all
four hosted [CI jobs](https://github.com/FengYuchen1314/open-node/actions/runs/33377433229):
Backend, Agent, Frontend and Probe Worker. This clean checkout check is separate
from the VPS run with all six opt-in TLS cases enabled.
After the independent exact-image audit, the same commit was fast-forwarded
to `main`; both remote branch refs were verified. Production was not upgraded.

The default Compose `/tmp` is only 64 MiB; the format maximum is not a default
deployment capacity promise. An independent real capacity gate used the same
immutable working image, its default entrypoint and UID 10001, and a synthetic
73,401,117-byte ZIP with 70 MiB of payload. With a 64 MiB tmpfs, the installed
CLI wrote 67,108,864 bytes and received actual kernel `ENOSPC`: exit `1`, empty
stdout and only the fixed Chinese error. With a separate 128 MiB tmpfs, the
same input passed, exit `0`, still `restoration_ready: false`. These were two
real CLI executions, not injected errors. Host `strace` observed `O_TMPFILE`,
the failing `write` and descriptor closure without changing container
capabilities. Both cases restored all temporary space, created no application
database/key state, and left input SHA-256 and complete stat unchanged.

Evidence is `/tmp/open-node-backup-capacity.UPRseCPU`. The root independently
checked the 179-entry `capacity-artifacts.sha256`
(`328125554bd4bb945b214e2649d6d36fd3cef4e066d43ada044479af654b2803`)
and 12-entry `final-chain.sha256`
(`b9f8b1d7167029dd7524cceabe5242dd911c4c323798007351cf26c874cabcf7`),
read both native observations and the independent audit, and checked the raw
syscall evidence. Source, dependencies and the image stayed unchanged; owned
containers and processes were removed. R1's observer-selector failure remains
recorded and did not invoke the CLI. R2 only fixed that external selector.

CLI code SHA-256 is
`a1f520b79853e815211268e451118e009d56bdbfd7a42735473afa68f4586cca`,
test code is
`4809f859a48b6cfa55fcee120ea3a31989debd04bd5503dcd6dbfd660d5ca9dc`,
and the native 77-case JUnit is
`90e7e7f3fd29f61d12abb862207513234489e6d2f8e02dadbf370786908e73db`.
The initial CLI static gate had two test line-length failures; R2 passed 57
cases and failed three assertions about help text that argparse had wrapped.
Those assertions now normalize display whitespace. A separate real edge case
was fixed: an ASCII stderr could raise a second encoding exception while
reporting a failure. The CLI now safely handles unwritable/unencodable error
output and tests real ASCII subprocesses, without changing default Chinese
messages or making JSON include sensitive declarations. All original evidence
remains beside R3.

### Site text settings — published first slice

This is the two-field branding slice in the [operator guide](system-settings.md)
and [pinned-source design](system-settings-plan.md), not complete general-settings
parity. Feature commit `f0ed515d71aa28f097d43f469d6fcc232859af9e`, after
`422c540`, is published on `main`. It is not included in notification feature
`bf8eaa8` and has not upgraded production.

The new integration root is `/tmp/open-node-branding-integration.ChNDrkyo`.
The full frozen R2 source archive contains 544 files, SHA-256
`25d899648c457ec13b158067ffa1564af6c07434422e4b22ca4508c0c297c3ec`.
All 18 frontend-owned files match the passed focused snapshot; the final store
fix and the administrator-route allowlist update below are also included.
The two new browser/Docker smoke scripts and later documentation are recorded
separately, not silently added to this archive.

| Focused gate | Verified result |
| --- | --- |
| Domain/store | **159 passed**, no skips or errors, 8.203 s in `/tmp/open-node-branding-store.YWSSePjT/source-r4`; strict Ruff and compile passed. Covers defaults, raw Unicode before trim, visible characters, strict versions, public projection, concurrent initialization/CAS, atomic pairs, corrupt storage, transaction faults, independent SQLite restoration and unchanged business tables. |
| Frontend | **175 passed in 8 files**, no failures or skips, 218.05 s in `/tmp/open-node-branding-fe.TtW5l0MC/source-r1`; forced typecheck passed. Service 83, provider 9, settings view 23, App 11, account 12, dashboard 30, sign-in 6, router 1. Includes default fallback, canonical responses, literal HTML, drafts, lifecycle/identity changes, late replies and read-only reconciliation after an uncertain save. |

Store R4 hashes: domain
`dcdac33174dae9caf915303d07d9c6e0307cd5d32be41d6dd0622cadab59c8eb`,
store `56fd4f60614046c653b2e67f00767bcaf1c1826cf515a4908513bb7998f6249a`,
tests `a11e35e62decceddd913f463131429d1673344010e37e62e9920f5b9b2cd9cf7`.
Its `final-report.json` SHA-256 is
`601e38b8b9757c0ef8e31fac4e90b7c627a08602cab52811e9cda5425f8ac440`;
`final-evidence.sha256` is
`7f73ed77458745061b88d59a6463950d9c0d57c3175f0747642af86cd3438ba7`.
The 159 baseline backend files and three new files stayed unchanged through the
run; no owned pytest process remained.

Earlier store R1/R2 failed only strict lint. R3 ran 157 tests: 156 passed and one
real pre-DBAPI-commit fault test failed because an uncommitted SQLite write could
remain on a pooled connection after the SQLAlchemy commit hook failed. The R4
fix explicitly rolls back that owned DBAPI connection before returning it to the
pool. Separate tests prove that a failure **after actual commit** preserves the
committed version for GET reconciliation and rejects an old CAS; the code does
not pretend that an already committed write can be rolled back. The original
failed logs remain alongside R4.

Frontend `gate-r1/focused.json` SHA-256 is
`1e02e265eb19d9935a52aa64bfe0feb61c282c5e9d285c7701dde03f8d30bc73`;
`gate-r1/artifacts.sha256` is
`01bee712bcd34bcc0546d24f24e3c048dc9a0ab3f1cf66de72fb1958c094cf53`.
All 190 frontend source files remained unchanged. The private physical dependency
copy and read-only canonical copy matched before/after on 24,021 content files;
only the two generated tsbuildinfo files and one Vitest cache were excluded.
No dependency was installed or changed in another candidate's environment.

The root's first API execution used the earlier `source-r1`: **109 passed and one
failed**, 87.80 s, `evidence/root-r3.log` and XML. The existing exhaustive auth test
did not yet list the deliberately public `GET /api/v1/branding`; that exact GET
was added to its public allowlist. The new administrator routes remain subject
to the exhaustive 401 check. Two preceding preflight attempts did not run pytest:
one inspected shared sysfs rather than actual namespace interfaces; the other
used an incorrect manifest path. Their evidence remains, and neither is a test
pass. R2 checks real netlink interfaces and has only loopback with no default route.

The R2 full backend run ended with **2498 passed and 2 failed**, no skips,
981.30 s. Both failures were the existing subscriber-role exhaustive auth test:
it treated the new public branding GET as an administrator-only route. The R3
test change permits only that exact method/path and also asserts the exact
three-field public projection and fixed `license_required: false` for both
subscriber roles. No application or frontend code changed. The old failed log
and XML remain in `backend-full-r2`.

The complete backend R3 rerun passed **2500 tests**, zero failures/errors/skips,
911.27 s, in `backend-full-r3`, using a new `source-r3`, not an overwritten R2.
Root downloaded the native JUnit XML and log, verified their SHA-256 values,
checked all 2500 testcase elements and found no failure/error/skipped elements.
All 544 source files matched before/after and at the independent postcheck;
the Python dependency inventory stayed unchanged and both recorded runner/pytest
processes exited. One existing Starlette deprecation warning remains in the log.
All 544 files match R2 except
`backend/tests/test_subscriber_auth.py` (SHA-256
`926d2b36ba3687979ba7a074361a42f06bd571cca8ba144277102e96e069fd18`).
The R3 source manifest SHA-256 is
`87554df475ee6984504df2bf607bfb8961ec7059c394b840b1d6cfa3badd139a`.
`full-pytest.log` SHA-256 is
`a22f03cadc0af504fd16eaac040a6617cf90c076fa0fa5d7cd29a34415a7f090`;
`full-pytest.xml` is
`f409a0f5e0b0aa0cb7297a36ecebc92b9b07d43d4bd3681b281dff08204f1233`;
`reports.sha256` is
`5d6c7c93165b748a7cafbeafb6a14480addf2a1156997c07c1a9e4d0072f7ff1`.
Both full backend runs enable all six real external-fetch TLS fixtures using an
address assigned only inside their private loopback namespaces. The runs use
the existing private notification venv read-only; there is no dependency install.

The full frontend R2 run passed **1013 tests in 75 files**, no failures or skips,
976.78 s. Root independently checked the native JSON totals, all file statuses
and every assertion result. `frontend-full-r2/full-vitest.json` SHA-256 is
`7ee93c130884c5b29ef0122513f0448e8034bf4dab1b6bf908d02328df79f6fd`;
the complete log is
`e3b2c032b3980807caa6ac6f6e57957528047c9331ca30c5fa33dbfb7cc977d4`.
Existing Ant Design deprecation and Probe chart-size warnings remain visible in
the log, not suppressed or described as test failures. The 544 source files,
24,021 private/canonical dependency content files, and built assets matched
before/after; generated caches are excluded as described above.

Frontend typecheck and both production builds passed; 40 administrator and
3 Probe assets were frozen at 2026-08-31 07:01:39 UTC. In `frontend-full-r2`,
`production-assets.tar.gz` SHA-256 is
`7e51ec83a0229ee6bf380bced2ec49473a953388c01e7492d08e034a689de886` and
`assets-before.sha256` is
`542350230e6538b7a00dfb890fde8f653b880a5dedbd8b08ad2a2fa4ce0cac8a`.
The 57 Agent/Probe Worker source files match the earlier notification R5 source;
no previous test counts are relabeled as a new run.

A normal Dockerfile build from a separate copy at
`/tmp/open-node-branding-docker-r2.KZ98bXQH/source` succeeded. Image
`open-node:branding-working-tree-r2` has ID
`sha256:e0b7b3ecff038b690ef198d1d811790db09db441439a85e4a50f819134a1e5d6`
and OCI revision **`working-tree-422c540-branding-r2`**, not a Git SHA. Source,
main/Probe assets and production identity/image/start/restart count were unchanged
through the build. The working-tree label is not an exact Git revision.

The working-tree Docker R2 gate passed **10 phases** at
`/tmp/open-node-branding-docker-gate-r2.wvo2pyoW`. It compares all 106 installed
application Python files and all 40 main assets, then exercises real permissions,
concurrent CAS, Unicode/literal text, restart and two stopped whole-volume copies
to empty independent volumes. The original administrator session/CSRF survives
restart and cold restoration; the other 61 business tables remain unchanged,
apart from the explicitly expected session `last_seen_at` refresh. All six
persisted files/entries match in content, mode and UID/GID across both copies.
All three graceful current-generation stops exited 143 in 0.24–0.28 s with
30 seconds allowed and no OOM. Four label-owned containers and three volumes were
cleaned; production, the shared candidate and frozen source/assets were untouched.

The dedicated `scripts/vps/smoke-branding-docker.py` fixture SHA-256 is
`2391e7ff5edaacb1f7e3f3c24f900f5ffce8e4bd100da2241ade9710237d505b`.
Strict Ruff, compile and its 49-check ownership/cleanup self-test passed without
invoking Docker. R1 failed because the fixture's SPA request lacked
`Accept: text/html`; the real SPA correctly refused the fallback. Only the
fixture header and its self-test changed, and R1 evidence remains.
R2 report SHA-256 is
`b1c1d879e7a7bff6fd3de6670bbca02cdca319fafabb5347656c2df5918484e0`;
`final-audit.json` is
`f4d0fd9d899bbb1ce25606b8c90e9580eaa1fe7dca8fbe8060a82514f767d5fa`;
`final-evidence.sha256` is
`13b5ebb19390bbc1f166333cb4e98943ec70b4bd873517b2dc54afd93ecccb7c`.

The real browser R3 gate passed **14 phases** and produced **27 new PNGs** at
`/tmp/open-node-branding-browser.BDV35Iyg/browser-r3`, using the same 544-file
product and all 43 built assets. Root downloaded the passing report/archive,
verified every PNG hash, and individually viewed all 27 originals at 1440, 390
and 320 pixels. Long Chinese/emoji names wrap or ellipsize without hiding
navigation/logout; literal HTML remains text; settings, administrator sign-in,
dashboard, subscriber sign-in/account and public fallback remain usable. The
independent Probe keeps its original title and real WebSocket connection.
Password masks are intentional. The synthetic loopback account subscription
URL in the private desktop screenshot is not a production credential; the
screenshots are test evidence, not published marketing assets.

The browser makes real administrator/subscriber logins and a real process
restart without reseeding users or replacing cookies/CSRF. It proves actual
409 conflict, post-commit lost-receipt GET reconciliation without replaying PUT,
late public/private responses, double-click protection, and real corrupt-row
503 fallback that does not block login. All 13 public branding requests omit
Cookie, Authorization, CSRF and Referer and expose exactly the public fields.
There are **two expected** status-specific browser network diagnostics (409 and
503), zero unexpected console errors, page errors or external requests. All
three service generations stopped under their 30-second grace, with current
generation shutdown logs and zero owned processes/listeners remaining.

The browser fixture SHA-256 is
`4e909540317dc6306b9dc79f7295d738dbca2d53ab45eec74d4e7db00ba3ae45`.
R3 report SHA-256 is
`01153b54c3adfa36029b091662cba2e61ba3f1c167d8b74ae9172096f91c24f2`;
`evidence-r4/screenshots-browser-r3.tar.gz` is
`694ca1a90e17214762af8eb1ac4af27dc533d37fa214df10364f9b0603f6ab02`;
the 143-file `evidence-r4/final-evidence-r3.sha256` is
`9a8de8c56b4c0936ac1b76da4863f68f61c7949f6eb650325372be86159265f8`;
independent `postcheck-final` SHA-256 is
`02bd4894158b3f385c36de8002135487fb25eeaf4a5e8dabe7ad834996a3fb35`.
Root's separate 27-image visual audit is retained at the integration root as
`evidence/root-visual-audit-browser-r3.json`, SHA-256
`c80df3c573b84e6ece4e8b60b8e8821f2503fdb6b5dd47e7ac041b09f0ee7afd`.

Browser R1 failed while reading the first real 200 response body; the cause
remains unknown and did not recur. R2 completed 12 phases but failed by comparing
the dynamic health timestamp as if it were fixed. R3 checks the exact health
schema/status/service and nondecreasing valid timestamp, while comparing the
complete metadata response unchanged. The fixture also waits for the exact
request-finished event rather than the installed Playwright 1.62 `finished()`
helper, whose unfinished auxiliary waiter generated a task warning. No product,
dependency, timeout or production security setting was changed. R1/R2 and the
auxiliary postcheck mistakes remain as failed evidence, not passing runs. An
old network namespace inode was reused by the later root R3 pytest process;
start time, private cwd and owner proved it unrelated, and it was never stopped.

The clean exact-Git Docker gate also passed at
`/root/open-node-branding-commit-f0ed515.u29eElRY/source`, checked out from GitHub
at **`f0ed515d71aa28f097d43f469d6fcc232859af9e`**. All 547 tracked files stayed
clean and unchanged. Relative to R3, there are exactly five changed Markdown
files plus the new operator guide and two smoke scripts; there are no product
or test differences. Relative to R2, only the exact public-GET test correction
is additionally present. All **300 Docker build inputs**, including
`backend/README.md`, are byte-identical to R2. Unlike the notification release,
this slice has no changed wheel-metadata input exception.

The normal Dockerfile build produced `open-node:branding-commit-f0ed515`, image
ID **`sha256:e1e62f68663a7f1e95423c5267cf0cf6bedbeafb2ceec3e59e7fd812cf92fae9`**,
with the complete feature SHA as its OCI revision and UID/GID 10001:10001. It was
not created by retagging the working-tree image. Strict Ruff, compile and the
same frozen fixture's 49-check self-test passed. All **10 full Docker phases**
passed again, including 106 installed Python files, 40 exact main assets, real
CAS, unchanged unrelated data, original session/CSRF, restart and two whole-volume
cold copies. Three current-generation graceful stops exited 143 in
0.176–0.279 s, with no OOM or shortened 30-second grace.

The independent postcheck reverified production/shared-candidate identity and
protected files, all 547 tracked files, 43 main/Probe comparison assets, zero
owner containers/volumes and the absence of all four container/three volume
names. Both old working-tree and new exact-Git images remain available. No
production service, database or image was replaced.

Exact-Git `gate/report.json` SHA-256 is
`92ed14fd521139c4e1674e252dd72c4608a9b229d5716b6cbc6d75c569b8686b`;
`evidence/independent-post-audit.json` is
`61d0f0f796916bb5e5cb8d9978851e23c9340f3dc4f125a464eb175c9c94ea15`;
`final-evidence.sha256` is
`54a398087e4f31b09f3e96f5e23bd7e8ff70bea60f7aed756cb48a1af062400d`.
Both tracked-file manifests have SHA-256
`f6635a94e1c9f3c7e8498ff04e5727a264df296c31deeaa3fb047051312d6f15`;
the 300-input Docker manifest has SHA-256
`bf5a23dd25d9648de8993b1183c370a5d866bede26b5929607249a2fefc20604`.

All four clean-checkout jobs (Backend, Frontend, Agent and Probe Worker) passed
in [run 33368641706](https://github.com/FengYuchen1314/open-node/actions/runs/33368641706),
with its head SHA checked against the complete feature commit. Root then
non-force fast-forwarded `main` from `422c540` to `f0ed515` and independently
verified both remote branch refs. The feature is published; this does not
authorize a production upgrade or establish complete MMWX parity.
The subsequent main-branch [run 33369806430](https://github.com/FengYuchen1314/open-node/actions/runs/33369806430)
also completed all four jobs successfully at the same `f0ed515` SHA.

### Earlier documentation-only CI failure — retained

The candidate-branch run
[33366136872](https://github.com/FengYuchen1314/open-node/actions/runs/33366136872)
at documentation-only `422c540` failed its Frontend job. All 890 test assertions
in 72 files passed, but Vitest reported two unhandled `ReferenceError: window is
not defined` exceptions after the `SubscriptionEditors.test.tsx` environment was
torn down. The main/Probe builds in that job were consequently skipped. It is a
failed run, not a full frontend pass. The separate main-branch run
[33366136941](https://github.com/FengYuchen1314/open-node/actions/runs/33366136941)
at the same SHA completed all four jobs successfully; this does not erase the
candidate failure.

The stack reaches a delayed state update in locked `@rc-component/util` 1.12.0
with Ant Design 6.6.2 and React 19.2.7. Static inspection found that its pending
timeout has no unmount cleanup; Ant Design form error/warning debounces use
0/10 ms delays. The affected test's teardown synchronously unmounts and restores
globals, while its flush helper only advances promise microtasks. The logs name
the test file, not the two originating test cases, so the exact creation sites
remain unknown. Pending-timer cleanup is covered by the separate test-only
follow-up below; no fix is attributed to `f0ed515`, whose affected test,
components, lockfile and CI configuration are unchanged from `422c540`.
The read-only investigation is retained at
`/tmp/open-node-ci-33366136872.9IfmAfUe/diagnosis.md`; its 38-file
`final-evidence.sha256` has SHA-256
`7b7407085462317c80dcc31f776dc778dc86bbf9817ea29b22ad5464612a7823`.
The original failed-job log SHA-256 is
`59b2d9ee02cac04efbdb7dedcc8164a8a28c9efa96347906e6c17f45a9596c97`.

### Subscription editor timer teardown — published test-only follow-up

Commit `100d93f9f990cbef9346d9e1f2f0ed2e972a5117` changes only
`frontend/src/react/components/SubscriptionEditors.test.tsx`, SHA-256
`a67c1e6384552f233c46402ca1fb003b9d10f1170264bc386a9ca4eb07e4bbbd`.
The seven original test bodies, microtask flush helper, timeouts, application
code, dependencies and CI configuration remain unchanged. The fixture now owns
`setTimeout`/`clearTimeout`, unmounts, actually executes pending callbacks inside
React `act` while jsdom still exists, asserts an empty queue, then restores real
timers, mocks and globals. It does not discard the queue or ignore unhandled
exceptions.

The isolated negative control at
`/tmp/open-node-editor-timers-f0.gXfbtmfo` ran all seven original test bodies but
deliberately omitted the drain before its new empty-queue assertion. Each failed
only that assertion, with 15/18/8/4/8/17/16 pending timers; `finally` then executed
them to leave the private environment clean. This is an **expected failing
negative control**, not a product regression or a passing suite. Its native
`evidence/negative-r1.json` SHA-256 is
`511311777d7226631675eca1fef14ac34cccec430a91ffb22c673cd21c8f1da0`.

The corrected fixture passed **7/7 tests in one file**, zero failed or pending
tests, in a separate lo-only namespace. Both strict app/node no-emit typechecks
passed. `evidence/positive-r1.json` SHA-256 is
`7f2920b7935e9e50b35d2f8c15064e83a817369053a44d7b28c1735270709ee4`.
Root read both native reports and verified the single-file diff and frozen hash.
This proves the controlled pending-timer risk and its cleanup; it does not
identify the two original CI callbacks' exact creating cases.

The final focused postcheck verifies all 547 original source files, the one-file
variants, all 25 unchanged assertion call sites, and unchanged 24,024-file/19-link
canonical and private dependency copies. No owned process remains; the original
38-file CI investigation is unchanged. Root independently read the postcheck
and verified all 55 entries in `evidence/final-evidence.sha256`, whose SHA-256 is
`95d436fbfd131306ba15ee31112f3a276fc373bcc56474217889ec68c2980780`.
`evidence/postcheck.json` SHA-256 is
`e2e5c45317dce04cc74d8a285c70f72003c6807017e84235a56f80dff6e2fe36`.

The exact-commit Docker gate at
`/root/open-node-editor-timers-commit-100d93f.b5j3J2No` passed all 10 phases,
with the unchanged fixture's 49 self-checks, compilation and strict Ruff also
passing. The normal Dockerfile build used full `VCS_REF=100d93f9f990cbef9346d9e1f2f0ed2e972a5117`
and produced `open-node:editor-timers-commit-100d93f`, image ID
`sha256:6464d21218abfe3969208d776740d744edb6b1f5a2754a1846e6be54784ae69d`.
It performed a new production build, not a retag. All 547 tracked files remain
clean and unchanged; only the test file differs from `f0ed515`, including among
the 300 Docker inputs. The other 299 inputs, including `backend/README.md`, are
identical. All 106 installed application files and 40 main assets match the
feature baseline; the three Probe assets are an unchanged comparison source,
not a claim that this Dockerfile builds Probe.

Permissions, concurrent CAS, original session/CSRF after restart, two complete
cold copies and all 61 other business tables passed. The only volatile database
exclusion is `operator_sessions.last_seen_at`. Three shutdowns completed in
0.188–0.249 seconds with SIGTERM exit 143 and no OOM. All four owned containers
and three volumes were removed; independent owner checks found no residuals.
Production, the shared candidate and both earlier branding sources, images and
evidence are unchanged. The new image is retained. This is a container gate,
not a new browser-rendering or real Telegram-delivery claim.

Root read the report and independent post-audit, then verified every one of the
53 entries in `final-evidence.sha256`. Report SHA-256 is
`86b018851982e3195796e15a541b4071affe270774b0d988cc8485acf0f98a86`;
`evidence/independent-post-audit.json` SHA-256 is
`54a19bc022c16cd272bd333cc45236e3dff78fa0f4119641379bc7169ed1c3fc`;
the final manifest SHA-256 is
`45999f8152d83c22011e403c48bddb90ed07fd4f28c20421f6a27a49595bb166`.

The independent complete frontend gate used an exact `100d93f` Git archive at
`/tmp/open-node-fe-testonly-f0.hE5fRW3Q/source-r1`, alongside an unchanged `f0ed515`
comparison archive. In one fresh loopback-only namespace, the original full
configuration passed **1013 tests in 75 files**, zero failed/skipped/todo tests
or unhandled error blocks, in 946.51 seconds. This is a new complete run, not a
reuse of the earlier R2 counts. Strict app/node no-emit checks and both normal
main/Probe builds passed. All 40 main and three Probe assets match R2 byte-for-byte;
the combined asset-manifest SHA-256 remains
`542350230e6538b7a00dfb890fde8f653b880a5dedbd8b08ad2a2fa4ce0cac8a`.

All 547 source files, including 190 frontend files, and the 18 frozen branding
files are unchanged by the run. The canonical and private copies' 24,021
dependency-content files and 19 symlinks remain unchanged. The only private
dependency-tree changes are the two expected TypeScript build-info files and
one Vitest result cache, recorded separately; canonical caches are unchanged.
Root independently checked the native report and every assertion status,
the phase exits, the actual source/dependency/asset checksums, and all 88 entries
in `gate-r1/artifacts.sha256`. The original runner PID no longer exists.

`gate-r1/full-vitest.json` SHA-256 is
`dd5e1ab1c682fde96d29fdd6571ef4fc288a93bda720071b981d1297818fa068`;
the full log SHA-256 is
`8079dc66e73816610975015d18c686242d4a2341f1e86fedcd58e8c8cf7c1ae4`;
the 88-entry artifact manifest SHA-256 is
`573991189bea13d5ee5e355be360e2f3fbe9658aca816a9ecf83702729d06f95`.

The separate closure check confirms private root/source/evidence directories,
physical dependencies, all eight exit records at zero, the exited runner and
released network namespace. Root verified all 13 entries in the final chained
manifest `gate-r1/final-chain.sha256`, SHA-256
`8101b4f4c899104767a7e4d88e6c09a9aa42c68fbf82030a6f5f3c33cfa42800`.
`gate-r1/closure-check.log` SHA-256 is
`bfa97a103370a0c261b809249bb3783dd9bf5e2248fb989235236dc7f24fa0f3`.

All four jobs in
[CI run 33370135840](https://github.com/FengYuchen1314/open-node/actions/runs/33370135840)
passed at the complete `100d93f` SHA. After the focused, complete-frontend,
exact-Docker and CI gates, root non-force fast-forwarded `main` from `f0ed515`
to `100d93f` and independently verified both remote refs. The test-only change
is published; it does not change the site-text feature baseline `f0ed515`,
upgrade production, or make the original CI callback creation sites known.

### Administrator Telegram notifications — published first slice

This is the first administrator-only notification slice described in the
[operator guide](notifications.md), not full Telegram Bot or renewal parity.
The feature is published on `main` in
`bf8eaa8e365f302aced10ab2eac9a340d7553d8a`, after the unified-source gates below,
the exact-Git Docker gate and all four clean-checkout CI jobs passed in
[run 33364557514](https://github.com/FengYuchen1314/open-node/actions/runs/33364557514).
The preceding public `caf016c` did not include notification code.
The production container has not been upgraded.
No real Telegram credentials or destination have been used.

The new root integration environment is
`/tmp/open-node-notifications-integration.v2sQeZ5w`. Its private Python virtualenv
was installed from the public baseline plus `tzdata`; other candidates' environments
are not installed into or rewritten. Source archives and failed rounds are retained.

| Component gate | Verified result |
| --- | --- |
| Durable store and domain | 108 passed, no skips, 16.804 s; strict Ruff and compile passed in `/tmp/open-node-notifications-store.CpeUz24C/source-r5`. Store SHA-256 `e36d28f43d299b42499d58c566777883717fb95a75c658ffebf1f840d1b6ed84`; test SHA-256 `e38977aa3c8529962db240d6d353ea36a40f617b5cad7f5b037dad8c4b8afc00`. Includes concurrent claims/CAS, full-expiry/user-incarnation dedupe, DST, quota-independent eligibility, unknown/late receipts, bounded safe retries, and missing/wrong-key refusal to clear ciphertext. |
| Telegram transport | 206 passed, no skips, 9.53 s; Ruff and compile passed in `/tmp/open-node-telegram-transport.w31Bc3jN/r3`. Real loopback HTTP/TLS fixtures run inside a fresh network namespace with no external route. Module SHA-256 `ac7d323cd34bfd8b8a64136df935742b76a20de9abbd111b68094983f4b0d9a6`; test SHA-256 `cd701429e5091266fe97adc7d22a90373c4de38da8e7efaa97f239a181e29bb3`. |
| API and worker integration | Root `source-r2`: 77 passed, 57.43 s, `evidence/root-focused-r2.log` and XML. Covers permissions, strict non-echoing request validation, idempotent request lookup/retry, actual app lifespan, cancellation and recovery. One root test exceeded the lint line-length limit; that formatting-only error was corrected in the next snapshot. Two additional real missing/wrong-key API tests passed in the unified run below, not these 77 results. |
| Unified backend | Root `source-r4`: **2298 passed, no skips**, 1023.21 s; strict Ruff and compile passed. Includes all six opt-in external-fetch TLS tests and the combined notification store, transport, API and worker. `evidence/backend-full-r4.log` SHA-256 `84a69f71a9ff176e688efba739c191c4a51501edcccdf431b853326f35b7d8bb`; XML SHA-256 `6dfae1fe69f67e3bfd199f7063d56fea7b3fec3a43fed95b380339a3dc8d39bf`. One Starlette/httpx deprecation warning is retained. |

Store tests read real inventory records and verify all non-notification tables
remain unchanged. Network tests include verified TLS/SNI, invalid certificates,
proxy/CA/key-log environment isolation, strict bounded HTTP framing, false-success
200, valid/invalid 429, redirection, disconnects and cancellation. They are
controlled fixtures, not a canary proving Telegram acceptance.

The root's first full-suite attempt (`source-r3`) stopped during collection:
the backend-only archive omitted `scripts/vps/sync-and-test.py`, which a backend
test imports. The error and XML are preserved in `evidence/backend-full-r3.*`;
this is not a full-suite pass. The replacement `source-r4` is a complete 530-file
repository snapshot, archive SHA-256
`d0a6148466ee94a57bd42bdb24ba4945f1ca1d0eea431255543c87fe7bd935dc`.
Its strict backend Ruff/compile and complete suite passed in a fresh
loopback-only namespace.
An assigned public-format address exists only inside that namespace, not on a
new host listener. No product SSRF/TLS bypass is enabled.

All 530 source-file hashes and the private Python dependency inventory remained
unchanged through the full run. The later unified `source-r5` contains 531 files,
archive SHA-256
`9e0ea5c8ecd4c637fc0d7a900a3b5fa1e678588becaf32e12a793c7404676916`.
Its backend application, tests and `pyproject.toml` are byte-identical to the
passed R4 source; the two `evidence/backend-executable-r*.sha256` manifests agree.
The R5 frontend type check and both production builds passed. Its complete
Vitest run passed **890 tests in 72 files**, no failures or pending tests,
995.86 s, in `frontend-gate-r5`. The original test process continued after an SSH
transport disconnect; a read-only recovery check found the same PID, growing
log, no host restart and no kernel OOM record. It was not rerun or counted as a
test failure. Native `full-vitest.json` SHA-256 is
`0f771f2a974e41f27b104454a72fe5aae3d0a6ed8d90a203b62b26f0f6104e28`;
log SHA-256 `2b71513bc40ee2ee37d03763f604f6876105fb1ad065c51f848bc1df73663569`.
All 531 source files, 24,024 shared dependency files and the 39 administrator / 3
Probe assets stayed unchanged. Combined asset manifest SHA-256 is
`39491685f0277eec9be2250e208817d4980d29f39ac91eafbcce1107ea8c3f61`.

The production-bundle browser, working-tree Docker and exact-Git Docker gates
below have passed. Clean-commit CI also passed for the full `bf8eaa8` revision.
Earlier Chinese/external gates further below are separate
evidence and must not be relabeled as notification acceptance.

#### Notification production-browser gate

`scripts/vps/smoke-notifications-browser.py` passed in
`/tmp/open-node-notifications-browser-r4.H23Kcoyq`, using an independent copy of
the frozen R5 product and its built assets. The executed fixture SHA-256 is
`7210494597bc55d0102aa8aa80a170ee3789236b422a66092dc1e620a3f1b09a`;
strict Ruff and compile passed. This **browser R4** is not backend `source-r4`.

All nine phases passed: default-off/no-key behavior; token input clearing at
request start; save/preview making no sends; confirmed double-click creating one
durable request; lost POST-response reconciliation through read-only GETs;
clearing configuration cancelling only unsent work while retaining attempt
snapshots; real subscriber login followed by administrator-API 401 and separate
CSRF/Origin 403 checks; an actual 40-second worker lease recovering to unknown
without automatic replay; risk/target confirmation, configuration CAS and old
attempt rejection on manual retry; late acceptance updating only the old attempt;
and a real over-quota expiry candidate using the preview formatter, scheduler and
restart deduplication. The phase count groups related checks, not individual
assertions. Logout and expired-session UI flows were not newly exercised here.

The fixture uses the actual application, store and worker with a trusted local
transport replacement in a fresh loopback-only network namespace. It records
6 transport calls with 6 committed-claim checks, one deliberately failed receipt
commit and one late receipt. Product clock, lease and timeout constants are not
shortened. These are **not** real Telegram deliveries or acceptance evidence.

Console errors and page errors were both zero. All 12 viewport PNGs were checked
individually at 1440, 390 and 320 pixels: default settings, unknown-result warning,
retry confirmation, and preview/history. Text and controls fit; wide tables keep
their own horizontal scroll. Source/assets, both read-only Python environments,
the earlier frozen R4 source and production fingerprints remained unchanged.
The namespace had zero owned processes and zero listeners after cleanup.
The read-only `durable-receipt-audit.json` confirms that all six attempt leases
were 40.0 seconds, recovery occurred after 40.36 seconds, and the late accepted
receipt did not change the newer unknown attempt or its current identity.

Evidence hashes:

- `evidence/report.json`: `0834a7096d9bac0a22141642d68394f70dc196a164b39ed1df43b6b405429ee9`.
- `screenshots.sha256`: `17759b6f9392986b552a084d29e7fa1113e28df3d40a696a02b3377834166f6c`.
- `screenshots.tar.gz`: `4a6d8b14dae70f96cad02b9192c27c96f2a80960f5845b26a8b5baf9ffb47e39`.
- `visual-qa.json`: `3992c220d02ce0400baf04651fdce39226acec58cf413c30e807f00fd0283bf4`.
- `final-evidence.sha256`: `27d0cbfa2d5642aea62a2db0b771b9a6abcd256dc1c31c5dc70e39a2bb48aa62`.

The earlier browser attempts remain failed evidence: R1 selected Ant Design's
hidden accessibility option instead of the visible dropdown; R2 checked the
lost-response fixture before the POST completed; R3 used `/account/profile`
instead of the real `/account/me` identity endpoint. Before R4, a further static
review corrected the fixture's retry expectation to the actual HTTP 200 contract.
Only the smoke script changed between those attempts; product source did not.

#### Notification working-tree Docker gate

The final `scripts/vps/smoke-notifications-docker.py` fixture SHA-256 is
`4610687487d15a7c88209e3ec3cc92411a4893b0ccdb644845f4ab9a3d461a34`.
It passed all 16 phases in
`/tmp/open-node-notifications-docker-r5.dFPBVH4K/docker-r3` with image
`open-node:notifications-working-tree-r5`, ID
`sha256:2a0aa26bcce8bcddc034fa00bcbba9fc9beb6fb013336bca7bf2f8c63eaea796`.
Its OCI revision is **`working-tree-caf016c-notifications-r5`**, not a Git SHA.

It verified UID/GID 10001, read-only root, no capabilities, no-new-privileges,
`--network none`, all 39 administrator assets (1,789,471 bytes), and the
`/notifications` SPA index. Default-off/no-key, encrypted persistence/private
permissions, request idempotency, original-session restart, independent cold
backup/restore, missing/wrong-key refusal across restart, preservation of
ciphertext while disabling, and restoration of the original key all passed.
No real Telegram host was reachable and no delivery was marked accepted.

Six explicit stops completed within 0.30 seconds each. They returned SIGTERM/143;
the fixture requires matching start/finish PID logs from **that current start**,
one completed application shutdown, no OOM/error, and completion within the
30-second grace period. Uvicorn 0.52.4 in the image matches the
[official version's signal re-raise behavior](https://github.com/Kludex/uvicorn/blob/0.52.4/uvicorn/server.py).
Seventeen independent positive/negative checks cover this stop criterion.
The initial R1 failure from incompatible Docker local-log options and R2 failure
from an exit-zero-only fixture remain preserved; neither was relabeled a pass.

The seven label-owned containers and three disposable volumes were removed;
an independent second check found no leftovers. All 531 R5 source files, 39
assets and 424 protected production/shared-candidate files remained unchanged.
The production container was not restarted or upgraded. Evidence hashes:

- `report.json`: `5f737c803554c5f94356efbcd31d785c53d758a3095602c1a50d7700c44c3598`.
- `independent-postcheck.json`: `7dac7f0a97740ff96227a29c322b332e94204643762c2a0b7669809a3af929e8`.
- `executed-source.tar`: `0baf9494cff019f64572c4017783243a7299f61cb05bc5252556e3edc4871447`.
- `final-evidence.sha256`: `206c49cf1349355abfe3dc6eb9bed418659d62903bfbe205d85d5542fff4a11b`.

#### Notification clean exact-commit publication gate

A fresh GitHub clone at
`/root/open-node-notifications-commit-bf8eaa8.JFudzeQC/source` was checked out
detached at `bf8eaa8e365f302aced10ab2eac9a340d7553d8a`. All 531 tracked files
remained clean and hash-identical throughout the gate. Against the earlier R5
snapshot, 522 files were identical; two smoke fixtures and seven documentation
files differed. The application, tests and other build inputs matched R5, but
`backend/README.md` also feeds wheel metadata. The initial all-build-inputs-equal
comparison correctly failed and is retained as `source-comparison.json`.
The subsequent reviewed allowlist records that specific documentation change;
it does **not** claim that all build inputs were identical.

The normal Dockerfile rebuilt the frontend and backend wheel from this clean
commit, without retagging the working-tree image or reusing its wheel:

- Image tag: `open-node:notifications-commit-bf8eaa8e365f302aced10ab2eac9a340d7553d8a`.
- Image ID: `sha256:fc0feccc66b9a2ea5877dcfb99e7e37fcdef70cd129ca4825064521c316eee5f`.
- OCI revision: the full `bf8eaa8` commit above.
- Build: 2026-08-31 06:37:07–06:37:36 UTC.
- Fixture: unchanged `4610687487d15a7c88209e3ec3cc92411a4893b0ccdb644845f4ab9a3d461a34`.

All 16 Docker phases passed with exit zero at 06:40:08–06:41:25 UTC. The 39
administrator assets (1,789,471 bytes) matched the previously verified R5 assets.
The same permission, encryption, session continuity, independent cold recovery,
missing/wrong-key and original-key restoration checks described above passed.
Each of the six SIGTERM/143 stops had matching current-start shutdown evidence,
took 0.187–0.259 seconds and had no OOM. No delivery was accepted and no real
Telegram endpoint was reachable.

Seven containers and three volumes bearing owner
`5019e8fb87e4b72498188d18dbce1112` were cleaned up; the independent postcheck
found zero owned leftovers. The frozen R5 source and all 42 main/Probe assets,
424 protected production/shared-candidate files, and production container
identity, image, start time and restart count remained unchanged. Evidence paths
below are relative to `/root/open-node-notifications-commit-bf8eaa8.JFudzeQC`:

- `docker-r1/report.json`: `b9c69297478e0d2324b8413ad2b441175a51c86aaf4f2eeb3db54196af3d89d6`.
- `evidence/independent-postcheck-r1.json`: `aacaf64bbbe059324e8c2d12211aceac61e2d429f56602adca46a53e1dcf1cd3`.
- `evidence/exact-commit-source.tar`: `2756de0f753d77339fb0fee8f38e0aaf14678b55ce410aa761ec8a8b76648cf3`.
- `evidence/final-evidence.sha256` (63 evidence files): `0a72ad3f24f8e351cb5a23849a846772ae1596cadcd0044fea58ef1c58515f21`.

After the exact-commit Docker and Backend, Agent, Frontend and Probe Worker CI
jobs all succeeded, a non-forced fast-forward published `bf8eaa8` to `main`.
Subsequent documentation-only commits do not change this functional baseline.
New system-settings work is a separate, unverified slice, not part of these gates.

### External subscriptions

This gate concerns the new administrator-managed, explicitly confirmed
Clash/Mihomo YAML source workflow. It is separate from the already published
React rewrite and does not establish full subscription-ecosystem parity.
The **English-language baseline**, before the subsequent Chinese UI requirement,
is frozen in
`/tmp/open-node-external-integration.YG95YRYU/source` on the VPS, based on
`0ffc07215244abcf69fb8e6935171082e0522747` plus the external-source changes.
The full-suite results below belong to that frozen source. Chinese UI work is
tested in new private directories; the English snapshot is not overwritten.
Neither these working-source checks nor the working-tree Docker image establish
a published Git revision or acceptance of the Chinese interface.

Focused checks passed: the parser's **402 tests** and Ruff, the fetcher's
**230 tests** including all six actual TLS cases, and the store/API integration
suite's **48 tests**. The final TLS rerun uses a new private network namespace
with only loopback; it does not bind the VPS public interface. Evidence:
`parser-source-options-after.log` in `/tmp/open-node-external-parser.6U8AFhSs`,
and `external-fetch-tls-r7.log` / `external-store-r6.log` in the integration
directory. The 48-test focused run predates the parser's final default-field
refinement; the complete backend run below covers the combined final source.

The combined English baseline completed these independent gates:

| Gate | Result and evidence |
| --- | --- |
| Backend | 1933 collected; **1927 passed, 6 skipped**, 853.21 s, `backend-full-r7.log`. All six opt-in real-TLS cases separately passed within the 230-test fetcher run in `external-fetch-tls-r7.log`. One Starlette/httpx warning retained. |
| Agent | **605 passed**, 10.92 s, `agent-full-r7-shortpath.log`. An earlier deep temporary path exceeded Linux's AF_UNIX limit; the unchanged suite passed with a fresh short private basetemp. Both raw logs remain. |
| Frontend | **570 tests / 65 files passed**, 883.85 s, `frontend-gate.E8vXRaoF/vitest.json`; SHA-256 `42bf4632e1977c67cc5275dc2e9cba93120b29a8f2212c76fb77cc584847e687`. Type checking and both production bundles passed. |
| Probe Worker | **5 passed** and type checking passed, `worker-r7.log`. |
| Lint/package | Backend and Agent Ruff passed, `backend-agent-ruff-r7.log`; the Agent wheel built privately without replacing any published release asset. |

Paths in that table are relative to `/tmp/open-node-external-integration.YG95YRYU`.
The backend/Agent/Worker product-and-test manifest remained identical across the
runs (208 files; SHA-256
`f4af434404e6ef030624b0f1585041fbd63cbf9041a9cabf95aef6d451e869ea`).
The frontend's 170-file source/configuration manifest also remained identical;
the 38 administrator and 3 public-Probe artifacts matched the browser bundle.
Ant Design row-key, Probe SVG-height and bundle-size warnings are retained in
the logs, not suppressed or counted as failed assertions.

The English Docker gate in `/root/open-node-external-docker-r7.b457dZfx` passed
using `open-node:external-working-tree-r7`, image ID
`sha256:09f49a038d68c93462aceea862199455608b326a504498caa26d194630486b47`.
Its OCI revision is **`working-tree-0ffc072-external-r7`**, not a Git commit.
The final `docker-evidence-r3/report.json` SHA-256 is
`c59673ed57db55d420bccf70247427abea7cd65a146f098b4c78745b11ecab79`.
It checked UID/GID 10001, a read-only/capability-dropped container, all 38 assets,
10 SPA routes, three viewport sizes, original sessions/data across restarts,
encrypted external-source persistence in the existing private volume, a real
verified-HTTPS child fetch with safe rejection of non-YAML input, missing raw
secrets in logs/argv, and unchanged production identity. Only its own labeled
temporary container and volume were removed. Earlier fixture-only header/error
shape mistakes and their cleanup reports remain separate from this final pass.

The production-bundle browser and native-client gate passed on that combined
source in `/tmp/open-node-external-browser-r3.XaVySQeW`. Its `evidence/report.json`
records every backend application file and frontend asset SHA-256; the report
SHA-256 is `925896e5ff2f07daa5bd9f4dd61cbc506b5ea2e397dacf712db6c6fb406aae84`.
The final parser fingerprint in this source is
`681e8769f2b7751411deba917bb942c4e0c6d267a2b55b42508483ed1f66d341`.

`scripts/vps/smoke-external-subscriptions.py` enters a fresh Linux network
namespace and verifies its identity and loopback-only netlink state before
opening any listener or assigning an address. Its local HTTPS provider uses
the normal public-IP/TLS fetch path, with a fixture-only trusted CA; no product
private-address or insecure-TLS bypass is enabled. It exercises:

- Real browser source creation, write-only editing, explicit fetch, selecting
  new nodes, acknowledging existing-node changes, confirmation and receipt
  recovery. Fifteen masked screenshots cover source/node forms, preview,
  confirmation footers and saved details at 1440/390/320 pixels.
- Complete primary-subscription loading in Mihomo v1.19.30 and real managed and
  external VLESS traffic through official Xray v26.3.27. The destination rejects
  direct traffic; both the original and rotated upstream credentials work
  through the selected proxy. Other parser-supported protocols are not claimed
  as traffic-tested by this fixture.
- Credential rotation, new/missing nodes, owner isolation, read-only preview,
  identical confirmation retry, real TLS/gzip, HTML/empty/redirect/gzip-bomb
  rejection and preservation of the active snapshot.
- Unchanged managed catalog/credentials/ledger, encrypted database contents,
  missing-key fail-closed without replacement, cold database plus key-directory
  restoration and the original administrator session.

The fixed native binary digests are checked before execution:

```text
Mihomo 1.19.30  8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a
Xray 26.3.27   8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed
```

Reproduce only on the isolated VPS, with the backend's browser dependencies and
Chromium installed in a private test environment. Build the administrator
frontend first, then use the verified native binaries and a new evidence path:

```bash
PYTHONPATH="$PWD/backend/app" /path/to/private/venv/bin/python \
  scripts/vps/smoke-external-subscriptions.py \
  --mihomo /path/to/verified/mihomo --xray /path/to/verified/xray \
  --output /absolute/new/private-evidence
```

Temporary runtime processes, TLS keys, databases and the namespace are removed
when the fixture exits; only the masked screenshots and source-bound report
remain. Error handling emits the failure stage/type and source locations, never
raw Playwright errors that could contain a password or provider URL. None of
these tests upgrades the production container or proves public HTTPS,
PostgreSQL concurrency, a customer provider's reachability, or off-site backup.

### Simplified Chinese interface

The later user requirement makes Simplified Chinese the default for the
administrator console, subscriber portal, public Probe, document language/title
and Ant Design's built-in component text. The implementation uses the official
`antd/locale/zh_CN` provider and explicit display labels, not DOM replacement.
API paths, enum values, protocol/configuration names, commands, raw diagnostics
and operator-provided content are unchanged. New Probe settings use Chinese
defaults; existing customized titles/descriptions are not rewritten.

The first unified Chinese product snapshot is frozen at
`/tmp/open-node-zh-release.fp33Igbt/source-r2`. The source archive
`source-zh-final-r2.tar` SHA-256 is
`e105396bbd5e215fa26f05478c2b1d760d1d9d911707f1b8a60aafbe12f10ff2`.
Subsequent browser-fixture selector/format corrections have separate manifests;
they do not rewrite that archive. The later certificate-message correction,
R4 source, exact-commit clean image and completed publication are recorded below.
The English baseline above is earlier evidence, not relabeled as Chinese acceptance.

| Unified working-source gate | Result and evidence |
| --- | --- |
| Frontend | **762 tests / 70 files passed**, 819.43 s; no failed/skipped tests. `frontend-full-r2.json` SHA-256 `fe312372e1802185f446f67f68bb716f4fb0295fd1376cd65a6194eb33f8cab6`. Includes all 36 external-panel tests and the final Chinese conflict/legacy-import warnings. |
| Frontend builds | Forced TypeScript project check and both main/Probe Vite builds passed: `typecheck-r2.log`, `build-main-r2.log`, `build-probe-r2.log`. |
| Backend | 1933 collected, **1927 passed / 6 opt-in skipped** in `/tmp/open-node-zh-integration.3ISDjgiA/backend-full-zh-r1.log`, SHA-256 `eeef2d6a2fcbcfdf44d0b56536f4d38099169da9962d7d278ce7ba059b657129`. The progress log and node-ID cache agree; `backend-verified-source-r2-match.log` proves the frozen R2 backend is identical. Ruff passed. |
| Real TLS fetcher | **230 passed, no skips**, 4.49 s, `external-fetch-tls-r1.log`; all six opt-in TLS cases ran in a new verified loopback-only network namespace, without a product SSRF or TLS bypass. |
| Agent | **605 passed**, 10.07 s, `agent-full-zh-r2.log`, plus Ruff. A fresh short private basetemp avoids the known AF_UNIX path limit. Agent source and published release assets are unchanged. |
| Probe Worker | **5 passed**, 180.996 ms, and type checking passed: `worker-tests-r2.log`, `worker-typecheck-r2.log`; private source with read-only dependency reuse. |

Unless otherwise qualified, paths in this section are relative to
`/tmp/open-node-zh-release.fp33Igbt`. The frontend source manifests before and
after the full run both hash to
`788b7cba49d20e9a6ff8b7929429ef6185d0a30dbcf2a1aead2472e1419e7d98`.
Backend, Agent and Worker before/after manifests also match. The final **41
assets** (38 main, 3 Probe) are in `frontend-assets-r2.tar.gz`, SHA-256
`2188112fa06f80cb12692e95ac71aa60c4826bafca5c10f190019a577016fe55`;
`frontend-assets-r2.sha256` hashes to
`480e182eeed4c37070c0275490639626a56ab61046640956ac990408d819f662`.
The following R2 real-browser gates use these exact assets, checked before/after:

| Chinese production-bundle workflow | Passing evidence and scope |
| --- | --- |
| External subscriptions | `external-browser-r2/report.json`, SHA-256 `6f69dc2171d1b9c9c2ae16021749fe22c3e6abd21b367e99d37bda344edb1c24`; 15 masked 1440/390/320 screenshots, preview/confirm/receipt, credential rotation, managed plus external VLESS forwarding, ownership and key/DB restoration. All boundary checks described in the English gate were rerun in a fresh namespace. |
| Operator UI | `operator-browser-r2.log` and `operator-browser-r2/`; 16 screenshots. Server creation, Nginx paths/sites, tunnel fields, certificate import/EAB forms/downloads, Probe tasks/tokens, Agent fingerprint, password change and expired-session handling. This is not a real certificate-issuance or tunnel-deployment gate. |
| Administrator MFA | `/tmp/open-node-zh-admin-mfa-gate.nUeYE2Ru/gate.log`, SHA-256 `171be7c1ea13432fafb5c707d6711a5d7930f4f214bb9e5a726e2acb8a2e5707`; enrollment, acknowledgment, challenge, mandatory policy, recovery, regeneration, disable and local CLI recovery; eight masked screenshots. |
| Subscriber account | `/tmp/open-node-zh-account-websocket.2j9JXUsQ/gate.log`, SHA-256 `ad2c9fa7806a729eab631b304df5ef25e101ff520a9edd0437bde5c2be7db41f`; 55.98 s, 12 masked screenshots. Real billing/forwarding, MFA/replay/recovery, sessions, password/link reset, administrator recovery and isolation. This Chinese rerun uses WebSocket only; prior English HTTP coverage stays separate. |
| Legacy import | `/tmp/open-node-zh-legacy-gate.NEfsSiok/gate.log`, SHA-256 `34908bbced1aa3498a8cd8bb4e1e3be3d7d2030c3f0cb0a004c8d545d5e7cd24`; four screenshots, mapping/confirmation safeguards, visible administrator-to-subscriber warning, legacy links, real stock-Xray forwarding, TOTP/recovery and foreign-key integrity. The role value itself is asserted by backend tests. |
| Bootstrap tickets | `/tmp/open-node-zh-bootstrap-browser.UsEJ81C4/evidence/report.json`, SHA-256 `61b1661e9dc0290f9e327318f26a31baa586f7db1cb4d5c4a9c712bcf870f3af`; 12 screenshots, replacement/reissue invalidation, revocation and synthetic registration UI. This rerun does not claim natural ticket expiry or a newly installed systemd Agent. |
| Anonymous Probe Worker | `/tmp/open-node-zh-worker-r2.ZrdktcNX/evidence/report.json`, SHA-256 `9668465c9950edd4e9bce8716300bd22c2ed6c1770be3e2e82b7474ac06b0c3d`; nine screenshots, HTTP/WS aliases, no credentials/cookies, private/mutation rejection, malformed frames/reconnect, idle fallback polling, ranges/themes/deep links. Actual Miniflare on the VPS, not a Cloudflare account deployment. |

The Chinese source-built Docker gate passed in
`/root/open-node-zh-docker.3r5SMaqB` with image
`open-node:zh-external-working-tree-re105396b`, ID
`sha256:3d08d1fe00f156d56b94bf451ddf1a8c6d62a563db714f1fef0e9c733d33d702`.
Its OCI revision is **`working-tree-0ffc072-zh-external-r2`**, not an exact Git
commit. The `evidence-r1/report.json` SHA-256 is
`99edc7ca6f83b91bff60a3a3b01f98b7ef4e9de35bdd99142036c2cd9b69d256`.
Fresh `npm ci` and the image build reproduced all 38 main assets byte for byte.
The gate checked UID/GID 10001, read-only root/capability drop, ten SPA routes
and reserved 404s, three viewports, original sessions and rows across restart,
encrypted-source key permissions/persistence, verified outbound HTTPS with safe
non-YAML rejection, and no raw secrets in logs/argv. Its labeled temporary
container and volume were removed after ownership checks; production did not
change.

The first Chinese certificate-administration browser gate stopped on a nested
Ant Design selector. Its corrected R2 fixture passed real certificate operations,
but visual inspection found a successful already-revoked receipt translated as
failure. The R2 `visual-review.json` in
`/tmp/open-node-zh-certificate-admin-r2.KG0Xt0St` explicitly records semantic
failure; it is not a final Chinese pass. The first full subscription-client gate
also stopped at Clash VLESS-gRPC TCP before reaching templates. Failures remain
at `/tmp/open-node-zh-certificate-admin.eJ5qvqzH` and
`/tmp/open-node-zh-clients.z2DJK0Gr`. The original gRPC failure's cause is unknown;
later success must not be relabeled as a proven protocol fix or environment cause.

Earlier Chinese focused runs (including the interrupted 62-test subscription
run) and the intermediate R1 bundle remain historical, not extra tests added to
762. Production retains its original image, data volume and service identity;
none of these fixtures verifies public HTTPS or external account deployment.

#### R4 certificate messages and final-source reruns

The current product snapshot is `/tmp/open-node-zh-release.fp33Igbt/source-r4`.
Its `source-zh-final-r4.tar` SHA-256 is
`5c8d6008d20c692710e9e4718b935e87a3558c2172f400c5dbb6d9ccf6fdec04`.
Relative to R2, only the Chinese message dictionary and two frontend test files
change: 18 precise translations cover the remaining fixed certificate-worker
outcomes; all 22 fixed/bounded values are tested, including success, skipped,
queued, unknown and failure. No substring match accepts arbitrary provider text.
The focused **187 tests passed**, adding **34** tests (27 service and 7 React).
Evidence: `/tmp/open-node-zh-cert-receipt.VsZWRAxp/focused.json`, SHA-256
`edfb3d19db3ada8b510a710efaca2c52a48a5f20ceceb60e97a4c5ace0cf562c`.

Forced type checking and both R4 production builds passed. Initial type checking
with a cross-directory dependency symlink raised TS2742 in the unchanged
`renderUi` helper; using an identical private physical dependency copy passed,
without changing application or helper code. Both logs remain. The frontend
source manifest hashes to
`612b5ad954e65cd5496f81d6b6d1c4572c0d22a935a31dc3d5aa43920d33d075`.
The final 41-asset manifest `frontend-assets-r4.sha256` hashes to
`9ba29231b866707dfe9afa4205bd1f2090f0e37cb761e360f3f135ef126ab6cd`;
`frontend-assets-r4.tar.gz` hashes to
`f9b1a53884fdae6a884f74b2377cc0ef82fbcad3470dcb04125e566cd4baa4f7`.
`backend-verified-source-r4-match.log` proves the complete previously tested
backend is identical. Agent and Worker code are unchanged.

The combined R4 frontend run passed **796 tests in 70 files**, 852.45 s, with
zero failed or skipped tests. `frontend-full-r4.json` SHA-256 is
`80fa75de132b3a20cf8053f9640ec8b9cc9fa46af9f1cb9e36ae7bd3146f8968`.
Source and all 41 assets matched their manifests after the run. These 796 tests
include the 34 added regressions; focused counts are not added a second time.

The following R4 gates have also completed:

- External subscriptions: `external-browser-r4/report.json` under the release
  directory, SHA-256
  `f310ce18ff5ec70abf2033eab35daed35dd7936536e3d7a7300b81ba8a5a97b6`.
  Repeats the full real HTTPS/Mihomo/stock-Xray, secret/ownership, failure
  preservation, rotation and cold-restore gate with 15 new masked screenshots.
- Certificate administration: `/tmp/open-node-zh-certificate-final-r4.Iygak2KU`,
  49.17 s; real HTTP-01/EAB, account update, renewal, version revocation,
  unknown-result retry/reconciliation and backend restart recovery passed.
  All 15 screenshots passed layout and Chinese semantic review, including a
  same-row success-state/precise already-revoked receipt assertion. `report.json`
  SHA-256 `c98aebc6231c0a727237a0f05d7657b1ecbc789d01ca8678e502f36f7777e8f8`;
  `visual-review.json` SHA-256
  `afeb9cc1c07bae3021581466d9ad9c5740d4a7b88e365cbb00fcf8022d3efbd3`.
  This gate does not cover remote certificate deployment or validation-host
  selection in the creation form. Namespace, temporary processes and data were
  fully cleaned, with no host DNS or production changes.
- Full subscription clients and templates:
  `/tmp/open-node-zh-clients-r4.zcFROMyb`, 58.417 s, exit 0. All 154 labeled
  TCP/UDP checks passed across default/custom Clash, sing-box, selected Xray,
  URI list and Base64; the unselected full Xray export also forwarded.
  Compatibility reports, node URLs, stale-response isolation, template
  permissions/CAS and identity stability passed, with nine masked screenshots.
  `gate.log` SHA-256
  `f53e7f507859c37f2cc08f6a20c891ee13aaa17126abafb030a80c7cf2a8a4ad`.
  New diagnostic metadata preserves the original curl command, timeouts and
  exact response-body equality; no failed probe occurred in this run. The first
  gRPC failure was not reproduced. All 299 product files, 49 Python fixtures,
  41 assets and six pinned native inputs remained unchanged, and the owned
  Agent root/unit/user were removed. A second independent full run at
  `/tmp/open-node-zh-clients-r4.drS6Jzhj` also passed, 55.561 s, with identical
  source, assets, native inputs, assertions and timeouts and no failed TCP probe.
  Its `gate.log` SHA-256 is
  `232be0e3fde2e483ea7893b1ef75b25f282c8f11b15206fb5e0da27ca6875a2a`.
  Both complete repetitions retain the original failed run and unknown cause.
- Operator UI: `operator-browser-r4.log` and `operator-browser-r4/` under the
  release directory; the entire R2 operator scope was rerun with 16 new
  screenshots, unchanged source/assets and exit 0.
- Anonymous Probe Worker: `/tmp/open-node-zh-worker-r4.L4wUF4T5/evidence/report.json`,
  SHA-256 `56c4bc924389a54d4f6996ae7406db0edf8d1dea9959ee1ff7fdf391f02ef854`;
  same full local Miniflare HTTP/WS/polling/reconnect/security/range/theme/deep-link
  scope, exit 0. Source, fixtures, assets, dependencies and production remained
  unchanged; all nine screenshots passed visual review. `visual-qa.json` SHA-256
  `b09bb90656940d6047e1ecf38d7fd33e5d5aff3f5603bffa0b691a6e0306dc7d`.
  This is still not a Cloudflare account deployment.

All 28 changed Python browser/native fixtures pass strict E/F/I/UP/B Ruff and
byte compilation in R4; backend and Agent Ruff also pass. The changed-fixture
manifest SHA-256 is
`01c8c37c7f9ecb8fcadbe74201a12866e8d7ea254f6c035f4019d47b90175352`.

R3 was only an intermediate source archive; it was not built or accepted as a
release. R2 browser evidence for unaffected workflows remains explicitly R2,
not a claim those screenshots came from the R4 bundle.

#### Exact published revision

Feature commit `998839ba06429d47de2e12b5562b4a4c4cad6a62` was published on public
`main` after its independent clean-checkout
[CI run 33359846368](https://github.com/FengYuchen1314/open-node/actions/runs/33359846368)
completed successfully on 2026-08-31. All four jobs passed: backend **1927 passed,
6 opt-in skipped**, 703.33 s; Agent **605 passed**, 10.23 s; frontend **796 tests /
70 files passed** plus main/Probe builds; Worker **5 passed** plus typecheck.
The six real TLS cases passed separately in the isolated VPS gate above; they
were not executed by this hosted-CI run. Later documentation-only commits do
not change the tested feature revision.

A fresh public clone at `/root/open-node-zh-commit-998839b.c3ycWOn7/source`
was clean at that exact SHA. Every non-documentation tracked file matched the
verified R4 snapshot; the manifest SHA-256 is
`4052a9519ac6bf9971e7bb3d4138695877d35dfe8776a81d04a94d6e4365311a`.
The source-built image
`open-node:zh-external-commit-998839ba06429d47de2e12b5562b4a4c4cad6a62`
has ID `sha256:77b0d0faed6aa4f3e2195eebb44be8c506c6a62bc624363c3ab4cb2f2eba8b04`
and an OCI revision label equal to the full Git SHA, not a working-tree label.

The image passed the same non-root/read-only, static route/404, three-viewport,
restart/session/data, encrypted-source key and real HTTPS-fetch boundaries as
the R2 Docker gate. All 38 packaged frontend files matched the final R4 assets
byte for byte. The exact-clone source and standalone assets remained unchanged.
`evidence-r1/report.json` SHA-256 is
`5ce7fb209a29a7ba1e57dee8674b8fd6c1793841fab5c63386ad0f610d1bfd23`.
The owned temporary container and volume were removed after identity checks;
production's source, image, instance, start time and restart count did not change.

### React and standard Ant Design migration (2026-08-31)

These results belong to the new React frontend, not the 268-test Vue baseline
below. The rewrite keeps the FastAPI API, session/CSRF contracts, Agent protocol
and single-image Docker deployment. It removes Vue, Vuetify, Pinia and their
compiler/router dependencies. Architecture and build instructions are in
[frontend.md](frontend.md).

The published feature commit is
`50897f928226c9fef2ab7d0f68de0c3aad46156a`. Its clean-checkout
[GitHub run 33330624705](https://github.com/FengYuchen1314/open-node/actions/runs/33330624705)
passed all four jobs: backend **1,253 tests** (618.73 s), Agent **605 tests**
(10.67 s), frontend **509 tests in 63 files** (535.20 s) and Probe Worker
**5 tests** (164.53 ms). Backend/Agent Ruff, the Agent wheel, frontend type
checking and both production bundles, and Worker type checking also passed.
The only backend warning was the known Starlette/httpx deprecation. Subsequent
documentation-only commits do not change this tested product source; this run
must not be attributed to a different commit.

The final frozen working-source run passed **509 tests in 63 files**
(638.05 s), with no unhandled errors, in
`/tmp/open-node-react-release.xaSu8WDc/frontend-tests.json`. It includes the
existing domain/API tests and real Ant Design DOM tests for the migrated views.
An earlier consolidation passed the same 509 tests in 644.30 s at
`/tmp/open-node-react-accessible.OTOVWliF/frontend-tests.json`. The intervening
Plan/Limiter checks passed 36 tests in three files; the user limit editor's
seven tests also passed after its gutter correction. These are overlapping
reruns, not additional tests to add to 509.

Final working-source builds are at
`/tmp/open-node-react-release.xaSu8WDc/source`. Both the administrator and
independent public Probe builds pass TypeScript and Vite. The frozen
`browser-assets.tar.gz` SHA-256 is
`da85b9cc62b5d78dfae10dbb2f85d3d4ff79e935514f894c67761eabfc64fb4c`.
Relative to the earlier consolidation, the only product-source changes are layout
corrections in `PlanManagementDialog`, `UserLimitEditor`, `LimiterPanel` and
`ProbeAdministrationPanel`; no backend or shared styles changed.

Production-bundle browser evidence is kept in separate immutable fixtures:

| Workflows | Passing evidence |
| --- | --- |
| Administrator shell, server creation, Nginx paths, tunnel forms, certificate import/download, Probe settings/tasks/tokens, password change and session expiry | Final source's parent directory: `operator.log`, `operator-proof/`; includes 1440/390/320 Probe-title and header-button geometry |
| Administrator MFA enrollment, recovery-code acknowledgment, challenge, policy, regeneration, disable and CLI recovery | Same parent: `administrator-mfa.log`, `administrator-mfa-proof/`; private material masked in screenshots |
| Subscriber portal, MFA/recovery, password/link reset, device revocation, user isolation and live forwarding | `/tmp/open-node-react-account-r5.ErZXGOSk/evidence/{websocket,http}.log`; both full transports pass on the final bundle, 24 private screenshots |
| Panel Agent bootstrap, server edit/delete and traffic | `/root/open-node-react-bootstrap-browser.gEUopOkd/react-dashboard-r2-evidence.json`; 24 screenshots, real systemd/runtime traffic and owned-resource cleanup |
| Certificates and dependent change sets | `/tmp/open-node-react-control-browser.FPqNskNQ/r2/evidence/`; real ACME/EAB/revocation/recovery plus ordered apply/rollback and compensation |
| Native limiter | Same root's `r3/evidence/limiter.log`; 18-protocol traffic, speed/connection enforcement, automatic-rule expiry, restart, revision conflicts and 1440/390/320 controls |
| Plans, node aliases, automatic speed rules and user limits | `/tmp/open-node-react-catalog.WDzjZMFf/evidence/r4-{plan-management-websocket,plan-node-aliases-websocket,plan-speed-rules-websocket,user-limits-websocket}` |
| Node management and legacy MMWX import | Same catalog root: `r4-node-management-http` and `r4-legacy-mmwx-stock`; node WebSocket evidence is also retained |
| Subscription access, clients/templates, links and user management | Same catalog root: `r2-subscription-access-websocket-v2`, `r2-subscription-clients`, `r2-subscription-links-websocket`, `r2-user-management-websocket` |

All ten catalog scripts completed their full gates. The client gate did not use
the templates-only shortcut: it ran real Mihomo 1.19.30, sing-box 1.13.19 and the
project's custom Xray. The final legacy-import gate uses official Xray 26.3.27
for standard VLESS; it does not test an Agent transport. Surge format/template
checks are not proof of a running Surge client.

The independent public Probe gate is at
`/tmp/open-node-react-browser.NyIq0V6p/public-probe-theme/report.json`. It uses
real Wrangler/Miniflare/workerd, HTTP and WebSocket, idle-stream polling,
disconnect/retry/reconnect, light/dark/system themes and credential stripping.
The final build's public JS and CSS are byte-identical to that tested bundle:
JS SHA-256 `3a1fc930fd7603da5b8a313aac9c5359dbe3915a6dcb5d8016d93cf103b26eb6`,
CSS `9fd60fb31ba60054d1203f3a99a81dbb50ca9d748e34a9cd293c9b721fda4db1`.
This is a local Cloudflare-runtime test on the VPS, not a deployment to a
customer's Cloudflare account.

The frozen working-source Docker gate passed against
`open-node:react-working-tree-r5`, image
`sha256:bc17d752fab8644a9ba7fbbf69077e9387c24e5d5840c01926ede7868f5dd3c1`,
running as UID/GID 10001 with the Compose read-only/capability restrictions.
All 39 served files match the frozen assets and image. Ten SPA deep links,
five reserved-path 404 cases and a non-HTML navigation 404 check pass; three
original browser sessions plus a server record survive restart. Three viewport
sizes and all eight administrator
lazy routes pass. The private helper also passes eight ownership/cleanup safety
controls. Its report is
`/root/open-node-react-bootstrap-browser.gEUopOkd/r5-docker-4/report.json`,
SHA-256 `201773b8df09bf2dcce869155f44edcedb3e834969fb0ebbc0516352a6b1f26c`.
The fixture follows Docker's newly allocated loopback port after restart while
preserving the original cookie values; it does not treat a stale test URL as a
product failure. The labeled container and volume were removed after verification.

The final source-provenance gate then cloned the exact GitHub `50897f9` commit
into `/root/open-node-react-commit-50897.0MDZIwd3/source`. A fresh `npm ci` and
both builds produced the same **39 administrator + 3 public Probe files** as
the frozen working-source bundle, with byte comparisons and sorted SHA-256
manifests agreeing and no extra or missing files. The checkout remained clean;
tracked Vue files and Vue dependencies in both the lockfile and installed tree
were zero.

The image was rebuilt from a pure Git archive, without local `node_modules` or
generated assets in its build context. Archive SHA-256:
`0d1e3b0886d3c03897a34b665d5e9f6b6a7acdadd0857978e8bb5c2a40da078b`.
The private test tag is `open-node:react-working-tree-r50897`, image ID
`sha256:e0dabde00261b3c4178a62dc367325a47b8bdf3736df0ed700ac99c157708d65`;
its OCI revision is the full `50897f9` commit, not a working-tree label. The
unchanged Docker helper repeated the full asset, route, three-viewport login,
original-session/data restart and eight ownership-safety gates successfully.
Its container and volume were removed; production remained unchanged. Report:
`/root/open-node-react-commit-50897.0MDZIwd3/source-proof.json`, SHA-256
`ff6cc9c18e7507f7311493ee94776b9489d7c1aea4357654fa48c8a7a4004a04`.
The report proves application-file identity, not bit-identical whole images.
The backend application Git tree remains
`31760f22ffae9c562b3b4a9949744b6b976163bf`, identical to `a677280`.

Real browser failures drove the retained regression checks: invalid numeric
drafts must not be clamped into valid quotas/ports on blur or Enter; loading
icons must not change action names; uploads must not retain private File lists;
and narrow-screen dialogs, grid gutters and headers must remain usable.
Test-only corrections scope queries to their actual form/dialog, wait for
closing popup animations and compare raw traffic with raw traffic separately
from two-way charged usage. They do not remove business assertions or enlarge
test timeouts. Ant Design Form's short presentation timers are drained before
the sign-in test's jsdom window is disposed, without suppressing errors.

The production container, image, start time, restart count and Git checkout
remain unchanged; the shared candidate remains clean at `6ca84e2`. No public
DNS/TLS, reverse-proxy subpath, customer Worker deployment or off-site backup
claim follows from these isolated checks.

### Committed Agent bootstrap and hosted-runner fixture fix (2026-08-31)

The feature commit is `1515a7bd56a2dbf257d861fe8760038a9329bae4`; its
host-fixture correction is `a677280ece64a71d7ee4e8c4f0720cd819bcf584`. Both are
published on `main`. The following historical results precede the React/Ant
Design rewrite; the frontend counts here describe the former Vue baseline.

- Clean-checkout [GitHub run 33325869097](https://github.com/FengYuchen1314/open-node/actions/runs/33325869097)
  passed all four jobs at `a677280`: backend **1,253 passed** (640.48 s), Agent
  **605 passed** (10.31 s), frontend **268 tests in 37 files** and both production
  bundles, and Probe Worker **5 tests** plus type checking. The backend emitted
  only the known Starlette/httpx warning.
- The earlier `1515a7b` hosted run failed during the installer fixture's parent
  ownership/mode precheck because it depended on the runner's real `/opt`.
  The correction makes that fixture use its owned temporary install base and
  explicit private directory modes. The actual installer still defaults to
  `/opt`, with unchanged rejection of unsafe parents; regression cases reject
  both `0775` and `0777` before creating a job directory.
- An independent VPS reproduction runs all **124 host-installer tests** as the
  existing unprivileged `nobody` account with `umask 0002`. It passed, along
  with Ruff, in `/tmp/open-node-ci-owner.wVwaXkVt`. No production paths or
  service accounts were adopted by those fixtures.
- The exact clean `a677280` checkout at
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/source` passed backend Ruff and
  **323 focused tests** (76.60 s): 75 API, 98 store, 124 installer and 26
  authentication cases. Log: the parent directory's `backend-focused.log`.
- The same checkout's real-systemd smoke reran the actual installer bytes,
  SHA-256 `00e18bc0c4c55a461b1b811c4e4faa636f558590325e3a2e26827e15cb468913`,
  against the unchanged public Agent 0.3.0a0 assets and official Xray v26.3.27.
  Forced WebSocket and HTTP both reached non-root Agent/runtime readiness and
  forwarded **1,223,915** and **1,102,535** downlink bytes. Wrong-nonce replay,
  redemption after registration and repeated installation were refused; no
  secret leaks or cleanup errors were reported. Evidence:
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/real-bootstrap/report.json`.

The feature-only `1515a7b` verification also checked all 142 tracked backend
files (2,201,738 bytes) against Git and the complete 1,250-test VPS source tree.
Its exact frontend/Worker/browser rerun is retained under
`/tmp/open-node-bootstrap-exact-ui-1515a7b`; 53 Compose preflight checks and
two isolation negative controls are at
`/root/open-node-bootstrap-committed-check.OmEteOtb/results/report.json`.
These counts are not added to the later CI or focused totals.

Production remained on container `c2594ea5b436950a92e310f320b072bfe5bbeda15b178672b4d14008e6e841aa`,
image `open-node:cb1eb0c`, start time `2026-08-29T12:59:02.442246035Z`, with
zero restarts. The shared candidate remained clean at `6ca84e2`. This is a
source publication, not a production upgrade or proof of public DNS/TLS,
reverse-proxy subpaths, or automatic transport fallback.

### Panel-issued Agent installation (2026-08-31)

The feature was tested in private VPS source snapshots based on `6ca84e2` with
the bootstrap changes overlaid. The shared candidate stayed clean at `6ca84e2`;
production stayed on `open-node:cb1eb0c`. These working-source checks are distinct
from clean-checkout CI after the feature is committed.

- Complete backend gate: **1,250 passed** (694.29 s), with only the known
  Starlette/httpx deprecation warning, in
  `/tmp/open-node-bootstrap-feature.3peB48sZ/backend-suite.log`. The SSH output
  session disconnected near the end; the VPS process continued and wrote the
  complete successful pytest summary. This is the retained VPS result, not an
  inferred success from that disconnected shell.
- Focused backend gate: **320 passed** (76.06 s), comprising 98 ticket-store,
  75 API/release-helper, 121 host-installer and 26 authentication tests. This
  includes concurrent claims, hash-only tickets, nonce/expiry/replay bounds,
  post-claim reissue refusal, Origin/CSRF, bounded JSON, persistent rate limiting,
  HTTPS/path validation, release-source/hash validation, owned paths and secret
  redaction. Backend Ruff passed.
- Frontend: **268 tests in 37 files** (7.65 s), administrator and probe-only
  production builds passed. The added cases cover late request invalidation,
  close/target change/disposal, replacement/revoke/claim, existing heartbeat,
  registration, failures and no persistent command storage.
- Probe Worker: **5 behavior tests** and TypeScript checks passed, including
  GET/POST/DELETE rejection of all Agent-bootstrap endpoints without contacting
  the origin or asset fallback, plus write rejection of public Probe routes.
- Production browser gate passed against disposable FastAPI/SQLite: disabled
  configuration, explicit mobile issuance/copy, close/reopen clearing, command
  replacement, revocation, claim vs. registration, existing-heartbeat refusal,
  installer checksum binding, cache/privacy headers and no page errors. The
  final registration and heartbeat are explicit API fixtures, **not** proof of
  installation. Desktop/mobile screenshots mask commands and manual tokens.
- Separate real-systemd bootstrap passed over forced **WebSocket and HTTP**
  using the published Agent 0.3.0a0 artifacts and official Xray v26.3.27. The
  gate exercises the actual panel-generated HTTPS command, claim, non-root
  process/runtime readiness, a subsequently configured VLESS inbound and live
  HTTP forwarding. Control-plane downlink counters observed **1,223,915 bytes**
  and **1,092,420 bytes**, respectively. Wrong-nonce replay returned 401;
  registration blocked redemption (401) and ticket reissue (409). Re-running
  the command was refused with PID/config/unit unchanged; logs leaked no Agent
  token and both owned fixtures were cleaned.
- Root installer compatibility: **53 preflight checks** passed for legacy and
  new Compose files, exact runtime environment matching, inherited-shell
  isolation and safe URL values. A separate real Docker fixture passed fresh
  install, same-source enable, identical-value no-op and same-source disable,
  keeping the administrator/inventory and two immutable stopped-volume backups.
  The non-root image could read the bundled installer/manifest, and HTTP
  resources plus configured state matched in all three deployment states.
  Owned fixture cleanup completed and the production snapshot was unchanged.
- Additional fixture safety controls passed after review: 15 injected `GIT_*`
  variables could not change an external sentinel repository's files, index,
  configuration, refs or objects, including through the imported Git helper.
  A mocked pre-existing namespace caused **zero cleanup calls**. These controls
  created no Docker resources and are reproducible with
  `--safety-negative-controls`.

The real bootstrap gate's only command deviation is appending its restricted
`--test-directory` option. Its root-URL loopback HTTPS control plane uses a
private trusted CA; GitHub downloads still use Debian system trust. It does not
prove public DNS/TLS, reverse-proxy subpath operation or an actual Auto-to-HTTP
fallback event. Those must not be inferred from running each transport
separately. The backend and browser fixture never use the production database.

From a reviewed isolated feature checkout, with the backend/browser dependencies
and Chromium installed:

```bash
npm --prefix frontend run build
backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap-browser.py \
  --output /tmp/open-node-bootstrap-browser-reviewed-revision
sudo backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap.py \
  --output /tmp/open-node-bootstrap-real-reviewed-revision
sudo python3 scripts/vps/smoke-installer-bootstrap-setting.py \
  --safety-negative-controls --guarded-update \
  --output /tmp/open-node-bootstrap-setting-reviewed-revision
```

The latter two require a disposable Debian 12 amd64 VPS with root, systemd,
Docker/Compose and the documented host tools. They create and remove only
explicitly owned test resources. Failed cleanup retains private recovery inputs;
inspect the reported exact fixture before doing anything else.

VPS evidence locations:

- `/tmp/mmwx-agent-bootstrap-store.lE9RnE` — frozen backend overlay and focused gate.
- `/tmp/open-node-bootstrap-ui.a8xVJ6VG/frontend-final.log` — final frontend gate.
- `/tmp/open-node-bootstrap-browser-20260831-final/report.json` — browser workflow.
- `/tmp/open-node-bootstrap-real-gate-20260831a/report.json` — real double-transport bootstrap.
- `/tmp/open-node-bootstrap-setting-guarded-20260831b/report.json` — root installer
  matrix, Docker setting transitions, image resource/API checks and cleanup.
- `/tmp/open-node-bootstrap-setting-safety-20260831a/report.json` — Git environment
  isolation and refused-namespace cleanup negative controls.

The Agent release itself is independently pinned to committed source
`6ca84e21202950bf5ee4754a8ae20e28dbde42ed`, not the newer control-plane overlay.
Its exact four assets passed pre-upload service/lifecycle gates, fresh anonymous
download/tag/BUILD/wheel/tar verification and default-GitHub-source WebSocket/HTTP
upgrade, VLESS forwarding and rollback (104.95 s). See
[the release record](releases/agent-0.3.0a0.md). The previous wheel in the upgrade
gate is a synthetic fixture; this is not a claim of a tested 0.2-to-0.3 migration.

### Administrator MFA acceptance (2026-08-30/31)

All commands below target the isolated `/opt/open-node/mmwx-parity-candidate`
checkout, never the production service or database.

- At `45515b6`, the complete backend suite passed: **955 tests** (621.28 s),
  with one known Starlette/httpx deprecation warning. This supersedes the earlier
  948-test result at `ee16ed3`.
- At `58b33af`, the expanded authentication suite passed: **26 tests** (23.68 s),
  including a persisted cross-IP/cross-challenge verification budget, two-store
  contention at the final allowed attempt, key-loss recovery and
  local/password-change invalidation. The concurrent-budget regression was
  added after the complete 955-test run; the two counts are separate evidence.
- The subsequent clean-checkout GitHub backend job at `58b33af` passed
  **956 tests** (561.06 s), including that added regression. This is separate
  hosted-CI evidence, not a claim that the earlier VPS run contained 956 tests.
- At `fb1aaaf`, frontend **239 tests**, main production build and probe-only
  production build passed. The remaining build messages are chunk-size warnings.
- The real production-frontend browser smoke passed at both `fb1aaaf` and
  `45515b6`: enrollment, recovery-code
  acknowledgement, mandatory policy, password-only challenge denial, recovery
  login, code replacement, policy removal, disablement and local reset followed
  by mandatory enrollment. Desktop (1440 px) and mobile (390 px) screenshots
  were inspected; authenticator secrets and QR codes are masked in artifacts.
  The script also checks horizontal overflow and absence of secrets from browser
  storage, and disposes its private SQLite database and loopback process.
- The independent Agent suite passed **605 tests** and Ruff on the VPS;
  the Probe Worker passed **3 behavior tests** and TypeScript checks. At
  `58b33af`, all four GitHub clean-checkout jobs passed. The Agent job now
  exercises real ownership changes with its installed
  interpreter under `sudo`, instead of failing on the hosted runner's missing
  `chown` privilege.

Run after building the frontend on the VPS:

```bash
cd /opt/open-node/mmwx-parity-candidate
backend/.venv/bin/python -m pip install -e './backend[browser]'
backend/.venv/bin/python -m playwright install chromium
backend/.venv/bin/python scripts/vps/smoke-administrator-mfa.py \
  --output /tmp/open-node-admin-mfa-reviewed-revision
```

The smoke creates random fixture credentials and an encryption key; it does not
read deployment secrets. Its CLI reset is performed only against the disposable
database. It is not evidence of public HTTPS deployment, multi-administrator
support, or automatic recovery backups. See [administrator security](administrator-security.md).

### Public Probe Worker acceptance (2026-08-31)

With the candidate's dependencies and Playwright Chromium installed, run on the
isolated VPS checkout:

```bash
npm --prefix frontend ci
npm --prefix frontend run build:probe
npm --prefix probe-worker ci
backend/.venv/bin/python scripts/vps/smoke-public-probe-worker.py \
  --output /tmp/open-node-public-probe-worker-reviewed-revision
```

The gate uses `wrangler deploy --dry-run` to compile the actual Worker, then
executes it in Cloudflare's Miniflare/workerd with the real production
`frontend/dist-probe` assets. It retains the repository's compatibility settings,
SPA fallback and `run_worker_first` policy. It neither deploys nor logs into a
Cloudflare account. All listeners are ephemeral loopback ports; the upstream is
a disposable fixture, not the production control plane or its database.

Passed using Wrangler **4.127.0**, Miniflare **5.20260826.0-alpha** and workerd
**1.20260826.1**, as locked by `probe-worker/package-lock.json`:

- Production HTML, JS and CSS byte/hash checks, nine HTTP aliases and three real
  WebSocket aliases; token replacement, bidirectional credential stripping,
  security headers, private-route 404, write-method 405 and no followed redirects.
- Anonymous Chromium requests use only the public API surface. Complete headers,
  HTTP bodies, WebSocket frames, DOM and browser storage are checked for leaked
  Worker credentials; cookies remain absent and there are no page errors.
- Both status and target polling continue while an established WebSocket sends
  no frames. Malformed frames do not break subsequent live updates. Polling
  continues through a forced disconnect and rejected reconnect, then automatic
  reconnect applies a new live snapshot.
- Target ranges, ping/system series, public-only deep links and 1440/390px
  layouts passed. Desktop/mobile screenshots were visually checked. The report
  records runtime versions, asset/bundle hashes and observed requests, without
  retaining the generated secret. Private runtime files and processes are removed.

The earlier `wrangler dev --local` attempts failed because its development
ProxyWorker exited on a connection error. The official SDK tracker describes
the same fatal error handling in [issue 15317](https://github.com/cloudflare/workers-sdk/issues/15317)
and a related five-second connection-reuse race in
[issue 14641](https://github.com/cloudflare/workers-sdk/issues/14641).
The gate uses the [official dry-run bundle](https://developers.cloudflare.com/workers/wrangler/bundling/)
with direct Miniflare instead; it does not patch dependencies, change application
polling intervals or mock the Worker's fetch implementation to bypass the failure.
The adapter targets the locked Miniflare v5 API and must be reviewed when updating
those dependencies. Build/runtime failures retain a stage report and redacted logs.

This closes the local anonymous Worker/browser gate, not a real Cloudflare
deployment, custom-domain/TLS setup, production-origin connectivity or all visual
themes from the reference probe. Those remain distinct operational/parity checks.

### Repository-wide runner

From Windows PowerShell in the repository root:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The script pushes the named local branch, records its exact commit and uses
the default SSH key for `root@185.99.135.224`. The VPS needs Python 3.11+ and Git
before the first call. It clones into a missing/empty target or fast-forwards
an existing clean checkout with the matching origin and branch. Local edits,
untracked files, divergence, symlinked paths, incoming ignored-file conflicts,
and a remote branch that moved after the push stop the update. Nothing is
reset or recursively removed. Uncommitted local Windows edits are not tested.

The default target is `/opt/open-node`; `-RemoteDir` can select a direct,
non-hidden child. Use a separate checkout for tests when the default directory
serves a live process. This helper does not stop services or back up databases;
follow [deployment.md](deployment.md) for production upgrades. The script then
bootstraps the Debian test host (unless `-SkipBootstrap` is set) and runs:

1. Python venv and Node.js bootstrap;
2. backend dependency installation;
3. backend pytest suite;
4. independent-agent dependency installation, Ruff, pytest, and wheel build;
5. frontend dependency installation;
6. frontend Vitest suite;
7. frontend production build;
8. probe Worker dependency installation and TypeScript checks.

The checkout safety tests use disposable local Git repositories on the VPS.
For the actual PowerShell-to-SSH path, run:

```bash
python3 scripts/vps/smoke-sync-and-test.py --pwsh /path/to/pwsh
```

This root-only fixture starts its own loopback `sshd`, generates temporary
client/host keys, and uses strict host-key checking. It verifies quoted branch
and repository names, the exact tested revision, bootstrap selection, and
preservation of a dirty checkout. It uses fixture bootstrap/test commands to
check the launch contract, not as a substitute for the application suites.
It does not change the existing SSH daemon, authorized keys or live checkout;
its temporary direct-child checkout and SSH files are removed on exit.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```

The runner removes stale local Agent wheels before building. In the same shell,
resolve the single artifact once and reuse it in the smoke commands below:

```bash
mapfile -t AGENT_WHEELS < <(
  find "$PWD/agent/dist" -maxdepth 1 -type f -name 'open_node_agent-*.whl' -print | sort
)
if (( ${#AGENT_WHEELS[@]} != 1 )); then
  printf 'expected exactly one built Agent wheel, found %s\n' "${#AGENT_WHEELS[@]}" >&2
  exit 1
fi
AGENT_WHEEL="${AGENT_WHEELS[0]}"
```

## Legacy MMWX Identity Smoke

With the frontend built and an Xray binary available on the VPS:

```bash
PYTHONPATH=backend/app backend/.venv/bin/python \
  scripts/vps/smoke-legacy-mmwx.py \
  --xray /absolute/path/to/xray \
  --output /tmp/open-node-legacy-mmwx-screenshots
```

The isolated fixture creates an active-main-shaped MMWX SQLite database, runs the
mode-0600 exporter, uploads the result through the preview/confirmation dialog
and explicit package mapping, then verifies secret clearing. It checks imported
multi-file assignments, administrator profile editing, subscriber profile selection,
bcrypt-to-Argon2id upgrade, original TOTP, one-use legacy recovery and source-admin
demotion. Long/generated/custom keys and direct file, file+user and package+user
`/x` links all render the same valid profile; one `/x` result forwards real VLESS
traffic. Screenshots and overflow checks cover 1440px, 390px and 320px. See
[legacy-mmwx-identities.md](legacy-mmwx-identities.md) for raw/template/rule limits.

## Subscriber Limit Smoke

On the designated VPS, with the frontend built and the independent Agent wheel
and free native-limiter Xray binary available:

```bash
python scripts/vps/smoke-user-limits.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-user-limits-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated root/systemd fixture installs a
non-root Agent and verifies real speed/connection caps, explicit unlimited,
inheritance, Agent restart persistence, offline quota withdrawal, unchanged
credentials and charged usage, and unrelated-user forwarding. Browser checks
cover stale forms, numeric validation, user overrides and subscriber visibility
at 1440px, 390px and 320px widths. See [user-limits.md](user-limits.md).

## Custom Subscription Link Smoke

Use the same VPS prerequisites, built frontend, Agent wheel and free Xray
binary as the subscriber-limit fixture:

```bash
python scripts/vps/smoke-subscription-links.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-link-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Operator/subscriber browser edits, password
and second-factor proof, stale/colliding values, clearing, custom-URL downloads
and complete link reset are checked against real forwarding and an unchanged
runtime PID. The same run creates a [temporary subscription link](temporary-subscriptions.md)
through the administrator UI, copies it, consumes its access limit with Xray and
URI-list downloads, proves real forwarding, checks exhaustion and revokes it.
Screenshots and overflow checks cover 1440px, 390px and 320px. The temporary
Agent installation is removed after the run. See
[subscription-links.md](subscription-links.md) for permanent link identity and
security rules.

## Plan Alias Smoke

With the same VPS prerequisites and built frontend:

```bash
python scripts/vps/smoke-plan-node-aliases.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-alias-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated fixture checks browser creation,
alias editing, stale revisions, saved enable/disable state, clearing, all five
export formats and a subscriber's downloaded Xray configuration forwarding
real traffic. Credentials, subscription keys, the unrelated plan and runtime
PID remain unchanged. It captures 1440/390/320px views and removes its temporary
Agent installation. See [plan-management.md](plan-management.md) for semantics.

## Plan Speed Rules Smoke

Use the current Agent wheel, built frontend and a free Xray binary reporting
`user_auto_speed_rules: 1` on the VPS:

```bash
python scripts/vps/smoke-plan-speed-rules.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-rule-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Real clients exercise sustained and burst
activation, measured throttling, expiry, an unrelated plan, hot refresh and
restart persistence. Browser coverage includes creation, ordered edits,
invalid values, continuous typing, clearing and preservation from Config >
Limits. Exports, credentials and subscription keys remain unchanged.
Screenshots cover 1440/390/320px. The fixture removes its non-root Agent.

## Subscription Client Smoke

Build the frontend and [patched runtime](fork-runtime.md) on the VPS. Use the
backend development environment with Playwright Chromium, the current Agent
wheel, Nginx, Mihomo v1.19.30 (digest below) and official sing-box v1.13.19.
The `sing-box-1.13.19-linux-amd64.tar.gz` SHA-256 is
`ef88a9e577d474210867bd708933d042e9b70106529df2656182c9db90106aa1`.

```bash
python scripts/vps/smoke-subscription-clients.py \
  --xray /tmp/open-node-runtime-build/xray \
  --mihomo /absolute/path/to/mihomo \
  --sing-box /absolute/path/to/sing-box \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-screenshots
```

Pass `--templates-only` to retain the full 18-variant fixture while running only
the custom-template API, Mihomo forwarding and administrator/subscriber browser
workflow. Surge output is validated from the real endpoint but not imported
into the proprietary Apple client on this Linux host.

This disposable root/systemd fixture installs a non-root Agent and provisions
18 inbound variants. It validates complete native exports, switches selectors,
tests each selected Xray node, and feeds unchanged URI/Base64 payloads to the
pinned Mihomo parser. Every compatible entry must forward TCP and UDP. Mieru is
covered over both TCP and UDP underlays only after a fresh Agent scan reports
strict integer `mieru_udp_target: 1`; the fixture checks both transports and
the backend's fail-closed capability gate. Explicit expected node sets prevent
a broken converter from passing by excluding everything.

It also verifies that the Shadowsocks 2022 shared key stays out of imported
node metadata and compatibility reports. Browser checks cover the format report,
Xray selection, selected URLs, desktop/mobile/narrow layout, and delayed
responses during format/user changes. Consult [subscriptions.md](subscriptions.md)
for the exact version-specific boundaries; this fixture is not an assertion
that arbitrary protocol extension fields are portable.

The template workflow additionally covers CRUD revisions, plan bindings,
unchanged credentials/tokens/runtime PID, custom Clash group order, real Mihomo
TCP/UDP forwarding, custom Surge section/node validation, personal permission,
and 1440/390/320px screenshots for both workspaces.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- Backend: 913 tests; Agent: 544 tests; frontend: 32 files and 216 tests,
  TypeScript checks and production build. Ruff, targeted formatting and probe
  Worker TypeScript checks passed.
- All 18 inbound variants passed their supported native client formats and
  unchanged URI/Base64 imports with real TCP/UDP target traffic. Mieru TCP and
  UDP underlays both passed through Mihomo v1.19.30; its executable SHA-256 was
  `8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a`.
  The sing-box v1.13.19 executable SHA-256 was
  `7e9dcd7239c49478a576d79f272751e5ed1c2aba7cc08ab1b2bd69c00c904ba1`.
- The custom Clash/Surge API, real Mihomo forwarding and both template browser
  workspaces passed. Generated credentials, subscription tokens and runtime
  identity remained stable.
- The patched runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  matching-source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Its `build.json` records four MPL-2.0 patches, package and race tests, and
  successful module verification.
- Existing Starlette/httpx deprecation, npm install-script approval and frontend
  bundle-size warnings remain.

These results do not close the other [migration gates](migration-map.md).

## Fork Protocol Smoke

Build the optional [compatibility runtime](fork-runtime.md), its unmodified
reference executable, and the current Agent wheel on the VPS. Obtain Mihomo
v1.19.30's `mihomo-linux-amd64-compatible-v1.19.30.gz` from its official release.
Verify the gzip SHA-256 before extraction:
`db214c7a2517e63c150d123178d16d102e03a241ccdae4e5e07ffbe9cf56c6f9`.
The tested Go 1.26.7 Linux amd64 tarball has SHA-256
`ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca`.

```bash
python scripts/vps/smoke-protocol-runtime.py \
  --xray /tmp/open-node-runtime-build/xray \
  --reference /tmp/open-node-runtime-build/xray-reference \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx
```

Use the backend development environment. The fixture requires root/systemd
only to install disposable dedicated non-root Agents and remove their units
and accounts afterward. All listeners are loopback-only; there is no public
provider registration. Both HTTPS lease and WSS paths use a trusted fixture CA.
The source runtime is first exercised unchanged, followed by the installed
Agent's patched runtime using the same configuration. The tests then import
nodes, assign a plan, consume actual subscribed credentials, check per-user
statistics, rotate non-managed credentials, exercise direct zero-user listeners,
withdraw managed access, restart the service with suspended listeners and
reactivate the same catalog credentials. Direct empty-user edits must keep each
TCP/UDP listener owned only by the new fork PID and reject old credentials;
managed withdrawal must remove every listener and preserve the private recovery
template across an Agent restart. The smoke also checks invalid-write
preservation and refusal of an official Xray switch without changing the fork
PID.

AnyTLS, every Snell variant and both Mieru underlays cover TCP and UDP target
bytes. Mieru additionally covers transformed UDP echo, DNS, a 4096-byte packet,
multiple targets on one association, user-attributed statistics and three fresh
negative associations. The UDP targets accept traffic only from Xray's explicit
loopback egress address, preventing Mihomo local-direct behavior from becoming a
false positive. The unmodified reference runtime is the Mieru UDP negative
control. Snell v6 uses the free fork client. Complete mixed exports are covered
separately by the subscription-client smoke above.
Other architectures, multi-file takeover and public-provider staging are not
established by these tests.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- The complete smoke passed independently over WebSocket and HTTP with exit
  code zero. Both used disposable non-root systemd Agents, trusted local TLS,
  real Mihomo and the pinned fork client.
- The unmodified reference accepted all original TCP paths but rejected Mieru
  UDP target traffic over both underlays. The patched runtime passed TCP/UDP,
  DNS, multi-target, large-packet, statistics, rotation, direct zero-user,
  managed suspension, Agent-restart persistence and exact reactivation checks.
- The failed official-Xray migration preserved the config bytes and the exact
  running fork PID. Every deliberate runtime restart removed the prior PID;
  `fuser` proved each active TCP/UDP listener belonged only to the replacement
  process, while managed suspension left no owner.
- The runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  the unmodified reference SHA-256 was
  `b0f43766871def4cad3952b9cecd2f4dfd4ac4dd9771866e9e778980682e5cbb`.
  Fixed source revision was `d3fdae5833a92070414db588ee9893264147b789`.
- Matching source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Patch SHA-256 values were
  `0914ab8149646801904d91f6229520acbe6cae1e749229fb5c8e129fee458814`
  (empty users),
  `d85463cfdf6b0c5ca3f17f046e2bf78e1dc44a1e21146baff9faf804137708d7`
  (AnyTLS UDP),
  `3841a90cae74b978de31671057a3bb05ec84589d86cecdf34222175f318da506`
  (Mieru UDP) and
  `91e7f33c3752f5fb8f46852e89cdc02ef2cfd7479657e154e26e5ea184c7d644`
  (limiter). Go 1.26.7 package, race and module-verification gates passed.

## Host Policy Smoke

Build the current Agent wheel on the VPS. Keep a trusted pre-policy bootstrap
checkout (including its sibling lifecycle modules) to exercise old-helper
compatibility. Use the backend test environment, Debian Nginx and the pinned
NextTrace binary from [agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-host-policy.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --previous-bootstrap /path/to/previous-checkout/agent/app/open_node_agent/service.py
```

The root-only fixture installs isolated non-root systemd services using the
previous installer and copied lifecycle helpers. It exercises both HTTPS leases
and WSS, actual TCP/ICMP and IPv4/IPv6 NextTrace results, capability removal,
unchanged PID on no-op, checksum-verified executable replacement failure,
SIGKILL during the transaction, old-bootstrap refusal, and separate helper
restart recovery. It preserves helper hashes and boot-enable preferences,
verifies stopped Agent/Xray intent, performs a real remote wheel upgrade through
the old helper, and checks VLESS forwarding after transitions. A deliberately
faulty fixture wheel exits only under the newly granted raw capability, proving
rollback after a real systemd startup failure. No fixture wheels are published.
GeoIP is disabled; this smoke does not query public IP/ASN providers or register
public accounts. The designated VPS denies unprivileged ICMP datagram sockets
(`ping_group_range: 1 0`), so removing the raw capability also denies ICMP
fallback there. The smoke does not change that global setting. It removes the
isolated units/accounts on exit.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 269 tests; frontend: 98 tests and production build.
- Agent Ruff and the new smoke's Ruff checks passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full host-policy smoke passed over both transports using the previous
  bootstrap from commit `84d0bc3`, including genuine process termination and
  startup failure, private recovery metadata and unchanged helper hashes.
- The separate systemd installation/upgrade/rollback/uninstall smoke passed.
- The current remote lifecycle helper passed its complete regression smoke,
  including interrupted staging/switch/removal, durable final callbacks,
  retained data, real VLESS forwarding and confirmed desktop/mobile/narrow
  browser actions. Desktop and narrow/mobile screenshots were inspected.
- These results do not establish other OS/architecture coverage, public
  provider registration, fork-specific protocols or the remaining migration
  gates in [migration-map.md](migration-map.md).

## Native WARP Smoke

Build the current Agent wheel and frontend on the VPS. Use the backend test
environment with Playwright/Chromium and a trusted Debian Nginx executable:

```bash
python scripts/vps/smoke-warp.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --output /tmp/open-node-warp-shots
```

The root-only fixture installs disposable non-root systemd services and uses a
local TLS provider fixture with actual Xray WireGuard peers. Tests cover both
Agent transports, explicit first-registration consent, free-account status,
real IPv4/IPv6 encrypted forwarding, reapply, optional account/config updates,
Agent restart, blocked referenced-outbound removal, retryable provider failure,
preserved direct traffic, private state and non-disclosure in WARP results/logs.
Browser checks cover 1440px, 390px and 320px confirmation/result layouts. Host
routes and interface names must be unchanged after cleanup.

This does not create a public Cloudflare account or establish public-provider
compatibility. Live registration and deletion require operator acceptance of
Cloudflare terms. See [warp.md](warp.md#verification-boundary). The wheel is a
source build, not a replacement for immutable published Agent 0.1.0 artifacts.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 269 tests; Agent: 231 tests; frontend: 98 tests and production build.
- Agent/backend Ruff checks and the WARP smoke passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full non-root WARP fixture smoke passed over both transports, including
  real encrypted IPv4/IPv6 forwarding, restart, provider-failure recovery and
  inspection of desktop/mobile/narrow screenshots. No routes or interfaces changed.
- A fresh installation of the unmodified built wheel passed the independent
  Agent runtime smoke on both transports, including real VLESS traffic,
  provisioning/revocation, statistics, failed configuration recovery and
  persistent stopped-runtime intent.
- These results do not verify Cloudflare public registration or a paid WARP+
  account. No public provider terms were accepted by the test harness.

## Native Diagnostics Smoke

Build the current Agent wheel and frontend on the VPS first. Use the backend
test environment with Playwright/Chromium installed, a trusted Debian Nginx
binary, and the pinned NextTrace Tiny executable documented in
[agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-diagnostics.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --output /tmp/open-node-diagnostic-shots
```

The root-only fixture installs disposable non-root services, uses its own
trusted HTTPS/WSS gateway, and removes owned units/accounts on exit. It checks
real TCP and ICMP fallback, DNS failure, IPv4/IPv6 TCP trace hops, public
ASN/geolocation evidence, history ingestion, log ownership/clearing, persistent
VLESS traffic, and a default service without raw socket privileges. The public
GeoIP check needs upstream connectivity; it is not substituted with fixture
metadata. Browser checks cover 1440px, 390px and 320px layouts, real queued
probes, confirmed log clearing and scheduled return-route creation.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 267 tests; Agent: 182 tests; frontend: 95 tests and production build.
- Ruff passed for the Agent and diagnostic smoke. Existing backend deprecation
  and frontend bundle-size warnings remain.
- The installed non-root Agent passed the complete diagnostic smoke over both
  transports, including default-denied raw-socket behavior, public NextTrace
  ASN evidence, real scheduled-task dispatch, VLESS forwarding after log
  clearing, and inspected desktop/mobile/narrow screenshots.
- The separate real systemd install/upgrade/rollback/failure/recovery/uninstall
  smoke passed again. Fixture cleanup reported no remaining owned resources.

These checks do not establish broader OS/tool support, automatic in-place
permission changes, cross-version public-release upgrades, or completion of
the remaining [migration gates](migration-map.md). Agent 0.2.0 is a source
build here, not a replacement for the immutable published 0.1.0 assets.

## Control Plane Deployment Smoke

On the designated VPS, with Docker Compose and a trusted Nginx binary:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser]'
backend/.venv/bin/playwright install --with-deps chromium
AGENT_ENV="$(mktemp -d /tmp/open-node-package-agent.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
OPEN_NODE_IMAGE_TAG=local OPEN_NODE_REVISION="$(git rev-parse HEAD)" \
  docker compose --env-file /dev/null -f deploy/compose.yaml build
backend/.venv/bin/python scripts/vps/smoke-control-plane.py \
  --image-tag local \
  --agent-python "$AGENT_ENV/bin/python" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-package-shots
```

Build the Agent wheel with the normal test runner first. The image tag must
identify the image built from the checkout under test.
This smoke uses the shipped Compose file and HTTPS proxy template. It creates
randomized projects with loopback-only ports, private named volumes, a local
TLS identity, and a private Nginx prefix. No public CA, DNS account, host
certificate store, production service, or existing volume is modified.

It verifies non-root/read-only runtime restrictions, an empty installation,
administrator creation and recovery, Secure/HttpOnly/SameSite cookies,
Origin/CSRF rejection, SPA route reloads, API/static-file boundaries, and an
actual WSS probe stream. It then checks session, inventory, and encrypted-key
persistence after container/network recreation, a stopped-volume backup
restored into a new project, a changed-image upgrade, and explicit rollback
after a deliberately broken release fails startup. Temporary candidate images
and owned volumes are removed afterward. No arbitrary future database
downgrade, multi-host deployment, or zero-downtime upgrade is claimed.

The installed Agent also connects through HTTPS/WSS using only the fixture
CA, with TLS verification enabled. The full real-Xray forwarding, client
provisioning/revocation, failed-restart rollback, config recovery, telemetry,
and persistent-deduplication smoke runs on both transports against the
container. It uses the pinned Xray archive documented below; the optional
`--xray-archive` argument reuses a copy without bypassing its checksum.

The full operator browser smoke runs against the production image through
HTTPS at desktop 1440x900 and mobile 390x844. HTTP and WSS clients validate the
fixture certificate and hostname. Chromium allows only the generated fixture
SPKI via its per-process test switch, not a blanket TLS bypass. Screenshots
remain at `--output`; fixture credentials are not written there.

## Independent-Agent Smoke

After the normal test runner, install the built wheel into a separate environment
and run the real-runtime smoke on the VPS (Linux x86-64, Python 3.11+, and curl):

```bash
AGENT_ENV="$(mktemp -d /tmp/open-node-agent-wheel.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
backend/.venv/bin/python scripts/vps/smoke-open-node-agent.py --agent-python "$AGENT_ENV/bin/python"
```

The smoke downloads the official
[Xray v26.3.27 Linux 64-bit release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)
and verifies the archive SHA-256
`23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae`.
`--xray-archive /absolute/path/Xray-linux-64.zip` can reuse a downloaded archive;
the same digest check is mandatory. It extracts only the binary into a private
temporary directory, never installs over a host Xray binary, and deletes its
runtime fixtures on completion. The separate wheel environment remains available
for inspection. No MMWX image or activation server is involved.

For each transport (WebSocket and HTTP), the test starts disposable FastAPI,
Agent, Xray server/client, and HTTP fixture processes. All listeners use loopback
and ephemeral ports. It checks actual SOCKS-to-VLESS forwarding, new-client
provisioning and revocation without removing other users, per-user traffic
reporting, invalid config rejection with protocol-sized error messages, failed
runtime restart with file/traffic rollback, recovery test/write/restart, and
persistence of users and stop intent across Agent restarts. Redelivery is
simulated by requeuing one completed non-idempotent command only in the fixture
database; a second execution would fail, so a cached successful result proves
restart deduplication. Owned process groups are terminated on exit.

This proves the managed official-Xray VLESS path, not every protocol, encrypted
legacy-agent migration, systemd mode, or host install/upgrade/uninstall lifecycle.

## Native Limiter Smoke

Build the [free runtime](fork-runtime.md), Agent wheel and frontend on the VPS.
With the backend development environment, Chromium and a verified Mihomo binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-native-limiter.py \
  --xray /absolute/path/to/free-runtime/xray \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-native-limits
```

The fixture installs a dedicated non-root Agent over trusted HTTPS/WSS, imports
18 protocol variants and provisions their plan caps. It measures actual
combined upload/download rates and UDP target forwarding where supported,
checks real Vision TLS traffic, live cap changes on existing connections,
shared parallel buckets and admission quotas, automatic rules and persistence.
Its browser portion exercises desktop/mobile/narrow limit editing, stale
revisions and confirmed removal. It does not reuse existing host services.
Both Mieru underlays carry UDP targets and retain the authenticated user's
limiter context.

Core unit tests cover policy persistence, private files, stale revisions,
concurrent admission, live bucket updates and automatic rule timing. Run
`go test -race ./common/nodelimits` inside the matching source tree with an
isolated C compiler on the VPS. Do not run tests or builds on the local workstation.

Verified again on 2026-08-29 on the designated Linux amd64 VPS: the real smoke
measured all 18 TCP variants and all 18 UDP-target variants, including Mieru
TCP/UDP underlays. It also passed Vision TLS bulk, hot caps, shared credential
aliases, admission/release, parallel slot release, sustained/burst activation
and expiry, restart persistence and desktop/mobile/narrow editing. The current
runtime SHA-256 is
`7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
the rebuilt Agent wheel SHA-256 is
`a049c7b76a34341b01c3de6705edd8fa888011054330bb42b9133e371ed552f2`.
These results do not establish arbitrary OS, external-service or public-provider
compatibility.

## Agent Service Lifecycle

After building the Agent wheel, run the following on the designated VPS as root:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-service.py \
  --wheel "$AGENT_WHEEL"
```

This requires a running systemd manager plus `useradd`, `runuser`, and curl.
It uses the same pinned official Xray archive as the independent-runtime smoke;
`--xray-archive` can reuse that archive without skipping digest verification.
The fixture creates a uniquely named `open-node-agent-<id>.service`, dedicated
non-login account, and `/opt/open-node-agent-smoke-<id>` directory. It does not
reuse existing MMWX services, tokens, databases, unit names, or install roots.

The test verifies failed first installation and corrected-input retry, non-root
systemd readiness/hardening, real forwarding and runtime edits, successful
upgrade, explicit rollback, failed preflight without stopping the old process,
failed-start rollback, and recovery after forcibly terminating the deployment
process during a recorded switch. It also kills the Agent process to verify
systemd restart and Xray child cleanup, then checks uninstall/reinstall with
config/journal preservation and explicit purge of only owned files/account.

Good and deliberately broken candidate wheels are generated only inside the
test fixture with updated wheel records. They are not published artifacts.
Fixtures are removed at the end; failures print the service journal and report
any cleanup that needs attention. Stopped-Agent upgrades and path/ownership
guards have additional focused unit tests. External `runtime_mode: systemd`
and arbitrary future schema rollback are not covered by this smoke.

## Xray Multifile Takeover Smoke

Build the frontend and Agent wheel on the designated VPS. Run with the backend
development environment, Playwright Chromium, systemd, polkit and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-takeover.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-takeover-screenshots
```

The root-only fixture creates a disposable root-owned virtual environment and
dedicated non-root services. It obtains official Xray 26.3.27 using the same
pinned archive digest as the runtime smoke. An existing verified archive can
be supplied with `--xray-archive`. It never operates on an existing MMWX service.

Both HTTPS polling and WSS exercise repeated explicit JSON/JSONC inputs plus
a directory. A separate polling case uses only `-confdir`, with an existing
target inside it. Conflicting credentials, outbound order and routing distinguish
the actual core's merge from generic JSON merging. Real VLESS traffic verifies
the source and consolidated layouts, newly provisioned users and Agent restarts.
Checks cover secret-free GET previews, stale checksums, exact original-byte
backups, unchanged unit definitions, neutralized secondary files, repeated no-op
requests, and consolidation of a stopped service without starting it.

Fixture-only wheels inject real SIGKILLs after the prepared, stopping and
activating records and after the first config replacement. Restarted Agents
restore files and forwarding; interrupted commands are redelivered and return
409, not a manufactured success. An independent file edit blocks recovery until
the host repairs it. A real occupied listener makes Xray activation fail and
verifies delayed rollback after the port is released. These modified wheels are
never published. The unmodified wheel then reruns the existing external-systemd
fixture over both transports, including ownership and authorization guards.

Browser checks exercise preview, explicit acknowledgment, checksum-bound apply,
command completion and actual forwarding at 1440x900, 390x844 and 320x740.
The dialog scrolls internally, keeps actions visible and wraps long paths and
checksums. Unit tests also cover read-only previews during pending recovery,
backup-before-commit ordering, input/output size limits and file safety.

Recorded verification on 2026-08-28 (UTC) on the designated VPS:

- Backend: 451 tests; Agent: 434 tests; frontend: 99 tests, totaling 984.
- Frontend production build, Ruff and probe Worker TypeScript checks passed.
- The installed-wheel takeover fixture passed both control transports, the
  directory-only case, all crash/failure cases and both original systemd regressions.
- Desktop, mobile and narrow browser workflows passed; screenshots were inspected.
- The final Agent wheel SHA-256 is
  `b971c38c455a0a5adc5a7f74fb703a54f25301923da17a07a4ab74acc3731b77`.
- Existing Starlette/httpx deprecation and frontend bundle-size warnings remain.

The verified host scope remains Debian 12 x86-64 and official Xray 26.3.27.
Other runtime/OS combinations, arbitrary host-process adoption and crash recovery
for ordinary config mutations are not established by this workflow. See
[takeover boundaries](xray-takeover.md) and the other [migration gates](migration-map.md).

## External Systemd Smoke

Build the Agent wheel and install it into a separate, root-owned virtual
environment readable by the disposable service account. On the designated
systemd/polkit VPS, with the existing smoke dependencies and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-external-systemd.py \
  --agent-python /path/to/installed-agent/bin/python \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx
```

The root-only fixture creates unique non-root accounts, independent Agent/Xray
units and exact polkit rules. For HTTPS polling and WSS it verifies actual
VLESS forwarding, provisioning, user stats, invalid-write rejection, failed
restart rollback, Agent restart without Xray interruption, remembered stop
intent, binding mismatch while the Agent stays online, and grant revocation
without stopping the host-owned runtime. It rejects aliases, mismatched binary
paths and writable unit files. Negative permission checks cover unrelated
services, manager reload and enablement. The polling fixture also exercises
`CAP_NET_BIND_SERVICE` on both services. Modified rules cannot be overwritten
or removed; fixture resources are cleaned up after the run.

This proves the [documented single-file binding](external-systemd.md), not
multi-file takeover, other OS/architectures, public providers, or a durable
rollback after a crash in the middle of an ordinary config mutation.

Recorded verification for this milestone on the designated Debian 12 VPS:
365 Agent tests, 387 backend tests, 98 frontend tests, the production frontend
build, and Ruff checks passed. The final installed wheel passed the external
fixture over both transports. The independent managed-runtime smoke and real
host install/upgrade/rollback/interruption/uninstall smoke also passed. These
results do not close the other [migration gates](migration-map.md).

## Remote Agent Lifecycle

Build the Agent wheel and production frontend assets first. On the designated VPS,
with the browser/cryptography dependencies and a trusted Nginx binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-lifecycle.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-agent-lifecycle-shots
```

The fixture uses separate HTTPS release and controller endpoints with explicit
local CA trust. Both native transports perform version/digest-pinned upgrades,
rollback, wrong-digest rejection, failed-preflight/start recovery, and actual
VLESS forwarding after changes. Mismatched wheel metadata and redirects outside
the host-approved source are rejected. Unix socket ownership and a foreign-UID request
test cover both filesystem permissions and the peer-credential boundary.

One-shot candidate-wheel pauses allow the test to kill the maintenance cgroup
during package staging and service switching. It checks persisted recovery,
unchanged configuration, old-version traffic, explicit interrupted results,
request deduplication, expired-lease redelivery, skipped dependent commands, and
a new explicit retry after staging recovery. A paused shutdown verifies recovery
from a crash during removal, before the Agent service finishes stopping.
Final uninstall reports are temporarily rejected by the fixture proxy, proving
the controller cannot claim completion before acknowledgment. Worker restart,
eventual reporting, worker shutdown and data-preserving reinstall are checked.

The browser checks explicit version/SHA input, confirmation, actual command
completion and resumed progress at 1440, 390 and 320 pixel widths. It also reopens
the uninstall dialog while the Agent is gone but its callback is still blocked,
and waits for the actual acknowledgment before displaying completion. Chromium
trusts only the fixture SPKI; the Agent and host downloader use normal TLS
verification. Screenshots remain in `--output` without fixture credentials.

After publishing the matching wheel, verify the actual default GitHub release
source separately, using that exact release artifact on the designated VPS:

```bash
PUBLISHED_AGENT_WHEEL=/absolute/path/to/published/open_node_agent-wheel.whl
backend/.venv/bin/python scripts/vps/smoke-agent-release.py \
  --wheel "$PUBLISHED_AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx
```

This performs real public release downloads without a test mirror, checks the
wheel pin and running release identity, sends VLESS traffic and rolls back on
both transports. Its controller remains a private trusted HTTPS fixture.

## Nginx And Certificate Smoke

On the root-accessible systemd VPS, supply a trusted Nginx binary and matching
stream module. Debian packages can be downloaded and extracted into a disposable
directory with `apt-get download` and `dpkg-deb -x`, without installing a global
service. Install `cryptography` in the smoke runner environment, then run:

```bash
backend/.venv/bin/pip install cryptography
backend/.venv/bin/python scripts/vps/smoke-nginx.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

An optional `--xray-archive` uses the existing pinned-digest Xray fixture. The
test installs a separate non-root Agent service for each transport, then checks
real HTTP, verified TLS, leaf serial rotation, key mismatch rejection, actual
reverse-proxy and stream response bytes, invalid configuration and occupied-port
rollback, exact stream cleanup, private file boundaries, site deletion, logs,
independent stop intent, Agent/Nginx crashes, durable interrupted-file recovery,
and data-preserving uninstall/reinstall. Test certificates are local fixtures;
no public CA or real domain validation is used. Fixture units/accounts and
directories are purged after the run, with existing services untouched.

## Atomic Tunnel Smoke

Use the same binary/module fixtures and built Agent wheel as the Nginx smoke:

```bash
backend/.venv/bin/python scripts/vps/smoke-tunnel.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

For each transport, this installs a fresh non-root systemd Agent and exercises
the real FastAPI tunnel planner and queue. It verifies fresh deployment without
prior Nginx installation, hostname-verified TLS SNI routing to static and proxy
sites, unmatched SNI reaching a fixed loopback TLS fallback, actual traffic
statistics, post-deployment snapshot refresh, stale-template rejection,
Nginx/Xray occupied-listener rollback, and owned stream-to-Xray listener
handover while preserving a neighboring stream server. It injects a durable
multi-file undo record with conflicting stored start intentions, restarts the
Agent, and verifies both running and intentionally stopped recovery. A failed
cold deployment must leave both services stopped. Unit tests also cover
command cancellation, corrupt intent records, and idempotent map merging.

This verifies official Xray v26.3.27 on Debian 12 x86-64, not an arbitrary
future Xray schema, zero-downtime switching, or fork-specific protocol support.

## ACME Lifecycle Smoke

On the same VPS, install the test-only DNS fixture dependency:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser,acme-test]'
```

Supply the verified lego v4.35.2 binary described in
[certificate setup](certificates.md#host-setup), and the
[Pebble v2.6.0 release](https://github.com/letsencrypt/pebble/releases/tag/v2.6.0).
The tested `pebble-linux-amd64.tar.gz` archive has SHA256
`ce5d87e1f674934c134b7cbcbc468e3df420994a17e77bdbf7aec611e2d373b9`.
Verify before extraction; the Pebble binary needs executable permission.

```bash
backend/.venv/bin/python scripts/vps/smoke-certificates.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

This root-accessible systemd test needs free UDP/TCP port 53 on `127.0.0.1`
and `::1`. It binds exclusively and fails instead of replacing an existing
listener. Existing DNS services on other addresses remain untouched; neither
`/etc/hosts` nor `/etc/resolv.conf` is modified. The fixture's authoritative
NS is `localhost`, keeping lego's OS-level NS address lookup offline. ACME,
webhook, backend, Agent and Nginx listeners are all loopback-only.

The test does real DNS ownership validation, not Pebble's always-valid mode.
It verifies HTTPS CA trust, EAB account creation, apex plus wildcard SANs,
TXT presentation and cleanup, not-due skips, credential rejection retaining
the active certificate, forced renewal, backend restart persistence, and
actual elapsed-time automatic renewal of four-minute certificates. Real
non-root Agent services then deploy/reload the certificate and restore a
historical version, checking trusted TLS leaf serials and HTTP bytes for
both transports. Test services, accounts, DNS listeners and private state
are removed on completion. No public CA or real DNS account is used.

## HTTP-01 Lifecycle Smoke

Use the same VPS dependencies, pinned binaries and free loopback DNS ports as
the ACME lifecycle smoke above. Build the frontend on the VPS first. This test
also needs the existing Debian `www-data` account for an independently running
non-root Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-http.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so \
  --screenshots /tmp/open-node-http01-screenshots
```

The browser creates and issues standalone and webroot profiles without a DNS
provider. It checks mode-specific controls, wildcard rejection, CA consent,
renewal controls, collapsed/expanded EAB fields and 1440/390/320px layouts.
Pebble fetches actual challenge responses through a loopback fault-injection
hop and Nginx: standalone requests reach lego's listener, while webroot
requests read real public challenge files as a different Unix user.

The test covers SAN issuance, not-due skips, deliberate HTTP validation
failure, forced renewal, file/listener cleanup and active-version preservation.
It kills the backend while lego survives, verifies the inherited worker lock,
then kills lego and checks interrupted-job recovery and stale-token removal.
Both modes renew automatically after actual elapsed time. HTTP-issued
certificates are deployed to non-root Agent Nginx instances over WebSocket
and HTTP, with trusted TLS serial checks and version rollback.

The website's original content is checked unchanged, and all private vault
files are checked for private permissions. Test listeners, processes, Agent
services and data are disposable; public-CA orders and production websites
are not used. This does not prove an operator's public DNS/port-80 routing.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 317 tests; Agent: 231 tests; frontend: 98 tests and production build.
- HTTP-01 standalone/webroot and existing DNS-01/EAB lifecycle smokes passed,
  including real automatic renewal and trusted Agent TLS/version rollback.
- HTTP hard-crash recovery retained the old certificate and removed stale
  challenge responses only after the surviving lego process released its lock.
- The operator browser regression passed. HTTP forms and expanded EAB fields
  were checked at 1440px, 390px and 320px, including fully visible submit controls.
- Additive SQLite migration retained DNS/imported profiles. EAB-only HTTP
  catalogs also detect missing vault keys instead of generating a replacement.
- Ruff passed for changed backend modules and the HTTP smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

## Remote HTTP-01 Smoke

Build the frontend and current Agent wheel on the VPS. Use the backend
development/browser/ACME-test extras and the same pinned Pebble, Nginx and
official Xray artifacts as the existing ACME and Agent smokes:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-remote.py \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --nginx-stream-module /path/to/ngx_stream_module.so \
  --screenshots /tmp/open-node-remote-http01-screenshots
```

The fixture starts a TLS-verified controller without lego or central HTTP-01
listeners. Real non-root systemd Agents connect over HTTPS polling and WSS.
An EAB-required Pebble CA reads standalone responses and owned Nginx webroots
on those nodes, through an observable fault-injection proxy. It never supplies
synthetic successful challenge data.

The workflow covers issue, not-due skip, failed validation retaining the old
version, forced renewal, node-disconnected cleanup and reconnect, actual TLS
deployment, account contact changes and elapsed-time automatic renewal. A
controller hard kill leaves the ACME child holding the inherited lock; after
the child is killed, recovery must reuse the same job/order and create a new
challenge lease after cleaning the old one.

Playwright creates remote profiles, selects validation nodes, checks wildcard
rejection and explicit terms/EAB fields, and reads issued versions. Layout
checks and screenshots cover 1440px, 390px and 320px. The test leaves existing
services untouched and removes its temporary systemd users/services/directories.
It does not use public CA orders or provider accounts.

Focused backend tests cover additive scan/profile migration, live capability
checks, command/lease receipts, cleanup retries, deletion protection, cancellation,
order-response loss, persisted CSR/key binding and public-only EAB payloads.
Agent tests cover host opt-in, exact HTTP host/path/token matching, expiry,
idempotent release-before-present ordering, restart, occupied ports, immutable
leases and filesystem replacement/link protection.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 387 tests; Agent: 304 tests; frontend: 98 tests and production build.
- Remote standalone/webroot issuance, EAB, HTTPS/WSS, cleanup after reconnect,
  inherited-lock/order recovery and elapsed-time renewal with live TLS passed.
- Existing DNS-01/EAB, control-plane HTTP-01 and account/revocation lifecycle
  smokes passed, including forced interruption and automatic renewal.
- Desktop/mobile/narrow screenshots were inspected; the changed Python code
  passed Ruff. Existing Starlette/httpx and frontend bundle-size warnings remain.

## Certificate Administration Smoke

Use the same pinned lego/Pebble binaries, backend development/browser/ACME-test
extras and free loopback DNS ports as the lifecycle smokes. Build the frontend
on the VPS first:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-administration.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --screenshots /tmp/open-node-ca-admin-screenshots
```

The fixture uses real HTTP-01 issuance and an EAB-required CA. It preserves a key
left by failed registration, edits EAB before registration, updates the registered
CA contact while checking the original key/URI, and renews with lego afterward.
Historical-version revocation is independently checked at Pebble's management API.

A TLS-verified forwarding fixture deliberately loses accepted account/revocation
responses. Retries must query and reconcile actual CA state, including
`alreadyRevoked`. A backend hard kill while the helper holds a confirmed response
verifies inherited locking and durable receipt recovery without a duplicate request.
The test also checks forced new-key reissuance, imported certificate revocation,
duplicate blocking and ledger retention after profile deletion.

Playwright operates account/EAB and revoke/retry dialogs with real backend requests.
Screenshots and layout checks cover 1440px, 390px and 320px, including visible
confirmation controls, masked credentials and disabled revoked-version actions.
The fixture checks private permissions and removes temporary request files.
No public CA, DNS-provider credential, production certificate or website is used.

The focused `test_certificate_administration.py` suite additionally exercises
input/secret validation, additive schema migration, competing deployment/revocation
and import transactions, retained commands without targets, receipt mismatches,
graceful cancellation and revoked on-disk candidate recovery.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 231 tests; frontend: 98 tests and production build.
- The administration smoke passed with actual CA contact/status checks, lost
  responses, hard restart, duplicate/import protection and new-key reissuance.
- Existing DNS-01/EAB and HTTP-01 standalone/webroot smokes passed, including
  automatic renewal, both Agent transports, trusted TLS and version rollback.
- Operator UI regression and 1440/390/320px account/revocation layouts passed.
  The final browser run also checks the revocation icon's loaded glyph.
- Ruff passed for changed backend code and the new smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

## Reference-Agent Smoke

After installing the backend development dependencies, run this on the VPS
with Docker available:

```bash
docker pull ghcr.io/iluobei/mmw-agent@sha256:d9ff8cd1525947e1e535ca49d6b22f1b63ff28d393c46efea6f88eeb40e8840d
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py --secure-channel
```

The script uses the unmodified `mmw-agent` 0.4.7 image pinned by digest. It
creates a private, internal Docker network, a temporary SQLite database and
config directories, and a backend listener on that bridge with an ephemeral
port. The agent has no host-network access, published ports, or host config
mounts. Container capabilities are dropped. Only disposable files are
modified, and the container, network, and backend are removed when it exits.

The smoke verifies actual `/api/remote/ws` authentication, the initial config
snapshot, an agent-validated config write, the automatic WebSocket refresh
and its returned config, restart-induced drift, and manual acceptance of the
pending config. It also checks sequential recovery validation/write and the
failure path: when the real agent returns HTTP 200 with `ok=false`, neither
the write nor restart is attempted and a previously repaired healthy config
is unchanged on disk. With `--secure-channel`, it also verifies rejection of
wrong and malformed pins before registration, encrypted round trips, and fresh
encrypted sessions after controller restart with the same stored identity.
Both modes run in external Xray mode without a live Xray process. They do not
prove forwarding traffic, embedded runtime behavior or legacy HTTP callbacks.
They do not make the reference image the distributable Open Node agent.

## Operator Browser Smoke

Install the optional browser dependencies and Chromium on the VPS, then run:

```bash
backend/.venv/bin/pip install -e 'backend[browser]'
backend/.venv/bin/python -m playwright install --with-deps chromium
backend/.venv/bin/python scripts/vps/smoke-operator-ui.py --output /tmp/open-node-ui-artifacts
```

The script creates a temporary administrator/database and starts disposable
FastAPI and Vite processes on loopback ports. It checks that private views do
not load before sign-in, rejects an incorrect password, creates a server
through the UI, verifies session persistence across reloads, changes the
password on mobile, checks rejection of the old password, signs out, and
expires a session to verify the UI returns to sign-in. It captures desktop
and mobile login/access screenshots and checks horizontal overflow and form
control bounds. Services and database files are removed on completion; only
the requested screenshots remain. No existing administrator is changed.

Certificate coverage also creates a DNS provider and profile, requires explicit
CA terms, imports a real PEM pair, downloads certificate/private key separately,
verifies secret fields clear on reopening, and checks desktop/mobile forms.
Private keys and provider credentials must not appear in browser storage.

The Access page also verifies the configured Agent public key/fingerprint,
native clipboard copy and desktop/mobile layout. The disposable browser fixture
creates its own private identity. The production-image smoke creates the seed
with the non-root container CLI and verifies refusal to overwrite it, private
permissions and identity preservation through container recreation and volume
backup/restore; its HTTPS browser run checks the same public metadata.

The reference-agent smoke also creates a temporary administrator and signs in
as the operator; the reference agent still authenticates only with its own
bootstrap token. No test disables management authentication.

## Managed Xray Release Smoke

With the backend's browser extra/Chromium and a built Agent wheel on the VPS:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-releases.py \
  --wheel "$AGENT_WHEEL" \
  --output /tmp/open-node-xray-release-shots < /dev/null
```

This root-only fixture installs dedicated non-root systemd Agents and uses
official Xray `v26.2.6` and `v26.3.27` archives. Each transport verifies real
version changes, process executable paths, actual VLESS forwarding, checksum
rejection, validation before stopping the old runtime, and geodata discovery.
It checks untouched root-owned bootstrap binaries and unchanged user config.

The ordinary wheel is exercised first. A separate fixture-only wheel then
supplies deterministic occupied-port and interruption faults while retaining
the real Xray binaries. The smoke verifies failed-start rollback, timeout
recovery, process-group crash recovery, an explicit interrupted-command result,
and restoration of the ordinary Agent wheel. Removal/reinstallation preserve
configuration and stopped intentions. Desktop/mobile browser checks submit
real version/checksum requests and require acknowledgment before rollback.
Temporary installations/accounts are purged; requested screenshots remain.

Unit coverage also checks archive/path/size boundaries, cached file integrity,
version mismatch, initial missing config, no-op reinstall preserving rollback,
unresolved transaction rejection and removal with a damaged config. See
[xray-releases.md](xray-releases.md) for ownership and recovery semantics.

## Multi-Node Change-Set Smoke

On the designated VPS, build the frontend, install the backend's `browser`
extra and Chromium, and install the Agent wheel into a separate environment.
Then run:

```bash
backend/.venv/bin/python scripts/vps/smoke-change-sets.py \
  --agent-python "$AGENT_ENV/bin/python" \
  --output /tmp/open-node-change-artifacts < /dev/null
```

This uses the same pinned official Xray archive as the independent-agent smoke
and accepts `--xray-archive` for a checksum-verified local copy. It starts an
authenticated disposable FastAPI controller with the production frontend,
two installed Agents and real VLESS traffic for WebSocket/WebSocket, HTTP/HTTP
and mixed transport pairs. Temporary gates verify forward ordering, reverse
rollback ordering, cancellation while a forward command is executing, and
automatic compensation after native Xray validation fails. Bootstrap and
newly provisioned client traffic are checked before and after recovery.

The mixed pair also exercises the real browser rollback-failure/retry workflow,
retained command history, incomplete compensation, and explicit acceptance
with a required reason and checkbox on desktop and mobile. Layout failures
retain screenshots and element-bound diagnostics. Temporary processes and
private state are removed; only requested artifacts remain. Unit tests cover
lease races, overlapping reservations, draining earlier sequences, late
rollback rejection, restart persistence and missing-column SQLite migration.

## Earlier Registration Invitation Verification

Registration invitations passed on the designated VPS:

- Backend full regression: 883 tests. Frontend: 32 files and 216 tests, Vue
  typecheck and production build. Ruff passed for all backend sources, tests and
  the new smoke script.
- Focused coverage passed digest-only persistence, working subscriber login,
  exact plan/runtime enrollment, generic invalid/revoked/expired/used responses,
  case-insensitive username retry, atomic concurrent claims, plan cleanup and
  administrator/public route isolation.
- The WebSocket smoke installed a temporary non-root Agent, claimed a plan-bound
  invitation, waited for the durable access command, exported the invited user's
  Xray configuration and forwarded 32 KiB of real TCP traffic. Reuse failed with
  the generic unavailable response and temporary services were removed.
- The production preview completed actual HTTP registration and subscriber login.
  Administrator invitation management and the invited account form passed at
  1440, 390 and 320 pixels without horizontal overflow or console errors.
- A copy of the prior preview database retained every count across 48 existing
  tables and two existing rows. Startup added an empty `registration_invitations`
  table and `PRAGMA foreign_key_check` remained clean.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not enable open anonymous signup or close the remaining
[migration gates](migration-map.md).

## Earlier Template Verification

Custom Clash and Surge templates passed on the designated VPS:

- Backend full regression: 858 tests. Frontend: 205 tests and production
  build. Ruff formatting and checks passed for all backend sources, tests and
  the subscription-client smoke script.
- The existing 18-variant client fixture passed real Mihomo, sing-box and Xray
  forwarding. Its focused template run passed administrator/subscriber CRUD,
  revision guards, personal permission, plan/system defaults and catalog
  remapping without changing credentials, tokens or the runtime PID.
- Custom Clash output was downloaded from the public endpoint, loaded by
  Mihomo and forwarded real TCP and UDP traffic. Custom Surge output from the
  same endpoint preserved the non-proxy profile text and matched the exact
  compatible node set under an independent parser.
- Administrator and subscriber workspaces passed at 1440, 390 and 320 pixels.
  Actual Surge application import remains an Apple-platform gate.
- Agent sources did not change. The prior 536-test Agent baseline, wheel and
  free-core artifacts were reused by the real lifecycle/client smoke.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Automatic Speed Rule Verification

Per-plan automatic speed rules passed on the designated VPS:

- Backend full regression: 815 tests. The final command-payload guard then
  passed 99 focused tests, including four new malformed-payload cases.
  Agent: 536 tests and wheel build; frontend: 202 tests and production build.
- The free core rebuilt successfully; protocol/core tests and the native
  limiter/dispatcher race tests passed. The existing multi-protocol smoke
  passed real TCP and supported UDP limits, Vision TLS bulk, shared connection
  quotas, live updates, sustained/burst rules, expiry and restart persistence.
- HTTP and WebSocket plan smokes passed create/edit/order/clear, validation,
  sequential input, native-editor preservation and independent subscribers.
  A 64 KiB echo took about 2.00 seconds under the automatic 0.5 Mbps combined
  cap, and under 1 ms for the other plan and after expiry, on this local VPS
  fixture. This is an enforcement check, not a network performance benchmark.
- Credentials, subscription exports and tokens stayed unchanged. Runtime
  policy survived restart; unchanged hot policy saves preserved active timers.
  Old Agent/core capability rejection, catalog roundtrips, legacy omission
  and additive schema upgrades passed focused tests.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for changed Python sources and smoke scripts. Temporary non-root
  Agent installations were removed after each smoke.

Verified Linux amd64 artifacts for this milestone:

- Agent wheel SHA-256:
  `7cf9f6463e13f691dbf198ded77fa49f3923cd600d64f507a47f2fb52a4374ca`.
- Free core SHA-256:
  `348434f6700cd49df8015c7707910fdc1bbfd196f9ea3fea05f8ed4189d4dc7a`.
- Matching MPL-2.0 source archive SHA-256:
  `4c0fa9c730ea58f88e3b0d5dca5b1a456085a3933a69d29eb73cf1dc79f63d43`.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Plan Alias Verification

Plan node aliases passed on the designated VPS:

- Full regression: backend 791 tests, Agent 522 tests, frontend 187 tests and
  production build passed. The earlier focused backend run passed 159 tests.
- All five subscription formats and previews use aliases before multipliers;
  reserved/original-name collisions, Unicode validation, isolated plans,
  preserved runtime records, legacy field omission, catalog remapping/rollback,
  node/server removal and repeated SQLite upgrades passed.
- Final HTTP and WebSocket browser runs passed creation, alias edits, stale
  revision rejection, disable/clear, and subscriber downloads. The downloaded
  Xray configuration forwarded real traffic while the runtime PID, credentials,
  subscription keys and unrelated plan remained unchanged.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for all changed Python sources and the smoke script. Temporary
  Agent installations were removed by the fixture.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Short-Code Verification

Custom subscription short-code verification on the designated VPS:

- Full regression: backend 765 tests, Agent 522 tests, frontend 177 tests and
  production build passed. After the final additive lookup-index change,
  84 focused backend tests passed, including a new query-plan check and both
  new-database and old-schema upgrade coverage.
- The final schema uses indexed lookups for long, generated and custom keys;
  the preceding table-scan query plan was reproduced and eliminated.
- The final WebSocket and HTTP runs passed operator/subscriber edits, stale
  revisions, case collisions, password/TOTP proof and actual browser downloads
  through the custom short URL. The downloaded Xray configuration forwarded
  real traffic. Clearing and resetting links preserved the runtime PID and
  node credentials; another subscriber kept forwarding.
- Desktop/mobile/narrow screenshots were inspected. Ruff passed for changed
  Python sources. Temporary Agent installations and private state were removed.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Subscriber Limit Verification

The subscriber-limit worktree passed on the designated VPS:

- Backend: 725 tests; Agent: 522 tests; frontend: 153 tests and production build.
- Ruff passed for the changed Python sources and the new smoke fixture.
- Real non-root installed Agents applied user/default/node speed and connection
  caps over trusted WebSocket and HTTP polling, including explicit unlimited,
  restored plan inheritance and persisted limits after an Agent restart.
- A paused Agent left existing forwarding available while quota withdrawal was
  pending. Reconnection denied the old credentials; raising the quota restored
  those same identities without resetting charged usage. Another subscriber
  kept forwarding throughout the quota changes.
- Browser checks covered stale saves, invalid values, subscriber visibility and
  1440px, 390px and 320px layouts. Screenshots were inspected. Fixture services
  and private state were removed after both transport runs.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Release Verification

The managed Xray release worktree passed on the designated VPS:

- Backend: 252 tests; Agent: 110 tests; frontend: 87 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real non-root systemd Agents changed between official Xray v26.2.6 and
  v26.3.27 over WebSocket and HTTP. Tests checked actual executable paths,
  VLESS forwarding, archive geodata, untouched root-owned bootstrap files,
  unchanged user configuration and checksum/validation failures.
- Fixture-only faults verified occupied-port rollback, command timeout,
  process-group crash recovery and an explicit interrupted-command result.
  Agent wheel rollback retained the selected runtime. Removal, stopped
  reinstallation and explicit service start preserved configuration.
- Installed-Agent forwarding, provisioning, revocation, failed-start recovery,
  journal deduplication and stop-intent checks passed again on both transports.
- Host service upgrade/rollback/removal, real Nginx HTTP/TLS and certificate
  rotation, atomic tunnel recovery and all three multi-node transport pairings
  passed again. Fixture installations and accounts were removed afterward.
- Desktop 1440x900, mobile 390x844 and narrow 320x740 release dialogs submitted
  real version/checksum requests, displayed the complete checksum and required
  acknowledgment before rollback. Each change was followed by real forwarding.
- The production image passed HTTPS/WSS, installed-Agent forwarding, private
  identity and session persistence, volume backup/restore and image rollback.
  Its operator flow also verified the complete product name at 320px after
  compacting the edition badge; desktop/mobile screenshots were inspected.
- The unmodified pinned reference Agent passed encrypted authentication,
  controller restart, config refresh, drift acceptance and validation-gated
  recovery again.

These results do not close the remaining gates in
[migration-map.md](migration-map.md), including remote Agent lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Encrypted-Agent Verification

The encrypted-Agent and safe-sync worktree passed on the designated VPS:

- Backend: 242 tests; Agent: 86 tests; frontend: 84 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- The unmodified pinned reference Agent passed both plaintext compatibility
  and encrypted WebSocket auth, config writes, refresh, controller/Agent
  restart and recovery. Wrong pins and malformed-pin plaintext fallback were
  rejected without registering the Agent or issuing work.
- Replay/tamper/direction/sequence checks, handshake deadlines, private-key
  files, concurrent send order, UTF-8/finite JSON and oversized historical
  command handling passed. Attempted historical work is not falsely completed.
- The production image passed HTTPS/WSS, real Xray forwarding on both native
  transports, private identity creation, non-overwrite, recreation and volume
  restore. Public key/fingerprint display and real clipboard copy passed in
  the desktop/mobile operator flow; screenshots were inspected.
- The real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport smoke
  passed again, including reverse compensation, in-flight cancellation and
  the desktop/mobile retry and explicit-acceptance workflows.
- The sync launcher passed real loopback SSH with key authentication and
  PowerShell 7.6.5 on Linux. Git fixtures verified non-destructive refusal of
  dirty/diverged/wrong-origin/wrong-branch checkouts and ignored-file conflicts.
  Windows PowerShell itself was not executed because tests run only on the VPS.

These results do not close the remaining runtime gates in
[migration-map.md](migration-map.md), including remote runtime lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Change-Set Verification

The coordinated change-set worktree passed on the designated VPS:

- Backend: 189 tests; Agent: 86 tests; frontend: 82 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport changes
  verified ordered execution, actual client forwarding, reverse compensation,
  cancellation in flight and automatic recovery after native validation failure.
- Desktop/mobile browser flows verified compensation retry, expanded command
  results, retained history, required acceptance reason/acknowledgment and live
  status. A deliberately delayed list response cannot overwrite a newer action.
- Independent installed-Agent and pinned reference-Agent smokes passed again,
  including snapshot refresh, validation-gated recovery and persistent journal
  behavior. No reference source is needed by the independent Agent.
- Missing-column SQLite upgrades preserve old command outcomes and pause legacy
  execution for review, including concurrent ordinary dependency sequences.

These results do not close the other runtime gates in
[migration-map.md](migration-map.md). Existing Starlette/httpx deprecation and
frontend bundle-size warnings remain.

## Earlier Certificate Verification

Certificate management was verified on the designated VPS:

- Backend: 167 tests; Agent: 86 tests; frontend: 77 tests and production build.
- Probe Worker type checks and Ruff passed.
- Real Pebble DNS-01/EAB, wildcard issuance, automatic and forced renewal,
  restart persistence, failure preservation, and trusted TLS/version rollback
  passed over both Agent transports.
- Browser certificate forms, terms confirmation, secret clearing and explicit
  PEM downloads passed on desktop and mobile; screenshots were inspected.
- Installed Agent, systemd lifecycle, Nginx, tunnel and reference-agent smokes
  passed again. No public-CA orders or real DNS credentials were used.

Public-provider staging and the remaining migration gates are not covered by
these results. Existing deprecation and bundle-size warnings remain.

## Previous Verification

On 2026-08-27 (UTC), the atomic-tunnel worktree passed on the VPS:

- Backend: 153 tests, including HTTP/WebSocket Nginx scan reporting, legacy SQLite
  scan-schema migration, anonymous management-route rejection, session
  persistence/expiry/revocation, CSRF/Origin rejection, concurrent login limiting,
  a password-reset/login race, administrator CLI recovery, and the existing
  inventory, dependency, migration, subscription, and change-set suites. Native
  tunnel coverage checks profile/capability selection, snapshot prerequisites,
  listener validation, generated paths/config, and post-deploy refresh.
- Independent agent: 86 tests, including private state/lock protection, TLS
  configuration, persistent deduplication, transport reconnects, heartbeats
  during commands, interrupted execution, bounded errors/subprocesses, atomic
  rollback, client edits, stop intent, network rate calculation, deployment
  ownership/path guards, package identity, activation recovery, and readiness checks.
  New coverage includes certificate matching/SAN/dates, file-boundary enforcement,
  include parsing/cycles, multi-file rollback, command cancellation, interrupted-file
  recovery, exact stream cleanup, separate stop intent, and master PID reuse guards.
  Coupled tunnel tests cover fresh files, map merging, stale snapshot rejection,
  start/cancellation rollback, durable file/intent recovery, invalid metadata,
  loopback stats discovery, and dynamic path rejection.
- Agent wheel: isolated build and installation into a separate environment;
  real Xray smoke passed over WebSocket and HTTP, including provisioning,
  revocation, actual forwarding/statistics, failed-start rollback, recovery,
  restart deduplication, and preserved stop intent.
- Real systemd lifecycle: failed first installation/retry, non-root service
  ownership and forwarding, upgrade/rollback, failed preflight and startup,
  interrupted-switch recovery, crash restart with child cleanup, data-preserving
  uninstall/reinstall, and explicit purge. No fixture units/accounts remain.
- Real Nginx: both transports with non-root Debian Nginx 1.22.1, verified HTTP/TLS,
  leaf serial rotation, key rejection, proxy/stream response bytes, occupied-listener
  rollback, exact stream cleanup, Agent and Nginx master crash recovery, interrupted
  file recovery, site deletion, and data-preserving service removal/reinstallation.
- Native tunnel: both transports with the real planner, queue, and installed
  wheel; verified TLS static/proxy/fallback bytes, traffic reporting, stale hash
  rejection, both runtime port conflicts, owned listener handover, and recovery
  of files plus running/stopped intentions. Failed cold deployment leaves no
  unwanted running service.
- Frontend: 74 tests, including session/CSRF request handling, expired-session
  transitions, waiting/skipped Vuetify component rendering, and the production build.
- Probe Worker: TypeScript checks.
- Ruff: backend, independent agent, and all six smoke scripts.
- Reference-agent smoke: all ten stages, with the pinned image.
- Chromium operator smoke: desktop 1440x900 and mobile 390x844 sign-in/access,
  server creation, reload persistence, password change, logout, and expiry. Nginx
  form defaults, page/control bounds, and full visibility of the active tab are
  also checked on both viewports, with configuration screenshots. Tunnel form
  checks cover default node-owned paths, duplicate/out-of-range port rejection,
  real request payloads, and single-line toggle text. Desktop and mobile
  screenshots were inspected after fixing the narrow desktop toggle layout.

The backend test run still reports a Starlette/httpx deprecation warning, and
the frontend build reports a large bundle warning. Neither is a failed check.
