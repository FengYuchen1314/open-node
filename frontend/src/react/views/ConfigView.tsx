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
import StrictInputNumber from "../components/StrictInputNumber";

export interface ConfigViewProps {
  onCommands?: (serverId: string, commands: AgentCommand[]) => void;
  onUpdated?: () => void;
}
type Revision = { serverId: string; sha256: string };
type SystemState = { revision: Revision | null; writable: boolean; reason: string; apiMode: string; grpcDisableSupported: boolean; grpcPortWritable: boolean; fixedStatsAddress: string };
type FileState = { revision: (Revision & { file: string }) | null; writable: boolean; reason: string };
type RuntimeState = { inventory: XrayRuntimeInventoryResponse | null; tunnels: XrayRuntimeTunnelInventoryResponse | null; drafts: XrayRuntimeNodeDraft[]; nodes: XrayRuntimeNodeReconciliationResponse | null; credentials: XrayRuntimeCredentialReconciliationResponse | null };
const emptyRuntime: RuntimeState = { inventory: null, tunnels: null, drafts: [], nodes: null, credentials: null };
const initialSystemState: SystemState = { revision: null, writable: false, reason: "Read the current Xray system configuration first.", apiMode: "unknown", grpcDisableSupported: false, grpcPortWritable: false, fixedStatsAddress: "" };
const initialFileState: FileState = { revision: null, writable: false, reason: "Read the exact Xray file before editing it." };
const logLevels: AgentXraySystemConfigOperationRequest["log_level"][] = ["none", "error", "warning", "info", "debug"];
const workspaceOperations = new Set<AgentOperationKind>(["xray_system_config_read", "xray_system_config_write", "xray_config_files_list", "xray_config_file_read", "xray_config_file_write"]);
const runtimeOperations: { label: string; value: AgentOperationKind }[] = [{ label: "Manage inbounds", value: "inbounds_manage" }, { label: "Manage outbounds", value: "outbounds_manage" }, { label: "Manage routing", value: "routing_manage" }, { label: "Batch apply", value: "batch_apply" }, { label: "Limiter", value: "limiter" }, { label: "Return route test", value: "return_route_test" }];
const siteOperations: { label: string; value: AgentOperationKind }[] = [{ label: "Setup SSL", value: "nginx_setup_ssl" }, { label: "Delete website", value: "nginx_website_delete" }, { label: "Deploy cert", value: "cert_deploy" }, { label: "Validate site", value: "validate_site" }];
const tunnelPortFields = [{ key: "listenPort", label: "Public port" }, { key: "nginxPort", label: "Nginx port" }, { key: "forwardPort", label: "Fallback port" }, { key: "apiPort", label: "Xray API port" }, { key: "metricsPort", label: "Metrics port" }] as const;
const queueAndScan = { queue_agent_commands: true, queue_scan_after_apply: true };
const asRecord = (value: unknown): Record<string, unknown> | null => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const readableError = (error: unknown) => error instanceof Error ? error.message : "Request failed.";
const blankToNull = (value: string) => value.trim() || null;
const remarkLabel = (value: string) => value.replaceAll("_", " ");
const statusLabel = (value: string) => ({ missing_runtime_clients: "Missing clients", extra_runtime_clients: "Extra clients", drift: "Client drift", pending_recovery: "Pending" } as Record<string, string>)[value] ?? (remarkLabel(value).charAt(0).toUpperCase() + remarkLabel(value).slice(1));
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
function formatTraffic(traffic?: { uplink: number; downlink: number }) { return `Up ${formatBytes(traffic?.uplink ?? 0)} / Down ${formatBytes(traffic?.downlink ?? 0)}`; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date); }
function tunnelTarget(address?: string | null, port?: number | null) {
  if (!address) return port == null ? "No target" : String(port);
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
  const upgradeMessage = !selectedId ? "Select a server to manage its Xray configuration." : !agent ? "Install and connect an upgraded Open Node Agent before using Xray system configuration or config files." : `Upgrade Open Node Agent ${agent.agent_version ?? "(version unknown)"} on this server; it does not advertise the xray_config_workspace capability.`;
  const selectedCommands = commands[selectedId] ?? [];
  const currentSnapshot = snapshots.find((snapshot) => snapshot.status === "current");
  const pendingSnapshot = snapshots.find((snapshot) => snapshot.status === "pending_recovery");
  const systemReady = systemState.writable && systemState.revision?.serverId === selectedId;
  const dnsError = jsonError(dnsText, "DNS"), policyError = jsonError(policyText, "Policy");
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
  const runtimeStatus = !runtime.inventory?.has_scan ? "No scan" : runtime.inventory.xray_running ? "Running" : "Stopped";
  const runtimeSummary = !runtime.inventory?.has_scan ? "No runtime scan reported" : [runtime.inventory.xray_version, runtime.inventory.api_port ? `api ${runtime.inventory.api_port}` : "", runtime.inventory.updated_at ? formatDate(runtime.inventory.updated_at) : ""].filter(Boolean).join(" / ") || runtime.inventory.message || "Runtime scan ready";

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
    if (!serverId) { setError("Target server is required."); return false; }
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
    if (!serverId) { setError("Target server is required."); return; }
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
  function readSystem() { invalidateSystem("Wait for the read command, then use its latest result."); void queueOperation("xray_system_config_read"); }
  function readFile(list = false) { invalidateFile(`Wait for the ${list ? "list" : "read"} command, then use its latest result.`); void queueOperation(list ? "xray_config_files_list" : "xray_config_file_read", list ? undefined : { file: file.file.trim() }); }
  async function writeSystem() {
    if (!systemReady || !systemState.revision) { setError(systemState.reason); return; }
    if (!validPort(system.grpc_port)) { setError("Xray gRPC API port must be an integer from 1 to 65535."); return; }
    try {
      const dns = parseJsonObjectText(dnsText, "DNS"), policy = parseJsonObjectText(policyText, "Policy");
      if (await queueOperation("xray_system_config_write", { ...system, dns, policy, expected_sha256: systemState.revision.sha256 })) invalidateSystem("Read the current Xray system configuration again before another write.");
    } catch (failure) { setError(readableError(failure)); }
  }
  async function writeFile() {
    if (!fileWriteReady || !fileState.revision) { setError(fileState.reason); return; }
    if (await queueOperation("xray_config_file_write", { file: file.file.trim(), content: file.content, expected_sha256: fileState.revision.sha256 })) invalidateFile("Read the file again before another write.");
  }
  function latestResult(path: string, content = false) {
    return asRecord(selectedCommands.find((command) => command.path === path && (content ? command.method === "GET" && typeof asRecord(command.result_body)?.content === "string" : command.result_body != null))?.result_body);
  }
  function useLatestRaw(kind: "xray" | "nginx" | "nginx-file") {
    const body = latestResult(kind === "xray" ? "/api/child/xray/config" : kind === "nginx" ? "/api/child/nginx/config" : "/api/child/nginx/config-files", kind === "nginx-file");
    const content = kind === "nginx-file" ? body?.content : body?.config;
    if (typeof content !== "string") { setError(`No completed ${kind === "xray" ? "Xray config" : kind === "nginx" ? "Nginx config" : "Nginx file"} result.`); return; }
    if (kind === "xray") patchXray({ configText: content, ...(typeof body?.path === "string" ? { path: body.path } : {}) });
    else if (kind === "nginx") patchNginx({ configText: content, ...(typeof body?.path === "string" ? { path: body.path } : {}) });
    else patchNginxFile({ content, ...(typeof body?.path === "string" ? { file: body.path, path: body.path } : {}) });
  }
  function useLatestSystem() {
    const body = latestSuccessfulGetResult(selectedCommands, "/api/child/xray/system-config")?.body;
    const config = asRecord(body?.config), sha256 = body?.sha256;
    if (!config || typeof sha256 !== "string" || !/^[0-9a-f]{64}$/.test(sha256)) { invalidateSystem("No complete Xray system configuration read is available."); setError("No completed Xray system config result."); return; }
    const dns = asRecord(config.dns), policy = asRecord(config.policy), logLevel = config.log_level;
    if (typeof logLevel !== "string" || !logLevels.includes(logLevel as typeof system.log_level) || !dns || !policy) { const message = "The Xray system read did not include valid log, DNS and policy objects."; invalidateSystem(message); setError(message); return; }
    setSystem((value) => ({ ...value, log_level: logLevel as typeof system.log_level,
      ...(typeof config.metrics_enabled === "boolean" ? { metrics_enabled: config.metrics_enabled } : {}),
      ...(typeof config.metrics_listen === "string" ? { metrics_listen: config.metrics_listen } : {}),
      ...(typeof config.stats_enabled === "boolean" ? { stats_enabled: config.stats_enabled } : {}),
      ...(typeof config.grpc_enabled === "boolean" ? { grpc_enabled: config.grpc_enabled } : {}),
      ...(typeof config.grpc_port === "number" ? { grpc_port: config.grpc_port } : {}),
    }));
    setDnsText(`${JSON.stringify(dns, null, 2)}\n`); setPolicyText(`${JSON.stringify(policy, null, 2)}\n`);
    setSystemState({ revision: config.writable === true ? { serverId: selectedId, sha256 } : null, writable: config.writable === true,
      reason: typeof config.read_only_reason === "string" && config.read_only_reason ? config.read_only_reason : config.writable === true ? "" : "This Xray system configuration shape is read-only in the form.",
      apiMode: typeof config.api_mode === "string" ? config.api_mode : "unknown", grpcDisableSupported: config.grpc_disable_supported === true, grpcPortWritable: config.grpc_port_writable === true, fixedStatsAddress: typeof config.fixed_stats_address === "string" ? config.fixed_stats_address : "",
    });
  }
  function useLatestFile() {
    const body = latestSuccessfulGetResult(selectedCommands, "/api/child/xray/config-files")?.body;
    if (!body) { invalidateFile("No successful Xray file read or list is available."); setError("No completed Xray file read or list result."); return; }
    if (typeof body.content !== "string") {
      const main = asRecord(body.files)?.main, entry = Array.isArray(main) ? asRecord(main[0]) : null;
      if (typeof entry?.name === "string") { patchFile({ file: entry.name }); invalidateFile(typeof entry.read_only_reason === "string" && entry.read_only_reason ? entry.read_only_reason : "The latest result is a file list. Read the primary file before editing it."); return; }
      invalidateFile("The latest Xray config-files result is incomplete."); setError("No completed Xray file read or list result."); return;
    }
    const filename = typeof body.path === "string" ? body.path.split(/[\\/]/).pop() ?? file.file : file.file;
    patchFile({ file: filename, content: body.content });
    if (!isWritableXrayFileResult(body, filename)) { invalidateFile(typeof body.read_only_reason === "string" && body.read_only_reason ? body.read_only_reason : "This Xray primary file is read-only."); return; }
    if (typeof body.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(body.sha256)) { const message = "The Xray file read did not include a valid revision."; invalidateFile(message); setError(message); return; }
    setFileState({ writable: true, reason: "", revision: { serverId: selectedId, file: filename, sha256: body.sha256 } });
  }
  function queueJson(kind: AgentOperationKind, text: string) {
    try { void queueOperation(kind, text.trim() ? parseJsonObjectText(text, "Payload") : undefined); }
    catch (failure) { setError(readableError(failure)); }
  }
  async function loadSnapshot(snapshot: XrayConfigSnapshot) {
    const serverId = selectedRef.current, generation = epoch.current;
    const loaded = typeof snapshot.config === "string" ? snapshot : (await refreshSnapshots(true)).find((item) => item.id === snapshot.id);
    if (!currentContext(serverId, generation)) return;
    if (!loaded?.config) { setError("Snapshot config is unavailable."); return; }
    patchXray({ configText: loaded.config }); setActiveTab("xray");
  }
  async function waitTakeover(serverId: string, id: string, request: number, generation: number) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      if (!currentContext(serverId, generation) || request !== requests.current.takeover) return null;
      const response = await listServerCommands(serverId);
      if (!currentContext(serverId, generation) || request !== requests.current.takeover) return null;
      receiveCommands(serverId, response.commands);
      const command = response.commands.find((item) => item.id === id);
      if (command?.status === "failed" || command?.status === "skipped") throw new Error(command.result_error || "Xray takeover command failed.");
      if (command?.status === "succeeded") { const body = asRecord(command.result_body); if (!body) throw new Error("Xray takeover returned an invalid result."); return body; }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error("Xray takeover command is still pending. Check command history.");
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
        if (result.success !== true) throw new Error("Xray takeover did not complete.");
        closeTakeover(); setSuccess(result.unchanged ? "Xray is already consolidated." : "Xray takeover completed.");
        callbacks.current.onUpdated?.(); await Promise.all([refreshSnapshots(), refreshRuntime()]);
      } else {
        if (result.preview !== true || typeof result.config_path !== "string" || typeof result.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(result.source_sha256) || !Array.isArray(result.source_files) || !result.source_files.every((item) => typeof item === "string")) throw new Error("This Agent did not return a supported takeover preview.");
        setTakeoverPreview({ target: result.config_path, files: result.source_files, sha256: result.source_sha256, running: result.running === true });
      }
    } catch (failure) { if (current()) setTakeoverError(readableError(failure)); }
    finally { if (current()) setTakeoverBusy(false); }
  }
  function deployTunnel() {
    if (deployDisabled) return;
    void mutate("deploy", async (serverId) => {
      const response = await deployXrayRuntimeTunnel(serverId, { domain: deploy.domain.trim(), proxy_domain: blankToNull(deploy.proxyDomain), site_type: deploy.siteType, site_value: blankToNull(deploy.siteValue), listen_address: deploy.listenAddress.trim(), listen_port: deploy.listenPort, nginx_port: deploy.nginxPort, forward_port: deploy.forwardPort, api_port: deploy.apiPort, metrics_port: deploy.metricsPort, cert_name: blankToNull(deploy.certName), clear_stream_port: deploy.clearStreamPort, restart_xray: deploy.restartXray, force: deploy.force, ...queueAndScan });
      return `Queued ${response.commands.length} tunnel deploy commands for ${response.domain}.${response.scan_command ? " Follow-up scan queued." : ""}${response.warnings.length ? ` Warnings: ${response.warnings.map(remarkLabel).join(", ")}.` : ""}`;
    });
  }
  function createChain() {
    if (chainDisabled) return;
    void mutate("chain", async () => {
      const response = await createXrayRuntimeTunnelChain({ label: chain.label.trim(), server_ids: [...new Set(chain.serverIds)], entry_port: chain.entryPort, target_address: chain.targetAddress.trim(), target_port: chain.targetPort, ...queueAndScan });
      return `Queued ${response.commands.length} chain hop commands for ${response.label} (${tunnelTarget(response.entry_host, response.entry_port)} -> ${response.final_target}).${response.scan_commands.length ? ` ${response.scan_commands.length} scans queued.` : ""}${response.warnings.length ? ` Warnings: ${response.warnings.map(remarkLabel).join(", ")}.` : ""}`;
    });
  }
  function repairCredentials(cleanup = false) {
    if (runtimeBusy || !runtime.inventory?.has_scan || !(cleanup ? runtime.credentials?.extra_runtime_client_count : runtime.credentials?.missing_runtime_client_count)) return;
    void mutate(cleanup ? "cleanup" : "repair", async (serverId) => {
      const response = cleanup ? await cleanupExtraXrayRuntimeCredentials(serverId, queueAndScan) : await repairMissingXrayRuntimeCredentials(serverId, queueAndScan);
      return `Queued ${response.planned_client_count} ${cleanup ? "extra runtime client removals" : "runtime clients"} in ${response.commands.length} commands.${response.scan_command ? " Follow-up scan queued." : ""}`;
    });
  }
  function deleteTunnel(tunnel: XrayRuntimeTunnel | XrayRuntimeTunnelChain) {
    if (runtimeBusy) return;
    const isChain = "hops" in tunnel;
    void mutate(`delete:${isChain ? `chain:${tunnel.label}` : tunnelKey(tunnel)}`, async (serverId) => {
      const response = await deleteXrayRuntimeTunnel(serverId, isChain ? { kind: "chain", label: tunnel.label, ...queueAndScan } : { kind: tunnel.kind, tag: tunnel.tag, rule_index: tunnel.rule_index, ...queueAndScan });
      return `Queued ${response.commands.length} ${isChain ? "chain" : "tunnel"} delete commands.`;
    });
  }

  const operationButton = (label: string, kind: AgentOperationKind, payload?: AgentOperationPayload) => <Button key={kind} disabled={!selectedId || mutationBusy || workspaceOperations.has(kind) && !workspaceSupported} loading={savingOperation === kind} onClick={() => void queueOperation(kind, payload)}>{label}</Button>;
  const xrayTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    <Space wrap>{operationButton("Read", "xray_config_read")}<Button onClick={() => useLatestRaw("xray")} disabled={mutationBusy}>Use latest</Button>{operationButton("Test", "xray_test_config", { config: xray.configText })}<Button type="primary" htmlType="submit" form="xray-config-form" disabled={!selectedId || mutationBusy} loading={savingOperation === "xray_config_write"}>Write</Button><Button disabled={!selectedId || mutationBusy || takeoverBusy} onClick={() => void runTakeover()}>Takeover external</Button></Space>
    <Card size="small" title="Config snapshots" extra={<Button aria-label="Refresh Xray snapshots" icon={<ReloadOutlined />} loading={snapshotsLoading} onClick={() => void refreshSnapshots()} />}>
      <Typography.Paragraph type="secondary">{currentSnapshot ? `${shortHash(currentSnapshot.config_hash)} current` : "No saved Xray config yet"}</Typography.Paragraph>
      {pendingSnapshot && <Alert type="warning" showIcon title="Pending recovery" description={<Space orientation="vertical"><Typography.Text>Agent {shortHash(pendingSnapshot.config_hash)} / Current {currentSnapshot ? shortHash(currentSnapshot.config_hash) : "none"}</Typography.Text><Space wrap><Button disabled={mutationBusy} loading={actionKey === `accept:${pendingSnapshot.id}`} onClick={() => void mutate(`accept:${pendingSnapshot.id}`, async (serverId, current) => { const response = await acceptXrayConfigPendingRecovery(serverId); if (current()) setSnapshots(response.snapshots); return `Accepted ${shortHash(response.current.config_hash)} as current.`; })}>Accept</Button><Button disabled={mutationBusy || !currentSnapshot} loading={actionKey === "recovery"} onClick={() => void mutate("recovery", async (serverId) => { const response = await applyXrayConfigRecovery(serverId, { restart_xray: true, merge_agent_only: true, command_timeout_ms: 60000 }); return `Queued ${response.command_count} recovery commands from ${shortHash(response.snapshot.config_hash)}.${response.merged_agent_only_count ? ` Merged ${response.merged_agent_only_count} agent-only entries.` : ""}${response.warnings.length ? ` Warnings: ${response.warnings.join(", ")}.` : ""}`; })}>Apply current</Button></Space></Space>} />}
      <Table<XrayConfigSnapshot> rowKey="id" dataSource={snapshots} loading={snapshotsLoading} pagination={false} locale={{ emptyText: "No snapshots." }} scroll={{ x: 570 }} columns={[
        { title: "Snapshot", key: "hash", render: (_, snapshot) => <Space orientation="vertical" size={0}><Tag color={snapshot.status === "current" ? "success" : snapshot.status === "pending_recovery" ? "warning" : "default"}>{statusLabel(snapshot.status)}</Tag><Typography.Text code>{shortHash(snapshot.config_hash)}</Typography.Text></Space> },
        { title: "Source", key: "source", render: (_, snapshot) => ({ agent_report: "Agent", master_write: "Master", manual_accept: "Accepted" })[snapshot.source] }, { title: "Size", key: "size", render: (_, snapshot) => formatBytes(snapshot.size_bytes) }, { title: "Created", key: "created", render: (_, snapshot) => formatDate(snapshot.created_at) },
        { title: "Actions", key: "actions", render: (_, snapshot) => <Space wrap><Button disabled={mutationBusy} onClick={() => void loadSnapshot(snapshot)}>Load</Button><Button disabled={!selectedId || mutationBusy || snapshot.status === "pending_recovery"} loading={actionKey === `restore:${snapshot.id}`} onClick={() => void mutate(`restore:${snapshot.id}`, async (serverId) => { await restoreXrayConfigSnapshot(serverId, snapshot.id); })}>Restore</Button></Space> },
      ]} />
    </Card>
    <Form id="xray-config-form" layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("xray_config_write", { config: xray.configText, path: blankToNull(xray.path), force: xray.force })}><Form.Item label="Path"><Input aria-label="Path" value={xray.path} onChange={(event) => patchXray({ path: event.target.value })} /></Form.Item><Form.Item label="Force"><Switch aria-label="Force" checked={xray.force} onChange={(force) => patchXray({ force })} /></Form.Item><Form.Item label="Xray config"><Input.TextArea aria-label="Xray config" spellCheck={false} rows={16} value={xray.configText} onChange={(event) => patchXray({ configText: event.target.value })} /></Form.Item></Form>
  </Space>;
  const systemTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {(!workspaceSupported || systemState.reason) && <Alert type="warning" title={!workspaceSupported ? upgradeMessage : systemState.reason} showIcon />}
    {systemReady && systemState.apiMode === "routed" ? <Alert type="info" title={`This server uses the verified routed Xray gRPC API shape. ${systemState.grpcPortWritable ? "Its loopback port can be changed, but the API cannot be disabled from this form." : `Its endpoint is fixed at ${systemState.fixedStatsAddress} by the Agent's stats_address, and the API cannot be disabled from this form.`}`} showIcon /> : systemReady && systemState.fixedStatsAddress ? <Alert type="info" title={`The Xray gRPC API endpoint is fixed at ${systemState.fixedStatsAddress} by the Agent's stats_address. Other supported system fields remain editable.`} showIcon /> : null}
    <Space wrap><Button disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_system_config_read"} onClick={readSystem}>Read</Button><Button disabled={mutationBusy} onClick={useLatestSystem}>Use latest</Button><Button type="primary" htmlType="submit" form="xray-system-config-form" disabled={!workspaceSupported || !systemWriteReady || mutationBusy} loading={savingOperation === "xray_system_config_write"}>Write</Button></Space>
    <Form id="xray-system-config-form" layout="vertical" disabled={!systemReady || mutationBusy} onFinish={() => void writeSystem()}>
      <Form.Item label="Xray log level"><Select aria-label="Xray log level" value={system.log_level} options={logLevels.map((value) => ({ value, label: value }))} onChange={(log_level) => patchSystem({ log_level })} /></Form.Item>
      <Space wrap><Form.Item label="Metrics"><Switch aria-label="Metrics" checked={system.metrics_enabled} onChange={(metrics_enabled) => patchSystem({ metrics_enabled })} /></Form.Item><Form.Item label="Stats"><Switch aria-label="Stats" checked={system.stats_enabled} onChange={(stats_enabled) => patchSystem({ stats_enabled })} /></Form.Item><Form.Item label="Xray gRPC API"><Switch aria-label="Xray gRPC API" checked={system.grpc_enabled} disabled={!systemReady || mutationBusy || !systemState.grpcDisableSupported} onChange={(grpc_enabled) => patchSystem({ grpc_enabled })} /></Form.Item></Space>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item label="Metrics listen"><Input aria-label="Metrics listen" value={system.metrics_listen} onChange={(event) => patchSystem({ metrics_listen: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="Xray gRPC API port"><StrictInputNumber aria-label="Xray gRPC API port" aria-valuemin={1} aria-valuemax={65535} value={system.grpc_port} disabled={!systemReady || mutationBusy || !systemState.grpcPortWritable} onChange={(value) => patchSystem({ grpc_port: value ?? Number.NaN })} /></Form.Item></Col></Row>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item label="DNS JSON" validateStatus={dnsError ? "error" : undefined} help={dnsError || undefined}><Input.TextArea aria-label="DNS JSON" rows={10} spellCheck={false} value={dnsText} onChange={(event) => setDnsText(event.target.value)} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="Policy JSON" validateStatus={policyError ? "error" : undefined} help={policyError || undefined} extra="Changing Stats normalizes its counters; otherwise existing stats, policy and API service state is preserved."><Input.TextArea aria-label="Policy JSON" rows={10} spellCheck={false} value={policyText} onChange={(event) => setPolicyText(event.target.value)} /></Form.Item></Col></Row>
    </Form>
  </Space>;
  const deploymentForm = <Card size="small" title="Deploy tunnel"><Form layout="vertical" disabled={runtimeBusy} onFinish={deployTunnel}>
    <Row gutter={16}>
      <Col xs={24} md={12}><Form.Item label="Domain"><Input aria-label="Domain" value={deploy.domain} onChange={(event) => patchDeploy({ domain: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="Proxy domain"><Input aria-label="Proxy domain" value={deploy.proxyDomain} onChange={(event) => patchDeploy({ proxyDomain: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="Site type"><Select aria-label="Site type" value={deploy.siteType} options={[{ value: "static", label: "Static" }, { value: "proxy", label: "Proxy" }]} onChange={(siteType) => patchDeploy({ siteType })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label={deploy.siteType === "proxy" ? "Proxy URL" : "Static root"}><Input aria-label={deploy.siteType === "proxy" ? "Proxy URL" : "Static root"} value={deploy.siteValue} onChange={(event) => patchDeploy({ siteValue: event.target.value })} /></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item label="Cert name"><Input aria-label="Cert name" value={deploy.certName} onChange={(event) => patchDeploy({ certName: event.target.value })} /></Form.Item></Col>
    </Row>
    <Space wrap align="start"><Form.Item label="Clear stream"><Switch aria-label="Clear stream" checked={deploy.clearStreamPort} onChange={(clearStreamPort) => patchDeploy({ clearStreamPort })} /></Form.Item><Form.Item label="Restart"><Switch aria-label="Restart" checked={deploy.restartXray} onChange={(restartXray) => patchDeploy({ restartXray })} /></Form.Item><Checkbox checked={deploy.force} onChange={(event) => patchDeploy({ force: event.target.checked })}>Force</Checkbox></Space>
    <Collapse items={[{ key: "listeners", label: "Listeners", children: <><Form.Item label="Listen address"><Input aria-label="Listen address" value={deploy.listenAddress} onChange={(event) => patchDeploy({ listenAddress: event.target.value })} /></Form.Item><Row gutter={16}>{tunnelPortFields.map(({ key, label }) => <Col xs={24} sm={12} lg={8} key={key}><Form.Item label={label}><StrictInputNumber aria-label={label} aria-valuemin={1} aria-valuemax={65535} value={deploy[key]} onChange={(value) => patchDeploy({ [key]: value ?? Number.NaN })} /></Form.Item></Col>)}</Row>{portsInvalid && <Alert type="error" title="A listener address and distinct ports from 1 to 65535 are required" />}</> }]} />
    <Button style={{ marginTop: 16 }} type="primary" htmlType="submit" disabled={deployDisabled} loading={actionKey === "deploy"}>Deploy tunnel</Button>
  </Form></Card>;
  const chainForm = <Card size="small" title="Tunnel chain"><Form layout="vertical" disabled={runtimeBusy} onFinish={createChain}>
    <Row gutter={16}><Col xs={24} md={12}><Form.Item label="Label"><Input aria-label="Label" value={chain.label} onChange={(event) => patchChain({ label: event.target.value })} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="Hop servers" extra="Hop order is the order shown below. Select 2–16 servers."><Select aria-label="Hop servers" mode="multiple" value={chain.serverIds} options={serverOptions} maxCount={16} onChange={(serverIds) => patchChain({ serverIds })} /></Form.Item></Col></Row>
    <Space orientation="vertical" style={{ width: "100%", marginBottom: 16 }}>{chain.serverIds.map((id, index) => <Space key={id} wrap><Typography.Text>{index + 1}. {servers.find((server) => server.id === id)?.name ?? id}</Typography.Text><Button icon={<ArrowUpOutlined />} aria-label={`Move hop ${index + 1} up`} disabled={runtimeBusy || index === 0} onClick={() => { const ids = [...chain.serverIds]; [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]]; patchChain({ serverIds: ids }); }} /><Button icon={<ArrowDownOutlined />} aria-label={`Move hop ${index + 1} down`} disabled={runtimeBusy || index === chain.serverIds.length - 1} onClick={() => { const ids = [...chain.serverIds]; [ids[index + 1], ids[index]] = [ids[index], ids[index + 1]]; patchChain({ serverIds: ids }); }} /></Space>)}</Space>
    <Row gutter={16}><Col xs={24} md={8}><Form.Item label="Entry port" extra="0 selects an available port"><StrictInputNumber aria-label="Entry port" aria-valuemin={0} aria-valuemax={65535} value={chain.entryPort} onChange={(value) => patchChain({ entryPort: value ?? Number.NaN })} /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="Final target"><Input aria-label="Final target" value={chain.targetAddress} onChange={(event) => patchChain({ targetAddress: event.target.value })} /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="Target port"><StrictInputNumber aria-label="Target port" aria-valuemin={1} aria-valuemax={65535} value={chain.targetPort} onChange={(value) => patchChain({ targetPort: value ?? Number.NaN })} /></Form.Item></Col></Row>
    <Button type="primary" htmlType="submit" disabled={chainDisabled} loading={actionKey === "chain"}>Create chain</Button>
  </Form></Card>;
  const runtimeTab = <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Card title="Runtime inventory" extra={<Space wrap><Tag color={!runtime.inventory?.has_scan ? "default" : runtime.inventory.xray_running ? "success" : "warning"}>{runtimeStatus}</Tag><Button aria-label="Refresh runtime inventory" icon={<ReloadOutlined />} loading={runtimeLoading} onClick={() => void refreshRuntime(true)} /></Space>}>
      <Typography.Paragraph type="secondary">{runtimeSummary}</Typography.Paragraph>
      <Space wrap style={{ marginBottom: 16 }}><Button disabled={runtimeBusy || !runtime.inventory?.has_scan || !missingNodeCount} loading={actionKey === "import"} onClick={() => void mutate("import", async (serverId) => { const response = await importManagedNodesFromRuntimeInbounds(serverId); return `Imported ${response.created_count} nodes, ${response.existing_count} already managed, ${response.skipped_count} skipped.`; })}>Import missing</Button><Button disabled={runtimeBusy || !runtime.inventory?.has_scan || !runtime.credentials?.missing_runtime_client_count} loading={actionKey === "repair"} onClick={() => repairCredentials()}>Repair clients</Button><Button disabled={runtimeBusy || !runtime.inventory?.has_scan || !runtime.credentials?.extra_runtime_client_count} loading={actionKey === "cleanup"} onClick={() => repairCredentials(true)}>Cleanup extras</Button></Space>
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: "inbounds", label: "Inbounds", children: runtime.inventory?.inbound_count ?? 0 }, { key: "clients", label: "Clients", children: runtime.inventory?.client_count ?? 0 }, { key: "tunnels", label: "Tunnels / chains", children: `${runtime.tunnels?.tunnel_count ?? 0} / ${runtime.tunnels?.chain_count ?? 0}` },
        { key: "traffic", label: "Inbound traffic", children: formatTraffic(runtime.inventory?.traffic) }, { key: "user-traffic", label: "User traffic", children: formatTraffic(runtime.inventory?.user_traffic) },
        { key: "managed", label: "Managed / unmanaged", children: `${runtime.nodes?.managed_runtime_count ?? 0} / ${runtime.nodes?.unmanaged_runtime_count ?? 0}` }, { key: "stale", label: "Stale / missing", children: `${runtime.nodes?.stale_count ?? 0} / ${runtime.nodes?.missing_runtime_count ?? 0}` },
        { key: "credentials", label: "Credential drift", children: `${runtime.credentials?.out_of_sync_count ?? 0} out of sync / ${runtime.credentials?.missing_runtime_client_count ?? 0} missing / ${runtime.credentials?.extra_runtime_client_count ?? 0} extra` },
      ]} />
      <Space wrap>{runtime.inventory?.config_modified && <Tag color="warning">Config repaired</Tag>}{Object.entries(runtime.inventory?.protocol_counts ?? {}).sort(([left], [right]) => left.localeCompare(right)).map(([protocol, count]) => <Tag key={protocol}>{protocol}: {count}</Tag>)}</Space>
      {(runtime.tunnels?.warnings ?? []).map((warning) => <Alert key={warning} type="warning" title={remarkLabel(warning)} showIcon />)}
      {runtime.inventory?.message && <Typography.Paragraph>{runtime.inventory.message}</Typography.Paragraph>}
    </Card>
    {deploymentForm}{chainForm}
    <Card size="small" title="Runtime inbounds"><Table<XrayRuntimeInbound> rowKey="source_index" dataSource={runtime.inventory?.inbounds ?? []} loading={runtimeLoading} scroll={{ x: 850 }} locale={{ emptyText: runtime.inventory?.has_scan ? "No runtime inbounds." : "No scan inventory." }} columns={[
      { title: "Inbound", key: "inbound", render: (_, inbound) => <Space orientation="vertical" size={0}><Typography.Text strong>{inbound.display_name}</Typography.Text><Typography.Text>{[inbound.protocol, inbound.network, inbound.security, inbound.client_container].filter(Boolean).join(" / ") || "No metadata"}</Typography.Text><Typography.Text>{[inbound.listen, inbound.port ? String(inbound.port) : ""].filter(Boolean).join(":") || "No endpoint"}</Typography.Text><Typography.Text>{inbound.client_count} clients</Typography.Text><Typography.Text>{inbound.user_emails.join(", ")}</Typography.Text></Space> },
      { title: "Status / sniffing", key: "status", render: (_, inbound) => { const entry = entryByIndex.get(inbound.source_index); return <Space orientation="vertical" size={0}><Tag color={entry?.status === "managed" ? "success" : entry?.status === "unavailable" ? "error" : "warning"}>{entry ? statusLabel(entry.status) : "Unknown"}</Tag>{entry?.managed_node_name && <Typography.Text>{entry.managed_node_name}</Typography.Text>}<Typography.Text>Sniffing {inbound.sniffing_enabled ? "on" : "off"}</Typography.Text>{inbound.sniffing_dest_override.length > 0 && <Typography.Text>Override: {inbound.sniffing_dest_override.join(", ")}</Typography.Text>}{inbound.sniffing_exclude_domains.length > 0 && <Typography.Text>Excludes: {inbound.sniffing_exclude_domains.join(", ")}</Typography.Text>}</Space>; } },
      { title: "Traffic", key: "traffic", render: (_, inbound) => <Space orientation="vertical" size={0}><Typography.Text>{formatTraffic(inbound.traffic)}</Typography.Text><Typography.Text>User {formatTraffic(inbound.user_traffic)}</Typography.Text></Space> },
      { title: "Catalog", key: "catalog", render: (_, inbound) => { const draft = draftByIndex.get(inbound.source_index); return <Space orientation="vertical"><Button disabled={runtimeBusy || !draft?.create_available || Boolean(draft.existing_node_id)} loading={actionKey === `create:${inbound.source_index}`} onClick={() => void mutate(`create:${inbound.source_index}`, async (serverId) => { const response = await createManagedNodeFromRuntimeInbound(serverId, { source_index: inbound.source_index }); return `Created managed node ${response.node.name}.`; })}>{draft?.existing_node_id ? "Node exists" : "Create node"}</Button>{(draft?.warnings ?? inbound.remarks).map((warning) => <Tag key={warning} color="warning">{remarkLabel(warning)}</Tag>)}</Space>; } },
    ]} /></Card>
    <Card size="small" title="Managed node reconciliation"><Table<XrayRuntimeNodeReconciliationManagedEntry> rowKey="node_id" dataSource={nodeIssues} pagination={false} locale={{ emptyText: "No managed node drift." }} scroll={{ x: 650 }} columns={[
      { title: "Node", key: "node", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text strong>{entry.node_name}</Typography.Text><Typography.Text>{entry.protocol} / {entry.inbound_tag || "No inbound tag"}</Typography.Text><Typography.Text>{entry.runtime_display_name || "No runtime inbound"}</Typography.Text></Space> },
      { title: "Status", key: "status", render: (_, entry) => <Tag color={entry.status === "missing_runtime" ? "error" : "warning"}>{statusLabel(entry.status)}</Tag> },
      { title: "Differences", key: "drifts", render: (_, entry) => <Space orientation="vertical" size={0}>{entry.drifts.map((drift) => <Typography.Text key={drift.field}>{drift.field}: {Array.isArray(drift.managed_value) ? drift.managed_value.join(",") : String(drift.managed_value ?? "-")} → {Array.isArray(drift.runtime_value) ? drift.runtime_value.join(",") : String(drift.runtime_value ?? "-")}</Typography.Text>)}</Space> },
      { title: "Actions", key: "actions", render: (_, entry) => <Button disabled={runtimeBusy || entry.status !== "stale" || entry.runtime_source_index == null} loading={actionKey === `sync:${entry.node_id}`} onClick={() => void mutate(`sync:${entry.node_id}`, async (serverId) => { const response = await syncManagedNodeFromRuntime(serverId, entry.node_id, { source_index: entry.runtime_source_index }); return `Synced ${response.node.name}: ${response.updated_fields.length} fields.`; })}>Sync</Button> },
    ]} /></Card>
    <Card size="small" title="Credential reconciliation"><Table<XrayRuntimeCredentialReconciliationEntry> rowKey="node_id" dataSource={credentialIssues} pagination={false} locale={{ emptyText: "No credential drift." }} scroll={{ x: 650 }} columns={[
      { title: "Node", key: "node", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text strong>{entry.node_name}</Typography.Text><Typography.Text>{entry.protocol} / {entry.inbound_tag || "No inbound tag"}</Typography.Text><Typography.Text>{entry.runtime_display_name || "No runtime inbound"}</Typography.Text></Space> },
      { title: "Status", key: "status", render: (_, entry) => <Tag color={entry.status === "missing_runtime" ? "error" : "warning"}>{statusLabel(entry.status)}</Tag> },
      { title: "Clients", key: "clients", render: (_, entry) => <Space orientation="vertical" size={0}><Typography.Text>{entry.expected_emails.length} expected / {entry.runtime_emails.length} runtime</Typography.Text>{entry.missing_runtime_emails.length > 0 && <Typography.Text>Missing: {entry.missing_runtime_emails.join(", ")}</Typography.Text>}{entry.extra_runtime_emails.length > 0 && <Typography.Text>Extra: {entry.extra_runtime_emails.join(", ")}</Typography.Text>}</Space> },
    ]} /></Card>
    <Card size="small" title="Runtime tunnels">
      {!runtime.tunnels?.tunnels.length && !runtime.tunnels?.chains.length && <Empty description="No runtime tunnels." />}
      {Boolean(runtime.tunnels?.tunnels.length) && <Table<XrayRuntimeTunnel> rowKey={tunnelKey} dataSource={runtime.tunnels?.tunnels} pagination={false} scroll={{ x: 650 }} columns={[
        { title: "Tunnel", key: "tunnel", render: (_, tunnel) => <Space orientation="vertical" size={0}><Typography.Text strong>{tunnel.tag}</Typography.Text><Tag color={tunnel.kind === "routed" ? "processing" : "default"}>{tunnel.kind}</Tag><Typography.Text>{tunnel.listen_port == null ? "No listen port" : `:${tunnel.listen_port}`} → {tunnelTarget(tunnel.target_address, tunnel.target_port)}</Typography.Text>{tunnel.kind === "routed" && <Typography.Text>{tunnel.inbound_tag ? `from ${tunnel.inbound_tag}` : "No inbound rule source"} / rule {tunnel.rule_index ?? "-"}</Typography.Text>}<Typography.Text>{tunnel.network}</Typography.Text><Typography.Text>{[...tunnel.match_domains, ...tunnel.match_ips].join(", ")}</Typography.Text></Space> },
        { title: "Actions", key: "actions", render: (_, tunnel) => <Button danger disabled={runtimeBusy} loading={actionKey === `delete:${tunnelKey(tunnel)}`} onClick={() => deleteTunnel(tunnel)}>Delete</Button> },
      ]} />}
      {(runtime.tunnels?.chains ?? []).map((item) => <Card key={item.label} size="small" title={item.label} extra={<Button danger disabled={runtimeBusy} loading={actionKey === `delete:chain:${item.label}`} onClick={() => deleteTunnel(item)}>Delete chain</Button>} style={{ marginTop: 16 }}><Typography.Paragraph>{item.entry_port == null ? "No entry port" : `:${item.entry_port}`} → {item.final_target || "No target"}</Typography.Paragraph>{item.hops.map((hop, index) => <Typography.Paragraph key={`${hop.tag}:${index}`}>{index + 1}. {hop.tag} / {hop.listen_port == null ? "No listen port" : `:${hop.listen_port}`} → {tunnelTarget(hop.target_address, hop.target_port)}</Typography.Paragraph>)}</Card>)}
    </Card>
    <Card size="small" title="Runtime operations"><Space wrap style={{ marginBottom: 16 }}>{operationButton("Inbounds", "inbounds_list")}{operationButton("Outbounds", "outbounds_list")}{operationButton("Routing", "routing_read")}</Space><Form layout="vertical" disabled={mutationBusy} onFinish={() => queueJson(runtimeOperation, runtimePayload)}><Form.Item label="Operation"><Select aria-label="Operation" value={runtimeOperation} options={runtimeOperations} onChange={setRuntimeOperation} /></Form.Item><Form.Item label="Payload"><Input.TextArea aria-label="Payload" rows={12} spellCheck={false} value={runtimePayload} onChange={(event) => setRuntimePayload(event.target.value)} /></Form.Item><Button type="primary" htmlType="submit" loading={savingOperation === runtimeOperation} disabled={!selectedId || mutationBusy}>Queue</Button></Form></Card>
  </Space>;
  const nginxTab = <Form layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("nginx_config_write", { config: nginx.configText, path: blankToNull(nginx.path) })}><Space wrap style={{ marginBottom: 16 }}>{operationButton("Read", "nginx_config_read")}<Button onClick={() => useLatestRaw("nginx")}>Use latest</Button><Button type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === "nginx_config_write"}>Write</Button></Space><Form.Item label="Path"><Input aria-label="Path" value={nginx.path} onChange={(event) => patchNginx({ path: event.target.value })} /></Form.Item><Form.Item label="Nginx config"><Input.TextArea aria-label="Nginx config" rows={16} spellCheck={false} value={nginx.configText} onChange={(event) => patchNginx({ configText: event.target.value })} /></Form.Item></Form>;
  const sitesTab = <Form layout="vertical" disabled={mutationBusy} onFinish={() => queueJson(siteOperation, sitePayload)}><Space wrap style={{ marginBottom: 16 }}>{operationButton("Servers", "nginx_servers_list")}{operationButton("Websites", "nginx_websites_list")}</Space><Form.Item label="Operation"><Select aria-label="Operation" value={siteOperation} options={siteOperations} onChange={setSiteOperation} /></Form.Item><Form.Item label="Payload"><Input.TextArea aria-label="Payload" rows={12} spellCheck={false} value={sitePayload} onChange={(event) => setSitePayload(event.target.value)} /></Form.Item><Button type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === siteOperation}>Queue</Button></Form>;
  const filesTab = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
    {(!workspaceSupported || fileState.reason) && <Alert type="warning" title={!workspaceSupported ? upgradeMessage : fileState.reason} showIcon />}
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}><Card size="small" title="Xray file"><Form layout="vertical" disabled={mutationBusy} onFinish={() => void writeFile()}><Space wrap style={{ marginBottom: 16 }}><Button disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_config_files_list"} onClick={() => readFile(true)}>List</Button><Button disabled={!selectedId || !workspaceSupported || mutationBusy} loading={savingOperation === "xray_config_file_read"} onClick={() => readFile()}>Read</Button><Button onClick={useLatestFile}>Use latest</Button><Button type="primary" htmlType="submit" disabled={!workspaceSupported || !fileWriteReady || mutationBusy} loading={savingOperation === "xray_config_file_write"}>Write</Button></Space><Form.Item label="File"><Input aria-label="File" value={file.file} onChange={(event) => { const filename = event.target.value; patchFile({ file: filename }); if (fileState.revision?.file !== filename.trim()) invalidateFile("Read this exact Xray file before editing it."); }} /></Form.Item><Form.Item label="Content"><Input.TextArea aria-label="Content" rows={12} spellCheck={false} value={file.content} disabled={!fileWriteReady || mutationBusy} placeholder={fileWriteReady ? undefined : "Read this exact file and use the latest result before editing."} onChange={(event) => patchFile({ content: event.target.value })} /></Form.Item></Form></Card></Col>
      <Col xs={24} lg={12}><Card size="small" title="Nginx file"><Form layout="vertical" disabled={mutationBusy} onFinish={() => void queueOperation("nginx_config_file_write", { path: nginxFile.path.trim(), content: nginxFile.content })}><Space wrap style={{ marginBottom: 16 }}>{operationButton("List", "nginx_config_files_list")}{operationButton("Read", "nginx_config_file_read", { file: nginxFile.file.trim() })}<Button onClick={() => useLatestRaw("nginx-file")}>Use latest</Button><Button type="primary" htmlType="submit" disabled={!selectedId || mutationBusy} loading={savingOperation === "nginx_config_file_write"}>Write</Button></Space><Form.Item label="Read path"><Input aria-label="Read path" value={nginxFile.file} onChange={(event) => patchNginxFile({ file: event.target.value })} /></Form.Item><Form.Item label="Write path"><Input aria-label="Write path" value={nginxFile.path} onChange={(event) => patchNginxFile({ path: event.target.value })} /></Form.Item><Form.Item label="Content"><Input.TextArea aria-label="Content" rows={12} spellCheck={false} value={nginxFile.content} onChange={(event) => patchNginxFile({ content: event.target.value })} /></Form.Item></Form></Card></Col>
    </Row>
  </Space>;
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <Space wrap style={{ width: "100%", justifyContent: "space-between" }}><Typography.Title level={2}>Configuration workspace</Typography.Title><Button icon={<ReloadOutlined />} aria-label="Refresh config commands" loading={loading} onClick={() => void refresh()} /></Space>
    {error && <Alert type="error" title={error} showIcon />}{success && <Alert type="success" title={success} showIcon />}
    <Card title="Workspace"><Typography.Paragraph type="secondary">MMW agent child config operations</Typography.Paragraph><Form layout="vertical"><Form.Item label="Target server"><Select aria-label="Target server" value={selectedId || undefined} options={serverOptions} disabled={!serverOptions.length} onChange={(id) => selectServer(id)} /></Form.Item></Form><Tabs activeKey={activeTab} onChange={setActiveTab} items={[
      { key: "xray", label: "Xray", children: xrayTab }, { key: "system", label: "System", children: systemTab }, { key: "runtime", label: "Runtime", children: runtimeTab },
      { key: "limits", label: "Limits", children: activeTab === "limits" ? <LimiterPanel key={selectedId} serverId={selectedId} inbounds={runtime.inventory?.inbounds ?? []} onCommands={receiveCommands} /> : null },
      { key: "nginx", label: "Nginx", children: nginxTab }, { key: "sites", label: "Sites", children: sitesTab }, { key: "files", label: "Files", children: filesTab },
    ]} /></Card>
    <Card title="Command results" extra={<Button icon={<ReloadOutlined />} aria-label="Refresh command results" onClick={() => void refreshCommands()} />}><Typography.Paragraph type="secondary">Selected server history</Typography.Paragraph><CommandInspector commands={selectedCommands} streamFramesByCommand={frames} emptyText="No config commands yet." /></Card>
    <Modal title="Xray takeover" open={takeoverOpen} onCancel={closeTakeover} closeIcon={<CloseOutlined aria-label="Close takeover" />} width={640} styles={{ body: { maxHeight: "70vh", overflowY: "auto" } }} footer={<Space><Button disabled={takeoverBusy} onClick={() => void runTakeover()}>Refresh</Button><Button type="primary" disabled={!takeoverConfirmed || !takeoverPreview || takeoverBusy} onClick={() => void runTakeover(true)}>Take over</Button></Space>}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}><Typography.Text strong>{selectedServer?.name}</Typography.Text>{takeoverBusy && <Spin description="Waiting for Agent command"><div style={{ minHeight: 32 }} /></Spin>}{takeoverError && <Alert type="error" title={takeoverError} showIcon />}{takeoverPreview && <><Descriptions column={1} items={[{ key: "target", label: "Target file", children: takeoverPreview.target }, { key: "runtime", label: "Runtime", children: takeoverPreview.running ? "Running" : "Stopped" }, { key: "checksum", label: "Source checksum", children: <Typography.Text code style={{ overflowWrap: "anywhere" }}>{takeoverPreview.sha256}</Typography.Text> }]} /><Typography.Title level={5}>{takeoverPreview.files.length} source files</Typography.Title><ul>{takeoverPreview.files.map((file) => <li key={file} style={{ overflowWrap: "anywhere" }}>{file}</li>)}</ul><Checkbox disabled={takeoverBusy} checked={takeoverConfirmed} onChange={(event) => setTakeoverConfirmed(event.target.checked)}>Replace source fragments and restart Xray if running</Checkbox></>}</Space>
    </Modal>
  </Space>;
}
