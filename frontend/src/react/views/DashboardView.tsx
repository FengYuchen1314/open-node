import { useLayoutEffect, useRef, useState } from "react";
import { Alert, AutoComplete, Button, Card, Checkbox, Col, Divider, Empty, Form, Input, Modal,
  Radio, Row, Select, Space, Statistic, Switch, Table, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { defaultServerCreateRequest, type AgentCommand, type AgentCommandStreamFrame, type AgentRead,
  type AgentOperationKind, type AgentOperationPayload, type AgentScanResult, type AgentTelemetry,
  type ConnectionMode, type RenewalCycle, type ServerCreateRequest, type ServerKind, type ServerProbeMetadataUpdate,
  type ServerSummary, type XrayMode } from "../../domain/inventory";
import { diagnosticPaths, latencyCommandTimeout, routeTargets, selectedRouteTargets } from "../../domain/diagnostics";
import { createServer, createServerCommand, getLatestScanResult, getLatestTelemetry, listAgents,
  listCommandStreamFrames, listServerCommands, listServers, queueAgentOperation, updateServerProbeMetadata } from "../../services/inventory";
import AgentBootstrapDialog from "../components/AgentBootstrapDialog";
import AgentLifecycleDialog, { type AgentLifecycleAction } from "../components/AgentLifecycleDialog";
import CommandInspector from "../components/CommandInspector";
import RouteProbeFields from "../components/RouteProbeFields";
import ServerManagementDialog from "../components/ServerManagementDialog";
import ServerTrafficPanel from "../components/ServerTrafficPanel";
import StrictInputNumber from "../components/StrictInputNumber";
import { useBranding } from "../hooks/useBranding";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

export interface DashboardViewProps { }
type Operation = { title: string; kind: AgentOperationKind; workspace?: boolean };
const quickOperations: Operation[] = [{ title: "系统信息", kind: "system_info" }, { title: "流量", kind: "traffic" }, { title: "速率", kind: "speed" }];
const diagnosticOperations: Operation[] = [{ title: "服务", kind: "services_status" }, { title: "网卡", kind: "system_nics" },
  { title: "扫描", kind: "scan" }, { title: "日志文件", kind: "log_files_list" }];
const maintenanceOperations: Operation[] = [
  { title: "安装 Xray", kind: "xray_install" }, { title: "移除 Xray", kind: "xray_remove" },
  { title: "Xray 发布版本", kind: "xray_release" }, { title: "回退 Xray", kind: "xray_rollback" },
  { title: "安装 Nginx", kind: "nginx_install" }, { title: "移除 Nginx", kind: "nginx_remove" },
  { title: "升级 Agent", kind: "agent_upgrade" },
  { title: "回退 Agent", kind: "agent_rollback" }, { title: "卸载 Agent", kind: "agent_uninstall" },
];
const configOperations: Operation[] = [{ title: "Xray 配置", kind: "xray_config_read" },
  { title: "Xray 系统配置", kind: "xray_system_config_read", workspace: true },
  { title: "Xray 文件", kind: "xray_config_files_list", workspace: true },
  { title: "Nginx 配置", kind: "nginx_config_read" }, { title: "Nginx 文件", kind: "nginx_config_files_list" }];
const connectionOptions = ["auto", "websocket", "http", "pull"].map(value => ({ value,
  label: { auto: "自动", websocket: "WebSocket", http: "HTTP", pull: "拉取" }[value] }));
const xrayOptions = [{ value: "external", label: "外部" }, { value: "embedded", label: "嵌入式" }];
const serverKindNames: Record<ServerKind, string> = { direct: "公网直连", "leased-line": "专线", residential: "家宽落地" };
const serverKindOptions = (Object.entries(serverKindNames) as Array<[ServerKind, string]>).map(([value, label]) => ({ value, label }));
const cycleNames: Record<RenewalCycle, string> = { month: "月", quarter: "季度", half_year: "半年", year: "年" };
const cycleOptions = Object.entries(cycleNames).map(([value, label]) => ({ value, label }));
const lifecyclePaths = /^\/api\/child\/agent\/(upgrade(?:-stream)?|uninstall(?:-stream)?|rollback)$/;
const textOrNull = (value: string | null | undefined) => value?.trim() || null;
const numberOrNull = (value: number | null | undefined) => value ?? null;
const dateOrNull = (value: string | null | undefined) => value?.trim() ? `${value.trim()}T00:00:00Z` : null;
const integerInRange = (value: number | null | undefined, min: number, max: number): value is number =>
  value != null && Number.isSafeInteger(value) && value >= min && value <= max;
const validPrice = (value: number | null | undefined) => value == null || (Number.isFinite(value) && value >= 0);
function metadataFor(server?: ServerSummary): ServerProbeMetadataUpdate {
  return { region: server?.region ?? "", region_country: server?.region_country ?? "", region_name: server?.region_name ?? "",
    region_city: server?.region_city ?? "", provider_name: server?.provider_name ?? "", provider_url: server?.provider_url ?? "",
    expires_at: server?.expires_at?.slice(0, 10) ?? "", renewal_price: server?.renewal_price ?? null,
    renewal_price_cny: server?.renewal_price_cny ?? null, renewal_cycle: server?.renewal_cycle ?? null,
    renewal_currency: server?.renewal_currency ?? "", telecom_paid_peer: server?.telecom_paid_peer ?? null };
}
function metadataPayload(value: ServerProbeMetadataUpdate): ServerProbeMetadataUpdate {
  return { region: textOrNull(value.region), region_country: textOrNull(value.region_country), region_name: textOrNull(value.region_name),
    region_city: textOrNull(value.region_city), provider_name: textOrNull(value.provider_name), provider_url: textOrNull(value.provider_url),
    expires_at: dateOrNull(value.expires_at), renewal_price: numberOrNull(value.renewal_price), renewal_price_cny: numberOrNull(value.renewal_price_cny),
    renewal_cycle: value.renewal_cycle || null, renewal_currency: textOrNull(value.renewal_currency), telecom_paid_peer: value.telecom_paid_peer ?? null };
}
function speed(value: number) {
  if (value < 1024) return `${value} B/s`;
  let amount = value / 1024; let index = 0;
  const units = ["KB/s", "MB/s", "GB/s", "TB/s"];
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[index]}`;
}
function telemetryLabel(item?: AgentTelemetry | null) {
  const parts: string[] = []; const metrics = item?.sysmetrics;
  if (metrics?.has_cpu) parts.push(`${metrics.cpu_pct.toFixed(1)}% CPU`);
  if (metrics?.has_mem && metrics.mem_total > 0) parts.push(`${(metrics.mem_used / metrics.mem_total * 100).toFixed(0)}% 内存`);
  return parts.join(" · ") || "暂无遥测数据";
}
function latencyLabel(item?: AgentTelemetry | null) {
  if (!item?.latency.length) return "暂无探测";
  const successful = item.latency.filter(sample => sample.success);
  return successful.length ? `${(successful.reduce((sum, sample) => sum + sample.latency_ms, 0) / successful.length).toFixed(0)} ms` : "探测失败";
}
function renewalLabel(server: ServerSummary) {
  return [server.expires_at ? new Date(server.expires_at).toLocaleDateString("zh-CN", { timeZone: "UTC" }) : "", server.renewal_price == null ? "" : `${server.renewal_price} ${server.renewal_currency ?? ""}`.trim(),
    server.renewal_cycle ? cycleNames[server.renewal_cycle] : ""].filter(Boolean).join(" · ") || "暂无续费信息";
}

export default function DashboardView(_props: DashboardViewProps) {
  const { branding } = useBranding();
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [agents, setAgents] = useState<Record<string, AgentRead>>({});
  const [telemetry, setTelemetry] = useState<Record<string, AgentTelemetry | null>>({});
  const [scans, setScans] = useState<Record<string, AgentScanResult | null>>({});
  const [commands, setCommands] = useState<Record<string, AgentCommand[]>>({});
  const [frames, setFrames] = useState<Record<string, AgentCommandStreamFrame[]>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingMetadata, setSavingMetadata] = useState(false);
  const [savingCommand, setSavingCommand] = useState(false);
  const [savingOperation, setSavingOperation] = useState<AgentOperationKind | "">("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [token, setToken] = useState<{ serverId: string; serverName: string; serverKind: ServerKind; value: string } | null>(null);
  const [form, setForm] = useState<ServerCreateRequest>(defaultServerCreateRequest);
  const [metadataTarget, setMetadataTarget] = useState("");
  const [metadata, setMetadata] = useState<ServerProbeMetadataUpdate>(() => metadataFor());
  const [command, setCommand] = useState({ server_id: "", method: "GET", path: "/api/child/system/info", query: "", bodyText: "", timeout_ms: 30000, stream: false });
  const [domainProbe, setDomainProbe] = useState({ domainsText: "", timeout_ms: 2000, allow_icmp: false });
  const [route, setRoute] = useState({ targets: routeTargets(), ip_version: 4 as 4 | 6, timeout_seconds: 25 });
  const [settings, setSettings] = useState({ xray_mode: "external" as XrayMode, listen_port: 23889, master_url: "", only_if_recovery: true });
  const [logs, setLogs] = useState({ name: "", all: false, confirmed: false });
  const [streamPort, setStreamPort] = useState(443);
  const [management, setManagement] = useState({ open: false, serverId: "", mode: "edit" as "edit" | "remove" });
  const [bootstrap, setBootstrap] = useState({ open: false, serverId: "", serverName: "", serverKind: "direct" as ServerKind });
  const [lifecycle, setLifecycle] = useState({ open: false, serverId: "", action: "agent_upgrade" as AgentLifecycleAction });
  const [xrayDialog, setXrayDialog] = useState({ action: "" as "" | "xray_install" | "xray_remove" | "xray_rollback", target: "", confirmed: false });
  const [xrayRelease, setXrayRelease] = useState({ version: "v26.3.27", sha256: "", state: "preserve" as "preserve" | "start" | "stop" });
  const control = useRef({ active: false, epoch: 0, inventorySequence: 0, commandSequence: 0,
    saving: false, savingMetadata: false, savingCommand: false, savingOperation: false, target: "", metadataTarget: "", servers: [] as ServerSummary[] });
  const poll = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const selectedServer = servers.find(server => server.id === command.server_id);
  const selectedFederated = selectedServer?.is_federated === true;
  const selectedAgent = agents[command.server_id];
  const workspace = selectedAgent?.capabilities.xray_config_workspace === true;
  const capabilities = selectedAgent?.capabilities;
  const workspaceMessage = !command.server_id ? "请选择服务器以管理其 Xray 配置文件。"
    : selectedFederated ? "分享服务器由拥有方控制 Agent；请在服务器分享页面管理获授权的入站。"
    : !selectedAgent ? "请先安装并连接升级后的 Agent，再管理 Xray 配置文件。"
      : "此 Agent 版本未上报 xray_config_workspace 能力，请先升级 Agent。";
  const settingsMessage = !command.server_id ? "请选择服务器以管理其 Agent 设置。" : selectedFederated
    ? "分享服务器的 Agent 仅由拥有方控制，本控制台保留状态、流量和获授权入站的只读/受限能力。" : !selectedAgent
    ? "请先连接 Agent，再管理 Agent 设置。" : "根据 Agent 上报的能力，已禁用不支持的控件。";
  const options = servers.map(server => ({ value: server.id, label: `${server.name}${server.is_federated ? "（分享）" : ""}` }));
  const metadataOptions = servers.filter(server => !server.is_federated).map(server => ({ value: server.id, label: server.name }));
  const blocked = !command.server_id || selectedFederated || Boolean(savingOperation);
  const current = (epoch: number) => control.current.active && control.current.epoch === epoch;
  function report(failure: unknown) { setError(failure instanceof Error ? failure.message : "请求失败。"); }

  async function refreshCommands(items: ServerSummary[]) {
    const epoch = control.current.epoch; const sequence = ++control.current.commandSequence;
    const rows = await Promise.all(items.map(async server => [server.id,
      (await listServerCommands(server.id).catch(() => ({ commands: [] as AgentCommand[] }))).commands] as const));
    if (!current(epoch) || sequence !== control.current.commandSequence) return;
    setCommands(Object.fromEntries(rows));
    const streamed = rows.flatMap(([, entries]) => entries).filter(entry => entry.stream);
    const nextFrames = await Promise.all(streamed.map(async entry => [entry.id,
      (await listCommandStreamFrames(entry.server_id, entry.id).catch(() => ({ frames: [] as AgentCommandStreamFrame[] }))).frames] as const));
    if (current(epoch) && sequence === control.current.commandSequence) setFrames(Object.fromEntries(nextFrames));
  }
  async function refreshServers() {
    const epoch = control.current.epoch; const sequence = ++control.current.inventorySequence;
    setLoading(true); setError("");
    try {
      const [next, nextAgents] = await Promise.all([listServers(), listAgents()]);
      if (!current(epoch) || sequence !== control.current.inventorySequence) return;
      control.current.servers = next; setServers(next);
      setAgents(Object.fromEntries(nextAgents.map(agent => [agent.server_id, agent])));
      const target = next.some(server => server.id === control.current.target) ? control.current.target : next[0]?.id ?? "";
      control.current.target = target; setCommand(previous => ({ ...previous, server_id: target }));
      const local = next.filter(server => !server.is_federated);
      const metadataId = local.some(server => server.id === control.current.metadataTarget) ? control.current.metadataTarget : local[0]?.id ?? "";
      control.current.metadataTarget = metadataId; setMetadataTarget(metadataId); setMetadata(metadataFor(next.find(server => server.id === metadataId)));
      const [nextTelemetry, nextScans] = await Promise.all([
        Promise.all(next.map(async server => [server.id, (await getLatestTelemetry(server.id).catch(() => null))?.latest ?? null] as const)),
        Promise.all(next.map(async server => [server.id, (await getLatestScanResult(server.id).catch(() => null))?.scan ?? null] as const)),
        refreshCommands(next),
      ]);
      if (current(epoch) && sequence === control.current.inventorySequence) {
        setTelemetry(Object.fromEntries(nextTelemetry)); setScans(Object.fromEntries(nextScans));
      }
    } catch (failure) { if (current(epoch) && sequence === control.current.inventorySequence) report(failure); }
    finally { if (current(epoch) && sequence === control.current.inventorySequence) setLoading(false); }
  }
  async function refreshLifecycleCommands() {
    const epoch = control.current.epoch;
    try {
      await refreshCommands(control.current.servers);
      const next = await listServers();
      if (current(epoch)) { control.current.servers = next; setServers(next); }
    } catch (failure) { if (current(epoch)) report(failure); }
  }
  useLayoutEffect(() => { refreshRef.current = refreshLifecycleCommands; });
  useLayoutEffect(() => {
    control.current.active = true; control.current.epoch += 1;
    void refreshServers();
    return () => { control.current.active = false; control.current.epoch += 1; clearTimeout(poll.current); };
  }, []);
  const pending = Object.values(commands).flat().some(entry => ["waiting", "pending", "leased"].includes(entry.status)
    && (diagnosticPaths.has(entry.path) || entry.path.startsWith("/api/child/warp/") || lifecyclePaths.test(entry.path)));
  useLayoutEffect(() => {
    clearTimeout(poll.current);
    if (pending) poll.current = setTimeout(() => { void refreshRef.current(); }, 2000);
    return () => clearTimeout(poll.current);
  }, [pending, commands]);
  useLayoutEffect(() => {
    control.current.target = command.server_id;
    setLogs(previous => ({ ...previous, confirmed: false }));
  }, [command.server_id]);

  async function submitServer() {
    if (control.current.saving || !form.name.trim()) return;
    if (!integerInRange(form.listen_port, 0, 65535)) { setError("端口必须是 0 至 65535 之间的整数。"); return; }
    if (!integerInRange(form.traffic_limit, 0, Number.MAX_SAFE_INTEGER)) { setError("流量限额必须是非负安全整数，单位为字节。"); return; }
    if (!validPrice(form.renewal_price) || !validPrice(form.renewal_price_cny)) { setError("续费价格可留空；填写时必须是有限的非负数。"); return; }
    const epoch = control.current.epoch;
    control.current.saving = true; setSaving(true); setError(""); setSuccess(""); setToken(null);
    try {
      const response = await createServer({ ...form, ...metadataPayload(form), name: form.name.trim(), ip_address: textOrNull(form.ip_address),
        ip_address_v6: textOrNull(form.ip_address_v6), domain: textOrNull(form.domain), domain_v6: textOrNull(form.domain_v6) });
      if (!current(epoch)) return;
      setToken({ serverId: response.server.id, serverName: response.server.name,
        serverKind: response.server.server_kind ?? form.server_kind ?? "direct", value: response.agent_token });
      setForm(defaultServerCreateRequest()); await refreshServers();
    } catch (failure) { if (current(epoch)) report(failure); }
    finally { if (current(epoch)) { control.current.saving = false; setSaving(false); } }
  }
  async function submitMetadata() {
    if (!metadataTarget || control.current.savingMetadata || servers.find(server => server.id === metadataTarget)?.is_federated) return;
    if (!validPrice(metadata.renewal_price) || !validPrice(metadata.renewal_price_cny)) { setError("续费价格可留空；填写时必须是有限的非负数。"); return; }
    const target = metadataTarget; const epoch = control.current.epoch;
    control.current.savingMetadata = true; setSavingMetadata(true); setError(""); setSuccess("");
    try {
      const response = await updateServerProbeMetadata(target, metadataPayload(metadata));
      if (!current(epoch)) return;
      setServers(previous => {
        const next = previous.map(server => server.id === response.server.id ? response.server : server);
        control.current.servers = next; return next;
      });
      if (control.current.metadataTarget === target) setMetadata(metadataFor(response.server));
      setSuccess("探针元数据已保存。");
    } catch (failure) { if (current(epoch)) report(failure); }
    finally { if (current(epoch)) { control.current.savingMetadata = false; setSavingMetadata(false); } }
  }
  async function queue(kind: AgentOperationKind, payload?: AgentOperationPayload, target = command.server_id) {
    if (!target || control.current.savingOperation || !control.current.active
      || control.current.servers.find(server => server.id === target)?.is_federated) return false;
    const epoch = control.current.epoch;
    control.current.savingOperation = true; setSavingOperation(kind); setError(""); setSuccess("");
    try {
      await queueAgentOperation(target, kind, payload);
      if (!current(epoch)) return false;
      await refreshCommands(control.current.servers);
      return current(epoch);
    } catch (failure) { if (current(epoch)) report(failure); return false; }
    finally { if (current(epoch)) { control.current.savingOperation = false; setSavingOperation(""); } }
  }
  function quick(kind: AgentOperationKind) {
    if (!command.server_id || selectedFederated || control.current.savingOperation) return;
    if (["xray_system_config_read", "xray_config_files_list"].includes(kind) && !workspace) { setError(workspaceMessage); return; }
    if (kind === "xray_install" || kind === "xray_remove" || kind === "xray_rollback") {
      setError(""); setXrayDialog({ action: kind, target: command.server_id, confirmed: false }); return;
    }
    if (kind === "agent_upgrade" || kind === "agent_rollback" || kind === "agent_uninstall") {
      setLifecycle({ open: true, serverId: command.server_id, action: kind }); return;
    }
    void queue(kind);
  }
  const xrayValid = /^v[0-9]{1,4}\.[0-9]{1,2}\.[0-9]{1,2}$/.test(xrayRelease.version.trim())
    && (/^[0-9a-f]{64}$/.test(xrayRelease.sha256.trim()) || (!xrayRelease.sha256.trim() && ["v26.3.27", "v26.2.6"].includes(xrayRelease.version.trim())));
  async function installXray() {
    if (!xrayValid || xrayDialog.action !== "xray_install") return;
    if (await queue("xray_install", { version: xrayRelease.version.trim(), sha256: xrayRelease.sha256.trim() || undefined,
      start: xrayRelease.state === "preserve" ? undefined : xrayRelease.state === "start" }, xrayDialog.target)) {
      setXrayDialog(previous => ({ ...previous, action: "", confirmed: false }));
    }
  }
  async function confirmXray() {
    if (!xrayDialog.confirmed || !["xray_remove", "xray_rollback"].includes(xrayDialog.action)) return;
    if (await queue(xrayDialog.action as "xray_remove" | "xray_rollback", {}, xrayDialog.target)) {
      setXrayDialog(previous => ({ ...previous, action: "", confirmed: false }));
    }
  }
  async function purgeLogs() {
    if (!logs.confirmed) return;
    if (!logs.all && !logs.name.trim()) { setError("请输入日志文件名，或选择全部文件。"); return; }
    const target = command.server_id;
    if (await queue("log_files_delete", logs.all ? { all: true } : { name: logs.name.trim() })) {
      if (control.current.target === target) setLogs(previous => ({ ...previous, name: previous.all ? previous.name : "", confirmed: false }));
    }
  }
  function switchSetting(kind: "agent_switch_xray_mode" | "agent_switch_listen_port" | "agent_probe_master_url" | "agent_update_master_url") {
    if (capabilities?.[kind] !== true) { setError(settingsMessage); return; }
    if (kind === "agent_switch_xray_mode") { void queue(kind, { xray_mode: settings.xray_mode }); return; }
    if (kind === "agent_switch_listen_port") {
      if (!integerInRange(settings.listen_port, 0, 65535)) { setError("监听端口必须是 0 至 65535 之间的整数。"); return; }
      void queue(kind, { listen_port: settings.listen_port }); return;
    }
    if (!settings.master_url.trim()) { setError("请输入控制台地址。"); return; }
    void queue(kind, { master_url: settings.master_url.trim(), ...(kind === "agent_update_master_url" ? { only_if_recovery: settings.only_if_recovery } : {}) });
  }
  async function submitLatency() {
    const domains = domainProbe.domainsText.split(/[\n,]+/).map(value => value.trim()).filter(Boolean);
    if (!domains.length) { setError("请至少输入一个延迟探测目标。"); return; }
    if (!integerInRange(domainProbe.timeout_ms, 200, 10000)) { setError("延迟探测超时必须是 200 至 10000 之间的整数，单位为毫秒。"); return; }
    const target = command.server_id;
    if (await queue("domain_latency", { domains, timeout_ms: domainProbe.timeout_ms, allow_icmp: domainProbe.allow_icmp,
      command_timeout_ms: latencyCommandTimeout(domains.length, domainProbe.timeout_ms, domainProbe.allow_icmp) })) {
      if (control.current.target === target) setDomainProbe(previous => ({ ...previous, domainsText: "" }));
    }
  }
  function submitRoute() {
    if (route.targets.some(target => target.host.trim() && !integerInRange(target.port, 1, 65535))) {
      setError("回程路由端口必须是 1 至 65535 之间的整数。"); return;
    }
    const targets = selectedRouteTargets(route.targets);
    if (!targets.length) { setError("请至少输入一个回程路由目标。"); return; }
    if (!integerInRange(route.timeout_seconds, 10, 45)) { setError("路由探测超时必须是 10 至 45 之间的整数，单位为秒。"); return; }
    void queue("return_route_test", { targets, ip_version: route.ip_version, timeout_seconds: route.timeout_seconds,
      command_timeout_ms: targets.length * route.timeout_seconds * 1000 + 5000 });
  }
  async function submitCommand() {
    if (!command.server_id || selectedFederated || !command.path.trim() || control.current.savingCommand) return;
    if (!integerInRange(command.timeout_ms, 1000, 300000)) { setError("命令超时必须是 1000 至 300000 之间的整数，单位为毫秒。"); return; }
    let body: unknown = null;
    try { if (command.bodyText.trim()) body = JSON.parse(command.bodyText); }
    catch { setError("命令请求体必须是有效的 JSON。"); return; }
    const epoch = control.current.epoch; const target = command.server_id;
    control.current.savingCommand = true; setSavingCommand(true); setError("");
    try {
      await createServerCommand(target, { method: command.method, path: command.path.trim(), query: command.query.trim(),
        body, timeout_ms: command.timeout_ms, stream: command.stream });
      if (!current(epoch)) return;
      if (control.current.target === target) setCommand(previous => ({ ...previous, bodyText: "" }));
      await refreshCommands(control.current.servers);
    } catch (failure) { if (current(epoch)) report(failure); }
    finally { if (current(epoch)) { control.current.savingCommand = false; setSavingCommand(false); } }
  }

  const columns: ColumnsType<ServerSummary> = [
    { title: "名称", key: "name", width: 200, render: (_, server) => <Space orientation="vertical" size={2}>
      <Space><Typography.Text strong>{server.name}</Typography.Text>{server.is_federated && <Tag color="purple">分享</Tag>}
        {!server.is_federated && <Tag>{serverKindNames[server.server_kind ?? "direct"]}</Tag>}</Space>
      <Typography.Text type="secondary">{server.xray_mode === "embedded" ? "嵌入式" : zhStatus(server.xray_mode)} Xray</Typography.Text>
      {!server.is_federated && <Space size={0}><Button type="text" icon={<DownloadOutlined />} aria-label={`在 ${server.name} 上安装 Agent`}
        onClick={() => setBootstrap({ open: true, serverId: server.id, serverName: server.name, serverKind: server.server_kind ?? "direct" })} />
        <Button type="text" icon={<EditOutlined />} aria-label={`编辑 ${server.name}`} onClick={() => setManagement({ open: true, serverId: server.id, mode: "edit" })} />
        <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除 ${server.name}`} onClick={() => setManagement({ open: true, serverId: server.id, mode: "remove" })} /></Space>}
    </Space> },
    { title: "状态", key: "status", width: 110, render: (_, server) => <Tag color={{ pending: "gold", connected: "green", offline: "default" }[server.status]}>
      {server.is_federated ? server.status === "connected" ? "拥有方在线" : "拥有方离线" : { pending: "待连接", connected: "已连接", offline: "离线" }[server.status]}</Tag> },
    { title: "连接地址", key: "endpoint", width: 180, render: (_, server) => server.domain || server.ip_address || server.domain_v6 || server.ip_address_v6 || "未分配" },
    { title: "探针", key: "probe", width: 220, render: (_, server) => <Space orientation="vertical" size={0}>
      <span>{[server.region_city || server.region_name || server.region, server.region_country].filter(Boolean).join(" · ") || "暂无地区"}</span>
      <Typography.Text type="secondary">{server.provider_name || "暂无服务商"} · {renewalLabel(server)}</Typography.Text></Space> },
    { title: "遥测数据", key: "telemetry", width: 180, render: (_, server) => <Space orientation="vertical" size={0}>
      <span>{telemetryLabel(telemetry[server.id])}</span><Typography.Text type="secondary">{latencyLabel(telemetry[server.id])}</Typography.Text></Space> },
    { title: "Xray", key: "scan", width: 180, render: (_, server) => { const scan = scans[server.id]; return <Space orientation="vertical" size={0}>
      <span>{scan ? scan.xray_running ? "运行中" : "已停止" : "暂无扫描"}</span><Typography.Text type="secondary">{scan
        ? [scan.xray_version, `${scan.inbounds.length} 个入站`, scan.api_port ? `API ${scan.api_port}` : ""].filter(Boolean).join(" · ")
          || (scan.message ? zhMessage(scan.message).slice(0, 80) : "") : ""}</Typography.Text></Space>; } },
    { title: "Nginx", key: "nginx", width: 190, render: (_, server) => { const nginx = scans[server.id]?.nginx; return <Space orientation="vertical" size={0}>
      <span>{nginx ? nginx.running ? "运行中" : nginx.installed ? "已停止" : nginx.available ? "未安装" : "不可用" : "暂无扫描"}</span>
      <Typography.Text type="secondary">{nginx?.version ?? ""}</Typography.Text></Space>; } },
    { title: "模式", dataIndex: "connection_mode", width: 110, render: value => connectionOptions.find(option => option.value === value)?.label ?? value }, { title: "端口", dataIndex: "listen_port", width: 80 },
    { title: "上传", key: "up", width: 110, render: (_, server) => speed(server.current_upload_speed) },
    { title: "下载", key: "down", width: 110, render: (_, server) => speed(server.current_download_speed) },
  ];
  function operationButtons(items: Operation[]) {
    return <Space wrap>{items.map(item => <Button key={item.kind} size="small" aria-label={item.title} disabled={blocked || (item.workspace && !workspace)}
      loading={savingOperation === item.kind} title={item.workspace && !workspace ? workspaceMessage : undefined}
      danger={item.kind.includes("remove") || item.kind === "agent_uninstall"} onClick={() => quick(item.kind)}>{item.title}</Button>)}</Space>;
  }
  const closeXray = () => { if (!control.current.savingOperation) setXrayDialog(previous => ({ ...previous, action: "", confirmed: false })); };

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <div className="branding-page-heading"><div className="min-width-zero">
      <Typography.Title level={2} className="branding-block-text">{branding.brand_title} 控制台</Typography.Title>
      <Typography.Paragraph type="secondary">管理服务器、查看 Agent 遥测数据并下发操作，无需许可证。</Typography.Paragraph>
    </div><Button aria-label="刷新" icon={<ReloadOutlined aria-hidden />} loading={loading} onClick={() => void refreshServers()}>刷新</Button></div>
    {error && <Alert type="error" showIcon title={zhMessage(error)} closable onClose={() => setError("")} />}
    {success && <Alert type="success" showIcon title={success} closable onClose={() => setSuccess("")} />}
    <Row gutter={[16, 16]}><Col xs={24} sm={8}><Card><Statistic title="服务器" value={servers.length} /></Card></Col>
      <Col xs={24} sm={8}><Card><Statistic title="已连接" value={servers.filter(server => server.status === "connected").length} /></Card></Col>
      <Col xs={24} sm={8}><Card><Statistic title="速率" value={`${speed(servers.reduce((sum, server) => sum + server.current_upload_speed, 0))} ↑ / ${speed(servers.reduce((sum, server) => sum + server.current_download_speed, 0))} ↓`} styles={{ content: { fontSize: 18 } }} />
        <Typography.Text type="secondary">{Object.values(telemetry).filter(Boolean).length} 份遥测报告</Typography.Text></Card></Col></Row>
    <Card title="服务器" styles={{ body: { padding: 0 } }}><Table rowKey="id" columns={columns} dataSource={servers}
      loading={loading} pagination={false} scroll={{ x: 1670 }} locale={{ emptyText: <Empty description="暂无服务器。" /> }} /></Card>
    {servers.length > 0 && <ServerTrafficPanel servers={servers} />}
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={9}><Space orientation="vertical" size="large" style={{ width: "100%" }}>
        <Card title="添加服务器"><Form layout="vertical" onFinish={() => void submitServer()} disabled={saving}>
          <Form.Item label="名称" required><Input aria-label="名称" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></Form.Item>
          <Form.Item label="服务器类型" required><Select aria-label="服务器类型" value={form.server_kind ?? "direct"} options={serverKindOptions}
            onChange={(server_kind: ServerKind) => setForm({ ...form, server_kind })} /></Form.Item>
          <Alert type="info" showIcon style={{ marginBottom: 16 }} title={{ direct: "可创建公网直连协议节点。", "leased-line": "专线服务器仅允许创建 Mieru 节点。", residential: "家宽落地服务器仅允许创建 SOCKS5 节点。" }[form.server_kind ?? "direct"]} />
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="IPv4"><Input aria-label="IPv4" value={form.ip_address ?? ""} onChange={event => setForm({ ...form, ip_address: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="连接方式"><Select aria-label="连接方式" value={form.connection_mode} options={connectionOptions} onChange={(value: ConnectionMode) => setForm({ ...form, connection_mode: value })} /></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="端口"><StrictInputNumber aria-label="端口" aria-valuemin={0} aria-valuemax={65535}
            value={form.listen_port ?? Number.NaN} onChange={value => setForm(previous => ({ ...previous, listen_port: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Xray"><Select aria-label="Xray" value={form.xray_mode} options={xrayOptions} onChange={(value: XrayMode) => setForm({ ...form, xray_mode: value })} /></Form.Item></Col></Row>
          <Form.Item label="流量限额（字节）"><StrictInputNumber aria-label="流量限额（字节）" aria-valuemin={0} aria-valuemax={Number.MAX_SAFE_INTEGER}
            value={form.traffic_limit ?? Number.NaN} onChange={value => setForm(previous => ({ ...previous, traffic_limit: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="探针城市"><Input aria-label="探针城市" value={form.region_city ?? ""} onChange={event => setForm({ ...form, region_city: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="服务商"><Input aria-label="新服务器服务商" value={form.provider_name ?? ""} onChange={event => setForm({ ...form, provider_name: event.target.value })} /></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="到期日期"><Input aria-label="新服务器到期日期" type="date" value={form.expires_at ?? ""} onChange={event => setForm({ ...form, expires_at: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="续费价格"><StrictInputNumber aria-label="新服务器续费价格" aria-valuemin={0} allowEmpty
              value={form.renewal_price ?? null} onChange={renewal_price => setForm(previous => ({ ...previous, renewal_price }))} style={{ width: "100%" }} /></Form.Item></Col></Row>
          <Form.Item label="IPv6"><Switch aria-label="IPv6" checked={form.ipv6_enabled} onChange={checked => setForm({ ...form, ipv6_enabled: checked })} /></Form.Item>
          <Button htmlType="submit" type="primary" aria-label="创建服务器" icon={<PlusOutlined aria-hidden />} loading={saving} disabled={!form.name.trim()}>创建服务器</Button>
        </Form>
        {token && <Alert style={{ marginTop: 16 }} type="success" showIcon title={`${token.serverName} 的 Agent 令牌`}
          description={<Space orientation="vertical" style={{ width: "100%" }}>
            <Typography.Text>请妥善保存此令牌，用于手动配置 Agent。令牌仅在此处显示。</Typography.Text>
            <Input.TextArea aria-label="Agent 令牌" value={token.value} readOnly rows={2} autoComplete="off" spellCheck={false} style={{ fontFamily: "monospace" }} />
            <Space wrap><Button aria-label="安装 Agent" onClick={() => setBootstrap({ open: true, serverId: token.serverId, serverName: token.serverName, serverKind: token.serverKind })}>安装 Agent</Button>
              <Button aria-label="隐藏令牌" onClick={() => setToken(null)}>隐藏令牌</Button></Space></Space>} />}
        </Card>
        <Card title="探针元数据"><Form layout="vertical" onFinish={() => void submitMetadata()}>
          <Form.Item label="服务器"><Select aria-label="元数据服务器" value={metadataTarget || undefined} options={metadataOptions} disabled={!metadataOptions.length || savingMetadata}
            onChange={value => { control.current.metadataTarget = value; setMetadataTarget(value); setMetadata(metadataFor(servers.find(server => server.id === value))); }} /></Form.Item>
          <Row gutter={12}>{([
            ["region", "地区代码"], ["region_country", "国家"], ["region_name", "地区"], ["region_city", "城市"],
            ["provider_name", "服务商"], ["provider_url", "服务商地址"],
          ] as const).map(([key, label]) => <Col key={key} xs={24} sm={12}><Form.Item label={label}><Input aria-label={label} value={metadata[key] ?? ""}
            onChange={event => setMetadata({ ...metadata, [key]: event.target.value })} /></Form.Item></Col>)}</Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="到期日期"><Input aria-label="到期日期" type="date" value={metadata.expires_at ?? ""} onChange={event => setMetadata({ ...metadata, expires_at: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="周期"><Select aria-label="周期" allowClear value={metadata.renewal_cycle ?? undefined} options={cycleOptions}
              onChange={(value: RenewalCycle | undefined) => setMetadata({ ...metadata, renewal_cycle: value ?? null })} /></Form.Item></Col></Row>
          <Row gutter={12}>{([["renewal_price", "续费价格"], ["renewal_price_cny", "人民币价格"]] as const).map(([key, label]) =>
            <Col key={key} xs={24} sm={12}><Form.Item label={label}><StrictInputNumber aria-label={label} aria-valuemin={0} allowEmpty
              value={metadata[key] ?? null} onChange={value => setMetadata(previous => ({ ...previous, [key]: value }))} style={{ width: "100%" }} /></Form.Item></Col>)}</Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="币种"><Input aria-label="币种" value={metadata.renewal_currency ?? ""} onChange={event => setMetadata({ ...metadata, renewal_currency: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="电信互联"><Select aria-label="电信互联" value={metadata.telecom_paid_peer == null ? "unknown" : metadata.telecom_paid_peer ? "paid" : "standard"}
              options={[{ value: "unknown", label: "未知" }, { value: "paid", label: "付费" }, { value: "standard", label: "标准" }]}
              onChange={value => setMetadata({ ...metadata, telecom_paid_peer: value === "unknown" ? null : value === "paid" })} /></Form.Item></Col></Row>
          <Space wrap><Button htmlType="submit" type="primary" aria-label="保存元数据" loading={savingMetadata} disabled={!metadataTarget}>保存元数据</Button>
            <Button aria-label="重新加载" disabled={!metadataTarget || savingMetadata} onClick={() => setMetadata(metadataFor(servers.find(server => server.id === metadataTarget)))}>重新加载</Button></Space>
        </Form></Card>
      </Space></Col>
      <Col xs={24} xl={15}><Card title="命令队列"><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Form layout="vertical"><Form.Item label="目标服务器"><Select aria-label="目标服务器" value={command.server_id || undefined} options={options} disabled={!servers.length}
          onChange={value => { control.current.target = value; setCommand(previous => ({ ...previous, server_id: value })); }} /></Form.Item></Form>
        {selectedFederated && <Alert type="info" showIcon title="分享服务器不接受本地 Agent 命令" description="状态、速度、流量和 Xray 版本来自拥有方；获授权的入站请在服务器分享页面管理。" />}
        {operationButtons(quickOperations)}
        <Typography.Title level={5}>诊断</Typography.Title>{operationButtons(diagnosticOperations)}
        <Typography.Title level={5}>维护操作</Typography.Title>{operationButtons(maintenanceOperations)}
        <Divider />
        <Typography.Title level={5}>Nginx 流转发清理</Typography.Title>
        <Form layout="vertical" onFinish={() => {
          if (!integerInRange(streamPort, 1, 65535)) { setError("流转发端口必须是 1 至 65535 之间的整数。"); return; }
          void queue("nginx_clear_stream_port", { port: streamPort });
        }}><Space wrap align="end"><Form.Item label="流转发端口"><StrictInputNumber aria-label="流转发端口" aria-valuemin={1} aria-valuemax={65535}
          value={streamPort} onChange={value => setStreamPort(value ?? Number.NaN)} /></Form.Item>
          <Form.Item><Button htmlType="submit" aria-label="清理流转发" disabled={blocked} loading={savingOperation === "nginx_clear_stream_port"}>清理流转发</Button></Form.Item></Space></Form>
        <Typography.Title level={5}>服务控制</Typography.Title><Space wrap>{(["xray", "nginx"] as const).map(service => <Button key={service} size="small" aria-label={`重启 ${service === "xray" ? "Xray" : "Nginx"}`} disabled={blocked}
          loading={savingOperation === "service_control"} onClick={() => void queue("service_control", { service, action: "restart" })}>重启 {service === "xray" ? "Xray" : "Nginx"}</Button>)}</Space>
        <Typography.Title level={5}>日志</Typography.Title><Space wrap>{(["agent", "xray", "nginx"] as const).map(service => <Button key={service} size="small" aria-label={`${service === "agent" ? "Agent" : service === "xray" ? "Xray" : "Nginx"} 日志`} disabled={blocked}
          loading={savingOperation === "logs"} onClick={() => void queue("logs", { service, lines: 200 })}>{service === "agent" ? "Agent" : service === "xray" ? "Xray" : "Nginx"} 日志</Button>)}</Space>
        <Form layout="vertical" onFinish={() => void purgeLogs()}><Form.Item label="日志文件名"><Input aria-label="日志文件名" value={logs.name} disabled={logs.all}
          onChange={event => setLogs({ ...logs, name: event.target.value, confirmed: false })} /></Form.Item>
          <Space wrap><Switch aria-label="全部文件" checked={logs.all} onChange={value => setLogs({ ...logs, all: value, confirmed: false })} /><span>全部文件</span>
            <Checkbox checked={logs.confirmed} onChange={event => setLogs({ ...logs, confirmed: event.target.checked })}>确认删除日志</Checkbox>
            <Button danger htmlType="submit" aria-label="清空日志" disabled={blocked || !logs.confirmed} loading={savingOperation === "log_files_delete"}>清空日志</Button></Space></Form>
        <Typography.Title level={5}>读取配置</Typography.Title>
        {command.server_id && !workspace && <Alert type="warning" showIcon title={workspaceMessage} />}{operationButtons(configOperations)}
        <Divider /><Typography.Title level={5}>Agent 设置</Typography.Title>
        {command.server_id && (!capabilities?.agent_switch_xray_mode || !capabilities?.agent_switch_listen_port || !capabilities?.agent_probe_master_url || !capabilities?.agent_update_master_url)
          && <Alert type="info" showIcon title={settingsMessage} />}
        <Form layout="vertical" onFinish={() => switchSetting("agent_update_master_url")}>
          <Row gutter={12}><Col xs={24} sm={16}><Form.Item label="Xray 模式"><Select aria-label="Xray 模式" value={settings.xray_mode} options={xrayOptions} onChange={(value: XrayMode) => setSettings({ ...settings, xray_mode: value })} /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item label=" "><Button aria-label="切换 Xray 模式" disabled={blocked || !capabilities?.agent_switch_xray_mode} loading={savingOperation === "agent_switch_xray_mode"} onClick={() => switchSetting("agent_switch_xray_mode")}>切换</Button></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={16}><Form.Item label="监听端口"><StrictInputNumber aria-label="监听端口" aria-valuemin={0} aria-valuemax={65535}
            value={settings.listen_port} onChange={value => setSettings(previous => ({ ...previous, listen_port: value ?? Number.NaN }))}
            onPressEnter={event => { event.preventDefault(); switchSetting("agent_switch_listen_port"); }} style={{ width: "100%" }} /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item label=" "><Button aria-label="应用监听端口" disabled={blocked || !capabilities?.agent_switch_listen_port} loading={savingOperation === "agent_switch_listen_port"} onClick={() => switchSetting("agent_switch_listen_port")}>应用</Button></Form.Item></Col></Row>
          <Form.Item label="控制台地址"><Input aria-label="控制台地址" value={settings.master_url} onChange={event => setSettings({ ...settings, master_url: event.target.value })} /></Form.Item>
          <Form.Item label="仅限恢复模式"><Switch aria-label="仅限恢复模式" checked={settings.only_if_recovery} onChange={value => setSettings({ ...settings, only_if_recovery: value })} /></Form.Item>
          <Space wrap><Button aria-label="探测" disabled={blocked || !capabilities?.agent_probe_master_url} loading={savingOperation === "agent_probe_master_url"} onClick={() => switchSetting("agent_probe_master_url")}>探测</Button>
            <Button htmlType="submit" aria-label="更新" disabled={blocked || !capabilities?.agent_update_master_url} loading={savingOperation === "agent_update_master_url"}>更新</Button></Space>
        </Form>
        <Divider />
        <Form layout="vertical" onFinish={() => void submitLatency()}><Form.Item label="延迟探测目标"><Input.TextArea aria-label="延迟探测目标" value={domainProbe.domainsText} rows={2} onChange={event => setDomainProbe({ ...domainProbe, domainsText: event.target.value })} /></Form.Item>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="超时"><StrictInputNumber aria-label="延迟探测超时" aria-valuemin={200} aria-valuemax={10000}
            value={domainProbe.timeout_ms} onChange={value => setDomainProbe(previous => ({ ...previous, timeout_ms: value ?? Number.NaN }))} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="ICMP"><Switch aria-label="ICMP" checked={domainProbe.allow_icmp} onChange={value => setDomainProbe({ ...domainProbe, allow_icmp: value })} /></Form.Item></Col></Row>
          <Button htmlType="submit" aria-label="下发延迟探测" disabled={blocked} loading={savingOperation === "domain_latency"}>下发延迟探测</Button></Form>
        <Form layout="vertical" onFinish={submitRoute}><Typography.Title level={5}>回程路由</Typography.Title>
          <RouteProbeFields value={route.targets} onChange={value => setRoute({ ...route, targets: value })} />
          <Form.Item label="IP 版本"><Radio.Group optionType="button" value={route.ip_version} options={[{ value: 4, label: "IPv4" }, { value: 6, label: "IPv6" }]}
            onChange={event => setRoute({ ...route, ip_version: event.target.value as 4 | 6 })} /></Form.Item>
          <Form.Item label="路由探测超时（秒）"><StrictInputNumber aria-label="路由探测超时（秒）" aria-valuemin={10} aria-valuemax={45}
            value={route.timeout_seconds} onChange={value => setRoute(previous => ({ ...previous, timeout_seconds: value ?? Number.NaN }))} /></Form.Item>
          <Button htmlType="submit" aria-label="追踪回程路由" disabled={blocked} loading={savingOperation === "return_route_test"}>追踪回程路由</Button></Form>
        <Divider /><Typography.Title level={5}>自定义命令</Typography.Title>
        <Form layout="vertical" onFinish={() => void submitCommand()}><Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item label="请求方法"><Select aria-label="请求方法" value={command.method} options={["GET", "POST", "PUT", "PATCH", "DELETE"].map(value => ({ value, label: value }))} onChange={value => setCommand({ ...command, method: value })} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item label="超时"><StrictInputNumber aria-label="命令超时" aria-valuemin={1000} aria-valuemax={300000}
            value={command.timeout_ms} onChange={value => setCommand(previous => ({ ...previous, timeout_ms: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col></Row>
          <Form.Item label="路径"><Input aria-label="路径" value={command.path} onChange={event => setCommand({ ...command, path: event.target.value })} /></Form.Item>
          <Form.Item label="查询参数"><Input aria-label="查询参数" value={command.query} onChange={event => setCommand({ ...command, query: event.target.value })} /></Form.Item>
          <Form.Item label="JSON 请求体"><Input.TextArea aria-label="JSON 请求体" value={command.bodyText} rows={2} onChange={event => setCommand({ ...command, bodyText: event.target.value })} style={{ fontFamily: "monospace" }} /></Form.Item>
          <Form.Item label="流式输出"><Switch aria-label="流式输出" checked={command.stream} onChange={value => setCommand({ ...command, stream: value })} /></Form.Item>
          <Button htmlType="submit" type="primary" aria-label="下发命令" disabled={!command.server_id || selectedFederated || savingCommand} loading={savingCommand}>下发命令</Button>
        </Form>
        <CommandInspector commands={commands[command.server_id] ?? []} streamFramesByCommand={frames} />
      </Space></Card></Col>
    </Row>
    <ServerManagementDialog {...management} onOpenChange={open => setManagement(previous => ({ ...previous, open }))} onUpdated={() => void refreshServers()} />
    <AgentBootstrapDialog {...bootstrap} onOpenChange={open => setBootstrap(previous => ({ ...previous, open }))} onUpdated={() => void refreshServers()} />
    <AgentLifecycleDialog {...lifecycle} serverName={servers.find(server => server.id === lifecycle.serverId)?.name ?? ""}
      onOpenChange={open => setLifecycle(previous => ({ ...previous, open }))} onUpdated={() => void refreshLifecycleCommands()} />
    <Modal open={xrayDialog.action === "xray_install"} title="安装 / 升级 Xray" width={560} destroyOnHidden
      mask={{ closable: !savingOperation }} keyboard={!savingOperation} closable={!savingOperation} onCancel={closeXray}
      footer={<Space><Button aria-label="取消" disabled={Boolean(savingOperation)} onClick={closeXray}>取消</Button><Button type="primary" aria-label="安装" htmlType="submit" form="xray-release-form"
        disabled={!xrayValid || Boolean(savingOperation)} loading={savingOperation === "xray_install"}>安装</Button></Space>}>
      <Typography.Paragraph>{servers.find(server => server.id === xrayDialog.target)?.name}</Typography.Paragraph>{error && <Alert type="error" title={zhMessage(error)} showIcon />}
      <Form id="xray-release-form" layout="vertical" preserve={false} disabled={Boolean(savingOperation)} onFinish={() => void installXray()}>
        <Form.Item label="Xray 版本"><AutoComplete aria-label="Xray 版本" value={xrayRelease.version} options={[{ value: "v26.3.27" }, { value: "v26.2.6" }]}
          onChange={value => setXrayRelease({ ...xrayRelease, version: value })} /></Form.Item>
        <Form.Item label="压缩包 SHA-256 校验和"><Input.TextArea aria-label="压缩包 SHA-256 校验和" value={xrayRelease.sha256} rows={2} maxLength={64} onChange={event => setXrayRelease({ ...xrayRelease, sha256: event.target.value })} style={{ fontFamily: "monospace" }} /></Form.Item>
        <Form.Item label="运行状态"><Select aria-label="运行状态" value={xrayRelease.state} options={[{ value: "preserve", label: "保持当前状态" }, { value: "start", label: "运行中" }, { value: "stop", label: "已停止" }]}
          onChange={(value: "preserve" | "start" | "stop") => setXrayRelease({ ...xrayRelease, state: value })} /></Form.Item>
      </Form>
    </Modal>
    <Modal open={xrayDialog.action === "xray_remove" || xrayDialog.action === "xray_rollback"} title={xrayDialog.action === "xray_remove" ? "移除 Xray" : "回退 Xray"} destroyOnHidden
      mask={{ closable: !savingOperation }} keyboard={!savingOperation} closable={!savingOperation} onCancel={closeXray}
      footer={<Space><Button aria-label="取消" disabled={Boolean(savingOperation)} onClick={closeXray}>取消</Button><Button type="primary" aria-label="确认" danger={xrayDialog.action === "xray_remove"}
        disabled={!xrayDialog.confirmed || Boolean(savingOperation)} loading={Boolean(savingOperation)} onClick={() => void confirmXray()}>确认</Button></Space>}>
      {error && <Alert type="error" title={zhMessage(error)} showIcon />}<Typography.Paragraph>{servers.find(server => server.id === xrayDialog.target)?.name}</Typography.Paragraph>
      <Checkbox checked={xrayDialog.confirmed} disabled={Boolean(savingOperation)} onChange={event => setXrayDialog({ ...xrayDialog, confirmed: event.target.checked })}>确认更改运行时</Checkbox>
    </Modal>
  </Space>;
}
