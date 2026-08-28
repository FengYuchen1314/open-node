<script setup lang="ts">
import { computed, ref } from "vue";
import type { ManagedNode } from "../domain/subscriptions";
import { limitSource, maxSpeed, maxTraffic, type UserLimitOverrides, type UserLimitsRead } from "../domain/user-limits";
import LimitOverrideField from "./LimitOverrideField.vue";

const props = defineProps<{ modelValue: UserLimitOverrides; nodes: ManagedNode[]; current: UserLimitsRead; disabled: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: UserLimitOverrides] }>();
const selected = ref<string | null>(null);
const rows = ref([...new Set([...Object.keys(props.modelValue.node_speed_limits), ...Object.keys(props.modelValue.node_device_limits)])]);
const available = computed(() => props.nodes.filter(node => !node.removal_id && !rows.value.includes(node.id)));
const name = (id: string) => props.nodes.find(node => node.id === id)?.name ?? id;
const speed = (value: number) => value ? `${value} Mbps` : "Unlimited";
function update(field: "traffic_limit_gb" | "speed_limit_mbps" | "device_limit", value: number | null) {
  emit("update:modelValue", { ...props.modelValue, [field]: value });
}
function nodeValue(field: "node_speed_limits" | "node_device_limits", id: string, value: number | null) {
  const mapping = { ...props.modelValue[field] };
  if (value === null) delete mapping[id]; else mapping[id] = value;
  emit("update:modelValue", { ...props.modelValue, [field]: mapping });
}
function add() { if (selected.value && !rows.value.includes(selected.value)) rows.value.push(selected.value); selected.value = null; }
function remove(id: string) {
  const speeds = { ...props.modelValue.node_speed_limits }, devices = { ...props.modelValue.node_device_limits };
  delete speeds[id]; delete devices[id]; rows.value = rows.value.filter(row => row !== id);
  emit("update:modelValue", { ...props.modelValue, node_speed_limits: speeds, node_device_limits: devices });
}
</script>

<template>
  <div class="user-limit-editor">
    <section aria-label="Account limits" class="limit-section">
      <h3>Account limits</h3>
      <LimitOverrideField :model-value="modelValue.traffic_limit_gb" label="Traffic quota" unit="GiB" :maximum="maxTraffic" :minimum="1 / 1024 ** 3" :suggested="current.traffic_limit_bytes / 1024 ** 3" :disabled="disabled" @update:model-value="update('traffic_limit_gb', $event)" />
      <LimitOverrideField :model-value="modelValue.speed_limit_mbps" label="Speed limit" unit="Mbps" :maximum="maxSpeed" :minimum="1 / 125000" :suggested="current.speed_limit_mbps" :disabled="disabled" @update:model-value="update('speed_limit_mbps', $event)" />
      <LimitOverrideField :model-value="modelValue.device_limit" label="Connection limit" :maximum="1000000" :minimum="1" integer :suggested="current.device_limit" :disabled="disabled" @update:model-value="update('device_limit', $event)" />
    </section>
    <section aria-label="Node overrides" class="limit-section">
      <h3>Node overrides</h3>
      <div class="node-picker">
        <v-autocomplete v-model="selected" :items="available" item-title="name" item-value="id" label="Node" variant="outlined" density="compact" hide-details :disabled="disabled" />
        <v-tooltip text="Add node override"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Add node override" icon="mdi-plus" variant="text" size="small" :disabled="disabled || !selected" @click="add" /></template></v-tooltip>
      </div>
      <div v-for="id in rows" :key="id" class="node-limit" :aria-label="`Overrides for ${name(id)}`">
        <div class="node-limit-heading"><strong>{{ name(id) }}</strong><v-tooltip text="Remove node override"><template #activator="{ props: tip }"><v-btn v-bind="tip" :aria-label="`Remove override ${name(id)}`" icon="mdi-close" variant="text" size="small" :disabled="disabled" @click="remove(id)" /></template></v-tooltip></div>
        <LimitOverrideField :model-value="modelValue.node_speed_limits[id] ?? null" label="Node speed" unit="Mbps" :maximum="maxSpeed" :minimum="1 / 125000" :suggested="current.speed_limit_mbps" :disabled="disabled" @update:model-value="nodeValue('node_speed_limits', id, $event)" />
        <LimitOverrideField :model-value="modelValue.node_device_limits[id] ?? null" label="Node connections" :maximum="1000000" :minimum="1" integer :suggested="current.device_limit" :disabled="disabled" @update:model-value="nodeValue('node_device_limits', id, $event)" />
      </div>
    </section>
    <section v-if="current.nodes.length" class="limit-section" aria-label="Saved node limits">
      <h3>Saved node limits</h3>
      <div v-for="node in current.nodes" :key="node.node_id" class="resolved-limit">
        <strong>{{ node.name }}<span v-if="!node.enabled"> (disabled)</span></strong>
        <div><span>{{ speed(node.speed_limit_mbps) }}</span><small>{{ limitSource(node.speed_source) }}</small></div>
        <div><span>{{ node.device_limit ? `${node.device_limit} connections` : 'Unlimited connections' }}</span><small>{{ limitSource(node.device_source) }}</small></div>
      </div>
    </section>
    <v-alert v-for="warning in current.warnings" :key="warning" type="warning" variant="tonal">{{ warning }}</v-alert>
  </div>
</template>

<style scoped>
.user-limit-editor { display: grid; gap: 24px; min-width: 0; }
.limit-section { display: grid; gap: 16px; min-width: 0; }
.limit-section h3 { font-size: 14px; margin: 0; }
.node-picker, .node-limit-heading { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; gap: 8px; }
.node-limit { display: grid; gap: 14px; border-top: 1px solid rgb(var(--v-theme-on-surface), .12); padding-top: 10px; min-width: 0; }
.node-limit-heading { font-size: 13px; overflow-wrap: anywhere; }
.node-picker :deep(.v-autocomplete__selection-text) { white-space: normal; overflow-wrap: anywhere; }
.resolved-limit { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; font-size: 13px; border-top: 1px solid rgb(var(--v-theme-on-surface), .12); padding-top: 12px; overflow-wrap: anywhere; }
.resolved-limit div { display: grid; align-content: start; gap: 4px; }
.resolved-limit small { color: rgb(var(--v-theme-on-surface), .65); }
@media (max-width: 480px) { .resolved-limit { grid-template-columns: minmax(0, 1fr); gap: 8px; } }
</style>
