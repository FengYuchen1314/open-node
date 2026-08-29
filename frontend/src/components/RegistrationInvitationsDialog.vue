<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type {
  RegistrationInvitation,
  RegistrationInvitationCreateResponse,
  RegistrationInvitationStatus,
} from "../domain/registration-invitations";
import type { SubscriptionPlan } from "../domain/subscriptions";
import {
  createRegistrationInvitation,
  listRegistrationInvitations,
  revokeRegistrationInvitation,
} from "../services/registration-invitations";

const props = defineProps<{ open: boolean; plans: SubscriptionPlan[] }>();
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const invitations = ref<RegistrationInvitation[]>([]);
const form = reactive({ plan_id: "", expires_minutes: 1440 });
const loading = ref(false);
const saving = ref(false);
const revoking = ref("");
const error = ref("");
const created = ref<RegistrationInvitationCreateResponse | null>(null);
const copied = ref(false);
const planOptions = computed(() => props.plans.map((plan) => ({ title: plan.name, value: plan.id })));
const expiryOptions = [
  { title: "1 hour", value: 60 },
  { title: "24 hours", value: 1440 },
  { title: "3 days", value: 4320 },
  { title: "7 days", value: 10080 },
];

watch(() => props.open, async (open) => {
  if (!open) return;
  form.plan_id = props.plans.some((plan) => plan.id === form.plan_id)
    ? form.plan_id
    : props.plans[0]?.id ?? "";
  created.value = null;
  copied.value = false;
  await load();
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    invitations.value = (await listRegistrationInvitations()).invitations;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Invitations unavailable";
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!form.plan_id || saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    created.value = await createRegistrationInvitation({
      plan_id: form.plan_id,
      expires_minutes: form.expires_minutes,
    });
    invitations.value = [
      created.value.invitation,
      ...invitations.value.filter((item) => item.id !== created.value?.invitation.id),
    ];
    copied.value = false;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Invitation creation failed";
  } finally {
    saving.value = false;
  }
}

async function copyLink() {
  if (!created.value) return;
  try {
    await navigator.clipboard.writeText(created.value.registration_url);
    copied.value = true;
  } catch {
    error.value = "Clipboard access failed";
  }
}

async function revoke(item: RegistrationInvitation) {
  revoking.value = item.id;
  error.value = "";
  try {
    const updated = await revokeRegistrationInvitation(item.id);
    invitations.value = invitations.value.map((entry) => entry.id === updated.id ? updated : entry);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Invitation revocation failed";
  } finally {
    revoking.value = "";
  }
}

function statusColor(status: RegistrationInvitationStatus) {
  return status === "active" ? "success" : status === "used" ? "primary" : status === "expired" ? "warning" : "error";
}

function dateTime(value: string) {
  return new Date(value).toLocaleString();
}
</script>

<template>
  <v-dialog :model-value="open" :persistent="saving || Boolean(revoking)" max-width="720" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="invitation-dialog">
      <v-card-title>Registration invitations</v-card-title>
      <v-card-text class="invitation-content">
        <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
        <div class="invitation-form">
          <v-select v-model="form.plan_id" :items="planOptions" label="Plan" variant="outlined" density="compact" hide-details :disabled="saving || !planOptions.length" />
          <v-select v-model="form.expires_minutes" :items="expiryOptions" label="Expires" variant="outlined" density="compact" hide-details :disabled="saving" />
          <v-tooltip text="Create registration invitation"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-account-plus-outline" aria-label="Create registration invitation" color="primary" variant="tonal" :loading="saving" :disabled="!form.plan_id" @click="submit" /></template></v-tooltip>
        </div>
        <v-text-field v-if="created" :model-value="created.registration_url" label="Registration URL" readonly variant="outlined" density="compact" hide-details>
          <template #append-inner><v-btn :icon="copied ? 'mdi-check' : 'mdi-content-copy'" :aria-label="copied ? 'Copied' : 'Copy registration URL'" variant="text" size="small" @click="copyLink" /></template>
        </v-text-field>
        <v-divider />
        <v-progress-linear v-if="loading" indeterminate color="primary" />
        <div v-if="!loading && invitations.length === 0" class="invitation-empty">No invitations.</div>
        <div v-for="item in invitations" :key="item.id" class="invitation-row">
          <div class="invitation-meta">
            <strong>{{ item.plan_name }}</strong>
            <span>{{ item.used_by || `Token ${item.token_hint}...` }} · {{ dateTime(item.expires_at) }}</span>
          </div>
          <div class="invitation-actions">
            <v-tooltip v-if="item.status === 'active'" text="Revoke invitation"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-link-off" :aria-label="`Revoke invitation for ${item.plan_name}`" variant="text" size="small" :loading="revoking === item.id" @click="revoke(item)" /></template></v-tooltip>
            <v-chip :color="statusColor(item.status)" size="small" variant="tonal">{{ item.status }}</v-chip>
          </div>
        </div>
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn :disabled="saving || Boolean(revoking)" @click="emit('update:open', false)">Close</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.invitation-dialog, .invitation-dialog :deep(*) { letter-spacing: 0; }
.invitation-content { display: grid; gap: 16px; min-width: 0; }
.invitation-form { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) 40px; align-items: center; gap: 12px; }
.invitation-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-width: 0; padding-block: 12px; border-bottom: 1px solid #e7ece9; }
.invitation-meta { display: grid; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
.invitation-meta span, .invitation-empty { color: #66736f; font-size: 12px; }
.invitation-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.invitation-dialog :deep(.v-card-title), .invitation-dialog :deep(.v-alert__content) { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 560px) {
  .invitation-form { grid-template-columns: minmax(0, 1fr) 40px; }
  .invitation-form :deep(.v-select:first-child) { grid-column: 1 / -1; }
  .invitation-row { align-items: flex-start; }
}
</style>
