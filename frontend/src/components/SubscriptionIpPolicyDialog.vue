<script setup lang="ts">
import { ref, watch } from "vue";
import type { SubscriptionIpPolicy } from "../domain/subscriptions";
import { subscriberIpPolicy, updateSubscriberIpPolicy } from "../services/subscriber-auth";
import { getProductUserIpPolicy, updateProductUserIpPolicy } from "../services/subscriptions";

const props = withDefaults(defineProps<{ open: boolean; username?: string; subscriber?: boolean }>(), {
  username: "",
  subscriber: false,
});
const emit = defineEmits<{ "update:open": [value: boolean]; updated: [value: SubscriptionIpPolicy] }>();
const policy = ref<SubscriptionIpPolicy | null>(null);
const value = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
let version = 0;

function close() {
  if (!saving.value) emit("update:open", false);
}

async function load() {
  const current = ++version;
  loading.value = true;
  error.value = "";
  try {
    const result = props.subscriber ? await subscriberIpPolicy() : await getProductUserIpPolicy(props.username);
    if (current !== version) return;
    policy.value = result;
    value.value = result.networks.join("\n");
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "IP policy unavailable";
  } finally {
    if (current === version) loading.value = false;
  }
}

async function save() {
  if (saving.value) return;
  const current = ++version;
  saving.value = true;
  error.value = "";
  const networks = value.value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean);
  try {
    const result = props.subscriber
      ? await updateSubscriberIpPolicy(networks)
      : await updateProductUserIpPolicy(props.username, networks);
    if (current !== version) return;
    policy.value = result;
    value.value = result.networks.join("\n");
    emit("updated", result);
    emit("update:open", false);
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "IP policy update failed";
  } finally {
    if (current === version) saving.value = false;
  }
}

watch(() => [props.open, props.username, props.subscriber] as const, ([open]) => {
  if (open) void load();
  else ++version;
}, { immediate: true });
</script>

<template>
  <v-dialog :model-value="open" max-width="560" persistent scrollable @update:model-value="close">
    <v-card class="ip-policy-dialog">
      <v-card-title class="ip-policy-title"><span>Subscription IP access</span><v-chip :color="policy?.enabled ? 'warning' : 'success'" size="small" variant="tonal">{{ policy?.enabled ? 'Restricted' : 'Unrestricted' }}</v-chip></v-card-title>
      <v-progress-linear v-if="loading" indeterminate color="primary" />
      <v-card-text>
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-textarea v-model="value" label="Allowed IPs and CIDRs" placeholder="203.0.113.8&#10;2001:db8::/48" rows="7" auto-grow :disabled="loading || saving" spellcheck="false" autocomplete="off" />
      </v-card-text>
      <v-card-actions><v-btn :disabled="saving" @click="close">Cancel</v-btn><v-spacer /><v-btn color="primary" prepend-icon="mdi-content-save-outline" :loading="saving" :disabled="loading" @click="save">Save</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.ip-policy-dialog, .ip-policy-dialog :deep(*) { letter-spacing: 0; }
.ip-policy-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; overflow: visible; font-size: 18px; white-space: normal; }
.ip-policy-title :deep(.v-chip) { flex-shrink: 0; }
.ip-policy-dialog :deep(.v-card-actions) { flex-wrap: wrap; }
.ip-policy-dialog :deep(textarea) { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 13px; }
.ip-policy-dialog :deep(.v-alert__content) { overflow-wrap: anywhere; }
</style>
