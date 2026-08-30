import { CopyOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Flex, Form, Input, Popconfirm, Row, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { latencyCommandTimeout, routeTargets, selectedRouteTargets } from "../../domain/diagnostics";
import type { ServerSummary } from "../../domain/inventory";
import type { ProbeSettings, ProbeSettingsUpdate, ProbeTask, ProbeTaskKind } from "../../domain/probe";
import { listServers } from "../../services/inventory";
import { clearProbeAccessToken, createProbeAccessToken, createProbeTask, dispatchDueProbeTasks, getPublicProbeSettings, listProbeTasks, updateProbeTask, updatePublicProbeSettings } from "../../services/probe";
import { useAsyncScope } from "../hooks/useAsyncScope";
import RouteProbeFields from "./RouteProbeFields";
import StrictInputNumber from "./StrictInputNumber";

export interface ProbeAdministrationPanelProps {
  accessToken: string;
  onSettings: (settings: ProbeSettings) => void;
  onAccessToken: (token: string, settings: ProbeSettings) => void;
  onRefresh: () => void;
}
const defaults = {
  enabled: true, has_access_token: false, require_access_token: false, title: "Open Node Probe",
  description: "MMWX probe-compatible node status without license gates.", logo: "", refresh_interval_sec: 5,
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
const toggles = [
  ["enabled", "Enabled"], ["require_access_token", "Worker token"], ["show_globe", "Regions"],
  ["show_resource_heatmap", "System"], ["show_traffic_quota", "Traffic"], ["show_health_score", "Health"],
  ["show_daily_trend", "Daily"], ["show_traffic_hotspots", "Hotspots"], ["show_traffic_7d", "7d traffic"],
  ["show_return_route", "Return routes"], ["show_renewal_timeline", "Renewal"],
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
      if (loadScope.isCurrent(current)) setError(cause instanceof Error ? cause.message : "Probe settings unavailable.");
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
      if (tasksScope.isCurrent(current)) setTaskError(cause instanceof Error ? `Probe tasks failed: ${cause.message}` : "Probe tasks failed.");
    } finally { if (tasksScope.isCurrent(current)) setTasksLoading(false); }
  }, [tasksScope]);
  useEffect(() => { void loadSettings(); void loadTasks(); }, [loadSettings, loadTasks]);
  async function settingsAction(action: "save" | "generate" | "clear") {
    if (!settingsLoaded || loadingSettings || settingsBusy.current) return;
    if (action === "save" && !validInteger(form.refresh_interval_sec, 1, 60)) {
      setError("Refresh seconds must be a whole number from 1 to 60."); setNotice(""); return;
    }
    settingsBusy.current = true; const current = settingsScope.begin(); loadScope.invalidate(); setLoadingSettings(false); setSaving(action); setError(""); setNotice("");
    try {
      if (action === "generate") {
        const result = await createProbeAccessToken();
        if (!settingsScope.isCurrent(current)) return;
        setForm(formSettings(result.settings)); callbacks.current.onAccessToken(result.token, result.settings); setNotice("Worker token generated.");
      } else if (action === "clear") {
        const result = await clearProbeAccessToken();
        if (!settingsScope.isCurrent(current)) return;
        setForm(formSettings(result.settings)); callbacks.current.onAccessToken("", result.settings); setNotice("Worker token cleared.");
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
        setForm(formSettings(result.settings)); callbacks.current.onSettings(result.settings); setNotice("Probe settings saved."); callbacks.current.onRefresh();
      }
    } catch (cause) {
      if (settingsScope.isCurrent(current)) setError(cause instanceof Error ? cause.message : "Probe settings failed.");
    } finally { if (settingsScope.isCurrent(current)) { setSaving(""); settingsBusy.current = false; } }
  }
  async function runTask(action: "create" | "dispatch" | ProbeTask) {
    if (!tasksLoaded || tasksLoading || taskBusy.current) return;
    const domains = [...new Set(taskForm.domains.split(/[\s,;]+/).map(item => item.trim()).filter(Boolean))];
    const targets = selectedRouteTargets(taskForm.targets);
    if (action === "create") {
      if (!taskForm.server_id) { setTaskError("Select a server before creating a probe task."); return; }
      if (!validInteger(taskForm.interval_sec, 60, 86400)) { setTaskError("Interval seconds must be a whole number from 60 to 86400."); return; }
      if (taskForm.kind === "domain_latency" && !domains.length) { setTaskError("Add at least one domain target."); return; }
      if (taskForm.kind === "domain_latency" && !validInteger(taskForm.domain_timeout_ms, 200, 10000)) { setTaskError("Timeout ms must be a whole number from 200 to 10000."); return; }
      if (taskForm.kind === "return_route" && !targets.length) { setTaskError("Add at least one return-route target."); return; }
      if (taskForm.kind === "return_route" && targets.some(target => !validInteger(target.port, 1, 65535))) { setTaskError("Every selected return-route port must be a whole number from 1 to 65535."); return; }
      if (taskForm.kind === "return_route" && !validInteger(taskForm.return_route_timeout_seconds, 10, 45)) { setTaskError("Route timeout seconds must be a whole number from 10 to 45."); return; }
    }
    const current = taskOperation.begin(); taskBusy.current = true; setTaskSaving(typeof action === "string" ? action : action.id); setTaskError(""); setTaskNotice(""); tasksScope.invalidate();
    try {
      if (action === "create") {
        await createProbeTask({ server_id: taskForm.server_id, kind: taskForm.kind, interval_sec: taskForm.interval_sec,
          domains: taskForm.kind === "domain_latency" ? domains : [], domain_timeout_ms: taskForm.kind === "domain_latency" ? taskForm.domain_timeout_ms : 2000, allow_icmp: taskForm.kind === "domain_latency" && taskForm.allow_icmp,
          return_route_targets: taskForm.kind === "return_route" ? targets : [], return_route_timeout_seconds: taskForm.kind === "return_route" ? taskForm.return_route_timeout_seconds : 25, ip_version: taskForm.kind === "return_route" ? taskForm.ip_version : 4,
          command_timeout_ms: taskForm.kind === "return_route" ? targets.length * taskForm.return_route_timeout_seconds * 1000 + 5000 : taskForm.kind === "domain_latency" ? latencyCommandTimeout(domains.length, taskForm.domain_timeout_ms, taskForm.allow_icmp) : 30000 });
        if (taskOperation.isCurrent(current)) { setTaskForm(previous => ({ ...previous, domains: domains.join(", ") })); setTaskNotice("Probe task created."); }
      } else if (action === "dispatch") {
        const result = await dispatchDueProbeTasks();
        if (taskOperation.isCurrent(current)) { setTaskNotice(`${result.dispatched.length} due probe task(s) dispatched.`); callbacks.current.onRefresh(); }
      } else await updateProbeTask(action.id, { enabled: !action.enabled });
      if (taskOperation.isCurrent(current)) await loadTasks();
    } catch (cause) {
      if (taskOperation.isCurrent(current)) setTaskError(cause instanceof Error ? `Probe task failed: ${cause.message}` : "Probe task failed.");
    } finally { if (taskOperation.isCurrent(current)) { setTaskSaving(""); setTasksLoading(false); taskBusy.current = false; } }
  }
  async function copyToken() {
    const current = settingsScope.capture();
    try { await navigator.clipboard.writeText(accessToken); if (settingsScope.isCurrent(current)) setNotice("Worker token copied. Keep it in the Worker's secret storage."); }
    catch { if (settingsScope.isCurrent(current)) setError("Clipboard unavailable"); }
  }
  return <>
    <Card title="Probe settings" extra={<Space wrap><Tag color={!settingsLoaded ? "default" : form.enabled ? "success" : "error"}>{!settingsLoaded ? "Unavailable" : form.enabled ? "Enabled" : "Disabled"}</Tag><Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh probe settings" disabled={Boolean(saving)} loading={loadingSettings} onClick={() => void loadSettings()} /></Space>}>
      {error && <Alert className="form-alert" type="error" showIcon title={error} />}
      {notice && <Alert className="form-alert" type="success" showIcon title={notice} />}
      <Form layout="vertical" disabled={settingsBlocked} onFinish={() => void settingsAction("save")}>
        <Row gutter={16}>
          <Col xs={24} md={16}><Form.Item label="Title" htmlFor="probe-title"><Input id="probe-title" value={form.title} onChange={event => setForm(previous => ({ ...previous, title: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="Refresh seconds" htmlFor="probe-refresh-seconds"><StrictInputNumber id="probe-refresh-seconds" value={form.refresh_interval_sec} aria-valuemin={1} aria-valuemax={60} onChange={value => setForm(previous => ({ ...previous, refresh_interval_sec: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col>
          <Col span={24}><Form.Item label="Description" htmlFor="probe-description"><Input.TextArea id="probe-description" value={form.description} autoSize={{ minRows: 2, maxRows: 6 }} onChange={event => setForm(previous => ({ ...previous, description: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="Logo URL" htmlFor="probe-logo"><Input id="probe-logo" value={form.logo} onChange={event => setForm(previous => ({ ...previous, logo: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="Theme" htmlFor="probe-theme"><Input id="probe-theme" value={form.theme} onChange={event => setForm(previous => ({ ...previous, theme: event.target.value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="Color mode" htmlFor="probe-color-mode"><Select id="probe-color-mode" value={form.color_mode} options={[{ label: "Light", value: "light" }, { label: "Dark", value: "dark" }, { label: "System", value: "system" }]} onChange={value => setForm(previous => ({ ...previous, color_mode: value }))} /></Form.Item></Col>
          <Col xs={24} md={12}><Form.Item label="Revision" htmlFor="probe-revision"><Input id="probe-revision" value={form.revision} onChange={event => setForm(previous => ({ ...previous, revision: event.target.value }))} /></Form.Item></Col>
          {toggles.map(([key, label]) => <Col xs={12} sm={8} lg={6} key={key}><Form.Item label={label} htmlFor={`probe-${key}`}><Switch id={`probe-${key}`} checked={Boolean(form[key])} disabled={settingsBlocked || (key === "require_access_token" && !form.has_access_token)} onChange={checked => setForm(previous => ({ ...previous, [key]: checked }))} /></Form.Item></Col>)}
        </Row>
        <Card size="small" title="Worker access" className="form-alert" extra={<Tag color={form.require_access_token ? "success" : form.has_access_token ? "blue" : "default"}>{form.require_access_token ? "Required" : form.has_access_token ? "Ready" : "None"}</Tag>}>
          <Space orientation="vertical" style={{ width: "100%" }}>
            <Space wrap><Popconfirm title="Generate a new Worker token?" description="An existing Worker token will stop working." disabled={settingsBlocked || !form.has_access_token} onConfirm={() => void settingsAction("generate")}><Button aria-label="Generate" disabled={settingsBlocked} loading={saving === "generate"} onClick={() => { if (!form.has_access_token) void settingsAction("generate"); }}>Generate</Button></Popconfirm><Popconfirm title="Clear Worker token?" description="The token will be revoked and the public access policy will be updated." disabled={settingsBlocked || !form.has_access_token} onConfirm={() => void settingsAction("clear")}><Button danger aria-label="Clear" disabled={settingsBlocked || !form.has_access_token} loading={saving === "clear"}>Clear</Button></Popconfirm></Space>
            {accessToken && <><Alert type="warning" showIcon title="Shown only in this page. Store the token as a Worker secret; never add it to frontend code or a URL." /><Form.Item label="New token" htmlFor="probe-new-token" style={{ marginBottom: 0 }}><Space.Compact style={{ width: "100%" }}><Input id="probe-new-token" value={accessToken} readOnly /><Button icon={<CopyOutlined aria-hidden />} aria-label="Copy Worker token" onClick={() => void copyToken()} /></Space.Compact></Form.Item></>}
          </Space>
        </Card>
        <Space wrap><Button type="primary" htmlType="submit" aria-label="Save settings" icon={<SaveOutlined aria-hidden />} loading={saving === "save"}>Save settings</Button><Button onClick={() => callbacks.current.onRefresh()}>Refresh</Button></Space>
      </Form>
    </Card>
    <Card styles={{ title: { whiteSpace: "normal" } }} title={<Flex wrap align="center" justify="space-between" gap={8} style={{ paddingBlock: 12 }}><span>Scheduled probes</span><Space wrap style={{ maxWidth: "100%" }}><Typography.Text type="secondary">{tasks.length} task(s), {dueCount} due</Typography.Text><Button icon={<ReloadOutlined aria-hidden />} aria-label="Refresh probe tasks" loading={tasksLoading} disabled={Boolean(taskSaving)} onClick={() => { if (!taskBusy.current) void loadTasks(); }} /><Button icon={<PlayCircleOutlined aria-hidden />} aria-label="Dispatch due" loading={taskSaving === "dispatch"} disabled={tasksBlocked} onClick={() => void runTask("dispatch")}>Dispatch due</Button></Space></Flex>}>
      {taskError && <Alert className="form-alert" type="error" showIcon title={taskError} />}{taskNotice && <Alert className="form-alert" type="success" showIcon title={taskNotice} />}
      <Form layout="vertical" disabled={tasksBlocked} onFinish={() => void runTask("create")}>
        <Row gutter={16}>
          <Col xs={24} md={8}><Form.Item label="Probe type" htmlFor="probe-task-type"><Select id="probe-task-type" value={taskForm.kind} options={[{ label: "Domain latency", value: "domain_latency" }, { label: "Return route", value: "return_route" }, { label: "System", value: "system" }]} onChange={value => setTaskForm(previous => ({ ...previous, kind: value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="Server" htmlFor="probe-task-server"><Select id="probe-task-server" value={taskForm.server_id || undefined} options={servers.map(server => ({ label: server.name, value: server.id }))} onChange={value => setTaskForm(previous => ({ ...previous, server_id: value }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item label="Interval seconds" htmlFor="probe-task-interval"><StrictInputNumber id="probe-task-interval" value={taskForm.interval_sec} aria-valuemin={60} aria-valuemax={86400} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, interval_sec: value ?? Number.NaN }))} /></Form.Item></Col>
          {taskForm.kind === "domain_latency" && <>
            <Col xs={24} md={12}><Form.Item label="Domains" htmlFor="probe-task-domains"><Input id="probe-task-domains" value={taskForm.domains} onChange={event => setTaskForm(previous => ({ ...previous, domains: event.target.value }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item label="Timeout ms" htmlFor="probe-task-timeout"><StrictInputNumber id="probe-task-timeout" value={taskForm.domain_timeout_ms} aria-valuemin={200} aria-valuemax={10000} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, domain_timeout_ms: value ?? Number.NaN }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item label="ICMP fallback" htmlFor="probe-task-icmp"><Switch id="probe-task-icmp" checked={taskForm.allow_icmp} onChange={value => setTaskForm(previous => ({ ...previous, allow_icmp: value }))} /></Form.Item></Col>
          </>}
          {taskForm.kind === "return_route" && <>
            <Col span={24}><RouteProbeFields value={taskForm.targets} onChange={value => setTaskForm(previous => ({ ...previous, targets: value }))} /></Col>
            <Col xs={24} sm={12}><Form.Item label="IP version" htmlFor="probe-task-ip-version"><Select id="probe-task-ip-version" value={taskForm.ip_version} options={[{ label: "IPv4", value: 4 }, { label: "IPv6", value: 6 }]} onChange={value => setTaskForm(previous => ({ ...previous, ip_version: value }))} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Route timeout seconds" htmlFor="probe-task-route-timeout"><StrictInputNumber id="probe-task-route-timeout" value={taskForm.return_route_timeout_seconds} aria-valuemin={10} aria-valuemax={45} style={{ width: "100%" }} onChange={value => setTaskForm(previous => ({ ...previous, return_route_timeout_seconds: value ?? Number.NaN }))} /></Form.Item></Col>
          </>}
        </Row>
        <Button type="primary" htmlType="submit" aria-label="Add task" icon={<PlusOutlined aria-hidden />} disabled={tasksBlocked || !servers.length} loading={taskSaving === "create"}>Add task</Button>
      </Form>
      <Table style={{ marginTop: 24 }} rowKey="id" dataSource={tasks} loading={tasksLoading} pagination={false} scroll={{ x: 620 }} locale={{ emptyText: "No scheduled probe tasks." }} columns={[
        { title: "Enabled", width: 100, render: (_, task) => <Switch aria-label={`Enable probe task ${task.id}`} checked={task.enabled} disabled={tasksBlocked} loading={taskSaving === task.id} onChange={() => void runTask(task)} /> },
        { title: "Server / targets", render: (_, task) => <><Typography.Text strong>{servers.find(server => server.id === task.server_id)?.name ?? task.server_id.slice(0, 8)}</Typography.Text><div>{task.kind === "domain_latency" ? task.domains.join(", ") : task.return_route_targets.map(target => target.host).join(", ") || "system info"}</div></> },
        { title: "Type", dataIndex: "kind", render: kind => <Tag>{kind.replaceAll("_", " ")}</Tag> },
        { title: "Next run", dataIndex: "next_run_at", render: value => new Date(value).toLocaleString() },
      ]} />
    </Card>
  </>;
}
