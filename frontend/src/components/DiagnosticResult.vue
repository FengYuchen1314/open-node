<script setup lang="ts">
import { computed } from "vue";
import { resultObject, resultRows } from "../domain/diagnostics";

const props = defineProps<{ path: string; body: unknown }>();
const result = computed(() => resultObject(props.body));
const rows = computed(() => resultRows(result.value.results));
const files = computed(() => resultRows(result.value.files));
function display(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}
function latency(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value + " ms" : "Unavailable";
}
</script>

<template>
  <div class="diagnostic-result">
    <template v-if="path === '/api/child/domains/latency'">
      <div v-for="(row, index) in rows" :key="index" class="diagnostic-row">
        <v-icon :icon="row.success === true ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'"
          :color="row.success === true ? 'success' : 'error'" size="20" />
        <div>
          <strong>{{ display(row.target || row.domain) }}</strong>
          <div class="diagnostic-meta">
            {{ row.method === 'icmp' ? row.success === true ? 'ICMP host reachable' : 'ICMP failed' : row.success === true ? 'TCP port open' : 'TCP failed' }}
            <span v-if="row.success === true"> | {{ latency(row.latency_ms) }}</span>
          </div>
          <div v-if="row.error || row.tcp_error" class="diagnostic-meta">{{ display(row.error || row.tcp_error) }}</div>
          <div v-if="row.icmp_error" class="diagnostic-meta">{{ display(row.icmp_error) }}</div>
        </div>
      </div>
    </template>
    <template v-else-if="path === '/api/child/network/return-route-test'">
      <section v-for="(row, index) in rows" :key="index" class="diagnostic-route">
        <h4>{{ display(row.carrier) }} | {{ display(row.target) }}</h4>
        <div>{{ display(row.route_type || 'Unknown') }} | {{ row.reached === true ? 'Target reached' : 'Target not confirmed' }}</div>
        <div v-if="row.error" class="diagnostic-error" role="status">{{ display(row.error) }}</div>
        <div class="diagnostic-meta">{{ display(row.reason) }}</div>
        <div v-for="(hop, position) in resultRows(row.hops)" :key="position" class="diagnostic-hop">
          <span>{{ display(hop.hop) }}</span>
          <div>
            <strong>{{ display(hop.ip) }}</strong>
            <div class="diagnostic-meta">{{ hop.asn ? 'AS' + display(hop.asn) : 'ASN unavailable' }} {{ display(hop.country) }} {{ display(hop.region) }}</div>
          </div>
          <span>{{ latency(hop.rtt_ms) }}</span>
        </div>
      </section>
    </template>
    <pre v-else-if="path === '/api/child/logs'" class="diagnostic-log">{{ display(result.logs) || 'No log entries.' }}</pre>
    <template v-else-if="Array.isArray(result.files)">
      <div class="diagnostic-meta">{{ display(result.total_size) }} bytes</div>
      <div v-for="file in files" :key="display(file.name)" class="diagnostic-file">
        <strong>{{ display(file.name) }}</strong>
        <span>{{ display(file.size) }} bytes</span>
        <span>{{ file.active ? 'Active' : 'Rotated' }}</span>
      </div>
      <div v-if="files.length === 0">No log files.</div>
    </template>
    <template v-else>
      <div v-if="result.removed !== undefined">{{ display(result.removed) }} files cleared | {{ display(result.freed) }} bytes freed</div>
      <div v-else>{{ result.success ? 'Log deletion completed' : 'Log deletion failed' }}</div>
      <div v-for="(error, index) in resultRows(result.errors)" :key="index" class="diagnostic-error">
        {{ display(error.name) }}: {{ display(error.error) }}
      </div>
    </template>
  </div>
</template>

<style scoped>
.diagnostic-result { margin-top: 16px; min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
.diagnostic-row { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 12px; padding: 12px 0; border-bottom: 1px solid rgba(var(--v-theme-on-surface), .12); }
.diagnostic-meta { font-size: 12px; color: rgba(var(--v-theme-on-surface), .72); margin-top: 4px; }
.diagnostic-route { padding: 12px 0; }
.diagnostic-route h4 { font-size: 14px; margin-bottom: 8px; }
.diagnostic-hop { display: grid; grid-template-columns: 24px minmax(0, 1fr) 88px; gap: 8px; padding: 10px 0; border-bottom: 1px solid rgba(var(--v-theme-on-surface), .12); }
.diagnostic-error { color: rgb(var(--v-theme-error)); margin-top: 8px; }
.diagnostic-log { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 480px; overflow: auto; font-size: 12px; }
.diagnostic-file { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 12px 0; }
.diagnostic-file strong { flex: 1 1 140px; }
@media (max-width: 380px) {
  .diagnostic-hop { grid-template-columns: 20px minmax(0, 1fr); }
  .diagnostic-hop > :last-child { grid-column: 2; }
}
</style>
