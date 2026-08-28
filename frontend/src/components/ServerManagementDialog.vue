<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { getServerRemoval, getServerSettings, removeServer, updateServerSettings, type RemovalPreview, type ServerSettings } from "../services/server-management";

const props = defineProps<{ serverId: string; mode: "edit" | "remove"; open: boolean }>();
const emit = defineEmits<{ "update:open": [value: boolean]; changed: [] }>();
const busy = ref(false);
const error = ref("");
const revision = ref("");
const preview = ref<RemovalPreview | null>(null);
const confirmName = ref("");
const acknowledged = ref(false);
const syncHosts = ref(true);
const form = reactive<ServerSettings>({ name: "", ip_address: null, ip_address_v6: null, domain: null, domain_v6: null, ipv6_enabled: true });
let version = 0;
const canRemove = computed(() => !!preview.value && !preview.value.blockers.length && confirmName.value === preview.value.server_name && acknowledged.value && !busy.value);

async function load() {
  const current = ++version;
  if (!props.open || !props.serverId) return;
  busy.value = true;
  error.value = "";
  revision.value = "";
  preview.value = null;
  confirmName.value = "";
  acknowledged.value = false;
  try {
    if (props.mode === "remove") {
      const value = await getServerRemoval(props.serverId);
      if (current === version) preview.value = value;
    } else {
      const value = await getServerSettings(props.serverId);
      if (current !== version) return;
      revision.value = value.revision;
      for (const key of Object.keys(form) as (keyof ServerSettings)[]) {
        Object.assign(form, { [key]: value.server[key] ?? (key === "ipv6_enabled" ? true : null) });
      }
      syncHosts.value = true;
    }
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Server request failed";
  } finally {
    if (current === version) busy.value = false;
  }
}

async function save() {
  if (busy.value || !revision.value || !form.name.trim()) return;
  const current = ++version;
  busy.value = true;
  error.value = "";
  try {
    await updateServerSettings(props.serverId, form, revision.value, syncHosts.value);
    emit("changed");
    if (current === version) emit("update:open", false);
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Server update failed";
  } finally {
    if (current === version) busy.value = false;
  }
}

async function remove() {
  if (!canRemove.value || !preview.value) return;
  const current = ++version;
  busy.value = true;
  error.value = "";
  try {
    await removeServer(props.serverId, preview.value, confirmName.value);
    emit("changed");
    if (current === version) emit("update:open", false);
  } catch (failure) {
    if (current === version) {
      error.value = failure instanceof Error ? failure.message : "Server removal failed";
      preview.value = null;
      acknowledged.value = false;
    }
  } finally {
    if (current === version) busy.value = false;
  }
}

watch(() => [props.open, props.serverId, props.mode], () => { busy.value = false; void load(); }, { immediate: true });
onBeforeUnmount(() => { ++version; });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="620" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="server-management-dialog">
      <v-card-title class="management-title">
        <span>{{ mode === 'edit' ? 'Edit server' : 'Remove server' }}</span>
        <v-tooltip text="Reload server details"><template #activator="{ props: tip }">
          <v-btn v-bind="tip" icon="mdi-refresh" aria-label="Reload server details" variant="text" size="small" :disabled="busy" @click="load" />
        </template></v-tooltip>
      </v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" indeterminate color="primary" class="mb-4" />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-form v-if="mode === 'edit' && revision" id="server-settings-form" class="management-form" @submit.prevent="save">
          <v-text-field v-model="form.name" label="Server name" maxlength="120" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="form.domain" label="Domain" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="form.ip_address" label="IPv4 address" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="form.domain_v6" label="IPv6 domain" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-text-field v-model="form.ip_address_v6" label="IPv6 address" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-switch v-model="form.ipv6_enabled" label="IPv6 enabled" color="primary" :disabled="busy" density="compact" hide-details />
          <v-checkbox v-model="syncHosts" label="Update matching node addresses" color="primary" :disabled="busy" density="compact" hide-details />
        </v-form>
        <template v-if="mode === 'remove' && preview">
          <h3>{{ preview.server_name }}</h3>
          <v-alert type="warning" variant="tonal" class="my-4">This removes control-plane records. The remote Agent, Xray and existing client access are not uninstalled or stopped.</v-alert>
          <dl class="removal-counts">
            <dt>Nodes removed</dt><dd>{{ preview.nodes.length }}</dd>
            <dt>Plans updated</dt><dd>{{ preview.plans.length }}</dd>
            <dt>Command records removed</dt><dd>{{ preview.command_count }}</dd>
            <dt>Unfinished commands</dt><dd>{{ preview.unfinished_command_count }}</dd>
            <dt>Telemetry records removed</dt><dd>{{ preview.telemetry_count }}</dd>
            <dt>User usage retained</dt><dd>{{ preview.user_count }}</dd>
            <dt>Change sets archived</dt><dd>{{ preview.change_sets.length }}</dd>
            <dt>Certificates retained</dt><dd>{{ preview.certificates.length }}</dd>
          </dl>
          <div v-for="[label, items] in [['Nodes', preview.nodes], ['Plans', preview.plans], ['Change sets', preview.change_sets], ['Certificates', preview.certificates]] as const" :key="label" class="removal-items">
            <strong v-if="items.length">{{ label }}</strong>
            <span v-for="item in items" :key="item.id">{{ item.name }}</span>
          </div>
          <v-alert v-if="preview.certificates.length" type="info" variant="tonal" class="my-4">Deployment targets on this server are removed. Certificates validated by this server stop automatic renewal until a new validation server is configured.</v-alert>
          <v-alert v-for="blocker in preview.blockers" :key="blocker" type="error" variant="tonal" class="my-4">{{ blocker }}</v-alert>
          <v-text-field v-model="confirmName" label="Confirm server name" :disabled="busy || !!preview.blockers.length" variant="outlined" density="compact" class="mt-4" hide-details />
          <v-checkbox v-model="acknowledged" label="I accept that remote services may keep running" :disabled="busy || !!preview.blockers.length" color="error" density="compact" hide-details />
        </template>
      </v-card-text>
      <v-card-actions class="management-actions">
        <v-btn :disabled="busy" @click="emit('update:open', false)">Cancel</v-btn>
        <v-spacer />
        <v-btn v-if="mode === 'edit'" color="primary" prepend-icon="mdi-content-save" :disabled="busy || !revision || !form.name?.trim()" @click="save">Save</v-btn>
        <v-btn v-else color="error" prepend-icon="mdi-delete-outline" :disabled="!canRemove" @click="remove">Remove</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.server-management-dialog, .server-management-dialog :deep(.v-card-text), .server-management-dialog :deep(.v-btn) { letter-spacing: 0; }
.management-title { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; font-size: 18px; gap: 12px; white-space: normal; }
.management-form { display: grid; gap: 16px; min-width: 0; }
.server-management-dialog h3 { font-size: 16px; overflow-wrap: anywhere; }
.removal-counts { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 16px; font-size: 13px; }
.removal-counts dd { text-align: right; }
.removal-items { display: grid; gap: 4px; margin-block: 12px; font-size: 12px; overflow-wrap: anywhere; }
.management-actions { display: flex; flex-wrap: wrap; }
.server-management-dialog :deep(.v-label) { white-space: normal; }
</style>
