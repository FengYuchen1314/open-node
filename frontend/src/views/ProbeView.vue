<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";

import type { ProbePayload, ProbeServer, ProbeSettingsUpdate } from "../domain/probe";
import type { ProbeHealth, ProbeLatencyBucket, ProbeStatusFilter } from "../domain/probe-insights";
import {
  buildRegionOptions,
  filterProbeServers,
  isExpired,
  isExpiring,
  latencyBucketLevels,
  percent,
  probeHealth,
  remainingDaysLabel,
  serverRegionLabel,
  summarizeSevenDayTraffic,
  trafficHotspots,
  trafficTotal,
  trafficUsed,
} from "../domain/probe-insights";
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
const statusFilter = ref<ProbeStatusFilter>("all");
const regionFilter = ref("all");
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
  show_globe: false,
  show_resource_heatmap: true,
  show_traffic_quota: true,
  show_daily_trend: false,
  show_traffic_hotspots: false,
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
const showHealthScore = computed(() => payload.value?.show_health_score !== false);
const showRegionOverview = computed(() => payload.value?.show_globe === true && regions.value.length > 0);
const showDailyTrend = computed(() => payload.value?.show_daily_trend === true);
const showTrafficHotspots = computed(() => payload.value?.show_traffic_hotspots === true);
const showSevenDayTraffic = computed(() => payload.value?.show_traffic_7d === true);
const onlineCount = computed(() => servers.value.filter((server) => server.online).length);
const expiringCount = computed(() => servers.value.filter((server) => isExpiring(server)).length);
const expiredCount = computed(() => servers.value.filter((server) => isExpired(server)).length);
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
const regions = computed(() => buildRegionOptions(servers.value));
const visibleServers = computed(() =>
  filterProbeServers(servers.value, statusFilter.value, regionFilter.value),
);
const filterCountLabel = computed(() => `${visibleServers.value.length} / ${servers.value.length} nodes`);
const statusFilterOptions = computed<
  Array<{ count: number; icon: string; title: string; value: ProbeStatusFilter }>
>(() => [
  { title: "All", value: "all", count: servers.value.length, icon: "mdi-server-network" },
  { title: "Online", value: "online", count: onlineCount.value, icon: "mdi-lan-check" },
  {
    title: "Offline",
    value: "offline",
    count: servers.value.length - onlineCount.value,
    icon: "mdi-lan-disconnect",
  },
  {
    title: "Renewal",
    value: "renewal",
    count: expiringCount.value + expiredCount.value,
    icon: "mdi-calendar-clock",
  },
  { title: "Expired", value: "expired", count: expiredCount.value, icon: "mdi-calendar-remove" },
]);
const regionSelectOptions = computed(() => [
  { title: "All regions", value: "all" },
  ...regions.value.map((region) => ({
    title: `${region.label} (${region.online}/${region.total})`,
    value: region.code,
  })),
]);
const dailyTraffic = computed(() => summarizeSevenDayTraffic(servers.value));
const dailyTrafficPeak = computed(() => Math.max(1, ...dailyTraffic.value.map((day) => day.total)));
const currentDailyTraffic = computed(() => dailyTraffic.value.at(-1)?.total ?? 0);
const hotspots = computed(() => trafficHotspots(servers.value));
const showInsightGrid = computed(
  () =>
    servers.value.length > 0 &&
    ((showRegionOverview.value && regions.value.length > 0) ||
      (showDailyTrend.value && dailyTraffic.value.length > 0) ||
      (showTrafficHotspots.value && hotspots.value.length > 0)),
);
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
  syncFilters(nextPayload.servers ?? []);
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
  settingsForm.show_globe = nextPayload.show_globe === true;
  settingsForm.show_resource_heatmap = nextPayload.show_resource_heatmap !== false;
  settingsForm.show_traffic_quota = nextPayload.show_traffic_quota !== false;
  settingsForm.show_daily_trend = nextPayload.show_daily_trend === true;
  settingsForm.show_traffic_hotspots = nextPayload.show_traffic_hotspots === true;
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
    show_globe: settingsForm.show_globe,
    show_resource_heatmap: settingsForm.show_resource_heatmap,
    show_traffic_quota: settingsForm.show_traffic_quota,
    show_daily_trend: settingsForm.show_daily_trend,
    show_traffic_hotspots: settingsForm.show_traffic_hotspots,
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

function syncFilters(nextServers: ProbeServer[]) {
  if (
    regionFilter.value !== "all" &&
    !buildRegionOptions(nextServers).some((region) => region.code === regionFilter.value)
  ) {
    regionFilter.value = "all";
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

function healthFor(server: ProbeServer): ProbeHealth {
  return probeHealth(server);
}

function healthHint(server: ProbeServer) {
  const health = healthFor(server);
  return health.issues.length ? health.issues.join(", ") : "No public health issues";
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
  const used = trafficUsed(server);
  if (used === 0 && !server.traffic_limit && server.traffic_used === undefined) {
    return "No traffic";
  }
  if (server.traffic_limit) {
    return `${formatBytes(used)} / ${formatBytes(server.traffic_limit)}`;
  }
  return formatBytes(used);
}

function regionSummary(server: ProbeServer) {
  return serverRegionLabel(server);
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

function rowClass(server: ProbeServer) {
  return {
    "is-filtered-offline": !server.online,
    "is-expired": isExpired(server),
    "is-expiring": isExpiring(server),
  };
}

function selectRegion(code: string) {
  regionFilter.value = code;
}

function regionShare(total: number) {
  const share = servers.value.length > 0 ? (total / servers.value.length) * 100 : 0;
  return `${Math.max(4, share)}%`;
}

function dailyBarHeight(value: number) {
  if (value <= 0) {
    return "0%";
  }
  return `${Math.max(6, (value / dailyTrafficPeak.value) * 100)}%`;
}

function dailyBarTitle(day: { date: string; uplink: number; downlink: number; total: number }) {
  return `${day.date}: ${formatBytes(day.total)} total`;
}

function latencyBuckets(server: ProbeServer): ProbeLatencyBucket[] {
  return latencyBucketLevels(server);
}

function latencyBucketTitle(bucket: ProbeLatencyBucket) {
  if (bucket.level === "none") {
    return "No sample";
  }
  return `${bucket.ms.toFixed(0)} ms, ${bucket.loss.toFixed(1)}% loss`;
}

function quotaWidth(server: ProbeServer) {
  return `${percent(trafficUsed(server), server.traffic_limit)}%`;
}

function sevenDayTrafficSummary(server: ProbeServer) {
  const rows = server.daily_traffic ?? [];
  return rows.length ? `7d ${formatBytes(trafficTotal(rows))}` : "No 7d traffic";
}

function renewalTone(server: ProbeServer) {
  if (isExpired(server)) {
    return "error";
  }
  if (isExpiring(server)) {
    return "warning";
  }
  return "default";
}

function renewalCountdown(server: ProbeServer) {
  return remainingDaysLabel(server.expires_at) || "No expiry";
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

    <section v-if="showInsightGrid" class="probe-insight-grid" aria-label="Probe insights">
      <v-sheet
        v-if="showRegionOverview"
        class="section-surface probe-insight-panel"
        border
      >
        <div class="section-head compact-head">
          <div>
            <div class="section-title">Regions</div>
            <div class="section-subtitle">{{ regions.length }} public groups</div>
          </div>
          <v-icon color="primary" icon="mdi-map-marker-radius-outline" />
        </div>
        <div class="probe-region-list">
          <button
            v-for="region in regions"
            :key="region.code"
            :class="{ active: regionFilter === region.code }"
            type="button"
            @click="selectRegion(region.code)"
          >
            <span>{{ region.label }}</span>
            <i>
              <b :style="{ width: regionShare(region.total) }" />
            </i>
            <strong>{{ region.online }}/{{ region.total }}</strong>
          </button>
        </div>
      </v-sheet>

      <v-sheet
        v-if="showDailyTrend && dailyTraffic.length > 0"
        class="section-surface probe-insight-panel"
        border
      >
        <div class="section-head compact-head">
          <div>
            <div class="section-title">Daily traffic</div>
            <div class="section-subtitle">{{ formatBytes(currentDailyTraffic) }} today</div>
          </div>
          <v-icon color="warning" icon="mdi-chart-bar" />
        </div>
        <div class="probe-daily-bars">
          <div
            v-for="day in dailyTraffic"
            :key="day.date"
            :title="dailyBarTitle(day)"
            class="probe-day-bar"
          >
            <span>
              <i class="down" :style="{ height: dailyBarHeight(day.downlink) }" />
              <i class="up" :style="{ height: dailyBarHeight(day.uplink) }" />
            </span>
            <small>{{ day.date.slice(5) }}</small>
          </div>
        </div>
      </v-sheet>

      <v-sheet
        v-if="showTrafficHotspots && hotspots.length > 0"
        class="section-surface probe-insight-panel"
        border
      >
        <div class="section-head compact-head">
          <div>
            <div class="section-title">Traffic hotspots</div>
            <div class="section-subtitle">Top live throughput</div>
          </div>
          <v-icon color="info" icon="mdi-chart-timeline-variant-shimmer" />
        </div>
        <div class="probe-hotspot-list">
          <div v-for="row in hotspots" :key="`${row.name}-${row.index}`">
            <span>{{ row.name }}</span>
            <i>
              <b :style="{ width: `${row.share}%` }" />
            </i>
            <strong>{{ formatBytesPerSecond(row.speed) }}</strong>
          </div>
        </div>
      </v-sheet>
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
            v-model="settingsForm.show_globe"
            color="primary"
            density="comfortable"
            hide-details
            label="Regions"
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
            v-model="settingsForm.show_traffic_hotspots"
            color="info"
            density="comfortable"
            hide-details
            label="Hotspots"
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

      <div v-if="servers.length > 0" class="probe-filter-row">
        <v-btn-toggle
          v-model="statusFilter"
          class="probe-status-toggle"
          density="comfortable"
          mandatory
          variant="outlined"
        >
          <v-btn
            v-for="option in statusFilterOptions"
            :key="option.value"
            :prepend-icon="option.icon"
            :value="option.value"
            size="small"
          >
            {{ option.title }} {{ option.count }}
          </v-btn>
        </v-btn-toggle>
        <v-select
          v-model="regionFilter"
          :items="regionSelectOptions"
          class="probe-region-select"
          density="comfortable"
          hide-details
          prepend-inner-icon="mdi-map-marker-outline"
          variant="outlined"
        />
        <div class="section-subtitle filter-count">{{ filterCountLabel }}</div>
      </div>

      <v-table v-if="visibleServers.length > 0" class="server-table" density="comfortable">
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
          <tr
            v-for="(server, index) in visibleServers"
            :key="`${displayName(server, index)}-${index}`"
            :class="rowClass(server)"
          >
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
              <v-tooltip v-if="showHealthScore" :text="healthHint(server)">
                <template #activator="{ props }">
                  <v-chip
                    v-bind="props"
                    :color="healthFor(server).tone"
                    class="health-chip"
                    density="compact"
                    size="x-small"
                    variant="tonal"
                  >
                    {{ healthFor(server).score }} {{ healthFor(server).label }}
                  </v-chip>
                </template>
              </v-tooltip>
            </td>
            <td v-if="showSystemColumn" class="telemetry-cell">
              <div class="server-name">{{ systemSummary(server) }}</div>
              <div class="server-subline">
                {{ server.os || "Unknown OS" }} / {{ server.loadavg || "No loadavg" }}
              </div>
            </td>
            <td class="telemetry-cell">
              <div class="server-name">{{ latencySummary(server) }}</div>
              <div v-if="latencyBuckets(server).length > 0" class="probe-latency-bars">
                <span
                  v-for="(bucket, bucketIndex) in latencyBuckets(server)"
                  :key="bucketIndex"
                  :class="`is-${bucket.level}`"
                  :title="latencyBucketTitle(bucket)"
                />
              </div>
            </td>
            <td v-if="showTrafficColumn" class="telemetry-cell">
              <div class="server-name">{{ trafficSummary(server) }}</div>
              <div v-if="server.traffic_limit" class="probe-quota-meter">
                <i :style="{ width: quotaWidth(server) }" />
              </div>
              <div v-if="showSevenDayTraffic" class="server-subline">
                {{ sevenDayTrafficSummary(server) }}
              </div>
              <div v-if="!showRenewalColumn" class="server-subline">
                {{ renewalSummary(server) }}
              </div>
            </td>
            <td v-if="showRenewalColumn" class="telemetry-cell">
              <v-chip
                :color="renewalTone(server)"
                density="compact"
                size="small"
                variant="tonal"
              >
                {{ renewalCountdown(server) }}
              </v-chip>
              <div class="server-subline">{{ renewalSummary(server) }}</div>
              <div class="server-subline">
                CNY {{ server.renewal_price_cny ?? "n/a" }}
              </div>
            </td>
            <td class="number-cell">{{ formatBytesPerSecond(server.upload_speed ?? 0) }}</td>
            <td class="number-cell">{{ formatBytesPerSecond(server.download_speed ?? 0) }}</td>
          </tr>
        </tbody>
      </v-table>

      <div v-else-if="servers.length === 0" class="empty-state">
        <v-icon color="secondary" icon="mdi-chart-line-variant" size="36" />
        <div>No public probe nodes yet.</div>
      </div>
      <div v-else class="empty-state">
        <v-icon color="secondary" icon="mdi-filter-off-outline" size="36" />
        <div>No nodes match the current filters.</div>
      </div>
    </v-sheet>
  </div>
</template>
