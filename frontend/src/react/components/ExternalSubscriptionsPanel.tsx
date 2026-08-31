import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Checkbox, Descriptions, Empty, Flex, Form, Input, Modal, Select, Spin, Switch, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ExternalNodeRead, ExternalPreviewConfirm, ExternalPreviewNode, ExternalPreviewRead,
  ExternalSourceDetail, ExternalSourceRead,
} from "../../domain/external-subscriptions";
import type { ProductUser } from "../../domain/subscriptions";
import {
  cancelExternalPreview, confirmExternalPreview, createExternalPreview, createExternalSource, deleteExternalSource,
  ExternalSubscriptionsError, externalSubscriptionsErrorMessage, getExternalPreview, getExternalSource,
  listExternalSources, updateExternalNode, updateExternalSource,
} from "../../services/external-subscriptions";
import { useAsyncScope } from "../hooks/useAsyncScope";

export interface ExternalSubscriptionsPanelProps {
  users: ProductUser[];
  /** Inactive tabs must destroy write-only credentials and pending preview UI. */
  active?: boolean;
  onUpdated?: () => void;
}

const defaultAgent = "clash-meta/2.4.0";
const modalStyle = { maxWidth: "calc(100vw - 24px)" };
const modalBody = { maxHeight: "65dvh", overflowY: "auto" as const };
const wrapText = { overflowWrap: "anywhere" as const };
const cleanName = (value: string) => value.trim().normalize("NFC");
const validName = (value: string) => !!cleanName(value) && [...cleanName(value)].length <= 160 && !/[\u0000-\u001f\u007f]/.test(cleanName(value));
const validAgent = (value: string) => value.length > 0 && value.length <= 256 && /^[\x20-\x7e]+$/.test(value);
const date = (value: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN") : "尚未同步";
const conflict = (failure: unknown) => failure instanceof ExternalSubscriptionsError && failure.status === 409;
const selectable = (node: ExternalPreviewNode) => node.change === "new" && node.selectable && !node.existing;

export default function ExternalSubscriptionsPanel({ active = true, ...props }: ExternalSubscriptionsPanelProps) {
  return active ? <ExternalSubscriptionsContent {...props} /> : null;
}

function ExternalSubscriptionsContent({ users, onUpdated }: Omit<ExternalSubscriptionsPanelProps, "active">) {
  const scope = useAsyncScope();
  const [sources, setSources] = useState<ExternalSourceRead[]>([]);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [owner, setOwner] = useState<string | undefined>(), [selectedId, setSelectedId] = useState("");
  const [creating, setCreating] = useState(false);
  const load = useCallback(async () => {
    const run = scope.begin(); setLoading(true); setError("");
    try {
      const value = await listExternalSources();
      if (!scope.isCurrent(run)) return;
      setSources(value.sources);
      setSelectedId(previous => value.sources.some(source => source.id === previous) ? previous : "");
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) setLoading(false); }
  }, [scope]);
  useEffect(() => { void load(); }, [load]);

  function changed(source?: ExternalSourceRead) {
    scope.invalidate(); setLoading(false);
    if (source) setSources(previous => [source, ...previous.filter(item => item.id !== source.id)]);
    else void load();
    onUpdated?.();
  }
  function removed(sourceId: string) {
    scope.invalidate(); setLoading(false); setSelectedId("");
    setSources(previous => previous.filter(source => source.id !== sourceId)); onUpdated?.();
  }
  const selected = sources.find(source => source.id === selectedId);
  const visible = sources.filter(source => owner === undefined || source.owner_username === owner);
  const ownerOptions = [...new Set([...users.map(user => user.username), ...sources.map(source => source.owner_username)])].map(username => ({ label: username, value: username }));
  return <section aria-label="外部订阅" data-testid="external-subscriptions-panel" style={{ minWidth: 0 }}><Flex vertical gap="middle">
    <Card size="small" title="外部订阅"><Flex vertical gap="middle">
      <Alert type="info" showIcon title="仍须满足本地订阅使用条件" description="已确认的外部节点仅加入所属用户的主订阅，并受本地套餐、有效期和配额检查约束。临时链接和命名订阅配置不会自动包含这些节点。上游流量仅供展示，不会改变本地用量计费。" />
      <Typography.Paragraph type="secondary">本阶段通过手动预览导入 Clash/Mihomo YAML。保存来源、打开此页面和下载订阅都不会抓取上游链接。此处尚未启用 URI/Base64 输入和定时刷新。</Typography.Paragraph>
      <Flex gap="small" wrap>
        <Button type="primary" icon={<PlusOutlined aria-hidden />} aria-label="添加外部订阅来源" disabled={!users.some(user => !user.removal_id)} onClick={() => { setSelectedId(""); setCreating(true); }}>添加来源</Button>
        <Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新外部订阅来源" loading={loading} onClick={() => void load()}>刷新来源</Button>
      </Flex>
      <Form layout="vertical"><Form.Item label="按所属用户筛选"><Select aria-label="按所属用户筛选外部订阅来源" allowClear placeholder="所有用户" value={owner} options={ownerOptions} onChange={value => { setOwner(value); setSelectedId(""); setCreating(false); }} /></Form.Item></Form>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}
      <Table<ExternalSourceRead> rowKey="id" size="small" loading={loading} dataSource={visible} scroll={{ x: 650 }} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: "暂无外部订阅来源。" }} columns={[
        { title: "来源", dataIndex: "name", width: 210, render: value => <Typography.Text style={wrapText}>{value}</Typography.Text> },
        { title: "所属用户", dataIndex: "owner_username", width: 150, render: value => <Typography.Text style={wrapText}>{value}</Typography.Text> },
        { title: "状态", width: 100, render: (_, source) => <Tag color={source.enabled ? "success" : "default"}>{source.enabled ? "已启用" : "已停用"}</Tag> },
        { title: "节点", width: 80, render: (_, source) => `${source.available_node_count}/${source.node_count}` },
        { title: "操作", width: 110, render: (_, source) => <Button aria-label={`查看外部订阅来源 ${source.name}`} onClick={() => { setCreating(false); setSelectedId(source.id); }}>详情</Button> },
      ]} />
    </Flex></Card>
    {selected ? <SourceDetails key={selected.id} sourceId={selected.id} users={users} ownerUsername={selected.owner_username} onClose={() => setSelectedId("")} onUpdated={changed} onDeleted={() => removed(selected.id)} /> : <Empty description="请选择来源以查看节点或准备预览。" />}
    {creating && <SourceEditor open users={users} onOpenChange={open => setCreating(open)} onSaved={source => { setCreating(false); changed(source); setOwner(undefined); setSelectedId(source.id); }} />}
  </Flex></section>;
}

function UpstreamMetadata({ metadata }: { metadata: Record<string, number> }) {
  const unavailable = "未报告或超出安全显示范围";
  const bytes = (key: string) => metadata[key] === undefined ? unavailable : `${metadata[key]!.toLocaleString("zh-CN")} 字节`;
  const expires = metadata.expire && Number.isFinite(new Date(metadata.expire * 1000).getTime()) ? date(new Date(metadata.expire * 1000).toISOString()) : unavailable;
  return <Descriptions title="上游信息（不参与本地计费）" column={1} size="small" items={[
    { key: "upload", label: "上游上传流量", children: bytes("upload") },
    { key: "download", label: "上游下载流量", children: bytes("download") },
    { key: "total", label: "上游流量额度", children: bytes("total") },
    { key: "expire", label: "上游到期时间", children: expires },
  ]} />;
}

interface SourceDetailsProps {
  sourceId: string; ownerUsername: string; users: ProductUser[];
  onClose: () => void; onUpdated: (source?: ExternalSourceRead) => void; onDeleted: () => void;
}
function SourceDetails({ sourceId, ownerUsername, users, onClose, onUpdated, onDeleted }: SourceDetailsProps) {
  const scope = useAsyncScope();
  const [detail, setDetail] = useState<ExternalSourceDetail | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [editing, setEditing] = useState(false), [deleting, setDeleting] = useState(false), [previewOpen, setPreviewOpen] = useState(false);
  const [editingNode, setEditingNode] = useState<ExternalNodeRead | null>(null);
  const writable = users.some(user => user.username === ownerUsername && !user.removal_id);
  const load = useCallback(async () => {
    const run = scope.begin(); setLoading(true); setError("");
    try { const value = await getExternalSource(sourceId); if (scope.isCurrent(run)) setDetail(value); }
    catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) setLoading(false); }
  }, [scope, sourceId]);
  useEffect(() => { void load(); }, [load]);
  function acceptRead(value: ExternalSourceDetail) { scope.invalidate(); setLoading(false); setDetail(value); }
  function close() { scope.invalidate(); onClose(); }
  return <Card size="small" title="来源详情" data-testid="external-source-detail"><Flex vertical gap="middle">
    <Flex gap="small" wrap><Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新外部订阅来源详情" loading={loading} onClick={() => void load()}>刷新详情</Button><Button aria-label="关闭外部订阅来源详情" onClick={close}>关闭详情</Button></Flex>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{loading && <Spin size="small" />}
    {!writable && <Alert type="warning" showIcon title="所属用户不存在或正在移除，暂时无法修改来源。" />}
    {detail && <>
      <Descriptions size="small" column={1} items={[
        { key: "name", label: "名称", children: <span style={wrapText}>{detail.source.name}</span> },
        { key: "owner", label: "所属用户（固定）", children: <span style={wrapText}>{detail.source.owner_username}</span> },
        { key: "status", label: "来源状态", children: <Tag color={detail.source.enabled ? "success" : "default"}>{detail.source.enabled ? "已启用" : "已停用"}</Tag> },
        { key: "revision", label: "来源版本", children: detail.source.revision },
        { key: "agent", label: "User-Agent", children: detail.source.has_custom_user_agent ? "自定义（不显示）" : `默认（${defaultAgent}）` },
        { key: "nodes", label: "可用节点 / 已保存节点", children: `${detail.source.available_node_count} / ${detail.source.node_count}` },
        { key: "synced", label: "上次确认同步时间", children: date(detail.source.last_synced_at) },
      ]} />
      <Typography.Paragraph type="secondary">已保存的链接和自定义 User-Agent 只可写入，不能读回。更换链接后，原先确认的节点会保留，直到手动预览并确认新内容。停用或删除来源只会停止后续分发，无法撤回客户端已下载的上游凭据。</Typography.Paragraph>
      <Flex gap="small" wrap>
        <Button icon={<EditOutlined aria-hidden />} aria-label="编辑外部订阅来源" disabled={loading || !writable} onClick={() => setEditing(true)}>编辑来源</Button>
        <Button type="primary" aria-label="预览外部订阅来源" disabled={loading || !writable} onClick={() => setPreviewOpen(true)}>预览 / 恢复回执</Button>
        <Button danger icon={<DeleteOutlined aria-hidden />} aria-label="删除外部订阅来源" disabled={loading || !writable} onClick={() => setDeleting(true)}>删除来源</Button>
      </Flex>
      <UpstreamMetadata metadata={detail.source.metadata} />
      <Table<ExternalNodeRead> rowKey="id" size="small" dataSource={detail.nodes} scroll={{ x: 720 }} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: "尚无已确认的外部节点。请抓取并确认预览后再导入。" }} columns={[
        { title: "节点", width: 220, render: (_, node) => <><Typography.Text style={wrapText}>{node.name}</Typography.Text><div><Typography.Text type="secondary" style={wrapText}>上游：{node.upstream_name}</Typography.Text></div></> },
        { title: "协议", dataIndex: "protocol", width: 100 },
        { title: "状态", width: 150, render: (_, node) => <Flex gap="small" wrap><Tag>{node.enabled ? "已启用" : "已停用"}</Tag><Tag color={node.available ? "success" : "warning"}>{node.available ? "可用" : node.present ? "不可用" : "缺失"}</Tag></Flex> },
        { title: "原因", dataIndex: "reason", width: 160, render: value => <span style={wrapText}>{value ? zhMessage(value) : "—"}</span> },
        { title: "操作", width: 90, render: (_, node) => <Button aria-label={`编辑外部节点 ${node.name}`} icon={<EditOutlined aria-hidden />} disabled={loading || !writable} onClick={() => setEditingNode(node)}>编辑</Button> },
      ]} />
      {writable && editing && <SourceEditor open source={detail.source} users={users} onOpenChange={setEditing} onRead={acceptRead} onSaved={source => { setEditing(false); scope.invalidate(); onUpdated(source); void load(); }} />}
      {writable && deleting && <SourceDeletion open source={detail.source} onOpenChange={setDeleting} onRead={acceptRead} onDeleted={onDeleted} />}
      {writable && editingNode && <NodeEditor key={editingNode.id} open source={detail.source} node={editingNode} onOpenChange={open => { if (!open) setEditingNode(null); }} onRead={acceptRead} onSaved={value => { setEditingNode(null); acceptRead(value); onUpdated(value.source); }} />}
      {writable && previewOpen && <PreviewDialog open source={detail.source} onOpenChange={setPreviewOpen} onRead={acceptRead} onApplied={() => { onUpdated(); void load(); }} />}
    </>}
  </Flex></Card>;
}

interface SourceEditorProps {
  open: boolean; source?: ExternalSourceRead; users: ProductUser[]; onOpenChange: (open: boolean) => void;
  onSaved: (source: ExternalSourceRead) => void; onRead?: (detail: ExternalSourceDetail) => void;
}
function SourceEditor(props: SourceEditorProps) { return props.open ? <SourceEditorContent {...props} /> : null; }
function SourceEditorContent({ source, users, onOpenChange, onSaved, onRead }: SourceEditorProps) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [owner, setOwner] = useState(source?.owner_username ?? ""), [name, setName] = useState(source?.name ?? ""), [enabled, setEnabled] = useState(source?.enabled ?? true);
  const [url, setUrl] = useState(""), [agent, setAgent] = useState(""), [agentMode, setAgentMode] = useState<"keep" | "default" | "replace">(source ? "keep" : "default");
  const [revision, setRevision] = useState(source?.revision ?? 1), [latest, setLatest] = useState<ExternalSourceRead | null>(null);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [stale, setStale] = useState(false);
  const ownerExists = users.some(user => user.username === owner && !user.removal_id);
  useEffect(() => {
    if (!owner || ownerExists) return;
    scope.invalidate(); busyRef.current = false; setBusy(""); setUrl(""); setAgent("");
    setError("所选用户不存在或正在移除，请选择可用用户。");
  }, [owner, ownerExists, scope]);
  const canSave = !busy && !stale && validName(name) && ownerExists && (source || !!url.trim()) && (agentMode !== "replace" || validAgent(agent));
  function close() { scope.invalidate(); setUrl(""); setAgent(""); onOpenChange(false); }
  async function save() {
    if (!canSave || busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy("save"); setError("");
    const replacementUrl = url.trim(), userAgent = agentMode === "keep" ? null : agentMode === "default" ? "" : agent;
    setUrl(""); setAgent("");
    try {
      const result = source
        ? await updateExternalSource(source.id, { expected_revision: revision, name: cleanName(name), enabled, url: replacementUrl || null, user_agent: userAgent })
        : await createExternalSource({ owner_username: owner, name: cleanName(name), enabled, url: replacementUrl, user_agent: userAgent ?? "" });
      if (scope.isCurrent(run)) onSaved(result);
    } catch (failure) { if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(conflict(failure)); } }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  async function refreshRevision() {
    if (!source || busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy("refresh"); setUrl(""); setAgent("");
    try {
      const value = await getExternalSource(source.id);
      if (!scope.isCurrent(run)) return;
      setLatest(value.source); setRevision(value.source.revision); setStale(false); setError(""); onRead?.(value);
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  return <Modal open title={source ? "编辑外部订阅来源" : "添加外部订阅来源"} width={600} style={modalStyle} styles={{ body: modalBody }} destroyOnHidden onCancel={close} footer={<Flex gap="small" wrap justify="end"><Button aria-label="取消编辑外部订阅来源" onClick={close}>取消</Button><Button type="primary" aria-label="保存外部订阅来源" loading={busy === "save"} disabled={!canSave} onClick={() => void save()}>保存来源</Button></Flex>}>
    <Form layout="vertical" preserve={false} autoComplete="off" onFinish={() => void save()}>
      {error && <Alert type="error" showIcon title={zhMessage(error)} />}
      {source ? <Descriptions size="small" column={1} items={[{ key: "owner", label: "所属用户（不可更改）", children: <span style={wrapText}>{source.owner_username}</span> }, { key: "revision", label: "预期来源版本", children: revision }]} /> : <Form.Item label="所属用户" required><Select aria-label="外部订阅所属用户" placeholder="选择已有用户" value={owner || undefined} disabled={!!busy} options={users.filter(user => !user.removal_id).map(user => ({ label: `${user.display_name || user.username} (${user.username})${user.is_active ? "" : " — 未启用"}`, value: user.username }))} onChange={value => { setOwner(value); setUrl(""); setAgent(""); setError(""); }} /></Form.Item>}
      <Form.Item label="来源名称" required help="1–160 个字符，不得包含控制字符"><Input aria-label="外部订阅来源名称" value={name} disabled={!!busy} onChange={event => setName(event.target.value)} /></Form.Item>
      <Form.Item label={source ? "新链接（可选）" : "订阅链接"} required={!source} help={source ? "留空保留已保存的链接。更换链接后，须先预览并确认，才会替换已确认的节点。" : "请输入私密的 HTTPS 订阅链接，仅在保存时发送。"}>
        <Input.Password aria-label="外部订阅链接" autoComplete="off" autoCapitalize="none" spellCheck={false} visibilityToggle={false} value={url} disabled={!!busy} onChange={event => setUrl(event.target.value)} />
      </Form.Item>
      <Form.Item label="User-Agent 设置"><Select aria-label="外部订阅 User-Agent 设置" value={agentMode} disabled={!!busy} options={[...(source ? [{ label: "保留已保存的 User-Agent", value: "keep" }] : []), { label: `使用默认值（${defaultAgent}）`, value: "default" }, { label: "设置自定义 User-Agent", value: "replace" }]} onChange={value => { setAgentMode(value); setAgent(""); }} /></Form.Item>
      {agentMode === "replace" && <Form.Item label="自定义 User-Agent" required help="1–256 个可打印 ASCII 字符"><Input.Password aria-label="外部订阅自定义 User-Agent" autoComplete="off" visibilityToggle={false} spellCheck={false} value={agent} disabled={!!busy} onChange={event => setAgent(event.target.value)} /></Form.Item>}
      <Form.Item label="启用来源"><Switch aria-label="启用外部订阅来源" checked={enabled} disabled={!!busy} onChange={setEnabled} /></Form.Item>
      <Typography.Paragraph type="secondary">链接和自定义 User-Agent 不会被读回，也不会保存在浏览器存储中。每次请求后都会清空秘密输入；重试前请重新输入需要更换的内容。关闭会丢弃表单，但无法撤销已发出的写入请求。</Typography.Paragraph>
      {!enabled && <Alert type="warning" showIcon title="停用只会停止后续分发，无法撤回客户端已下载的凭据。" />}
      {source && stale && <Button aria-label="刷新来源版本" loading={busy === "refresh"} disabled={!!busy} onClick={() => void refreshRevision()}>刷新来源版本</Button>}
      {latest && <Alert type="info" showIcon title={`最新保存的来源：${latest.name} · ${latest.enabled ? "已启用" : "已停用"} · 版本 ${latest.revision}`} description="已保留填写的名称和启用选项。请与最新保存状态核对后手动保存；未自动重试写入。" />}
    </Form>
  </Modal>;
}

interface SourceDeletionProps { open: boolean; source: ExternalSourceRead; onOpenChange: (open: boolean) => void; onRead: (detail: ExternalSourceDetail) => void; onDeleted: () => void }
function SourceDeletion(props: SourceDeletionProps) { return props.open ? <SourceDeletionContent {...props} /> : null; }
function SourceDeletionContent({ source, onOpenChange, onRead, onDeleted }: SourceDeletionProps) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [revision, setRevision] = useState(source.revision), [accepted, setAccepted] = useState(false), [stale, setStale] = useState(false), [busy, setBusy] = useState(""), [error, setError] = useState("");
  function close() { scope.invalidate(); onOpenChange(false); }
  async function remove() {
    if (!accepted || stale || busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy("delete"); setError("");
    try { await deleteExternalSource(source.id, { expected_revision: revision, confirm: true }); if (scope.isCurrent(run)) onDeleted(); }
    catch (failure) { if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(conflict(failure)); } }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  async function refreshRevision() {
    if (busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy("refresh");
    try {
      const result = await getExternalSource(source.id);
      if (!scope.isCurrent(run)) return;
      setRevision(result.source.revision); setStale(false); setAccepted(false); setError(""); onRead(result);
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  return <Modal open title="删除外部订阅来源？" style={modalStyle} destroyOnHidden onCancel={close} footer={<Flex justify="end" wrap gap="small"><Button aria-label="保留外部订阅来源" onClick={close}>保留来源</Button><Button danger type="primary" aria-label="确认删除外部订阅来源" loading={busy === "delete"} disabled={!!busy || !accepted || stale} onClick={() => void remove()}>删除来源</Button></Flex>}>
    <Flex vertical gap="middle"><Typography.Paragraph style={wrapText}>是否删除用户 {source.owner_username} 的来源 {source.name} 及其 {source.node_count} 个已保存节点？预期版本：{revision}。</Typography.Paragraph>
      <Alert type="warning" showIcon title="此操作会停止后续分发。此前下载的上游凭据无法在此撤回。" />
      <Checkbox aria-label="确认外部订阅来源删除影响" checked={accepted} disabled={!!busy} onChange={event => setAccepted(event.target.checked)}>我理解此操作将删除来源及其已导入的节点。</Checkbox>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}{stale && <Button aria-label="刷新删除操作版本" loading={busy === "refresh"} disabled={!!busy} onClick={() => void refreshRevision()}>先刷新，再重新核对删除操作</Button>}
    </Flex>
  </Modal>;
}

interface NodeEditorProps { open: boolean; source: ExternalSourceRead; node: ExternalNodeRead; onOpenChange: (open: boolean) => void; onRead: (detail: ExternalSourceDetail) => void; onSaved: (detail: ExternalSourceDetail) => void }
function NodeEditor(props: NodeEditorProps) { return props.open ? <NodeEditorContent {...props} /> : null; }
function NodeEditorContent({ source, node, onOpenChange, onRead, onSaved }: NodeEditorProps) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [name, setName] = useState(node.name), [enabled, setEnabled] = useState(node.enabled), [revision, setRevision] = useState(source.revision);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [stale, setStale] = useState(false), [latest, setLatest] = useState<ExternalNodeRead | null>(null);
  function close() { scope.invalidate(); onOpenChange(false); }
  async function save() {
    if (busyRef.current || stale || !validName(name)) return;
    const run = scope.begin(); busyRef.current = true; setBusy("save"); setError("");
    try { const value = await updateExternalNode(source.id, node.id, { expected_revision: revision, name: cleanName(name), enabled }); if (scope.isCurrent(run)) onSaved(value); }
    catch (failure) { if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(conflict(failure)); } }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  async function refreshRevision() {
    if (busyRef.current) return;
    const run = scope.begin(); busyRef.current = true; setBusy("refresh");
    try {
      const value = await getExternalSource(source.id);
      if (!scope.isCurrent(run)) return;
      const currentNode = value.nodes.find(item => item.id === node.id);
      if (!currentNode) { setError("此外部节点已不存在，请关闭编辑器并刷新来源详情。"); return; }
      setRevision(value.source.revision); setLatest(currentNode); setStale(false); setError(""); onRead(value);
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }
  }
  return <Modal open title="编辑外部节点" width={540} style={modalStyle} destroyOnHidden onCancel={close} footer={<Flex justify="end" gap="small" wrap><Button aria-label="取消编辑外部节点" onClick={close}>取消</Button><Button type="primary" aria-label="保存外部节点" loading={busy === "save"} disabled={!!busy || stale || !validName(name)} onClick={() => void save()}>保存节点</Button></Flex>}>
    <Form layout="vertical" preserve={false} onFinish={() => void save()}>
      <Typography.Paragraph style={wrapText}>上游标识：{node.upstream_name}。预期来源版本：{revision}。</Typography.Paragraph>
      <Form.Item label="节点名称" required><Input aria-label="外部节点名称" value={name} disabled={!!busy} onChange={event => setName(event.target.value)} /></Form.Item>
      <Form.Item label="启用节点"><Switch aria-label="启用外部节点" checked={enabled} disabled={!!busy} onChange={setEnabled} /></Form.Item>
      <Typography.Paragraph type="secondary">此操作仅更改本地显示名称和后续分发，不会创建或撤销上游凭据。</Typography.Paragraph>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}{stale && <Button aria-label="刷新外部节点版本" loading={busy === "refresh"} disabled={!!busy} onClick={() => void refreshRevision()}>刷新来源版本</Button>}
      {latest && <Alert type="info" title={`最新保存的节点：${latest.name} · ${latest.enabled ? "已启用" : "已停用"}`} description="已保留填写的选项，请核对后再保存；未自动重试写入。" showIcon />}
    </Form>
  </Modal>;
}

interface PreviewDialogProps { open: boolean; source: ExternalSourceRead; onOpenChange: (open: boolean) => void; onRead: (detail: ExternalSourceDetail) => void; onApplied: () => void }
function PreviewDialog(props: PreviewDialogProps) { return props.open ? <PreviewContent {...props} /> : null; }
function PreviewContent({ source, onOpenChange, onRead, onApplied }: PreviewDialogProps) {
  const scope = useAsyncScope(), busyRef = useRef(false);
  const [preview, setPreview] = useState<ExternalPreviewRead | null>(null), [recoveryId, setRecoveryId] = useState("");
  const [selection, setSelection] = useState<string[]>([]), [accepted, setAccepted] = useState(false);
  const [attempt, setAttempt] = useState<ExternalPreviewConfirm | null>(null);
  const [cancelNeedsCheck, setCancelNeedsCheck] = useState(false);
  const [busy, setBusy] = useState(""), [error, setError] = useState(""), [notice, setNotice] = useState(""), [stale, setStale] = useState(false);
  const choices = preview?.nodes.filter(selectable) ?? [], confirmed = !!preview?.receipt;
  const locked = !!busy || !!attempt || confirmed || stale || cancelNeedsCheck;
  function close() { scope.invalidate(); setPreview(null); setSelection([]); setAttempt(null); setRecoveryId(""); setAccepted(false); onOpenChange(false); }
  function start(action: string) { if (busyRef.current) return null; busyRef.current = true; setBusy(action); setError(""); setNotice(""); return scope.begin(); }
  function finish(run: number) { if (scope.isCurrent(run)) { busyRef.current = false; setBusy(""); } }

  async function fetchPreview() {
    if (preview || stale) return;
    const run = start("fetch"); if (run === null) return;
    try {
      const value = await createExternalPreview(source.id, { expected_revision: source.revision });
      if (!scope.isCurrent(run)) return;
      setPreview(value); setSelection([]); setAccepted(false); setAttempt(null); setRecoveryId("");
    } catch (failure) { if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(conflict(failure)); } }
    finally { finish(run); }
  }
  async function checkReceipt() {
    const id = preview?.id ?? recoveryId.trim(); if (!id) return;
    const run = start("receipt"); if (run === null) return;
    try {
      const value = await getExternalPreview(source.id, id);
      if (!scope.isCurrent(run)) return;
      setPreview(value); setRecoveryId("");
      if (value.receipt) { setAttempt(null); setStale(false); setCancelNeedsCheck(false); onApplied(); }
      else {
        // Recovering another known preview is not a retry of a failed fetch.
        // Never remove a confirmed-write conflict merely because GET has no receipt.
        if (!preview || cancelNeedsCheck) setStale(false);
        setCancelNeedsCheck(false);
        setNotice(attempt ? "暂未取得回执。只能重试同一确认，已选节点保持不变。" : "此预览尚未确认。请核对变更，并明确选择要导入的新节点。");
      }
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { finish(run); }
  }
  async function confirm() {
    if (!preview || confirmed || stale || cancelNeedsCheck || (!attempt && (!accepted || selection.length > 1000))) return;
    if (!attempt && (!Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= Date.now())) {
      setError("此预览已过期，请关闭后手动抓取新的预览。"); setStale(true); return;
    }
    const run = start("confirm"); if (run === null) return;
    const payload = attempt ?? { expected_revision: preview.source_revision, selected_node_ids: selection.filter(id => choices.some(node => node.node_id === id)), accept_changes: true };
    setAttempt(payload);
    try {
      const value = await confirmExternalPreview(source.id, preview.id, payload);
      if (!scope.isCurrent(run)) return;
      setPreview(previous => previous ? { ...previous, receipt: value } : null); setAttempt(null); onApplied();
    } catch (failure) {
      if (!scope.isCurrent(run)) return;
      setError(externalSubscriptionsErrorMessage(failure));
      if (conflict(failure)) setStale(true);
      else if (failure instanceof ExternalSubscriptionsError && !failure.outcomeUnknown) setAttempt(null);
    } finally { finish(run); }
  }
  async function cancel() {
    if (!preview || confirmed || attempt || cancelNeedsCheck) return;
    const run = start("cancel"); if (run === null) return;
    try { await cancelExternalPreview(source.id, preview.id); if (scope.isCurrent(run)) close(); }
    catch (failure) { if (scope.isCurrent(run)) { setError(externalSubscriptionsErrorMessage(failure)); setStale(conflict(failure)); setCancelNeedsCheck(true); } }
    finally { finish(run); }
  }
  async function refreshSource() {
    const run = start("source"); if (run === null) return;
    try {
      const value = await getExternalSource(source.id);
      if (!scope.isCurrent(run)) return;
      onRead(value);
      // A new source revision never upgrades an old preview or silently retries it.
      if (!preview) setStale(false);
      setNotice(preview ? `当前来源版本：${value.source.revision}。已保留预览中的选择；可使用“查询确认结果”获取回执，或关闭后手动抓取新预览。` : `来源版本已刷新为 ${value.source.revision}。准备好核对内容后，再手动抓取。`);
    } catch (failure) { if (scope.isCurrent(run)) setError(externalSubscriptionsErrorMessage(failure)); }
    finally { finish(run); }
  }

  return <Modal open title="外部订阅来源预览" width={880} style={modalStyle} styles={{ body: modalBody }} destroyOnHidden onCancel={close} footer={<Flex gap="small" wrap justify="end">
    {preview && !confirmed && !attempt && !cancelNeedsCheck && <Button danger aria-label="取消外部订阅预览" loading={busy === "cancel"} disabled={!!busy} onClick={() => void cancel()}>取消预览</Button>}
    <Button aria-label="关闭外部订阅预览" onClick={close}>关闭</Button>
    {preview && !confirmed && <Button type="primary" aria-label={attempt ? "重试同一外部订阅确认" : "确认外部订阅预览"} loading={busy === "confirm"} disabled={!!busy || stale || cancelNeedsCheck || (!attempt && (!accepted || selection.length > 1000))} onClick={() => void confirm()}>{attempt ? "重试同一确认" : "确认预览"}</Button>}
  </Flex>}><Flex vertical gap="middle">
    <Typography.Text style={wrapText}>{source.name} · 所属用户 {source.owner_username}</Typography.Text>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{notice && <Alert type="info" title={notice} showIcon />}
    {!preview ? <>
      <Alert type="info" showIcon title="抓取只生成预览，不会导入或更新节点。" description="预览在 15 分钟后过期，每个来源最多保留 3 个未确认预览。保存来源不会自动抓取。" />
      <Button type="primary" aria-label="抓取外部订阅预览" loading={busy === "fetch"} disabled={!!busy || stale} onClick={() => void fetchPreview()}>抓取预览</Button>
      <Form layout="vertical" preserve={false} onFinish={() => void checkReceipt()}><Form.Item label="预览 ID" help="恢复已有预览或确认回执，不会抓取上游链接。回执保留 7 天。"><Input aria-label="恢复外部订阅预览 ID" value={recoveryId} autoComplete="off" disabled={!!busy} onChange={event => setRecoveryId(event.target.value)} /></Form.Item><Button aria-label="恢复外部订阅预览" loading={busy === "receipt"} disabled={!!busy || !recoveryId.trim()} htmlType="submit">恢复预览 / 回执</Button></Form>
    </> : <>
      <Descriptions size="small" column={1} items={[
        { key: "id", label: "预览 ID", children: <Typography.Text style={wrapText}>{preview.id}</Typography.Text> },
        { key: "revision", label: "预览对应的来源版本", children: preview.source_revision },
        { key: "created", label: "创建时间", children: date(preview.created_at) },
        { key: "expires", label: "预览到期时间", children: date(preview.expires_at) },
      ]} />
      <Typography.Paragraph type="secondary">只导入已选中且受支持的新节点。明确确认后，显示的已有节点更新和缺失、不可用状态会一并应用。已重命名的节点保留本地显示名称。</Typography.Paragraph>
      <Typography.Paragraph type="secondary">关闭会清空浏览器中的预览和已选节点，不会取消或撤销服务器请求。后续如需恢复回执，请自行保存预览 ID。ID、选择内容和凭据都不会写入浏览器存储。</Typography.Paragraph>
      {attempt && !confirmed && <Alert type="warning" showIcon title="确认结果尚不明确" description="原预览、来源版本及已选节点已锁定，以便重试完全相同的确认。响应丢失时，请先查询确认结果。不会重新抓取上游。" />}
      {cancelNeedsCheck && <Alert type="warning" showIcon title="请先查询预览状态，再执行其他操作" description="取消操作尚未确认。请使用“查询确认结果”恢复当前状态；已确认的回执无法取消。" />}
      {selection.length > 1000 && <Alert type="warning" showIcon title="每次确认最多选择 1,000 个新节点。请先清空或减少选择，再确认。" />}
      {confirmed && <Alert type="success" showIcon title="外部订阅预览已确认" description={<span>已导入 {preview.receipt!.imported_count} 个，更新 {preview.receipt!.updated_count} 个，缺失 {preview.receipt!.missing_count} 个。来源版本 {preview.receipt!.revision}。应用时间：{date(preview.receipt!.applied_at)}。</span>} />}
      <Flex gap="small" wrap><Button aria-label="查询外部订阅确认结果" loading={busy === "receipt"} disabled={!!busy} onClick={() => void checkReceipt()}>查询确认结果</Button>{!confirmed && <><Button aria-label="选择全部外部新节点" disabled={locked || !choices.length} onClick={() => setSelection(choices.map(node => node.node_id))}>选择全部新节点</Button><Button aria-label="清空外部节点选择" disabled={locked || !selection.length} onClick={() => setSelection([])}>清空选择</Button><Tag>已选择 {selection.length} 个新节点</Tag></>}</Flex>
      <Table<ExternalPreviewNode> rowKey="node_id" size="small" dataSource={preview.nodes} scroll={{ x: 760 }} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: "此预览中没有节点。" }} rowSelection={confirmed ? undefined : {
        selectedRowKeys: selection, hideSelectAll: true, preserveSelectedRowKeys: false,
        getCheckboxProps: node => ({ disabled: locked || !selectable(node), "aria-label": `导入外部节点 ${node.name}` }),
        onChange: keys => { if (!locked) setSelection(keys.map(String).filter(id => choices.some(node => node.node_id === id))); },
      }} columns={[
        { title: "节点", width: 220, render: (_, node) => <><Typography.Text style={wrapText}>{node.name}</Typography.Text><div><Typography.Text type="secondary" style={wrapText}>上游：{node.upstream_name}</Typography.Text></div></> },
        { title: "协议", dataIndex: "protocol", width: 100 },
        { title: "变更", dataIndex: "change", width: 120, render: value => <Tag color={value === "new" ? "processing" : value === "missing" || value === "unavailable" ? "warning" : "default"}>{zhStatus(value)}</Tag> },
        { title: "详情", width: 280, render: (_, node) => <Flex vertical gap="small">{node.changed_fields.length > 0 && <Typography.Text style={wrapText}>变更字段：{node.changed_fields.join(", ")}</Typography.Text>}{node.reason && <Typography.Text style={wrapText}>{zhMessage(node.reason)}</Typography.Text>}{!selectable(node) && !node.existing && <Typography.Text type="secondary">不可导入</Typography.Text>}</Flex> },
      ]} />
      {!confirmed && <Checkbox aria-label="接受外部订阅预览变更" checked={accepted} disabled={locked} onChange={event => setAccepted(event.target.checked)}>我接受显示的已有节点更新及缺失、不可用状态，并确认所选的新节点。</Checkbox>}
      <UpstreamMetadata metadata={preview.metadata} />
    </>}
    {stale && <Button aria-label="刷新预览来源状态" loading={busy === "source"} disabled={!!busy} onClick={() => void refreshSource()}>刷新来源状态</Button>}
  </Flex></Modal>;
}
