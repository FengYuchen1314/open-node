# Agent 0.3.0a4 预览版

这是 Open Node Linux Agent 的预览版本。`agent-v0.3.0a4` 注解标签、`BUILD.json`
和控制面固定清单都指向源码提交
`0fc7519f267917342fe7e7086ecc696c42ed36e8`。

## 本次内容

- 新增受管服务器 Xray 出站、完整路由、WARP 和“其他节点作为出口”能力。
- 同机与跨服务器节点出口使用原子预览、确认、应用和回滚；控制面生成的凭据不返回浏览器。
- REALITY 出口自动维护来源入站的 SNI 排除，并按引用计数精确恢复人工配置。
- TLS 出口支持由来源 Agent 探测并固定证书 SHA-256，拒绝 `allowInsecure`、私网探测、DNS
  重绑定及未固定的自签证书。
- 节点删除、服务器删除和运行时身份同步会阻止破坏仍被受管出口引用的资源。

## 发布制品

GitHub prerelease 必须恰好包含以下 6 个文件：

- `open_node_agent-0.3.0a4-py3-none-any.whl`
- `open-node-agent-bootstrap-0.3.0a4.tar.gz`
- `BUILD.json`
- `SHA256SUMS`
- `mihomo-linux-amd64-compatible-v1.19.30.gz`
- `mihomo-linux-arm64-v1.19.30.gz`

SHA-256：

```text
388cdcbfda8fbad2a9c3d977355fd2afc0fe15884ef435419009573a533531db  open_node_agent-0.3.0a4-py3-none-any.whl
53d1d566570141c1078c95a4b1da1973324decc6ec4ba540fc8d745f34d2bd07  open-node-agent-bootstrap-0.3.0a4.tar.gz
82d0db8f7dde14d06c72edf672d555546252bd0a6f663edc96a6e3675c787578  BUILD.json
f7ddd2014d840a28e1d0d62195a8bf1240ee4920d3d293d12284a0a07a629b1a  SHA256SUMS
db214c7a2517e63c150d123178d16d102e03a241ccdae4e5e07ffbe9cf56c6f9  mihomo-linux-amd64-compatible-v1.19.30.gz
58896873736d28628f66de3677c8654fa0f180662523148e136cff4f6e890069  mihomo-linux-arm64-v1.19.30.gz
```

## 验收记录

- Agent Linux 全量：715 通过，1 跳过。
- 后端 12 分片：5237 通过，121 跳过。
- 前端：119 个测试文件、1291 个用例全部通过；管理端与 Probe 生产构建通过。
- 公网网关验证使用系统信任校验 `https://185.99.135.224:58090/healthz`，证书包含精确
  IP SAN；同端口 HTTP 请求以 308 保留路径和查询参数跳转至 HTTPS。

此版本仍是 Preview，不代表可以无审查接管任意既有 MMWX 主机。安装范围、安全边界和
固定版本以仓库中的中文 README、Agent 部署文档及 MMWX 源码对齐表为准。
