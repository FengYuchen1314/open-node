<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import type { ProductUser, SubscriptionAccessResponse } from "../domain/subscriptions";
import { getSubscriptionAccess, setProductUserActive, syncSubscriptionAccess } from "../services/subscriptions";

const props = defineProps<{ username: string; isActive: boolean; refreshKey?: string }>();
const emit = defineEmits<{ updated: [user: ProductUser] }>();
const state = ref<SubscriptionAccessResponse | null>(null);
const busy = ref(false);
const error = ref("");
const confirm = ref(false);
const switchVersion = ref(0);
let version = 0;
let disposed = false;
let timer: ReturnType<typeof setTimeout> | undefined;

async function load(sync = false) {
  if (disposed) return;
  const request = ++version;
  clearTimeout(timer);
  timer = undefined;
  if (!props.username) return;
  if (sync) busy.value = true;
  try {
    const response = await (sync ? syncSubscriptionAccess : getSubscriptionAccess)(props.username);
    if (request !== version) return;
    state.value = response;
    error.value = "";
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Access request failed";
  } finally {
    if (request === version) {
      busy.value = false;
      timer = setTimeout(() => void load(), 5000);
    }
  }
}

async function toggle(active: boolean | null) {
  if (active === false) { confirm.value = true; return; }
  await update(true);
}

async function update(active: boolean) {
  confirm.value = false;
  const username = props.username;
  ++version;
  clearTimeout(timer);
  busy.value = true;
  error.value = "";
  try {
    const response = await setProductUserActive(username, active);
    emit("updated", response.user);
    if (username === props.username) await load();
  } catch (failure) {
    if (username === props.username) error.value = failure instanceof Error ? failure.message : "User update failed";
  } finally {
    if (!disposed && username === props.username) {
      busy.value = false;
      switchVersion.value += 1;
      if (!timer) timer = setTimeout(() => void load(), 5000);
    }
  }
}

watch(() => [props.username, props.refreshKey], () => {
  state.value = null;
  error.value = "";
  confirm.value = false;
  busy.value = false;
  void load();
}, { immediate: true });
watch(confirm, (open) => { if (!open) switchVersion.value += 1; });
onBeforeUnmount(() => { disposed = true; ++version; clearTimeout(timer); });

function reason(value: string) {
  return ({ available: "Enabled", disabled: "Account disabled", no_plan: "No plan", expired: "Expired", quota_exceeded: "Quota exceeded", node_not_in_plan: "Outside current plan" } as Record<string, string>)[value] ?? value;
}
</script>

<template>
  <section v-if="username" class="subscription-access" aria-label="Node access">
    <div class="access-toolbar">
      <h3>Node access</h3>
      <v-switch :key="switchVersion" :model-value="isActive" :disabled="busy" label="Account enabled" color="success" density="compact" hide-details @update:model-value="toggle" />
      <v-tooltip text="Refresh access status">
        <template #activator="{ props: tip }">
          <v-btn v-bind="tip" icon="mdi-refresh" aria-label="Refresh access status" variant="text" size="small" :disabled="busy" @click="load()" />
        </template>
      </v-tooltip>
      <v-tooltip text="Reconcile node access">
        <template #activator="{ props: tip }">
          <v-btn v-bind="tip" icon="mdi-sync" aria-label="Reconcile node access" variant="text" size="small" :loading="busy" :disabled="!state?.managed" @click="load(true)" />
        </template>
      </v-tooltip>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" density="compact">{{ error }}</v-alert>
    <div v-if="state && !state.managed" class="access-empty">No managed credentials</div>
    <div v-for="server in state?.servers ?? []" :key="server.server_id" class="access-server">
      <div class="access-server-heading">
        <strong>{{ server.server_name }}</strong>
        <v-chip size="small" :color="server.status === 'applied' ? 'success' : server.status === 'failed' ? 'error' : 'warning'" variant="tonal">{{ server.status }}</v-chip>
      </div>
      <div v-for="entry in server.entries" :key="`${entry.inbound_tag}:${entry.email}`" class="access-entry">
        <span>{{ entry.inbound_tag }}</span>
        <span>{{ reason(entry.reason) }}</span>
      </div>
      <p v-if="server.error" class="access-error">{{ server.error }}</p>
    </div>
    <v-dialog v-model="confirm" max-width="440">
      <v-card title="Disable account?">
        <v-card-text>{{ username }} will lose access on managed nodes. Applying this change restarts Xray and disconnects its current connections.</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="confirm = false">Cancel</v-btn>
          <v-btn color="error" @click="update(false)">Disable</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<style scoped>
.subscription-access { min-width: 0; padding-block: 12px; border-block: 1px solid rgb(var(--v-theme-on-surface), 0.12); }
.access-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 36px 36px; align-items: center; gap: 8px; }
.access-server-heading { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.access-toolbar h3 { font-size: 14px; font-weight: 600; margin-right: auto; }
.access-toolbar h3, .access-toolbar :deep(.v-btn) { grid-row: 1; }
.access-toolbar :deep(.v-switch) { grid-column: 1 / -1; grid-row: 2; }
.access-server { padding-block: 10px; min-width: 0; }
.access-server-heading { justify-content: space-between; }
.access-server-heading strong { font-size: 13px; overflow-wrap: anywhere; }
.access-entry { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; font-size: 12px; padding-top: 6px; overflow-wrap: anywhere; }
.access-entry span:last-child { text-align: right; }
.access-empty { font-size: 12px; color: rgb(var(--v-theme-on-surface), 0.6); padding-block: 8px; }
.access-error { color: rgb(var(--v-theme-error)); font-size: 12px; overflow-wrap: anywhere; margin-block: 8px 0; }
</style>
