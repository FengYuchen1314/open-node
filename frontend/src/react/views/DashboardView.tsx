import { useLayoutEffect, useRef, useState } from "react";
import { Alert, AutoComplete, Button, Card, Checkbox, Col, Divider, Empty, Form, Input, Modal,
  Radio, Row, Select, Space, Statistic, Switch, Table, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { defaultServerCreateRequest, type AgentCommand, type AgentCommandStreamFrame, type AgentRead,
  type AgentOperationKind, type AgentOperationPayload, type AgentScanResult, type AgentTelemetry,
  type ConnectionMode, type RenewalCycle, type ServerCreateRequest, type ServerProbeMetadataUpdate,
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

export interface DashboardViewProps { }
type Operation = { title: string; kind: AgentOperationKind; workspace?: boolean };
const quickOperations: Operation[] = [{ title: "System info", kind: "system_info" }, { title: "Traffic", kind: "traffic" }, { title: "Speed", kind: "speed" }];
const diagnosticOperations: Operation[] = [{ title: "Services", kind: "services_status" }, { title: "NICs", kind: "system_nics" },
  { title: "Scan", kind: "scan" }, { title: "Log files", kind: "log_files_list" }];
const maintenanceOperations: Operation[] = [
  { title: "Install Xray", kind: "xray_install" }, { title: "Remove Xray", kind: "xray_remove" },
  { title: "Xray release", kind: "xray_release" }, { title: "Roll back Xray", kind: "xray_rollback" },
  { title: "Install Nginx", kind: "nginx_install" }, { title: "Remove Nginx", kind: "nginx_remove" },
  { title: "Install WARP", kind: "warp_install" }, { title: "WARP status", kind: "warp_status" },
  { title: "Remove WARP", kind: "warp_remove" }, { title: "Upgrade Agent", kind: "agent_upgrade" },
  { title: "Roll back Agent", kind: "agent_rollback" }, { title: "Uninstall Agent", kind: "agent_uninstall" },
];
const configOperations: Operation[] = [{ title: "Xray config", kind: "xray_config_read" },
  { title: "Xray system", kind: "xray_system_config_read", workspace: true },
  { title: "Xray files", kind: "xray_config_files_list", workspace: true },
  { title: "Nginx config", kind: "nginx_config_read" }, { title: "Nginx files", kind: "nginx_config_files_list" }];
const connectionOptions = ["auto", "websocket", "http", "pull"].map(value => ({ value,
  label: { auto: "Auto", websocket: "WebSocket", http: "HTTP", pull: "Pull" }[value] }));
const xrayOptions = [{ value: "external", label: "External" }, { value: "embedded", label: "Embedded" }];
const cycleNames: Record<RenewalCycle, string> = { month: "Month", quarter: "Quarter", half_year: "Half year", year: "Year" };
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
  if (metrics?.has_mem && metrics.mem_total > 0) parts.push(`${(metrics.mem_used / metrics.mem_total * 100).toFixed(0)}% mem`);
  return parts.join(" · ") || "No telemetry";
}
function latencyLabel(item?: AgentTelemetry | null) {
  if (!item?.latency.length) return "No probe";
  const successful = item.latency.filter(sample => sample.success);
  return successful.length ? `${(successful.reduce((sum, sample) => sum + sample.latency_ms, 0) / successful.length).toFixed(0)} ms` : "Probe failed";
}
function renewalLabel(server: ServerSummary) {
  return [server.expires_at?.slice(0, 10), server.renewal_price == null ? "" : `${server.renewal_price} ${server.renewal_currency ?? ""}`.trim(),
    server.renewal_cycle ? cycleNames[server.renewal_cycle] : ""].filter(Boolean).join(" · ") || "No renewal";
}

export default function DashboardView(_props: DashboardViewProps) {
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
  const [token, setToken] = useState<{ serverId: string; serverName: string; value: string } | null>(null);
  const [form, setForm] = useState<ServerCreateRequest>(defaultServerCreateRequest);
  const [metadataTarget, setMetadataTarget] = useState("");
  const [metadata, setMetadata] = useState<ServerProbeMetadataUpdate>(() => metadataFor());
  const [command, setCommand] = useState({ server_id: "", method: "GET", path: "/api/child/system/info", query: "", bodyText: "", timeout_ms: 30000, stream: false });
  const [domainProbe, setDomainProbe] = useState({ domainsText: "", timeout_ms: 2000, allow_icmp: false });
  const [route, setRoute] = useState({ targets: routeTargets(), ip_version: 4 as 4 | 6, timeout_seconds: 25 });
  const [settings, setSettings] = useState({ xray_mode: "external" as XrayMode, listen_port: 23889, master_url: "", only_if_recovery: true, warp_license: "" });
  const [logs, setLogs] = useState({ name: "", all: false, confirmed: false });
  const [streamPort, setStreamPort] = useState(443);
  const [management, setManagement] = useState({ open: false, serverId: "", mode: "edit" as "edit" | "remove" });
  const [bootstrap, setBootstrap] = useState({ open: false, serverId: "", serverName: "" });
  const [lifecycle, setLifecycle] = useState({ open: false, serverId: "", action: "agent_upgrade" as AgentLifecycleAction });
  const [xrayDialog, setXrayDialog] = useState({ action: "" as "" | "xray_install" | "xray_remove" | "xray_rollback", target: "", confirmed: false });
  const [xrayRelease, setXrayRelease] = useState({ version: "v26.3.27", sha256: "", state: "preserve" as "preserve" | "start" | "stop" });
  const [warp, setWarp] = useState({ action: "" as "" | "warp_install" | "warp_remove", target: "", confirmed: false });
  const control = useRef({ active: false, epoch: 0, inventorySequence: 0, commandSequence: 0,
    saving: false, savingMetadata: false, savingCommand: false, savingOperation: false, target: "", metadataTarget: "", servers: [] as ServerSummary[] });
  const poll = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const selectedAgent = agents[command.server_id];
  const workspace = selectedAgent?.capabilities.xray_config_workspace === true;
  const capabilities = selectedAgent?.capabilities;
  const workspaceMessage = !command.server_id ? "Select a server to manage its Xray configuration files."
    : !selectedAgent ? "Install and connect an upgraded Agent before managing Xray configuration files."
      : "This Agent version does not advertise the xray_config_workspace capability. Upgrade the Agent first.";
  const settingsMessage = !command.server_id ? "Select a server to manage its Agent settings." : !selectedAgent
    ? "Connect an Agent before managing Agent settings." : "Unsupported controls are disabled from the Agent's advertised capabilities.";
  const options = servers.map(server => ({ value: server.id, label: server.name }));
  const blocked = !command.server_id || Boolean(savingOperation);
  const current = (epoch: number) => control.current.active && control.current.epoch === epoch;
  function report(failure: unknown) { setError(failure instanceof Error ? failure.message : "Request failed."); }

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
      const metadataId = next.some(server => server.id === control.current.metadataTarget) ? control.current.metadataTarget : next[0]?.id ?? "";
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
    setSettings(previous => ({ ...previous, warp_license: "" }));
    setLogs(previous => ({ ...previous, confirmed: false }));
  }, [command.server_id]);

  async function submitServer() {
    if (control.current.saving || !form.name.trim()) return;
    if (!integerInRange(form.listen_port, 0, 65535)) { setError("Port must be an integer between 0 and 65535."); return; }
    if (!integerInRange(form.traffic_limit, 0, Number.MAX_SAFE_INTEGER)) { setError("Traffic limit must be a non-negative safe integer in bytes."); return; }
    if (!validPrice(form.renewal_price) || !validPrice(form.renewal_price_cny)) { setError("Renewal prices must be blank or finite non-negative numbers."); return; }
    const epoch = control.current.epoch;
    control.current.saving = true; setSaving(true); setError(""); setSuccess(""); setToken(null);
    try {
      const response = await createServer({ ...form, ...metadataPayload(form), name: form.name.trim(), ip_address: textOrNull(form.ip_address),
        ip_address_v6: textOrNull(form.ip_address_v6), domain: textOrNull(form.domain), domain_v6: textOrNull(form.domain_v6) });
      if (!current(epoch)) return;
      setToken({ serverId: response.server.id, serverName: response.server.name, value: response.agent_token });
      setForm(defaultServerCreateRequest()); await refreshServers();
    } catch (failure) { if (current(epoch)) report(failure); }
    finally { if (current(epoch)) { control.current.saving = false; setSaving(false); } }
  }
  async function submitMetadata() {
    if (!metadataTarget || control.current.savingMetadata) return;
    if (!validPrice(metadata.renewal_price) || !validPrice(metadata.renewal_price_cny)) { setError("Renewal prices must be blank or finite non-negative numbers."); return; }
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
      setSuccess("Probe metadata saved.");
    } catch (failure) { if (current(epoch)) report(failure); }
    finally { if (current(epoch)) { control.current.savingMetadata = false; setSavingMetadata(false); } }
  }
  async function queue(kind: AgentOperationKind, payload?: AgentOperationPayload, target = command.server_id) {
    if (!target || control.current.savingOperation || !control.current.active) return false;
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
    if (!command.server_id || control.current.savingOperation) return;
    if (["xray_system_config_read", "xray_config_files_list"].includes(kind) && !workspace) { setError(workspaceMessage); return; }
    if (kind === "xray_install" || kind === "xray_remove" || kind === "xray_rollback") {
      setError(""); setXrayDialog({ action: kind, target: command.server_id, confirmed: false }); return;
    }
    if (kind === "warp_install" || kind === "warp_remove") {
      setError(""); setWarp({ action: kind, target: command.server_id, confirmed: false }); return;
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
  async function confirmWarp() {
    if (!warp.action || !warp.confirmed) return;
    if (await queue(warp.action, warp.action === "warp_install" ? { accept_terms: true } : { confirm: true }, warp.target)) {
      setWarp(previous => ({ ...previous, action: "", confirmed: false }));
    }
  }
  async function purgeLogs() {
    if (!logs.confirmed) return;
    if (!logs.all && !logs.name.trim()) { setError("Enter a log file name or select All files."); return; }
    const target = command.server_id;
    if (await queue("log_files_delete", logs.all ? { all: true } : { name: logs.name.trim() })) {
      if (control.current.target === target) setLogs(previous => ({ ...previous, name: previous.all ? previous.name : "", confirmed: false }));
    }
  }
  function switchSetting(kind: "agent_switch_xray_mode" | "agent_switch_listen_port" | "agent_probe_master_url" | "agent_update_master_url") {
    if (capabilities?.[kind] !== true) { setError(settingsMessage); return; }
    if (kind === "agent_switch_xray_mode") { void queue(kind, { xray_mode: settings.xray_mode }); return; }
    if (kind === "agent_switch_listen_port") {
      if (!integerInRange(settings.listen_port, 0, 65535)) { setError("Listen port must be an integer between 0 and 65535."); return; }
      void queue(kind, { listen_port: settings.listen_port }); return;
    }
    if (!settings.master_url.trim()) { setError("Enter a Master URL."); return; }
    void queue(kind, { master_url: settings.master_url.trim(), ...(kind === "agent_update_master_url" ? { only_if_recovery: settings.only_if_recovery } : {}) });
  }
  async function updateWarpCredential() {
    if (!settings.warp_license.trim()) { setError("Enter a WARP+ credential."); return; }
    const target = command.server_id;
    if (await queue("warp_license", { license: settings.warp_license.trim() })) {
      if (control.current.target === target) setSettings(previous => ({ ...previous, warp_license: "" }));
    }
  }
  async function submitLatency() {
    const domains = domainProbe.domainsText.split(/[\n,]+/).map(value => value.trim()).filter(Boolean);
    if (!domains.length) { setError("Enter at least one latency target."); return; }
    if (!integerInRange(domainProbe.timeout_ms, 200, 10000)) { setError("Latency timeout must be an integer between 200 and 10000 milliseconds."); return; }
    const target = command.server_id;
    if (await queue("domain_latency", { domains, timeout_ms: domainProbe.timeout_ms, allow_icmp: domainProbe.allow_icmp,
      command_timeout_ms: latencyCommandTimeout(domains.length, domainProbe.timeout_ms, domainProbe.allow_icmp) })) {
      if (control.current.target === target) setDomainProbe(previous => ({ ...previous, domainsText: "" }));
    }
  }
  function submitRoute() {
    if (route.targets.some(target => target.host.trim() && !integerInRange(target.port, 1, 65535))) {
      setError("Return route ports must be integers between 1 and 65535."); return;
    }
    const targets = selectedRouteTargets(route.targets);
    if (!targets.length) { setError("Enter at least one return route target."); return; }
    if (!integerInRange(route.timeout_seconds, 10, 45)) { setError("Route timeout must be an integer between 10 and 45 seconds."); return; }
    void queue("return_route_test", { targets, ip_version: route.ip_version, timeout_seconds: route.timeout_seconds,
      command_timeout_ms: targets.length * route.timeout_seconds * 1000 + 5000 });
  }
  async function submitCommand() {
    if (!command.server_id || !command.path.trim() || control.current.savingCommand) return;
    if (!integerInRange(command.timeout_ms, 1000, 300000)) { setError("Command timeout must be an integer between 1000 and 300000 milliseconds."); return; }
    let body: unknown = null;
    try { if (command.bodyText.trim()) body = JSON.parse(command.bodyText); }
    catch { setError("Command body must be valid JSON."); return; }
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
    { title: "Name", key: "name", width: 200, render: (_, server) => <Space orientation="vertical" size={2}>
      <Typography.Text strong>{server.name}</Typography.Text><Typography.Text type="secondary">{server.xray_mode} Xray</Typography.Text>
      <Space size={0}><Button type="text" icon={<DownloadOutlined />} aria-label={`Install Agent on ${server.name}`}
        onClick={() => setBootstrap({ open: true, serverId: server.id, serverName: server.name })} />
        <Button type="text" icon={<EditOutlined />} aria-label={`Edit ${server.name}`} onClick={() => setManagement({ open: true, serverId: server.id, mode: "edit" })} />
        <Button type="text" danger icon={<DeleteOutlined />} aria-label={`Remove ${server.name}`} onClick={() => setManagement({ open: true, serverId: server.id, mode: "remove" })} /></Space>
    </Space> },
    { title: "Status", key: "status", width: 110, render: (_, server) => <Tag color={{ pending: "gold", connected: "green", offline: "default" }[server.status]}>
      {{ pending: "Pending", connected: "Connected", offline: "Offline" }[server.status]}</Tag> },
    { title: "Endpoint", key: "endpoint", width: 180, render: (_, server) => server.domain || server.ip_address || server.domain_v6 || server.ip_address_v6 || "Unassigned" },
    { title: "Probe", key: "probe", width: 220, render: (_, server) => <Space orientation="vertical" size={0}>
      <span>{[server.region_city || server.region_name || server.region, server.region_country].filter(Boolean).join(" · ") || "No region"}</span>
      <Typography.Text type="secondary">{server.provider_name || "No provider"} · {renewalLabel(server)}</Typography.Text></Space> },
    { title: "Telemetry", key: "telemetry", width: 180, render: (_, server) => <Space orientation="vertical" size={0}>
      <span>{telemetryLabel(telemetry[server.id])}</span><Typography.Text type="secondary">{latencyLabel(telemetry[server.id])}</Typography.Text></Space> },
    { title: "Xray", key: "scan", width: 180, render: (_, server) => { const scan = scans[server.id]; return <Space orientation="vertical" size={0}>
      <span>{scan ? scan.xray_running ? "Running" : "Stopped" : "No scan"}</span><Typography.Text type="secondary">{scan
        ? [scan.xray_version, `${scan.inbounds.length} inbounds`, scan.api_port ? `API ${scan.api_port}` : ""].filter(Boolean).join(" · ")
          || scan.message?.slice(0, 80) : ""}</Typography.Text></Space>; } },
    { title: "Mode", dataIndex: "connection_mode", width: 110 }, { title: "Port", dataIndex: "listen_port", width: 80 },
    { title: "Up", key: "up", width: 110, render: (_, server) => speed(server.current_upload_speed) },
    { title: "Down", key: "down", width: 110, render: (_, server) => speed(server.current_download_speed) },
  ];
  function operationButtons(items: Operation[]) {
    return <Space wrap>{items.map(item => <Button key={item.kind} size="small" aria-label={item.title} disabled={blocked || (item.workspace && !workspace)}
      loading={savingOperation === item.kind} title={item.workspace && !workspace ? workspaceMessage : undefined}
      danger={item.kind.includes("remove") || item.kind === "agent_uninstall"} onClick={() => quick(item.kind)}>{item.title}</Button>)}</Space>;
  }
  const closeXray = () => { if (!control.current.savingOperation) setXrayDialog(previous => ({ ...previous, action: "", confirmed: false })); };
  const closeWarp = () => { if (!control.current.savingOperation) setWarp(previous => ({ ...previous, action: "", confirmed: false })); };

  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space align="start" wrap style={{ width: "100%", justifyContent: "space-between" }}><div>
      <Typography.Title level={2}>Open Node control plane</Typography.Title>
      <Typography.Paragraph type="secondary">Manage servers, inspect Agent telemetry and queue operations. No license required.</Typography.Paragraph>
    </div><Button aria-label="Refresh" icon={<ReloadOutlined aria-hidden />} loading={loading} onClick={() => void refreshServers()}>Refresh</Button></Space>
    {error && <Alert type="error" showIcon title={error} closable onClose={() => setError("")} />}
    {success && <Alert type="success" showIcon title={success} closable onClose={() => setSuccess("")} />}
    <Row gutter={[16, 16]}><Col xs={24} sm={8}><Card><Statistic title="Servers" value={servers.length} /></Card></Col>
      <Col xs={24} sm={8}><Card><Statistic title="Connected" value={servers.filter(server => server.status === "connected").length} /></Card></Col>
      <Col xs={24} sm={8}><Card><Statistic title="Speed" value={`${speed(servers.reduce((sum, server) => sum + server.current_upload_speed, 0))} ↑ / ${speed(servers.reduce((sum, server) => sum + server.current_download_speed, 0))} ↓`} styles={{ content: { fontSize: 18 } }} />
        <Typography.Text type="secondary">{Object.values(telemetry).filter(Boolean).length} telemetry reports</Typography.Text></Card></Col></Row>
    <Card title="Servers" styles={{ body: { padding: 0 } }}><Table rowKey="id" columns={columns} dataSource={servers}
      loading={loading} pagination={false} scroll={{ x: 1480 }} locale={{ emptyText: <Empty description="No servers yet." /> }} /></Card>
    {servers.length > 0 && <ServerTrafficPanel servers={servers} />}
    <Row gutter={[24, 24]}>
      <Col xs={24} xl={9}><Space orientation="vertical" size="large" style={{ width: "100%" }}>
        <Card title="Add server"><Form layout="vertical" onFinish={() => void submitServer()} disabled={saving}>
          <Form.Item label="Name" required><Input aria-label="Name" value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></Form.Item>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="IPv4"><Input aria-label="IPv4" value={form.ip_address ?? ""} onChange={event => setForm({ ...form, ip_address: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Connection"><Select aria-label="Connection" value={form.connection_mode} options={connectionOptions} onChange={(value: ConnectionMode) => setForm({ ...form, connection_mode: value })} /></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Port"><StrictInputNumber aria-label="Port" aria-valuemin={0} aria-valuemax={65535}
            value={form.listen_port ?? Number.NaN} onChange={value => setForm(previous => ({ ...previous, listen_port: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Xray"><Select aria-label="Xray" value={form.xray_mode} options={xrayOptions} onChange={(value: XrayMode) => setForm({ ...form, xray_mode: value })} /></Form.Item></Col></Row>
          <Form.Item label="Traffic limit (bytes)"><StrictInputNumber aria-label="Traffic limit (bytes)" aria-valuemin={0} aria-valuemax={Number.MAX_SAFE_INTEGER}
            value={form.traffic_limit ?? Number.NaN} onChange={value => setForm(previous => ({ ...previous, traffic_limit: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Probe city"><Input aria-label="Probe city" value={form.region_city ?? ""} onChange={event => setForm({ ...form, region_city: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Provider"><Input aria-label="New server provider" value={form.provider_name ?? ""} onChange={event => setForm({ ...form, provider_name: event.target.value })} /></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Expires"><Input aria-label="New server expires" type="date" value={form.expires_at ?? ""} onChange={event => setForm({ ...form, expires_at: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Renewal"><StrictInputNumber aria-label="Renewal" aria-valuemin={0} allowEmpty
              value={form.renewal_price ?? null} onChange={renewal_price => setForm(previous => ({ ...previous, renewal_price }))} style={{ width: "100%" }} /></Form.Item></Col></Row>
          <Form.Item label="IPv6"><Switch aria-label="IPv6" checked={form.ipv6_enabled} onChange={checked => setForm({ ...form, ipv6_enabled: checked })} /></Form.Item>
          <Button htmlType="submit" type="primary" aria-label="Create server" icon={<PlusOutlined aria-hidden />} loading={saving} disabled={!form.name.trim()}>Create server</Button>
        </Form>
        {token && <Alert style={{ marginTop: 16 }} type="success" showIcon title={`Agent token for ${token.serverName}`}
          description={<Space orientation="vertical" style={{ width: "100%" }}>
            <Typography.Text>Save this token securely for manual Agent setup. It is shown only here.</Typography.Text>
            <Input.TextArea aria-label="Agent token" value={token.value} readOnly rows={2} autoComplete="off" spellCheck={false} style={{ fontFamily: "monospace" }} />
            <Space wrap><Button onClick={() => setBootstrap({ open: true, serverId: token.serverId, serverName: token.serverName })}>Install Agent</Button>
              <Button onClick={() => setToken(null)}>Hide token</Button></Space></Space>} />}
        </Card>
        <Card title="Probe metadata"><Form layout="vertical" onFinish={() => void submitMetadata()}>
          <Form.Item label="Server"><Select aria-label="Metadata server" value={metadataTarget || undefined} options={options} disabled={!servers.length || savingMetadata}
            onChange={value => { control.current.metadataTarget = value; setMetadataTarget(value); setMetadata(metadataFor(servers.find(server => server.id === value))); }} /></Form.Item>
          <Row gutter={12}>{([
            ["region", "Region code"], ["region_country", "Country"], ["region_name", "Region"], ["region_city", "City"],
            ["provider_name", "Provider"], ["provider_url", "Provider URL"],
          ] as const).map(([key, label]) => <Col key={key} xs={24} sm={12}><Form.Item label={label}><Input aria-label={label} value={metadata[key] ?? ""}
            onChange={event => setMetadata({ ...metadata, [key]: event.target.value })} /></Form.Item></Col>)}</Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Expires"><Input aria-label="Expires" type="date" value={metadata.expires_at ?? ""} onChange={event => setMetadata({ ...metadata, expires_at: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Cycle"><Select aria-label="Cycle" allowClear value={metadata.renewal_cycle ?? undefined} options={cycleOptions}
              onChange={(value: RenewalCycle | undefined) => setMetadata({ ...metadata, renewal_cycle: value ?? null })} /></Form.Item></Col></Row>
          <Row gutter={12}>{([["renewal_price", "Renewal price"], ["renewal_price_cny", "CNY price"]] as const).map(([key, label]) =>
            <Col key={key} xs={24} sm={12}><Form.Item label={label}><StrictInputNumber aria-label={label} aria-valuemin={0} allowEmpty
              value={metadata[key] ?? null} onChange={value => setMetadata(previous => ({ ...previous, [key]: value }))} style={{ width: "100%" }} /></Form.Item></Col>)}</Row>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Currency"><Input aria-label="Currency" value={metadata.renewal_currency ?? ""} onChange={event => setMetadata({ ...metadata, renewal_currency: event.target.value })} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="Telecom peer"><Select aria-label="Telecom peer" value={metadata.telecom_paid_peer == null ? "unknown" : metadata.telecom_paid_peer ? "paid" : "standard"}
              options={[{ value: "unknown", label: "Unknown" }, { value: "paid", label: "Paid" }, { value: "standard", label: "Standard" }]}
              onChange={value => setMetadata({ ...metadata, telecom_paid_peer: value === "unknown" ? null : value === "paid" })} /></Form.Item></Col></Row>
          <Space wrap><Button htmlType="submit" type="primary" aria-label="Save metadata" loading={savingMetadata} disabled={!metadataTarget}>Save metadata</Button>
            <Button disabled={!metadataTarget || savingMetadata} onClick={() => setMetadata(metadataFor(servers.find(server => server.id === metadataTarget)))}>Reload</Button></Space>
        </Form></Card>
      </Space></Col>
      <Col xs={24} xl={15}><Card title="Command queue"><Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Form layout="vertical"><Form.Item label="Target server"><Select aria-label="Target server" value={command.server_id || undefined} options={options} disabled={!servers.length}
          onChange={value => { control.current.target = value; setCommand(previous => ({ ...previous, server_id: value })); }} /></Form.Item></Form>
        {operationButtons(quickOperations)}
        <Typography.Title level={5}>Diagnostics</Typography.Title>{operationButtons(diagnosticOperations)}
        <Typography.Title level={5}>Maintenance workflows</Typography.Title>{operationButtons(maintenanceOperations)}
        <Divider />
        <Typography.Title level={5}>Nginx stream cleanup</Typography.Title>
        <Form layout="vertical" onFinish={() => {
          if (!integerInRange(streamPort, 1, 65535)) { setError("Stream port must be an integer between 1 and 65535."); return; }
          void queue("nginx_clear_stream_port", { port: streamPort });
        }}><Space wrap align="end"><Form.Item label="Stream port"><StrictInputNumber aria-label="Stream port" aria-valuemin={1} aria-valuemax={65535}
          value={streamPort} onChange={value => setStreamPort(value ?? Number.NaN)} /></Form.Item>
          <Form.Item><Button htmlType="submit" aria-label="Clear stream" disabled={blocked} loading={savingOperation === "nginx_clear_stream_port"}>Clear stream</Button></Form.Item></Space></Form>
        <Typography.Title level={5}>Service control</Typography.Title><Space wrap>{(["xray", "nginx"] as const).map(service => <Button key={service} size="small" aria-label={`Restart ${service === "xray" ? "Xray" : "Nginx"}`} disabled={blocked}
          loading={savingOperation === "service_control"} onClick={() => void queue("service_control", { service, action: "restart" })}>Restart {service === "xray" ? "Xray" : "Nginx"}</Button>)}</Space>
        <Typography.Title level={5}>Logs</Typography.Title><Space wrap>{(["agent", "xray", "nginx"] as const).map(service => <Button key={service} size="small" aria-label={`${service === "agent" ? "Agent" : service === "xray" ? "Xray" : "Nginx"} logs`} disabled={blocked}
          loading={savingOperation === "logs"} onClick={() => void queue("logs", { service, lines: 200 })}>{service === "agent" ? "Agent" : service === "xray" ? "Xray" : "Nginx"} logs</Button>)}</Space>
        <Form layout="vertical" onFinish={() => void purgeLogs()}><Form.Item label="Log file"><Input aria-label="Log file" value={logs.name} disabled={logs.all}
          onChange={event => setLogs({ ...logs, name: event.target.value, confirmed: false })} /></Form.Item>
          <Space wrap><Switch aria-label="All files" checked={logs.all} onChange={value => setLogs({ ...logs, all: value, confirmed: false })} /><span>All files</span>
            <Checkbox checked={logs.confirmed} onChange={event => setLogs({ ...logs, confirmed: event.target.checked })}>Confirm log deletion</Checkbox>
            <Button danger htmlType="submit" aria-label="Purge logs" disabled={blocked || !logs.confirmed} loading={savingOperation === "log_files_delete"}>Purge logs</Button></Space></Form>
        <Typography.Title level={5}>Config reads</Typography.Title>
        {command.server_id && !workspace && <Alert type="warning" showIcon title={workspaceMessage} />}{operationButtons(configOperations)}
        <Divider /><Typography.Title level={5}>Agent settings</Typography.Title>
        {command.server_id && (!capabilities?.agent_switch_xray_mode || !capabilities?.agent_switch_listen_port || !capabilities?.agent_probe_master_url || !capabilities?.agent_update_master_url)
          && <Alert type="info" showIcon title={settingsMessage} />}
        <Form layout="vertical" onFinish={() => switchSetting("agent_update_master_url")}>
          <Row gutter={12}><Col xs={24} sm={16}><Form.Item label="Xray mode"><Select aria-label="Xray mode" value={settings.xray_mode} options={xrayOptions} onChange={(value: XrayMode) => setSettings({ ...settings, xray_mode: value })} /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item label=" "><Button aria-label="Switch Xray mode" disabled={blocked || !capabilities?.agent_switch_xray_mode} loading={savingOperation === "agent_switch_xray_mode"} onClick={() => switchSetting("agent_switch_xray_mode")}>Switch</Button></Form.Item></Col></Row>
          <Row gutter={12}><Col xs={24} sm={16}><Form.Item label="Listen port"><StrictInputNumber aria-label="Listen port" aria-valuemin={0} aria-valuemax={65535}
            value={settings.listen_port} onChange={value => setSettings(previous => ({ ...previous, listen_port: value ?? Number.NaN }))}
            onPressEnter={event => { event.preventDefault(); switchSetting("agent_switch_listen_port"); }} style={{ width: "100%" }} /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item label=" "><Button aria-label="Apply listen port" disabled={blocked || !capabilities?.agent_switch_listen_port} loading={savingOperation === "agent_switch_listen_port"} onClick={() => switchSetting("agent_switch_listen_port")}>Apply</Button></Form.Item></Col></Row>
          <Form.Item label="Master URL"><Input aria-label="Master URL" value={settings.master_url} onChange={event => setSettings({ ...settings, master_url: event.target.value })} /></Form.Item>
          <Form.Item label="Recovery only"><Switch aria-label="Recovery only" checked={settings.only_if_recovery} onChange={value => setSettings({ ...settings, only_if_recovery: value })} /></Form.Item>
          <Space wrap><Button aria-label="Probe" disabled={blocked || !capabilities?.agent_probe_master_url} loading={savingOperation === "agent_probe_master_url"} onClick={() => switchSetting("agent_probe_master_url")}>Probe</Button>
            <Button htmlType="submit" aria-label="Update" disabled={blocked || !capabilities?.agent_update_master_url} loading={savingOperation === "agent_update_master_url"}>Update</Button></Space>
          <Form.Item label="WARP+ credential (optional)" style={{ marginTop: 16 }}><Input.Password aria-label="WARP+ credential (optional)" autoComplete="off" value={settings.warp_license} onChange={event => setSettings({ ...settings, warp_license: event.target.value })} /></Form.Item>
          <Button aria-label="Update WARP+" disabled={blocked} loading={savingOperation === "warp_license"} onClick={() => void updateWarpCredential()}>Update WARP+</Button>
        </Form>
        <Divider />
        <Form layout="vertical" onFinish={() => void submitLatency()}><Form.Item label="Latency targets"><Input.TextArea aria-label="Latency targets" value={domainProbe.domainsText} rows={2} onChange={event => setDomainProbe({ ...domainProbe, domainsText: event.target.value })} /></Form.Item>
          <Row gutter={12}><Col xs={24} sm={12}><Form.Item label="Timeout"><StrictInputNumber aria-label="Latency timeout" aria-valuemin={200} aria-valuemax={10000}
            value={domainProbe.timeout_ms} onChange={value => setDomainProbe(previous => ({ ...previous, timeout_ms: value ?? Number.NaN }))} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item label="ICMP"><Switch aria-label="ICMP" checked={domainProbe.allow_icmp} onChange={value => setDomainProbe({ ...domainProbe, allow_icmp: value })} /></Form.Item></Col></Row>
          <Button htmlType="submit" aria-label="Queue latency probe" disabled={blocked} loading={savingOperation === "domain_latency"}>Queue latency probe</Button></Form>
        <Form layout="vertical" onFinish={submitRoute}><Typography.Title level={5}>Return route</Typography.Title>
          <RouteProbeFields value={route.targets} onChange={value => setRoute({ ...route, targets: value })} />
          <Form.Item label="IP version"><Radio.Group optionType="button" value={route.ip_version} options={[{ value: 4, label: "IPv4" }, { value: 6, label: "IPv6" }]}
            onChange={event => setRoute({ ...route, ip_version: event.target.value as 4 | 6 })} /></Form.Item>
          <Form.Item label="Route timeout seconds"><StrictInputNumber aria-label="Route timeout seconds" aria-valuemin={10} aria-valuemax={45}
            value={route.timeout_seconds} onChange={value => setRoute(previous => ({ ...previous, timeout_seconds: value ?? Number.NaN }))} /></Form.Item>
          <Button htmlType="submit" aria-label="Trace return route" disabled={blocked} loading={savingOperation === "return_route_test"}>Trace return route</Button></Form>
        <Divider /><Typography.Title level={5}>Custom command</Typography.Title>
        <Form layout="vertical" onFinish={() => void submitCommand()}><Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item label="Method"><Select aria-label="Method" value={command.method} options={["GET", "POST", "PUT", "PATCH", "DELETE"].map(value => ({ value, label: value }))} onChange={value => setCommand({ ...command, method: value })} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item label="Timeout"><StrictInputNumber aria-label="Command timeout" aria-valuemin={1000} aria-valuemax={300000}
            value={command.timeout_ms} onChange={value => setCommand(previous => ({ ...previous, timeout_ms: value ?? Number.NaN }))} style={{ width: "100%" }} /></Form.Item></Col></Row>
          <Form.Item label="Path"><Input aria-label="Path" value={command.path} onChange={event => setCommand({ ...command, path: event.target.value })} /></Form.Item>
          <Form.Item label="Query"><Input aria-label="Query" value={command.query} onChange={event => setCommand({ ...command, query: event.target.value })} /></Form.Item>
          <Form.Item label="JSON body"><Input.TextArea aria-label="JSON body" value={command.bodyText} rows={2} onChange={event => setCommand({ ...command, bodyText: event.target.value })} style={{ fontFamily: "monospace" }} /></Form.Item>
          <Form.Item label="Stream"><Switch aria-label="Stream" checked={command.stream} onChange={value => setCommand({ ...command, stream: value })} /></Form.Item>
          <Button htmlType="submit" type="primary" aria-label="Queue command" disabled={!command.server_id || savingCommand} loading={savingCommand}>Queue command</Button>
        </Form>
        <CommandInspector commands={commands[command.server_id] ?? []} streamFramesByCommand={frames} />
      </Space></Card></Col>
    </Row>
    <ServerManagementDialog {...management} onOpenChange={open => setManagement(previous => ({ ...previous, open }))} onUpdated={() => void refreshServers()} />
    <AgentBootstrapDialog {...bootstrap} onOpenChange={open => setBootstrap(previous => ({ ...previous, open }))} onUpdated={() => void refreshServers()} />
    <AgentLifecycleDialog {...lifecycle} serverName={servers.find(server => server.id === lifecycle.serverId)?.name ?? ""}
      onOpenChange={open => setLifecycle(previous => ({ ...previous, open }))} onUpdated={() => void refreshLifecycleCommands()} />
    <Modal open={Boolean(warp.action)} title={warp.action === "warp_install" ? "Install free WARP" : "Remove WARP"} destroyOnHidden
      mask={{ closable: !savingOperation }} keyboard={!savingOperation} closable={!savingOperation} onCancel={closeWarp}
      footer={<Space><Button disabled={Boolean(savingOperation)} onClick={closeWarp}>Cancel</Button><Button type="primary" aria-label={warp.action === "warp_install" ? "Install" : "Remove"} danger={warp.action === "warp_remove"}
        disabled={!warp.confirmed || Boolean(savingOperation)} loading={Boolean(savingOperation)} onClick={() => void confirmWarp()}>{warp.action === "warp_install" ? "Install" : "Remove"}</Button></Space>}>
      <Space orientation="vertical" style={{ width: "100%" }}><Typography.Text>{servers.find(server => server.id === warp.target)?.name}</Typography.Text>
        {error && <Alert type="error" title={error} showIcon />}<Checkbox checked={warp.confirmed} disabled={Boolean(savingOperation)} onChange={event => setWarp({ ...warp, confirmed: event.target.checked })}>
          {warp.action === "warp_install" ? <>I accept the <a href="https://www.cloudflare.com/application/terms/" target="_blank" rel="noopener noreferrer" onClick={event => event.stopPropagation()}>Cloudflare application terms</a></>
            : "Confirm WARP device and outbound removal"}</Checkbox></Space>
    </Modal>
    <Modal open={xrayDialog.action === "xray_install"} title="Install / Upgrade Xray" width={560} destroyOnHidden
      mask={{ closable: !savingOperation }} keyboard={!savingOperation} closable={!savingOperation} onCancel={closeXray}
      footer={<Space><Button disabled={Boolean(savingOperation)} onClick={closeXray}>Cancel</Button><Button type="primary" aria-label="Install" htmlType="submit" form="xray-release-form"
        disabled={!xrayValid || Boolean(savingOperation)} loading={savingOperation === "xray_install"}>Install</Button></Space>}>
      <Typography.Paragraph>{servers.find(server => server.id === xrayDialog.target)?.name}</Typography.Paragraph>{error && <Alert type="error" title={error} showIcon />}
      <Form id="xray-release-form" layout="vertical" preserve={false} disabled={Boolean(savingOperation)} onFinish={() => void installXray()}>
        <Form.Item label="Xray version"><AutoComplete aria-label="Xray version" value={xrayRelease.version} options={[{ value: "v26.3.27" }, { value: "v26.2.6" }]}
          onChange={value => setXrayRelease({ ...xrayRelease, version: value })} /></Form.Item>
        <Form.Item label="Archive SHA-256"><Input.TextArea aria-label="Archive SHA-256" value={xrayRelease.sha256} rows={2} maxLength={64} onChange={event => setXrayRelease({ ...xrayRelease, sha256: event.target.value })} style={{ fontFamily: "monospace" }} /></Form.Item>
        <Form.Item label="Runtime state"><Select aria-label="Runtime state" value={xrayRelease.state} options={[{ value: "preserve", label: "Keep current state" }, { value: "start", label: "Running" }, { value: "stop", label: "Stopped" }]}
          onChange={(value: "preserve" | "start" | "stop") => setXrayRelease({ ...xrayRelease, state: value })} /></Form.Item>
      </Form>
    </Modal>
    <Modal open={xrayDialog.action === "xray_remove" || xrayDialog.action === "xray_rollback"} title={xrayDialog.action === "xray_remove" ? "Remove Xray" : "Roll back Xray"} destroyOnHidden
      mask={{ closable: !savingOperation }} keyboard={!savingOperation} closable={!savingOperation} onCancel={closeXray}
      footer={<Space><Button disabled={Boolean(savingOperation)} onClick={closeXray}>Cancel</Button><Button type="primary" aria-label="Confirm" danger={xrayDialog.action === "xray_remove"}
        disabled={!xrayDialog.confirmed || Boolean(savingOperation)} loading={Boolean(savingOperation)} onClick={() => void confirmXray()}>Confirm</Button></Space>}>
      {error && <Alert type="error" title={error} showIcon />}<Typography.Paragraph>{servers.find(server => server.id === xrayDialog.target)?.name}</Typography.Paragraph>
      <Checkbox checked={xrayDialog.confirmed} disabled={Boolean(savingOperation)} onChange={event => setXrayDialog({ ...xrayDialog, confirmed: event.target.checked })}>Confirm runtime change</Checkbox>
    </Modal>
  </Space>;
}
