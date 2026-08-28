<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { subscriberAccount, type SubscriberAccount } from "../services/subscriber-auth";

const props = defineProps<{ username: string; open: boolean }>();
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const account = ref<SubscriberAccount | null>(null);
const password = ref("");
const confirm = ref("");
const resetTotp = ref(false);
const acknowledged = ref(false);
const busy = ref(false);
const error = ref("");
const saved = ref(false);
let version = 0;
const valid = computed(() => !!account.value && password.value.length >= 12 && password.value === confirm.value && acknowledged.value && !busy.value);
async function load() {
  const current = ++version;
  account.value = null; password.value = ""; confirm.value = ""; resetTotp.value = false;
  acknowledged.value = false; saved.value = false; error.value = ""; busy.value = false;
  if (!props.open) return;
  busy.value = true;
  try {
    const result = await subscriberAccount(props.username);
    if (current === version) account.value = result;
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Login settings unavailable";
  } finally { if (current === version) busy.value = false; }
}
async function submit() {
  if (!valid.value || !account.value) return;
  const current = ++version;
  busy.value = true; error.value = "";
  try {
    const result = await subscriberAccount(props.username, { expected_revision: account.value.revision, new_password: password.value, reset_totp: resetTotp.value });
    if (current !== version) return;
    account.value = result; saved.value = true; acknowledged.value = false; resetTotp.value = false;
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Password reset failed";
  } finally { if (current === version) { busy.value = false; password.value = ""; confirm.value = ""; } }
}
watch(() => [props.open, props.username], load, { immediate: true });
onBeforeUnmount(() => { ++version; password.value = ""; confirm.value = ""; });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="520" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="user-login-dialog">
      <v-card-title class="login-heading"><span>User login</span><v-tooltip text="Reload login settings"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-refresh" aria-label="Reload login settings" variant="text" size="small" :disabled="busy" @click="load" /></template></v-tooltip></v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" indeterminate color="primary" />
        <strong class="login-name">{{ username }}</strong>
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-alert v-if="saved" type="success" variant="tonal" class="mb-4">Login password saved. Existing sessions have been revoked.</v-alert>
        <form v-if="account" id="subscriber-admin-password" class="login-form" @submit.prevent="submit">
          <div class="login-status"><span>{{ account.configured ? 'Password configured' : 'Login not configured' }}</span><span>Two-factor: {{ account.totp_enabled ? 'On' : 'Off' }}</span></div>
          <v-text-field v-model="password" type="password" label="New login password" autocomplete="new-password" :disabled="busy" minlength="12" maxlength="1024" required />
          <v-text-field v-model="confirm" type="password" label="Confirm login password" autocomplete="new-password" :disabled="busy" maxlength="1024" required :error-messages="confirm && confirm !== password ? 'Passwords do not match' : ''" />
          <v-checkbox v-if="account.totp_enabled" v-model="resetTotp" label="Reset two-factor authentication and recovery codes" :disabled="busy" hide-details />
          <v-checkbox v-model="acknowledged" label="Revoke all existing user sessions" :disabled="busy" hide-details />
        </form>
      </v-card-text>
      <v-card-actions><v-btn :disabled="busy" @click="emit('update:open', false)">Close</v-btn><v-spacer /><v-btn form="subscriber-admin-password" type="submit" color="primary" prepend-icon="mdi-key-outline" :disabled="!valid" :loading="busy">Save password</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.user-login-dialog, .user-login-dialog :deep(*) { letter-spacing: 0; }
.login-heading { display: flex; align-items: center; justify-content: space-between; font-size: 18px; }
.login-name { display: block; margin-block: 8px 20px; overflow-wrap: anywhere; }
.login-form { display: grid; gap: 8px; }
.login-status { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; font-size: 13px; margin-bottom: 12px; }
.user-login-dialog :deep(.v-label) { white-space: normal; }
.user-login-dialog :deep(.v-card-actions) { flex-wrap: wrap; }
</style>
