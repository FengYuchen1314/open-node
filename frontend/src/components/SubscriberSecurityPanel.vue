<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import QRCode from "qrcode";
import {
  beginSubscriberTotp, clearSubscriberSession, confirmSubscriberTotp, revokeSubscriberDevice,
  subscriberChangePassword, subscriberDevices, subscriberSecurity, subscriberToken, updateSubscriberTotp,
  type SubscriberDevice, type SubscriberEnrollment, type SubscriberSecurity,
} from "../services/subscriber-auth";

const emit = defineEmits<{ changed: [] }>();
const security = ref<SubscriberSecurity | null>(null);
const devices = ref<SubscriberDevice[]>([]);
const loading = ref(false);
const error = ref("");
const notice = ref("");
type Mode = "password" | "enroll" | "disable" | "recovery" | "link";
const mode = ref<Mode>("password");
const titles: Record<Mode, string> = { password: "Change password", enroll: "Two-factor authentication", disable: "Disable two-factor authentication", recovery: "New recovery codes", link: "Reset subscription links" };
const dialog = ref(false);
const busy = ref(false);
const dialogError = ref("");
const password = ref("");
const newPassword = ref("");
const confirmation = ref("");
const code = ref("");
const enrollment = ref<SubscriberEnrollment | null>(null);
const qr = ref("");
const recovery = ref<string[]>([]);
const accepted = ref(false);
let version = 0;
let operationVersion = 0;
const needsCode = computed(() => !!enrollment.value || (security.value?.totp_enabled && mode.value !== "enroll"));
const canSubmit = computed(() => !busy.value && !recovery.value.length && (enrollment.value ? !!code.value.trim() : !!password.value)
  && (!needsCode.value || !!code.value.trim()) && (mode.value !== "password" || (newPassword.value.length >= 12 && newPassword.value === confirmation.value)));

async function load() {
  const current = ++version;
  loading.value = true; error.value = "";
  try {
    const [settings, sessions] = await Promise.all([subscriberSecurity(), subscriberDevices()]);
    if (current === version) { security.value = settings; devices.value = sessions; }
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Security settings unavailable";
  } finally { if (current === version) loading.value = false; }
}
function clearSecrets() {
  password.value = ""; newPassword.value = ""; confirmation.value = ""; code.value = "";
  enrollment.value = null; qr.value = ""; recovery.value = []; accepted.value = false;
}
function open(action: Mode) {
  ++operationVersion; clearSecrets(); mode.value = action; dialogError.value = ""; dialog.value = true;
}
function close() {
  if (busy.value || (recovery.value.length && !accepted.value)) return;
  ++operationVersion; dialog.value = false; clearSecrets();
}
async function submit() {
  if (!canSubmit.value) return;
  const current = ++operationVersion;
  busy.value = true; dialogError.value = ""; notice.value = "";
  try {
    const proof = { password: password.value, code: code.value };
    if (mode.value === "enroll") {
      if (!enrollment.value) {
        const result = await beginSubscriberTotp(password.value);
        const image = await QRCode.toDataURL(result.provisioning_uri, { width: 240, margin: 2 });
        if (current !== operationVersion) return;
        enrollment.value = result; qr.value = image; password.value = "";
        return;
      }
      const result = await confirmSubscriberTotp(code.value);
      if (current !== operationVersion) return;
      recovery.value = result.recovery_codes; enrollment.value = null; qr.value = "";
    } else if (mode.value === "password") {
      await subscriberChangePassword(proof, newPassword.value);
    } else if (mode.value === "link") {
      await subscriberToken(proof); emit("changed"); notice.value = "Subscription links reset";
    } else {
      const result = await updateSubscriberTotp(proof, mode.value === "disable");
      if (current !== operationVersion) return;
      recovery.value = result?.recovery_codes ?? [];
      notice.value = mode.value === "disable" ? "Two-factor authentication disabled" : "Recovery codes replaced";
    }
    if (current !== operationVersion) return;
    password.value = ""; code.value = ""; newPassword.value = ""; confirmation.value = "";
    if (!recovery.value.length) dialog.value = false;
    if (mode.value !== "password") await load();
  } catch (failure) {
    if (current === operationVersion) dialogError.value = failure instanceof Error ? failure.message : "Security update failed";
  } finally { if (current === operationVersion) busy.value = false; }
}
async function revoke(device?: SubscriberDevice) {
  if (loading.value) return;
  loading.value = true; error.value = "";
  try {
    await revokeSubscriberDevice(device?.id);
    if (device?.current) clearSubscriberSession();
    else await load();
  } catch (failure) { error.value = failure instanceof Error ? failure.message : "Session revocation failed"; }
  finally { loading.value = false; }
}
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); }
  catch { dialogError.value = "Clipboard unavailable"; }
}
function saveRecovery() {
  const url = URL.createObjectURL(new Blob([recovery.value.join("\n") + "\n"], { type: "text/plain" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "open-node-recovery-codes.txt"; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const date = (value: string) => new Date(value).toLocaleString();
onMounted(load);
onBeforeUnmount(() => { ++version; ++operationVersion; clearSecrets(); });
</script>

<template>
  <div class="subscriber-security">
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
    <v-alert v-if="notice" type="success" variant="tonal" closable>{{ notice }}</v-alert>
    <div class="security-heading"><h2>Account security</h2><v-tooltip text="Refresh security settings"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-refresh" aria-label="Refresh security settings" variant="text" :disabled="loading" @click="load" /></template></v-tooltip></div>
    <template v-if="security">
      <section class="security-setting"><div><h3>Password</h3></div><v-btn variant="text" prepend-icon="mdi-key-outline" @click="open('password')">Change password</v-btn></section>
      <section class="security-setting"><div><h3>Two-factor authentication</h3><p>{{ security.totp_enabled ? 'Enabled' : 'Not enabled' }}</p><p v-if="security.totp_enabled">{{ security.recovery_codes_remaining }} recovery codes remaining</p><p v-else-if="!security.totp_available">Enrollment unavailable</p></div><div class="security-actions"><template v-if="security.totp_enabled"><v-btn variant="text" prepend-icon="mdi-key-change" @click="open('recovery')">New recovery codes</v-btn><v-btn variant="text" color="error" prepend-icon="mdi-shield-off-outline" @click="open('disable')">Disable</v-btn></template><v-btn v-else variant="text" color="primary" prepend-icon="mdi-shield-plus-outline" :disabled="!security.totp_available" @click="open('enroll')">Enable</v-btn></div></section>
      <section class="security-setting"><h3>Subscription links</h3><v-btn variant="text" prepend-icon="mdi-link-variant-remove" @click="open('link')">Reset links</v-btn></section>
    </template>
    <section class="security-devices" aria-label="Active sessions">
      <div class="security-heading"><h2>Active sessions</h2><v-btn variant="text" prepend-icon="mdi-logout-variant" :disabled="loading || devices.length < 2" @click="revoke()">Revoke others</v-btn></div>
      <div v-for="device in devices" :key="device.id" class="security-device">
        <v-icon icon="mdi-monitor-cellphone" aria-hidden="true" />
        <div class="device-details"><strong>{{ device.peer }}</strong><v-chip v-if="device.current" size="x-small" color="primary" variant="tonal" class="ml-2">Current</v-chip><p>{{ device.user_agent || 'Unknown device' }}</p><dl><dt>Signed in</dt><dd>{{ date(device.created_at) }}</dd><dt>Last active</dt><dd>{{ date(device.last_seen_at) }}</dd></dl></div>
        <v-tooltip :text="device.current ? 'Sign out this device' : 'Revoke session'"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-logout" :aria-label="device.current ? 'Sign out this device' : `Revoke session ${device.id}`" variant="text" :disabled="loading" @click="revoke(device)" /></template></v-tooltip>
      </div>
      <p v-if="!loading && !devices.length">No active sessions</p>
    </section>
    <v-dialog :model-value="dialog" :persistent="busy || !!recovery.length" max-width="520" scrollable @update:model-value="close">
      <v-card class="subscriber-security-dialog">
        <v-card-title>{{ recovery.length ? 'Recovery codes' : titles[mode] }}</v-card-title>
        <v-card-text>
          <v-alert v-if="dialogError" type="error" variant="tonal" class="mb-4">{{ dialogError }}</v-alert>
          <template v-if="recovery.length">
            <div class="recovery-actions"><v-tooltip text="Download recovery codes"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-download" aria-label="Download recovery codes" variant="text" @click="saveRecovery" /></template></v-tooltip><v-tooltip text="Copy recovery codes"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-content-copy" aria-label="Copy recovery codes" variant="text" @click="copy(recovery.join('\n'))" /></template></v-tooltip></div>
            <ul class="recovery-codes"><li v-for="item in recovery" :key="item"><code>{{ item }}</code></li></ul>
            <v-checkbox v-model="accepted" label="I have stored my recovery codes securely" hide-details />
          </template>
          <form v-else id="subscriber-security-form" class="security-form" @submit.prevent="submit">
            <v-alert v-if="mode === 'password'" type="warning" variant="tonal">All sessions will be signed out.</v-alert>
            <v-alert v-if="mode === 'link'" type="warning" variant="tonal">Existing subscription links will stop working.</v-alert>
            <v-alert v-if="mode === 'disable'" type="warning" variant="tonal">Two-factor authentication and recovery codes will be removed. Other sessions will be signed out.</v-alert>
            <v-alert v-if="mode === 'recovery'" type="warning" variant="tonal">Existing recovery codes will stop working. Other sessions will be signed out.</v-alert>
            <template v-if="enrollment">
              <img :src="qr" alt="Authenticator enrollment QR code" width="240" height="240" class="totp-qr" />
              <v-text-field :model-value="enrollment.secret" label="Setup key" readonly hide-details append-inner-icon="mdi-content-copy" @click:append-inner="copy(enrollment.secret)" />
              <p class="enrollment-expiry">Expires {{ date(enrollment.expires_at) }}</p>
            </template>
            <v-text-field v-else v-model="password" label="Current password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
            <template v-if="mode === 'password'">
              <v-text-field v-model="newPassword" label="New password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="busy" />
              <v-text-field v-model="confirmation" label="Confirm password" type="password" autocomplete="new-password" required maxlength="1024" :disabled="busy" :error-messages="confirmation && confirmation !== newPassword ? 'Passwords do not match' : ''" />
            </template>
            <v-text-field v-if="needsCode" v-model="code" :label="enrollment ? 'Authenticator code' : 'Authenticator or recovery code'" autocomplete="one-time-code" :inputmode="enrollment ? 'numeric' : 'text'" required maxlength="64" :disabled="busy" />
          </form>
        </v-card-text>
        <v-card-actions><v-btn :disabled="busy || (!!recovery.length && !accepted)" @click="close">{{ recovery.length ? 'Done' : 'Cancel' }}</v-btn><v-spacer /><v-btn v-if="!recovery.length" form="subscriber-security-form" type="submit" color="primary" :prepend-icon="mode === 'enroll' ? 'mdi-shield-check-outline' : 'mdi-check'" :disabled="!canSubmit" :loading="busy">{{ mode === 'enroll' ? (enrollment ? 'Verify and enable' : 'Continue') : 'Confirm' }}</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.subscriber-security, .subscriber-security-dialog, .subscriber-security-dialog :deep(*), .subscriber-security :deep(*) { letter-spacing: 0; }
.subscriber-security { display: grid; gap: 12px; }
.security-heading, .security-setting, .security-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.security-heading h2 { font-size: 18px; }
.security-setting { border-bottom: 1px solid #dbe5e0; padding: 20px 0; }
.security-setting h3 { font-size: 14px; }
.security-setting p, .security-device p, .enrollment-expiry { color: #66736f; font-size: 13px; margin-top: 6px; overflow-wrap: anywhere; }
.security-devices { padding-top: 24px; }
.security-actions { justify-content: flex-end; }
.security-device { display: grid; grid-template-columns: 24px minmax(0, 1fr) 48px; gap: 14px; align-items: start; border-top: 1px solid #dbe5e0; padding: 20px 0; margin-top: 12px; }
.device-details { min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
.device-details dl { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 4px 8px; margin-top: 10px; font-size: 12px; }
.device-details dt { color: #66736f; }
.security-form { display: grid; gap: 12px; }
.totp-qr { justify-self: center; max-width: 100%; height: auto; aspect-ratio: 1; }
.subscriber-security-dialog :deep(.v-card-title) { font-size: 18px; white-space: normal; }
.subscriber-security-dialog :deep(.v-label) { white-space: normal; }
.subscriber-security-dialog :deep(.v-card-actions) { flex-wrap: wrap; }
.subscriber-security-dialog :deep(.v-alert__content) { font-size: 14px; overflow-wrap: anywhere; }
.recovery-actions { display: flex; justify-content: flex-end; }
.recovery-codes { list-style: none; display: grid; gap: 12px; padding: 12px 0; font-size: 14px; overflow-wrap: anywhere; text-align: center; }
@media (max-width: 400px) { .security-device { gap: 8px; } }
</style>
