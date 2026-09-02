import { CopyOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from "../../ui/icons";
import { Alert, Button, Card, Col, Flex, Form, Input, Popconfirm, Row, Select, Space, Switch, Table, Tag, Typography } from "../../ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { latencyCommandTimeout, routeTargets, selectedRouteTargets } from "../../domain/diagnostics";
import type { ServerSummary } from "../../domain/inventory";
import type { ProbeSettings, ProbeSettingsUpdate, ProbeTask, ProbeTaskKind } from "../../domain/probe";
import { listServers } from "../../services/inventory";
import { clearProbeAccessToken, createProbeAccessToken, createProbeTask, dispatchDueProbeTasks, getPublicProbeSettings, listProbeTasks, updateProbeTask, updatePublicProbeSettings } from "../../services/probe";
import { useAsyncScope } from "../hooks/useAsyncScope";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";
import RouteProbeFields from "./RouteProbeFields";
import StrictInputNumber from "./StrictInputNumber";

export interface ProbeAdministrationPanelProps {
  accessToken: string;
  onSettings: (settings: ProbeSettings) => void;
  onAccessToken: (token: string, settings: ProbeSettings) => void;
  onRefresh: () => void;
}
const defaults = {
  enabled: true, has_access_token: false, require_access_token: false, title: "Open Node 探针",
  description: "兼容 MMWX 探针的节点状态页面，无需授权许可。", logo: "", refresh_interval_sec: 5,
  theme: "open-node", color_mode: "light" as "light" | "dark" | "system", revision: "open-node",
  show_globe: false, show_resource_heatmap: true, show_traffic_quota: true, show_daily_trend: false,
  show_traffic_hotspots: false, show_traffic_7d: false, show_return_route: false, show_renewal_timeline: false, show_health_score: true,
};
function formSettings(value: ProbeSettings) {
  return { ...defaults, ...value, has_access_token: value.has_access_token === true, require_access_token: value.require_access_token === true,
    title: value.title ?? defaults.title, description: value.description ?? defaults.description, logo: value.logo ?? "",
    refresh_interval_sec: value.refresh_interval_sec ?? 5, theme: value.appearance?.theme ?? "open-node",
    color_mode: value.appearance?.color_mode ?? "light", revision: value.appearance?.revision ?? "open-node" };
}
type SettingsForm = ReturnType<typeof formSettings>;
function validInteger(value: number | undefined, minimum: number, maximum: number) {
  return value !== undefined && Number.isInteger(value) && value >= minimum && value <= maximum;
}
const taskLabels: Record<ProbeTaskKind, string> = { domain_latency: "域名延迟", return_route: "回程路由", system: "系统信息" };
const toggles = [
  ["enabled", "已启用"], ["require_access_token", "Worker 令牌"], ["show_globe", "地区"],
  ["show_resource_heatmap", "系统"], ["show_traffic_quota", "流量"], ["show_health_score", "健康状态"],
  ["show_daily_trend", "每日趋势"], ["show_traffic_hotspots", "流量热点"], ["show_traffic_7d", "近 7 天流量"],
  ["show_return_route", "回程路由"], ["show_renewal_timeline", "续费时间"],
] as const;

export default function ProbeAdministrationPanel({ accessToken, onSettings, onAccessToken, onRefresh }: ProbeAdministrationPanelProps) {
  const loadScope = useAsyncScope();
  const settingsScope = useAsyncScope();
  const tasksScope = useAsyncScope();
  const taskOperation = useAsyncScope();
  const settingsBusy = useRef(false);
  const taskBusy = useRef(false);
  const callbacks = useRef({ onSettings, onAccessToken, onRefresh });
  callbacks.current = { onSettings, onAccessToken, onRefresh };
  const [form, setForm] = useState<SettingsForm>({ ...defaults });
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [tasks, setTasks] = useState<ProbeTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const [taskSaving, setTaskSaving] = useState("");
  const [taskError, setTaskError] = useState("");
  const [taskNotice, setTaskNotice] = useState("");
  const [taskForm, setTaskForm] = useState({ server_id: "", kind: "domain_latency" as ProbeTaskKind, domains: "example.com", interval_sec: 300, domain_timeout_ms: 2000, allow_icmp: false, targets: routeTargets(), ip_version: 4 as 4 | 6, return_route_timeout_seconds: 25 });
  const dueCount = tasks.filter(task => task.enabled && Date.parse(task.next_run_at) <= Date.now()).length;
  const settingsBlocked = !settingsLoaded || loadingSettings || Boolean(saving);
  const tasksBlocked = !tasksLoaded || tasksLoading || Boolean(taskSaving);

  const loadSettings = useCallback(async () => {
    if (settingsBusy.current) return;
    const current = loadScope.begin(); setSettingsLoaded(false); setLoadingSettings(true); setError("");
    try {
      const result = await getPublicProbeSettings();
      if (loadScope.isCurrent(current)) { setForm(formSettings(result.settings)); setSettingsLoaded(true); callbacks.current.onSettings(result.settings); }
    } catch (cause) {
      if (loadScope.isCurrent(current)) setError(zhMessage(cause, "暂时无法加载探针设置。"));
    } finally { if (loadScope.isCurrent(current)) setLoadingSettings(false); }
  }, [loadScope]);
  const loadTasks = useCallback(async () => {
    const current = tasksScope.begin(); setTasksLoaded(false); setTasksLoading(true); setTaskError("");
    try {
      const [inventory, response] = await Promise.all([listServers(), listProbeTasks()]);
      if (tasksScope.isCurrent(current)) {
        setServers(inventory); setTasks(response.tasks); setTasksLoaded(true);
        setTaskForm(previous => ({ ...previous, server_id: inventory.some(server => server.id === previous.server_id) ? previous.server_id : inventory[0]?.id ?? "" }));
      }
    } catch (cause) {
      if (tasksScope.isCurrent(current)) setTaskError(zhMessage(cause, "暂时无法加载探针任务。"));
    } finally { if (tasksScope.isCurrent(current)) setTasksLoading(false); }
  }, [tasksScope]);
  useEffect(() => { void loadSettings(); void loadTasks(); }, [loadSettings, loadTasks]);
  async function settingsAction(action: "save" | "generate" | "clear") {
    if (!settingsLoaded || loadingSettings || settingsBusy.current) return;
    if (action === "save" && !validInteger(form.refresh_interval_sec, 1, 60)) {
      setError("刷新间隔必须为 1 至 60 秒的整数。"); setNotice(""); return;
    }
    settingsBusy.current = true; const current = settingsScope.begin(); loadScope.invalidate(); setLoadingSettings(false); setSaving(action); setError(""); setNotice("");
    try {
      if (action === "generate") {
        const result = await createProbeAccessToken();
        if (!settingsScope.isCurrent(current)) return;
        setForm(formSettings(result.settings)); callbacks.current.onAccessToken(result.token, result.settings); setNotice("已生成 Worker 令牌。");
      } else if (action === "clear") {
        const result = await clearProbeAccessToken();
        if (!settingsScope.isCurrent(current)) return;
        setForm(formSettings(result.settings)); callbacks.current.onAccessToken("", result.settings); setNotice("已清除 Worker 令牌。");
      } else {
        const settings: ProbeSettingsUpdate = {
          enabled: form.enabled, require_access_token: form.has_access_token && form.require_access_token,
          title: form.title, description: form.description, logo: form.logo, refresh_interval_sec: form.refresh_interval_sec,
          show_globe: form.show_globe, show_resource_heatmap: form.show_resource_heatmap, show_traffic_quota: form.show_traffic_quota,
          show_daily_trend: form.show_daily_trend, show_traffic_hotspots: form.show_traffic_hotspots, show_traffic_7d: form.show_traffic_7d,
          show_return_route: form.show_return_route, show_renewal_timeline: form.show_renewal_timeline, show_health_score: form.show_health_score,
          appearance: { theme: form.theme, color_mode: form.color_mode, revision: form.revision },
        };
        const result = await updatePublicProbeSettings(settings);
        if (!settingsScope.isCurrent(current)) return;
        setForm(formSettings(result.settings)); callbacks.current.onSettings(result.settings); setNotice("探针设置已保存。"); callbacks.current.onRefresh();
      }
    } catch (cause) {
      if (settingsScope.isCurrent(current)) setError(zhMessage(cause, "探针设置操作失败，请稍后重试。"));
    } finally { if (settingsScope.isCurrent(current)) { setSaving(""); settingsBusy.current = false; } }
  }
  async function runTask(action: "create" | "dispatch" | ProbeTask) {
    if (!tasksLoaded || tasksLoading || taskBusy.current) return;
    const domains = [...new Set(taskForm.domains.split(/[\s,;]+/).map(item => item.trim()).filter(Boolean))];
    const targets = selectedRouteTargets(taskForm.targets);
    if (action === "create") {
      if (!taskForm.server_id) { setTaskError("请先选择服务器，再创建探针任务。"); return; }
      if (!validInteger(taskForm.interval_sec, 60, 86400)) { setTaskError("执行间隔必须为 60 至 86400 秒的整数。"); return; }
      if (taskForm.kind === "domain_latency" && !domains.length) { setTaskError("请至少填写一个域名目标。"); return; }
      if (taskForm.kind === "domain_latency" && !validInteger(taskForm.domain_timeout_ms, 200, 10000)) { setTaskError("超时时间必须为 200 至 10000 毫秒的整数。"); return; }
      if (taskForm.kind === "return_route" && !targets.length) { setTaskError("请至少填写一个回程路由目标。"); return; }
      if (taskForm.kind === "return_route" && targets.some(target => !validInteger(target.port, 1, 65535))) { setTaskError("所有已选回程路由目标的端口都必须为 1 至 65535 的整数。"); return; }
      if (taskForm.kind === "return_route" && !validInteger(taskForm.return_route_timeout_seconds, 10, 45)) { setTaskError("回程探测超时时间必须为 10 至 45 秒的整数。"); return; }
    }
    const current = taskOperation.begin(); taskBusy.current = true; setTaskSaving(typeof action === "string" ? action : action.id); setTaskError(""); setTaskNotice(""); tasksScope.invalidate();
    try {
      if (action === "create") {
        await createProbeTask({ server_id: taskForm.server_id, kind: taskForm.kind, interval_sec: taskForm.interval_sec,
          domains: taskForm.kind === "domain_latency" ? domains : [], domain_timeout_ms: taskForm.kind === "domain_latency" ? taskForm.domain_timeout_ms : 2000, allow_icmp: taskForm.kind === "domain_latency" && taskForm.allow_icmp,
          return_route_targets: taskForm.kind === "return_route" ? targets : [], return_route_timeout_seconds: taskForm.kind === "return_route" ? taskForm.return_route_timeout_seconds : 25, ip_version: taskForm.kind === "return_route" ? taskForm.ip_version : 4,
          command_timeout_ms: taskForm.kind === "return_route" ? targets.length * taskForm.return_route_timeout_seconds * 1000 + 5000 : taskForm.kind === "domain_latency" ? latencyCommandTimeout(domains.length, taskForm.domain_timeout_ms, taskForm.allow_icmp) : 30000 });
        if (taskOperation.isCurrent(current)) { setTaskForm(previous => ({ ...previous, domains: domains.join(", ") })); setTaskNotice("探针任务已创建。"); }
      } else if (action === "dispatch") {
        const result = await dispatchDueProbeTasks();
        if (taskOperation.isCurrent(current)) { setTaskNotice(`已下发 ${result.dispatched.length} 个到期探针任务。`); callbacks.current.onRefresh(); }
      } else await updateProbeTask(action.id, { enabled: !action.enabled });
      if (taskOperation.isCurrent(current)) await loadTasks();
    } catch (cause) {
      if (taskOperation.isCurrent(current)) setTaskError(zhMessage(cause, "探针任务操作失败，请稍后重试。"));
    } finally { if (taskOperation.isCurrent(current)) { setTaskSaving(""); setTasksLoading(false); taskBusy.current = false; } }
  }
  async function copyToken() {
    const current = settingsScope.capture();
    try { await navigator.clipboard.writeText(accessToken); if (settingsScope.isCurrent(current)) setNotice("Worker 令牌已复制，请将其存入 Worker 的密钥存储。"); }
    catch { if (settingsScope.isCurrent(current)) setError("无法访问剪贴板，请手动复制。"); }
  }
  return <>
    <Card title="探针设置" extra={<Space wrap><Tag color={!settingsLoaded ? "default" : form.enabled ? "success" : "error"}>{!settingsLoaded ? "不可用" : form.enabled ? "已启用" : "已停用"}</Tag><Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新探针设置" disabled={Boolean(saving)} loading={loadingSettings} onClick={() => void loadSettings()} /></Space>}>
      {error && <Alert className="form-alert" type="error" showIcon title={error} />}
      {notice && <Alert className="form-alert" type="success" showIcon title={notice} />}
      <Form layout="vertical" disabled={settingsBlocked} onFinish={() => void settingsAction("save")}>
        <Row gutter={16}>
          <Col xs={24} md={16}><Form.Item label="标题" htmlFor="probe-title"><Input id="probe-title" value={form.title} onChange={event => setForm(previous => ({ ...previous, title: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="刷新间隔（秒）" htmlFor="probe-refresh-seconds"><StrictInputNumber id="probe-refresh-seconds" value={form.refresh_interval_sec} aria-valuemin={1} aria-valuemax={60} onChange={value => setForm(previous => ({ ...previous, refresh_interval_sec: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col>
          <Col span={24}><Form.Item label="说明" htmlFor="probe-description"><Input.TextArea id="probe-description" value={form.description} autoSize={{ minRows: 2, maxRows: 6 }} onChange={event => setForm(previous => ({ ...previous, description: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="标志图片地址" htmlFor="probe-logo"><Input id="probe-logo" value={form.logo} onChange={event => setForm(previous => ({ ...previous, logo: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="主题" htmlFor="probe-theme"><Input id="probe-theme" value={form.theme} onChange={event => setForm(previous => ({ ...previous, theme: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="颜色模式" htmlFor="probe-color-mode"><Select id="probe-color-mode" value={form.color_mode} options={[{ label: "浅色", value: "light" }, { label: "深色", value: "dark" }, { label: "跟随系统", value: "system" }]} onChange={value => setForm(previous => ({ ...previous, color_mode: value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="修订标识" htmlFor="probe-revision"><Input id="probe-revision" value={form.revision} onChange={event => setForm(previous => ({ ...previous, revision: event.target.value }))} /></Form.Item></Col>
          {toggles.map(([key, label]) => <Col xs={12} sm={8} lg={6} key={key}><Form.Item label={label} htmlFor={`probe-${key}`}><Switch id={`probe-${key}`} checked={Boolean(form[key])} disabled={settingsBlocked || (key === "require_access_token" && !form.has_access_token)} onChange={checked => setForm(previous => ({ ...previous, [key]: checked }))} /></Form.Item></Col>)}
        </Row>
        <Card size="small" title="Worker 访问" className="form-alert" extra={<Tag color={form.require_access_token ? "success" : form.has_access_token ? "blue" : "default"}>{form.require_access_token ? "必须使用令牌" : form.has_access_token ? "已就绪" : "无"}</Tag>}>
          <Space orientation="vertical" style={{ width: "100%" }}>
            <Space wrap><Popconfirm title="要生成新的 Worker 令牌吗？" description="现有 Worker 令牌将立即失效。" disabled={settingsBlocked || !form.has_access_token} onConfirm={() => void settingsAction("generate")}><Button aria-label="生成" disabled={settingsBlocked} loading={saving === "generate"} onClick={() => { if (!form.has_access_token) void settingsAction("generate"); }}>生成</Button></Popconfirm><Popconfirm title="要清除 Worker 令牌吗？" description="令牌将被撤销，同时更新公开访问策略。" disabled={settingsBlocked || !form.has_access_token} onConfirm={() => void settingsAction("clear")}><Button danger aria-label="清除" disabled={settingsBlocked || !form.has_access_token} loading={saving === "clear"}>清除</Button></Popconfirm></Space>
            {accessToken && <><Alert type="warning" showIcon title="令牌仅在当前页面显示。请将其存为 Worker 密钥，切勿写入前端代码或地址链接。" /><Form.Item label="新令牌" htmlFor="probe-new-token" style={{ marginBottom: 0 }}><Space.Compact style={{ width: "100%" }}><Input id="probe-new-token" value={accessToken} readOnly /><Button icon={<CopyOutlined aria-hidden />} aria-label="复制 Worker 令牌" onClick={() => void copyToken()} /></Space.Compact></Form.Item></>}
          </Space>
        </Card>
        <Space wrap><Button type="primary" htmlType="submit" aria-label="保存设置" icon={<SaveOutlined aria-hidden />} loading={saving === "save"}>保存设置</Button><Button onClick={() => callbacks.current.onRefresh()}>刷新</Button></Space>
      </Form>
    </Card>
    <Card styles={{ title: { whiteSpace: "normal" } }} title={<Flex wrap align="center" justify="space-between" gap={8} style={{ paddingBlock: 12 }}><span>定时探针</span><Space wrap style={{ maxWidth: "100%" }}><Typography.Text type="secondary">{tasks.length} 个任务，{dueCount} 个已到执行时间</Typography.Text><Button icon={<ReloadOutlined aria-hidden />} aria-label="刷新探针任务" loading={tasksLoading} disabled={Boolean(taskSaving)} onClick={() => { if (!taskBusy.current) void loadTasks(); }} /><Button icon={<PlayCircleOutlined aria-hidden />} aria-label="下发到期任务" loading={taskSaving === "dispatch"} disabled={tasksBlocked} onClick={() => void runTask("dispatch")}>下发到期任务</Button></Space></Flex>}>
      {taskError && <Alert className="form-alert" type="error" showIcon title={taskError} />}{taskNotice && <Alert className="form-alert" type="success" showIcon title={taskNotice} />}
      <Form layout="vertical" disabled={tasksBlocked} onFinish={() => void runTask("create")}>
        <Row gutter={16}>
          <Col xs={24} md={8}><Form.Item label="探针类型" htmlFor="probe-task-type"><Select id="probe-task-type" value={taskForm.kind} options={[{ label: "域名延迟", value: "domain_latency" }, { label: "回程路由", value: "return_route" }, { label: "系统", value: "system" }]} onChange={value => setTaskForm(previous => ({ ...previous, kind: value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="服务器" htmlFor="probe-task-server"><Select id="probe-task-server" value={taskForm.server_id || undefined} options={servers.map(server => ({ label: server.name, value: server.id }))} onChange={value => setTaskForm(previous => ({ ...previous, server_id: value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="执行间隔（秒）" htmlFor="probe-task-interval"><StrictInputNumber id="probe-task-interval" value={taskForm.interval_sec} aria-valuemin={60} aria-valuemax={86400} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, interval_sec: value ?? Number.NaN }))} /></Form.Item></Col>
          {taskForm.kind === "domain_latency" && <>
            <Col xs={24} md={12}><Form.Item label="目标域名" htmlFor="probe-task-domains"><Input id="probe-task-domains" value={taskForm.domains} onChange={event => setTaskForm(previous => ({ ...previous, domains: event.target.value }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item label="超时时间（毫秒）" htmlFor="probe-task-timeout"><StrictInputNumber id="probe-task-timeout" value={taskForm.domain_timeout_ms} aria-valuemin={200} aria-valuemax={10000} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, domain_timeout_ms: value ?? Number.NaN }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item label="ICMP 回退探测" htmlFor="probe-task-icmp"><Switch id="probe-task-icmp" checked={taskForm.allow_icmp} onChange={value => setTaskForm(previous => ({ ...previous, allow_icmp: value }))} /></Form.Item></Col>
          </>}
          {taskForm.kind === "return_route" && <>
            <Col span={24}><RouteProbeFields value={taskForm.targets} onChange={value => setTaskForm(previous => ({ ...previous, targets: value }))} /></Col>
            <Col xs={24} sm={12}><Form.Item label="IP 版本" htmlFor="probe-task-ip-version"><Select id="probe-task-ip-version" value={taskForm.ip_version} options={[{ label: "IPv4", value: 4 }, { label: "IPv6", value: 6 }]} onChange={value => setTaskForm(previous => ({ ...previous, ip_version: value }))} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="回程探测超时（秒）" htmlFor="probe-task-route-timeout"><StrictInputNumber id="probe-task-route-timeout" value={taskForm.return_route_timeout_seconds} aria-valuemin={10} aria-valuemax={45} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, return_route_timeout_seconds: value ?? Number.NaN }))} /></Form.Item></Col>
          </>}
        </Row>
        <Button type="primary" htmlType="submit" aria-label="添加任务" icon={<PlusOutlined aria-hidden />} disabled={tasksBlocked || !servers.length} loading={taskSaving === "create"}>添加任务</Button>
      </Form>
      <Table style={{ marginTop: 24 }} rowKey="id" dataSource={tasks} loading={tasksLoading} pagination={false} scroll={{ x: 620 }} locale={{ emptyText: "暂无定时探针任务。" }} columns={[
        { title: "已启用", width: 100, render: (_, task) => <Switch aria-label={`启用探针任务 ${task.id}`} checked={task.enabled} disabled={tasksBlocked} loading={taskSaving === task.id} onChange={() => void runTask(task)} /> },
        { title: "服务器 / 目标", render: (_, task) => <><Typography.Text strong>{servers.find(server => server.id === task.server_id)?.name ?? task.server_id.slice(0, 8)}</Typography.Text><div>{task.kind === "domain_latency" ? task.domains.join(", ") : task.return_route_targets.map(target => target.host).join(", ") || "系统信息"}</div></> },
        { title: "类型", dataIndex: "kind", render: kind => <Tag>{taskLabels[kind as ProbeTaskKind] ?? zhStatus(kind)}</Tag> },
        { title: "下次执行", dataIndex: "next_run_at", render: value => new Date(value).toLocaleString("zh-CN") },
      ]} />
    </Card>
  </>;
}
