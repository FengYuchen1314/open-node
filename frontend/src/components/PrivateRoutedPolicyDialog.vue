<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import type { PrivateRoutedPolicy } from "../domain/private-routed-nodes";
import { updatePrivateRoutePolicy } from "../services/private-routed-nodes";

const props = defineProps<{ open: boolean; policy: PrivateRoutedPolicy | null }>();
const emit = defineEmits<{
  "update:open": [value: boolean];
  saved: [value: PrivateRoutedPolicy];
}>();
const form = reactive({ enabled: false, max_nodes: 2, daily_limit: 5 });
const busy = ref(false);
const error = ref("");

watch(() => props.open, (open) => {
  if (!open) return;
  Object.assign(form, props.policy ?? { enabled: false, max_nodes: 2, daily_limit: 5 });
  error.value = "";
});

async function save() {
  busy.value = true;
  error.value = "";
  try {
    const value = await updatePrivateRoutePolicy({
      enabled: form.enabled,
      max_nodes: Number(form.max_nodes),
      daily_limit: Number(form.daily_limit),
    });
    emit("saved", value);
    emit("update:open", false);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Policy update failed";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="560" @update:model-value="emit('update:open', $event)">
    <v-card class="policy-dialog">
      <v-card-title>Private route policy</v-card-title>
      <v-card-text class="policy-form">
        <v-alert v-if="error" type="error" variant="tonal" density="compact">{{ error }}</v-alert>
        <v-switch v-model="form.enabled" label="Allow subscriber private routes" color="primary" hide-details />
        <div class="policy-row">
          <v-text-field v-model.number="form.max_nodes" label="Routes per subscriber" type="number" min="1" max="20" variant="outlined" density="compact" />
          <v-text-field v-model.number="form.daily_limit" label="Daily actions" type="number" min="1" max="100" variant="outlined" density="compact" />
        </div>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="emit('update:open', false)">Cancel</v-btn>
        <v-spacer />
        <v-btn color="primary" prepend-icon="mdi-content-save" :loading="busy" :disabled="form.max_nodes < 1 || form.daily_limit < 1" @click="save">Save</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.policy-dialog, .policy-dialog :deep(*) { letter-spacing: 0; }
.policy-form { display: grid; gap: 12px; }
.policy-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.policy-dialog :deep(.v-card-title), .policy-dialog :deep(.v-alert__content) { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 480px) { .policy-row { grid-template-columns: minmax(0, 1fr); } }
</style>
