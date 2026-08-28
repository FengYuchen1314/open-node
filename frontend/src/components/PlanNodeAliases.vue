<script setup lang="ts">
import { computed, watch } from "vue";
import { aliasErrors } from "../domain/plan-node-aliases";

const props = defineProps<{
  nodes: { id: string; name: string }[];
  names: Record<string, string>;
  enabled: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  "update:names": [value: Record<string, string>];
  "update:enabled": [value: boolean];
  valid: [value: boolean];
}>();
const ids = computed(() => props.nodes.map(node => node.id));
const errors = computed(() => aliasErrors(props.names, ids.value));
watch(errors, value => emit("valid", Object.keys(value).length === 0), { immediate: true });
watch(ids, value => {
  if (Object.keys(props.names).some(id => !value.includes(id))) {
    emit("update:names", Object.fromEntries(Object.entries(props.names).filter(([id]) => value.includes(id))));
  }
}, { immediate: true });
function setName(id: string, value: string | null) {
  const names = { ...props.names };
  if (value) names[id] = value;
  else delete names[id];
  emit("update:names", names);
}
</script>

<template>
  <div class="plan-node-aliases">
    <v-switch :model-value="enabled" label="Custom subscription names" color="primary" density="compact" hide-details :disabled="disabled || !nodes.length" @update:model-value="emit('update:enabled', !!$event)" />
    <section v-for="node in nodes" :key="node.id" class="plan-alias-node" :aria-label="node.name">
      <strong>{{ node.name }}</strong>
      <v-text-field :model-value="names[node.id] ?? ''" :aria-label="`${node.name}: subscription name`" label="Subscription name" :placeholder="node.name" variant="outlined" density="compact" hide-details="auto" clearable :disabled="disabled || !enabled" :error-messages="errors[node.id] ?? []" @update:model-value="setName(node.id, $event)" />
      <slot :node="node" />
    </section>
  </div>
</template>

<style scoped>
.plan-node-aliases { min-width: 0; }
.plan-alias-node { display: grid; gap: 12px; padding-block: 12px; border-top: 1px solid rgb(var(--v-theme-on-surface), .12); min-width: 0; }
.plan-alias-node strong { font-size: 13px; overflow-wrap: anywhere; }
.plan-node-aliases :deep(.v-label) { white-space: normal; }
.plan-node-aliases :deep(.v-field__input) { min-width: 0; }
</style>
