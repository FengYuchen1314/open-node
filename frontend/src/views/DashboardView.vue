<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import {
  defaultServerCreateRequest,
  type AgentCommand,
  type AgentCommandStreamFrame,
  type AgentOperationKind,
  type AgentTelemetry,
  type ConnectionMode,
  type ServerCreateRequest,
  type ServerStatus,
  type ServerSummary,
  type XrayMode,
} from "../domain/inventory";
import {
  createServer,
  createServerCommand,
  getLatestTelemetry,
  listCommandStreamFrames,
  listServerCommands,
  listServers,
  queueAgentOperation,
} from "../services/inventory";

const servers = ref<ServerSummary[]>([]);
const telemetryByServer = ref<Record<string, AgentTelemetry | null>>({});
const commandsByServer = ref<Record<string, AgentCommand[]>>({});
const streamFramesByCommand = ref<Record<string, AgentCommandStreamFrame[]>>({});
const loading = ref(false);
const saving = ref(false);
const savingCommand = ref(false);
const savingOperation = ref<AgentOperationKind | "">("");
const errorMessage = ref("");
const latestToken = ref<{ serverName: string; token: string } | null>(null);
const form = reactive<ServerCreateRequest>(defaultServerCreateRequest());
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

const commandMethods = ["GET", "POST", "PUT", "PATCH", "DELETE"];
type SimpleAgentOperation = Exclude<AgentOperationKind, "domain_latency">;
const quickOperations: Array<{
  title: string;
  icon: string;
  kind: SimpleAgentOperation;
}> = [
  { title: "System info", icon: "mdi-monitor-dashboard", kind: "system_info" },
  { title: "Traffic", icon: "mdi-swap-vertical", kind: "traffic" },
  { title: "Speed", icon: "mdi-speedometer", kind: "speed" },
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

const statusMeta: Record<ServerStatus, { color: string; icon: string; label: string }> = {
  pending: { color: "warning", icon: "mdi-timer-sand", label: "Pending" },
  connected: { color: "success", icon: "mdi-lan-connect", label: "Connected" },
  offline: { color: "error", icon: "mdi-lan-disconnect", label: "Offline" },
};

const commandStatusMeta = {
  pending: { color: "warning", icon: "mdi-clock-outline" },
  leased: { color: "info", icon: "mdi-progress-clock" },
  succeeded: { color: "success", icon: "mdi-check-circle-outline" },
  failed: { color: "error", icon: "mdi-alert-circle-outline" },
} as const;

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
    await Promise.all([refreshTelemetry(nextServers), refreshCommands(nextServers)]);
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

function telemetryFor(server: ServerSummary) {
  return telemetryByServer.value[server.id] ?? null;
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

function commandSubtitle(command: AgentCommand) {
  const status = command.result_status ? `status ${command.result_status}` : "waiting";
  const frames = streamFramesByCommand.value[command.id] ?? [];
  const stream = command.stream ? `, ${frames.length} stream frames` : "";
  return `${command.attempts} attempts, ${status}${stream}`;
}

function latestStreamData(command: AgentCommand) {
  const frames = streamFramesByCommand.value[command.id] ?? [];
  const latest = frames[frames.length - 1]?.data.trim();
  if (!latest) {
    return "";
  }
  return latest.length > 180 ? `${latest.slice(0, 177)}...` : latest;
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
              <th>Telemetry</th>
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
                <div class="server-name">{{ systemSummary(server) }}</div>
                <div class="server-subline">{{ latencySummary(server) }}</div>
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

        <v-list v-if="selectedCommands.length > 0" class="command-list" density="compact">
          <v-list-item
            v-for="command in selectedCommands"
            :key="command.id"
            :subtitle="commandSubtitle(command)"
            :title="`${command.method} ${command.path}`"
          >
            <template #prepend>
              <v-icon
                :color="commandStatusMeta[command.status].color"
                :icon="commandStatusMeta[command.status].icon"
              />
            </template>
            <div v-if="latestStreamData(command)" class="command-stream-snippet">
              {{ latestStreamData(command) }}
            </div>
          </v-list-item>
        </v-list>
        <div v-else class="empty-command">No commands queued.</div>
      </v-sheet>
    </section>
  </div>
</template>
