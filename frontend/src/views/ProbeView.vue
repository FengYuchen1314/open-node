<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";

import type { ProbePayload, ProbeServer, ProbeSettingsUpdate } from "../domain/probe";
import {
  getPublicProbePayload,
  getPublicProbeStreamUrl,
  updatePublicProbeSettings,
} from "../services/probe";

const payload = ref<ProbePayload | null>(null);
const loading = ref(false);
const savingSettings = ref(false);
const errorMessage = ref("");
const successMessage = ref("");
const streamActive = ref(false);
let probeStream: WebSocket | undefined;

const settingsForm = reactive({
  enabled: true,
  title: "Open Node Probe",
  description: "MMWX probe-compatible node status without license gates.",
  logo: "",
  refresh_interval_sec: 5,
  theme: "open-node",
  color_mode: "light" as "light" | "dark" | "system",
  revision: "open-node",
  show_resource_heatmap: true,
  show_traffic_quota: true,
  show_daily_trend: false,
  show_traffic_7d: false,
  show_renewal_timeline: false,
  show_health_score: true,
});

const servers = computed(() => payload.value?.servers ?? []);
const probeDescription = computed(
  () => payload.value?.description ?? "MMWX probe-compatible node status without license gates.",
);
const showSystemColumn = computed(() => payload.value?.show_resource_heatmap !== false);
const showTrafficColumn = computed(() => payload.value?.show_traffic_quota !== false);
const showRenewalColumn = computed(() => payload.value?.show_renewal_timeline === true);
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
const colorModeOptions = [
  { title: "Light", value: "light" },
  { title: "Dark", value: "dark" },
  { title: "System", value: "system" },
];

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
  syncSettingsForm(nextPayload);
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

async function saveProbeSettings() {
  savingSettings.value = true;
  errorMessage.value = "";
  successMessage.value = "";
  try {
    await updatePublicProbeSettings(settingsPayload());
    await refreshProbe();
    successMessage.value = "Probe settings saved.";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "Probe settings failed.";
  } finally {
    savingSettings.value = false;
  }
}

function syncSettingsForm(nextPayload: ProbePayload) {
  settingsForm.enabled = nextPayload.enabled;
  settingsForm.title = nextPayload.title ?? "Open Node Probe";
  settingsForm.description =
    nextPayload.description ?? "MMWX probe-compatible node status without license gates.";
  settingsForm.logo = nextPayload.logo ?? "";
  settingsForm.refresh_interval_sec = nextPayload.refresh_interval_sec ?? 5;
  settingsForm.theme = nextPayload.appearance?.theme ?? "open-node";
  settingsForm.color_mode = nextPayload.appearance?.color_mode ?? "light";
  settingsForm.revision = nextPayload.appearance?.revision ?? "open-node";
  settingsForm.show_resource_heatmap = nextPayload.show_resource_heatmap !== false;
  settingsForm.show_traffic_quota = nextPayload.show_traffic_quota !== false;
  settingsForm.show_daily_trend = nextPayload.show_daily_trend === true;
  settingsForm.show_traffic_7d = nextPayload.show_traffic_7d === true;
  settingsForm.show_renewal_timeline = nextPayload.show_renewal_timeline === true;
  settingsForm.show_health_score = nextPayload.show_health_score !== false;
}

function settingsPayload(): ProbeSettingsUpdate {
  return {
    enabled: settingsForm.enabled,
    title: settingsForm.title,
    description: settingsForm.description,
    logo: settingsForm.logo,
    refresh_interval_sec: settingsForm.refresh_interval_sec,
    show_resource_heatmap: settingsForm.show_resource_heatmap,
    show_traffic_quota: settingsForm.show_traffic_quota,
    show_daily_trend: settingsForm.show_daily_trend,
    show_traffic_7d: settingsForm.show_traffic_7d,
    show_renewal_timeline: settingsForm.show_renewal_timeline,
    show_health_score: settingsForm.show_health_score,
    appearance: {
      theme: settingsForm.theme,
      color_mode: settingsForm.color_mode,
      revision: settingsForm.revision,
    },
  };
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

function regionSummary(server: ProbeServer) {
  const region = server.region_city ?? server.region_name ?? server.region ?? null;
  const country = server.region_country;
  return [region, country].filter(Boolean).join(", ") || "No region";
}

function providerSummary(server: ProbeServer) {
  return server.provider_name ?? "No provider";
}

function renewalSummary(server: ProbeServer) {
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

function renewalCycleLabel(cycle: NonNullable<ProbeServer["renewal_cycle"]>) {
  const labels: Record<NonNullable<ProbeServer["renewal_cycle"]>, string> = {
    month: "Month",
    quarter: "Quarter",
    half_year: "Half year",
    year: "Year",
  };
  return labels[cycle];
}

function peerSummary(server: ProbeServer) {
  if (server.telecom_paid_peer === null || server.telecom_paid_peer === undefined) {
    return "Peer unknown";
  }
  return server.telecom_paid_peer ? "Paid peer" : "Standard peer";
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
      <div class="probe-title-row">
        <v-avatar v-if="payload?.logo" class="probe-logo" rounded="lg" size="52">
          <v-img :src="payload.logo" alt="" cover />
        </v-avatar>
        <div>
          <div class="eyebrow">Public probe</div>
          <h1 class="page-title">{{ payload?.title ?? "Open Node Probe" }}</h1>
          <p class="page-copy">{{ probeDescription }}</p>
        </div>
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
    <v-alert
      v-if="successMessage"
      class="status-alert"
      density="comfortable"
      type="success"
      variant="tonal"
    >
      {{ successMessage }}
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

    <v-sheet class="section-surface" border>
      <div class="section-head">
        <div>
          <div class="section-title">Probe settings</div>
          <div class="section-subtitle">{{ settingsForm.revision }}</div>
        </div>
        <v-chip :color="settingsForm.enabled ? 'success' : 'error'" variant="tonal">
          {{ settingsForm.enabled ? "Enabled" : "Disabled" }}
        </v-chip>
      </div>

      <v-form class="compact-form" @submit.prevent="saveProbeSettings">
        <div class="form-row">
          <v-text-field
            v-model="settingsForm.title"
            density="comfortable"
            label="Title"
            prepend-inner-icon="mdi-format-title"
            variant="outlined"
          />
          <v-text-field
            v-model.number="settingsForm.refresh_interval_sec"
            density="comfortable"
            label="Refresh seconds"
            max="60"
            min="1"
            type="number"
            variant="outlined"
          />
        </div>
        <v-textarea
          v-model="settingsForm.description"
          auto-grow
          density="comfortable"
          label="Description"
          rows="2"
          variant="outlined"
        />
        <div class="form-row">
          <v-text-field
            v-model="settingsForm.logo"
            density="comfortable"
            label="Logo URL"
            prepend-inner-icon="mdi-image-outline"
            variant="outlined"
          />
          <v-text-field
            v-model="settingsForm.theme"
            density="comfortable"
            label="Theme"
            prepend-inner-icon="mdi-palette-outline"
            variant="outlined"
          />
        </div>
        <div class="form-row">
          <v-select
            v-model="settingsForm.color_mode"
            :items="colorModeOptions"
            density="comfortable"
            label="Color mode"
            variant="outlined"
          />
          <v-text-field
            v-model="settingsForm.revision"
            density="comfortable"
            label="Revision"
            prepend-inner-icon="mdi-source-commit"
            variant="outlined"
          />
        </div>
        <div class="probe-toggle-grid">
          <v-switch
            v-model="settingsForm.enabled"
            color="primary"
            density="comfortable"
            hide-details
            label="Enabled"
          />
          <v-switch
            v-model="settingsForm.show_resource_heatmap"
            color="secondary"
            density="comfortable"
            hide-details
            label="System"
          />
          <v-switch
            v-model="settingsForm.show_traffic_quota"
            color="info"
            density="comfortable"
            hide-details
            label="Traffic"
          />
          <v-switch
            v-model="settingsForm.show_health_score"
            color="success"
            density="comfortable"
            hide-details
            label="Health"
          />
          <v-switch
            v-model="settingsForm.show_daily_trend"
            color="warning"
            density="comfortable"
            hide-details
            label="Daily"
          />
          <v-switch
            v-model="settingsForm.show_traffic_7d"
            color="primary"
            density="comfortable"
            hide-details
            label="7d traffic"
          />
          <v-switch
            v-model="settingsForm.show_renewal_timeline"
            color="secondary"
            density="comfortable"
            hide-details
            label="Renewal"
          />
        </div>
        <div class="settings-action-row">
          <v-btn
            :loading="savingSettings"
            color="primary"
            prepend-icon="mdi-content-save-outline"
            type="submit"
            variant="flat"
          >
            Save settings
          </v-btn>
          <v-btn
            :loading="loading"
            prepend-icon="mdi-refresh"
            variant="tonal"
            @click="refreshProbe"
          >
            Refresh
          </v-btn>
        </div>
      </v-form>
    </v-sheet>

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
            <th v-if="showSystemColumn">System</th>
            <th>Latency</th>
            <th v-if="showTrafficColumn">Traffic</th>
            <th v-if="showRenewalColumn">Renewal</th>
            <th class="number-cell">Up</th>
            <th class="number-cell">Down</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(server, index) in servers" :key="`${displayName(server, index)}-${index}`">
            <td>
              <div class="server-name">{{ displayName(server, index) }}</div>
              <div class="server-subline">{{ regionSummary(server) }}</div>
              <div class="server-subline">{{ providerSummary(server) }}</div>
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
              <div class="server-subline">{{ peerSummary(server) }}</div>
            </td>
            <td v-if="showSystemColumn" class="telemetry-cell">
              <div class="server-name">{{ systemSummary(server) }}</div>
              <div class="server-subline">
                {{ server.os || "Unknown OS" }} / {{ server.loadavg || "No loadavg" }}
              </div>
            </td>
            <td>{{ latencySummary(server) }}</td>
            <td v-if="showTrafficColumn" class="telemetry-cell">
              <div class="server-name">{{ trafficSummary(server) }}</div>
              <div v-if="!showRenewalColumn" class="server-subline">
                {{ renewalSummary(server) }}
              </div>
            </td>
            <td v-if="showRenewalColumn" class="telemetry-cell">
              <div class="server-name">{{ renewalSummary(server) }}</div>
              <div class="server-subline">
                CNY {{ server.renewal_price_cny ?? "n/a" }}
              </div>
            </td>
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
