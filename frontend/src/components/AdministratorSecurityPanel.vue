<script setup lang="ts">
import QRCode from "qrcode";
import { computed, onMounted, ref } from "vue";
import {
  administratorSecurity,
  beginAdministratorTotp,
  confirmAdministratorTotp,
  disableAdministratorTotp,
  regenerateAdministratorRecoveryCodes,
  updateAdministratorTotpPolicy,
  type AdministratorSecurity,
  type AdministratorTotpEnrollment,
} from "../services/auth";

type Mode = "enroll" | "disable" | "recovery" | "policy";

const security = ref<AdministratorSecurity | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const mode = ref<Mode | null>(null);
const password = ref("");
const code = ref("");
const enrollment = ref<AdministratorTotpEnrollment | null>(null);
const qr = ref("");
const recoveryCodes = ref<string[]>([]);
const accepted = ref(false);
const requiredTarget = ref(false);
const copied = ref(false);

const title = computed(() => ({
  enroll: "Enable administrator two-factor authentication",
  disable: "Disable administrator two-factor authentication",
  recovery: "Generate new administrator recovery codes",
  policy: requiredTarget.value ? "Require administrator 2FA" : "Make administrator 2FA optional",
})[mode.value ?? "enroll"]);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    security.value = await administratorSecurity();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Administrator security settings unavailable";
  } finally {
    loading.value = false;
  }
}

function open(selected: Mode, required = false) {
  mode.value = selected;
  requiredTarget.value = required;
  password.value = "";
  code.value = "";
  enrollment.value = null;
  qr.value = "";
  recoveryCodes.value = [];
  accepted.value = false;
  copied.value = false;
  error.value = "";
}

function close() {
  if (recoveryCodes.value.length && !accepted.value) return;
  mode.value = null;
  password.value = "";
  code.value = "";
  enrollment.value = null;
  qr.value = "";
  recoveryCodes.value = [];
}

async function submit() {
  if (!mode.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (mode.value === "enroll" && !enrollment.value) {
      enrollment.value = await beginAdministratorTotp(password.value);
      qr.value = await QRCode.toDataURL(enrollment.value.provisioning_uri, { width: 240, margin: 1 });
      password.value = "";
      return;
    }
    if (mode.value === "enroll") {
      recoveryCodes.value = await confirmAdministratorTotp(code.value);
    } else if (mode.value === "disable") {
      await disableAdministratorTotp(password.value, code.value);
    } else if (mode.value === "recovery") {
      recoveryCodes.value = await regenerateAdministratorRecoveryCodes(password.value, code.value);
    } else {
      security.value = await updateAdministratorTotpPolicy(
        requiredTarget.value, password.value, code.value,
      );
    }
    await load();
    if (!recoveryCodes.value.length) mode.value = null;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Security update failed";
  } finally {
    code.value = "";
    if (mode.value !== "enroll" || !enrollment.value) password.value = "";
    busy.value = false;
  }
}

async function copyCodes() {
  try {
    await navigator.clipboard.writeText(recoveryCodes.value.join("\n"));
    copied.value = true;
  } catch {
    error.value = "Could not copy recovery codes";
  }
}

onMounted(load);
</script>

<template>
  <section class="administrator-security" aria-label="Administrator security">
    <header class="security-heading">
      <div>
        <h2 class="section-title">Administrator security</h2>
        <p>Protect control-plane access with an authenticator and one-time recovery codes.</p>
      </div>
      <v-btn icon="mdi-refresh" aria-label="Refresh administrator security" variant="text" :loading="loading" @click="load" />
    </header>
    <v-alert v-if="error && !mode" type="error" variant="tonal">{{ error }}</v-alert>
    <template v-if="security">
      <div class="security-row">
        <div>
          <h3>Two-factor authentication</h3>
          <p>{{ security.totp_enabled ? "Enabled" : "Not enabled" }}</p>
          <p v-if="security.totp_enabled">{{ security.recovery_codes_remaining }} recovery codes remaining</p>
          <p v-else-if="!security.totp_available">Enrollment unavailable because the TOTP encryption key is not configured.</p>
        </div>
        <div class="security-actions">
          <template v-if="security.totp_enabled">
            <v-btn variant="text" prepend-icon="mdi-key-change" @click="open('recovery')">New recovery codes</v-btn>
            <v-btn variant="text" color="error" prepend-icon="mdi-shield-off-outline" :disabled="security.require_totp" @click="open('disable')">Disable</v-btn>
          </template>
          <v-btn v-else color="primary" variant="tonal" prepend-icon="mdi-shield-plus-outline" :disabled="!security.totp_available" @click="open('enroll')">Enable</v-btn>
        </div>
      </div>
      <div class="security-row">
        <div>
          <h3>Mandatory administrator 2FA</h3>
          <p>{{ security.require_totp ? "Every password login must complete a second-factor challenge." : "Administrators may sign in with a password when 2FA is not enrolled." }}</p>
        </div>
        <v-btn variant="text" :color="security.require_totp ? 'warning' : 'primary'" :disabled="!security.totp_enabled" @click="open('policy', !security.require_totp)">
          {{ security.require_totp ? "Make optional" : "Require 2FA" }}
        </v-btn>
      </div>
    </template>

    <v-dialog :model-value="!!mode" max-width="620" persistent>
      <v-card>
        <v-card-title class="security-title">{{ title }}</v-card-title>
        <v-card-text class="security-dialog">
          <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
          <template v-if="recoveryCodes.length">
            <v-alert type="success" variant="tonal">Store these codes now. Existing recovery codes no longer work.</v-alert>
            <div class="recovery-heading">
              <strong>One-time recovery codes</strong>
              <v-btn variant="text" :prepend-icon="copied ? 'mdi-check' : 'mdi-content-copy'" @click="copyCodes">{{ copied ? "Copied" : "Copy" }}</v-btn>
            </div>
            <div class="recovery-grid"><code v-for="item in recoveryCodes" :key="item">{{ item }}</code></div>
            <v-checkbox v-model="accepted" label="I have stored the recovery codes securely" hide-details />
          </template>
          <template v-else>
            <template v-if="enrollment">
              <v-alert type="info" variant="tonal">Scan the QR code, then enter the current six-digit code.</v-alert>
              <img v-if="qr" :src="qr" alt="Administrator authenticator enrollment QR code" width="240" height="240" class="totp-qr" />
              <v-text-field :model-value="enrollment.secret" label="Authenticator secret" readonly />
            </template>
            <v-alert v-if="mode === 'disable'" type="warning" variant="tonal">Disabling 2FA removes all recovery codes. Other administrator sessions will be revoked.</v-alert>
            <v-alert v-if="mode === 'recovery'" type="warning" variant="tonal">Generating new codes invalidates every existing recovery code and revokes other sessions.</v-alert>
            <v-alert v-if="mode === 'policy'" type="info" variant="tonal">Confirm this policy change with the administrator password and a current authenticator or recovery code.</v-alert>
            <v-text-field v-if="!enrollment" v-model="password" label="Current password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
            <v-text-field v-if="enrollment || mode !== 'enroll'" v-model="code" :label="enrollment ? 'Authenticator code' : 'Authenticator or recovery code'" autocomplete="one-time-code" :inputmode="enrollment ? 'numeric' : 'text'" required maxlength="64" :disabled="busy" />
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="busy || (recoveryCodes.length > 0 && !accepted)" @click="close">{{ recoveryCodes.length ? "Done" : "Cancel" }}</v-btn>
          <v-btn v-if="!recoveryCodes.length" color="primary" :loading="busy" :disabled="(!enrollment && !password) || ((enrollment || mode !== 'enroll') && !code)" @click="submit">
            {{ mode === "enroll" && !enrollment ? "Start enrollment" : "Confirm" }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>

<style scoped>
.administrator-security { border-top: 1px solid #dfe5e2; display: grid; gap: 14px; max-width: 760px; min-width: 0; padding-top: 16px; }
.security-heading, .security-row, .recovery-heading { align-items: center; display: flex; justify-content: space-between; gap: 16px; }
.security-heading p, .security-row p { color: #607069; margin: 4px 0 0; }
.security-row { border: 1px solid #dfe5e2; border-radius: 12px; padding: 14px; }
.security-row h3 { font-size: 15px; margin: 0; }
.security-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; }
.security-dialog { display: grid; gap: 14px; }
.security-title { white-space: normal; overflow-wrap: anywhere; }
.totp-qr { justify-self: center; max-width: 100%; height: auto; aspect-ratio: 1; }
.recovery-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
.recovery-grid code { overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .security-heading, .security-row { align-items: stretch; flex-direction: column; }
  .security-actions { justify-content: flex-start; }
  .recovery-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
