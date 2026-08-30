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
    } catch (failure) { if (run === version.current && current === pollVersion.current) setStatusError(failure instanceof Error ? failure.message : "Node status unavailable"); }
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
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Node request failed"); }
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
        const value = await saveNode(id, { ...form, config: parseNodeObject(config, "Node config"), client_template: parseNodeObject(clientTemplate, "Client template") }, detail.revision);
        if (run !== version.current) return;
        setDetail(value); setForm(nodeSettings(value.node)); acceptAccess(value.access); setSaved(true); updated.current?.();
      } else {
        const value = await removeNode(id, detail.revision, confirmName, unmanaged); if (run !== version.current) return; acceptRemoval(value); updated.current?.();
      }
      setAcknowledged(false); if (!completed.current) void poll(run);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "Node update failed"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patch(change: Partial<NodeSettings>) { setForm(previous => previous ? { ...previous, ...change } : previous); }
  return <Modal open title={removal ? "Node removal" : mode === "edit" ? "Edit node" : "Remove node"} width={760} centered styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }} destroyOnHidden mask={{ closable: !busy }} keyboard={!busy} closable={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{saved || removal ? "Close" : "Cancel"}</Button>{!removal && <Button type="primary" aria-label={mode === "edit" ? "Save" : "Remove"} aria-busy={busy} danger={mode === "remove"} disabled={!canSubmit} loading={busy} onClick={() => void submit()}>{mode === "edit" ? "Save" : "Remove"}</Button>}</Flex>}>
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="center"><Typography.Text strong>{detail?.node.name ?? removal?.name}</Typography.Text>{detail && <Tag>{detail.node.protocol} / {detail.node.node_type}</Tag>}<Button aria-label="Reload node details" icon={<ReloadOutlined />} disabled={busy || completed.current} onClick={() => void (removal ? poll(version.current) : load())} /></Flex>
      {busy && <Spin />}{error && <Alert type="error" title={error} showIcon />}{saved && <Alert type="success" title="Node saved" showIcon />}
      {removal && <Alert type={removal.status === "completed" ? "success" : removal.status === "failed" ? "error" : "info"} title={removal.status === "completed" ? "Node removed" : removal.status === "failed" ? "Removal needs attention" : "Removal pending Agent confirmation"} showIcon />}
      {detail && form && !removal && (mode === "edit" ? <Form layout="vertical" preserve={false} disabled={busy}>
        <Form.Item label="Node name"><Input aria-label="Node name" maxLength={120} value={form.name} onChange={event => patch({ name: event.target.value })} /></Form.Item>
        <Form.Item label="Primary tag"><Input aria-label="Primary tag" maxLength={120} value={form.tag ?? ""} onChange={event => patch({ tag: event.target.value || null })} /></Form.Item>
        <Form.Item label="Tags"><Select aria-label="Tags" mode="tags" value={form.tags} onChange={tags => patch({ tags })} options={form.tags.map(value => ({ label: value, value }))} /></Form.Item>
        {detail.node.node_type === "routed" && <>
          <Form.Item label="Parent node"><Select aria-label="Parent node" allowClear value={form.parent_id ?? undefined} onChange={parent_id => patch({ parent_id: parent_id ?? null })} options={parents.map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
          <Form.Item label="Target node"><Select aria-label="Target node" allowClear value={form.target_node_id ?? undefined} onChange={target_node_id => patch({ target_node_id: target_node_id ?? null })} options={selectable.map(node => ({ label: node.name, value: node.id }))} /></Form.Item>
        </>}
        <Form.Item label="Node config"><Input.TextArea aria-label="Node config" rows={5} value={config} onChange={event => setConfig(event.target.value)} /></Form.Item>
        <Form.Item label="Client template"><Input.TextArea aria-label="Client template" rows={4} value={clientTemplate} onChange={event => setClientTemplate(event.target.value)} /></Form.Item>
        <Form.Item label="Enabled"><Switch aria-label="Enabled" checked={form.enabled} onChange={enabled => patch({ enabled })} /></Form.Item>
      </Form> : <>
        <Typography.Text>{detail.nodes.length} nodes / {detail.plans.length} plans / {detail.credential_count} credentials</Typography.Text>
        <section aria-label="Affected nodes"><Typography.Title level={5}>Nodes</Typography.Title>{detail.nodes.map(node => <Typography.Paragraph key={node.id}>{node.name}</Typography.Paragraph>)}</section>
        {!!detail.plans.length && <section aria-label="Affected plans"><Typography.Title level={5}>Plans</Typography.Title>{detail.plans.map(plan => <Typography.Paragraph key={plan.id}>{plan.name}</Typography.Paragraph>)}</section>}
        {detail.blockers.map(blocker => <Alert key={blocker} type="error" title={blocker} showIcon />)}
        <Alert type="warning" title="Selected nodes leave subscriptions immediately. Remote resources remain until the Agent confirms cleanup. Shared listeners, subscriber accounts, links and charged traffic are retained. Removal cannot be cancelled." showIcon />
        <Form.Item label="Confirm node name"><Input aria-label="Confirm node name" value={confirmName} disabled={busy} onChange={event => setConfirmName(event.target.value)} /></Form.Item>
      </>)}
      {(removal || (detail && mode === "remove")) && <section aria-label="Node resource cleanup"><Flex justify="space-between"><Typography.Title level={5}>Remote resources</Typography.Title>{removal && <Button icon={<SyncOutlined />} aria-label="Retry node removal" loading={syncing} disabled={busy || completed.current} onClick={() => void poll(version.current, true)} />}</Flex>
        {(removal?.servers ?? detail?.servers ?? []).map(server => <Card key={server.server_id} size="small" title={server.server_name} extra={removal && <Tag color={server.error ? "error" : server.phase === "completed" ? "success" : "warning"}>{server.error ? "Failed" : server.phase}</Tag>}>
          <Descriptions column={1} size="small" items={[
            { key: "in", label: "Remove inbounds", children: server.inbound_tags.join(", ") || "None" }, { key: "out", label: "Remove outbounds", children: server.outbound_tags.join(", ") || "None" },
            { key: "keep-in", label: "Keep shared inbounds", children: server.retained_inbound_tags.join(", ") || "None" }, { key: "keep-out", label: "Keep shared outbounds", children: server.retained_outbound_tags.join(", ") || "None" },
          ]} />{server.error && <Alert type="error" title={server.error} showIcon />}
        </Card>)}
      </section>}
      {warnings.map(warning => <Alert key={warning} type="warning" title={warning} showIcon />)}
      {detail && !removal && <>{mode === "remove" && !!warnings.length && <Checkbox checked={unmanaged} disabled={busy} onChange={event => setUnmanaged(event.target.checked)}>I accept responsibility for unmanaged resources</Checkbox>}<Checkbox checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)}>I accept Xray restarts, disconnected clients and pending remote changes</Checkbox></>}
      {!removal && !!access.length && <section aria-label="Node subscription access"><Flex justify="space-between"><Typography.Title level={5}>Subscription access</Typography.Title><Button icon={<SyncOutlined />} aria-label="Retry node access synchronization" loading={syncing} disabled={busy} onClick={() => void poll(version.current, true)} /></Flex>
        {access.map(user => <Card size="small" key={user.username} title={user.username}>{user.servers.map(server => <Flex key={server.server_id} justify="space-between" wrap><span>{server.server_name}</span><Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{server.status}</Tag>{server.error && <Alert type="error" title={server.error} showIcon />}</Flex>)}</Card>)}
      </section>}{statusError && <Alert type="error" title={statusError} showIcon />}
    </Flex>
  </Modal>;
}
