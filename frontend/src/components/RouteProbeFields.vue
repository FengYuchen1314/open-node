<script setup lang="ts">
import type { AgentReturnRouteTarget } from "../domain/inventory";
const props = defineProps<{ modelValue: AgentReturnRouteTarget[] }>();
const emit = defineEmits<{ "update:modelValue": [value: AgentReturnRouteTarget[]] }>();
const names = { telecom: "Telecom", unicom: "Unicom", mobile: "Mobile" };
function update(index: number, key: "host" | "region" | "port", value: unknown) {
  emit("update:modelValue", props.modelValue.map((target, position) =>
    position === index ? { ...target, [key]: key === "port" ? Number(value) : String(value ?? "") } : target));
}
</script>

<template>
  <div class="route-probe-fields">
    <div v-for="(target, index) in modelValue" :key="target.carrier" class="route-probe-target">
      <div class="route-probe-name">{{ names[target.carrier] }}</div>
      <v-text-field :model-value="target.host" :label="names[target.carrier] + ' host'"
        density="comfortable" variant="outlined" @update:model-value="update(index, 'host', $event)" />
      <v-text-field :model-value="target.port" :label="names[target.carrier] + ' port'"
        type="number" min="1" max="65535" density="comfortable" variant="outlined"
        @update:model-value="update(index, 'port', $event)" />
      <v-text-field :model-value="target.region" :label="names[target.carrier] + ' region'"
        density="comfortable" variant="outlined" @update:model-value="update(index, 'region', $event)" />
    </div>
  </div>
</template>

<style scoped>
.route-probe-fields { width: 100%; min-width: 0; grid-column: 1 / -1; }
.route-probe-target { display: grid; grid-template-columns: minmax(0, 1fr) 104px; gap: 0 12px; }
.route-probe-name { grid-column: 1 / -1; font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.route-probe-target > :last-child { grid-column: 1 / -1; }
.route-probe-target :deep(.v-field-label) { font-size: 13px; }
@media (max-width: 380px) {
  .route-probe-target { grid-template-columns: minmax(0, 1fr); }
}
</style>
