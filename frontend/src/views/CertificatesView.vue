<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { listServers } from "../services/inventory";
import { certificateRequest, type CertificateCapabilities, type CertificateDetail, type DNSProvider, type ManagedCertificate } from "../services/certificates";
import type { ServerSummary } from "../domain/inventory";

const tab = ref("certificates");
const certificates = ref<ManagedCertificate[]>([]);
const providers = ref<DNSProvider[]>([]);
const capabilities = ref<CertificateCapabilities>({ available: false, directories: [], providers: [] });
const servers = ref<ServerSummary[]>([]);
const selected = ref("");
const detail = ref<CertificateDetail | null>(null);
const error = ref("");
const busy = ref(false);
const loading = ref(false);
const dialog = ref<"" | "provider" | "certificate" | "import">("");
const editingProvider = ref("");
const providerForm = reactive({ name: "", provider: "cloudflare", credentials: {} as Record<string, string> });
const form = reactive({ name: "", domains: "", email: "", provider_id: "", directory_url: "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" });
const importForm = reactive({ name: "", cert_pem: "", key_pem: "" });
const target = reactive({ server_id: "", domain: "", cert_name: "", reload: "nginx", auto_deploy: true });
const force = ref(false);
const providerFields = computed(() => capabilities.value.providers.find((item) => item.id === providerForm.provider));
const serverOptions = computed(() => servers.value.map((server) => ({ title: server.name, value: server.id })));
const providerOptions = computed(() => providers.value.map((provider) => ({ title: provider.name, value: provider.id })));
let timer: ReturnType<typeof setInterval> | undefined;

function date(value: number | null) { return value ? new Date(value * 1000).toLocaleString() : "-"; }
function color(status: string) { return ["issued", "succeeded"].includes(status) ? "success" : ["failed", "interrupted"].includes(status) ? "error" : "secondary"; }
function serverName(id: string) { return servers.value.find((server) => server.id === id)?.name ?? id; }

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    const [catalog, dns, options, inventory] = await Promise.all([
      certificateRequest<{ certificates: ManagedCertificate[] }>(),
      certificateRequest<{ providers: DNSProvider[] }>("/providers"),
      certificateRequest<CertificateCapabilities>("/capabilities"), listServers(),
    ]);
    certificates.value = catalog.certificates;
    providers.value = dns.providers;
    capabilities.value = options;
    servers.value = inventory;
    const current = selected.value;
    if (current) {
      const response = await certificateRequest<CertificateDetail>(`/${current}`);
      if (selected.value === current) detail.value = response;
    }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "Certificate request failed"; }
  finally { loading.value = false; }
}

async function action(work: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try { await work(); await refresh(); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "Certificate request failed"; }
  finally { busy.value = false; }
}

async function inspect(row: ManagedCertificate) {
  if (busy.value) return;
  selected.value = row.id;
  detail.value = null;
  force.value = false;
  target.domain = row.domains[0]?.startsWith("*.") ? "" : row.domains[0] ?? "";
  target.cert_name = (row.domains[0] ?? "").replace("*.", "_.");
  await action(async () => {
    const response = await certificateRequest<CertificateDetail>(`/${row.id}`);
    if (selected.value === row.id) detail.value = response;
  });
}

function openProvider(provider?: DNSProvider) {
  editingProvider.value = provider?.id ?? "";
  providerForm.name = provider?.name ?? "";
  providerForm.provider = provider?.provider ?? "cloudflare";
  providerForm.credentials = {};
  dialog.value = "provider";
}

function openCertificate() {
  Object.assign(form, { name: "", domains: "", email: "", provider_id: providers.value[0]?.id ?? "", directory_url: capabilities.value.directories[0] ?? "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" });
  dialog.value = "certificate";
}

function saveProvider() { void action(async () => {
  const credentials = Object.fromEntries(Object.entries(providerForm.credentials).filter(([, value]) => value));
  await certificateRequest(`/providers${editingProvider.value ? "/" + editingProvider.value : ""}`, editingProvider.value ? "PUT" : "POST", { ...providerForm, credentials });
  dialog.value = "";
}); }

function createCertificate() { void action(async () => {
  await certificateRequest("", "POST", { ...form, domains: form.domains.trim().split(/[\s,]+/), eab_kid: form.eab_kid || null, eab_hmac_key: form.eab_hmac_key || null });
  dialog.value = "";
}); }

function importCertificate() { void action(async () => {
  await certificateRequest("/import", "POST", { ...importForm });
  dialog.value = "";
}); }

function queue(row: ManagedCertificate, forced = false) { void action(() => certificateRequest(`/${row.id}/${row.version_id ? "renew" : "issue"}`, "POST", { force: forced })); }
function toggleRenew(row: ManagedCertificate, enabled: boolean | null) { void action(() => certificateRequest(`/${row.id}`, "PATCH", { name: row.name, auto_renew: Boolean(enabled) })); }
function remove(row: ManagedCertificate) {
  if (!window.confirm(`Delete certificate "${row.name}"?`)) return;
  void action(async () => { await certificateRequest(`/${row.id}`, "DELETE"); if (selected.value === row.id) { selected.value = ""; detail.value = null; } });
}
function removeProvider(provider: DNSProvider) {
  if (window.confirm(`Delete DNS provider "${provider.name}"?`)) void action(() => certificateRequest(`/providers/${provider.id}`, "DELETE"));
}
function saveTarget() { void action(() => certificateRequest(`/${selected.value}/targets`, "POST", { ...target })); }
function removeTarget(id: string) {
  if (window.confirm("Remove deployment target?")) void action(() => certificateRequest(`/${selected.value}/targets/${id}`, "DELETE"));
}
function restoreVersion(id: string) {
  if (window.confirm("Activate this certificate version?")) void action(() => certificateRequest(`/${selected.value}/versions/${id}/activate`, "POST"));
}
function download(privateKey = false) {
  if (privateKey && !window.confirm("Download the private key?")) return;
  void action(async () => {
    const data = await certificateRequest<{ cert_pem: string; key_pem?: string }>(`/${selected.value}/material?include_private_key=${privateKey}`);
    const url = URL.createObjectURL(new Blob([privateKey ? data.key_pem ?? "" : data.cert_pem], { type: "application/x-pem-file" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${detail.value?.certificate.domains[0]?.replace("*.", "_.") ?? "certificate"}.${privateKey ? "key" : "pem"}`;
    link.click();
    URL.revokeObjectURL(url);
  });
}

watch(() => providerForm.provider, () => { providerForm.credentials = {}; });
watch(dialog, (value) => { if (!value) { providerForm.credentials = {}; importForm.cert_pem = ""; importForm.key_pem = ""; form.eab_kid = ""; form.eab_hmac_key = ""; } });
onMounted(() => { void refresh(); timer = setInterval(() => { if (!busy.value && !dialog.value && !document.hidden) void refresh(); }, 5000); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <div class="page-shell certificates-page">
    <header class="certificate-heading">
      <h1>Certificates</h1>
      <v-btn icon="mdi-refresh" variant="text" title="Refresh certificates" aria-label="Refresh certificates" :loading="loading" @click="refresh" />
    </header>
    <v-alert v-if="error" type="error" closable @click:close="error = ''">{{ error }}</v-alert>
    <v-tabs v-model="tab" color="primary"><v-tab value="certificates">Certificates</v-tab><v-tab value="providers">DNS providers</v-tab></v-tabs>
    <section v-if="tab === 'certificates'" class="certificate-section">
      <div class="certificate-toolbar">
        <v-btn prepend-icon="mdi-plus" color="primary" :disabled="busy || !providers.length" @click="openCertificate">New certificate</v-btn>
        <v-btn prepend-icon="mdi-import" variant="outlined" :disabled="busy" @click="dialog = 'import'">Import PEM</v-btn>
        <v-chip v-if="!capabilities.available" color="warning" size="small">ACME unavailable</v-chip>
      </div>
      <p v-if="!certificates.length" class="certificate-empty">No certificates</p>
      <article v-for="row in certificates" :key="row.id" class="certificate-row">
        <div class="certificate-identity"><button class="certificate-name" @click="inspect(row)">{{ row.name }}</button><div>{{ row.domains.join(', ') }}</div></div>
        <v-chip :color="color(row.status)" size="small">{{ row.status }}</v-chip>
        <div class="certificate-expiry"><span>Expires</span><time>{{ date(row.expires_at) }}</time></div>
        <div class="certificate-actions">
          <v-btn icon="mdi-folder-open-outline" variant="text" size="small" title="Certificate details" aria-label="Certificate details" :disabled="busy" @click="inspect(row)" />
          <v-btn :icon="row.version_id ? 'mdi-autorenew' : 'mdi-certificate-outline'" variant="text" size="small" :title="row.version_id ? 'Renew certificate' : 'Issue certificate'" :aria-label="row.version_id ? 'Renew certificate' : 'Issue certificate'" :disabled="busy || Boolean(row.active_job_id) || !row.provider_id || !capabilities.available" @click="queue(row)" />
          <v-btn icon="mdi-trash-can-outline" variant="text" size="small" title="Delete certificate" aria-label="Delete certificate" :disabled="busy || Boolean(row.active_job_id)" @click="remove(row)" />
        </div>
      </article>
    </section>
    <section v-else class="certificate-section">
      <div class="certificate-toolbar"><v-btn prepend-icon="mdi-plus" color="primary" :disabled="busy" @click="openProvider()">Add DNS provider</v-btn></div>
      <p v-if="!providers.length" class="certificate-empty">No DNS providers</p>
      <article v-for="provider in providers" :key="provider.id" class="provider-row">
        <div class="certificate-identity"><strong>{{ provider.name }}</strong><div>{{ provider.provider }}</div></div>
        <div class="credential-fields">{{ provider.credential_fields.join(', ') }}</div>
        <div class="certificate-actions"><v-btn icon="mdi-key-change" title="Rotate DNS credentials" aria-label="Rotate DNS credentials" variant="text" size="small" :disabled="busy" @click="openProvider(provider)" /><v-btn icon="mdi-trash-can-outline" title="Delete DNS provider" aria-label="Delete DNS provider" variant="text" size="small" :disabled="busy" @click="removeProvider(provider)" /></div>
      </article>
    </section>

    <section v-if="detail && tab === 'certificates'" class="certificate-detail">
      <header class="certificate-heading"><h2>{{ detail.certificate.name }}</h2><v-btn icon="mdi-close" variant="text" title="Close certificate details" aria-label="Close certificate details" @click="detail = null; selected = ''" /></header>
      <v-alert v-if="detail.certificate.last_error" type="error" variant="tonal">{{ detail.certificate.last_error }}</v-alert>
      <div class="certificate-toolbar">
        <v-switch :model-value="detail.certificate.auto_renew" label="Auto-renew" color="primary" hide-details density="compact" :disabled="busy || !detail.certificate.provider_id" @update:model-value="toggleRenew(detail.certificate, $event)" />
        <v-checkbox v-model="force" label="Force renewal" hide-details density="compact" :disabled="busy || !detail.certificate.provider_id" />
        <v-btn icon="mdi-autorenew" title="Renew now" aria-label="Renew now" variant="text" :disabled="busy || !detail.certificate.version_id || !detail.certificate.provider_id || Boolean(detail.certificate.active_job_id) || !capabilities.available" @click="queue(detail.certificate, force)" />
        <v-btn icon="mdi-certificate-outline" title="Download certificate" aria-label="Download certificate" variant="text" :disabled="busy || !detail.certificate.version_id" @click="download()" />
        <v-btn icon="mdi-key-arrow-right" title="Download private key" aria-label="Download private key" variant="text" :disabled="busy || !detail.certificate.version_id" @click="download(true)" />
      </div>
      <h3>Deployment targets</h3>
      <v-form class="certificate-form target-form" @submit.prevent="saveTarget">
        <v-select v-model="target.server_id" label="Target server" :items="serverOptions" hide-details density="comfortable" variant="outlined" />
        <v-text-field v-model="target.domain" label="Hostname" hide-details density="comfortable" variant="outlined" />
        <v-text-field v-model="target.cert_name" label="Certificate filename" hide-details density="comfortable" variant="outlined" />
        <v-select v-model="target.reload" label="Reload" :items="['nginx', 'xray', 'both', 'none']" hide-details density="comfortable" variant="outlined" />
        <v-checkbox v-model="target.auto_deploy" label="Auto-deploy" hide-details density="compact" />
        <v-btn type="submit" prepend-icon="mdi-plus" :disabled="busy || !target.server_id || !target.domain || !target.cert_name" variant="outlined">Add target</v-btn>
      </v-form>
      <article v-for="item in detail.targets" :key="item.id" class="provider-row">
        <div class="certificate-identity"><strong>{{ serverName(item.server_id) }}</strong><div>{{ item.domain }} / {{ item.cert_name }}</div><div v-if="item.error" class="certificate-error">{{ item.error }}</div></div>
        <v-chip :color="color(item.status)" size="small">{{ item.status }}</v-chip>
        <div class="certificate-actions"><v-btn icon="mdi-cloud-upload-outline" title="Deploy certificate" aria-label="Deploy certificate" size="small" variant="text" :disabled="busy || !detail.certificate.version_id" @click="action(() => certificateRequest(`/${selected}/targets/${item.id}/deploy`, 'POST'))" /><v-btn icon="mdi-trash-can-outline" title="Remove target" aria-label="Remove target" size="small" variant="text" :disabled="busy" @click="removeTarget(item.id)" /></div>
      </article>
      <h3>Versions</h3>
      <div v-for="version in detail.versions" :key="version.id" class="version-row"><div><strong>{{ date(version.created_at) }}</strong><div class="version-issuer">{{ version.details.issuer }}</div></div><time>{{ date(version.details.expires_at) }}</time><v-chip v-if="version.id === detail.certificate.version_id" color="success" size="small">Active</v-chip><v-btn v-else icon="mdi-restore" variant="text" title="Activate version" aria-label="Activate version" :disabled="busy || Boolean(detail.certificate.active_job_id)" @click="restoreVersion(version.id)" /></div>
      <h3>Jobs</h3>
      <div v-for="job in detail.jobs" :key="job.id" class="version-row"><div><strong>{{ job.kind }}</strong><div>{{ job.message }}</div></div><time>{{ date(job.created_at) }}</time><v-chip :color="color(job.status)" size="small">{{ job.status }}</v-chip></div>
    </section>

    <v-dialog :model-value="Boolean(dialog)" max-width="620" :persistent="busy" @update:model-value="(value) => { if (!value) dialog = ''; }">
      <v-card class="certificate-dialog">
        <v-card-title>{{ dialog === 'provider' ? (editingProvider ? 'Rotate DNS credentials' : 'Add DNS provider') : dialog === 'import' ? 'Import PEM' : 'New certificate' }}</v-card-title>
        <v-card-text>
          <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
          <v-form v-if="dialog === 'provider'" id="provider-form" class="certificate-form" @submit.prevent="saveProvider">
            <v-text-field v-model="providerForm.name" label="Provider name" variant="outlined" hide-details />
            <v-select v-model="providerForm.provider" label="DNS provider type" :items="capabilities.providers.map((item) => item.id)" :disabled="Boolean(editingProvider)" variant="outlined" hide-details />
            <v-text-field v-for="field in providerFields?.fields ?? []" :key="field" v-model="providerForm.credentials[field]" :label="field" :type="field.endsWith('ENDPOINT') ? 'url' : 'password'" autocomplete="off" variant="outlined" hide-details />
          </v-form>
          <v-form v-else-if="dialog === 'certificate'" id="certificate-form" class="certificate-form" @submit.prevent="createCertificate">
            <v-text-field v-model="form.name" label="Certificate name" variant="outlined" hide-details />
            <v-textarea v-model="form.domains" label="DNS names" rows="2" variant="outlined" hide-details />
            <v-text-field v-model="form.email" label="Account email" type="email" variant="outlined" hide-details />
            <v-select v-model="form.provider_id" label="DNS provider" :items="providerOptions" variant="outlined" hide-details />
            <v-select v-model="form.directory_url" label="ACME directory" :items="capabilities.directories" variant="outlined" hide-details />
            <details><summary>External account binding</summary><div class="certificate-form mt-3"><v-text-field v-model="form.eab_kid" label="EAB key ID" type="password" variant="outlined" hide-details /><v-text-field v-model="form.eab_hmac_key" label="EAB HMAC key" type="password" variant="outlined" hide-details /></div></details>
            <v-switch v-model="form.auto_renew" label="Auto-renew" color="primary" hide-details density="compact" />
            <v-checkbox v-model="form.accept_terms" label="I accept this CA's terms of service" hide-details density="compact" />
          </v-form>
          <v-form v-else id="import-form" class="certificate-form" @submit.prevent="importCertificate">
            <v-text-field v-model="importForm.name" label="Certificate name" variant="outlined" hide-details />
            <v-textarea v-model="importForm.cert_pem" label="Certificate PEM" rows="5" variant="outlined" hide-details spellcheck="false" />
            <v-textarea v-model="importForm.key_pem" label="Private key PEM" rows="5" variant="outlined" hide-details spellcheck="false" autocomplete="off" />
          </v-form>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn :disabled="busy" @click="dialog = ''">Cancel</v-btn><v-btn v-if="dialog === 'provider'" type="submit" form="provider-form" color="primary" :loading="busy" :disabled="!providerForm.name || !providerFields?.required.every((key) => providerForm.credentials[key])">Save provider</v-btn><v-btn v-else-if="dialog === 'certificate'" type="submit" form="certificate-form" color="primary" :loading="busy" :disabled="!form.name || !form.domains.trim() || !form.email || !form.provider_id || !form.accept_terms">Create certificate</v-btn><v-btn v-else type="submit" form="import-form" color="primary" :loading="busy" :disabled="!importForm.name || !importForm.cert_pem || !importForm.key_pem">Import certificate</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.certificates-page { max-width: 1440px; min-width: 0; }
.certificate-heading, .certificate-toolbar, .certificate-actions { display: flex; align-items: center; gap: 12px; }
.certificate-heading { justify-content: space-between; }
h1 { font-size: 28px; } h2 { font-size: 22px; } h3 { font-size: 16px; margin: 24px 0 12px; }
.certificate-toolbar { flex-wrap: wrap; padding: 18px 0; }
.certificate-toolbar > .v-input { flex: 0 0 auto; }
.certificate-section { padding-bottom: 24px; }
.certificate-row, .provider-row, .version-row { display: grid; align-items: center; gap: 16px; border-bottom: 1px solid #dfe5e2; padding: 16px 0; }
.certificate-row { grid-template-columns: minmax(180px, 1fr) 110px 190px auto; }
.certificate-row > .v-chip, .provider-row > .v-chip, .version-row > .v-chip { justify-self: start; }
.provider-row, .version-row { grid-template-columns: minmax(160px, 1fr) minmax(100px, 0.7fr) auto; }
.certificate-identity, .credential-fields, .version-issuer { min-width: 0; overflow-wrap: anywhere; }
.certificate-identity > div, .certificate-expiry span, .certificate-empty, .version-issuer { color: #66736f; font-size: 14px; }
.certificate-name { text-align: left; font-weight: 600; color: #176958; }
.certificate-expiry { display: grid; gap: 4px; font-size: 14px; }
.certificate-detail { border-top: 2px solid #dfe5e2; padding-top: 20px; }
.certificate-form { display: grid; gap: 14px; min-width: 0; }
.target-form { grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: center; }
.certificate-error { color: #a33232 !important; }
.certificate-dialog { border-radius: 8px; }
.certificate-dialog :deep(.v-card-title) { white-space: normal; font-size: 20px; }
:deep(.v-label), :deep(.v-btn) { letter-spacing: 0; }
@media (max-width: 900px) {
  .certificate-row { grid-template-columns: minmax(0, 1fr) auto; }
  .provider-row, .version-row { grid-template-columns: minmax(0, 1fr) auto; }
  .credential-fields { grid-column: 1; }
  .version-row time { font-size: 13px; }
}
@media (max-width: 600px) {
  .certificates-page { padding: 20px; }
  h1 { font-size: 24px; }
  .target-form, .version-row { grid-template-columns: minmax(0, 1fr); }
  .certificate-row, .provider-row { gap: 10px; }
  .certificate-actions { gap: 0; justify-content: flex-end; }
}
</style>
