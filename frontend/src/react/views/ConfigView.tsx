import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Collapse, Descriptions, Empty, Form, Input, Modal, Row, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, CloseOutlined, ReloadOutlined } from "@ant-design/icons";
import type { AgentCommand, AgentCommandStreamFrame, AgentRead, AgentOperationKind, AgentOperationPayload, AgentXraySystemConfigOperationRequest, ServerSummary, XrayConfigSnapshot, XrayRuntimeInbound, XrayRuntimeInventoryResponse, XrayRuntimeTunnel, XrayRuntimeTunnelChain, XrayRuntimeTunnelInventoryResponse } from "../../domain/inventory";
import type { XrayRuntimeCredentialReconciliationEntry, XrayRuntimeCredentialReconciliationResponse, XrayRuntimeNodeDraft, XrayRuntimeNodeReconciliationManagedEntry, XrayRuntimeNodeReconciliationResponse } from "../../domain/subscriptions";
import { isJsoncFilename, isWritableXrayFileResult, latestSuccessfulGetResult, parseJsonObjectText } from "../../domain/xray-config-workspace";
import { acceptXrayConfigPendingRecovery, applyXrayConfigRecovery, createXrayRuntimeTunnelChain, deleteXrayRuntimeTunnel, deployXrayRuntimeTunnel, getXrayRuntimeInventory, getXrayRuntimeTunnelInventory, listCommandStreamFrames, listAgents, listServerCommands, listServers, listXrayConfigSnapshots, queueAgentOperation, restoreXrayConfigSnapshot } from "../../services/inventory";
import { cleanupExtraXrayRuntimeCredentials, createManagedNodeFromRuntimeInbound, getXrayRuntimeCredentialReconciliation, getXrayRuntimeNodeReconciliation, importManagedNodesFromRuntimeInbounds, listXrayRuntimeNodeDrafts, repairMissingXrayRuntimeCredentials, syncManagedNodeFromRuntime } from "../../services/subscriptions";
import CommandInspector from "../components/CommandInspector";
import LimiterPanel from "../components/LimiterPanel";
import OnlineUsersPanel from "../components/OnlineUsersPanel";
import StrictInputNumber from "../components/StrictInputNumber";
import { zhMessage, zhStatus } from "../../i18n/zh-CN";

export interface ConfigViewProps {
  onCommands?: (serverId: string, commands: AgentCommand[]) => void;
  onUpdated?: () => void;
}
type Revision = { serverId: string; sha256: string };
type SystemState = { revision: Revision | null; writable: boolean; reason: string; apiMode: string; grpcDisableSupported: boolean; grpcPortWritable: boolean; fixedStatsAddress: string };
type FileState = { revision: (Revision & { file: string }) | null; writable: boolean; reason: string };
type RuntimeState = { inventory: XrayRuntimeInventoryResponse | null; tunnels: XrayRuntimeTunnelInventoryResponse | null; drafts: XrayRuntimeNodeDraft[]; nodes: XrayRuntimeNodeReconciliationResponse | null; credentials: XrayRuntimeCredentialReconciliationResponse | null };
const emptyRuntime: RuntimeState = { inventory: null, tunnels: null, drafts: [], nodes: null, credentials: null };
const initialSystemState: SystemState = { revision: null, writable: false, reason: "请先读取当前 Xray 系统配置。", apiMode: "unknown", grpcDisableSupported: false, grpcPortWritable: false, fixedStatsAddress: "" };
const initialFileState: FileState = { revision: null, writable: false, reason: "编辑前请先读取对应的 Xray 文件。" };
const logLevels: AgentXraySystemConfigOperationRequest["log_level"][] = ["none", "error", "warning", "info", "debug"];
const workspaceOperations = new Set<AgentOperationKind>(["xray_system_config_read", "xray_system_config_write", "xray_config_files_list", "xray_config_file_read", "xray_config_file_write"]);
const runtimeOperations: { label: string; value: AgentOperationKind }[] = [{ label: "管理入站", value: "inbounds_manage" }, { label: "管理出站", value: "outbounds_manage" }, { label: "管理路由", value: "routing_manage" }, { label: "批量应用", value: "batch_apply" }, { label: "限速", value: "limiter" }, { label: "回程路由测试", value: "return_route_test" }];
const siteOperations: { label: string; value: AgentOperationKind }[] = [{ label: "配置 SSL", value: "nginx_setup_ssl" }, { label: "删除网站", value: "nginx_website_delete" }, { label: "部署证书", value: "cert_deploy" }, { label: "验证网站", value: "validate_site" }];
const tunnelPortFields = [{ key: "listenPort", label: "公网端口" }, { key: "nginxPort", label: "Nginx 端口" }, { key: "forwardPort", label: "回落端口" }, { key: "apiPort", label: "Xray API 端口" }, { key: "metricsPort", label: "指标端口" }] as const;
const queueAndScan = { queue_agent_commands: true, queue_scan_after_apply: true };
const asRecord = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const readableError = (error: unknown) => zhMessage(error);
const blankToNull = (value: string) => value.trim() || null;
const remarkLabel = (value: string) => zhMessage(value);
const statusLabel = (value: string) => ({ missing_runtime_clients: "缺少客户端", extra_runtime_clients: "存在多余客户端", drift: "客户端不一致", pending_recovery: "待恢复" } as Record<string, string>)[value] ?? zhStatus(value);
const shortHash = (value: string) => value.slice(0, 12);
// Validate ports here: InputNumber min/max would silently clamp invalid input on blur or Enter.
const validPort = (value: number, zero = false) => Number.isInteger(value) && value >= (zero ? 0 : 1) && value <= 65535;
function jsonError(value: string, label: string) { try { parseJsonObjectText(value, label); return ""; } catch (error) { return readableError(error); } }
function formatBytes(value: number) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let current = Math.max(0, value), index = 0;
  while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1; }
  return `${index === 0 ? Math.round(current) : current.toFixed(current < 10 ? 1 : 0)} ${units[index]}`;
}
function formatTraffic(traffic?: { uplink: number; downlink: number }) { return `上传 ${formatBytes(traffic?.uplink ?? 0)} / 下载 ${formatBytes(traffic?.downlink ?? 0)}`; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date); }
function tunnelTarget(address?: string | null, port?: number | null) {
  if (!address) return port == null ? "无目标" : String(port);
  if (port == null) return address;
  return `${address.includes(":") && !address.startsWith("[") ? `[${address}]` : address}:${port}`;
}
const tunnelKey = (tunnel: XrayRuntimeTunnel) => `${tunnel.kind}:${tunnel.tag}:${tunnel.rule_index ?? "inbound"}`;
function useFields<T extends object>(initial: T) {
  const [value, setValue] = useState(initial);
  return [value, (patch: Partial<T>) => setValue((previous) => ({ ...previous, ...patch })), setValue] as const;
}

export default function ConfigView(props: ConfigViewProps) {
  const [servers, setServers] = useState<ServerSummary[]>([]);
  const [agents, setAgents] = useState<Record<string, AgentRead>>({});
  const [selectedId, setSelectedId] = useState("");
  const [commands, setCommands] = useState<Record<string, AgentCommand[]>>({});
  const [frames, setFrames] = useState<Record<string, AgentCommandStreamFrame[]>>({});
  const [snapshots, setSnapshots] = useState<XrayConfigSnapshot[]>([]);
  const [runtime, setRuntime] = useState<RuntimeState>(emptyRuntime);
  const [loading, setLoading] = useState(false);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [savingOperation, setSavingOperation] = useState<AgentOperationKind | "">("");
  const [actionKey, setActionKey] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [activeTab, setActiveTab] = useState("xray");
  const [xray, patchXray] = useFields({ path: "", configText: '{\n  "inbounds": [],\n  "outbounds": []\n}', force: false });
  const [system, patchSystem, setSystem] = useFields({ log_level: "warning" as AgentXraySystemConfigOperationRequest["log_level"], metrics_enabled: false, metrics_listen: "127.0.0.1:11111", stats_enabled: true, grpc_enabled: true, grpc_port: 46736 });
  const [systemState, patchSystemState, setSystemState] = useFields<SystemState>(initialSystemState);
  const [dnsText, setDnsText] = useState("{}\n");
  const [policyText, setPolicyText] = useState("{}\n");
  const [file, patchFile] = useFields({ file: "xray.json", content: "" });
  const [fileState, patchFileState, setFileState] = useFields<FileState>(initialFileState);
  const [nginx, patchNginx] = useFields({ path: "", configText: "events {}\nhttp {}\n" });
  const [nginxFile, patchNginxFile] = useFields({ file: "servers/site.conf", path: "servers/site.conf", content: "server {\n    listen 80;\n}\n" });
  const [runtimeOperation, setRuntimeOperation] = useState<AgentOperationKind>("inbounds_manage");
  const [runtimePayload, setRuntimePayload] = useState('{\n  "action": "add-client",\n  "tag": "vless-443",\n  "client": {\n    "id": "uuid",\n    "email": "user@example.com"\n  }\n}');
  const [siteOperation, setSiteOperation] = useState<AgentOperationKind>("nginx_setup_ssl");
  const [sitePayload, setSitePayload] = useState('{\n  "domain": "example.com"\n}');
  const [chain, patchChain, setChain] = useFields({ label: "relay", serverIds: [] as string[], entryPort: 19000, targetAddress: "127.0.0.1", targetPort: 443 });
  const [deploy, patchDeploy] = useFields({ domain: "", proxyDomain: "", siteType: "static" as "static" | "proxy", siteValue: "", listenAddress: "0.0.0.0", listenPort: 443, nginxPort: 8001, forwardPort: 46174, apiPort: 46736, metricsPort: 38889, certName: "", clearStreamPort: true, restartXray: true, force: false });
  const [takeoverOpen, setTakeoverOpen] = useState(false);
  const [takeoverBusy, setTakeoverBusy] = useState(false);
  const [takeoverConfirmed, setTakeoverConfirmed] = useState(false);
  const [takeoverError, setTakeoverError] = useState("");
  const [takeoverPreview, setTakeoverPreview] = useState<{ target: string; files: string[]; sha256: string; running: boolean } | null>(null);
  const mounted = useRef(true);
  const selectedRef = useRef("");
  const serversRef = useRef(servers);
  const epoch = useRef(0);
  const requests = useRef({ refresh: 0, commands: 0, frames: 0, snapshots: 0, runtime: 0, takeover: 0, operation: 0, action: 0 });
  const operationBusy = useRef(false);
  const actionBusy = useRef(false);
  const callbacks = useRef(props);
  callbacks.current = props;
  const selectedServer = servers.find((server) => server.id === selectedId);
  const agent = agents[selectedId];
  const workspaceSupported = agent?.capabilities.xray_config_workspace === true;
  const upgradeMessage = !selectedId ? "请选择服务器以管理其 Xray 配置。" : !agent ? "使用 Xray 系统配置或配置文件前，请先安装新版 Open Node Agent 并使其连接控制台。" : `请升级此服务器上的 Open Node Agent ${agent.agent_version ?? "（版本未知）"}；该版本未声明支持 xray_config_workspace 功能。`;
  const selectedCommands = commands[selectedId] ?? [];
  const currentSnapshot = snapshots.find((snapshot) => snapshot.status === "current");
  const pendingSnapshot = snapshots.find((snapshot) => snapshot.status === "pending_recovery");
  const systemReady = systemState.writable && systemState.revision?.serverId === selectedId;
  const dnsError = jsonError(dnsText, "DNS"), policyError = jsonError(policyText, "策略");
  const systemWriteReady = systemReady && !dnsError && !policyError && validPort(system.grpc_port);
  const fileWriteReady = fileState.writable && !isJsoncFilename(file.file) && fileState.revision?.serverId === selectedId && fileState.revision.file === file.file.trim();
  const mutationBusy = Boolean(savingOperation || actionKey);
  const runtimeBusy = mutationBusy || runtimeLoading;
  const serverOptions = servers.map((server) => ({ value: server.id, label: server.name }));
  const ports = tunnelPortFields.map(({ key }) => deploy[key]);
  const portsInvalid = ports.some((port) => !validPort(port)) || new Set(ports).size !== ports.length;
  const deployDisabled = runtimeBusy || !selectedId || !deploy.domain.trim() || !deploy.listenAddress.trim() || portsInvalid || deploy.siteType === "proxy" && !deploy.siteValue.trim();
  const chainDisabled = runtimeBusy || chain.serverIds.length < 2 || chain.serverIds.length > 16 || !chain.label.trim() || !chain.targetAddress.trim() || !validPort(chain.entryPort, true) || !validPort(chain.targetPort);
  const missingNodeCount = runtime.drafts.filter((draft) => draft.create_available && !draft.existing_node_id).length;
  const nodeIssues = (runtime.nodes?.managed_entries ?? []).filter((entry) => ["stale", "missing_runtime"].includes(entry.status));
  const credentialIssues = (runtime.credentials?.entries ?? []).filter((entry) => entry.status !== "in_sync");
  const draftByIndex = new Map(runtime.drafts.map((draft) => [draft.source_index, draft]));
  const entryByIndex = new Map((runtime.nodes?.runtime_entries ?? []).map((entry) => [entry.source_index, entry]));
  const runtimeStatus = !runtime.inventory?.has_scan ? "未扫描" : runtime.inventory.xray_running ? "运行中" : "已停止";
  const runtimeSummary = !runtime.inventory?.has_scan ? "尚未上报运行时扫描结果" : [runtime.inventory.xray_version, runtime.inventory.api_port ? `api ${runtime.inventory.api_port}` : "", runtime.inventory.updated_at ? formatDate(runtime.inventory.updated_at) : ""].filter(Boolean).join(" / ") || (runtime.inventory.message ? zhMessage(runtime.inventory.message) : "运行时扫描结果已就绪");

  function currentContext(serverId: string, generation: number) { return mounted.current && serverId === selectedRef.current && generation === epoch.current; }
  function closeTakeover() { requests.current.takeover += 1; setTakeoverOpen(false); setTakeoverBusy(false); setTakeoverPreview(null); setTakeoverConfirmed(false); }
  function syncDefaults(serverId: string, inventory: ServerSummary[]) {
    const server = inventory.find((item) => item.id === serverId);
    const domain = server?.domain ?? server?.pull_address ?? "";
    patchDeploy({ domain, proxyDomain: server?.pull_address && server.pull_address !== domain ? server.pull_address : "", certName: domain.trim().toLowerCase().replace(/^\*\./, "_.") });
    setChain((previous) => {
      const ids = new Set(inventory.map((item) => item.id));
      const next = [serverId, ...previous.serverIds.filter((id) => id !== serverId)].filter((id) => ids.has(id));
      for (const item of inventory) { if (next.length >= 2) break; if (!next.includes(item.id)) next.push(item.id); }
      return { ...previous, serverIds: [...new Set(next)].slice(0, 16) };
    });
  }
  function selectServer(id: string, inventory = serversRef.current) {
    if (id === selectedRef.current) return;
    selectedRef.current = id;
    epoch.current += 1;
    setSelectedId(id);
    setSystemState({ ...initialSystemState });
    setFileState({ ...initialFileState });
    setDnsText("{}\n"); setPolicyText("{}\n");
    setSnapshots([]); setRuntime(emptyRuntime); setSuccess(""); setError("");
    closeTakeover(); syncDefaults(id, inventory);
  }
  async function refreshFrames(allCommands: AgentCommand[]) {
    const request = ++requests.current.frames;
    const entries = await Promise.all(allCommands.filter((command) => command.stream).map(async (command) => {
      try { return [command.id, (await listCommandStreamFrames(command.server_id, command.id)).frames] as const; }
      catch { return [command.id, [] as AgentCommandStreamFrame[]] as const; }
    }));
    if (mounted.current && request === requests.current.frames) setFrames(Object.fromEntries(entries));
  }
  async function refreshCommands(inventory = serversRef.current) {
    const request = ++requests.current.commands;
    const entries = await Promise.all(inventory.map(async (server) => {
      try { return [server.id, (await listServerCommands(server.id)).commands] as const; }
      catch { return [server.id, [] as AgentCommand[]] as const; }
    }));
    if (!mounted.current || request !== requests.current.commands) return;
    const next: Record<string, AgentCommand[]> = Object.fromEntries(entries);
    setCommands(next);
    entries.forEach(([id, items]) => callbacks.current.onCommands?.(id, items));
    await refreshFrames(Object.values(next).flat());
  }
  function receiveCommands(serverId: string, items: AgentCommand[]) {
    if (!mounted.current) return;
    requests.current.commands += 1;
    setCommands((previous) => ({ ...previous, [serverId]: items }));
    callbacks.current.onCommands?.(serverId, items);
  }
  async function refreshSnapshots(includeConfig = false) {
    const serverId = selectedRef.current, generation = epoch.current, request = ++requests.current.snapshots;
    if (!serverId) { setSnapshots([]); setSnapshotsLoading(false); return []; }
    setSnapshotsLoading(true);
    const current = () => currentContext(serverId, generation) && request === requests.current.snapshots;
    try {
      const response = await listXrayConfigSnapshots(serverId, { limit: 8, withConfig: includeConfig });
      if (current()) setSnapshots(response.snapshots);
      return current() ? response.snapshots : [];
    } catch (failure) { if (current()) { setSnapshots([]); if (includeConfig) setError(readableError(failure)); } return []; }
    finally { if (current()) setSnapshotsLoading(false); }
  }
  async function refreshRuntime(reportErrors = false) {
    const serverId = selectedRef.current, generation = epoch.current, request = ++requests.current.runtime;
    if (!serverId) { setRuntime(emptyRuntime); setRuntimeLoading(false); return; }
    const current = () => currentContext(serverId, generation) && request === requests.current.runtime;
    setRuntimeLoading(true);
    try {
      const [inventory, tunnels, drafts, nodes, credentials] = await Promise.all([getXrayRuntimeInventory(serverId), getXrayRuntimeTunnelInventory(serverId), listXrayRuntimeNodeDrafts(serverId), getXrayRuntimeNodeReconciliation(serverId), getXrayRuntimeCredentialReconciliation(serverId)]);
      if (current()) setRuntime({ inventory, tunnels, drafts: drafts.drafts, nodes, credentials });
    } catch (failure) { if (current()) { setRuntime(emptyRuntime); if (reportErrors) setError(readableError(failure)); } }
    finally { if (current()) setRuntimeLoading(false); }
  }
  async function refresh() {
    const request = ++requests.current.refresh;
    setLoading(true); setError("");
    try {
      const [inventory, agents] = await Promise.all([listServers(), listAgents()]);
      if (!mounted.current || request !== requests.current.refresh) return;
      serversRef.current = inventory; setServers(inventory); setAgents(Object.fromEntries(agents.map((agent) => [agent.server_id, agent])));
      const id = inventory.some((server) => server.id === selectedRef.current) ? selectedRef.current : inventory[0]?.id ?? "";
      const changed = id !== selectedRef.current;
      if (changed) selectServer(id, inventory); else syncDefaults(id, inventory);
      await refreshCommands(inventory);
      if (!changed && mounted.current && request === requests.current.refresh) await Promise.all([refreshSnapshots(), refreshRuntime()]);
    } catch (failure) { if (mounted.current && request === requests.current.refresh) setError(readableError(failure)); }
    finally { if (mounted.current && request === requests.current.refresh) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; epoch.current += 1; requests.current.refresh += 1; requests.current.takeover += 1; };
  }, []);
  useEffect(() => { void refreshSnapshots(); void refreshRuntime(); }, [selectedId]);

  async function queueOperation(kind: AgentOperationKind, payload?: AgentOperationPayload) {
    const serverId = selectedRef.current, generation = epoch.current;
    if (!serverId) { setError("请选择目标服务器。"); return false; }
    if (workspaceOperations.has(kind) && !workspaceSupported) { setError(upgradeMessage); return false; }
    if (operationBusy.current || actionBusy.current) return false;
    const request = ++requests.current.operation;
    operationBusy.current = true; setSavingOperation(kind); setError("");
    try {
      await queueAgentOperation(serverId, kind, payload);
      await refreshCommands();
      if (currentContext(serverId, generation)) callbacks.current.onUpdated?.();
      return currentContext(serverId, generation);
    } catch (failure) { if (currentContext(serverId, generation)) setError(readableError(failure)); return false; }
    finally { if (request === requests.current.operation) { operationBusy.current = false; if (mounted.current) setSavingOperation(""); } }
  }
  async function mutate(key: string, work: (serverId: string, isCurrent: () => boolean) => Promise<string | void>) {
    const serverId = selectedRef.current, generation = epoch.current;
    if (!serverId) { setError("请选择目标服务器。"); return; }
    if (actionBusy.current || operationBusy.current) return;
    const request = ++requests.current.action;
    actionBusy.current = true; setActionKey(key); setError(""); setSuccess("");
    const current = () => currentContext(serverId, generation);
    try {
      const message = await work(serverId, current);
      if (!current()) return;
      if (message) setSuccess(message);
      callbacks.current.onUpdated?.();
      await Promise.all([refreshCommands(), refreshRuntime()]);
    } catch (failure) { if (current()) setError(readableError(failure)); }
    finally { if (request === requests.current.action) { actionBusy.current = false; if (mounted.current) setActionKey(""); } }
  }
  function invalidateSystem(reason: string) { patchSystemState({ revision: null, writable: false, reason }); }
  function invalidateFile(reason: string) { patchFileState({ revision: null, writable: false, reason }); }
  function readSystem() { invalidateSystem("请等待读取命令完成，再使用最新结果。"); void queueOperation("xray_system_config_read"); }
  function readFile(list = false) { invalidateFile(`请等待${list ? "列出文件" : "读取"}命令完成，再使用最新结果。`); void queueOperation(list ? "xray_config_files_list" : "xray_config_file_read", list ? undefined : { file: file.file.trim() }); }
  async function writeSystem() {
    if (!systemReady || !systemState.revision) { setError(systemState.reason); return; }
    if (!validPort(system.grpc_port)) { setError("Xray gRPC API 端口必须为 1–65535 的整数。"); return; }
    try {
      const dns = parseJsonObjectText(dnsText, "DNS"), policy = parseJsonObjectText(policyText, "策略");
      if (await queueOperation("xray_system_config_write", { ...system, dns, policy, expected_sha256: systemState.revision.sha256 })) invalidateSystem("再次写入前，请重新读取当前 Xray 系统配置。");
    } catch (failure) { setError(readableError(failure)); }
  }
  async function writeFile() {
    if (!fileWriteReady || !fileState.revision) { setError(fileState.reason); return; }
    if (await queueOperation("xray_config_file_write", { file: file.file.trim(), content: file.content, expected_sha256: fileState.revision.sha256 })) invalidateFile("再次写入前，请重新读取文件。");
  }
  function latestResult(path: string, content = false) {
    return asRecord(selectedCommands.find((command) => command.path === path && (content ? command.method === "GET" && typeof asRecord(command.result_body)?.content === "string" : command.result_body != null))?.result_body);
  }
  function useLatestRaw(kind: "xray" | "nginx" | "nginx-file") {
    const body = latestResult(kind === "xray" ? "/api/child/xray/config" : kind === "nginx" ? "/api/child/nginx/config" : "/api/child/nginx/config-files", kind === "nginx-file");
    const content = kind === "nginx-file" ? body?.content : body?.config;
    if (typeof content !== "string") { setError(`暂无已完成的${kind === "xray" ? " Xray 配置" : kind === "nginx" ? " Nginx 配置" : " Nginx 文件"}结果。`); return; }
    if (kind === "xray") patchXray({ configText: content, ...(typeof body?.path === "string" ? { path: body.path } : {}) });
    else if (kind === "nginx") patchNginx({ configText: content, ...(typeof body?.path === "string" ? { path: body.path } : {}) });
    else patchNginxFile({ content, ...(typeof body?.path === "string" ? { file: body.path, path: body.path } : {}) });
  }
  function useLatestSystem() {
    const body = latestSuccessfulGetResult(selectedCommands, "/api/child/xray/system-config")?.body;
    const config = asRecord(body?.config), sha256 = body?.sha256;
    if (!config || typeof sha256 !== "string" || !/^[0-9a-f]{64}$/.test(sha256)) { invalidateSystem("暂无完整的 Xray 系统配置读取结果。"); setError("暂无已完成的 Xray 系统配置结果。"); return; }
    const dns = asRecord(config.dns), policy = asRecord(config.policy), logLevel = config.log_level;
    if (typeof logLevel !== "string" || !logLevels.includes(logLevel as typeof system.log_level) || !dns || !policy) { const message = "Xray 系统配置读取结果缺少有效的日志、DNS 或策略对象。"; invalidateSystem(message); setError(message); return; }
    setSystem((value) => ({ ...value, log_level: logLevel as typeof system.log_level,
      ...(typeof config.metrics_enabled === "boolean" ? { metrics_enabled: config.metrics_enabled } : {}),
      ...(typeof config.metrics_listen === "string" ? { metrics_listen: config.metrics_listen } : {}),
      ...(typeof config.stats_enabled === "boolean" ? { stats_enabled: config.stats_enabled } : {}),
      ...(typeof config.grpc_enabled === "boolean" ? { grpc_enabled: config.grpc_enabled } : {}),
      ...(typeof config.grpc_port === "number" ? { grpc_port: config.grpc_port } : {}),
    }));
    setDnsText(`${JSON.stringify(dns, null, 2)}\n`); setPolicyText(`${JSON.stringify(policy, null, 2)}\n`);
    setSystemState({ revision: config.writable === true ? { serverId: selectedId, sha256 } : null, writable: config.writable === true,
      reason: typeof config.read_only_reason === "string" && config.read_only_reason ? config.read_only_reason : config.writable === true ? "" : "此 Xray 系统配置结构在表单中仅可读取。",
      apiMode: typeof config.api_mode === "string" ? config.api_mode : "unknown", grpcDisableSupported: config.grpc_disable_supported === true, grpcPortWritable: config.grpc_port_writable === true, fixedStatsAddress: typeof config.fixed_stats_address === "string" ? config.fixed_stats_address : "",
    });
  }
  function useLatestFile() {
    const body = latestSuccessfulGetResult(selectedCommands, "/api/child/xray/config-files")?.body;
    if (!body) { invalidateFile("暂无成功的 Xray 文件读取或列表结果。"); setError("暂无已完成的 Xray 文件读取或列表结果。"); return; }
    if (typeof body.content !== "string") {
      const main = asRecord(body.files)?.main, entry = Array.isArray(main) ? asRecord(main[0]) : null;
      if (typeof entry?.name === "string") { patchFile({ file: entry.name }); invalidateFile(typeof entry.read_only_reason === "string" && entry.read_only_reason ? entry.read_only_reason : "最新结果为文件列表。请先读取主配置文件，再进行编辑。"); return; }
      invalidateFile("最新的 Xray config-files 结果不完整。"); setError("暂无已完成的 Xray 文件读取或列表结果。"); return;
    }
    const filename = typeof body.path === "string" ? body.path.split(/[\\/]/).pop() ?? file.file : file.file;
    patchFile({ file: filename, content: body.content });
    if (!isWritableXrayFileResult(body, filename)) { invalidateFile(typeof body.read_only_reason === "string" && body.read_only_reason ? body.read_only_reason : "此 Xray 主配置文件为只读。"); return; }
    if (typeof body.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(body.sha256)) { const message = "Xray 文件读取结果不含有效版本。"; invalidateFile(message); setError(message); return; }
    setFileState({ writable: true, reason: "", revision: { serverId: selectedId, file: filename, sha256: body.sha256 } });
  }
  function queueJson(kind: AgentOperationKind, text: string) {
    try { void queueOperation(kind, text.trim() ? parseJsonObjectText(text, "请求内容") : undefined); }
    catch (failure) { setError(readableError(failure)); }
  }
  async function loadSnapshot(snapshot: XrayConfigSnapshot) {
    const serverId = selectedRef.current, generation = epoch.current;
    const loaded = typeof snapshot.config === "string" ? snapshot : (await refreshSnapshots(true)).find((item) => item.id === snapshot.id);
    if (!currentContext(serverId, generation)) return;
    if (!loaded?.config) { setError("快照配置不可用。"); return; }
    patchXray({ configText: loaded.config }); setActiveTab("xray");
  }
  async function waitTakeover(serverId: string, id: string, request: number, generation: number) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      if (!currentContext(serverId, generation) || request !== requests.current.takeover) return null;
      const response = await listServerCommands(serverId);
      if (!currentContext(serverId, generation) || request !== requests.current.takeover) return null;
      receiveCommands(serverId, response.commands);
      const command = response.commands.find((item) => item.id === id);
      if (command?.status === "failed" || command?.status === "skipped") throw new Error(command.result_error || "Xray 接管命令失败。");
      if (command?.status === "succeeded") { const body = asRecord(command.result_body); if (!body) throw new Error("Xray 接管返回了无效结果。"); return body; }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error("Xray 接管命令仍未完成，请查看命令历史。");
  }
  async function runTakeover(confirm = false) {
    const serverId = selectedRef.current, generation = epoch.current;
    if (!serverId || confirm && (!takeoverConfirmed || !takeoverPreview || takeoverBusy)) return;
    const request = ++requests.current.takeover;
    const current = () => currentContext(serverId, generation) && request === requests.current.takeover;
    setTakeoverOpen(true); setTakeoverBusy(true); setTakeoverError("");
    if (!confirm) { setTakeoverPreview(null); setTakeoverConfirmed(false); }
    try {
      const queued = await queueAgentOperation(serverId, "xray_takeover_external", confirm ? { confirm: true, expected_sha256: takeoverPreview!.sha256 } : { preview: true });
      const result = await waitTakeover(serverId, queued.command.id, request, generation);
      if (!result || !current()) return;
      if (confirm) {
        if (result.success !== true) throw new Error("Xray 接管未完成。");
        closeTakeover(); setSuccess(result.unchanged ? "Xray 配置已合并。" : "Xray 接管已完成。");
        callbacks.current.onUpdated?.(); await Promise.all([refreshSnapshots(), refreshRuntime()]);
      } else {
        if (result.preview !== true || typeof result.config_path !== "string" || typeof result.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(result.source_sha256) || !Array.isArray(result.source_files) || !result.source_files.every((item) => typeof item === "string")) throw new Error("此 Agent 未返回受支持的接管预览。");
        setTakeoverPreview({ target: result.config_path, files: result.source_files, sha256: result.source_sha256, running: result.running === true });
      }
    } catch (failure) { if (current()) setTakeoverError(readableError(failure)); }
    finally { if (current()) setTakeoverBusy(false); }
  }
  function deployTunnel() {
    if (deployDisabled) return;
    void mutate("deploy", async (serverId) => {
      const response = await deployXrayRuntimeTunnel(serverId, { domain: deploy.domain.trim(), proxy_domain: blankToNull(deploy.proxyDomain), site_type: deploy.siteType, site_value: blankToNull(deploy.siteValue), listen_address: deploy.listenAddress.trim(), listen_port: deploy.listenPort, nginx_port: deploy.nginxPort, forward_port: deploy.forwardPort, api_port: deploy.apiPort, metrics_port: deploy.metricsPort, cert_name: blankToNull(deploy.certName), clear_stream_port: deploy.clearStreamPort, restart_xray: deploy.restartXray, force: deploy.force, ...queueAndScan });
      return `已为 ${response.domain} 排队 ${response.commands.length} 条隧道部署命令。${response.scan_command ? "后续扫描已排队。" : ""}${response.warnings.length ? `警告：${response.warnings.map(remarkLabel).join("，")}。` : ""}`;
    });
  }
  function createChain() {
    if (chainDisabled) return;
    void mutate("chain", async () => {
      const response = await createXrayRuntimeTunnelChain({ label: chain.label.trim(), server_ids: [...new Set(chain.serverIds)], entry_port: chain.entryPort, target_address: chain.targetAddress.trim(), target_port: chain.targetPort, ...queueAndScan });
      return `已为 ${response.label}（${tunnelTarget(response.entry_host, response.entry_port)} -> ${response.final_target}）排队 ${response.commands.length} 条链式隧道逐跳命令。${response.scan_commands.length ? `已排队 ${response.scan_commands.length} 次扫描。` : ""}${response.warnings.length ? `警告：${response.warnings.map(remarkLabel).join("，")}。` : ""}`;
    });
  }
  function repairCredentials(cleanup = false) {
    if (runtimeBusy || !runtime.inventory?.has_scan || !(cleanup ? runtime.credentials?.extra_runtime_client_count : runtime.credentials?.missing_runtime_client_count)) return;
    void mutate(cleanup ? "cleanup" : "repair", async (serverId) => {
      const response = cleanup ? await cleanupExtraXrayRuntimeCredentials(serverId, queueAndScan) : await repairMissingXrayRuntimeCredentials(serverId, queueAndScan);
      return `已排队 ${response.commands.length} 条命令，${cleanup ? "移除" : "添加"} ${response.planned_client_count} 个${cleanup ? "多余运行时客户端" : "运行时客户端"}。${response.scan_command ? "后续扫描已排队。" : ""}`;
    });
  }
  function deleteTunnel(tunnel: XrayRuntimeTunnel | XrayRuntimeTunnelChain) {
    if (runtimeBusy) return;
    const isChain = "hops" in tunnel;
    void mutate(`delete:${isChain ? `chain:${tunnel.label}` : tunnelKey(tunnel)}`, async (serverId) => {
      const response = await deleteXrayRuntimeTunnel(serverId, isChain ? { kind: "chain", label: tunnel.label, ...queueAndScan } : { kind: tunnel.kind, tag: tunnel.tag, rule_index: tunnel.rule_index, ...queueAndScan });
      return `已排队 ${response.commands.length} 条${isChain ? "链式隧道" : "隧道"}删除命令。`;
    });
  }

  const operationButton = (label: string, kind: AgentOperationKind, payload?: AgentOperationPayload) => <Button key={kind} aria-label={label} disabled={!selectedId || mutationBusy || workspaceOperations.has(kind) && !workspaceSupported} loading={savingOperation === kind} onClick={() => void queueOperation(kind, payload)}>{label}</Button>;
  const xrayTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Space wrap>{operationButton("读取", "xray_config_read")}<Button aria-label="使用最新结果" onClick={() => useLatestRaw("xray")} disabled={mutationBusy}>使用最新结果</Button>{operationButton("测试", "xray_test_config", { config: xray.configText })}<Button aria-label="写入" type="primary" htmlType="submit" form="xray-config-form" disabled={!selectedId || mutationBusy} loading={savingOperation === "xray_config_write"}>写入</Button><Button aria-label="接管外部 Xray" disabled={!selectedId || mutationBusy || takeoverBusy} onClick={() => void runTakeover()}>接管外部 Xray</Button></Space>
    <Card size="small" title="配置快照" extra={<Button aria-label="刷新 Xray 快照" icon={<ReloadOutlined />} loading={snapshotsLoading} onClick={() => void refreshSnapshots()} />}>
      <Typography.Paragraph type="secondary">{currentSnapshot ? `${shortHash(currentSnapshot.config_hash)}（当前）` : "尚无已保存的 Xray 配置"}</Typography.Paragraph>
      {pendingSnapshot && <Alert type="warning" showIcon title="待恢复" description={<Space orientation="vertical"><Typography.Text>Agent {shortHash(pendingSnapshot.config_hash)} / 当前 {currentSnapshot ? shortHash(currentSnapshot.config_hash) : "无"}</Typography.Text><Space wrap><Button aria-label="接受" disabled={mutationBusy} loading={actionKey === `accept:${pendingSnapshot.id}`} onClick={() => void mutate(`accept:${pendingSnapshot.id}`, async (serverId, current) => { const response = await acceptXrayConfigPendingRecovery(serverId); if (current()) setSnapshots(response.snapshots); return `已将 ${shortHash(response.current.config_hash)} 接受为当前配置。`; })}>接受</Button><Button aria-label="应用当前配置" disabled={mutationBusy || !currentSnapshot} loading={actionKey === "recovery"} onClick={() => void mutate("recovery", async (serverId) => { const response = await applyXrayConfigRecovery(serverId, { restart_xray: true, merge_agent_only: true, command_timeout_ms: 60000 }); return `已根据 ${shortHash(response.snapshot.config_hash)} 排队 ${response.command_count} 条恢复命令。${response.merged_agent_only_count ? `已合并 ${response.merged_agent_only_count} 个仅存在于 Agent 的条目。` : ""}${response.warnings.length ? `警告：${response.warnings.map(remarkLabel).join("，")}。` : ""}`; })}>应用当前配置</Button></Space></Space>} />}
      <Table<XrayConfigSnapshot> rowKey="id" dataSource={snapshots} loading={snapshotsLoading} pagination={false} locale={{ emptyText: "暂无快照。" }} scroll={{ x: 570 }} columns={[
        { title: "快照", key: "hash", render: (_, snapshot) => <Space orientation="vertical" size={0}><Tag color={snapshot.status === "current" ? "success" : snapshot.status === "pending_recovery" ? "warning" : "default"}>{statusLabel(snapshot.status)}</Tag><Typography.Text code>{shortHash(snapshot.config_hash)}</Typography.Text></Space> },
        { title: "来源", key: "source", render: (_, snapshot) => ({ agent_report: "Agent", master_write: "控制台", manual_accept: "已接受" })[snapshot.source] }, { title: "大小", key: "size", render: (_, snapshot) => formatBytes(snapshot.size_bytes) }, { title: "创建时间", key: "created", render: (_, snapshot) => formatDate(snapshot.created_at) },
        { title: "操作", key: "actions", render: (_, snapshot) => <Space wrap><Button aria-label="载入" disabled={mutationBusy} onClick={() => void loadSnapshot(snapshot)}>载入</Button><Button aria-label="恢复" disabled={!selectedId || mutationBusy || snapshot.status === "pending_recovery"} loading={actionKey === `restore:${snapshot.id}`} onClick={() => void mutate(`restore:${snapshot.id}`, async (serverId) => { await restoreXrayConfigSnapshot(serverId, snapshot.id); })}>恢复</Button></Space> },
      ]} />
    </Card>
    <Form id="xray-config-form" layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("xray_config_write", { config: xray.configText, path: blankToNull(xray.path), force: xray.force })}><Form.Item label="路径"><Input aria-label="路径" value={xray.path} onChange={(event) => patchXray({ path: event.target.value })} /></Form.Item><Form.Item label="强制"><Switch aria-label="强制" checked={xray.force} onChange={(force) => patchXray({ force })} /></Form.Item><Form.Item label="Xray 配置"><Input.TextArea aria-label="Xray 配置" spellCheck={false} rows={16} value={xray.configText} onChange={(event) => patchXray({ configText: event.target.value })} /></Form.Item></Form>
  </Space>;
  const systemTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {(!workspaceSupported || systemState.reason) && <Alert type="warning" title={!workspaceSupported ? upgradeMessage : zhMessage(systemState.reason)} showIcon />}
    {systemReady && systemState.apiMode === "routed" ? <Alert type="info" title={`此服务器使用已验证的路由式 Xray gRPC API 配置。${systemState.grpcPortWritable ? "可更改其本机回环端口，但无法在此表单中关闭 API。" : `其端点由 Agent 的 stats_address 固定为 ${systemState.fixedStatsAddress}，且无法在此表单中关闭 API。`}`} showIcon /> : systemReady && systemState.fixedStatsAddress ? <Alert type="info" title={`Xray gRPC API 端点由 Agent 的 stats_address 固定为 ${systemState.fixedStatsAddress}。其他受支持的系统字段仍可编辑。`} showIcon /> : null}
    <Space wrap><Button aria-label="读取" disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_system_config_read"} onClick={readSystem}>读取</Button><Button aria-label="使用最新结果" disabled={mutationBusy} onClick={useLatestSystem}>使用最新结果</Button><Button aria-label="写入" type="primary" htmlType="submit" form="xray-system-config-form" disabled={!workspaceSupported || !systemWriteReady || mutationBusy} loading={savingOperation === "xray_system_config_write"}>写入</Button></Space>
    <Form id="xray-system-config-form" layout="vertical" disabled={!systemReady || mutationBusy} onFinish={() => void writeSystem()}>
      <Form.Item label="Xray 日志级别"><Select aria-label="Xray 日志级别" value={system.log_level} options={logLevels.map((value) => ({ value, label: ({ none: "无", error: "错误", warning: "警告", info: "信息", debug: "调试" })[value] }))} onChange={(log_level) => patchSystem({ log_level })} /></Form.Item>
      <Space wrap><Form.Item label="指标"><Switch aria-label="指标" checked={system.metrics_enabled} onChange={(metrics_enabled) => patchSystem({ metrics_enabled })} /></Form.Item><Form.Item label="统计"><Switch aria-label="统计" checked={system.stats_enabled} onChange={(stats_enabled) => patchSystem({ stats_enabled })} /></Form.Item><Form.Item label="Xray gRPC API"><Switch aria-label="Xray gRPC API" checked={system.grpc_enabled} disabled={!systemReady || mutationBusy || !systemState.grpcDisableSupported} onChange={(grpc_enabled) => patchSystem({ grpc_enabled })} /></Form.Item></Space>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item label="指标监听地址"><Input aria-label="指标监听地址" value={system.metrics_listen} onChange={(event) => patchSystem({ metrics_listen: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="Xray gRPC API 端口"><StrictInputNumber aria-label="Xray gRPC API 端口" aria-valuemin={1} aria-valuemax={65535} value={system.grpc_port} disabled={!systemReady || mutationBusy || !systemState.grpcPortWritable} onChange={(value) => patchSystem({ grpc_port: value ?? Number.NaN })} /></Form.Item></Col></Row>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item label="DNS JSON" validateStatus={dnsError ? "error" : undefined} help={dnsError || undefined}><Input.TextArea aria-label="DNS JSON" rows={10} spellCheck={false} value={dnsText} onChange={(event) => setDnsText(event.target.value)} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="策略 JSON" validateStatus={policyError ? "error" : undefined} help={policyError || undefined} extra="更改统计开关会规范化其计数器；否则将保留现有的统计、策略和 API 服务状态。"><Input.TextArea aria-label="策略 JSON" rows={10} spellCheck={false} value={policyText} onChange={(event) => setPolicyText(event.target.value)} /></Form.Item></Col></Row>
    </Form>
  </Space>;
  const deploymentForm = <Card size="small" title="部署隧道"><Form layout="vertical" disabled={runtimeBusy} onFinish={deployTunnel}>
    <Row gutter={16}>
      <Col xs={24} md={12}><Form.Item label="域名"><Input aria-label="域名" value={deploy.domain} onChange={(event) => patchDeploy({ domain: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="代理域名"><Input aria-label="代理域名" value={deploy.proxyDomain} onChange={(event) => patchDeploy({ proxyDomain: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="网站类型"><Select aria-label="网站类型" value={deploy.siteType} options={[{ value: "static", label: "静态网站" }, { value: "proxy", label: "反向代理" }]} onChange={(siteType) => patchDeploy({ siteType })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label={deploy.siteType === "proxy" ? "代理 URL" : "静态网站根目录"}><Input aria-label={deploy.siteType === "proxy" ? "代理 URL" : "静态网站根目录"} value={deploy.siteValue} onChange={(event) => patchDeploy({ siteValue: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="证书名称"><Input aria-label="证书名称" value={deploy.certName} onChange={(event) => patchDeploy({ certName: event.target.value })} /></Form.Item></Col>
    </Row>
    <Space wrap align="start"><Form.Item label="清理流式端口"><Switch aria-label="清理流式端口" checked={deploy.clearStreamPort} onChange={(clearStreamPort) => patchDeploy({ clearStreamPort })} /></Form.Item><Form.Item label="重启"><Switch aria-label="重启" checked={deploy.restartXray} onChange={(restartXray) => patchDeploy({ restartXray })} /></Form.Item><Checkbox checked={deploy.force} onChange={(event) => patchDeploy({ force: event.target.checked })}>强制</Checkbox></Space>
    <Collapse items={[{ key: "listeners", label: "监听配置", children: <><Form.Item label="监听地址"><Input aria-label="监听地址" value={deploy.listenAddress} onChange={(event) => patchDeploy({ listenAddress: event.target.value })} /></Form.Item><Row gutter={16}>{tunnelPortFields.map(({ key, label }) => <Col xs={24} sm={12} lg={8} key={key}><Form.Item label={label}><StrictInputNumber aria-label={label} aria-valuemin={1} aria-valuemax={65535} value={deploy[key]} onChange={(value) => patchDeploy({ [key]: value ?? Number.NaN })} /></Form.Item></Col>)}</Row>{portsInvalid && <Alert type="error" title="请填写监听地址，并为各项设置互不相同的 1–65535 端口" />}</> }]} />
    <Button aria-label="部署隧道" style={{ marginTop: 16 }} type="primary" htmlType="submit" disabled={deployDisabled} loading={actionKey === "deploy"}>部署隧道</Button>
  </Form></Card>;
  const chainForm = <Card size="small" title="链式隧道"><Form layout="vertical" disabled={runtimeBusy} onFinish={createChain}>
    <Row gutter={16}><Col xs={24} md={12}><Form.Item label="名称"><Input aria-label="名称" value={chain.label} onChange={(event) => patchChain({ label: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="逐跳服务器" extra="转发顺序以下方显示为准。请选择 2–16 台服务器。"><Select aria-label="逐跳服务器" mode="multiple" value={chain.serverIds} options={serverOptions} maxCount={16} onChange={(serverIds) => patchChain({ serverIds })} /></Form.Item></Col></Row>
    <Space orientation="vertical" style={{ width: "100%", marginBottom: 16 }}>{chain.serverIds.map((id, index) => <Space key={id} wrap><Typography.Text>{index + 1}. {servers.find((server) => server.id === id)?.name ?? id}</Typography.Text><Button icon={<ArrowUpOutlined />} aria-label={`上移第 ${index + 1} 跳`} disabled={runtimeBusy || index === 0} onClick={() => { const ids = [...chain.serverIds]; [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]]; patchChain({ serverIds: ids }); }} /><Button icon={<ArrowDownOutlined />} aria-label={`下移第 ${index + 1} 跳`} disabled={runtimeBusy || index === chain.serverIds.length - 1} onClick={() => { const ids = [...chain.serverIds]; [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]]; patchChain({ serverIds: ids }); }} /></Space>)}</Space>
    <Row gutter={16}><Col xs={24} md={8}><Form.Item label="入口端口" extra="0 表示自动选择可用端口"><StrictInputNumber aria-label="入口端口" aria-valuemin={0} aria-valuemax={65535} value={chain.entryPort} onChange={(value) => patchChain({ entryPort: value ?? Number.NaN })} /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="最终目标"><Input aria-label="最终目标" value={chain.targetAddress} onChange={(event) => patchChain({ targetAddress: event.target.value })} /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="目标端口"><StrictInputNumber aria-label="目标端口" aria-valuemin={1} aria-valuemax={65535} value={chain.targetPort} onChange={(value) => patchChain({ targetPort: value ?? Number.NaN })} /></Form.Item></Col></Row>
    <Button aria-label="创建链式隧道" type="primary" htmlType="submit" disabled={chainDisabled} loading={actionKey === "chain"}>创建链式隧道</Button>
  </Form></Card>;
  const runtimeTab = <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Card title="运行时清单" extra={<Space wrap><Tag color={!runtime.inventory?.has_scan ? "default" : runtime.inventory.xray_running ? "success" : "warning"}>{runtimeStatus}</Tag><Button aria-label="刷新运行时清单" icon={<ReloadOutlined />} loading={runtimeLoading} onClick={() => void refreshRuntime(true)} /></Space>}>
      <Typography.Paragraph type="secondary">{runtimeSummary}</Typography.Paragraph>
      <Space wrap style={{ marginBottom: 16 }}><Button aria-label="导入缺失节点" disabled={runtimeBusy || !runtime.inventory?.has_scan || !missingNodeCount} loading={actionKey === "import"} onClick={() => void mutate("import", async (serverId) => { const response = await importManagedNodesFromRuntimeInbounds(serverId); return `已导入 ${response.created_count} 个节点，${response.existing_count} 个已受管理，已跳过 ${response.skipped_count} 个。`; })}>导入缺失节点</Button><Button aria-label="补齐客户端" disabled={runtimeBusy || !runtime.inventory?.has_scan || !runtime.credentials?.missing_runtime_client_count} loading={actionKey === "repair"} onClick={() => repairCredentials()}>补齐客户端</Button><Button aria-label="清理多余客户端" disabled={runtimeBusy || !runtime.inventory?.has_scan || !runtime.credentials?.extra_runtime_client_count} loading={actionKey === "cleanup"} onClick={() => repairCredentials(true)}>清理多余客户端</Button></Space>
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: "inbounds", label: "入站", children: runtime.inventory?.inbound_count ?? 0 }, { key: "clients", label: "客户端", children: runtime.inventory?.client_count ?? 0 }, { key: "tunnels", label: "隧道 / 链式隧道", children: `${runtime.tunnels?.tunnel_count ?? 0} / ${runtime.tunnels?.chain_count ?? 0}` },
        { key: "traffic", label: "入站流量", children: formatTraffic(runtime.inventory?.traffic) }, { key: "user-traffic", label: "用户流量", children: formatTraffic(runtime.inventory?.user_traffic) },
        { key: "managed", label: "受管理 / 未受管理", children: `${runtime.nodes?.managed_runtime_count ?? 0} / ${runtime.nodes?.unmanaged_runtime_count ?? 0}` }, { key: "stale", label: "过时 / 缺失", children: `${runtime.nodes?.stale_count ?? 0} / ${runtime.nodes?.missing_runtime_count ?? 0}` },
        { key: "credentials", label: "凭据不一致", children: `${runtime.credentials?.out_of_sync_count ?? 0} 个不一致 / ${runtime.credentials?.missing_runtime_client_count ?? 0} 个缺失 / ${runtime.credentials?.extra_runtime_client_count ?? 0} 个多余` },
      ]} />
      <Space wrap>{runtime.inventory?.config_modified && <Tag color="warning">配置已修复</Tag>}{Object.entries(runtime.inventory?.protocol_counts ?? {}).sort(([left], [right]) => left.localeCompare(right)).map(([protocol, count]) => <Tag key={protocol}>{protocol}: {count}</Tag>)}</Space>
      {(runtime.tunnels?.warnings ?? []).map((warning) => <Alert key={warning} type="warning" title={remarkLabel(warning)} showIcon />)}
      {runtime.inventory?.message && <Typography.Paragraph>{zhMessage(runtime.inventory.message)}</Typography.Paragraph>}
    </Card>
    {deploymentForm}{chainForm}
    <Card size="small" title="运行时入站"><Table<XrayRuntimeInbound> rowKey="source_index" dataSource={runtime.inventory?.inbounds ?? []} loading={runtimeLoading} scroll={{ x: 850 }} locale={{ emptyText: runtime.inventory?.has_scan ? "暂无运行时入站。" : "暂无扫描清单。" }} columns={[
      { title: "入站", key: "inbound", render: (_, inbound) => <Space orientation="vertical" size={0}><Typography.Text strong>{inbound.display_name}</Typography.Text><Typography.Text>{[inbound.protocol, inbound.network, inbound.security, inbound.client_container].filter(Boolean).join(" / ") || "无元数据"}</Typography.Text><Typography.Text>{[inbound.listen, inbound.port ? String(inbound.port) : ""].filter(Boolean).join(":") || "无端点"}</Typography.Text><Typography.Text>{inbound.client_count} 个客户端</Typography.Text><Typography.Text>{inbound.user_emails.join(", ")}</Typography.Text></Space> },
      { title: "状态 / 嗅探", key: "status", render: (_, inbound) => { const entry = entryByIndex.get(inbound.source_index); return <Space orientation="vertical" size={0}><Tag color={entry?.status === "managed" ? "success" : entry?.status === "unavailable" ? "error" : "warning"}>{entry ? statusLabel(entry.status) : "未知"}</Tag>{entry?.managed_node_name && <Typography.Text>{entry.managed_node_name}</Typography.Text>}<Typography.Text>嗅探：{inbound.sniffing_enabled ? "开启" : "关闭"}</Typography.Text>{inbound.sniffing_dest_override.length > 0 && <Typography.Text>目标覆盖：{inbound.sniffing_dest_override.join(", ")}</Typography.Text>}{inbound.sniffing_exclude_domains.length > 0 && <Typography.Text>排除域名：{inbound.sniffing_exclude_domains.join(", ")}</Typography.Text>}</Space>; } },
      { title: "流量", key: "traffic", render: (_, inbound) => <Space orientation="vertical" size={0}><Typography.Text>{formatTraffic(inbound.traffic)}</Typography.Text><Typography.Text>用户 {formatTraffic(inbound.user_traffic)}</Typography.Text></Space> },
      { title: "节点目录", key: "catalog", render: (_, inbound) => { const draft = draftByIndex.get(inbound.source_index); return <Space orientation="vertical"><Button disabled={runtimeBusy || !draft?.create_available || Boolean(draft.existing_node_id)} loading={actionKey === `create:${inbound.source_index}`} onClick={() => void mutate(`create:${inbound.source_index}`, async (serverId) => { const response = await createManagedNodeFromRuntimeInbound(serverId, { source_index: inbound.source_index }); return `已创建受管理节点 ${response.node.name}。`; })}>{draft?.existing_node_id ? "节点已存在" : "创建节点"}</Button>{(draft?.warnings ?? inbound.remarks).map((warning) => <Tag key={warning} color="warning">{remarkLabel(warning)}</Tag>)}</Space>; } },
    ]} /></Card>
    <Card size="small" title="受管理节点核对"><Table<XrayRuntimeNodeReconciliationManagedEntry> rowKey="node_id" dataSource={nodeIssues} pagination={false} locale={{ emptyText: "受管理节点无差异。" }} scroll={{ x: 650 }} columns={[
      { title: "节点", key: "node", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text strong>{entry.node_name}</Typography.Text><Typography.Text>{entry.protocol} / {entry.inbound_tag || "无入站标签"}</Typography.Text><Typography.Text>{entry.runtime_display_name || "无运行时入站"}</Typography.Text></Space> },
      { title: "状态", key: "status", render: (_, entry) => <Tag color={entry.status === "missing_runtime" ? "error" : "warning"}>{statusLabel(entry.status)}</Tag> },
      { title: "差异", key: "drifts", render: (_, entry) => <Space orientation="vertical" size={0}>{entry.drifts.map((drift) => <Typography.Text key={drift.field}>{drift.field}: {Array.isArray(drift.managed_value) ? drift.managed_value.join(",") : String(drift.managed_value ?? "-")} → {Array.isArray(drift.runtime_value) ? drift.runtime_value.join(",") : String(drift.runtime_value ?? "-")}</Typography.Text>)}</Space> },
      { title: "操作", key: "actions", render: (_, entry) => <Button aria-label="同步" disabled={runtimeBusy || entry.status !== "stale" || entry.runtime_source_index == null} loading={actionKey === `sync:${entry.node_id}`} onClick={() => void mutate(`sync:${entry.node_id}`, async (serverId) => { const response = await syncManagedNodeFromRuntime(serverId, entry.node_id, { source_index: entry.runtime_source_index }); return `已同步 ${response.node.name} 的 ${response.updated_fields.length} 个字段。`; })}>同步</Button> },
    ]} /></Card>
    <Card size="small" title="凭据核对"><Table<XrayRuntimeCredentialReconciliationEntry> rowKey="node_id" dataSource={credentialIssues} pagination={false} locale={{ emptyText: "凭据无差异。" }} scroll={{ x: 650 }} columns={[
      { title: "节点", key: "node", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text strong>{entry.node_name}</Typography.Text><Typography.Text>{entry.protocol} / {entry.inbound_tag || "无入站标签"}</Typography.Text><Typography.Text>{entry.runtime_display_name || "无运行时入站"}</Typography.Text></Space> },
      { title: "状态", key: "status", render: (_, entry) => <Tag color={entry.status === "missing_runtime" ? "error" : "warning"}>{statusLabel(entry.status)}</Tag> },
      { title: "客户端", key: "clients", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text>预期 {entry.expected_emails.length} 个 / 运行时 {entry.runtime_emails.length} 个</Typography.Text>{entry.missing_runtime_emails.length > 0 && <Typography.Text>缺失：{entry.missing_runtime_emails.join(", ")}</Typography.Text>}{entry.extra_runtime_emails.length > 0 && <Typography.Text>多余：{entry.extra_runtime_emails.join(", ")}</Typography.Text>}</Space> },
    ]} /></Card>
    <Card size="small" title="运行时隧道">
      {!runtime.tunnels?.tunnels.length && !runtime.tunnels?.chains.length && <Empty description="暂无运行时隧道。" />}
      {Boolean(runtime.tunnels?.tunnels.length) && <Table<XrayRuntimeTunnel> rowKey={tunnelKey} dataSource={runtime.tunnels?.tunnels} pagination={false} scroll={{ x: 650 }} columns={[
        { title: "隧道", key: "tunnel", render: (_, tunnel) => <Space orientation="vertical" size={0}><Typography.Text strong>{tunnel.tag}</Typography.Text><Tag color={tunnel.kind === "routed" ? "processing" : "default"}>{zhStatus(tunnel.kind)}</Tag><Typography.Text>{tunnel.listen_port == null ? "无监听端口" : `:${tunnel.listen_port}`} → {tunnelTarget(tunnel.target_address, tunnel.target_port)}</Typography.Text>{tunnel.kind === "routed" && <Typography.Text>{tunnel.inbound_tag ? `来自 ${tunnel.inbound_tag}` : "无入站规则来源"} / 规则 {tunnel.rule_index ?? "-"}</Typography.Text>}<Typography.Text>{tunnel.network}</Typography.Text><Typography.Text>{[...tunnel.match_domains, ...tunnel.match_ips].join(", ")}</Typography.Text></Space> },
        { title: "操作", key: "actions", render: (_, tunnel) => <Button aria-label="删除" danger disabled={runtimeBusy} loading={actionKey === `delete:${tunnelKey(tunnel)}`} onClick={() => deleteTunnel(tunnel)}>删除</Button> },
      ]} />}
      {(runtime.tunnels?.chains ?? []).map((item) => <Card key={item.label} size="small" title={item.label} extra={<Button aria-label="删除链式隧道" danger disabled={runtimeBusy} loading={actionKey === `delete:chain:${item.label}`} onClick={() => deleteTunnel(item)}>删除链式隧道</Button>} style={{ marginTop: 16 }}><Typography.Paragraph>{item.entry_port == null ? "无入口端口" : `:${item.entry_port}`} → {item.final_target || "无目标"}</Typography.Paragraph>{item.hops.map((hop, index) => <Typography.Paragraph key={`${hop.tag}:${index}`}>{index + 1}. {hop.tag} / {hop.listen_port == null ? "无监听端口" : `:${hop.listen_port}`} → {tunnelTarget(hop.target_address, hop.target_port)}</Typography.Paragraph>)}</Card>)}
    </Card>
    <Card size="small" title="运行时操作"><Space wrap style={{ marginBottom: 16 }}>{operationButton("入站", "inbounds_list")}{operationButton("出站", "outbounds_list")}{operationButton("路由", "routing_read")}</Space><Form layout="vertical" disabled={mutationBusy} onFinish={() => queueJson(runtimeOperation, runtimePayload)}><Form.Item label="操作"><Select aria-label="操作" value={runtimeOperation} options={runtimeOperations} onChange={setRuntimeOperation} /></Form.Item><Form.Item label="请求内容"><Input.TextArea aria-label="请求内容" rows={12} spellCheck={false} value={runtimePayload} onChange={(event) => setRuntimePayload(event.target.value)} /></Form.Item><Button aria-label="排队执行" type="primary" htmlType="submit" loading={savingOperation === runtimeOperation} disabled={!selectedId || mutationBusy}>排队执行</Button></Form></Card>
  </Space>;
  const nginxTab = <Form layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("nginx_config_write", { config: nginx.configText, path: blankToNull(nginx.path) })}><Space wrap style={{ marginBottom: 16 }}>{operationButton("读取", "nginx_config_read")}<Button aria-label="使用最新结果" onClick={() => useLatestRaw("nginx")}>使用最新结果</Button><Button aria-label="写入" type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === "nginx_config_write"}>写入</Button></Space><Form.Item label="路径"><Input aria-label="路径" value={nginx.path} onChange={(event) => patchNginx({ path: event.target.value })} /></Form.Item><Form.Item label="Nginx 配置"><Input.TextArea aria-label="Nginx 配置" rows={16} spellCheck={false} value={nginx.configText} onChange={(event) => patchNginx({ configText: event.target.value })} /></Form.Item></Form>;
  const sitesTab = <Form layout="vertical" disabled={mutationBusy} onFinish={() => queueJson(siteOperation, sitePayload)}><Space wrap style={{ marginBottom: 16 }}>{operationButton("服务器", "nginx_servers_list")}{operationButton("网站", "nginx_websites_list")}</Space><Form.Item label="操作"><Select aria-label="操作" value={siteOperation} options={siteOperations} onChange={setSiteOperation} /></Form.Item><Form.Item label="请求内容"><Input.TextArea aria-label="请求内容" rows={12} spellCheck={false} value={sitePayload} onChange={(event) => setSitePayload(event.target.value)} /></Form.Item><Button aria-label="排队执行" type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === siteOperation}>排队执行</Button></Form>;
  const filesTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {(!workspaceSupported || fileState.reason) && <Alert type="warning" title={!workspaceSupported ? upgradeMessage : zhMessage(fileState.reason)} showIcon />}
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}><Card size="small" title="Xray 文件"><Form layout="vertical" disabled={mutationBusy} onFinish={() => void writeFile()}><Space wrap style={{ marginBottom: 16 }}><Button aria-label="列出文件" disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_config_files_list"} onClick={() => readFile(true)}>列出文件</Button><Button aria-label="读取" disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_config_file_read"} onClick={() => readFile()}>读取</Button><Button aria-label="使用最新结果" onClick={useLatestFile}>使用最新结果</Button><Button aria-label="写入" type="primary" htmlType="submit" disabled={!workspaceSupported || !fileWriteReady || mutationBusy} loading={savingOperation === "xray_config_file_write"}>写入</Button></Space><Form.Item label="文件"><Input aria-label="文件" value={file.file} onChange={(event) => { const filename = event.target.value; patchFile({ file: filename }); if (fileState.revision?.file !== filename.trim()) invalidateFile("编辑前请先读取此 Xray 文件。"); }} /></Form.Item><Form.Item label="内容"><Input.TextArea aria-label="内容" rows={12} spellCheck={false} value={file.content} disabled={!fileWriteReady || mutationBusy} placeholder={fileWriteReady ? undefined : "请先读取此文件并使用最新结果，再进行编辑。"} onChange={(event) => patchFile({ content: event.target.value })} /></Form.Item></Form></Card></Col>
      <Col xs={24} lg={12}><Card size="small" title="Nginx 文件"><Form layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("nginx_config_file_write", { path: nginxFile.path.trim(), content: nginxFile.content })}><Space wrap style={{ marginBottom: 16 }}>{operationButton("列出文件", "nginx_config_files_list")}{operationButton("读取", "nginx_config_file_read", { file: nginxFile.file.trim() })}<Button aria-label="使用最新结果" onClick={() => useLatestRaw("nginx-file")}>使用最新结果</Button><Button aria-label="写入" type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === "nginx_config_file_write"}>写入</Button></Space><Form.Item label="读取路径"><Input aria-label="读取路径" value={nginxFile.file} onChange={(event) => patchNginxFile({ file: event.target.value })} /></Form.Item><Form.Item label="写入路径"><Input aria-label="写入路径" value={nginxFile.path} onChange={(event) => patchNginxFile({ path: event.target.value })} /></Form.Item><Form.Item label="内容"><Input.TextArea aria-label="内容" rows={12} spellCheck={false} value={nginxFile.content} onChange={(event) => patchNginxFile({ content: event.target.value })} /></Form.Item></Form></Card></Col>
    </Row>
  </Space>;
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>配置工作区</Typography.Title><Button icon={<ReloadOutlined />} aria-label="刷新配置命令" loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={zhMessage(error)} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Card title="工作区"><Typography.Paragraph type="secondary">MMW Agent 子接口配置操作</Typography.Paragraph><Form layout="vertical"><Form.Item label="目标服务器"><Select aria-label="目标服务器" value={selectedId || undefined} options={serverOptions} disabled={!serverOptions.length} onChange={(id) => selectServer(id)} /></Form.Item></Form><Tabs activeKey={activeTab} onChange={setActiveTab} items={[
      { key: "xray", label: "Xray", children: xrayTab }, { key: "system", label: "系统", children: systemTab }, { key: "runtime", label: "运行时", children: runtimeTab },
      { key: "limits", label: "限制", children: activeTab === "limits" ? <LimiterPanel key={selectedId} serverId={selectedId} inbounds={runtime.inventory?.inbounds ?? []} onCommands={receiveCommands} /> : null },
      { key: "online", label: "在线用户", children: activeTab === "online" ? <OnlineUsersPanel key={selectedId} serverId={selectedId} /> : null },
      { key: "nginx", label: "Nginx", children: nginxTab }, { key: "sites", label: "网站", children: sitesTab }, { key: "files", label: "文件", children: filesTab },
    ]} /></Card>
    <Card title="命令结果" extra={<Button icon={<ReloadOutlined />} aria-label="刷新命令结果" onClick={() => void refreshCommands()} />}><Typography.Paragraph type="secondary">所选服务器的历史记录</Typography.Paragraph><CommandInspector commands={selectedCommands} streamFramesByCommand={frames} emptyText="暂无配置命令。" /></Card>
    <Modal title="Xray 接管" open={takeoverOpen} onCancel={closeTakeover} closeIcon={<CloseOutlined aria-label="关闭接管窗口" />} width={640} styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }} footer={<Space><Button aria-label="刷新" disabled={takeoverBusy} onClick={() => void runTakeover()}>刷新</Button><Button aria-label="接管" type="primary" disabled={!takeoverConfirmed || !takeoverPreview || takeoverBusy} onClick={() => void runTakeover(true)}>接管</Button></Space>}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}><Typography.Text strong>{selectedServer?.name}</Typography.Text>{takeoverBusy && <Spin description="等待 Agent 命令完成"><div style={{ minHeight: 32 }} /></Spin>}{takeoverError && <Alert type="error" title={zhMessage(takeoverError)} showIcon />}{takeoverPreview && <><Descriptions column={1} items={[{ key: "target", label: "目标文件", children: takeoverPreview.target }, { key: "runtime", label: "运行时", children: takeoverPreview.running ? "运行中" : "已停止" }, { key: "checksum", label: "源文件校验和", children: <Typography.Text code style={{ overflowWrap: "anywhere" }}>{takeoverPreview.sha256}</Typography.Text> }]} /><Typography.Title level={5}>{takeoverPreview.files.length} 个源文件</Typography.Title><ul>{takeoverPreview.files.map((file) => <li key={file} style={{ overflowWrap: "anywhere" }}>{file}</li>)}</ul><Checkbox disabled={takeoverBusy} checked={takeoverConfirmed} onChange={(event) => setTakeoverConfirmed(event.target.checked)}>替换源配置片段，并在 Xray 正在运行时重启</Checkbox></>}</Space>
    </Modal>
  </Space>;
}
