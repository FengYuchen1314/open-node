<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import CommandInspector from "../components/CommandInspector.vue";
import {
  defaultServerCreateRequest,
  type AgentCommand,
  type AgentCommandStreamFrame,
  type AgentLogService,
  type AgentOperationKind,
  type AgentServiceName,
  type AgentOperationPayload,
  type AgentScanResult,
  type AgentTelemetry,
  type ConnectionMode,
  type RenewalCycle,
  type ServerCreateRequest,
  type ServerProbeMetadataUpdate,
  type ServerStatus,
  type ServerSummary,
  type XrayMode,
} from "../domain/inventory";
import {
  createServer,
  createServerCommand,
  getLatestScanResult,
  getLatestTelemetry,
  listCommandStreamFrames,
  listServerCommands,
  listServers,
  queueAgentOperation,
  updateServerProbeMetadata,
} from "../services/inventory";

const servers = ref<ServerSummary[]>([]);
const telemetryByServer = ref<Record<string, AgentTelemetry | null>>({});
const scanResultsByServer = ref<Record<string, AgentScanResult | null>>({});
const commandsByServer = ref<Record<string, AgentCommand[]>>({});
const streamFramesByCommand = ref<Record<string, AgentCommandStreamFrame[]>>({});
const loading = ref(false);
const saving = ref(false);
const savingMetadata = ref(false);
const savingCommand = ref(false);
const savingOperation = ref<AgentOperationKind | "">("");
const errorMessage = ref("");
const successMessage = ref("");
const latestToken = ref<{ serverName: string; token: string } | null>(null);
const form = reactive<ServerCreateRequest>(defaultServerCreateRequest());
const metadataForm = reactive({
  server_id: "",
  region: "",
  region_country: "",
  region_name: "",
  region_city: "",
  provider_name: "",
  provider_url: "",
  expires_at: "",
  renewal_price: null as number | null,
  renewal_price_cny: null as number | null,
  renewal_cycle: null as RenewalCycle | null,
  renewal_currency: "",
  telecom_paid_peer: null as boolean | null,
});
const commandForm = reactive({
  server_id: "",
  method: "GET",
  path: "/api/child/system/info",
  query: "",
  bodyText: "",
  timeout_ms: 30_000,
  stream: false,
});
const domainLatencyForm = reactive({
  domainsText: "",
  timeout_ms: 2_000,
  allow_icmp: false,
});
const agentSettingsForm = reactive({
  xray_mode: "external" as XrayMode,
  listen_port: 23889,
  master_url: "",
  only_if_recovery: true,
  warp_license: "",
});
const logFilesForm = reactive({
  name: "",
  all: false,
});
const nginxToolsForm = reactive({
  stream_port: 443,
});

const connectionModes: Array<{ title: string; value: ConnectionMode }> = [
  { title: "Auto", value: "auto" },
  { title: "WebSocket", value: "websocket" },
  { title: "HTTP", value: "http" },
  { title: "Pull", value: "pull" },
];

const xrayModes: Array<{ title: string; value: XrayMode }> = [
  { title: "External", value: "external" },
  { title: "Embedded", value: "embedded" },
];
const renewalCycleOptions: Array<{ title: string; value: RenewalCycle }> = [
  { title: "Month", value: "month" },
  { title: "Quarter", value: "quarter" },
  { title: "Half year", value: "half_year" },
  { title: "Year", value: "year" },
];
const paidPeerOptions: Array<{ title: string; value: boolean | null }> = [
  { title: "Unknown", value: null },
  { title: "Paid peer", value: true },
  { title: "Standard peer", value: false },
];

const commandMethods = ["GET", "POST", "PUT", "PATCH", "DELETE"];
type PayloadAgentOperation =
  | "domain_latency"
  | "service_control"
  | "logs"
  | "log_files_delete"
  | "xray_test_config"
  | "xray_config_write"
  | "xray_system_config_write"
  | "xray_config_file_read"
  | "xray_config_file_write"
  | "nginx_config_write"
  | "nginx_config_file_read"
  | "nginx_config_file_write"
  | "nginx_clear_stream_port"
  | "warp_license"
  | "agent_switch_xray_mode"
  | "agent_switch_listen_port"
  | "agent_probe_master_url"
  | "agent_update_master_url";
type SimpleAgentOperation = Exclude<AgentOperationKind, PayloadAgentOperation>;
const quickOperations: Array<{
  title: string;
  icon: string;
  kind: SimpleAgentOperation;
}> = [
  { title: "System info", icon: "mdi-monitor-dashboard", kind: "system_info" },
  { title: "Traffic", icon: "mdi-swap-vertical", kind: "traffic" },
  { title: "Speed", icon: "mdi-speedometer", kind: "speed" },
];
const diagnosticOperations: Array<{
  title: string;
  icon: string;
  kind: SimpleAgentOperation;
}> = [
  { title: "Services", icon: "mdi-list-status", kind: "services_status" },
  { title: "NICs", icon: "mdi-ethernet", kind: "system_nics" },
  { title: "Scan", icon: "mdi-radar", kind: "scan" },
  { title: "Log files", icon: "mdi-file-document-multiple-outline", kind: "log_files_list" },
];
const maintenanceOperations: Array<{
  title: string;
  icon: string;
  kind: SimpleAgentOperation;
  color: string;
}> = [
  {
    title: "Install Xray",
    icon: "mdi-download-network-outline",
    kind: "xray_install",
    color: "secondary",
  },
  { title: "Remove Xray", icon: "mdi-delete-outline", kind: "xray_remove", color: "error" },
  {
    title: "Install Nginx",
    icon: "mdi-server-plus",
    kind: "nginx_install",
    color: "secondary",
  },
  { title: "Remove Nginx", icon: "mdi-server-minus", kind: "nginx_remove", color: "error" },
  {
    title: "Install WARP",
    icon: "mdi-cloud-download-outline",
    kind: "warp_install",
    color: "info",
  },
  { title: "WARP status", icon: "mdi-cloud-check-outline", kind: "warp_status", color: "info" },
  { title: "Remove WARP", icon: "mdi-cloud-remove-outline", kind: "warp_remove", color: "error" },
  { title: "Upgrade Agent", icon: "mdi-update", kind: "agent_upgrade", color: "warning" },
  {
    title: "Uninstall Agent",
    icon: "mdi-power-plug-off-outline",
    kind: "agent_uninstall",
    color: "error",
  },
];
const serviceControlOperations: Array<{
  title: string;
  icon: string;
  service: AgentServiceName;
}> = [
  { title: "Restart Xray", icon: "mdi-restart", service: "xray" },
  { title: "Restart Nginx", icon: "mdi-restart", service: "nginx" },
];
const logOperations: Array<{
  title: string;
  icon: string;
  service: AgentLogService;
}> = [
  { title: "Agent", icon: "mdi-text-box-search-outline", service: "agent" },
  { title: "Xray", icon: "mdi-text-box-search-outline", service: "xray" },
  { title: "Nginx", icon: "mdi-text-box-search-outline", service: "nginx" },
];
const configReadOperations: Array<{
  title: string;
  icon: string;
  kind: SimpleAgentOperation;
}> = [
  { title: "Xray config", icon: "mdi-file-code-outline", kind: "xray_config_read" },
  {
    title: "Xray system",
    icon: "mdi-tune-variant",
    kind: "xray_system_config_read",
  },
  {
    title: "Xray files",
    icon: "mdi-folder-cog-outline",
    kind: "xray_config_files_list",
  },
  { title: "Nginx config", icon: "mdi-file-cog-outline", kind: "nginx_config_read" },
  {
    title: "Nginx files",
    icon: "mdi-folder-cog-outline",
    kind: "nginx_config_files_list",
  },
];

const statusMeta: Record<ServerStatus, { color: string; icon: string; label: string }> = {
  pending: { color: "warning", icon: "mdi-timer-sand", label: "Pending" },
  connected: { color: "success", icon: "mdi-lan-connect", label: "Connected" },
  offline: { color: "error", icon: "mdi-lan-disconnect", label: "Offline" },
};

const totalUpload = computed(() =>
  servers.value.reduce((sum, server) => sum + server.current_upload_speed, 0),
);
const totalDownload = computed(() =>
  servers.value.reduce((sum, server) => sum + server.current_download_speed, 0),
);
const telemetryCount = computed(
  () => servers.value.filter((server) => telemetryByServer.value[server.id]).length,
);
const metrics = computed(() => [
  {
    label: "Servers",
    value: servers.value.length.toString(),
    note: "SQLite inventory records",
    icon: "mdi-server-network",
    color: "primary",
  },
  {
    label: "Connected",
    value: servers.value.filter((server) => server.status === "connected").length.toString(),
    note: "Agents currently reporting",
    icon: "mdi-access-point-check",
    color: "success",
  },
  {
    label: "Speed",
    value: `${formatBytesPerSecond(totalUpload.value)} / ${formatBytesPerSecond(totalDownload.value)}`,
    note: `${telemetryCount.value} telemetry snapshots`,
    icon: "mdi-speedometer",
    color: "info",
  },
]);
const emptyState = computed(() => !loading.value && servers.value.length === 0);
const serverOptions = computed(() =>
  servers.value.map((server) => ({ title: server.name, value: server.id })),
);
const selectedCommands = computed(() => commandsByServer.value[commandForm.server_id] ?? []);
const selectedMetadataServer = computed(
  () => servers.value.find((server) => server.id === metadataForm.server_id) ?? null,
);

onMounted(() => {
  void refreshServers();
});

async function refreshServers() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const nextServers = await listServers();
    servers.value = nextServers;
    syncCommandTarget(nextServers);
    syncMetadataTarget(nextServers);
    await Promise.all([
      refreshTelemetry(nextServers),
      refreshScanResults(nextServers),
      refreshCommands(nextServers),
    ]);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
  }
}

async function refreshTelemetry(nextServers: ServerSummary[]) {
  const entries = await Promise.all(
    nextServers.map(async (server) => {
      try {
        const response = await getLatestTelemetry(server.id);
        return [server.id, response.latest ?? null] as const;
      } catch {
        return [server.id, null] as const;
      }
    }),
  );
  telemetryByServer.value = Object.fromEntries(entries);
}

async function refreshScanResults(nextServers: ServerSummary[]) {
  const entries = await Promise.all(
    nextServers.map(async (server) => {
      try {
        const response = await getLatestScanResult(server.id);
        return [server.id, response.scan ?? null] as const;
      } catch {
        return [server.id, null] as const;
      }
    }),
  );
  scanResultsByServer.value = Object.fromEntries(entries);
}

async function refreshCommands(nextServers: ServerSummary[]) {
  const entries = await Promise.all(
    nextServers.map(async (server) => {
      try {
        const response = await listServerCommands(server.id);
        return [server.id, response.commands] as const;
      } catch {
        return [server.id, []] as const;
      }
    }),
  );
  const nextCommandsByServer: Record<string, AgentCommand[]> = Object.fromEntries(entries);
  commandsByServer.value = nextCommandsByServer;
  await refreshStreamFrames(Object.values(nextCommandsByServer).flat());
}

async function refreshStreamFrames(commands: AgentCommand[]) {
  const streamCommands = commands.filter((command) => command.stream);
  const entries = await Promise.all(
    streamCommands.map(async (command) => {
      try {
        const response = await listCommandStreamFrames(command.server_id, command.id);
        return [command.id, response.frames] as const;
      } catch {
        return [command.id, []] as const;
      }
    }),
  );
  streamFramesByCommand.value = Object.fromEntries(entries);
}

function syncCommandTarget(nextServers: ServerSummary[]) {
  if (nextServers.length === 0) {
    commandForm.server_id = "";
    return;
  }
  const stillPresent = nextServers.some((server) => server.id === commandForm.server_id);
  if (!stillPresent) {
    commandForm.server_id = nextServers[0].id;
  }
}

function syncMetadataTarget(nextServers: ServerSummary[]) {
  if (nextServers.length === 0) {
    metadataForm.server_id = "";
    fillProbeMetadataFromServer(null);
    return;
  }
  const target =
    nextServers.find((server) => server.id === metadataForm.server_id) ?? nextServers[0];
  metadataForm.server_id = target.id;
  fillProbeMetadataFromServer(target);
}

async function submitServer() {
  const name = form.name.trim();
  if (!name) {
    errorMessage.value = "Server name is required.";
    return;
  }

  saving.value = true;
  errorMessage.value = "";
  try {
    const response = await createServer({
      ...form,
      name,
      ip_address: blankToNull(form.ip_address),
      ip_address_v6: blankToNull(form.ip_address_v6),
      domain: blankToNull(form.domain),
      domain_v6: blankToNull(form.domain_v6),
      region: blankToNull(form.region),
      region_country: blankToNull(form.region_country),
      region_name: blankToNull(form.region_name),
      region_city: blankToNull(form.region_city),
      provider_name: blankToNull(form.provider_name),
      provider_url: blankToNull(form.provider_url),
      expires_at: dateToUtcDateTime(form.expires_at),
      renewal_price: numberToNull(form.renewal_price),
      renewal_price_cny: numberToNull(form.renewal_price_cny),
      renewal_cycle: form.renewal_cycle ?? null,
      renewal_currency: blankToNull(form.renewal_currency),
      telecom_paid_peer: form.telecom_paid_peer ?? null,
    });
    latestToken.value = {
      serverName: response.server.name,
      token: response.agent_token,
    };
    resetForm();
    await refreshServers();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    saving.value = false;
  }
}

async function saveProbeMetadata() {
  if (!metadataForm.server_id) {
    errorMessage.value = "Target server is required.";
    return;
  }

  savingMetadata.value = true;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const response = await updateServerProbeMetadata(
      metadataForm.server_id,
      probeMetadataPayload(),
    );
    servers.value = servers.value.map((server) =>
      server.id === response.server.id ? response.server : server,
    );
    fillProbeMetadataFromServer(response.server);
    successMessage.value = "Probe metadata saved.";
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingMetadata.value = false;
  }
}

async function submitCommand() {
  if (!commandForm.server_id) {
    errorMessage.value = "Target server is required.";
    return;
  }

  const path = commandForm.path.trim();
  if (!path) {
    errorMessage.value = "Command path is required.";
    return;
  }

  let body: unknown;
  if (commandForm.bodyText.trim()) {
    try {
      body = JSON.parse(commandForm.bodyText);
    } catch {
      errorMessage.value = "Command body must be valid JSON.";
      return;
    }
  }

  savingCommand.value = true;
  errorMessage.value = "";
  try {
    await createServerCommand(commandForm.server_id, {
      method: commandForm.method,
      path,
      query: commandForm.query.trim(),
      body,
      timeout_ms: commandForm.timeout_ms,
      stream: commandForm.stream,
    });
    commandForm.bodyText = "";
    await refreshCommands(servers.value);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingCommand.value = false;
  }
}

async function queueQuickOperation(kind: SimpleAgentOperation) {
  if (!commandForm.server_id) {
    errorMessage.value = "Target server is required.";
    return;
  }

  savingOperation.value = kind;
  errorMessage.value = "";
  try {
    await queueAgentOperation(commandForm.server_id, kind);
    await refreshCommands(servers.value);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingOperation.value = "";
  }
}

async function queuePayloadOperation(
  kind: PayloadAgentOperation,
  payload: AgentOperationPayload,
) {
  if (!commandForm.server_id) {
    errorMessage.value = "Target server is required.";
    return false;
  }

  savingOperation.value = kind;
  errorMessage.value = "";
  try {
    await queueAgentOperation(commandForm.server_id, kind, payload);
    await refreshCommands(servers.value);
    return true;
  } catch (error) {
    errorMessage.value = readableError(error);
    return false;
  } finally {
    savingOperation.value = "";
  }
}

async function queueServiceRestart(service: AgentServiceName) {
  await queuePayloadOperation("service_control", { service, action: "restart" });
}

async function queueLogs(service: AgentLogService) {
  await queuePayloadOperation("logs", { service, lines: 200 });
}

async function purgeLogFiles() {
  const name = logFilesForm.name.trim();
  if (!logFilesForm.all && !name) {
    errorMessage.value = "Log file name is required.";
    return;
  }

  const queued = await queuePayloadOperation(
    "log_files_delete",
    logFilesForm.all ? { all: true } : { name },
  );
  if (queued && !logFilesForm.all) {
    logFilesForm.name = "";
  }
}

async function clearNginxStreamPort() {
  const port = Number(nginxToolsForm.stream_port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    errorMessage.value = "Stream port must be between 1 and 65535.";
    return;
  }
  await queuePayloadOperation("nginx_clear_stream_port", { port });
}

async function submitWarpLicense() {
  const license = agentSettingsForm.warp_license.trim();
  if (!license) {
    errorMessage.value = "WARP credential is required.";
    return;
  }
  const queued = await queuePayloadOperation("warp_license", { license });
  if (queued) {
    agentSettingsForm.warp_license = "";
  }
}

async function switchAgentXrayMode() {
  await queuePayloadOperation("agent_switch_xray_mode", {
    xray_mode: agentSettingsForm.xray_mode,
  });
}

async function switchAgentListenPort() {
  await queuePayloadOperation("agent_switch_listen_port", {
    listen_port: agentSettingsForm.listen_port,
  });
}

async function probeMasterUrl() {
  const masterUrl = agentSettingsForm.master_url.trim();
  if (!masterUrl) {
    errorMessage.value = "Master URL is required.";
    return;
  }
  await queuePayloadOperation("agent_probe_master_url", { master_url: masterUrl });
}

async function updateMasterUrl() {
  const masterUrl = agentSettingsForm.master_url.trim();
  if (!masterUrl) {
    errorMessage.value = "Master URL is required.";
    return;
  }
  await queuePayloadOperation("agent_update_master_url", {
    master_url: masterUrl,
    only_if_recovery: agentSettingsForm.only_if_recovery,
  });
}

async function submitDomainLatency() {
  if (!commandForm.server_id) {
    errorMessage.value = "Target server is required.";
    return;
  }

  const domains = parseDomainTargets(domainLatencyForm.domainsText);
  if (domains.length === 0) {
    errorMessage.value = "At least one latency target is required.";
    return;
  }

  savingOperation.value = "domain_latency";
  errorMessage.value = "";
  try {
    await queueAgentOperation(commandForm.server_id, "domain_latency", {
      domains,
      timeout_ms: domainLatencyForm.timeout_ms,
      allow_icmp: domainLatencyForm.allow_icmp,
    });
    domainLatencyForm.domainsText = "";
    await refreshCommands(servers.value);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingOperation.value = "";
  }
}

function resetForm() {
  Object.assign(form, defaultServerCreateRequest());
}

function fillProbeMetadataFromServer(server: ServerSummary | null = selectedMetadataServer.value) {
  if (!server) {
    metadataForm.region = "";
    metadataForm.region_country = "";
    metadataForm.region_name = "";
    metadataForm.region_city = "";
    metadataForm.provider_name = "";
    metadataForm.provider_url = "";
    metadataForm.expires_at = "";
    metadataForm.renewal_price = null;
    metadataForm.renewal_price_cny = null;
    metadataForm.renewal_cycle = null;
    metadataForm.renewal_currency = "";
    metadataForm.telecom_paid_peer = null;
    return;
  }
  metadataForm.server_id = server.id;
  metadataForm.region = server.region ?? "";
  metadataForm.region_country = server.region_country ?? "";
  metadataForm.region_name = server.region_name ?? "";
  metadataForm.region_city = server.region_city ?? "";
  metadataForm.provider_name = server.provider_name ?? "";
  metadataForm.provider_url = server.provider_url ?? "";
  metadataForm.expires_at = server.expires_at?.slice(0, 10) ?? "";
  metadataForm.renewal_price = server.renewal_price ?? null;
  metadataForm.renewal_price_cny = server.renewal_price_cny ?? null;
  metadataForm.renewal_cycle = server.renewal_cycle ?? null;
  metadataForm.renewal_currency = server.renewal_currency ?? "";
  metadataForm.telecom_paid_peer = server.telecom_paid_peer ?? null;
}

function probeMetadataPayload(): ServerProbeMetadataUpdate {
  return {
    region: blankToNull(metadataForm.region),
    region_country: blankToNull(metadataForm.region_country),
    region_name: blankToNull(metadataForm.region_name),
    region_city: blankToNull(metadataForm.region_city),
    provider_name: blankToNull(metadataForm.provider_name),
    provider_url: blankToNull(metadataForm.provider_url),
    expires_at: dateToUtcDateTime(metadataForm.expires_at),
    renewal_price: numberToNull(metadataForm.renewal_price),
    renewal_price_cny: numberToNull(metadataForm.renewal_price_cny),
    renewal_cycle: metadataForm.renewal_cycle,
    renewal_currency: blankToNull(metadataForm.renewal_currency),
    telecom_paid_peer: metadataForm.telecom_paid_peer,
  };
}

function selectMetadataServer(serverId: unknown) {
  metadataForm.server_id = typeof serverId === "string" ? serverId : "";
  fillProbeMetadataFromServer(selectedMetadataServer.value);
}

function parseDomainTargets(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function blankToNull(value: string | null | undefined) {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function dateToUtcDateTime(value: string | null | undefined) {
  const trimmed = value?.trim();
  return trimmed ? `${trimmed}T00:00:00Z` : null;
}

function numberToNull(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function readableError(error: unknown) {
  return error instanceof Error ? error.message : "Request failed.";
}

function formatBytesPerSecond(value: number) {
  if (value < 1024) {
    return `${value} B/s`;
  }
  const units = ["KB/s", "MB/s", "GB/s", "TB/s"];
  let scaled = value / 1024;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function endpointFor(server: ServerSummary) {
  return server.domain ?? server.ip_address ?? server.domain_v6 ?? server.ip_address_v6 ?? "Unassigned";
}

function probeRegionSummary(server: ServerSummary) {
  const region = server.region_city ?? server.region_name ?? server.region ?? null;
  const country = server.region_country;
  return [region, country].filter(Boolean).join(", ") || "No probe region";
}

function providerSummary(server: ServerSummary) {
  return server.provider_name ?? "No provider";
}

function renewalSummary(server: ServerSummary) {
  const parts: string[] = [];
  if (server.expires_at) {
    parts.push(`expires ${server.expires_at.slice(0, 10)}`);
  }
  if (server.renewal_price !== null && server.renewal_price !== undefined) {
    parts.push(`${server.renewal_price} ${server.renewal_currency ?? ""}`.trim());
  }
  if (server.renewal_cycle) {
    parts.push(renewalCycleLabel(server.renewal_cycle));
  }
  return parts.join(" / ") || "No renewal";
}

function renewalCycleLabel(cycle: RenewalCycle) {
  return renewalCycleOptions.find((option) => option.value === cycle)?.title ?? cycle;
}

function telemetryFor(server: ServerSummary) {
  return telemetryByServer.value[server.id] ?? null;
}

function scanFor(server: ServerSummary) {
  return scanResultsByServer.value[server.id] ?? null;
}

function scanStatusLabel(server: ServerSummary) {
  const scan = scanFor(server);
  if (!scan) {
    return "No scan";
  }
  return scan.xray_running ? "Running" : "Stopped";
}

function scanStatusColor(server: ServerSummary) {
  const scan = scanFor(server);
  if (!scan) {
    return "grey";
  }
  return scan.xray_running ? "success" : "warning";
}

function scanStatusIcon(server: ServerSummary) {
  const scan = scanFor(server);
  if (!scan) {
    return "mdi-radar";
  }
  return scan.xray_running ? "mdi-check-network-outline" : "mdi-alert-circle-outline";
}

function scanSummary(server: ServerSummary) {
  const scan = scanFor(server);
  if (!scan) {
    return "Scan not reported";
  }
  const parts = [
    scan.xray_version ?? "",
    `${scan.inbounds.length} inbound${scan.inbounds.length === 1 ? "" : "s"}`,
    scan.api_port ? `api ${scan.api_port}` : "",
  ].filter(Boolean);
  if (parts.length > 0) {
    return parts.join(" / ");
  }
  return scan.message ? truncateText(scan.message, 80) : "No scan details";
}

function systemSummary(server: ServerSummary) {
  const telemetry = telemetryFor(server);
  const metrics = telemetry?.sysmetrics;
  if (!metrics) {
    return "No telemetry";
  }
  const cpu = metrics.has_cpu ? `${metrics.cpu_pct.toFixed(1)}% CPU` : "CPU n/a";
  const mem =
    metrics.has_mem && metrics.mem_total > 0
      ? `${formatPercent(metrics.mem_used, metrics.mem_total)} mem`
      : "mem n/a";
  return `${cpu}, ${mem}`;
}

function latencySummary(server: ServerSummary) {
  const latency = telemetryFor(server)?.latency ?? [];
  if (latency.length === 0) {
    return "No probe";
  }
  const ok = latency.filter((sample) => sample.success);
  if (ok.length === 0) {
    return "probe failed";
  }
  const avg = ok.reduce((sum, sample) => sum + sample.latency_ms, 0) / ok.length;
  return `${avg.toFixed(0)} ms avg`;
}

function formatPercent(used: number, total: number) {
  return `${((used / total) * 100).toFixed(0)}%`;
}

function truncateText(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}...` : value;
}

</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">MMWX refactor</div>
        <h1 class="page-title">Open Node control plane</h1>
        <p class="page-copy">
          Server inventory is now backed by SQLite and issues agent bootstrap
          tokens without any license gate.
        </p>
      </div>

      <v-tooltip text="Refresh server inventory">
        <template #activator="{ props }">
          <v-btn
            v-bind="props"
            :loading="loading"
            icon="mdi-refresh"
            variant="text"
            @click="refreshServers"
          />
        </template>
      </v-tooltip>
    </section>

    <section class="metric-grid" aria-label="Inventory status">
      <v-card
        v-for="tile in metrics"
        :key="tile.label"
        class="metric-card"
        variant="flat"
      >
        <v-icon :color="tile.color" :icon="tile.icon" size="28" />
        <div class="metric-label">{{ tile.label }}</div>
        <div class="metric-value">{{ tile.value }}</div>
        <div class="metric-note">{{ tile.note }}</div>
      </v-card>
    </section>

    <v-alert
      v-if="errorMessage"
      class="status-alert"
      density="comfortable"
      type="error"
      variant="tonal"
    >
      {{ errorMessage }}
    </v-alert>
    <v-alert
      v-if="successMessage"
      class="status-alert"
      density="comfortable"
      type="success"
      variant="tonal"
    >
      {{ successMessage }}
    </v-alert>

    <section class="inventory-layout">
      <v-sheet class="section-surface server-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Servers</div>
            <div class="section-subtitle">Agent-facing control-plane records</div>
          </div>
          <v-progress-circular
            v-if="loading"
            color="primary"
            indeterminate
            size="24"
            width="3"
          />
        </div>

        <v-table v-if="!emptyState" class="server-table" density="comfortable">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Endpoint</th>
              <th>Probe</th>
              <th>Telemetry</th>
              <th>Xray</th>
              <th>Mode</th>
              <th class="number-cell">Port</th>
              <th class="number-cell">Up</th>
              <th class="number-cell">Down</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="server in servers" :key="server.id">
              <td>
                <div class="server-name">{{ server.name }}</div>
                <div class="server-subline">{{ server.xray_mode }} xray</div>
              </td>
              <td>
                <v-chip
                  :color="statusMeta[server.status].color"
                  :prepend-icon="statusMeta[server.status].icon"
                  density="comfortable"
                  size="small"
                  variant="tonal"
                >
                  {{ statusMeta[server.status].label }}
                </v-chip>
              </td>
              <td>{{ endpointFor(server) }}</td>
              <td class="telemetry-cell">
                <div class="server-name">{{ probeRegionSummary(server) }}</div>
                <div class="server-subline">
                  {{ providerSummary(server) }} / {{ renewalSummary(server) }}
                </div>
              </td>
              <td class="telemetry-cell">
                <div class="server-name">{{ systemSummary(server) }}</div>
                <div class="server-subline">{{ latencySummary(server) }}</div>
              </td>
              <td class="scan-cell">
                <v-chip
                  :color="scanStatusColor(server)"
                  :prepend-icon="scanStatusIcon(server)"
                  density="comfortable"
                  size="small"
                  variant="tonal"
                >
                  {{ scanStatusLabel(server) }}
                </v-chip>
                <div class="server-subline">{{ scanSummary(server) }}</div>
              </td>
              <td>{{ server.connection_mode }}</td>
              <td class="number-cell">{{ server.listen_port }}</td>
              <td class="number-cell">
                {{ formatBytesPerSecond(server.current_upload_speed) }}
              </td>
              <td class="number-cell">
                {{ formatBytesPerSecond(server.current_download_speed) }}
              </td>
            </tr>
          </tbody>
        </v-table>

        <div v-else class="empty-state">
          <v-icon color="secondary" icon="mdi-server-network-off" size="36" />
          <div>No servers yet.</div>
        </div>
      </v-sheet>

      <v-sheet class="section-surface create-panel" border>
        <div class="section-title">New server</div>
        <v-form class="server-form" @submit.prevent="submitServer">
          <v-text-field
            v-model="form.name"
            density="comfortable"
            label="Name"
            prepend-inner-icon="mdi-server"
            variant="outlined"
          />
          <v-text-field
            v-model="form.ip_address"
            density="comfortable"
            label="IPv4"
            prepend-inner-icon="mdi-ip-network"
            variant="outlined"
          />
          <div class="form-row">
            <v-select
              v-model="form.connection_mode"
              :items="connectionModes"
              density="comfortable"
              label="Connection"
              variant="outlined"
            />
            <v-text-field
              v-model.number="form.listen_port"
              density="comfortable"
              label="Port"
              min="0"
              max="65535"
              type="number"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-select
              v-model="form.xray_mode"
              :items="xrayModes"
              density="comfortable"
              label="Xray"
              variant="outlined"
            />
            <v-text-field
              v-model.number="form.traffic_limit"
              density="comfortable"
              label="Traffic limit"
              min="0"
              type="number"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="form.region_city"
              density="comfortable"
              label="Probe city"
              prepend-inner-icon="mdi-map-marker-outline"
              variant="outlined"
            />
            <v-text-field
              v-model="form.provider_name"
              density="comfortable"
              label="Provider"
              prepend-inner-icon="mdi-cloud-outline"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="form.expires_at"
              density="comfortable"
              label="Expires"
              prepend-inner-icon="mdi-calendar-outline"
              type="date"
              variant="outlined"
            />
            <v-text-field
              v-model.number="form.renewal_price"
              density="comfortable"
              label="Renewal"
              min="0"
              type="number"
              variant="outlined"
            />
          </div>
          <v-switch
            v-model="form.ipv6_enabled"
            color="primary"
            density="comfortable"
            hide-details
            label="IPv6"
          />
          <v-btn
            :loading="saving"
            block
            color="primary"
            prepend-icon="mdi-plus"
            type="submit"
            variant="flat"
          >
            Create server
          </v-btn>
        </v-form>

        <v-alert
          v-if="latestToken"
          class="token-alert"
          color="success"
          density="comfortable"
          icon="mdi-key-variant"
          variant="tonal"
        >
          <div class="token-label">{{ latestToken.serverName }} agent token</div>
          <code class="token-code">{{ latestToken.token }}</code>
        </v-alert>

        <v-divider class="command-divider" />

        <div class="section-title">Probe metadata</div>
        <v-form class="server-form" @submit.prevent="saveProbeMetadata">
          <v-select
            v-model="metadataForm.server_id"
            :disabled="serverOptions.length === 0"
            :items="serverOptions"
            density="comfortable"
            label="Server"
            prepend-inner-icon="mdi-server-network"
            variant="outlined"
            @update:model-value="selectMetadataServer"
          />
          <div class="form-row">
            <v-text-field
              v-model="metadataForm.region"
              density="comfortable"
              label="Region code"
              prepend-inner-icon="mdi-map-marker-radius-outline"
              variant="outlined"
            />
            <v-text-field
              v-model="metadataForm.region_country"
              density="comfortable"
              label="Country"
              prepend-inner-icon="mdi-flag-outline"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="metadataForm.region_name"
              density="comfortable"
              label="Region"
              prepend-inner-icon="mdi-map-outline"
              variant="outlined"
            />
            <v-text-field
              v-model="metadataForm.region_city"
              density="comfortable"
              label="City"
              prepend-inner-icon="mdi-city-variant-outline"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="metadataForm.provider_name"
              density="comfortable"
              label="Provider"
              prepend-inner-icon="mdi-cloud-outline"
              variant="outlined"
            />
            <v-text-field
              v-model="metadataForm.provider_url"
              density="comfortable"
              label="Provider URL"
              prepend-inner-icon="mdi-link-variant"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="metadataForm.expires_at"
              density="comfortable"
              label="Expires"
              prepend-inner-icon="mdi-calendar-outline"
              type="date"
              variant="outlined"
            />
            <v-select
              v-model="metadataForm.renewal_cycle"
              :items="renewalCycleOptions"
              clearable
              density="comfortable"
              label="Cycle"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model.number="metadataForm.renewal_price"
              density="comfortable"
              label="Renewal price"
              min="0"
              type="number"
              variant="outlined"
            />
            <v-text-field
              v-model.number="metadataForm.renewal_price_cny"
              density="comfortable"
              label="CNY price"
              min="0"
              type="number"
              variant="outlined"
            />
          </div>
          <div class="form-row">
            <v-text-field
              v-model="metadataForm.renewal_currency"
              density="comfortable"
              label="Currency"
              prepend-inner-icon="mdi-cash"
              variant="outlined"
            />
            <v-select
              v-model="metadataForm.telecom_paid_peer"
              :items="paidPeerOptions"
              density="comfortable"
              label="Telecom peer"
              variant="outlined"
            />
          </div>
          <div class="settings-action-row">
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingMetadata"
              color="primary"
              prepend-icon="mdi-content-save-outline"
              type="submit"
              variant="flat"
            >
              Save metadata
            </v-btn>
            <v-btn
              :disabled="serverOptions.length === 0"
              prepend-icon="mdi-refresh"
              variant="tonal"
              @click="fillProbeMetadataFromServer()"
            >
              Reload
            </v-btn>
          </div>
        </v-form>

        <v-divider class="command-divider" />

        <div class="section-title">Command queue</div>
        <div class="quick-command-grid">
          <v-btn
            v-for="operation in quickOperations"
            :key="operation.kind"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === operation.kind"
            :prepend-icon="operation.icon"
            color="primary"
            size="small"
            variant="tonal"
            @click="queueQuickOperation(operation.kind)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <div class="section-subtitle operation-subtitle">Diagnostics</div>
        <div class="diagnostic-command-grid">
          <v-btn
            v-for="operation in diagnosticOperations"
            :key="operation.kind"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === operation.kind"
            :prepend-icon="operation.icon"
            color="info"
            size="small"
            variant="tonal"
            @click="queueQuickOperation(operation.kind)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <div class="section-subtitle operation-subtitle">Maintenance workflows</div>
        <div class="maintenance-command-grid">
          <v-btn
            v-for="operation in maintenanceOperations"
            :key="operation.kind"
            :color="operation.color"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === operation.kind"
            :prepend-icon="operation.icon"
            size="small"
            variant="tonal"
            @click="queueQuickOperation(operation.kind)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <div class="section-subtitle operation-subtitle">Nginx stream cleanup</div>
        <v-form class="server-form compact-form" @submit.prevent="clearNginxStreamPort">
          <div class="form-row">
            <v-text-field
              v-model.number="nginxToolsForm.stream_port"
              density="comfortable"
              label="Stream port"
              min="1"
              max="65535"
              prepend-inner-icon="mdi-lan"
              type="number"
              variant="outlined"
            />
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'nginx_clear_stream_port'"
              color="warning"
              prepend-icon="mdi-broom"
              size="small"
              type="submit"
              variant="tonal"
            >
              Clear stream
            </v-btn>
          </div>
        </v-form>
        <div class="section-subtitle operation-subtitle">Service control</div>
        <div class="service-command-grid">
          <v-btn
            v-for="operation in serviceControlOperations"
            :key="operation.service"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === 'service_control'"
            :prepend-icon="operation.icon"
            color="warning"
            size="small"
            variant="tonal"
            @click="queueServiceRestart(operation.service)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <div class="section-subtitle operation-subtitle">Logs</div>
        <div class="log-command-grid">
          <v-btn
            v-for="operation in logOperations"
            :key="operation.service"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === 'logs'"
            :prepend-icon="operation.icon"
            color="info"
            size="small"
            variant="tonal"
            @click="queueLogs(operation.service)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <v-form class="server-form compact-form" @submit.prevent="purgeLogFiles">
          <div class="form-row">
            <v-text-field
              v-model="logFilesForm.name"
              :disabled="logFilesForm.all"
              density="comfortable"
              label="Log file"
              prepend-inner-icon="mdi-file-document-outline"
              variant="outlined"
            />
            <v-switch
              v-model="logFilesForm.all"
              color="error"
              density="comfortable"
              hide-details
              label="All files"
            />
          </div>
          <v-btn
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === 'log_files_delete'"
            color="error"
            prepend-icon="mdi-delete-sweep-outline"
            type="submit"
            variant="tonal"
          >
            Purge logs
          </v-btn>
        </v-form>
        <div class="section-subtitle operation-subtitle">Config reads</div>
        <div class="config-command-grid">
          <v-btn
            v-for="operation in configReadOperations"
            :key="operation.kind"
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === operation.kind"
            :prepend-icon="operation.icon"
            color="secondary"
            size="small"
            variant="tonal"
            @click="queueQuickOperation(operation.kind)"
          >
            {{ operation.title }}
          </v-btn>
        </div>
        <div class="section-subtitle operation-subtitle">Agent settings</div>
        <v-form class="server-form compact-form" @submit.prevent="updateMasterUrl">
          <div class="form-row">
            <v-select
              v-model="agentSettingsForm.xray_mode"
              :items="xrayModes"
              density="comfortable"
              label="Xray mode"
              variant="outlined"
            />
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'agent_switch_xray_mode'"
              color="warning"
              prepend-icon="mdi-swap-horizontal"
              size="small"
              variant="tonal"
              @click="switchAgentXrayMode"
            >
              Switch
            </v-btn>
          </div>
          <div class="form-row">
            <v-text-field
              v-model.number="agentSettingsForm.listen_port"
              density="comfortable"
              label="Listen port"
              min="0"
              max="65535"
              type="number"
              variant="outlined"
            />
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'agent_switch_listen_port'"
              color="warning"
              prepend-icon="mdi-lan"
              size="small"
              variant="tonal"
              @click="switchAgentListenPort"
            >
              Apply
            </v-btn>
          </div>
          <v-text-field
            v-model="agentSettingsForm.master_url"
            density="comfortable"
            label="Master URL"
            prepend-inner-icon="mdi-link-variant"
            variant="outlined"
          />
          <v-switch
            v-model="agentSettingsForm.only_if_recovery"
            color="primary"
            density="comfortable"
            hide-details
            label="Recovery only"
          />
          <div class="settings-action-row">
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'agent_probe_master_url'"
              color="info"
              prepend-icon="mdi-access-point-network"
              size="small"
              variant="tonal"
              @click="probeMasterUrl"
            >
              Probe
            </v-btn>
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'agent_update_master_url'"
              color="warning"
              prepend-icon="mdi-link-lock"
              size="small"
              type="submit"
              variant="tonal"
            >
              Update
            </v-btn>
          </div>
          <div class="form-row">
            <v-text-field
              v-model="agentSettingsForm.warp_license"
              density="comfortable"
              label="WARP credential"
              prepend-inner-icon="mdi-key-variant"
              type="password"
              variant="outlined"
            />
            <v-btn
              :disabled="serverOptions.length === 0"
              :loading="savingOperation === 'warp_license'"
              color="info"
              prepend-icon="mdi-cloud-key-outline"
              size="small"
              variant="tonal"
              @click="submitWarpLicense"
            >
              Set WARP
            </v-btn>
          </div>
        </v-form>
        <v-form class="server-form" @submit.prevent="submitDomainLatency">
          <v-textarea
            v-model="domainLatencyForm.domainsText"
            auto-grow
            density="comfortable"
            label="Latency targets"
            prepend-inner-icon="mdi-crosshairs-gps"
            rows="2"
            variant="outlined"
          />
          <div class="form-row">
            <v-text-field
              v-model.number="domainLatencyForm.timeout_ms"
              density="comfortable"
              label="Timeout"
              min="200"
              max="10000"
              type="number"
              variant="outlined"
            />
            <v-switch
              v-model="domainLatencyForm.allow_icmp"
              color="primary"
              density="comfortable"
              hide-details
              label="ICMP"
            />
          </div>
          <v-btn
            :disabled="serverOptions.length === 0"
            :loading="savingOperation === 'domain_latency'"
            block
            color="primary"
            prepend-icon="mdi-radar"
            type="submit"
            variant="tonal"
          >
            Queue latency probe
          </v-btn>
        </v-form>
        <v-divider class="command-divider" />

        <v-form class="server-form" @submit.prevent="submitCommand">
          <v-select
            v-model="commandForm.server_id"
            :disabled="serverOptions.length === 0"
            :items="serverOptions"
            density="comfortable"
            label="Target server"
            prepend-inner-icon="mdi-server-network"
            variant="outlined"
          />
          <div class="form-row">
            <v-select
              v-model="commandForm.method"
              :items="commandMethods"
              density="comfortable"
              label="Method"
              variant="outlined"
            />
            <v-text-field
              v-model.number="commandForm.timeout_ms"
              density="comfortable"
              label="Timeout"
              min="1000"
              max="300000"
              type="number"
              variant="outlined"
            />
          </div>
          <v-text-field
            v-model="commandForm.path"
            density="comfortable"
            label="Path"
            prepend-inner-icon="mdi-api"
            variant="outlined"
          />
          <v-text-field
            v-model="commandForm.query"
            density="comfortable"
            label="Query"
            prepend-inner-icon="mdi-tune"
            variant="outlined"
          />
          <v-textarea
            v-model="commandForm.bodyText"
            auto-grow
            density="comfortable"
            label="JSON body"
            rows="2"
            variant="outlined"
          />
          <v-switch
            v-model="commandForm.stream"
            color="primary"
            density="comfortable"
            hide-details
            label="Stream"
          />
          <v-btn
            :disabled="serverOptions.length === 0"
            :loading="savingCommand"
            block
            color="secondary"
            prepend-icon="mdi-send"
            type="submit"
            variant="flat"
          >
            Queue command
          </v-btn>
        </v-form>

        <CommandInspector
          class="command-list"
          :commands="selectedCommands"
          :stream-frames-by-command="streamFramesByCommand"
        />
      </v-sheet>
    </section>
  </div>
</template>
