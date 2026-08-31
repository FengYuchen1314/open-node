import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Descriptions, Empty, Form, Input, Modal, Row, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined, ReloadOutlined, RollbackOutlined, SendOutlined } from "@ant-design/icons";
import { changeSetActions, type AgentChangeSet, type AgentChangeSetStatus, type AgentChangeSetStepCreateRequest } from "../../domain/changes";
import type { AgentCommand, AgentCommandCreateRequest, ServerSummary } from "../../domain/inventory";
import { listServers } from "../../services/inventory";
import { acceptChangeSet, createChangeSet, createRoutedOutboundChangeSet, dispatchChangeSet, listChangeSets, rollbackChangeSet } from "../../services/changes";
import CommandInspector from "../components/CommandInspector";
import StrictInputNumber from "../components/StrictInputNumber";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

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
  if (!text.trim()) throw new Error(`请填写${label}。`);
  const value: unknown = JSON.parse(text);
  if (!isRecord(value)) throw new Error(`${label} 必须是 JSON 对象。`);
  return value;
}
function stepsFrom(text: string): AgentChangeSetStepCreateRequest[] {
  const value: unknown = JSON.parse(text || "[]");
  if (!Array.isArray(value) || !value.length) throw new Error("步骤必须是非空 JSON 数组。");
  value.forEach((step, index) => {
    if (!isRecord(step)) throw new Error(`第 ${index + 1} 步必须是 JSON 对象。`);
    if (typeof step.server_id !== "string" || !step.server_id.trim()) throw new Error(`第 ${index + 1} 步缺少 server_id。`);
    if (!isRecord(step.forward)) throw new Error(`第 ${index + 1} 步缺少 forward 命令。`);
    if (step.rollback !== undefined && step.rollback !== null && !isRecord(step.rollback)) throw new Error(`第 ${index + 1} 步的 rollback 必须是对象或 null。`);
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
    <Typography.Text code style={{ overflowWrap: "anywhere" }}>{request ? commandText(request) : "无"}</Typography.Text>
    {request && commandBody(request) && <Typography.Paragraph style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{commandBody(request)}</Typography.Paragraph>}
    <Tag color={color}>{command ? zhStatus(command.status) : "未排队"}</Tag>
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
      if (mounted.current && !background && revision === refreshRevision.current) setError(failure instanceof Error ? failure.message : "请求失败。");
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
    catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "请求失败。"); }
    finally { mutationBusy.current = false; if (mounted.current) setSaving(""); }
  }
  function submitRouted() {
    void mutate("routed", async () => {
      if (!routed.server_id.trim()) throw new Error("请选择服务器。");
      if (!routed.inbound_tag.trim()) throw new Error("请填写入站标签。");
      if (!routed.label.trim()) throw new Error("请填写名称。");
      if (!Number.isFinite(routed.command_timeout_ms) || routed.command_timeout_ms <= 0) throw new Error("超时时间必须大于 0。");
      const response = await createRoutedOutboundChangeSet({
        server_id: routed.server_id.trim(), inbound_tag: routed.inbound_tag.trim(), inbound_protocol: routed.inbound_protocol.trim() || "vless", label: routed.label.trim(),
        outbound: jsonObject(routed.outboundText, "出站 JSON"), parent_ref: optionalText(routed.parent_ref), admin_username: routed.admin_username.trim() || "admin", admin_email: optionalText(routed.admin_email),
        outbound_tag: optionalText(routed.outbound_tag), marktag: optionalText(routed.marktag), node_name: optionalText(routed.node_name), client: routed.clientText.trim() ? jsonObject(routed.clientText, "客户端 JSON") : null,
        sniffing_exclude_domains: splitDomains(routed.sniffingExcludeDomainsText), add_reality_sniffing_excludes: routed.add_reality_sniffing_excludes,
        command_timeout_ms: routed.command_timeout_ms, rollback_on_failure: routed.rollback_on_failure, dispatch: routed.dispatch,
      });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `已创建路由出站方案，并下发 ${response.commands.length} 条命令。` : "已创建路由出站变更集。");
      await refresh();
    });
  }
  function submitRaw() {
    void mutate("create", async () => {
      if (!form.name.trim()) throw new Error("请填写名称。");
      const response = await createChangeSet({ name: form.name.trim(), description: form.description.trim(), rollback_on_failure: form.rollback_on_failure, dispatch: form.dispatch, steps: stepsFrom(form.stepsText) });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `已创建并下发 ${response.commands.length} 条命令。` : "变更集已创建。");
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
      setSuccess(response.commands.length ? `已下发 ${response.commands.length} 条命令。` : "没有新增的正向命令。");
      await refresh();
    });
  }
  function rollbackSelected() {
    if (!selected || !actions.rollback) return;
    void mutate("rollback", async () => {
      const response = await rollbackChangeSet(selected.id, { reason: form.rollbackReason.trim() });
      if (!mounted.current) return;
      remember(response.change_set, response.commands, response.warnings);
      setSuccess(response.commands.length ? `已排队 ${response.commands.length} 条回滚命令。` : "没有新增的回滚命令。");
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
      setSuccess("已接受当前状态并释放节点预留。");
      await refresh();
    });
  }

  const routedForm = <Form layout="vertical" onFinish={submitRouted} disabled={Boolean(saving)}>
    <Form.Item label="服务器"><Select aria-label="服务器" value={routed.server_id || undefined} options={options} disabled={!options.length || Boolean(saving)} onChange={(server_id) => patchRouted({ server_id })} /></Form.Item>
    <Row gutter={16}>
      <Col xs={24} sm={12}><Form.Item label="父级入站标签"><Input aria-label="父级入站标签" value={routed.inbound_tag} onChange={(event) => patchRouted({ inbound_tag: event.target.value })} /></Form.Item></Col>
      <Col xs={24} sm={12}><Form.Item label="协议"><Select aria-label="协议" value={routed.inbound_protocol} options={protocols.map((value) => ({ value, label: value }))} onChange={(inbound_protocol) => patchRouted({ inbound_protocol })} /></Form.Item></Col>
      {([
        ["label", "名称"], ["parent_ref", "父级引用"], ["node_name", "节点名称"], ["admin_username", "管理员用户名"], ["admin_email", "管理员邮箱"], ["outbound_tag", "出站标签"], ["marktag", "路由标记"],
      ] as const).map(([field, label]) => <Col xs={24} sm={12} key={field}><Form.Item label={label}><Input aria-label={label} value={routed[field]} onChange={(event) => patchRouted({ [field]: event.target.value })} /></Form.Item></Col>)}
    </Row>
    <Form.Item label="出站 JSON"><Input.TextArea aria-label="出站 JSON" value={routed.outboundText} rows={9} spellCheck={false} onChange={(event) => patchRouted({ outboundText: event.target.value })} /></Form.Item>
    <Form.Item label="客户端 JSON"><Input.TextArea aria-label="客户端 JSON" value={routed.clientText} rows={4} spellCheck={false} onChange={(event) => patchRouted({ clientText: event.target.value })} /></Form.Item>
    <Form.Item label="嗅探排除域名"><Input.TextArea aria-label="嗅探排除域名" value={routed.sniffingExcludeDomainsText} rows={2} onChange={(event) => patchRouted({ sniffingExcludeDomainsText: event.target.value })} /></Form.Item>
    <Space wrap align="start">
      <Form.Item label="失败时回滚"><Switch aria-label="失败时回滚" checked={routed.rollback_on_failure} onChange={(rollback_on_failure) => patchRouted({ rollback_on_failure })} /></Form.Item>
      <Form.Item label="立即下发"><Switch aria-label="立即下发" checked={routed.dispatch} onChange={(dispatch) => patchRouted({ dispatch })} /></Form.Item>
      <Form.Item label="排除 Reality SNI"><Switch aria-label="排除 Reality SNI" checked={routed.add_reality_sniffing_excludes} onChange={(add_reality_sniffing_excludes) => patchRouted({ add_reality_sniffing_excludes })} /></Form.Item>
      <Form.Item label="超时时间（毫秒）"><StrictInputNumber aria-label="超时时间（毫秒）" aria-valuemin={1} value={routed.command_timeout_ms} onChange={(value) => patchRouted({ command_timeout_ms: value ?? Number.NaN })} /></Form.Item>
    </Space>
    <Space><Button type="primary" aria-label="创建方案" icon={<PlusOutlined />} htmlType="submit" loading={saving === "routed"}>创建方案</Button><Button aria-label="填入示例" onClick={() => patchRouted({ outboundText: sampleOutbound() })}>填入示例</Button></Space>
  </Form>;
  const rawForm = <Form layout="vertical" onFinish={submitRaw} disabled={Boolean(saving)}>
    <Form.Item label="变更集名称"><Input aria-label="变更集名称" value={form.name} onChange={(event) => patchForm({ name: event.target.value })} /></Form.Item>
    <Form.Item label="说明"><Input.TextArea aria-label="说明" value={form.description} rows={2} onChange={(event) => patchForm({ description: event.target.value })} /></Form.Item>
    <Space wrap><Form.Item label="失败时回滚"><Switch aria-label="失败时回滚" checked={form.rollback_on_failure} onChange={(rollback_on_failure) => patchForm({ rollback_on_failure })} /></Form.Item><Form.Item label="立即下发"><Switch aria-label="立即下发" checked={form.dispatch} onChange={(dispatch) => patchForm({ dispatch })} /></Form.Item></Space>
    <Form.Item label="示例服务器"><Select aria-label="示例服务器" options={options} disabled={!options.length || Boolean(saving)} onChange={(serverId) => patchForm({ stepsText: sampleSteps(serverId) })} /></Form.Item>
    <Form.Item label="步骤 JSON"><Input.TextArea aria-label="步骤 JSON" value={form.stepsText} rows={16} spellCheck={false} onChange={(event) => patchForm({ stepsText: event.target.value })} /></Form.Item>
    <Space><Button type="primary" aria-label="创建" htmlType="submit" icon={<PlusOutlined />} loading={saving === "create"}>创建</Button><Button aria-label="填入示例" onClick={() => patchForm({ stepsText: sampleSteps(servers[0]?.id ?? "") })}>填入示例</Button></Space>
  </Form>;

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>变更集与回滚</Typography.Title><Button aria-label="刷新变更集" icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}
    {success && <Alert type="success" title={success} showIcon />}
    {warnings.filter((warning) => !selected?.warnings?.includes(warning)).map((warning) => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={10}><Card title="方案"><Typography.Paragraph type="secondary">正向与回滚命令序列</Typography.Paragraph><Tabs activeKey={planMode} onChange={setPlanMode} items={[{ key: "routed", label: "路由出站", children: routedForm }, { key: "raw", label: "原始步骤", children: rawForm }]} /></Card></Col>
      <Col xs={24} xl={14}><Card title="执行记录" extra={<Tag color="success">免费版</Tag>}>
        <Typography.Paragraph type="secondary">{changes.length} 个变更集</Typography.Paragraph>
        <Table<AgentChangeSet> rowKey="id" dataSource={changes} loading={loading} pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: "暂无变更集。" }} scroll={{ x: 440 }} columns={[
          { title: "变更集", key: "name", render: (_, row) => <Button type="link" disabled={Boolean(saving) || Boolean(acceptId)} onClick={() => setSelectedId(row.id)}>{row.name}</Button> },
          { title: "步骤", key: "steps", render: (_, row) => row.steps.length },
          { title: "更新时间", key: "updated", render: (_, row) => new Date(row.updated_at).toLocaleString("zh-CN", { timeZone: "UTC", hour12: false }) },
          { title: "状态", key: "status", render: (_, row) => <Tag color={statusColors[row.status]}>{zhStatus(row.status)}</Tag> },
        ]} />
        {selected ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <Descriptions title={selected.name} column={1} items={[{ key: "description", label: "说明", children: selected.description || selected.id }, { key: "status", label: "状态", children: <Tag color={statusColors[selected.status]}>{zhStatus(selected.status)}</Tag> }]} />
          <Space wrap align="end">
            <Button aria-label="下发" icon={<SendOutlined />} disabled={!actions.dispatch || Boolean(saving)} loading={saving === "dispatch"} onClick={dispatchSelected}>下发</Button>
            <Form.Item label="回滚原因" style={{ marginBottom: 0 }}><Input aria-label="回滚原因" value={form.rollbackReason} disabled={Boolean(saving)} onChange={(event) => patchForm({ rollbackReason: event.target.value })} /></Form.Item>
            <Button aria-label={actions.retry ? "重试回滚" : selected.status === "planned" ? "取消方案" : "回滚"} icon={<RollbackOutlined />} disabled={!actions.rollback || Boolean(saving)} loading={saving === "rollback"} onClick={rollbackSelected}>{actions.retry ? "重试回滚" : selected.status === "planned" ? "取消方案" : "回滚"}</Button>
            {actions.accept && <Button aria-label="接受当前状态" disabled={Boolean(saving)} onClick={() => { setAcceptanceReason(""); setAcceptanceConfirmed(false); setAcceptId(selected.id); }}>接受当前状态</Button>}
          </Space>
          {Boolean(selected.held_server_ids?.length) && <Typography.Text>已预留 {selected.held_server_ids?.length} 个节点</Typography.Text>}
          {Boolean(selected.blocking_command_ids?.length) && <Alert type="info" title={`等待命令结果：${selected.blocking_command_ids?.join("，")}`} showIcon />}
          {(selected.warnings ?? []).map((warning) => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
          {selected.rollback_reason && <Typography.Paragraph>{selected.rollback_reason}</Typography.Paragraph>}
          {selected.resolution_reason && <Typography.Paragraph>{selected.resolution_reason}</Typography.Paragraph>}
          {selected.steps.map((step) => <Card key={step.id} size="small" title={`${step.sequence}. ${step.label}`} extra={<Tag>{step.server_id.slice(0, 8)}</Tag>}>
            <Typography.Paragraph type="secondary">{step.server_name || servers.find((server) => server.id === step.server_id)?.name || "未知服务器"}{step.archived ? "（已归档）" : ""}</Typography.Paragraph>
            <Descriptions column={{ xs: 1, sm: 2 }} items={[{ key: "forward", label: "正向", children: <CommandSummary request={step.forward} command={step.forward_command} /> }, { key: "rollback", label: "回滚", children: <CommandSummary request={step.rollback} command={step.rollback_command} /> }]} />
            <CommandInspector commands={[...new Map([step.forward_command, ...(step.rollback_history ?? []), step.rollback_command].filter((command): command is AgentCommand => Boolean(command)).map((command) => [command.id, command])).values()]} streamFramesByCommand={{}} />
          </Card>)}
          {lastCommands.length > 0 && <Card size="small" title="最近命令"><CommandInspector commands={lastCommands} streamFramesByCommand={{}} /></Card>}
        </Space> : changes.length > 0 ? <Empty description="请选择变更集" /> : null}
      </Card></Col>
    </Row>
    <Modal title="接受当前状态" open={Boolean(acceptId)} onCancel={() => { if (!saving) setAcceptId(""); }} closable={!saving} mask={{ closable: !saving }} keyboard={!saving} onOk={acceptSelected} okText="接受状态" confirmLoading={saving === "accept"} okButtonProps={{ "aria-label": "接受状态", disabled: !acceptanceConfirmed || !acceptanceReason.trim() || Boolean(saving) }} cancelButtonProps={{ disabled: Boolean(saving) }}>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}
      <Form layout="vertical" disabled={Boolean(saving)}><Form.Item label="处理原因"><Input.TextArea aria-label="处理原因" rows={3} value={acceptanceReason} onChange={(event) => setAcceptanceReason(event.target.value)} /></Form.Item><Checkbox checked={acceptanceConfirmed} onChange={(event) => setAcceptanceConfirmed(event.target.checked)}>我已检查节点并接受所有剩余变更</Checkbox></Form>
    </Modal>
  </Space>;
}
