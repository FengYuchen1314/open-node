<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import {
  defaultServerCreateRequest,
  type ConnectionMode,
  type ServerCreateRequest,
  type ServerStatus,
  type ServerSummary,
  type XrayMode,
} from "../domain/inventory";
import { createServer, listServers } from "../services/inventory";

const servers = ref<ServerSummary[]>([]);
const loading = ref(false);
const saving = ref(false);
const errorMessage = ref("");
const latestToken = ref<{ serverName: string; token: string } | null>(null);
const form = reactive<ServerCreateRequest>(defaultServerCreateRequest());

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
    note: "Upload and download heartbeat totals",
    icon: "mdi-speedometer",
    color: "info",
  },
]);
const emptyState = computed(() => !loading.value && servers.value.length === 0);

onMounted(() => {
  void refreshServers();
});

async function refreshServers() {
  loading.value = true;
  errorMessage.value = "";
  try {
    servers.value = await listServers();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
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

function resetForm() {
  Object.assign(form, defaultServerCreateRequest());
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
      </v-sheet>
    </section>
  </div>
</template>
