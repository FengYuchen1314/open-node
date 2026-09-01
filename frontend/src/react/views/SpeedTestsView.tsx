import {
  ClockCircleOutlined,
  DeleteOutlined,
  FundOutlined,
  HomeOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Flex,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import type { SpeedTester, SpeedTesterSecret, SpeedTestResult } from "../../domain/speedtests";
import type { ManagedNode } from "../../domain/subscriptions";
import {
  createSpeedTester,
  loadLatestSpeedTests,
  loadMihomoStatus,
  loadSpeedTesters,
  loadSpeedTestHistory,
  revokeSpeedTester,
  rotateSpeedTester,
  runSpeedTest,
  speedTestError,
  speedTestStatusMessage,
} from "../../services/speedtests";
import { listManagedNodes } from "../../services/subscriptions";

const SOURCE_KEY = "open-node-speedtest-source-v1";
const THREADS_KEY = "open-node-speedtest-threads-v1";
function storedSource() { try { return localStorage.getItem(SOURCE_KEY) || "master"; } catch { return "master"; } }
function storedThreads(): 1 | 8 { try { return localStorage.getItem(THREADS_KEY) === "8" ? 8 : 1; } catch { return 1; } }
function date(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "—"; }
function bytes(value: number) {
  if (!value) return "—";
  return value >= 1_073_741_824 ? `${(value / 1_073_741_824).toFixed(2)} GiB`
    : `${(value / 1_048_576).toFixed(1)} MiB`;
}
function resultState(item?: SpeedTestResult) {
  if (!item) return <Tag>未测速</Tag>;
  if (item.status === "running") return <Tag color="processing">运行中</Tag>;
  if (item.status === "failed") return <Tag color="error">失败</Tag>;
  return <Tag color="success">完成</Tag>;
}

export default function SpeedTestsView() {
  const { modal } = App.useApp();
  const alive = useRef(true), timer = useRef<number | undefined>(undefined), sequence = useRef(0);
  const [nodes, setNodes] = useState<ManagedNode[]>([]), [results, setResults] = useState<SpeedTestResult[]>([]);
  const [testers, setTesters] = useState<SpeedTester[]>([]), [mihomo, setMihomo] = useState<Awaited<ReturnType<typeof loadMihomoStatus>> | null>(null);
  const [source, setSource] = useState(storedSource), [threads, setThreads] = useState<1 | 8>(storedThreads);
  const [selected, setSelected] = useState<React.Key[]>([]), [busy, setBusy] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [history, setHistory] = useState<SpeedTestResult[] | null>(null), [historyNode, setHistoryNode] = useState("");
  const [createOpen, setCreateOpen] = useState(false), [testerName, setTesterName] = useState("");
  const [secret, setSecret] = useState<SpeedTesterSecret | null>(null);
  const latest = useMemo(() => new Map(results.map(item => [item.node_id, item])), [results]);

  async function refresh(background = false) {
    const run = ++sequence.current;
    if (!background) setBusy(previous => previous || "load");
    try {
      const [nodeResponse, recent, testerRows, core] = await Promise.all([
        listManagedNodes(), loadLatestSpeedTests(), loadSpeedTesters(), loadMihomoStatus(),
      ]);
      if (!alive.current || run !== sequence.current) return;
      const available = nodeResponse.nodes.filter(node => !node.removal_id);
      setNodes(available); setResults(recent); setTesters(testerRows); setMihomo(core);
      setSelected(value => value.filter(id => available.some(node => node.id === id)));
      if (source !== "master" && !testerRows.some(item => item.id === source && item.online)) {
        setSource("master");
        try { localStorage.setItem(SOURCE_KEY, "master"); } catch { /* private mode */ }
      }
      const delay = recent.some(item => item.status === "running") ? 1500 : 5000;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void refresh(true), delay);
    } catch (failure) {
      if (alive.current && run === sequence.current) setError(speedTestError(failure));
    } finally {
      if (alive.current && run === sequence.current && !background) setBusy("");
    }
  }
  useEffect(() => {
    alive.current = true; void refresh();
    return () => { alive.current = false; sequence.current += 1; window.clearTimeout(timer.current); };
  }, []);

  function chooseSource(value: string) {
    setSource(value); try { localStorage.setItem(SOURCE_KEY, value); } catch { /* private mode */ }
  }
  function chooseThreads(value: 1 | 8) {
    setThreads(value); try { localStorage.setItem(THREADS_KEY, String(value)); } catch { /* private mode */ }
  }
  async function queue(node: ManagedNode, latencyOnly: boolean) {
    if (busy) return;
    setBusy(`${latencyOnly ? "latency" : "speed"}:${node.id}`); setError(""); setNotice("");
    try {
      const accepted = await runSpeedTest({ node_id: node.id, tester_id: source === "master" ? null : source,
        threads, buf_size: 1_048_576, latency_only: latencyOnly });
      if (!alive.current) return;
      setResults(value => [accepted, ...value.filter(item => item.node_id !== node.id)]);
      setNotice(`${node.name} 已加入${latencyOnly ? "延迟" : "速度"}测试队列。`);
      window.clearTimeout(timer.current); timer.current = window.setTimeout(() => void refresh(true), 1000);
    } catch (failure) { if (alive.current) setError(speedTestError(failure)); }
    finally { if (alive.current) setBusy(""); }
  }
  async function batch() {
    if (busy || !selected.length) return;
    const targets = nodes.filter(node => selected.includes(node.id) && node.enabled);
    setBusy("batch"); setError(""); setNotice("");
    try {
      const accepted = await Promise.all(targets.map(node => runSpeedTest({ node_id: node.id,
        tester_id: source === "master" ? null : source, threads, buf_size: 1_048_576,
        latency_only: false })));
      if (!alive.current) return;
      const ids = new Set(accepted.map(item => item.node_id));
      setResults(value => [...accepted, ...value.filter(item => !ids.has(item.node_id))]);
      setNotice(`已提交 ${accepted.length} 个节点；同一测速来源会串行执行，避免带宽互相干扰。`);
      window.clearTimeout(timer.current); timer.current = window.setTimeout(() => void refresh(true), 1000);
    } catch (failure) { if (alive.current) { setError(speedTestError(failure)); void refresh(true); } }
    finally { if (alive.current) setBusy(""); }
  }
  async function openHistory(node: ManagedNode) {
    setBusy(`history:${node.id}`); setError("");
    try { const rows = await loadSpeedTestHistory(node.id); if (alive.current) { setHistory(rows); setHistoryNode(node.name); } }
    catch (failure) { if (alive.current) setError(speedTestError(failure)); }
    finally { if (alive.current) setBusy(""); }
  }
  async function createTester() {
    const name = testerName.trim(); if (!name || busy) return;
    setBusy("create-tester"); setError("");
    try {
      const value = await createSpeedTester(name); if (!alive.current) return;
      setSecret(value); setCreateOpen(false); setTesterName(""); setTesters(rows => [...rows, value.tester]);
    } catch (failure) { if (alive.current) setError(speedTestError(failure)); }
    finally { if (alive.current) setBusy(""); }
  }
  function rotate(item: SpeedTester) {
    modal.confirm({ title: `轮换 ${item.name} 的配对令牌？`, content: "旧令牌立即失效，当前连接会被断开。新令牌也只显示一次。",
      okText: "轮换令牌", cancelText: "取消", onOk: async () => {
        setBusy(`rotate:${item.id}`); setError("");
        try { const value = await rotateSpeedTester(item.id); if (alive.current) { setSecret(value); void refresh(true); } }
        catch (failure) { if (alive.current) setError(speedTestError(failure)); }
        finally { if (alive.current) setBusy(""); }
      } });
  }
  function revoke(item: SpeedTester) {
    modal.confirm({ title: `撤销测速端 ${item.name}？`, content: "配对令牌和在线连接会立即失效，历史测速记录仍保留。",
      okText: "撤销", okButtonProps: { danger: true }, cancelText: "取消", onOk: async () => {
        setBusy(`revoke:${item.id}`); setError("");
        try { await revokeSpeedTester(item.id); if (alive.current) { setTesters(rows => rows.filter(row => row.id !== item.id)); if (source === item.id) chooseSource("master"); } }
        catch (failure) { if (alive.current) setError(speedTestError(failure)); }
        finally { if (alive.current) setBusy(""); }
      } });
  }
  const sourceOptions = [{ value: "master", label: "主控本机" }, ...testers.map(item => ({
    value: item.id, label: `${item.name}（${item.online ? "在线" : "离线"}）`, disabled: !item.online,
  }))];
  const master = typeof window === "undefined" ? "https://你的主控.example.com" : window.location.origin;
  const officialScript = "https://raw.githubusercontent.com/MMWOrg/mmwX-plugins/7457360b40fb2045a7eeee4b9c68358cdbaf94e4/speedtest/scripts";
  const linuxCommand = secret
    ? `curl -fsSL ${officialScript}/install.sh | bash -s -- -master '${master}' -token '${secret.token}'`
    : "";
  const windowsCommand = secret
    ? `irm ${officialScript}/install.ps1 -OutFile mmwx-speedtester-install.ps1\n.\\mmwx-speedtester-install.ps1 -Master '${master}' -Token '${secret.token}'`
    : "";

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Flex justify="space-between" align="center" gap={16} wrap>
      <div><Typography.Title level={2} style={{ marginBottom: 4 }}>节点测速</Typography.Title><Typography.Text type="secondary">通过真实节点代理测试下行速度、真连接延迟和出口 IP，结果异步保存。</Typography.Text></div>
      <Button icon={<ReloadOutlined />} loading={busy === "load"} onClick={() => void refresh()}>刷新状态</Button>
    </Flex>
    <Alert type="info" showIcon title="测速规则与官方妙妙屋X一致" description="速度测试约 8 秒；真连接延迟使用 Cloudflare 204 三次采样取最快两次均值。同一来源串行执行，单线程看单连接表现，8 线程看聚合带宽。" />
    {error && <Alert type="error" showIcon title={error} role="alert" />}{notice && <Alert type="success" showIcon title={notice} />}
    <Card>
      <Flex gap="middle" align="center" wrap style={{ marginBottom: 16 }}>
        <Select aria-label="测速来源" style={{ minWidth: 220 }} value={source} options={sourceOptions} onChange={chooseSource} />
        <Radio.Group aria-label="测速线程" optionType="button" buttonStyle="solid" value={threads} onChange={event => chooseThreads(event.target.value)} options={[{ value: 1, label: "单线程" }, { value: 8, label: "8 线程" }]} />
        <Button type="primary" icon={<FundOutlined />} loading={busy === "batch"} disabled={!selected.length || Boolean(busy)} onClick={() => void batch()}>批量测速（{selected.length}）</Button>
        {mihomo && <Tag color={mihomo.ready ? "success" : mihomo.supported ? "processing" : "error"}>Mihomo {mihomo.version} · {mihomo.ready ? "已就绪" : mihomo.downloading ? "下载中" : mihomo.supported ? "首次使用时准备" : "不支持"}</Tag>}
      </Flex>
      <Table<ManagedNode> rowKey="id" dataSource={nodes} loading={busy === "load"} scroll={{ x: 1040 }} locale={{ emptyText: "暂无可测速节点" }} rowSelection={{ selectedRowKeys: selected, onChange: setSelected,
        getCheckboxProps: node => ({ disabled: !node.enabled || latest.get(node.id)?.status === "running", "aria-label": `选择节点 ${node.name}` }) }} columns={[
        { title: "节点", dataIndex: "name", width: 200, render: (value, node) => <Space orientation="vertical" size={0}><Typography.Text strong>{value}</Typography.Text><Typography.Text type="secondary">{node.protocol} · {node.enabled ? "已启用" : "已停用"}</Typography.Text></Space> },
        { title: "状态", width: 100, render: (_, node) => resultState(latest.get(node.id)) },
        { title: "下行", width: 125, sorter: (a, b) => (latest.get(a.id)?.down_mbps ?? -1) - (latest.get(b.id)?.down_mbps ?? -1), render: (_, node) => { const item = latest.get(node.id); return item?.down_mbps != null ? `${item.down_mbps.toFixed(2)} Mbps` : "—"; } },
        { title: "真连接延迟", width: 145, sorter: (a, b) => (latest.get(a.id)?.latency_ms ?? 999999) - (latest.get(b.id)?.latency_ms ?? 999999), render: (_, node) => { const item = latest.get(node.id); return item?.latency_ms != null ? `${item.latency_ms.toFixed(1)} ms` : "—"; } },
        { title: "出口 IP", width: 170, render: (_, node) => <Typography.Text copyable={Boolean(latest.get(node.id)?.egress_ip)}>{latest.get(node.id)?.egress_ip || "—"}</Typography.Text> },
        { title: "来源 / 时间", width: 190, render: (_, node) => { const item = latest.get(node.id); return item ? <Space orientation="vertical" size={0}><span>{item.source === "master" ? "主控本机" : item.tester_name || "家用测速端"}</span><Typography.Text type="secondary">{date(item.created_at)}</Typography.Text>{item.status === "failed" && <Typography.Text type="danger">{speedTestStatusMessage(item.error_code)}</Typography.Text>}</Space> : "—"; } },
        { title: "操作", fixed: "right", width: 250, render: (_, node) => { const running = latest.get(node.id)?.status === "running"; return <Space><Button icon={<FundOutlined />} disabled={!node.enabled || running || Boolean(busy)} loading={busy === `speed:${node.id}`} onClick={() => void queue(node, false)}>测速</Button><Button aria-label={`测试延迟 ${node.name}`} icon={<ThunderboltOutlined />} disabled={!node.enabled || running || Boolean(busy)} loading={busy === `latency:${node.id}`} onClick={() => void queue(node, true)}>延迟</Button><Button aria-label={`查看历史 ${node.name}`} icon={<ClockCircleOutlined />} loading={busy === `history:${node.id}`} onClick={() => void openHistory(node)} /></Space>; } },
      ]} />
    </Card>
    <Card title="家用测速端" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建测速端</Button>}>
      <Typography.Paragraph type="secondary">测速端从家庭网络主动通过 WebSocket 反连主控，不需要公网 IP。它会接收完整节点凭据，请只部署在可信设备上。</Typography.Paragraph>
      <Table<SpeedTester> rowKey="id" size="small" dataSource={testers} pagination={false} locale={{ emptyText: "尚未创建家用测速端" }} columns={[
        { title: "名称", dataIndex: "name" },
        { title: "状态", render: (_, item) => <Tag color={item.online ? "success" : "default"}>{item.online ? "在线" : "离线"}</Tag> },
        { title: "版本 / 能力", render: (_, item) => <Space orientation="vertical" size={0}><span>{item.version || "未上报版本"}</span><Typography.Text type="secondary">{item.caps.length ? item.caps.join("、") : "未上报能力"}</Typography.Text></Space> },
        { title: "最近连接", render: (_, item) => date(item.last_seen_at) },
        { title: "操作", width: 190, render: (_, item) => <Space><Button icon={<KeyOutlined />} loading={busy === `rotate:${item.id}`} onClick={() => rotate(item)}>轮换令牌</Button><Button danger aria-label={`撤销测速端 ${item.name}`} icon={<DeleteOutlined />} loading={busy === `revoke:${item.id}`} onClick={() => revoke(item)} /></Space> },
      ]} />
    </Card>
    <Modal title={`测速历史 · ${historyNode}`} open={history !== null} width={900} footer={null} onCancel={() => setHistory(null)} destroyOnHidden>
      <Table<SpeedTestResult> rowKey="id" size="small" dataSource={history ?? []} scroll={{ x: 760 }} pagination={{ pageSize: 10, showSizeChanger: false }} columns={[
        { title: "时间", render: (_, item) => date(item.created_at), sorter: (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) },
        { title: "来源", render: (_, item) => item.source === "master" ? "主控本机" : item.tester_name || "家用测速端" },
        { title: "下行", render: (_, item) => item.down_mbps == null ? "—" : `${item.down_mbps.toFixed(2)} Mbps`, sorter: (a, b) => (a.down_mbps ?? -1) - (b.down_mbps ?? -1) },
        { title: "延迟", render: (_, item) => item.latency_ms == null ? "—" : `${item.latency_ms.toFixed(1)} ms`, sorter: (a, b) => (a.latency_ms ?? 999999) - (b.latency_ms ?? 999999) },
        { title: "流量", render: (_, item) => bytes(item.bytes) }, { title: "出口 IP", dataIndex: "egress_ip", render: value => value || "—" },
        { title: "结果", render: (_, item) => item.status === "failed" ? <Typography.Text type="danger">{speedTestStatusMessage(item.error_code)}</Typography.Text> : resultState(item) },
      ]} />
    </Modal>
    <Modal title="创建家用测速端" open={createOpen} onCancel={() => !busy && setCreateOpen(false)} onOk={() => void createTester()} confirmLoading={busy === "create-tester"} okButtonProps={{ disabled: !testerName.trim() }} okText="创建" destroyOnHidden>
      <Form layout="vertical"><Form.Item label="测速端名称" required><Input autoFocus maxLength={120} value={testerName} placeholder="例如：上海家庭宽带" onChange={event => setTesterName(event.target.value)} /></Form.Item></Form>
    </Modal>
    <Modal title={`一次性配对信息 · ${secret?.tester.name ?? ""}`} open={Boolean(secret)} width={760} footer={<Button type="primary" onClick={() => setSecret(null)}>我已保存，关闭</Button>} closable={false} maskClosable={false} keyboard={false} destroyOnHidden>
      <Alert type="warning" showIcon title="令牌只显示这一次" description="关闭后无法找回；如丢失，请轮换令牌。轮换会使旧令牌和当前连接立即失效。" />
      <Descriptions column={1} bordered size="small" style={{ marginTop: 16 }} items={[
        { key: "master", label: "主控地址", children: <Typography.Text copyable>{master}</Typography.Text> },
        { key: "ws", label: "WebSocket 路径", children: <Typography.Text code>{secret?.websocket_path}</Typography.Text> },
        { key: "token", label: "配对令牌", children: <Typography.Text copyable code>{secret?.token}</Typography.Text> },
      ]} />
      <Typography.Title level={5}>Linux / macOS 官方一键命令</Typography.Title>
      <Input.TextArea aria-label="Linux 家用测速端安装命令" readOnly autoSize={{ minRows: 2, maxRows: 4 }} value={linuxCommand} />
      <Typography.Title level={5}>Windows PowerShell 官方一键命令</Typography.Title>
      <Input.TextArea aria-label="Windows 家用测速端安装命令" readOnly autoSize={{ minRows: 3, maxRows: 5 }} value={windowsCommand} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}><HomeOutlined /> 命令固定引用官方 speedtest-v0.1.5 对应提交中的安装脚本；脚本会从 <Typography.Link href="https://github.com/MMWOrg/mmwX-plugins/releases/latest" target="_blank" rel="noreferrer">官方 mmwX-plugins 发布页</Typography.Link> 下载当前平台二进制并启动。若需要长期后台运行，请按所在系统配置 systemd 或开机自启。</Typography.Paragraph>
    </Modal>
  </Space>;
}
