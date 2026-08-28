<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { ManagedNode, SubscriptionAccessResponse } from "../domain/subscriptions";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../services/subscriptions";
import {
  getNodeManagement, getNodeRemoval, nodeSettings, parseNodeObject, removeNode, retryNodeRemoval, saveNode,
  type NodeManagementRead, type NodeOperation, type NodeRemoval, type NodeSettings,
} from "../services/node-management";

const props = defineProps<{ id: string; mode: NodeOperation; open: boolean; nodes: ManagedNode[] }>();
const emit = defineEmits<{ "update:open": [value: boolean]; changed: [] }>();
const detail = ref<NodeManagementRead | null>(null);
const form = ref<NodeSettings | null>(null);
const removal = ref<NodeRemoval | null>(null);
const access = ref<SubscriptionAccessResponse[]>([]);
const config = ref("{}");
const clientTemplate = ref("{}");
const error = ref("");
const statusError = ref("");
const busy = ref(false);
const syncing = ref(false);
const saved = ref(false);
const confirmName = ref("");
const acknowledged = ref(false);
const unmanaged = ref(false);
const completed = ref(false);
let version = 0;
let pollVersion = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const title = computed(() => removal.value ? "Node removal" : props.mode === "edit" ? "Edit node" : "Remove node");
const name = computed(() => detail.value?.node.name ?? removal.value?.name ?? "");
const warnings = computed(() => removal.value?.warnings ?? detail.value?.warnings ?? []);
const selectable = computed(() => props.nodes.filter(node => node.id !== props.id && !node.removal_id));
const parents = computed(() => selectable.value.filter(node => node.server_id === detail.value?.node.server_id && node.inbound_tag === detail.value.node.inbound_tag && node.protocol === detail.value.node.protocol));
const canSubmit = computed(() => !busy.value && !!form.value && !!detail.value && !removal.value && acknowledged.value
  && (props.mode === "edit" ? !!form.value.name.trim()
    : !detail.value.blockers.length && confirmName.value === detail.value.node.name && (!warnings.value.length || unmanaged.value)));

function stopPolling() { clearTimeout(timer); ++pollVersion; syncing.value = false; }
function acceptRemoval(value: NodeRemoval) {
  removal.value = value;
  if (value.status === "completed" && !completed.value) { completed.value = true; emit("changed"); }
}
async function poll(request: number, retry = false) {
  clearTimeout(timer);
  const current = ++pollVersion;
  syncing.value = retry;
  try {
    if (removal.value) {
      const value = await (retry ? retryNodeRemoval : getNodeRemoval)(removal.value.id);
      if (request !== version || current !== pollVersion) return;
      acceptRemoval(value);
    } else {
      const values: SubscriptionAccessResponse[] = [];
      for (const user of access.value) values.push(await (retry ? syncSubscriptionAccess : getSubscriptionAccess)(user.username));
      if (request !== version || current !== pollVersion) return;
      access.value = values;
    }
    statusError.value = "";
  } catch (failure) {
    if (request === version && current === pollVersion) statusError.value = failure instanceof Error ? failure.message : "Node status unavailable";
  } finally {
    if (request === version && current === pollVersion) {
      syncing.value = false;
      if (props.open && !completed.value) timer = setTimeout(() => void poll(request), 5000);
    }
  }
}
async function load() {
  const request = ++version;
  stopPolling();
  detail.value = null; form.value = null; removal.value = null; access.value = [];
  saved.value = false; completed.value = false; error.value = ""; statusError.value = "";
  confirmName.value = ""; acknowledged.value = false; unmanaged.value = false; busy.value = false;
  if (!props.open || !props.id) return;
  busy.value = true;
  try {
    const value = await getNodeManagement(props.id);
    if (request !== version) return;
    detail.value = value; form.value = nodeSettings(value.node); access.value = value.access;
    config.value = JSON.stringify(value.node.config, null, 2);
    clientTemplate.value = JSON.stringify(value.node.client_template, null, 2);
    if (value.node.removal_id) {
      const job = await getNodeRemoval(value.node.removal_id);
      if (request !== version) return;
      acceptRemoval(job);
    }
    if (!completed.value) timer = setTimeout(() => void poll(request), 5000);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Node request failed";
  } finally { if (request === version) busy.value = false; }
}
async function submit() {
  if (!canSubmit.value || !detail.value || !form.value) return;
  const request = ++version;
  stopPolling(); busy.value = true; error.value = "";
  try {
    if (props.mode === "edit") {
      const value = await saveNode(props.id, {
        ...form.value, config: parseNodeObject(config.value, "Node config"),
        client_template: parseNodeObject(clientTemplate.value, "Client template"),
      }, detail.value.revision);
      emit("changed");
      if (request !== version) return;
      detail.value = value; form.value = nodeSettings(value.node); access.value = value.access; saved.value = true;
    } else {
      const value = await removeNode(props.id, detail.value.revision, confirmName.value, unmanaged.value);
      emit("changed");
      if (request !== version) return;
      acceptRemoval(value);
    }
    acknowledged.value = false;
    if (!completed.value) void poll(request);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Node update failed";
  } finally { if (request === version) busy.value = false; }
}
watch(() => [props.open, props.id, props.mode], () => void load(), { immediate: true });
onBeforeUnmount(() => { ++version; stopPolling(); });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="760" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="node-management-dialog">
      <v-card-title class="node-toolbar">
        <span>{{ title }}</span>
        <v-tooltip text="Reload node details"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Reload node details" icon="mdi-refresh" variant="text" size="small" :disabled="busy || completed" @click="removal ? poll(version) : load()" /></template></v-tooltip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" indeterminate color="primary" class="mb-4" />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <div class="node-heading"><strong>{{ name }}</strong><v-chip v-if="detail" size="small" variant="tonal">{{ detail.node.protocol }} / {{ detail.node.node_type }}</v-chip></div>
        <v-alert v-if="saved" type="success" variant="tonal" class="my-4">Node saved</v-alert>
        <v-alert v-if="removal" :type="completed ? 'success' : removal.status === 'failed' ? 'error' : 'info'" variant="tonal" class="my-4">{{ completed ? 'Node removed' : removal.status === 'failed' ? 'Removal needs attention' : 'Removal pending Agent confirmation' }}</v-alert>
        <template v-if="detail && form && !removal">
          <div v-if="mode === 'edit'" class="node-form">
            <v-text-field v-model="form.name" label="Node name" maxlength="120" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-text-field v-model="form.tag" label="Primary tag" maxlength="120" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-combobox v-model="form.tags" label="Tags" chips multiple closable-chips variant="outlined" density="compact" hide-details :disabled="busy" />
            <template v-if="detail.node.node_type === 'routed'">
              <v-select v-model="form.parent_id" label="Parent node" :items="parents" item-title="name" item-value="id" clearable variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-select v-model="form.target_node_id" label="Target node" :items="selectable" item-title="name" item-value="id" clearable variant="outlined" density="compact" hide-details :disabled="busy" />
            </template>
            <v-textarea v-model="config" label="Node config" rows="5" class="node-json" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-textarea v-model="clientTemplate" label="Client template" rows="4" class="node-json" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-switch v-model="form.enabled" label="Enabled" color="primary" hide-details :disabled="busy" />
          </div>
          <template v-else>
            <div class="node-impact">{{ detail.nodes.length }} nodes / {{ detail.plans.length }} plans / {{ detail.credential_count }} credentials</div>
            <section class="node-section" aria-label="Affected nodes"><h3>Nodes</h3><p v-for="node in detail.nodes" :key="node.id">{{ node.name }}</p></section>
            <section v-if="detail.plans.length" class="node-section" aria-label="Affected plans"><h3>Plans</h3><p v-for="plan in detail.plans" :key="plan.id">{{ plan.name }}</p></section>
            <v-alert v-for="blocker in detail.blockers" :key="blocker" type="error" variant="tonal" class="my-4">{{ blocker }}</v-alert>
            <v-alert type="warning" variant="tonal" class="my-4">Selected nodes leave subscriptions immediately. Remote resources remain until the Agent confirms cleanup. Shared listeners, subscriber accounts, links and charged traffic are retained. Removal cannot be cancelled.</v-alert>
            <v-text-field v-model="confirmName" label="Confirm node name" variant="outlined" density="compact" hide-details :disabled="busy" />
          </template>
        </template>
        <section v-if="removal || (detail && mode === 'remove')" class="node-section" aria-label="Node resource cleanup">
          <div class="node-heading"><h3>Remote resources</h3><v-tooltip v-if="removal" text="Retry node removal"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Retry node removal" icon="mdi-sync" size="small" variant="text" :loading="syncing" :disabled="busy || completed" @click="poll(version, true)" /></template></v-tooltip></div>
          <div v-for="server in removal?.servers ?? detail?.servers ?? []" :key="server.server_id" class="node-server">
            <div class="node-heading"><strong>{{ server.server_name }}</strong><v-chip v-if="removal" :color="server.error ? 'error' : server.phase === 'completed' ? 'success' : 'warning'" variant="tonal" size="small">{{ server.error ? 'Failed' : server.phase }}</v-chip></div>
            <dl class="node-resources">
              <dt>Remove inbounds</dt><dd>{{ server.inbound_tags.join(', ') || 'None' }}</dd>
              <dt>Remove outbounds</dt><dd>{{ server.outbound_tags.join(', ') || 'None' }}</dd>
              <dt>Keep shared inbounds</dt><dd>{{ server.retained_inbound_tags.join(', ') || 'None' }}</dd>
              <dt>Keep shared outbounds</dt><dd>{{ server.retained_outbound_tags.join(', ') || 'None' }}</dd>
            </dl>
            <p v-if="server.error" class="text-error">{{ server.error }}</p>
          </div>
        </section>
        <v-alert v-for="warning in warnings" :key="warning" type="warning" variant="tonal" class="my-4">{{ warning }}</v-alert>
        <template v-if="detail && !removal">
          <v-checkbox v-if="mode === 'remove' && warnings.length" v-model="unmanaged" label="I accept responsibility for unmanaged resources" hide-details :disabled="busy" />
          <v-checkbox v-model="acknowledged" label="I accept Xray restarts, disconnected clients and pending remote changes" hide-details :disabled="busy" />
        </template>
        <section v-if="!removal && access.length" class="node-section" aria-label="Node subscription access">
          <div class="node-heading"><h3>Subscription access</h3><v-tooltip text="Retry access synchronization"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Retry node access synchronization" icon="mdi-sync" variant="text" size="small" :loading="syncing" :disabled="busy" @click="poll(version, true)" /></template></v-tooltip></div>
          <div v-for="user in access" :key="user.username" class="node-server">
            <strong>{{ user.username }}</strong>
            <div v-for="server in user.servers" :key="server.server_id" class="node-heading">
              <span>{{ server.server_name }}</span><v-chip size="small" variant="tonal" :color="server.status === 'applied' ? 'success' : server.status === 'failed' ? 'error' : 'warning'">{{ server.status }}</v-chip>
              <p v-if="server.error" class="text-error">{{ server.error }}</p>
            </div>
          </div>
        </section>
        <p v-if="statusError" class="text-error">{{ statusError }}</p>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="emit('update:open', false)">{{ saved || removal ? 'Close' : 'Cancel' }}</v-btn>
        <v-spacer />
        <v-btn v-if="!removal" :prepend-icon="mode === 'edit' ? 'mdi-content-save' : 'mdi-delete-outline'" :color="mode === 'edit' ? 'primary' : 'error'" :disabled="!canSubmit" @click="submit">{{ mode === 'edit' ? 'Save' : 'Remove' }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.node-management-dialog, .node-management-dialog :deep(*) { letter-spacing: 0; }
.node-management-dialog :deep(.v-label) { white-space: normal; }
.node-management-dialog :deep(.v-alert__content) { font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.node-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; gap: 12px; font-size: 18px; white-space: normal; }
.node-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; overflow-wrap: anywhere; }
.node-form { display: grid; gap: 16px; margin-top: 20px; min-width: 0; }
.node-json :deep(textarea) { font: 12px/1.6 monospace; }
.node-impact { margin-top: 16px; font-size: 13px; }
.node-section { margin-top: 20px; font-size: 13px; }
.node-section h3 { font-size: 14px; }
.node-section p { margin-top: 8px; overflow-wrap: anywhere; }
.node-server { border-top: 1px solid rgb(var(--v-theme-on-surface), .12); padding-block: 12px; margin-top: 10px; }
.node-resources { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); gap: 8px 14px; margin-top: 14px; overflow-wrap: anywhere; }
.node-resources dt { color: rgb(var(--v-theme-on-surface), .7); }
</style>
