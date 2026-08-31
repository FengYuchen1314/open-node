import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Descriptions, Flex, Form, Input, Modal, Select, Spin, Switch, Tag, Typography } from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import type { ManagedNode, SubscriptionAccessResponse } from "../../domain/subscriptions";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../../services/subscriptions";
import { getNodeManagement, getNodeRemoval, nodeSettings, parseNodeObject, removeNode, retryNodeRemoval, saveNode, type NodeManagementRead, type NodeOperation, type NodeRemoval, type NodeSettings } from "../../services/node-management";

export interface NodeManagementDialogProps { id: string; mode: NodeOperation; open: boolean; nodes: ManagedNode[]; onOpenChange: (open: boolean) => void; onUpdated?: () => void }
export default function NodeManagementDialog(props: NodeManagementDialogProps) { return props.open ? <NodeContent key={`${props.id}:${props.mode}`} {...props} /> : null; }
function NodeContent({ id, mode, nodes, onOpenChange, onUpdated }: NodeManagementDialogProps) {
  const [detail, setDetail] = useState<NodeManagementRead | null>(null), [form, setForm] = useState<NodeSettings | null>(null);
  const [removal, setRemoval] = useState<NodeRemoval | null>(null), [access, setAccess] = useState<SubscriptionAccessResponse[]>([]);
  const [config, setConfig] = useState("{}"), [clientTemplate, setClientTemplate] = useState("{}");
  const [busy, setBusy] = useState(false), [syncing, setSyncing] = useState(false), [saved, setSaved] = useState(false);
  const [error, setError] = useState(""), [statusError, setStatusError] = useState(""), [confirmName, setConfirmName] = useState("");
  const [acknowledged, setAcknowledged] = useState(false), [unmanaged, setUnmanaged] = useState(false);
  const version = useRef(0), pollVersion = useRef(0), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const removalRef = useRef<NodeRemoval | null>(null), accessRef = useRef<SubscriptionAccessResponse[]>([]), completed = useRef(false);
  const updated = useRef(onUpdated); updated.current = onUpdated;
  function stop() { clearTimeout(timer.current); ++pollVersion.current; }
  function acceptAccess(value: SubscriptionAccessResponse[]) { accessRef.current = value; setAccess(value); }
  function acceptRemoval(value: NodeRemoval) {
    removalRef.current = value; setRemoval(value);
    if (value.status === "completed" && !completed.current) { completed.current = true; updated.current?.(); }
  }
  async function poll(run: number, retry = false) {
    clearTimeout(timer.current); const current = ++pollVersion.current; setSyncing(retry);
    try {
      if (removalRef.current) {
        const value = await (retry ? retryNodeRemoval : getNodeRemoval)(removalRef.current.id);
        if (run !== version.current || current !== pollVersion.current) return; acceptRemoval(value);
      } else {
        const values = await Promise.all(accessRef.current.map(user => (retry ? syncSubscriptionAccess : getSubscriptionAccess)(user.username)));
        if (run !== version.current || current !== pollVersion.current) return; acceptAccess(values);
      }
      setStatusError("");
    } catch (failure) { if (run === version.current && current === pollVersion.current) setStatusError(failure instanceof Error ? failure.message : "无法读取节点状态"); }
    finally { if (run === version.current && current === pollVersion.current) { setSyncing(false); if (!completed.current) timer.current = setTimeout(() => void poll(run), 5000); } }
  }
  async function load() {
    const run = ++version.current; stop(); removalRef.current = null; completed.current = false;
    setDetail(null); setForm(null); setRemoval(null); acceptAccess([]); setSaved(false); setError(""); setStatusError(""); setConfirmName(""); setAcknowledged(false); setUnmanaged(false); setSyncing(false); setBusy(true);
    try {
      const value = await getNodeManagement(id); if (run !== version.current) return;
      setDetail(value); setForm(nodeSettings(value.node)); acceptAccess(value.access);
      setConfig(JSON.stringify(value.node.config, null, 2)); setClientTemplate(JSON.stringify(value.node.client_template, null, 2));
      if (value.node.removal_id) { const job = await getNodeRemoval(value.node.removal_id); if (run !== version.current) return; acceptRemoval(job); }
      if (!completed.current) timer.current = setTimeout(() => void poll(run), 5000);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "请求节点信息失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { ++version.current; stop(); }; }, []);
  const warnings = removal?.warnings ?? detail?.warnings ?? [], selectable = nodes.filter(node => node.id !== id && !node.removal_id);
  const parents = selectable.filter(node => node.server_id === detail?.node.server_id && node.inbound_tag === detail?.node.inbound_tag && node.protocol === detail?.node.protocol);
  const canSubmit = !busy && !!form && !!detail && !removal && acknowledged && (mode === "edit" ? !!form.name.trim() : !detail.blockers.length && confirmName === detail.node.name && (!warnings.length || unmanaged));
  async function submit() {
    if (!canSubmit || !detail || !form) return;
    const run = ++version.current; stop(); setSyncing(false); setBusy(true); setError("");
    try {
      if (mode === "edit") {
        const value = await saveNode(id, { ...form, config: parseNodeObject(config, "节点配置"), client_template: parseNodeObject(clientTemplate, "客户端模板") }, detail.revision);
        if (run !== version.current) return;
        setDetail(value); setForm(nodeSettings(value.node)); acceptAccess(value.access); setSaved(true); updated.current?.();
      } else {
        const value = await removeNode(id, detail.revision, confirmName, unmanaged); if (run !== version.current) return; acceptRemoval(value); updated.current?.();
      }
      setAcknowledged(false); if (!completed.current) void poll(run);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新节点失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patch(change: Partial<NodeSettings>) { setForm(previous => previous ? { ...previous, ...change } : previous); }
  return <Modal open title={removal ? "节点移除" : mode === "edit" ? "编辑节点" : "移除节点"} width={760} centered styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{saved || removal ? "关闭" : "取消"}</Button>{!removal && <Button type="primary" aria-label={mode === "edit" ? "保存" : "移除"} aria-busy={busy} danger={mode === "remove"} disabled={!canSubmit} loading={busy} onClick={() => void submit()}>{mode === "edit" ? "保存" : "移除"}</Button>}</Flex>}>
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="center"><Typography.Text strong>{detail?.node.name ?? removal?.name}</Typography.Text>{detail && <Tag>{detail.node.protocol} / {zhStatus(detail.node.node_type)}</Tag>}<Button aria-label="重新加载节点详情" icon={<ReloadOutlined />} disabled={busy || completed.current} onClick={() => void (removal ? poll(version.current) : load())} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={zhMessage(error)} showIcon />}{saved && <Alert type="success" title="节点已保存" showIcon />}
      {removal && <Alert type={removal.status === "completed" ? "success" : removal.status === "failed" ? "error" : "info"} title={removal.status === "completed" ? "节点已移除" : removal.status === "failed" ? "移除操作需要处理" : "正在等待 Agent 确认移除"} showIcon />}
      {detail && form && !removal && (mode === "edit" ? <Form layout="vertical" preserve={false} disabled={busy}>
        <Form.Item label="节点名称"><Input aria-label="节点名称" maxLength={120} value={form.name} onChange={event => patch({ name: event.target.value })} /></Form.Item>
        <Form.Item label="主要标签"><Input aria-label="主要标签" maxLength={120} value={form.tag ?? ""} onChange={event => patch({ tag: event.target.value || null })} /></Form.Item>
        <Form.Item label="标签"><Select aria-label="标签" mode="tags" value={form.tags} onChange={tags => patch({ tags })} options={form.tags.map(value => ({ label: value, value }))} /></Form.Item>
        {detail.node.node_type === "routed" && <>
          <Form.Item label="父节点"><Select aria-label="父节点" allowClear value={form.parent_id ?? undefined} onChange={parent_id => patch({ parent_id: parent_id ?? null })} options={parents.map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
          <Form.Item label="目标节点"><Select aria-label="目标节点" allowClear value={form.target_node_id ?? undefined} onChange={target_node_id => patch({ target_node_id: target_node_id ?? null })} options={selectable.map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
        </>}
        <Form.Item label="节点配置"><Input.TextArea aria-label="节点配置" rows={5} value={config} onChange={event => setConfig(event.target.value)} /></Form.Item>
        <Form.Item label="客户端模板"><Input.TextArea aria-label="客户端模板" rows={4} value={clientTemplate} onChange={event => setClientTemplate(event.target.value)} /></Form.Item>
        <Form.Item label="已启用"><Switch aria-label="已启用" checked={form.enabled} onChange={enabled => patch({ enabled })} /></Form.Item>
      </Form> : <>
        <Typography.Text>{detail.nodes.length} 个节点 / {detail.plans.length} 个套餐 / {detail.credential_count} 份凭据</Typography.Text>
        <section aria-label="受影响的节点"><Typography.Title level={5}>节点</Typography.Title>{detail.nodes.map(node => <Typography.Paragraph key={node.id}>{node.name}</Typography.Paragraph>)}</section>
        {!!detail.plans.length && <section aria-label="受影响的套餐"><Typography.Title level={5}>套餐</Typography.Title>{detail.plans.map(plan => <Typography.Paragraph key={plan.id}>{plan.name}</Typography.Paragraph>)}</section>}
        {detail.blockers.map(blocker => <Alert key={blocker} type="error" title={zhMessage(blocker)} showIcon />)}
        <Alert type="warning" title="所选节点会立即从订阅中移除。远程资源会保留至 Agent 确认清理完成。共享监听器、用户账户、链接及已计费流量均会保留。移除操作无法取消。" showIcon />
        <Form.Item label="确认节点名称"><Input aria-label="确认节点名称" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
      </>)}
      {(removal || (detail && mode === "remove")) && <section aria-label="节点资源清理"><Flex justify="space-between"><Typography.Title level={5}>远程资源</Typography.Title>{removal && <Button icon={<SyncOutlined />} aria-label="重试移除节点" loading={syncing} disabled={busy || completed.current} onClick={() => void poll(version.current, true)} />}</Flex>
        {(removal?.servers ?? detail?.servers ?? []).map(server => <Card key={server.server_id} size="small" title={server.server_name} extra={removal && <Tag color={server.error ? "error" : server.phase === "completed" ? "success" : "warning"}>{server.error ? "失败" : zhStatus(server.phase)}</Tag>}>
          <Descriptions column={1} size="small" items={[
            { key: "in", label: "移除入站", children: server.inbound_tags.join(", ") || "无" }, { key: "out", label: "移除出站", children: server.outbound_tags.join(", ") || "无" },
            { key: "keep-in", label: "保留共享入站", children: server.retained_inbound_tags.join(", ") || "无" }, { key: "keep-out", label: "保留共享出站", children: server.retained_outbound_tags.join(", ") || "无" },
          ]} />{server.error && <Alert type="error" title={zhMessage(server.error)} showIcon />}
        </Card>)}
      </section>}
      {warnings.map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
      {detail && !removal && <>{mode === "remove" && !!warnings.length && <Checkbox checked={unmanaged} disabled={busy} onChange={event => setUnmanaged(event.target.checked)}>我接受自行处理未托管资源的责任</Checkbox>}<Checkbox checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)}>我接受 Xray 重启、客户端断开及远程变更待确认的影响</Checkbox></>}
      {!removal && !!access.length && <section aria-label="节点订阅访问"><Flex justify="space-between"><Typography.Title level={5}>订阅访问权限</Typography.Title><Button icon={<SyncOutlined />} aria-label="重试同步节点访问权限" loading={syncing} disabled={busy} onClick={() => void poll(version.current, true)} /></Flex>
        {access.map(user => <Card size="small" key={user.username} title={user.username}>{user.servers.map(server => <Flex key={server.server_id} justify="space-between" wrap><span>{server.server_name}</span><Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{zhStatus(server.status)}</Tag>{server.error && <Alert type="error" title={zhMessage(server.error)} showIcon />}</Flex>)}</Card>)}
      </section>}{statusError && <Alert type="error" title={zhMessage(statusError)} showIcon />}
    </Flex>
  </Modal>;
}
