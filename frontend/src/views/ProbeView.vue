<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import type { ProbePayload, ProbeServer } from "../domain/probe";
import { getPublicProbePayload, getPublicProbeStreamUrl } from "../services/probe";

const payload = ref<ProbePayload | null>(null);
const loading = ref(false);
const errorMessage = ref("");
const streamActive = ref(false);
let probeStream: WebSocket | undefined;

const servers = computed(() => payload.value?.servers ?? []);
const onlineCount = computed(() => servers.value.filter((server) => server.online).length);
const totalUpload = computed(() =>
  servers.value.reduce((sum, server) => sum + (server.upload_speed ?? 0), 0),
);
const totalDownload = computed(() =>
  servers.value.reduce((sum, server) => sum + (server.download_speed ?? 0), 0),
);
const metrics = computed(() => [
  {
    label: "Public Nodes",
    value: servers.value.length.toString(),
    note: probeEndpointNote(),
    icon: "mdi-access-point-network",
    color: "primary",
  },
  {
    label: "Online",
    value: onlineCount.value.toString(),
    note: `${servers.value.length - onlineCount.value} offline`,
    icon: "mdi-lan-check",
    color: "success",
  },
  {
    label: "Throughput",
    value: `${formatBytesPerSecond(totalUpload.value)} / ${formatBytesPerSecond(totalDownload.value)}`,
    note: "Upload / download",
    icon: "mdi-chart-timeline-variant",
    color: "info",
  },
]);

onMounted(() => {
  void refreshProbe();
  openProbeStream();
});

onUnmounted(() => {
  probeStream?.close();
  probeStream = undefined;
});

async function refreshProbe() {
  loading.value = true;
  errorMessage.value = "";
  try {
    acceptProbe(await getPublicProbePayload());
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "Probe request failed.";
  } finally {
    loading.value = false;
  }
}

function acceptProbe(nextPayload: ProbePayload) {
  payload.value = nextPayload;
  errorMessage.value = "";
}

function probeEndpointNote() {
  if (streamActive.value) {
    return "Live stream connected";
  }
  return payload.value?.enabled ? "Probe endpoint enabled" : "Probe endpoint disabled";
}

function openProbeStream() {
  if (typeof WebSocket === "undefined") {
    return;
  }
  try {
    probeStream = new WebSocket(getPublicProbeStreamUrl(window.location));
    probeStream.onopen = () => {
      streamActive.value = true;
    };
    probeStream.onmessage = (event) => {
      try {
        acceptProbe(JSON.parse(event.data as string) as ProbePayload);
      } catch {
        // Ignore malformed stream frames; the next snapshot replaces the page state.
      }
    };
    probeStream.onerror = () => {
      streamActive.value = false;
    };
    probeStream.onclose = () => {
      streamActive.value = false;
      probeStream = undefined;
    };
  } catch {
    streamActive.value = false;
  }
}

function displayName(server: ProbeServer, index: number) {
  return server.name || `Node ${index + 1}`;
}

function statusColor(server: ProbeServer) {
  return server.online ? "success" : "error";
}

function statusIcon(server: ProbeServer) {
  return server.online ? "mdi-check-network-outline" : "mdi-network-off-outline";
}

function statusLabel(server: ProbeServer) {
  return server.online ? "Online" : "Offline";
}

function systemSummary(server: ProbeServer) {
  const cpu = server.cpu_pct !== undefined && server.cpu_pct !== null
    ? `${server.cpu_pct.toFixed(1)}% CPU`
    : "CPU n/a";
  const memory =
    server.mem_used !== undefined && server.mem_used !== null && server.mem_total
      ? `${formatPercent(server.mem_used, server.mem_total)} mem`
      : "mem n/a";
  return `${cpu}, ${memory}`;
}

function latencySummary(server: ProbeServer) {
  const ping = server.ping ?? [];
  if (ping.length === 0) {
    return "No probe";
  }
  const live = ping.filter((series) => series.current_ms >= 0);
  if (live.length === 0) {
    return "probe failed";
  }
  const average = live.reduce((sum, series) => sum + series.current_ms, 0) / live.length;
  return `${average.toFixed(0)} ms avg`;
}

function trafficSummary(server: ProbeServer) {
  const used = server.traffic_used_total ?? server.traffic_used;
  if (used === undefined || used === null) {
    return "No traffic";
  }
  if (server.traffic_limit) {
    return `${formatBytes(used)} / ${formatBytes(server.traffic_limit)}`;
  }
  return formatBytes(used);
}

function formatPercent(used: number, total: number) {
  return `${((used / total) * 100).toFixed(0)}%`;
}

function formatBytesPerSecond(value: number) {
  return `${formatBytes(value)}/s`;
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}
</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">Public probe</div>
        <h1 class="page-title">{{ payload?.title ?? "Open Node Probe" }}</h1>
        <p class="page-copy">MMWX probe-compatible node status without license gates.</p>
      </div>

      <v-tooltip text="Refresh probe status">
        <template #activator="{ props }">
          <v-btn
            v-bind="props"
            :loading="loading"
            icon="mdi-refresh"
            variant="text"
            @click="refreshProbe"
          />
        </template>
      </v-tooltip>
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

    <section class="metric-grid" aria-label="Probe status">
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

    <v-sheet class="section-surface server-surface" border>
      <div class="section-head">
        <div>
          <div class="section-title">Probe nodes</div>
          <div class="section-subtitle">Public read-only view</div>
        </div>
        <v-progress-circular
          v-if="loading"
          color="primary"
          indeterminate
          size="24"
          width="3"
        />
      </div>

      <v-table v-if="servers.length > 0" class="server-table" density="comfortable">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>System</th>
            <th>Latency</th>
            <th>Traffic</th>
            <th class="number-cell">Up</th>
            <th class="number-cell">Down</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(server, index) in servers" :key="`${displayName(server, index)}-${index}`">
            <td>
              <div class="server-name">{{ displayName(server, index) }}</div>
              <div class="server-subline">{{ server.os || "Unknown OS" }}</div>
            </td>
            <td>
              <v-chip
                :color="statusColor(server)"
                :prepend-icon="statusIcon(server)"
                density="comfortable"
                size="small"
                variant="tonal"
              >
                {{ statusLabel(server) }}
              </v-chip>
            </td>
            <td class="telemetry-cell">
              <div class="server-name">{{ systemSummary(server) }}</div>
              <div class="server-subline">{{ server.loadavg || "No loadavg" }}</div>
            </td>
            <td>{{ latencySummary(server) }}</td>
            <td>{{ trafficSummary(server) }}</td>
            <td class="number-cell">{{ formatBytesPerSecond(server.upload_speed ?? 0) }}</td>
            <td class="number-cell">{{ formatBytesPerSecond(server.download_speed ?? 0) }}</td>
          </tr>
        </tbody>
      </v-table>

      <div v-else class="empty-state">
        <v-icon color="secondary" icon="mdi-chart-line-variant" size="36" />
        <div>No public probe nodes yet.</div>
      </div>
    </v-sheet>
  </div>
</template>
