import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Flex, Form, Input, Modal, Row, Select, Spin, Switch, Tag, Typography } from "antd";
import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import PlanNodeAliases from "./PlanNodeAliases";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor";
import StrictInputNumber from "./StrictInputNumber";
import type { ManagedNode, SubscriptionAccessResponse } from "../../domain/subscriptions";
import type { SubscriptionTemplate } from "../../domain/subscription-templates";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../../services/subscriptions";
import { getPlanManagement, planSettings, removePlan, savePlan, type PlanManagementRead, type PlanManagementResult, type PlanOperation, type PlanSettings } from "../../services/plan-management";
import { listSubscriptionTemplates } from "../../services/subscription-templates";

export interface PlanManagementDialogProps { id: string; mode: PlanOperation; nodes: ManagedNode[]; open: boolean; onOpenChange: (open: boolean) => void; onUpdated?: () => void }
export default function PlanManagementDialog(props: PlanManagementDialogProps) { return props.open ? <PlanContent key={`${props.id}:${props.mode}`} {...props} /> : null; }
function PlanContent({ id, mode, nodes, onOpenChange, onUpdated }: PlanManagementDialogProps) {
  const [detail, setDetail] = useState<PlanManagementRead | null>(null), [form, setForm] = useState<PlanSettings | null>(null);
  const [result, setResult] = useState<PlanManagementResult | null>(null), [templates, setTemplates] = useState<SubscriptionTemplate[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [acknowledgment, setAcknowledgment] = useState(false), [confirmName, setConfirmName] = useState("");
  const [aliasesValid, setAliasesValid] = useState(true), [rulesValid, setRulesValid] = useState(true);
  const [states, setStates] = useState<Record<string, SubscriptionAccessResponse>>({}), [stateErrors, setStateErrors] = useState<Record<string, string>>({});
  const version = useRef(0), pollVersion = useRef(0), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const updated = useRef(onUpdated); updated.current = onUpdated;
  const title = mode === "edit" ? "编辑套餐" : mode === "remove" ? "移除套餐" : "取消套餐分配";
  const removed = !!result && mode !== "edit";
  const expectedName = mode === "unassign" ? id : detail?.plan.name ?? "";
  const canSubmit = !busy && !!detail && !!form && acknowledgment && !removed
    && (mode === "edit" ? !!form.name.trim() && form.node_ids.length > 0 && aliasesValid && rulesValid : confirmName === expectedName);
  function stop() { clearTimeout(timer.current); ++pollVersion.current; }
  async function load() {
    const run = ++version.current; stop(); setStates({}); setStateErrors({}); setDetail(null); setForm(null); setResult(null); setAcknowledgment(false); setConfirmName(""); setError("");
    if (!id) return; setBusy(true);
    try {
      const [value, library] = await Promise.all([getPlanManagement(id, mode), listSubscriptionTemplates()]);
      if (run === version.current) { setTemplates(library.templates); setDetail(value); setForm(planSettings(value.plan)); }
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "请求套餐信息失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { ++version.current; stop(); }; }, []);
  async function poll(run: number, names: string[], retry?: string) {
    clearTimeout(timer.current); const current = ++pollVersion.current;
    await Promise.all(names.map(async username => {
      try {
        const value = await (retry === username ? syncSubscriptionAccess : getSubscriptionAccess)(username);
        if (run !== version.current || current !== pollVersion.current) return;
        setStates(previous => ({ ...previous, [username]: value }));
        setStateErrors(previous => { const next = { ...previous }; delete next[username]; return next; });
      } catch (failure) {
        if (run === version.current && current === pollVersion.current) setStateErrors(previous => ({ ...previous, [username]: failure instanceof Error ? failure.message : "无法读取访问状态" }));
      }
    }));
    if (run === version.current && current === pollVersion.current) timer.current = setTimeout(() => void poll(run, names), 5000);
  }
  async function submit() {
    if (!canSubmit || !detail || !form) return;
    const run = ++version.current; stop(); setBusy(true); setError(""); setStates({}); setStateErrors({});
    try {
      const value = mode === "edit" ? await savePlan(id, form, detail.revision) : await removePlan(id, mode, detail.revision, confirmName);
      if (run !== version.current) return;
      setResult(value); setAcknowledgment(false); updated.current?.();
      if (value.plan && value.revision) { setDetail({ ...detail, plan: value.plan, revision: value.revision }); setForm(planSettings(value.plan)); }
      void poll(run, value.affected_users);
    } catch (failure) { if (run === version.current) setError(failure instanceof Error ? failure.message : "更新套餐失败"); }
    finally { if (run === version.current) setBusy(false); }
  }
  function patch(change: Partial<PlanSettings>) { setForm(previous => previous ? { ...previous, ...change } : previous); }
  function selectNodes(node_ids: string[]) {
    if (!form) return;
    const trim = (value: Record<string, number>) => Object.fromEntries(Object.entries(value).filter(([key]) => node_ids.includes(key)));
    patch({ node_ids, node_multipliers: trim(form.node_multipliers), node_speed_limits: trim(form.node_speed_limits), node_device_limits: trim(form.node_device_limits) });
  }
  function override(field: "node_multipliers" | "node_speed_limits" | "node_device_limits", nodeId: string, value: number | null) {
    if (!form) return; const next = { ...form[field] }; if (value === null) delete next[nodeId]; else next[nodeId] = value; patch({ [field]: next });
  }
  return <Modal open title={title} width={760} centered styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }} destroyOnHidden mask={{ closable: !busy }} closable={!busy} keyboard={!busy} onCancel={() => !busy && onOpenChange(false)}
    footer={<Flex justify="space-between"><Button disabled={busy} onClick={() => onOpenChange(false)}>{result ? "关闭" : "取消"}</Button>
      {!removed && <Button type="primary" aria-label={mode === "edit" ? "保存" : mode === "remove" ? "移除" : "取消分配"} aria-busy={busy} danger={mode !== "edit"} disabled={!canSubmit} loading={busy} onClick={() => void submit()}>{mode === "edit" ? "保存" : mode === "remove" ? "移除" : "取消分配"}</Button>}</Flex>}>
    <Flex vertical gap="middle">
      <Button icon={<ReloadOutlined />} aria-label="重新加载套餐详情" disabled={busy || removed} onClick={() => void load()}>重新加载</Button>
      {busy && <Spin />}{error && <Alert type="error" title={zhMessage(error)} showIcon />}
      {detail && form && <>
        {result && <Alert type="info" title={`${mode === "edit" ? "套餐已保存" : mode === "remove" ? "套餐已移除" : "套餐分配已取消"}。正在跟踪 ${result.commands.length} 条 Agent 命令。`} showIcon />}
        {mode === "edit" ? <Form layout="vertical" style={{ paddingInline: 8 }} preserve={false} disabled={busy} onFinish={() => void submit()}>
          <Form.Item label="套餐名称"><Input aria-label="套餐名称" value={form.name} onChange={event => patch({ name: event.target.value })} maxLength={120} /></Form.Item>
          <Form.Item label="说明"><Input.TextArea aria-label="说明" value={form.description} onChange={event => patch({ description: event.target.value })} rows={2} maxLength={1000} /></Form.Item>
          <Row gutter={16}>
            {([{ field: "traffic_limit_gb", label: "流量配额（GiB）", min: 0.000001 }, { field: "cycle_days", label: "新分配的有效期（天）", min: 1 }, { field: "speed_limit_mbps", label: "默认速度（Mbps）", min: 0 }, { field: "device_limit", label: "默认连接数", min: 0 }] as const).map(item => <Col xs={24} sm={12} key={item.field}><Form.Item label={item.label}>
              <StrictInputNumber aria-label={item.label} value={form[item.field]} aria-valuemin={item.min} style={{ width: "100%" }} onChange={number => patch({ [item.field]: number ?? Number.NaN })} />
            </Form.Item></Col>)}
            <Col xs={24} sm={12}><Form.Item label="流量计费系数"><Select aria-label="流量计费系数" value={form.traffic_mode} options={[{ label: "单向计费（×1）", value: "oneway" }, { label: "双向计费（×2）", value: "twoway" }]} onChange={traffic_mode => patch({ traffic_mode })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="新分配的重置日（UTC）"><Select aria-label="新分配的重置日（UTC）" value={form.reset_day} disabled={busy || !form.is_reset} options={Array.from({ length: 31 }, (_, i) => ({ label: i + 1, value: i + 1 }))} onChange={reset_day => patch({ reset_day })} /></Form.Item></Col>
          </Row>
          <Form.Item label="新分配的套餐按月重置"><Switch aria-label="新分配的套餐按月重置" checked={form.is_reset} onChange={is_reset => patch({ is_reset })} /></Form.Item>
          <Row gutter={16}>{(["clash", "surge"] as const).map(format => <Col xs={24} sm={12} key={format}><Form.Item label={format === "clash" ? "Clash 模板" : "Surge 模板"}>
            <Select aria-label={format === "clash" ? "Clash 模板" : "Surge 模板"} value={form[`${format}_template_id`] ?? undefined} allowClear options={templates.filter(item => item.format === format).map(item => ({ label: item.name, value: item.id }))} onChange={value => patch({ [`${format}_template_id`]: value ?? null })} />
          </Form.Item></Col>)}</Row>
          <Form.Item label="套餐节点" required help={!form.node_ids.length ? "套餐至少需要一个节点。" : `已选择 ${form.node_ids.length} 个节点。`}><Select aria-label="套餐节点" mode="multiple" optionFilterProp="label" value={form.node_ids} options={nodes.filter(node => !node.removal_id).map(node => ({ label: node.name, value: node.id }))} onChange={selectNodes} /></Form.Item>
          <PlanNodeAliases nodes={form.node_ids.map(nodeId => ({ id: nodeId, name: nodes.find(node => node.id === nodeId)?.name ?? nodeId }))}
            value={form.node_name_overrides} onChange={node_name_overrides => patch({ node_name_overrides })} enabled={form.node_name_override_enabled} onEnabledChange={node_name_override_enabled => patch({ node_name_override_enabled })} onValid={setAliasesValid} disabled={busy}
            renderNode={node => <Row gutter={16}>{([{ field: "node_multipliers", label: "计费倍率", suffix: "计费倍率", placeholder: "1", min: 0.000001 }, { field: "node_speed_limits", label: "速度（Mbps）", suffix: "速度", placeholder: "继承", min: 0 }, { field: "node_device_limits", label: "连接数", suffix: "连接数", placeholder: "继承", min: 0 }] as const).map(item => <Col xs={24} sm={8} key={item.field}><Form.Item label={item.label}><StrictInputNumber aria-label={`${node.name}：${item.suffix}`} allowEmpty value={form[item.field][node.id] ?? null} aria-valuemin={item.min} placeholder={item.placeholder} style={{ width: "100%" }} disabled={busy} onChange={number => override(item.field, node.id, number)} /></Form.Item></Col>)}</Row>} />
          <AutoSpeedRuleEditor value={form.auto_speed_rules} onChange={auto_speed_rules => patch({ auto_speed_rules })} onValid={setRulesValid} disabled={busy} />
        </Form> : <Typography.Title level={5}>{detail.plan.name}</Typography.Title>}
        <section aria-label="受影响的用户"><Typography.Title level={5}>用户</Typography.Title>{!detail.users.length && "无"}{detail.users.map(user => <Flex key={user.username} justify="space-between"><span>{user.username}</span><Tag>{user.managed ? "托管" : "仅预览"}</Tag></Flex>)}</section>
        {(result?.warnings ?? (mode === "edit" ? detail.warnings : [])).map(warning => <Alert key={warning} type="warning" title={zhMessage(warning)} showIcon />)}
        {!removed && <>
          <Alert type="warning" title={`${mode === "edit" ? "节点和限制变更会应用于托管用户。" : "订阅将不可用。已保存的凭据和用量会保留；远程撤销需要 Agent 确认。"}应用运行时变更可能重启 Xray 并断开当前客户端。离线或失败的 Agent 会继续保持待处理或失败状态。`} showIcon />
          {mode !== "edit" && <Form.Item label={mode === "unassign" ? "确认用户名" : "确认套餐名称"}><Input aria-label={mode === "unassign" ? "确认用户名" : "确认套餐名称"} value={confirmName} onChange={event => setConfirmName(event.target.value)} disabled={busy} /></Form.Item>}
          <Checkbox checked={acknowledgment} onChange={event => setAcknowledgment(event.target.checked)} disabled={busy}>我接受运行时重启及变更待确认的影响</Checkbox>
        </>}
        {!!result?.affected_users.length && <section aria-label="套餐部署状态"><Typography.Title level={5}>Agent 状态</Typography.Title>
          {result.affected_users.map(username => <Card key={username} size="small" title={username} extra={<Button icon={<SyncOutlined />} aria-label={`重试 ${username} 的访问同步`} disabled={busy} onClick={() => void poll(version.current, result.affected_users, username)} />}>
            {!states[username] && !stateErrors[username] && <Spin />}{states[username] && !states[username].managed && "暂无托管凭据"}
            {states[username]?.servers.map(server => <Flex key={server.server_id} vertical gap="small"><Flex justify="space-between"><span>{server.server_name}</span><Tag color={server.status === "applied" ? "success" : server.status === "failed" ? "error" : "warning"}>{zhStatus(server.status)}</Tag></Flex>{server.error && <Alert type="error" title={zhMessage(server.error)} />}</Flex>)}
            {stateErrors[username] && <Alert type="error" title={zhMessage(stateErrors[username])} showIcon />}
          </Card>)}
        </section>}
      </>}
    </Flex>
  </Modal>;
}
