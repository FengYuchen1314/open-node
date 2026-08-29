<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { TemporarySubscription } from "../domain/temporary-subscriptions";
import { createTemporarySubscription } from "../services/temporary-subscriptions";

const props = defineProps<{
  open: boolean;
  username: string;
  nodes: Array<{ title: string; value: string }>;
}>();
const emit = defineEmits<{
  "update:open": [value: boolean];
  created: [value: TemporarySubscription];
}>();
const form = reactive({
  label: "Temporary subscription",
  node_ids: [] as string[],
  max_access: 1,
  expires_in_seconds: 300,
});
const expiryOptions = [
  { title: "5 minutes", value: 300 },
  { title: "15 minutes", value: 900 },
  { title: "1 hour", value: 3600 },
];
const busy = ref(false);
const error = ref("");
const created = ref<TemporarySubscription | null>(null);
const copied = ref(false);
const canCreate = computed(() => Boolean(
  props.username && form.label.trim() && form.node_ids.length && form.max_access >= 1
  && form.max_access <= 100 && !busy.value,
));

watch(() => props.open, (open) => {
  if (!open) return;
  Object.assign(form, {
    label: "Temporary subscription",
    node_ids: props.nodes.map(item => item.value),
    max_access: 1,
    expires_in_seconds: 300,
  });
  error.value = "";
  created.value = null;
  copied.value = false;
});

async function submit() {
  if (!canCreate.value) return;
  busy.value = true;
  error.value = "";
  try {
    const value = await createTemporarySubscription({
      username: props.username,
      label: form.label.trim(),
      node_ids: form.node_ids,
      max_access: Number(form.max_access),
      expires_in_seconds: form.expires_in_seconds,
    });
    created.value = value;
    emit("created", value);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Temporary link creation failed";
  } finally {
    busy.value = false;
  }
}

async function copyLink() {
  if (!created.value) return;
  try {
    await navigator.clipboard.writeText(created.value.subscription_url);
    copied.value = true;
  } catch {
    error.value = "Clipboard access failed";
  }
}
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="640" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="temporary-dialog">
      <v-card-title>Create temporary link</v-card-title>
      <v-card-text class="temporary-form">
        <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
        <template v-if="!created">
          <v-text-field :model-value="username" label="Subscriber" readonly variant="outlined" density="compact" />
          <v-text-field v-model="form.label" label="Label" variant="outlined" density="compact" maxlength="120" />
          <v-autocomplete v-model="form.node_ids" :items="nodes" label="Nodes" multiple chips closable-chips variant="outlined" />
          <div class="temporary-row">
            <v-text-field v-model.number="form.max_access" type="number" min="1" max="100" label="Downloads" variant="outlined" density="compact" />
            <v-select v-model="form.expires_in_seconds" :items="expiryOptions" label="Expires" variant="outlined" density="compact" />
          </div>
        </template>
        <template v-else>
          <v-text-field :model-value="created.subscription_url" label="Temporary URL" readonly variant="outlined" density="compact">
            <template #append-inner><v-btn :icon="copied ? 'mdi-check' : 'mdi-content-copy'" :aria-label="copied ? 'Copied' : 'Copy temporary URL'" variant="text" size="small" @click="copyLink" /></template>
          </v-text-field>
          <v-alert type="info" variant="tonal" density="compact">URL expiry does not revoke credentials already downloaded.</v-alert>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="emit('update:open', false)">{{ created ? "Close" : "Cancel" }}</v-btn>
        <v-spacer />
        <v-btn v-if="!created" color="primary" prepend-icon="mdi-link-plus" :loading="busy" :disabled="!canCreate" @click="submit">Create</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.temporary-dialog, .temporary-dialog :deep(*) { letter-spacing: 0; }
.temporary-form { display: grid; gap: 14px; min-width: 0; }
.temporary-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.temporary-dialog :deep(.v-card-title), .temporary-dialog :deep(.v-alert__content) { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 520px) { .temporary-row { grid-template-columns: minmax(0, 1fr); } }
</style>
