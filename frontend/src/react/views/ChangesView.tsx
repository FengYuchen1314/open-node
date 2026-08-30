import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Descriptions, Empty, Form, Input, Modal, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined, ReloadOutlined, RollbackOutlined, SendOutlined } from "@ant-design/icons";
import { changeSetActions, type AgentChangeSet, type AgentChangeSetStatus, type AgentChangeSetStepCreateRequest } from "../../domain/changes";
import type { AgentCommand, AgentCommandCreateRequest, ServerSummary } from "../../domain/inventory";
import { listServers } from "../../services/inventory";
import { acceptChangeSet, createChangeSet, createRoutedOutboundChangeSet, dispatchChangeSet, listChangeSets, rollbackChangeSet } from "../../services/changes";
import CommandInspector from "../components/CommandInspector";
import StrictInputNumber from "../components/StrictInputNumber";

export interface ChangesViewProps {
  onCommands?: (commands: AgentCommand[]) => void;
  onUpdated?: () => void;
}
type Action = "create" | "routed" | "dispatch" | "rollback" | "accept" | "";
const protocols = ["vless", "vmess", "trojan", "shadowsocks", "anytls", "snell", "mieru", "hysteria", "socks", "http"];
const statusColors: Record<AgentChangeSetStatus, string> = {
  planned: "default", dispatched: "processing", rollback_queued: "warning", succeeded: "success", failed: "error",
  rolled_back: "success", rollback_failed: "error", rollback_incomplete: "warning", cancelled: "default", accepted: "success", needs_review: "warning",
};
const sampleOutbound = () => JSON.stringify({ protocol: "freedom", settings: { domainStrategy: "UseIPv4" } }, null, 2);
const sampleSteps = (serverId: string) => JSON.stringify([{ server_id: serverId, label: "Read system info", forward: { method: "GET", path: "/api/child/system/info" }, rollback: null }], null, 2);
const optionalText = (value: string) => value.trim() || null;
const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
function jsonObject(text: string, label: string) {
  if (!text.trim()) throw new Error(`${label} is required.`);
  const value: unknown = JSON.parse(text);
  if (!isRecord(value)) throw new Error(`${label} must be a JSON object.`);
  return value;
}
function stepsFrom(text: string): AgentChangeSetStepCreateRequest[] {
  const value: unknown = JSON.parse(text || "[]");
  if (!Array.isArray(value) || !value.length) throw new Error("Steps must be a non-empty JSON array.");
  value.forEach((step, index) => {
    if (!isRecord(step)) throw new Error(`Step ${index + 1} must be a JSON object.`);
    if (typeof step.server_id !== "string" || !step.server_id.trim()) throw new Error(`Step ${index + 1} server_id is required.`);
    if (!isRecord(step.forward)) throw new Error(`Step ${index + 1} forward command is required.`);
    if (step.rollback !== undefined && step.rollback !== null && !isRecord(step.rollback)) throw new Error(`Step ${index + 1} rollback must be an object or null.`);
  });
  return value as AgentChangeSetStepCreateRequest[];
}
function splitDomains(text: string) {
  const seen = new Set<string>();
  return text.split(/[\n,]+/).map((item) => item.trim()).filter((item) => {
    if (!item || seen.has(item.toLowerCase())) return false;
    seen.add(item.toLowerCase());
    return true;
  });
}
function commandText(command: AgentCommandCreateRequest) {
  return `${command.method || "GET"} ${command.path}${command.query ? `?${command.query}` : ""}`;
}
function commandBody(command: AgentCommandCreateRequest) {
  return command.body == null ? "" : typeof command.body === "string" ? command.body : JSON.stringify(command.body);
}
function CommandSummary({ request, command }: { request?: AgentCommandCreateRequest | null; command?: AgentCommand | null }) {
  const color = command?.status === "succeeded" ? "success" : command?.status === "failed" ? "error" : command?.status === "leased" ? "processing" : command?.status === "pending" ? "warning" : "default";
  return <Space orientation="vertical" size="small" style={{ maxWidth: "100%" }}>
    <Typography.Text code style={{ overflowWrap: "anywhere" }}>{request ? commandText(request) : "none"}</Typography.Text>
    {request && commandBody(request) && <Typography.Paragraph style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{commandBody(request)}</Typography.Paragraph>}
    <Tag color={color}>{command?.status ?? "not queued"}</Tag>
  </Space>;
}

export default function ChangesView(props: ChangesViewProps) {
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [changes, setChanges] = useState<AgentChangeSet[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<Action>("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [lastCommands, setLastCommands] = useState<AgentCommand[]>([]);
  const [planMode, setPlanMode] = useState("routed");
  const [acceptId, setAcceptId] = useState("");
  const [acceptanceReason, setAcceptanceReason] = useState("");
  const [acceptanceConfirmed, setAcceptanceConfirmed] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", rollback_on_failure: true, dispatch: false, rollbackReason: "", stepsText: sampleSteps("") });
  const [routed, setRouted] = useState({ server_id: "", inbound_tag: "", inbound_protocol: "vless", label: "direct", parent_ref: "", admin_username: "admin", admin_email: "", outbound_tag: "", marktag: "", node_name: "", outboundText: sampleOutbound(), clientText: "", sniffingExcludeDomainsText: "", add_reality_sniffing_excludes: true, rollback_on_failure: true, dispatch: false, command_timeout_ms: 30000 });
  const refreshRevision = useRef(0);
  const refreshOwner = useRef(0);
  const refreshBusy = useRef(false);
  const mutationBusy = useRef(false);
  const mounted = useRef(true);
  const live = useRef({ changes, selectedId, saving, acceptId, props });
  live.current = { changes, selectedId, saving, acceptId, props };
  const selected = changes.find((change) => change.id === selectedId) ?? null;
  const actions = changeSetActions(selected);
  const options = servers.map((server) => ({ label: server.name, value: server.id }));
  const patchForm = (patch: Partial<typeof form>) => setForm((value) => ({ ...value, ...patch }));
  const patchRouted = (patch: Partial<typeof routed>) => setRouted((value) => ({ ...value, ...patch }));

  async function refresh(background = false, replace = false) {
    if (refreshBusy.current && !replace) return;
    const revision = ++refreshRevision.current;
    refreshOwner.current = revision;
    refreshBusy.current = true;
    setLoading(true);
    if (!background) setError("");
    try {
      const [inventory, response] = await Promise.all([listServers(), listChangeSets()]);
      if (!mounted.current || revision !== refreshRevision.current) return;
      setServers(inventory);
      setChanges(response.change_sets);
      setForm((value) => value.stepsText.includes('"server_id": ""') && inventory[0] ? { ...value, stepsText: sampleSteps(inventory[0].id) } : value);
      setRouted((value) => inventory.some((server) => server.id === value.server_id) ? value : { ...value, server_id: inventory[0]?.id ?? "" });
      setSelectedId((value) => response.change_sets.some((change) => change.id === value) ? value : response.change_sets[0]?.id ?? "");
    } catch (failure) {
      if (mounted.current && !background && revision === refreshRevision.current) setError(failure instanceof Error ? failure.message : "Request failed.");
    } finally {
      if (refreshOwner.current === revision) {
        refreshBusy.current = false;
        if (mounted.current) setLoading(false);
      }
    }
  }
  useEffect(() => {
    mounted.current = true;
    void refresh(false, true);
    const timer = window.setInterval(() => {
      const current = live.current;
      const active = current.changes.some((change) => ["dispatched", "rollback_queued"].includes(change.status) || change.blocking_command_ids?.length);
      if (active && !refreshBusy.current && !mutationBusy.current && !current.acceptId) void refresh(true);
    }, 2500);
    return () => { mounted.current = false; refreshRevision.current += 1; window.clearInterval(timer); };
  }, []);

  function remember(change: AgentChangeSet, commands?: AgentCommand[], responseWarnings?: string[]) {
    refreshRevision.current += 1;
    setChanges((rows) => rows.some((item) => item.id === change.id) ? rows.map((item) => item.id === change.id ? change : item) : [change, ...rows]);
    setSelectedId(change.id);
    if (commands) { setLastCommands(commands); live.current.props.onCommands?.(commands); }
    if (responseWarnings) setWarnings(responseWarnings);
    live.current.props.onUpdated?.();
  }
  async function mutate(action: Exclude<Action, "">, work: () => Promise<void>) {
    if (mutationBusy.current) return;
    mutationBusy.current = true;
    setSaving(action);
    setError("");
    setSuccess("");
    setWarnings([]);
    try { await work(); }
    catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "Request failed."); }
    finally { mutationBusy.current = false; if (mounted.current) setSaving(""); }
  }
  function submitRouted() {
    void mutate("routed", async () => {
      if (!routed.server_id.trim()) throw new Error("Server is required.");
      if (!routed.inbound_tag.trim()) throw new Error("Inbound tag is required.");
      if (!routed.label.trim()) throw new Error("Label is required.");
      if (!Number.isFinite(routed.command_timeout_ms) || routed.command_timeout_ms <= 0) throw new Error("Timeout must be greater than zero.");
      const response = await createRoutedOutboundChangeSet({
        server_id: routed.server_id.trim(), inbound_tag: routed.inbound_tag.trim(), inbound_protocol: routed.inbound_protocol.trim() || "vless", label: routed.label.trim(),
        outbound: jsonObject(routed.outboundText, "Outbound JSON"), parent_ref: optionalText(routed.parent_ref), admin_username: routed.admin_username.trim() || "admin", admin_email: optionalText(routed.admin_email),
        outbound_tag: optionalText(routed.outbound_tag), marktag: optionalText(routed.marktag), node_name: optionalText(routed.node_name), client: routed.clientText.trim() ? jsonObject(routed.clientText, "Client JSON") : null,
        sniffing_exclude_domains: splitDomains(routed.sniffingExcludeDomainsText), add_reality_sniffing_excludes: routed.add_reality_sniffing_excludes,
        command_timeout_ms: routed.command_timeout_ms, rollback_on_failure: routed.rollback_on_failure, dispatch: routed.dispatch,
      });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `Created routed outbound plan and dispatched ${response.commands.length} commands.` : "Routed outbound change set created.");
      await refresh();
    });
  }
  function submitRaw() {
    void mutate("create", async () => {
      if (!form.name.trim()) throw new Error("Name is required.");
      const response = await createChangeSet({ name: form.name.trim(), description: form.description.trim(), rollback_on_failure: form.rollback_on_failure, dispatch: form.dispatch, steps: stepsFrom(form.stepsText) });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `Created and dispatched ${response.commands.length} commands.` : "Change set created.");
      patchForm({ name: "", description: "", rollback_on_failure: true, dispatch: false });
      await refresh();
    });
  }
  function dispatchSelected() {
    if (!selected || !actions.dispatch) return;
    void mutate("dispatch", async () => {
      const response = await dispatchChangeSet(selected.id);
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `Dispatched ${response.commands.length} commands.` : "No new forward commands.");
      await refresh();
    });
  }
  function rollbackSelected() {
    if (!selected || !actions.rollback) return;
    void mutate("rollback", async () => {
      const response = await rollbackChangeSet(selected.id, { reason: form.rollbackReason.trim() });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `Queued ${response.commands.length} rollback commands.` : "No new rollback commands.");
      await refresh();
    });
  }
  function acceptSelected() {
    const change = changes.find((item) => item.id === acceptId);
    if (!changeSetActions(change ?? null).accept || !acceptanceConfirmed || !acceptanceReason.trim()) return;
    void mutate("accept", async () => {
      const response = await acceptChangeSet(acceptId, acceptanceReason.trim());
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setAcceptId("");
      setSuccess("Current state accepted. Node reservations released.");
      await refresh();
    });
  }

  const routedForm = <Form layout="vertical" onFinish={submitRouted} disabled={Boolean(saving)}>
    <Form.Item label="Server"><Select aria-label="Server" value={routed.server_id || undefined} options={options} disabled={!options.length || Boolean(saving)} onChange={(server_id) => patchRouted({ server_id })} /></Form.Item>
    <Row gutter={16}>
      <Col xs={24} sm={12}><Form.Item label="Parent inbound tag"><Input aria-label="Parent inbound tag" value={routed.inbound_tag} onChange={(event) => patchRouted({ inbound_tag: event.target.value })} /></Form.Item></Col>
      <Col xs={24} sm={12}><Form.Item label="Protocol"><Select aria-label="Protocol" value={routed.inbound_protocol} options={protocols.map((value) => ({ value, label: value }))} onChange={(inbound_protocol) => patchRouted({ inbound_protocol })} /></Form.Item></Col>
      {([
        ["label", "Label"], ["parent_ref", "Parent ref"], ["node_name", "Node name"], ["admin_username", "Admin username"], ["admin_email", "Admin email"], ["outbound_tag", "Outbound tag"], ["marktag", "Route mark"],
      ] as const).map(([field, label]) => <Col xs={24} sm={12} key={field}><Form.Item label={label}><Input aria-label={label} value={routed[field]} onChange={(event) => patchRouted({ [field]: event.target.value })} /></Form.Item></Col>)}
    </Row>
    <Form.Item label="Outbound JSON"><Input.TextArea aria-label="Outbound JSON" value={routed.outboundText} rows={9} spellCheck={false} onChange={(event) => patchRouted({ outboundText: event.target.value })} /></Form.Item>
    <Form.Item label="Client JSON"><Input.TextArea aria-label="Client JSON" value={routed.clientText} rows={4} spellCheck={false} onChange={(event) => patchRouted({ clientText: event.target.value })} /></Form.Item>
    <Form.Item label="Sniffing excludes"><Input.TextArea aria-label="Sniffing excludes" value={routed.sniffingExcludeDomainsText} rows={2} onChange={(event) => patchRouted({ sniffingExcludeDomainsText: event.target.value })} /></Form.Item>
    <Space wrap align="start">
      <Form.Item label="Rollback on failure"><Switch aria-label="Rollback on failure" checked={routed.rollback_on_failure} onChange={(rollback_on_failure) => patchRouted({ rollback_on_failure })} /></Form.Item>
      <Form.Item label="Dispatch now"><Switch aria-label="Dispatch now" checked={routed.dispatch} onChange={(dispatch) => patchRouted({ dispatch })} /></Form.Item>
      <Form.Item label="Reality SNI excludes"><Switch aria-label="Reality SNI excludes" checked={routed.add_reality_sniffing_excludes} onChange={(add_reality_sniffing_excludes) => patchRouted({ add_reality_sniffing_excludes })} /></Form.Item>
      <Form.Item label="Timeout ms"><StrictInputNumber aria-label="Timeout ms" aria-valuemin={1} value={routed.command_timeout_ms} onChange={(value) => patchRouted({ command_timeout_ms: value ?? Number.NaN })} /></Form.Item>
    </Space>
    <Space><Button type="primary" aria-label="Create plan" icon={<PlusOutlined />} htmlType="submit" loading={saving === "routed"}>Create plan</Button><Button onClick={() => patchRouted({ outboundText: sampleOutbound() })}>Sample</Button></Space>
  </Form>;
  const rawForm = <Form layout="vertical" onFinish={submitRaw} disabled={Boolean(saving)}>
    <Form.Item label="Name"><Input aria-label="Name" value={form.name} onChange={(event) => patchForm({ name: event.target.value })} /></Form.Item>
    <Form.Item label="Description"><Input.TextArea aria-label="Description" value={form.description} rows={2} onChange={(event) => patchForm({ description: event.target.value })} /></Form.Item>
    <Space wrap><Form.Item label="Rollback on failure"><Switch aria-label="Rollback on failure" checked={form.rollback_on_failure} onChange={(rollback_on_failure) => patchForm({ rollback_on_failure })} /></Form.Item><Form.Item label="Dispatch now"><Switch aria-label="Dispatch now" checked={form.dispatch} onChange={(dispatch) => patchForm({ dispatch })} /></Form.Item></Space>
    <Form.Item label="Sample server"><Select aria-label="Sample server" options={options} disabled={!options.length || Boolean(saving)} onChange={(serverId) => patchForm({ stepsText: sampleSteps(serverId) })} /></Form.Item>
    <Form.Item label="Steps JSON"><Input.TextArea aria-label="Steps JSON" value={form.stepsText} rows={16} spellCheck={false} onChange={(event) => patchForm({ stepsText: event.target.value })} /></Form.Item>
    <Space><Button type="primary" aria-label="Create" htmlType="submit" icon={<PlusOutlined />} loading={saving === "create"}>Create</Button><Button onClick={() => patchForm({ stepsText: sampleSteps(servers[0]?.id ?? "") })}>Sample</Button></Space>
  </Form>;

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>Change sets and rollback</Typography.Title><Button aria-label="Refresh change sets" icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={error} showIcon />}
    {success && <Alert type="success" title={success} showIcon />}
    {warnings.filter((warning) => !selected?.warnings?.includes(warning)).map((warning) => <Alert key={warning} type="warning" title={warning} showIcon />)}
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={10}><Card title="Plan"><Typography.Paragraph type="secondary">Forward and rollback command sequence</Typography.Paragraph><Tabs activeKey={planMode} onChange={setPlanMode} items={[{ key: "routed", label: "Routed outbound", children: routedForm }, { key: "raw", label: "Raw steps", children: rawForm }]} /></Card></Col>
      <Col xs={24} xl={14}><Card title="Runs" extra={<Tag color="success">Free edition</Tag>}>
        <Typography.Paragraph type="secondary">{changes.length} change sets</Typography.Paragraph>
        <Table<AgentChangeSet> rowKey="id" dataSource={changes} loading={loading} pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: "No change sets yet." }} scroll={{ x: 440 }} columns={[
          { title: "Change set", key: "name", render: (_, row) => <Button type="link" disabled={Boolean(saving) || Boolean(acceptId)} onClick={() => setSelectedId(row.id)}>{row.name}</Button> },
          { title: "Steps", key: "steps", render: (_, row) => row.steps.length },
          { title: "Updated", key: "updated", render: (_, row) => row.updated_at.replace("T", " ").slice(0, 19) },
          { title: "Status", key: "status", render: (_, row) => <Tag color={statusColors[row.status]}>{row.status.replaceAll("_", " ")}</Tag> },
        ]} />
        {selected ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Descriptions title={selected.name} column={1} items={[{ key: "description", label: "Description", children: selected.description || selected.id }, { key: "status", label: "Status", children: <Tag color={statusColors[selected.status]}>{selected.status.replaceAll("_", " ")}</Tag> }]} />
          <Space wrap align="end">
            <Button aria-label="Dispatch" icon={<SendOutlined />} disabled={!actions.dispatch || Boolean(saving)} loading={saving === "dispatch"} onClick={dispatchSelected}>Dispatch</Button>
            <Form.Item label="Rollback reason" style={{ marginBottom: 0 }}><Input aria-label="Rollback reason" value={form.rollbackReason} disabled={Boolean(saving)} onChange={(event) => patchForm({ rollbackReason: event.target.value })} /></Form.Item>
            <Button aria-label={actions.retry ? "Retry rollback" : selected.status === "planned" ? "Cancel plan" : "Rollback"} icon={<RollbackOutlined />} disabled={!actions.rollback || Boolean(saving)} loading={saving === "rollback"} onClick={rollbackSelected}>{actions.retry ? "Retry rollback" : selected.status === "planned" ? "Cancel plan" : "Rollback"}</Button>
            {actions.accept && <Button disabled={Boolean(saving)} onClick={() => { setAcceptanceReason(""); setAcceptanceConfirmed(false); setAcceptId(selected.id); }}>Accept current state</Button>}
          </Space>
          {Boolean(selected.held_server_ids?.length) && <Typography.Text>{selected.held_server_ids?.length} nodes reserved</Typography.Text>}
          {Boolean(selected.blocking_command_ids?.length) && <Alert type="info" title={`Waiting for command results: ${selected.blocking_command_ids?.join(", ")}`} showIcon />}
          {(selected.warnings ?? []).map((warning) => <Alert key={warning} type="warning" title={warning} showIcon />)}
          {selected.rollback_reason && <Typography.Paragraph>{selected.rollback_reason}</Typography.Paragraph>}
          {selected.resolution_reason && <Typography.Paragraph>{selected.resolution_reason}</Typography.Paragraph>}
          {selected.steps.map((step) => <Card key={step.id} size="small" title={`${step.sequence}. ${step.label}`} extra={<Tag>{step.server_id.slice(0, 8)}</Tag>}>
            <Typography.Paragraph type="secondary">{step.server_name || servers.find((server) => server.id === step.server_id)?.name || "Unknown server"}{step.archived ? " (archived)" : ""}</Typography.Paragraph>
            <Descriptions column={{ xs: 1, sm: 2 }} items={[{ key: "forward", label: "Forward", children: <CommandSummary request={step.forward} command={step.forward_command} /> }, { key: "rollback", label: "Rollback", children: <CommandSummary request={step.rollback} command={step.rollback_command} /> }]} />
            <CommandInspector commands={[...new Map([step.forward_command, ...(step.rollback_history ?? []), step.rollback_command].filter((command): command is AgentCommand => Boolean(command)).map((command) => [command.id, command])).values()]} streamFramesByCommand={{}} />
          </Card>)}
          {lastCommands.length > 0 && <Card size="small" title="Last commands"><CommandInspector commands={lastCommands} streamFramesByCommand={{}} /></Card>}
        </Space> : changes.length > 0 ? <Empty description="Select a change set" /> : null}
      </Card></Col>
    </Row>
    <Modal title="Accept current state" open={Boolean(acceptId)} onCancel={() => { if (!saving) setAcceptId(""); }} closable={!saving} mask={{ closable: !saving }} keyboard={!saving} onOk={acceptSelected} okText="Accept state" confirmLoading={saving === "accept"} okButtonProps={{ disabled: !acceptanceConfirmed || !acceptanceReason.trim() || Boolean(saving) }} cancelButtonProps={{ disabled: Boolean(saving) }}>
      {error && <Alert type="error" title={error} showIcon />}
      <Form layout="vertical" disabled={Boolean(saving)}><Form.Item label="Resolution reason"><Input.TextArea aria-label="Resolution reason" rows={3} value={acceptanceReason} onChange={(event) => setAcceptanceReason(event.target.value)} /></Form.Item><Checkbox checked={acceptanceConfirmed} onChange={(event) => setAcceptanceConfirmed(event.target.checked)}>I have checked the nodes and accept any remaining changes</Checkbox></Form>
    </Modal>
  </Space>;
}
