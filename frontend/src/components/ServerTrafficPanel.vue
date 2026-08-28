<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import type { ServerSummary, ServerTraffic, TrafficSource, TrafficStatsMode } from "../domain/inventory";
import { getServerTraffic, resetServerTraffic, updateServerTraffic } from "../services/inventory";

const props = defineProps<{ servers: ServerSummary[] }>();
const selected = ref("");
const state = ref<ServerTraffic | null>(null);
const busy = ref(false);
const error = ref("");
const confirmation = ref(false);
const form = reactive({ limit: 0, day: 0, source: "xray" as TrafficSource, mode: "both" as TrafficStatsMode });
const sources = [{ title: "Xray nodes", value: "xray" }, { title: "System network", value: "system" }];
const modes = [{ title: "Upload + download", value: "both" }, { title: "Upload", value: "upload" }, { title: "Download", value: "download" }, { title: "Larger direction", value: "max" }];
const days = [{ title: "Off", value: 0 }, ...Array.from({ length: 31 }, (_, i) => ({ title: String(i + 1), value: i + 1 }))];
const limitBytes = computed(() => Math.round(Number(form.limit) * 1024 ** 3));
const valid = computed(() => form.limit !== null && String(form.limit) !== "" && Number.isSafeInteger(limitBytes.value) && limitBytes.value >= 0);
const quota = computed(() => state.value?.traffic_limit ? Math.min(100, state.value.used / state.value.traffic_limit * 100) : 0);
let version = 0;
let disposed = false;
let timer: ReturnType<typeof setTimeout> | undefined;

function fill(value: ServerTraffic) {
  Object.assign(form, { limit: value.traffic_limit / 1024 ** 3, day: value.traffic_reset_day, source: value.traffic_source, mode: value.traffic_stats_mode });
}

async function request(action: "read" | "save" | "reset", replaceForm = false) {
  if (disposed || !selected.value || (action === "save" && !valid.value)) return;
  const id = selected.value;
  const current = ++version;
  clearTimeout(timer);
  timer = undefined;
  busy.value = true;
  error.value = "";
  if (action !== "read") confirmation.value = false;
  try {
    const response = await (action === "save" ? updateServerTraffic(id, {
      traffic_limit: limitBytes.value, traffic_reset_day: form.day,
      traffic_source: form.source, traffic_stats_mode: form.mode,
    }) : action === "reset" ? resetServerTraffic(id) : getServerTraffic(id));
    if (current !== version) return;
    if (replaceForm || !state.value) fill(response);
    state.value = response;
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Server traffic request failed";
  } finally {
    if (current === version) {
      busy.value = false;
      timer = setTimeout(() => void request("read"), 10000);
    }
  }
}

watch(() => props.servers.map((server) => server.id), (ids) => {
  if (!ids.includes(selected.value)) selected.value = ids[0] ?? "";
}, { immediate: true });
watch(selected, () => {
  ++version;
  clearTimeout(timer);
  state.value = null;
  confirmation.value = false;
  busy.value = false;
  error.value = "";
  void request("read", true);
}, { immediate: true });
onBeforeUnmount(() => { disposed = true; ++version; clearTimeout(timer); });

function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(2)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
function date(value: string | null) { return value ? new Date(value).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "None"; }
</script>

<template>
  <section class="server-traffic" aria-label="Server traffic">
    <div class="traffic-toolbar">
      <h3>Server traffic</h3>
      <v-tooltip text="Refresh traffic and settings"><template #activator="{ props: tip }">
        <v-btn v-bind="tip" icon="mdi-refresh" aria-label="Refresh server traffic" variant="text" size="small" :disabled="busy || !selected" @click="request('read', true)" />
      </template></v-tooltip>
    </div>
    <v-select v-model="selected" :items="servers" item-title="name" item-value="id" label="Traffic server" :disabled="busy || !servers.length" density="compact" variant="outlined" hide-details />
    <v-alert v-if="error" type="error" variant="tonal" density="compact">{{ error }}</v-alert>
    <template v-if="state">
      <div class="traffic-usage" aria-live="polite">
        <strong data-testid="server-traffic-used">{{ bytes(state.used) }}</strong>
        <span>/ {{ state.traffic_limit ? bytes(state.traffic_limit) : "Unlimited" }}</span>
        <span>{{ state.traffic_source === 'system' ? 'System network' : 'Xray nodes' }}</span>
      </div>
      <v-progress-linear :model-value="quota" :color="quota >= 100 ? 'error' : 'success'" height="5" />
      <dl class="traffic-details">
        <dt>Upload / download</dt><dd>{{ bytes(state.upload) }} / {{ bytes(state.download) }}</dd>
        <dt>Last report</dt><dd>{{ date(state.last_reported_at) }}</dd>
        <dt>Last reset</dt><dd>{{ date(state.last_reset_at) }}</dd>
        <dt>Next reset</dt><dd>{{ date(state.next_reset_at) }}</dd>
      </dl>
      <v-form class="traffic-form" @submit.prevent="request('save', true)">
        <v-select v-model="form.source" :items="sources" label="Traffic source" :disabled="busy" density="compact" variant="outlined" hide-details />
        <v-select v-model="form.mode" :items="modes" label="Counted direction" :disabled="busy" density="compact" variant="outlined" hide-details />
        <v-text-field v-model.number="form.limit" label="Quota (GiB, 0 = unlimited)" type="number" min="0" step="any" :disabled="busy" :error="!valid" density="compact" variant="outlined" hide-details />
        <v-select v-model="form.day" :items="days" label="Monthly reset day (UTC)" :disabled="busy" density="compact" variant="outlined" hide-details />
        <div class="traffic-actions">
          <v-btn type="submit" color="primary" prepend-icon="mdi-content-save" :disabled="busy || !valid">Save</v-btn>
          <v-btn prepend-icon="mdi-backup-restore" variant="text" :disabled="busy" @click="confirmation = true">Reset cycle</v-btn>
        </div>
      </v-form>
    </template>
    <v-dialog v-model="confirmation" max-width="440">
      <v-card title="Reset server traffic?">
        <v-card-text>The current traffic cycle for {{ servers.find(server => server.id === selected)?.name }} will start at zero for both sources. Historical counters and user quotas stay unchanged.</v-card-text>
        <v-card-actions><v-spacer /><v-btn @click="confirmation = false">Cancel</v-btn><v-btn color="error" @click="request('reset')">Reset</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<style scoped>
.server-traffic { border-top: 1px solid rgb(var(--v-theme-on-surface), 0.12); padding: 20px 0 0; margin-top: 20px; min-width: 0; display: grid; gap: 16px; }
.traffic-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; gap: 8px; }
.traffic-toolbar h3 { font-size: 16px; }
.traffic-usage { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.traffic-usage strong { font-size: 20px; }
.traffic-usage span { font-size: 13px; }
.traffic-usage span:last-child { margin-left: auto; }
.traffic-details { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px 16px; font-size: 12px; }
.traffic-details dd { text-align: right; overflow-wrap: anywhere; }
.traffic-details dt { color: rgb(var(--v-theme-on-surface), 0.65); }
.traffic-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.traffic-actions { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px; }
@media (max-width: 650px) { .traffic-form { grid-template-columns: minmax(0, 1fr); } }
</style>
