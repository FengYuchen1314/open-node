<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { ProductUserSubscriptionToken } from "../domain/subscriptions";
import { shortCodeError } from "../domain/subscription-links";
import { getProductUserSubscriptionToken, updateProductUserShortCode } from "../services/subscriptions";
import { subscriberSecurity, subscriberShortCode, subscriberToken } from "../services/subscriber-auth";

const props = withDefaults(defineProps<{ open: boolean; username: string; subscriber?: boolean }>(), { subscriber: false });
const emit = defineEmits<{ "update:open": [value: boolean]; saved: [value: ProductUserSubscriptionToken] }>();
const detail = ref<ProductUserSubscriptionToken | null>(null);
const custom = ref<string | null>("");
const password = ref("");
const factor = ref("");
const needsFactor = ref(false);
const busy = ref(false);
const error = ref("");
const saved = ref(false);
let version = 0;
const code = computed(() => (custom.value ?? "").trim());
const invalid = computed(() => shortCodeError(custom.value));
const canSave = computed(() => !!detail.value?.revision && !busy.value && !invalid.value
  && code.value !== (detail.value.custom_short_code ?? "")
  && (!props.subscriber || (!!password.value && (!needsFactor.value || !!factor.value.trim()))));
const replacing = computed(() => detail.value?.custom_short_code && detail.value.custom_short_code !== detail.value.generated_short_code && code.value !== detail.value.custom_short_code);
function clearSecrets() { password.value = ""; factor.value = ""; }
async function load() {
  const current = ++version;
  detail.value = null; custom.value = ""; error.value = ""; saved.value = false; busy.value = false; clearSecrets();
  if (!props.open || !props.username) return;
  busy.value = true;
  try {
    const value = props.subscriber ? await subscriberToken() : (await getProductUserSubscriptionToken(props.username)).subscription;
    const security = props.subscriber ? await subscriberSecurity() : null;
    if (current !== version) return;
    detail.value = value; custom.value = value.custom_short_code ?? ""; needsFactor.value = !!security?.totp_enabled;
  } catch (failure) { if (current === version) error.value = failure instanceof Error ? failure.message : "Subscription links unavailable"; }
  finally { if (current === version) busy.value = false; }
}
async function save() {
  if (!canSave.value || !detail.value) return;
  const current = ++version;
  busy.value = true; error.value = ""; saved.value = false;
  try {
    const value = props.subscriber
      ? await subscriberShortCode(code.value, detail.value.revision, { password: password.value, code: factor.value })
      : (await updateProductUserShortCode(props.username, code.value, detail.value.revision)).subscription;
    if (current !== version) return;
    detail.value = value; custom.value = value.custom_short_code ?? ""; saved.value = true;
    emit("saved", value);
  } catch (failure) { if (current === version) error.value = failure instanceof Error ? failure.message : "Short code update failed"; }
  finally { if (current === version) { busy.value = false; clearSecrets(); } }
}
async function copy() {
  if (!detail.value) return;
  try { await navigator.clipboard.writeText(detail.value.short_url); }
  catch { error.value = "Clipboard unavailable"; }
}
watch(() => [props.open, props.username, props.subscriber], load, { immediate: true });
onBeforeUnmount(() => { ++version; clearSecrets(); });
</script>

<template>
  <v-dialog :model-value="open" :persistent="busy" max-width="560" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="short-code-dialog">
      <v-card-title class="short-code-heading"><span>Subscription short code</span><v-tooltip text="Reload subscription links"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-refresh" aria-label="Reload subscription links" size="small" variant="text" :disabled="busy" @click="load" /></template></v-tooltip></v-card-title>
      <v-card-text>
        <v-progress-linear v-if="busy" indeterminate class="mb-4" />
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
        <v-alert v-if="saved" type="success" variant="tonal" class="mb-4">Short code saved</v-alert>
        <form v-if="detail" id="short-code-form" class="short-code-form" @submit.prevent="save">
          <strong class="short-code-username">{{ username }}</strong>
          <dl class="short-code-values"><div><dt>System code</dt><dd>{{ detail.generated_short_code }}</dd></div><div><dt>Current code</dt><dd>{{ detail.short_code }}</dd></div></dl>
          <v-text-field v-model="custom" label="Custom short code" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="16" clearable variant="outlined" density="compact" :error-messages="invalid" :hide-details="!invalid" :disabled="busy" />
          <div class="short-code-link"><v-text-field :model-value="detail.short_url" label="Short URL" readonly variant="outlined" density="compact" hide-details /><v-tooltip text="Copy short URL"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-content-copy" aria-label="Copy short URL" variant="text" @click="copy" /></template></v-tooltip></div>
          <v-alert type="warning" variant="tonal">Anyone with a subscription link can download its configuration. Custom short codes can be guessed.</v-alert>
          <v-alert v-if="replacing" type="warning" variant="tonal">The previous custom link will stop working.</v-alert>
          <v-text-field v-if="subscriber" v-model="password" label="Current password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" variant="outlined" density="compact" hide-details />
          <v-text-field v-if="subscriber && needsFactor" v-model="factor" label="Authenticator or recovery code" autocomplete="one-time-code" required maxlength="64" :disabled="busy" variant="outlined" density="compact" hide-details />
        </form>
      </v-card-text>
      <v-card-actions><v-btn :disabled="busy" @click="emit('update:open', false)">{{ saved ? 'Close' : 'Cancel' }}</v-btn><v-spacer /><v-btn type="submit" form="short-code-form" prepend-icon="mdi-content-save" color="primary" :disabled="!canSave">Save</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.short-code-dialog, .short-code-dialog :deep(*) { letter-spacing: 0; }
.short-code-heading { display: grid; grid-template-columns: minmax(0, 1fr) 36px; gap: 12px; align-items: center; font-size: 18px; white-space: normal; }
.short-code-form { display: grid; gap: 20px; min-width: 0; }
.short-code-username { font-size: 14px; overflow-wrap: anywhere; }
.short-code-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; font-size: 13px; }
.short-code-values dt { color: #66736f; margin-bottom: 4px; }
.short-code-values dd { overflow-wrap: anywhere; }
.short-code-link { display: grid; grid-template-columns: minmax(0, 1fr) 48px; gap: 8px; align-items: center; }
.short-code-dialog :deep(.v-alert__content) { font-size: 14px; overflow-wrap: anywhere; }
.short-code-dialog :deep(.v-card-actions) { flex-wrap: wrap; }
</style>
