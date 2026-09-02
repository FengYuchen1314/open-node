import {
  CopyOutlined,
  DeleteOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  ShareAltOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Flex,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useEffect, useRef, useState } from "react";

import type { ServerSummary } from "../../domain/inventory";
import type {
  FederatedServer,
  FederationCommand,
  ServerShare,
} from "../../domain/server-sharing";
import { listServers } from "../../services/inventory";
import {
  addFederatedServer,
  createServerShare,
  deleteFederatedServer,
  getFederatedCommand,
  listFederatedServers,
  listServerShares,
  manageFederatedServer,
  refreshFederatedServer,
  revokeServerShare,
  serverSharingErrorMessage,
} from "../../services/server-sharing";

const pending = new Set(["waiting", "pending", "leased"]);
const statusText = { pending: "等待连接", connected: "已连接", offline: "离线" } as const;
const commandStatus = {
  waiting: "等待前置命令", pending: "等待下发", leased: "执行中", succeeded: "成功",
  failed: "失败", skipped: "已跳过",
} as const;
function date(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}
function speed(value: number) {
  if (value < 1024) return `${value} B/s`;
  const units = ["KB/s", "MB/s", "GB/s"]; let amount = value / 1024, unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`;
}

export default function ServerSharingView() {
  const active = useRef(true), sequence = useRef(0), poll = useRef<number | undefined>(undefined);
  const [servers, setServers] = useState<ServerSummary[]>([]), [selected, setSelected] = useState("");
  const [shares, setShares] = useState<ServerShare[]>([]), [federated, setFederated] = useState<FederatedServer[]>([]);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false), [shareDraft, setShareDraft] = useState({ label: "", full: false });
  const [createdToken, setCreatedToken] = useState<{ label: string; value: string } | null>(null);
  const [revoke, setRevoke] = useState<{ item: ServerShare; deleteInbounds: boolean } | null>(null);
  const [addOpen, setAddOpen] = useState(false), [addDraft, setAddDraft] = useState({ owner_url: "", share_token: "", name: "", prefix: "" });
  const [remove, setRemove] = useState<FederatedServer | null>(null);
  const [manage, setManage] = useState<FederatedServer | null>(null);
  const [commandDraft, setCommandDraft] = useState({ method: "GET" as "GET" | "POST", path: "/api/child/inbounds", body: "", timeout: 30000 });
  const [result, setResult] = useState<FederationCommand | null>(null);

  function report(failure: unknown) { setError(serverSharingErrorMessage(failure)); }
  async function load(serverId = selected) {
    const run = ++sequence.current; setBusy("read"); setError("");
    try {
      const [inventory, imported] = await Promise.all([listServers(), listFederatedServers()]);
      if (!active.current || run !== sequence.current) return;
      const shareable = inventory.filter(item => !item.is_federated);
      const target = shareable.some(item => item.id === serverId) ? serverId : shareable[0]?.id ?? "";
      setServers(shareable); setSelected(target); setFederated(imported.servers);
      const owned = target ? await listServerShares(target) : { shares: [] as ServerShare[] };
      if (active.current && run === sequence.current) setShares(owned.shares);
    } catch (failure) { if (active.current && run === sequence.current) report(failure); }
    finally { if (active.current && run === sequence.current) setBusy(""); }
  }
  async function loadShares(serverId: string) {
    const run = ++sequence.current; setSelected(serverId); setBusy("shares"); setError("");
    try { const value = await listServerShares(serverId); if (active.current && run === sequence.current) setShares(value.shares); }
    catch (failure) { if (active.current && run === sequence.current) report(failure); }
    finally { if (active.current && run === sequence.current) setBusy(""); }
  }
  useEffect(() => {
    active.current = true; void load();
    return () => { active.current = false; sequence.current += 1; window.clearTimeout(poll.current); };
  }, []);

  async function create() {
    if (!selected || busy) return;
    const target = selected; setBusy("create"); setError(""); setNotice("");
    try {
      const value = await createServerShare(target, shareDraft.label.trim(), shareDraft.full);
      if (!active.current || selected !== target) return;
      setShares(previous => [value.share, ...previous]); setCreateOpen(false);
      setShareDraft({ label: "", full: false }); setCreatedToken({ label: value.share.label, value: value.share_token });
    } catch (failure) { if (active.current) report(failure); }
    finally { if (active.current) setBusy(""); }
  }
  async function revokeCurrent() {
    if (!revoke || busy) return;
    const current = revoke; setBusy("revoke"); setError(""); setNotice("");
    try {
      const value = await revokeServerShare(current.item, current.deleteInbounds);
      if (!active.current) return;
      setShares(previous => previous.filter(item => item.id !== current.item.id)); setRevoke(null);
      setNotice(value.cleanup_commands.length ? `分享已吊销，已创建 ${value.cleanup_commands.length} 条入站清理命令。` : "分享已吊销。后续请求已立即失效。");
    } catch (failure) { if (active.current) { report(failure); void loadShares(selected); } }
    finally { if (active.current) setBusy(""); }
  }
  async function add() {
    if (busy || !addDraft.owner_url.trim() || !/^[A-Za-z0-9_-]{43}$/.test(addDraft.share_token.trim())) return;
    const draft = { ...addDraft, owner_url: addDraft.owner_url.trim(), share_token: addDraft.share_token.trim(), name: addDraft.name.trim(), prefix: addDraft.prefix.trim() };
    setBusy("add"); setError(""); setNotice("");
    try {
      const value = await addFederatedServer(draft); if (!active.current) return;
      setFederated(previous => [...previous, value]); setAddDraft({ owner_url: "", share_token: "", name: "", prefix: "" }); setAddOpen(false); setNotice("共享服务器已接入，令牌已加密保存。");
    } catch (failure) { if (active.current) { setAddDraft(previous => ({ ...previous, share_token: "" })); report(failure); } }
    finally { if (active.current) setBusy(""); }
  }
  async function refresh(item: FederatedServer) {
    if (busy) return; setBusy(`refresh:${item.id}`); setError("");
    try {
      const value = await refreshFederatedServer(item); if (!active.current) return;
      setFederated(previous => previous.map(row => row.id === value.id ? value : row));
      let command = await manageFederatedServer(value, { method: "GET", path: "/api/child/inbounds", body: null, timeout_ms: 30000 });
      for (let attempt = 0; active.current && pending.has(command.status) && attempt < 60; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        command = await getFederatedCommand(value, command.id);
      }
      if (command.failed || pending.has(command.status)) throw new Error("server_share_owner_unavailable");
      if (active.current) setNotice("共享服务器状态与获授权入站已同步，可在“节点管理”中创建或导入节点。");
    } catch (failure) { if (active.current) { report(failure); void load(); } }
    finally { if (active.current) setBusy(""); }
  }
  async function removeCurrent() {
    if (!remove || busy) return; const current = remove; setBusy("delete"); setError("");
    try {
      await deleteFederatedServer(current); if (!active.current) return;
      setFederated(previous => previous.filter(item => item.id !== current.id)); setRemove(null); setNotice("本地主控已移除这台共享服务器；拥有方分享未被吊销。");
    } catch (failure) { if (active.current) { report(failure); void load(); } }
    finally { if (active.current) setBusy(""); }
  }
  async function pollCommand(item: FederatedServer, command: FederationCommand) {
    if (!pending.has(command.status) || !active.current) return;
    window.clearTimeout(poll.current);
    poll.current = window.setTimeout(async () => {
      try {
        const value = await getFederatedCommand(item, command.id);
        if (!active.current || manage?.id !== item.id) return;
        setResult(value); if (pending.has(value.status)) void pollCommand(item, value);
      } catch (failure) { if (active.current) report(failure); }
    }, 1000);
  }
  async function submitCommand() {
    if (!manage || busy || !commandDraft.path.startsWith("/api/child/")) return;
    let body: Record<string, unknown> | null = null;
    if (commandDraft.method === "POST" && commandDraft.body.trim()) {
      try {
        const parsed = JSON.parse(commandDraft.body) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
        body = parsed as Record<string, unknown>;
      } catch { setError("请求体必须是 JSON 对象。不会发送当前内容。"); return; }
    }
    const target = manage; setBusy("manage"); setError(""); setResult(null);
    try {
      const value = await manageFederatedServer(target, { method: commandDraft.method, path: commandDraft.path.trim(), body, timeout_ms: commandDraft.timeout });
      if (!active.current || manage?.id !== target.id) return;
      setResult(value); void pollCommand(target, value);
    } catch (failure) { if (active.current) report(failure); }
    finally { if (active.current) setBusy(""); }
  }
  function openManage(item: FederatedServer) {
    window.clearTimeout(poll.current); setManage(item); setResult(null); setError("");
    setCommandDraft({ method: "GET", path: "/api/child/inbounds", body: "", timeout: 30000 });
  }

  return <Flex vertical gap="middle" className="page-shell">
    <div><Typography.Title level={2}>服务器共享</Typography.Title><Typography.Paragraph type="secondary">以一次性令牌把服务器授权给另一台主控，或接入别人分享的服务器。功能免费，不需要许可证。</Typography.Paragraph></div>
    <Alert type="warning" showIcon title="分享令牌只在创建时显示一次。完整 Xray 权限可以读取和修改整台服务器配置，只授予可信主控。" />
    {error && <Alert type="error" showIcon role="alert" title={error} />}{notice && <Alert type="success" showIcon role="status" title={notice} />}
    <Tabs items={[
      { key: "owned", label: "我分享的服务器", children: <Card title="分享授权" extra={<Space><Button icon={<ReloadOutlined />} loading={busy === "read" || busy === "shares"} disabled={Boolean(busy)} onClick={() => void loadShares(selected)}>重新读取</Button><Button type="primary" icon={<ShareAltOutlined />} disabled={!selected || Boolean(busy)} onClick={() => setCreateOpen(true)}>创建分享</Button></Space>}>
        <Form layout="vertical"><Form.Item label="服务器"><Select aria-label="分享服务器" value={selected || undefined} disabled={Boolean(busy)} options={servers.map(item => ({ value: item.id, label: item.name }))} onChange={value => void loadShares(value)} /></Form.Item></Form>
        <Table rowKey="id" pagination={false} dataSource={shares} columns={[
          { title: "标签", key: "label", render: (_, item) => item.label || "未命名分享" },
          { title: "权限", key: "scope", render: (_, item) => item.allow_manage_xray ? <Tag color="warning">完整 Xray 管理</Tag> : <Tag color="blue">仅自己的入站</Tag> },
          { title: "创建时间", dataIndex: "created_at", render: date },
          { title: "操作", key: "action", render: (_, item) => <Button danger icon={<DeleteOutlined />} disabled={Boolean(busy)} onClick={() => setRevoke({ item, deleteInbounds: true })}>吊销</Button> },
        ]} locale={{ emptyText: selected ? "这台服务器还没有有效分享" : "请先创建服务器" }} />
      </Card> },
      { key: "imported", label: "接入的共享服务器", children: <Card title="联邦服务器" extra={<Space><Button icon={<ReloadOutlined />} disabled={Boolean(busy)} onClick={() => void load()}>重新读取</Button><Button type="primary" icon={<PlusOutlined />} disabled={Boolean(busy)} onClick={() => setAddOpen(true)}>接入服务器</Button></Space>}>
        <Table rowKey="id" pagination={false} dataSource={federated} expandable={{ expandedRowRender: item => <Descriptions size="small" column={{ xs: 1, md: 2 }} items={[
          { key: "owner", label: "拥有方", children: item.owner_url }, { key: "prefix", label: "入站前缀", children: item.prefix || "无" },
          { key: "address", label: "地址", children: item.info.domain || item.info.ip_address || "未提供" }, { key: "heartbeat", label: "拥有方心跳", children: date(item.info.last_heartbeat) },
          { key: "xray", label: "Xray", children: item.info.xray_running == null ? "未知" : item.info.xray_running ? `运行中 ${item.info.xray_version ?? ""}` : "已停止" },
          { key: "nginx", label: "Nginx", children: !item.info.nginx ? "未知" : item.info.nginx.running ? `运行中 ${item.info.nginx.version ?? ""}` : item.info.nginx.installed ? "已停止" : "未安装" },
          { key: "sync", label: "同步时间", children: date(item.last_synced_at) },
        ]} /> }} columns={[
          { title: "服务器", key: "name", render: (_, item) => <Space orientation="vertical" size={0}><Typography.Text strong>{item.name}</Typography.Text><Typography.Text type="secondary">{item.owner_url}</Typography.Text></Space> },
          { title: "状态", key: "status", render: (_, item) => <Tag color={item.info.status === "connected" ? "success" : item.info.status === "offline" ? "error" : "default"}>{statusText[item.info.status]}</Tag> },
          { title: "实时速率", key: "speed", render: (_, item) => `${speed(item.info.current_upload_speed)} ↑ / ${speed(item.info.current_download_speed)} ↓` },
          { title: "操作", key: "action", render: (_, item) => <Space wrap><Button icon={<LinkOutlined />} disabled={Boolean(busy)} onClick={() => openManage(item)}>管理</Button><Button icon={<ReloadOutlined />} loading={busy === `refresh:${item.id}`} disabled={Boolean(busy)} onClick={() => void refresh(item)}>同步状态与入站</Button><Button danger icon={<DeleteOutlined />} disabled={Boolean(busy)} onClick={() => setRemove(item)}>移除</Button></Space> },
        ]} locale={{ emptyText: "还没有接入共享服务器" }} />
      </Card> },
    ]} />

    <Modal open={createOpen} title="创建服务器分享" okText="创建并显示令牌" cancelText="取消" confirmLoading={busy === "create"} okButtonProps={{ disabled: !selected || Boolean(busy) }} onOk={() => void create()} onCancel={() => !busy && setCreateOpen(false)} destroyOnHidden>
      <Form layout="vertical"><Form.Item label="用途标签"><Input aria-label="分享用途标签" maxLength={80} value={shareDraft.label} onChange={event => setShareDraft(value => ({ ...value, label: event.target.value }))} /></Form.Item><Checkbox checked={shareDraft.full} onChange={event => setShareDraft(value => ({ ...value, full: event.target.checked }))}>允许接收方查看和修改完整 Xray 配置</Checkbox></Form>
    </Modal>
    <Modal open={!!createdToken} title="保存分享令牌" footer={<Button type="primary" onClick={() => setCreatedToken(null)}>我已安全保存</Button>} closable={false} mask={{ closable: false }} destroyOnHidden>
      <Alert type="warning" showIcon title="关闭后无法再次查看；丢失时请吊销并重新创建。" /><Form.Item label={createdToken?.label || "分享令牌"} style={{ marginTop: 16 }}><Space.Compact block><Input aria-label="一次性分享令牌" readOnly value={createdToken?.value ?? ""} /><Button aria-label="复制分享令牌" icon={<CopyOutlined />} onClick={() => createdToken && void navigator.clipboard.writeText(createdToken.value)} /></Space.Compact></Form.Item>
    </Modal>
    <Modal open={!!revoke} title="吊销服务器分享" okText="确认吊销" cancelText="返回" okButtonProps={{ danger: true, disabled: Boolean(busy) }} confirmLoading={busy === "revoke"} onOk={() => void revokeCurrent()} onCancel={() => !busy && setRevoke(null)}>
      <Typography.Paragraph>吊销后，接收方令牌立即失效且无法恢复。</Typography.Paragraph><Checkbox checked={revoke?.deleteInbounds ?? true} onChange={event => setRevoke(value => value ? { ...value, deleteInbounds: event.target.checked } : value)}>同时删除此分享创建的入站，使已分发节点失效</Checkbox>
    </Modal>
    <Modal open={addOpen} title="接入共享服务器" okText="验证并接入" cancelText="取消" confirmLoading={busy === "add"} okButtonProps={{ disabled: Boolean(busy) || !addDraft.owner_url.trim() || !/^[A-Za-z0-9_-]{43}$/.test(addDraft.share_token.trim()) }} onOk={() => void add()} onCancel={() => { if (!busy) { setAddOpen(false); setAddDraft(value => ({ ...value, share_token: "" })); } }} destroyOnHidden>
      <Form layout="vertical"><Form.Item label="拥有方公网 HTTPS 地址" required><Input aria-label="拥有方地址" placeholder="https://owner.example.com" value={addDraft.owner_url} onChange={event => setAddDraft(value => ({ ...value, owner_url: event.target.value }))} /></Form.Item><Form.Item label="分享令牌" required><Input.Password aria-label="接入分享令牌" autoComplete="off" value={addDraft.share_token} onChange={event => setAddDraft(value => ({ ...value, share_token: event.target.value }))} /></Form.Item><Form.Item label="本地名称"><Input aria-label="共享服务器名称" maxLength={120} value={addDraft.name} onChange={event => setAddDraft(value => ({ ...value, name: event.target.value }))} /></Form.Item><Form.Item label="入站标签前缀" help="可用于避免多个主控创建同名入站。"><Input aria-label="入站标签前缀" maxLength={40} value={addDraft.prefix} onChange={event => setAddDraft(value => ({ ...value, prefix: event.target.value }))} /></Form.Item></Form>
    </Modal>
    <Modal open={!!remove} title="移除共享服务器" okText="仅从本地主控移除" cancelText="返回" okButtonProps={{ danger: true, disabled: Boolean(busy) }} confirmLoading={busy === "delete"} onOk={() => void removeCurrent()} onCancel={() => !busy && setRemove(null)}><Typography.Paragraph>这不会吊销拥有方创建的分享，也不会删除远端入站。如需彻底停用，请联系拥有方吊销令牌。</Typography.Paragraph></Modal>
    <Modal width={760} open={!!manage} title={`管理共享服务器${manage ? `：${manage.name}` : ""}`} okText="发送命令" cancelText="关闭" confirmLoading={busy === "manage"} okButtonProps={{ disabled: Boolean(busy) || !commandDraft.path.startsWith("/api/child/") }} onOk={() => void submitCommand()} onCancel={() => { if (!busy) { window.clearTimeout(poll.current); setManage(null); setResult(null); } }} destroyOnHidden>
      <Alert type="info" showIcon title="受限分享只能列出和管理本分享创建的入站；完整权限由拥有方创建分享时决定。" />
      <Form layout="vertical" style={{ marginTop: 16 }}><Form.Item label="方法"><Select aria-label="联邦命令方法" value={commandDraft.method} options={[{ value: "GET", label: "GET" }, { value: "POST", label: "POST" }]} onChange={method => setCommandDraft(value => ({ ...value, method }))} /></Form.Item><Form.Item label="Agent 路径" validateStatus={commandDraft.path.startsWith("/api/child/") ? undefined : "error"} help="必须以 /api/child/ 开头。"><Input aria-label="联邦 Agent 路径" maxLength={255} value={commandDraft.path} onChange={event => setCommandDraft(value => ({ ...value, path: event.target.value }))} /></Form.Item>{commandDraft.method === "POST" && <Form.Item label="JSON 请求体"><Input.TextArea aria-label="联邦命令 JSON" rows={7} value={commandDraft.body} onChange={event => setCommandDraft(value => ({ ...value, body: event.target.value }))} /></Form.Item>}</Form>
      {result && <Card size="small" title="命令结果" extra={<Tag color={result.failed ? "error" : result.status === "succeeded" ? "success" : "processing"}>{commandStatus[result.status]}</Tag>}><Typography.Paragraph type="secondary">HTTP 状态：{result.result_status ?? "等待中"}</Typography.Paragraph><Input.TextArea aria-label="联邦命令结果" readOnly autoSize={{ minRows: 3, maxRows: 12 }} value={result.result_body == null ? "" : JSON.stringify(result.result_body, null, 2)} /></Card>}
    </Modal>
  </Flex>;
}
