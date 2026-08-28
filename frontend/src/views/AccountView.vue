<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import SubscriberSecurityPanel from "../components/SubscriberSecurityPanel.vue";
import type { ProductUserSubscriptionToken, SubscriptionClientFormat } from "../domain/subscriptions";
import {
  loadSubscriberSession, subscriberFormatUrl, subscriberProfile, subscriberSignIn, subscriberSignOut,
  subscriberState, subscriberToken, verifySubscriberLogin, type SubscriberProfile,
} from "../services/subscriber-auth";

const username = ref("");
const password = ref("");
const code = ref("");
const challenge = ref("");
const busy = ref(false);
const signInError = ref("");
const error = ref("");
const loading = ref(false);
const tab = ref("subscription");
const profile = ref<SubscriberProfile | null>(null);
const subscription = ref<ProductUserSubscriptionToken | null>(null);
const format = ref<SubscriptionClientFormat>("clash");
const copied = ref(false);
const formats = [
  { title: "Clash / Mihomo", value: "clash" }, { title: "sing-box", value: "sing-box" },
  { title: "Xray", value: "xray" }, { title: "URI list", value: "uri-list" }, { title: "Base64", value: "base64" },
];
let version = 0;
const url = computed(() => subscription.value ? subscriberFormatUrl(subscription.value, format.value) : "");
const quota = computed(() => profile.value?.quota);
const status = computed(() => !quota.value?.has_plan ? "No plan" : quota.value.expired ? "Expired" : quota.value.over_quota ? "Quota reached" : "Active");
const date = (value?: string | null) => value ? new Date(value).toLocaleDateString() : "None";
function bytes(value: number) {
  if (!value) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
async function submit() {
  if (busy.value) return;
  busy.value = true; signInError.value = "";
  try {
    const result = challenge.value ? await verifySubscriberLogin(challenge.value, code.value) : await subscriberSignIn(username.value, password.value);
    challenge.value = result.challenge ?? "";
  } catch (failure) { signInError.value = failure instanceof Error ? failure.message : "Sign-in failed"; }
  finally { password.value = ""; code.value = ""; busy.value = false; }
}
function restartSignIn() { challenge.value = ""; code.value = ""; password.value = ""; signInError.value = ""; }
async function load() {
  const current = ++version;
  loading.value = true; error.value = "";
  try {
    const [account, token] = await Promise.all([subscriberProfile(), subscriberToken()]);
    if (current === version) { profile.value = account; subscription.value = token; }
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Account unavailable";
  } finally { if (current === version) loading.value = false; }
}
async function logout() {
  error.value = "";
  try { await subscriberSignOut(); }
  catch (failure) { error.value = failure instanceof Error ? failure.message : "Sign-out failed"; }
}
async function copyLink() {
  try { await navigator.clipboard.writeText(url.value); copied.value = true; }
  catch { error.value = "Clipboard unavailable"; }
}
watch(url, () => { copied.value = false; });
watch(() => subscriberState.ready && subscriberState.session?.authenticated, (authenticated) => {
  ++version; profile.value = null; subscription.value = null; error.value = "";
  if (authenticated) { tab.value = "subscription"; void load(); }
  else restartSignIn();
});
onMounted(async () => { subscriberState.ready = false; await loadSubscriberSession(); });
onBeforeUnmount(() => { ++version; password.value = ""; code.value = ""; challenge.value = ""; });
</script>

<template>
  <div v-if="!subscriberState.ready" class="auth-page" role="status" aria-label="Loading account"><v-progress-circular indeterminate color="primary" /></div>
  <section v-else-if="!subscriberState.session?.authenticated" class="auth-page">
    <div class="auth-workspace">
      <div class="brand-mark" aria-hidden="true">ON</div><h1>Open Node</h1><h2>{{ challenge ? 'Two-Factor Verification' : 'Subscriber Sign-In' }}</h2>
      <v-alert v-if="subscriberState.error" type="error" variant="tonal">{{ subscriberState.error }}<v-btn icon="mdi-refresh" aria-label="Retry account connection" title="Retry account connection" @click="loadSubscriberSession()" /></v-alert>
      <form v-else class="auth-form" @submit.prevent="submit">
        <v-alert v-if="signInError" type="error" variant="tonal">{{ signInError }}</v-alert>
        <template v-if="!challenge"><v-text-field v-model="username" label="Username" autocomplete="username" autofocus required maxlength="80" :disabled="busy" /><v-text-field v-model="password" label="Password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" /></template>
        <v-text-field v-else v-model="code" label="Authenticator or recovery code" autocomplete="one-time-code" autofocus required maxlength="64" :disabled="busy" />
        <v-btn type="submit" color="primary" prepend-icon="mdi-login" :loading="busy" :disabled="challenge ? !code : !username || !password">{{ challenge ? 'Verify' : 'Sign In' }}</v-btn>
        <v-btn v-if="challenge" variant="text" prepend-icon="mdi-arrow-left" :disabled="busy" @click="restartSignIn">Back</v-btn>
      </form>
      <router-link to="/" class="account-admin-link">Administrator sign-in</router-link>
    </div>
  </section>
  <div v-else class="subscriber-account">
    <header class="account-header"><div class="account-brand"><div class="brand-mark" aria-hidden="true">ON</div><h1>Open Node</h1></div><div class="account-identity"><span>{{ subscriberState.session.username }}</span><v-tooltip text="Sign out"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-logout" aria-label="Sign out" variant="text" @click="logout" /></template></v-tooltip></div></header>
    <main class="account-content">
      <div class="account-heading"><h2>{{ profile?.display_name || subscriberState.session.username }}</h2><v-tooltip text="Refresh account"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-refresh" aria-label="Refresh account" variant="text" :loading="loading" @click="load" /></template></v-tooltip></div>
      <v-tabs v-model="tab" color="primary" class="account-tabs"><v-tab value="subscription" prepend-icon="mdi-link-variant">Subscription</v-tab><v-tab value="security" prepend-icon="mdi-shield-account-outline">Security</v-tab></v-tabs>
      <v-alert v-if="error" type="error" variant="tonal" class="my-4">{{ error }}</v-alert>
      <v-progress-linear v-if="loading" indeterminate color="primary" />
      <template v-if="tab === 'subscription' && profile && quota">
        <section class="account-plan" aria-label="Current plan">
          <div class="account-plan-title"><div><p class="account-label">Current plan</p><h3>{{ quota.plan_name || 'No plan assigned' }}</h3></div><v-chip :color="quota.available ? 'success' : 'warning'" size="small" variant="tonal">{{ status }}</v-chip></div>
          <div class="account-usage"><strong>{{ bytes(quota.charged_usage_bytes) }}</strong><span>/ {{ quota.traffic_limit_bytes ? bytes(quota.traffic_limit_bytes) : quota.has_plan ? 'Unlimited' : '0 B' }}</span></div>
          <v-progress-linear :model-value="Math.min(quota.percent_used, 100)" :color="quota.over_quota ? 'error' : 'primary'" height="6" class="my-4" aria-label="Traffic quota used" />
          <dl class="account-facts"><div><dt>Expires</dt><dd>{{ date(quota.plan_expires_at) }}</dd></div><div><dt>Next reset</dt><dd>{{ date(quota.next_reset_at) }}</dd></div><div><dt>Speed limit</dt><dd>{{ profile.speed_limit_mbps ? `${profile.speed_limit_mbps} Mbps` : 'Unlimited' }}</dd></div><div><dt>Device limit</dt><dd>{{ profile.device_limit || 'Unlimited' }}</dd></div><div><dt>Uploaded</dt><dd>{{ bytes(quota.upload) }}</dd></div><div><dt>Downloaded</dt><dd>{{ bytes(quota.download) }}</dd></div></dl>
        </section>
        <section class="account-links" aria-label="Subscription links">
          <h3>Subscription</h3><div class="account-link-controls"><v-select v-model="format" :items="formats" label="Client format" variant="outlined" density="compact" hide-details /><div class="account-link-actions"><v-tooltip :text="copied ? 'Copied' : 'Copy subscription link'"><template #activator="{ props: tip }"><v-btn v-bind="tip" :icon="copied ? 'mdi-check' : 'mdi-content-copy'" aria-label="Copy subscription link" variant="text" :disabled="!url" @click="copyLink" /></template></v-tooltip><v-tooltip text="Download subscription"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-download" aria-label="Download subscription" variant="text" :href="url" :disabled="!url || !quota.available" rel="noreferrer" download /></template></v-tooltip></div></div>
          <v-text-field :model-value="url" label="Subscription URL" variant="outlined" density="compact" readonly hide-details />
          <v-alert v-if="!quota.available" type="warning" variant="tonal" class="mt-4">{{ !quota.has_plan ? 'No subscription plan assigned' : quota.expired ? 'Your plan has expired' : 'Your traffic quota has been reached' }}</v-alert>
        </section>
      </template>
      <SubscriberSecurityPanel v-else-if="tab === 'security'" class="account-security-panel" @changed="load" />
    </main>
  </div>
</template>

<style scoped>
.subscriber-account, .subscriber-account :deep(*) { letter-spacing: 0; }
.subscriber-account { min-height: 100dvh; background: #fff; }
.account-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 28px; border-bottom: 1px solid #dbe5e0; }
.account-brand, .account-identity { display: flex; align-items: center; gap: 12px; min-width: 0; }
.account-brand { flex-shrink: 0; }
.account-brand h1 { font-size: 20px; }
.account-identity { font-size: 13px; overflow-wrap: anywhere; }
.account-content { max-width: 1000px; margin: 0 auto; padding: 32px 28px 60px; }
.account-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.account-heading h2 { font-size: 24px; overflow-wrap: anywhere; min-width: 0; }
.account-tabs { border-bottom: 1px solid #dbe5e0; }
.account-plan { padding: 28px 0; }
.account-plan-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.account-label, .account-facts dt { font-size: 12px; color: #66736f; }
.account-plan h3 { font-size: 20px; margin-top: 6px; overflow-wrap: anywhere; }
.account-usage { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-top: 28px; }
.account-usage strong { font-size: 28px; }
.account-usage span { font-size: 14px; color: #66736f; }
.account-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; padding-top: 12px; }
.account-facts dd { font-size: 14px; margin-top: 6px; overflow-wrap: anywhere; }
.account-links { border-top: 1px solid #dbe5e0; padding: 28px 0; }
.account-links h3 { font-size: 18px; margin-bottom: 20px; }
.account-link-controls { display: grid; grid-template-columns: minmax(0, 300px) auto; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; }
.account-link-actions { display: flex; }
.account-security-panel { padding-top: 24px; }
.account-admin-link { font-size: 13px; color: #176b5b; justify-self: start; }
@media (max-width: 600px) {
  .account-header { padding: 14px 16px; flex-wrap: wrap; }
  .account-content { padding: 24px 16px 40px; }
  .account-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
  .account-heading h2 { font-size: 22px; }
  .account-link-controls { gap: 8px; }
}
</style>
