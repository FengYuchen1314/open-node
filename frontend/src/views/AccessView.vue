<script setup lang="ts">
import { onMounted, ref } from "vue";
import AdministratorSecurityPanel from "../components/AdministratorSecurityPanel.vue";
import { authState, changePassword } from "../services/auth";
import { getAgentIdentity } from "../services/inventory";
import type { AgentIdentityInfo } from "../domain/inventory";

const currentPassword = ref("");
const newPassword = ref("");
const confirmation = ref("");
const busy = ref(false);
const error = ref("");
const identity = ref<AgentIdentityInfo | null>(null);
const identityBusy = ref(false);
const identityError = ref("");
const copied = ref(false);

async function loadIdentity() {
  if (identityBusy.value) return;
  identityBusy.value = true;
  identityError.value = "";
  copied.value = false;
  try {
    identity.value = await getAgentIdentity();
  } catch (cause) {
    identityError.value = cause instanceof Error ? cause.message : "Agent identity unavailable";
  } finally {
    identityBusy.value = false;
  }
}

async function copyPublicKey() {
  if (!identity.value?.public_key) return;
  try {
    await navigator.clipboard.writeText(identity.value.public_key);
    copied.value = true;
  } catch {
    identityError.value = "Could not copy public key";
  }
}

onMounted(loadIdentity);

async function submit() {
  if (busy.value) return;
  error.value = "";
  if (newPassword.value !== confirmation.value) {
    error.value = "Passwords do not match";
    return;
  }
  busy.value = true;
  try {
    await changePassword(currentPassword.value, newPassword.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Password change failed";
  } finally {
    currentPassword.value = "";
    newPassword.value = "";
    confirmation.value = "";
    busy.value = false;
  }
}
</script>

<template>
  <section class="page-shell access-page">
    <header class="page-heading">
      <div><h1 class="page-title">Access</h1><p>{{ authState.session?.username }}</p></div>
    </header>
    <form class="auth-form password-form" @submit.prevent="submit">
      <h2 class="section-title">Change Password</h2>
      <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
      <input :value="authState.session?.username" type="text" autocomplete="username" hidden />
      <v-text-field v-model="currentPassword" label="Current password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
      <v-text-field v-model="newPassword" label="New password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="busy" />
      <v-text-field v-model="confirmation" label="Confirm new password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="busy" />
      <v-btn type="submit" color="primary" prepend-icon="mdi-lock-reset" :loading="busy" :disabled="!currentPassword || newPassword.length < 12 || !confirmation">Change Password</v-btn>
    </form>
    <AdministratorSecurityPanel />
    <section class="agent-identity">
      <header class="identity-heading">
        <h2 class="section-title">Legacy Agent identity</h2>
        <v-tooltip text="Refresh identity">
          <template #activator="{ props }">
            <v-btn v-bind="props" aria-label="Refresh identity" icon="mdi-refresh" variant="text"
              :loading="identityBusy" @click="loadIdentity" />
          </template>
        </v-tooltip>
      </header>
      <v-alert v-if="identityError" type="error" variant="tonal">{{ identityError }}</v-alert>
      <template v-if="identity">
        <v-chip :color="identity.enabled ? 'success' : 'grey'" size="small" variant="tonal">
          {{ identity.enabled ? identity.protocol : "Not configured" }}
        </v-chip>
        <template v-if="identity.enabled">
          <div class="detail-label">Master public key</div>
          <div class="identity-key-row">
            <code class="identity-public-key">{{ identity.public_key }}</code>
            <v-tooltip :text="copied ? 'Copied' : 'Copy public key'">
              <template #activator="{ props }">
                <v-btn v-bind="props" aria-label="Copy public key" :icon="copied ? 'mdi-check' : 'mdi-content-copy'"
                  variant="text" @click="copyPublicKey" />
              </template>
            </v-tooltip>
          </div>
          <div class="detail-label">SHA-256 fingerprint</div>
          <code class="identity-fingerprint">{{ identity.fingerprint }}</code>
        </template>
      </template>
    </section>
  </section>
</template>

<style scoped>
.access-page { grid-template-columns: minmax(0, 1fr); }
.access-page :deep(.v-btn) { letter-spacing: 0; }
.agent-identity { border-top: 1px solid #dfe5e2; max-width: 600px; min-width: 0; padding-top: 16px; }
.identity-heading, .identity-key-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px; }
.agent-identity .detail-label { margin-top: 16px; }
.identity-public-key, .identity-fingerprint { font-size: 13px; overflow-wrap: anywhere; }
.identity-fingerprint { display: block; margin-top: 6px; }
</style>
