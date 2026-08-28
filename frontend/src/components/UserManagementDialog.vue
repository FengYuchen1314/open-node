<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { ManagedNode, SubscriptionAccessResponse } from "../domain/subscriptions";
import { validUserLimits } from "../domain/user-limits";
import UserLimitEditor from "./UserLimitEditor.vue";
import { getSubscriptionAccess, syncSubscriptionAccess } from "../services/subscriptions";
import { getUserManagement, getUserRemoval, removeUser, retryUserRemoval, saveUser, userSettings, type UserManagementRead, type UserOperation, type UserRemoval, type UserSettings } from "../services/user-management";

const props = defineProps<{ username: string; mode: UserOperation; removalId: string | null; open: boolean; nodes: ManagedNode[] }>();
const emit = defineEmits<{ "update:open": [value: boolean]; changed: [] }>();
const detail = ref<UserManagementRead | null>(null);
const form = ref<UserSettings | null>(null);
const removal = ref<UserRemoval | null>(null);
const access = ref<SubscriptionAccessResponse | null>(null);
const busy = ref(false);
const syncing = ref(false);
const saved = ref(false);
const error = ref("");
const statusError = ref("");
const confirmName = ref("");
const acknowledgment = ref(false);
const unmanaged = ref(false);
let version = 0;
let pollVersion = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const completed = ref(false);
const tab = ref("profile");
const title = computed(() => removal.value ? "User removal" : props.mode === "edit" ? "Edit user" : "Remove user");
const servers = computed(() => removal.value?.servers ?? access.value?.servers ?? []);
const warnings = computed(() => removal.value?.warnings ?? detail.value?.warnings ?? []);
const canSubmit = computed(() => !busy.value && !!detail.value && !!form.value && !removal.value && acknowledgment.value
  && (props.mode === "edit" ? !!form.value.display_name.trim() && validUserLimits(form.value.limit_overrides)
    : !detail.value.blockers.length && confirmName.value === props.username && (!warnings.value.length || unmanaged.value)));

function stopPolling() { clearTimeout(timer); ++pollVersion; syncing.value = false; }
function acceptRemoval(value: UserRemoval) {
  removal.value = value;
  if (value.status === "completed" && !completed.value) { completed.value = true; emit("changed"); }
}
async function poll(request: number, retry = false) {
  clearTimeout(timer);
  const current = ++pollVersion;
  syncing.value = retry;
  try {
    if (removal.value) {
      const value = await (retry ? retryUserRemoval : getUserRemoval)(removal.value.id);
      if (request !== version || current !== pollVersion) return;
      acceptRemoval(value);
    } else {
      const value = await (retry ? syncSubscriptionAccess : getSubscriptionAccess)(props.username);
      if (request !== version || current !== pollVersion) return;
      access.value = value;
    }
    statusError.value = "";
  } catch (failure) {
    if (request === version && current === pollVersion) statusError.value = failure instanceof Error ? failure.message : "User status unavailable";
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
  detail.value = null; form.value = null; removal.value = null; access.value = null;
  saved.value = false; error.value = ""; statusError.value = "";
  confirmName.value = ""; acknowledgment.value = false; unmanaged.value = false; completed.value = false;
  tab.value = "profile";
  busy.value = false;
  if (!props.open || !props.username) return;
  busy.value = true;
  try {
    if (props.removalId) {
      const value = await getUserRemoval(props.removalId);
      if (request !== version) return;
      acceptRemoval(value);
    } else {
      const value = await getUserManagement(props.username);
      if (request !== version) return;
      detail.value = value; form.value = userSettings(value.user); access.value = value.access;
      if (value.user.removal_id) {
        const job = await getUserRemoval(value.user.removal_id);
        if (request !== version) return;
        acceptRemoval(job);
      }
    }
    if (!completed.value) timer = setTimeout(() => void poll(request), 5000);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "User request failed";
  } finally { if (request === version) busy.value = false; }
}
async function submit() {
  if (!canSubmit.value || !detail.value || !form.value) return;
  const request = ++version;
  stopPolling(); busy.value = true; error.value = "";
  try {
    if (props.mode === "edit") {
      const value = await saveUser(props.username, form.value, detail.value.revision);
      emit("changed");
      if (request !== version) return;
      detail.value = value; form.value = userSettings(value.user); access.value = value.access; saved.value = true;
    } else {
      const value = await removeUser(props.username, detail.value.revision, confirmName.value, unmanaged.value);
      emit("changed");
      if (request !== version) return;
      acceptRemoval(value);
    }
    acknowledgment.value = false;
    if (!completed.value) void poll(request);
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "User update failed";
  } finally { if (request === version) busy.value = false; }
}
watch(() => [props.open, props.username, props.mode, props.removalId], () => void load(), { immediate: true });
onBeforeUnmount(() => { ++version; stopPolling(); });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="680" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="user-management-dialog">
      <v-card-title class="user-toolbar">
        <span>{{ title }}</span>
        <v-tooltip text="Reload user details"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Reload user details" icon="mdi-refresh" variant="text" size="small" :disabled="busy || completed" @click="removal ? poll(version) : load()" /></template></v-tooltip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" color="primary" indeterminate class="mb-4" />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <div class="user-heading"><strong>{{ username }}</strong><v-chip v-if="detail" size="small" variant="tonal">{{ detail.user.role }}</v-chip></div>
        <v-alert v-if="saved" type="success" variant="tonal" class="my-4">User saved</v-alert>
        <v-alert v-if="removal" :type="removal.status === 'completed' ? 'success' : removal.status === 'failed' ? 'error' : 'info'" variant="tonal" class="my-4">{{ removal.status === 'completed' ? 'User removed' : removal.status === 'failed' ? 'Removal needs attention' : 'Removal pending Agent confirmation' }}</v-alert>
        <template v-if="detail && form && !removal">
          <v-tabs v-if="mode === 'edit'" v-model="tab" density="compact" color="primary" class="mb-6"><v-tab value="profile">Profile</v-tab><v-tab value="limits">Limits</v-tab></v-tabs>
          <div v-if="mode === 'edit' && tab === 'profile'" class="user-form">
            <v-text-field v-model="form.display_name" label="Display name" maxlength="120" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-text-field v-model="form.email" label="Email" maxlength="255" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-textarea v-model="form.remark" label="Remark" maxlength="1000" rows="3" variant="outlined" density="compact" hide-details :disabled="busy" />
            <v-switch v-model="form.is_active" label="Active" color="primary" hide-details :disabled="busy || (detail.user.role === 'admin' && form.is_active)" />
          </div>
          <UserLimitEditor v-if="mode === 'edit' && tab === 'limits'" :key="detail.revision" v-model="form.limit_overrides" :nodes="nodes" :current="detail.limits" :disabled="busy" />
          <template v-if="mode === 'remove'">
            <div class="user-impact">{{ detail.credential_count }} stored credentials</div>
            <v-alert v-for="blocker in detail.blockers" :key="blocker" type="error" variant="tonal" class="my-4">{{ blocker }}</v-alert>
            <v-alert type="warning" variant="tonal" class="my-4">Subscription links stop working immediately. The profile and user traffic ledger are removed after Agent confirmation. Command history, revocation fingerprints, plans and shared nodes remain. Removal cannot be cancelled after confirmation.</v-alert>
            <v-text-field v-model="confirmName" label="Confirm username" variant="outlined" density="compact" hide-details :disabled="busy" />
          </template>
        </template>
        <v-alert v-for="warning in warnings" :key="warning" type="warning" variant="tonal" class="my-4">{{ warning }}</v-alert>
        <template v-if="detail && !removal">
          <v-checkbox v-if="mode === 'remove' && warnings.length" v-model="unmanaged" label="I accept responsibility for unmanaged credential cleanup" hide-details :disabled="busy" />
          <v-alert type="warning" variant="tonal" class="my-4">Runtime changes can restart Xray and disconnect current clients. Offline credentials may still forward until the Agent confirms withdrawal.</v-alert>
          <v-checkbox v-model="acknowledgment" label="I accept runtime restarts and pending changes" hide-details :disabled="busy" />
        </template>
        <section v-if="access || removal" class="user-status" aria-label="User deployment status">
          <div class="user-status-heading"><h3>Agent status</h3><v-tooltip text="Retry synchronization"><template #activator="{ props: tip }"><v-btn v-bind="tip" aria-label="Retry user synchronization" icon="mdi-sync" variant="text" size="small" :loading="syncing" :disabled="busy || completed" @click="poll(version, true)" /></template></v-tooltip></div>
          <p v-if="!servers.length">No managed credentials</p>
          <div v-for="server in servers" :key="server.server_id" class="user-status-server">
            <div class="user-status-heading"><strong>{{ server.server_name }}</strong><v-chip :color="server.status === 'applied' ? 'success' : server.status === 'failed' ? 'error' : 'warning'" size="small" variant="tonal">{{ server.status }}</v-chip></div>
            <div v-for="entry in server.entries" :key="entry.inbound_tag + entry.email" class="user-entry"><span>{{ entry.inbound_tag }}</span><span>{{ server.status === 'applied' ? (entry.enabled ? 'Enabled' : 'Disabled') : (entry.enabled ? 'Enable requested' : 'Disable requested') }}</span></div>
            <p v-if="server.error" class="text-error">{{ server.error }}</p>
          </div>
          <p v-if="statusError" class="text-error">{{ statusError }}</p>
        </section>
      </v-card-text>
      <v-card-actions class="user-actions">
        <v-btn :disabled="busy" @click="emit('update:open', false)">{{ saved || removal ? 'Close' : 'Cancel' }}</v-btn>
        <v-spacer />
        <v-btn v-if="!removal" :prepend-icon="mode === 'edit' ? 'mdi-content-save' : 'mdi-delete-outline'" :color="mode === 'edit' ? 'primary' : 'error'" :disabled="!canSubmit" @click="submit">{{ mode === 'edit' ? 'Save' : 'Remove' }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.user-management-dialog, .user-management-dialog :deep(*) { letter-spacing: 0; }
.user-management-dialog :deep(.v-alert__content) { font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.user-management-dialog :deep(.v-label) { white-space: normal; }
.user-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; gap: 12px; font-size: 18px; white-space: normal; }
.user-heading, .user-status-heading, .user-entry { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; overflow-wrap: anywhere; }
.user-heading { margin-bottom: 20px; font-size: 14px; }
.user-form { display: grid; gap: 16px; min-width: 0; }
.user-impact, .user-status { font-size: 13px; }
.user-status { padding-top: 18px; }
.user-status h3 { font-size: 14px; }
.user-status-server { border-top: 1px solid rgb(var(--v-theme-on-surface), .12); margin-top: 12px; padding-block: 12px; }
.user-entry { margin-top: 10px; }
.user-status p { margin-top: 10px; overflow-wrap: anywhere; }
.user-actions { flex-wrap: wrap; }
</style>
