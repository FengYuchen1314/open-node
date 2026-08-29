<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { ManagedNode, ProductUser } from "../domain/subscriptions";
import type { SubscriptionProfile } from "../domain/subscription-profiles";
import type { SubscriptionTemplate } from "../domain/subscription-templates";
import { updateSubscriptionProfile } from "../services/subscription-profiles";

const props = defineProps<{
  open: boolean;
  profile: SubscriptionProfile | null;
  nodes: ManagedNode[];
  users: ProductUser[];
  templates: SubscriptionTemplate[];
}>();
const emit = defineEmits<{ "update:open": [value: boolean]; saved: [value: SubscriptionProfile] }>();
const form = reactive({
  name: "", description: "", node_ids: [] as string[], assigned_usernames: [] as string[],
  clash_template_id: null as string | null, surge_template_id: null as string | null, enabled: false,
});
const busy = ref(false);
const error = ref("");
const nodeOptions = computed(() => props.nodes.filter(item => !item.removal_id).map(item => ({ title: item.name, value: item.id })));
const userOptions = computed(() => props.users.filter(item => !item.removal_id).map(item => ({ title: item.display_name || item.username, value: item.username })));
const clashTemplates = computed(() => props.templates.filter(item => item.format === "clash"));
const surgeTemplates = computed(() => props.templates.filter(item => item.format === "surge"));

watch(() => [props.open, props.profile] as const, ([open, profile]) => {
  error.value = "";
  if (!open || !profile) return;
  Object.assign(form, {
    name: profile.name,
    description: profile.description,
    node_ids: [...profile.node_ids],
    assigned_usernames: [...profile.assigned_usernames],
    clash_template_id: profile.clash_template_id,
    surge_template_id: profile.surge_template_id,
    enabled: profile.enabled,
  });
}, { immediate: true });

async function save() {
  if (!props.profile || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const value = await updateSubscriptionProfile(props.profile.id, {
      ...form,
      expected_revision: props.profile.revision,
    });
    emit("saved", value);
    emit("update:open", false);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Subscription profile update failed";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="720" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="profile-dialog">
      <v-card-title>Edit subscription profile</v-card-title>
      <v-card-text class="profile-form">
        <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
        <v-alert v-if="profile?.migration_warnings.length" type="warning" variant="tonal">{{ profile.migration_warnings.join("; ") }}</v-alert>
        <div class="field-row">
          <v-text-field v-model="form.name" label="Name" variant="outlined" density="compact" />
          <v-switch v-model="form.enabled" label="Enabled" color="primary" hide-details />
        </div>
        <v-textarea v-model="form.description" label="Description" variant="outlined" rows="2" auto-grow />
        <v-autocomplete v-model="form.assigned_usernames" :items="userOptions" label="Assigned subscribers" multiple chips closable-chips variant="outlined" />
        <v-autocomplete v-model="form.node_ids" :items="nodeOptions" label="Node subset" hint="Empty uses every node in each subscriber's plan" persistent-hint multiple chips closable-chips variant="outlined" />
        <div class="field-row">
          <v-select v-model="form.clash_template_id" :items="clashTemplates" item-title="name" item-value="id" label="Clash template" clearable variant="outlined" />
          <v-select v-model="form.surge_template_id" :items="surgeTemplates" item-title="name" item-value="id" label="Surge template" clearable variant="outlined" />
        </div>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="emit('update:open', false)">Cancel</v-btn>
        <v-spacer />
        <v-btn color="primary" prepend-icon="mdi-content-save" :loading="busy" :disabled="!form.name.trim()" @click="save">Save</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.profile-dialog, .profile-dialog :deep(*) { letter-spacing: 0; }
.profile-form { display: grid; gap: 14px; min-width: 0; }
.field-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: center; }
.profile-dialog :deep(.v-card-title), .profile-dialog :deep(.v-alert__content) { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 560px) { .field-row { grid-template-columns: minmax(0, 1fr); } }
</style>
