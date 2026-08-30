<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import PlanNodeAliases from "./PlanNodeAliases.vue";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor.vue";
import type { ManagedNode, SubscriptionAccessResponse } from "../domain/subscriptions";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../services/subscriptions";
import { getPlanManagement, planSettings, removePlan, savePlan, type PlanManagementRead, type PlanManagementResult, type PlanOperation, type PlanSettings } from "../services/plan-management";
import { listSubscriptionTemplates } from "../services/subscription-templates";
import type { SubscriptionTemplate } from "../domain/subscription-templates";

const props = defineProps<{ id: string; mode: PlanOperation; open: boolean; nodes: ManagedNode[] }>();
const emit = defineEmits<{ "update:open": [value: boolean]; changed: [] }>();
const detail = ref<PlanManagementRead | null>(null);
const form = ref<PlanSettings | null>(null);
const result = ref<PlanManagementResult | null>(null);
const busy = ref(false);
const error = ref("");
const acknowledgment = ref(false);
const confirmName = ref("");
const aliasesValid = ref(true);
const rulesValid = ref(true);
const states = reactive<Record<string, SubscriptionAccessResponse>>({});
const templates = ref<SubscriptionTemplate[]>([]);
const stateErrors = reactive<Record<string, string>>({});
let version = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const title = computed(() => props.mode === "edit" ? "Edit plan" : props.mode === "remove" ? "Remove plan" : "Unassign plan");
const removed = computed(() => !!result.value && props.mode !== "edit");
const expectedName = computed(() => props.mode === "unassign" ? props.id : detail.value?.plan.name ?? "");
const options = computed(() => props.nodes.filter(node => !node.removal_id).map(node => ({ title: node.name, value: node.id })));
const selectedNodes = computed(() => form.value?.node_ids.map(id => ({ id, name: props.nodes.find(node => node.id === id)?.name ?? id })) ?? []);
const clashTemplates = computed(() => templates.value.filter(item => item.format === "clash").map(item => ({ title: item.name, value: item.id })));
const surgeTemplates = computed(() => templates.value.filter(item => item.format === "surge").map(item => ({ title: item.name, value: item.id })));
const canSubmit = computed(() => !busy.value && !!detail.value && acknowledgment.value && !removed.value && (props.mode === "edit" ? !!form.value?.name.trim() && aliasesValid.value && rulesValid.value : confirmName.value === expectedName.value));

function resetStatus() {
  clearTimeout(timer);
  for (const key of Object.keys(states)) delete states[key];
  for (const key of Object.keys(stateErrors)) delete stateErrors[key];
}
async function load() {
  const request = ++version;
  resetStatus();
  detail.value = null; form.value = null; result.value = null;
  acknowledgment.value = false; confirmName.value = ""; error.value = "";
  if (!props.open || !props.id) return;
  busy.value = true;
  try {
    const [value, library] = await Promise.all([
      getPlanManagement(props.id, props.mode),
      listSubscriptionTemplates(),
    ]);
    if (request !== version) return;
    templates.value = library.templates;
    detail.value = value;
    form.value = planSettings(value.plan);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Plan request failed";
  } finally { if (request === version) busy.value = false; }
}
async function poll(request: number, retry?: string) {
  clearTimeout(timer);
  const names = result.value?.affected_users ?? [];
  await Promise.all(names.map(async username => {
    try {
      const value = await (retry === username ? syncSubscriptionAccess : getSubscriptionAccess)(username);
      if (request !== version) return;
      states[username] = value;
      delete stateErrors[username];
    } catch (failure) {
      if (request === version) stateErrors[username] = failure instanceof Error ? failure.message : "Access status unavailable";
    }
  }));
  if (props.open && request === version) timer = setTimeout(() => void poll(request), 5000);
}
async function submit() {
  if (!canSubmit.value || !detail.value || !form.value) return;
  const request = ++version;
  busy.value = true; error.value = "";
  resetStatus();
  try {
    const value = props.mode === "edit"
      ? await savePlan(props.id, form.value, detail.value.revision)
      : await removePlan(props.id, props.mode, detail.value.revision, confirmName.value);
    emit("changed");
    if (request !== version) return;
    result.value = value;
    acknowledgment.value = false;
    if (value.plan && value.revision) {
      detail.value.plan = value.plan; detail.value.revision = value.revision;
      form.value = planSettings(value.plan);
    }
    void poll(request);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Plan update failed";
  } finally { if (request === version) busy.value = false; }
}
function setOverride(key: "node_multipliers" | "node_speed_limits" | "node_device_limits", id: string, value: string | number | null) {
  if (!form.value) return;
  if (value === "" || value === null) delete form.value[key][id];
  else form.value[key][id] = Number(value);
}
watch(() => form.value?.node_ids, ids => {
  if (!ids || !form.value) return;
  for (const key of ["node_multipliers", "node_speed_limits", "node_device_limits"] as const) {
    form.value[key] = Object.fromEntries(Object.entries(form.value[key]).filter(([id]) => ids.includes(id)));
  }
});
watch(() => [props.open, props.id, props.mode], () => { busy.value = false; void load(); }, { immediate: true });
onBeforeUnmount(() => { ++version; resetStatus(); });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="760" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="plan-management-dialog">
      <v-card-title class="plan-toolbar">
        <span>{{ title }}</span>
        <v-tooltip text="Reload plan details"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Reload plan details" icon="mdi-refresh" variant="text" size="small" :disabled="busy || removed" @click="load" /></template></v-tooltip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" color="primary" indeterminate class="mb-4" />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <template v-if="detail && form">
          <v-alert v-if="result" type="info" variant="tonal" class="mb-4">{{ mode === 'edit' ? 'Plan saved' : mode === 'remove' ? 'Plan removed' : 'Plan unassigned' }}. {{ result.commands.length }} Agent commands tracked.</v-alert>
          <v-form v-if="mode === 'edit'" id="plan-management-form" class="plan-form" @submit.prevent="submit">
            <v-text-field v-model="form.name" label="Plan name" variant="outlined" density="compact" maxlength="120" hide-details :disabled="busy" />
            <v-textarea v-model="form.description" label="Description" variant="outlined" density="compact" rows="2" maxlength="1000" hide-details :disabled="busy" />
            <div class="plan-fields">
              <v-text-field v-model.number="form.traffic_limit_gb" label="Traffic quota (GiB)" type="number" min="0.000001" step="any" variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-select v-model="form.traffic_mode" :items="[{ title: 'One-way billing (x1)', value: 'oneway' }, { title: 'Two-way billing (x2)', value: 'twoway' }]" label="Traffic billing factor" variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-text-field v-model.number="form.cycle_days" label="New duration (days)" type="number" min="1" step="1" variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-text-field v-model.number="form.speed_limit_mbps" label="Default speed (Mbps)" type="number" min="0" step="any" variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-text-field v-model.number="form.device_limit" label="Default connections" type="number" min="0" step="1" variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-select v-model="form.reset_day" :items="Array.from({ length: 31 }, (_, i) => i + 1)" label="New reset day (UTC)" variant="outlined" density="compact" hide-details :disabled="busy || !form.is_reset" />
            </div>
            <v-switch v-model="form.is_reset" label="Monthly reset for new assignments" color="primary" density="compact" hide-details :disabled="busy" />
            <div class="plan-fields">
              <v-select v-model="form.clash_template_id" :items="clashTemplates" label="Clash template" clearable variant="outlined" density="compact" hide-details :disabled="busy" />
              <v-select v-model="form.surge_template_id" :items="surgeTemplates" label="Surge template" clearable variant="outlined" density="compact" hide-details :disabled="busy" />
            </div>
            <v-autocomplete v-model="form.node_ids" :items="options" label="Plan nodes" multiple chips closable-chips variant="outlined" density="compact" hide-details :disabled="busy" />
            <PlanNodeAliases v-model:names="form.node_name_overrides" v-model:enabled="form.node_name_override_enabled" :nodes="selectedNodes" :disabled="busy" @valid="aliasesValid = $event">
              <template #default="{ node }">
              <div class="plan-overrides">
                <v-text-field :model-value="form.node_multipliers[node.id] ?? ''" :aria-label="`${node.name}: multiplier`" label="Billing multiplier" placeholder="1" type="number" min="0.000001" step="any" variant="outlined" density="compact" hide-details :disabled="busy" @update:model-value="setOverride('node_multipliers', node.id, $event)" />
                <v-text-field :model-value="form.node_speed_limits[node.id] ?? ''" :aria-label="`${node.name}: speed`" label="Speed (Mbps)" placeholder="Inherit" type="number" min="0" step="any" variant="outlined" density="compact" hide-details :disabled="busy" @update:model-value="setOverride('node_speed_limits', node.id, $event)" />
                <v-text-field :model-value="form.node_device_limits[node.id] ?? ''" :aria-label="`${node.name}: connections`" label="Connections" placeholder="Inherit" type="number" min="0" step="1" variant="outlined" density="compact" hide-details :disabled="busy" @update:model-value="setOverride('node_device_limits', node.id, $event)" />
              </div>
              </template>
            </PlanNodeAliases>
            <AutoSpeedRuleEditor v-model="form.auto_speed_rules" :disabled="busy" @valid="rulesValid = $event" />
          </v-form>
          <h3 v-else>{{ detail.plan.name }}</h3>
          <section class="plan-subscribers" aria-label="Affected subscribers">
            <h3>Subscribers</h3>
            <div v-if="!detail.users.length">None</div>
            <div v-for="user in detail.users" :key="user.username" class="subscriber-row"><span>{{ user.username }}</span><span>{{ user.managed ? 'Managed' : 'Preview only' }}</span></div>
          </section>
          <v-alert v-for="warning in (result?.warnings ?? (mode === 'edit' ? detail.warnings : []))" :key="warning" type="warning" variant="tonal" class="my-4">{{ warning }}</v-alert>
          <template v-if="!removed">
            <v-alert type="warning" variant="tonal" class="my-4">{{ mode === 'edit' ? 'Node and limit changes are applied to managed subscribers.' : 'The subscription becomes unavailable. Stored credentials and usage are retained; remote revocation needs Agent confirmation.' }} Applying runtime changes can restart Xray and disconnect its current clients. Offline or failed Agents remain pending or failed.</v-alert>
            <v-text-field v-if="mode !== 'edit'" v-model="confirmName" :label="mode === 'unassign' ? 'Confirm username' : 'Confirm plan name'" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-checkbox v-model="acknowledgment" label="I accept the runtime restart and pending changes" color="primary" hide-details :disabled="busy" />
          </template>
          <section v-if="result?.affected_users.length" class="plan-status" aria-label="Plan deployment status">
            <h3>Agent status</h3>
            <div v-for="username in result.affected_users" :key="username" class="plan-status-user">
              <div class="plan-status-heading"><strong>{{ username }}</strong><v-tooltip text="Retry access synchronization"><template #activator="{ props: tip }"><v-btn v-bind="tip" :aria-label="`Retry access for ${username}`" icon="mdi-sync" variant="text" size="small" @click="poll(version, username)" /></template></v-tooltip></div>
              <div v-if="!states[username] && !stateErrors[username]">Loading</div>
              <div v-if="states[username] && !states[username].managed">No managed credentials</div>
              <div v-for="server in states[username]?.servers ?? []" :key="server.server_id" class="plan-status-server">
                <span>{{ server.server_name }}</span><v-chip :color="server.status === 'applied' ? 'success' : server.status === 'failed' ? 'error' : 'warning'" size="small" variant="tonal">{{ server.status }}</v-chip>
                <p v-if="server.error">{{ server.error }}</p>
              </div>
              <p v-if="stateErrors[username]" class="text-error">{{ stateErrors[username] }}</p>
            </div>
          </section>
        </template>
      </v-card-text>
      <v-card-actions class="plan-actions">
        <v-btn :disabled="busy" @click="emit('update:open', false)">{{ result ? 'Close' : 'Cancel' }}</v-btn>
        <v-spacer />
        <v-btn v-if="!removed" :color="mode === 'edit' ? 'primary' : 'error'" :prepend-icon="mode === 'edit' ? 'mdi-content-save' : mode === 'remove' ? 'mdi-delete-outline' : 'mdi-link-off'" :disabled="!canSubmit" @click="submit">{{ mode === 'edit' ? 'Save' : mode === 'remove' ? 'Remove' : 'Unassign' }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.plan-management-dialog, .plan-management-dialog :deep(*) { letter-spacing: 0; }
.plan-management-dialog :deep(.v-alert__content) { font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.plan-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; white-space: normal; font-size: 18px; gap: 12px; }
.plan-form { display: grid; gap: 16px; min-width: 0; }
.plan-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.plan-node, .plan-status-user { padding-block: 12px; border-top: 1px solid rgb(var(--v-theme-on-surface), .12); }
.plan-node strong { display: block; font-size: 13px; margin-bottom: 12px; overflow-wrap: anywhere; }
.plan-overrides { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.plan-subscribers, .plan-status { padding-top: 20px; font-size: 13px; }
.plan-management-dialog h3 { font-size: 14px; margin-bottom: 12px; overflow-wrap: anywhere; }
.subscriber-row, .plan-status-server, .plan-status-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding-block: 6px; flex-wrap: wrap; overflow-wrap: anywhere; }
.plan-status-server p { flex-basis: 100%; margin: 0; color: rgb(var(--v-theme-error)); }
.plan-actions { flex-wrap: wrap; }
.plan-management-dialog :deep(.v-label) { white-space: normal; }
.plan-management-dialog :deep(.v-field__input), .plan-management-dialog :deep(.v-chip) { min-width: 0; max-width: 100%; }
@media (max-width: 600px) { .plan-fields, .plan-overrides { grid-template-columns: minmax(0, 1fr); } }
</style>
