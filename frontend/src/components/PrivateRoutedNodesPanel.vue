<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import type {
  PrivateRoutedNode,
  PrivateRoutedNodesResponse,
  PrivateRoutedNodeStatus,
} from "../domain/private-routed-nodes";
import {
  createSubscriberPrivateRoute,
  deleteSubscriberPrivateRoute,
  listSubscriberPrivateRoutes,
} from "../services/private-routed-nodes";

const state = ref<PrivateRoutedNodesResponse | null>(null);
const loading = ref(false);
const busy = ref("");
const confirming = ref("");
const error = ref("");
const form = reactive({ label: "", parent_id: "", target_node_id: "" });
let timer: ReturnType<typeof setTimeout> | null = null;
let disposed = false;

const parentOptions = computed(() => (state.value?.candidates ?? [])
  .filter(item => item.can_parent)
  .map(item => ({ title: item.name, value: item.id })));
const targetOptions = computed(() => (state.value?.candidates ?? [])
  .filter(item => item.can_target && item.id !== form.parent_id)
  .map(item => ({ title: item.name, value: item.id })));
const canCreate = computed(() => Boolean(
  state.value?.policy.enabled
  && state.value.used_nodes < state.value.policy.max_nodes
  && state.value.actions_today < state.value.policy.daily_limit
  && /^[A-Za-z0-9-]{2,32}$/.test(form.label.trim())
  && form.parent_id
  && form.target_node_id
  && form.parent_id !== form.target_node_id
  && !busy.value,
));

function statusColor(status: PrivateRoutedNodeStatus) {
  if (status === "active") return "success";
  if (status === "failed") return "error";
  return "warning";
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = null;
  if (!disposed && state.value?.nodes.some(item => ["provisioning", "removing"].includes(item.status))) {
    timer = setTimeout(() => void load(true), 2000);
  }
}

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    state.value = await listSubscriberPrivateRoutes();
    if (form.parent_id && !parentOptions.value.some(item => item.value === form.parent_id)) {
      form.parent_id = "";
    }
    if (form.target_node_id && !targetOptions.value.some(item => item.value === form.target_node_id)) {
      form.target_node_id = "";
    }
    if (silent) error.value = "";
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Private routes unavailable";
  } finally {
    loading.value = false;
    schedule();
  }
}

async function create() {
  if (!canCreate.value) return;
  busy.value = "create";
  error.value = "";
  try {
    await createSubscriberPrivateRoute({
      label: form.label.trim(),
      parent_id: form.parent_id,
      target_node_id: form.target_node_id,
    });
    Object.assign(form, { label: "", parent_id: "", target_node_id: "" });
    await load(true);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Private route creation failed";
  } finally {
    busy.value = "";
  }
}

async function remove(node: PrivateRoutedNode) {
  if (confirming.value !== node.id) {
    confirming.value = node.id;
    return;
  }
  busy.value = node.id;
  error.value = "";
  try {
    await deleteSubscriberPrivateRoute(node.id);
    confirming.value = "";
    await load(true);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Private route deletion failed";
  } finally {
    busy.value = "";
  }
}

onMounted(() => void load());
onBeforeUnmount(() => {
  disposed = true;
  if (timer) clearTimeout(timer);
});
</script>

<template>
  <section class="private-routes" aria-label="Private routes">
    <div class="route-heading">
      <div><p class="route-label">Private routes</p><h3>Routed exits</h3></div>
      <div v-if="state" class="route-counters">
        <v-chip size="small" variant="tonal">{{ state.used_nodes }}/{{ state.policy.max_nodes }}</v-chip>
        <v-chip size="small" variant="tonal">{{ state.actions_today }}/{{ state.policy.daily_limit }} today</v-chip>
      </div>
    </div>
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <v-alert v-if="error" type="error" variant="tonal" density="compact">{{ error }}</v-alert>
    <v-alert v-if="state && !state.policy.enabled" type="info" variant="tonal" density="compact">Private routes are disabled.</v-alert>

    <form v-if="state?.policy.enabled" class="route-form" @submit.prevent="create">
      <v-text-field v-model="form.label" label="Label" maxlength="32" variant="outlined" density="compact" hide-details />
      <v-select v-model="form.parent_id" :items="parentOptions" label="Entry node" variant="outlined" density="compact" hide-details />
      <v-select v-model="form.target_node_id" :items="targetOptions" label="Exit node" variant="outlined" density="compact" hide-details />
      <v-btn type="submit" color="primary" prepend-icon="mdi-routes" :loading="busy === 'create'" :disabled="!canCreate">Create</v-btn>
    </form>

    <div v-if="state && !state.nodes.length" class="route-empty">No private routes.</div>
    <div v-for="node in state?.nodes" :key="node.id" class="route-row">
      <div class="route-main">
        <strong>{{ node.name }}</strong>
        <span>{{ node.parent_name }} <v-icon icon="mdi-arrow-right" size="14" /> {{ node.target_name }}</span>
        <span v-if="node.last_error" class="route-error">{{ node.last_error }}</span>
      </div>
      <div class="route-actions">
        <v-chip :color="statusColor(node.status)" size="small" variant="tonal">{{ node.status }}</v-chip>
        <template v-if="!['provisioning', 'removing'].includes(node.status)">
          <v-btn v-if="confirming === node.id" size="small" color="error" variant="tonal" :loading="busy === node.id" @click="remove(node)">Confirm</v-btn>
          <v-tooltip v-else text="Delete private route"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-delete-outline" :aria-label="`Delete private route ${node.name}`" variant="text" :disabled="Boolean(busy)" @click="remove(node)" /></template></v-tooltip>
          <v-btn v-if="confirming === node.id" icon="mdi-close" aria-label="Cancel private route deletion" variant="text" :disabled="busy === node.id" @click="confirming = ''" />
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.private-routes, .private-routes :deep(*) { letter-spacing: 0; }
.private-routes { padding-top: 28px; display: grid; gap: 18px; min-width: 0; }
.route-heading, .route-counters, .route-actions { display: flex; align-items: center; gap: 8px; }
.route-heading { justify-content: space-between; gap: 16px; }
.route-label { color: #66736f; font-size: 12px; }
.route-heading h3 { margin-top: 5px; font-size: 20px; }
.route-counters { flex-wrap: wrap; justify-content: flex-end; }
.route-form { display: grid; grid-template-columns: minmax(120px, .7fr) minmax(160px, 1fr) minmax(160px, 1fr) auto; gap: 12px; align-items: center; }
.route-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; border-bottom: 1px solid #e5ece8; padding: 14px 0; }
.route-main { min-width: 0; display: grid; gap: 5px; }
.route-main span { color: #66736f; font-size: 13px; overflow-wrap: anywhere; }
.route-main .route-error { color: #b42318; }
.route-actions { justify-content: flex-end; }
.route-empty { color: #66736f; font-size: 14px; padding: 18px 0; }
@media (max-width: 760px) {
  .route-form { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .route-form :deep(.v-btn) { min-height: 40px; }
}
@media (max-width: 480px) {
  .route-heading, .route-row { align-items: flex-start; }
  .route-heading { flex-direction: column; }
  .route-counters { justify-content: flex-start; }
  .route-form, .route-row { grid-template-columns: minmax(0, 1fr); }
  .route-actions { justify-content: flex-start; flex-wrap: wrap; }
}
</style>
