<script setup lang="ts">
import QRCode from "qrcode";
import { ref } from "vue";
import {
  acceptOperatorSession,
  authState,
  loadSession,
  signIn,
  verifySignIn,
  type AdministratorTotpEnrollment,
  type OperatorLogin,
} from "../services/auth";

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const challenge = ref("");
const code = ref("");
const enrollment = ref<AdministratorTotpEnrollment | null>(null);
const qr = ref("");
const recoveryCodes = ref<string[]>([]);
const accepted = ref(false);
const stagedSession = ref<OperatorLogin | null>(null);

async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await signIn(username.value, password.value);
    if (result.requires_2fa && result.challenge) {
      challenge.value = result.challenge;
      enrollment.value = result.enrollment;
      qr.value = result.enrollment
        ? await QRCode.toDataURL(result.enrollment.provisioning_uri, { width: 240, margin: 1 })
        : "";
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Sign-in failed";
  } finally {
    password.value = "";
    busy.value = false;
  }
}

async function verify() {
  if (busy.value || !challenge.value || !code.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await verifySignIn(challenge.value, code.value);
    if (result.recovery_codes.length) {
      stagedSession.value = result;
      recoveryCodes.value = result.recovery_codes;
      challenge.value = "";
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Verification failed";
  } finally {
    code.value = "";
    busy.value = false;
  }
}

function continueLogin() {
  if (!accepted.value || !stagedSession.value) return;
  acceptOperatorSession(stagedSession.value);
}

function restart() {
  challenge.value = "";
  code.value = "";
  enrollment.value = null;
  qr.value = "";
  recoveryCodes.value = [];
  stagedSession.value = null;
  accepted.value = false;
  error.value = "";
}
</script>

<template>
  <section class="auth-page">
    <div class="auth-workspace">
      <div class="brand-mark" aria-hidden="true">ON</div>
      <h1>Open Node</h1>
      <h2>Administrator Sign-In</h2>
      <v-alert v-if="authState.error" type="error" variant="tonal">
        {{ authState.error }}
        <v-btn icon="mdi-refresh" title="Retry connection" aria-label="Retry connection" @click="loadSession()" />
      </v-alert>
      <v-alert v-else-if="authState.session?.configured === false" type="warning" variant="tonal">
        Administrator account is not configured.
      </v-alert>
      <template v-else>
      <section v-if="recoveryCodes.length" class="auth-form recovery-panel">
        <v-alert type="success" variant="tonal">Two-factor authentication is enabled. Store these one-time recovery codes before continuing.</v-alert>
        <div class="recovery-grid" aria-label="Administrator recovery codes">
          <code v-for="item in recoveryCodes" :key="item">{{ item }}</code>
        </div>
        <v-checkbox v-model="accepted" label="I have stored the recovery codes securely" hide-details />
        <v-btn color="primary" :disabled="!accepted" @click="continueLogin">Continue to Open Node</v-btn>
      </section>
      <form v-else-if="challenge" class="auth-form" @submit.prevent="verify">
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
        <template v-if="enrollment">
          <v-alert type="info" variant="tonal">Administrator 2FA is required. Scan this code before completing sign-in.</v-alert>
          <img v-if="qr" :src="qr" alt="Administrator authenticator enrollment QR code" width="240" height="240" class="totp-qr" />
          <v-text-field :model-value="enrollment.secret" label="Authenticator secret" readonly />
        </template>
        <v-text-field v-model="code" :label="enrollment ? 'Authenticator code' : 'Authenticator or recovery code'" autocomplete="one-time-code" :inputmode="enrollment ? 'numeric' : 'text'" required maxlength="64" autofocus :disabled="busy" />
        <div class="challenge-actions">
          <v-btn variant="text" :disabled="busy" @click="restart">Start over</v-btn>
          <v-btn type="submit" color="primary" prepend-icon="mdi-shield-check" :loading="busy" :disabled="!code">Verify</v-btn>
        </div>
      </form>
      <form v-else class="auth-form" @submit.prevent="submit">
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
        <v-text-field v-model="username" label="Username" autocomplete="username" autofocus required maxlength="64" :disabled="busy" />
        <v-text-field v-model="password" label="Password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
        <v-btn type="submit" color="primary" prepend-icon="mdi-login" :loading="busy" :disabled="!username || !password">Sign In</v-btn>
      </form>
      </template>
      <router-link to="/account" class="subscriber-link">Subscriber sign-in</router-link>
    </div>
  </section>
</template>

<style scoped>
.subscriber-link { font-size: 13px; color: #176b5b; justify-self: start; }
.totp-qr { justify-self: center; max-width: 100%; height: auto; aspect-ratio: 1; }
.challenge-actions { display: flex; justify-content: space-between; gap: 12px; }
.recovery-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
.recovery-grid code { overflow-wrap: anywhere; }
@media (max-width: 520px) { .recovery-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
