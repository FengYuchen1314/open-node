<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ body: unknown }>();
const status = computed(() => (props.body && typeof props.body === "object"
  ? props.body : {}) as Record<string, unknown>);
const phase = computed(() => ({
  absent: "Not installed", configured: "Outbounds configured",
  needs_apply: "Needs configuration", removal_pending: "Removal pending",
}[String(status.value.phase)] ?? (status.value.installed ? "Outbounds configured" : "Not installed")));
const account = computed(() => status.value.account_type === "free" ? "Free WARP"
  : status.value.license_active ? "WARP+" : String(status.value.account_type ?? "Unknown"));
const registered = computed(() => {
  const date = new Date(String(status.value.registered_at ?? ""));
  if (Number.isNaN(date.getTime())) return null;
  const iso = date.toISOString();
  return { iso, day: iso.slice(0, 10), time: iso.slice(11, 16) + " UTC" };
});
</script>

<template>
  <section class="warp-status" aria-label="WARP result">
    <v-chip :color="status.installed ? 'success' : status.registered ? 'warning' : 'grey'"
      size="small" variant="tonal">{{ phase }}</v-chip>
    <dl v-if="status.registered || status.installed">
      <dt>Account</dt><dd>{{ account }}</dd>
      <dt>IPv4</dt><dd>{{ status.addr_v4 || "None" }}</dd>
      <dt>IPv6</dt><dd>{{ status.addr_v6 || "None" }}</dd>
      <dt>Registered</dt><dd><time v-if="registered" :datetime="registered.iso">
        <span>{{ registered.day }}</span><span>{{ registered.time }}</span>
      </time><span v-else>Unknown</span></dd>
    </dl>
  </section>
</template>

<style scoped>
.warp-status { min-width: 0; }
dl { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 8px 12px; font-size: 13px; margin-top: 16px; }
dt { color: rgba(var(--v-theme-on-surface), .72); }
dd { margin: 0; overflow-wrap: anywhere; }
time span { display: block; }
</style>
