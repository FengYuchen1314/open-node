<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { listServers } from "../services/inventory";
import { certificateRequest, type CertificateCapabilities, type CertificateChallenge, type CertificateDetail, type CertificateVersion, type DNSProvider, type ManagedCertificate } from "../services/certificates";
import type { ServerSummary } from "../domain/inventory";

const tab = ref("certificates");
const certificates = ref<ManagedCertificate[]>([]);
const providers = ref<DNSProvider[]>([]);
const capabilities = ref<CertificateCapabilities>({ available: false, account_management: false, revocation: false, directories: [], providers: [], challenge_types: [], webroots: [] });
const servers = ref<ServerSummary[]>([]);
const selected = ref("");
const detail = ref<CertificateDetail | null>(null);
const error = ref("");
const busy = ref(false);
const loading = ref(false);
const dialog = ref<"" | "provider" | "certificate" | "import" | "account" | "revoke">("");
const editingProvider = ref("");
const providerForm = reactive({ name: "", provider: "cloudflare", credentials: {} as Record<string, string> });
const form = reactive({ name: "", domains: "", email: "", challenge_type: "dns" as CertificateChallenge, validation_server_id: "", provider_id: "", webroot_id: "", directory_url: "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" });
const importForm = reactive({ name: "", cert_pem: "", key_pem: "" });
const accountForm = reactive({ email: "", eab_action: "keep", eab_kid: "", eab_hmac_key: "" });
const revokeForm = reactive({ version_id: "", serial: "", directory_url: "", reason: 0, confirm: false });
const reasons = [
  { title: "Unspecified", value: 0 }, { title: "Key compromise", value: 1 },
  { title: "Affiliation changed", value: 3 }, { title: "Superseded", value: 4 },
  { title: "Cessation of operation", value: 5 }, { title: "Privilege withdrawn", value: 9 },
];
const target = reactive({ server_id: "", domain: "", cert_name: "", reload: "nginx", auto_deploy: true });
const force = ref(false);
const providerFields = computed(() => capabilities.value.providers.find((item) => item.id === providerForm.provider));
const serverOptions = computed(() => servers.value.map((server) => ({ title: server.name, value: server.id })));
const providerOptions = computed(() => providers.value.map((provider) => ({ title: provider.name, value: provider.id })));
const challengeLabels: Record<CertificateChallenge, string> = { dns: "DNS-01", standalone: "HTTP-01 / Standalone", webroot: "HTTP-01 / Webroot" };
const validationNodes = computed(() => capabilities.value.remote_http_available ? (capabilities.value.validation_nodes ?? []).filter((node) => !node.cleanup_error) : []);
const challengeTypes = computed(() => [...new Set<CertificateChallenge>([
  ...capabilities.value.challenge_types,
  ...(validationNodes.value.some((node) => node.standalone) ? ["standalone" as const] : []),
  ...(validationNodes.value.some((node) => node.webroots.length) ? ["webroot" as const] : []),
])]);
const challengeOptions = computed(() => challengeTypes.value.map((value) => ({ value, title: challengeLabels[value] })));
const validationOptions = computed(() => [
  { title: "Control plane", value: "", props: { disabled: !capabilities.value.challenge_types.includes(form.challenge_type) } },
  ...validationNodes.value.filter((node) => form.challenge_type === "standalone" ? node.standalone : form.challenge_type === "webroot" && node.webroots.length > 0).map((node) => ({ title: node.name, value: node.id, props: { disabled: false } })),
]);
const webrootOptions = computed(() => form.validation_server_id ? validationNodes.value.find((node) => node.id === form.validation_server_id)?.webroots ?? [] : capabilities.value.webroots);
const wildcardError = computed(() => form.challenge_type !== "dns" && form.domains.trim().split(/[\s,]+/).some((name) => name.startsWith("*.")));
const canCreate = computed(() => Boolean(form.name && form.domains.trim() && form.email && form.directory_url && form.accept_terms && !wildcardError.value && challengeTypes.value.includes(form.challenge_type) && (form.challenge_type === "dns" ? form.provider_id : validationOptions.value.some((option) => option.value === form.validation_server_id && !option.props.disabled)) && (form.challenge_type !== "webroot" || webrootOptions.value.includes(form.webroot_id))));
const hasChallenge = computed(() => providers.value.length > 0 || challengeTypes.value.some((type) => type !== "dns"));
const currentRevocation = computed(() => detail.value?.versions.find((version) => version.id === detail.value?.certificate.version_id)?.revocation);
const canSaveAccount = computed(() => Boolean(accountForm.email.trim() && (accountForm.eab_action !== "replace" || (accountForm.eab_kid && accountForm.eab_hmac_key))));
let timer: ReturnType<typeof setInterval> | undefined;

function date(value: number | null) { return value ? new Date(value * 1000).toLocaleString() : "-"; }
function color(status: string) { return ["issued", "succeeded"].includes(status) ? "success" : ["failed", "interrupted", "revoked"].includes(status) ? "error" : ["unknown", "revocation_unknown"].includes(status) ? "warning" : "secondary"; }
function statusLabel(status: string) { return ({ revocation_pending: "Revoking", revocation_unknown: "Unconfirmed", updating_account: "Account update", not_registered: "Not registered", unconfirmed: "Unconfirmed", unavailable: "Unavailable", registered: "Registered" } as Record<string, string>)[status] ?? status; }
function needsReissue(row: ManagedCertificate) { return ["revoked", "revocation_unknown", "revocation_pending", "revoking"].includes(row.status); }
function serverName(id: string) { return servers.value.find((server) => server.id === id)?.name ?? id; }
function challengeName(row: ManagedCertificate) { return row.directory_url ? challengeLabels[row.challenge_type] + (row.validation_server_id ? " / " + serverName(row.validation_server_id) : "") + (row.webroot_id ? " / " + row.webroot_id : "") : "Imported"; }
function issuerAvailable(row: ManagedCertificate) { return row.validation_server_id ? Boolean(capabilities.value.remote_http_available) : capabilities.value.available; }
function selectValidationHost() {
  if (form.challenge_type === "dns") form.validation_server_id = "";
  else if (!validationOptions.value.some((option) => option.value === form.validation_server_id && !option.props.disabled)) {
    form.validation_server_id = validationOptions.value.find((option) => !option.props.disabled)?.value ?? "";
  }
}

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
  Object.assign(form, { name: "", domains: "", email: "", challenge_type: providers.value.length ? "dns" : challengeTypes.value.find((type) => type !== "dns") ?? "dns", validation_server_id: "", provider_id: providers.value[0]?.id ?? "", webroot_id: capabilities.value.webroots[0] ?? "", directory_url: capabilities.value.directories[0] ?? "", accept_terms: false, auto_renew: true, eab_kid: "", eab_hmac_key: "" });
  selectValidationHost();
  dialog.value = "certificate";
}

function openAccount() {
  Object.assign(accountForm, { email: detail.value?.account?.pending_email ?? detail.value?.certificate.email ?? "", eab_action: "keep", eab_kid: "", eab_hmac_key: "" });
  dialog.value = "account";
}

function saveAccount() { void action(async () => {
  if (!canSaveAccount.value) return;
  await certificateRequest(`/${selected.value}/account`, "POST", {
    email: accountForm.email, eab_action: accountForm.eab_action,
    ...(accountForm.eab_action === "replace" ? { eab_kid: accountForm.eab_kid, eab_hmac_key: accountForm.eab_hmac_key } : {}),
  });
  dialog.value = "";
}); }

function openRevoke(version: CertificateVersion) {
  Object.assign(revokeForm, { version_id: version.id, serial: version.details.serial, reason: version.revocation?.reason ?? 0, directory_url: detail.value?.certificate.directory_url ?? version.revocation?.directory_url ?? "", confirm: false });
  dialog.value = "revoke";
}

function revoke() { void action(async () => {
  if (!revokeForm.confirm || !revokeForm.directory_url) return;
  await certificateRequest(`/${selected.value}/versions/${revokeForm.version_id}/revoke`, "POST", {
    confirm: true, reason: revokeForm.reason, directory_url: revokeForm.directory_url,
  });
  dialog.value = "";
}); }

function saveProvider() { void action(async () => {
  const credentials = Object.fromEntries(Object.entries(providerForm.credentials).filter(([, value]) => value));
  await certificateRequest(`/providers${editingProvider.value ? "/" + editingProvider.value : ""}`, editingProvider.value ? "PUT" : "POST", { ...providerForm, credentials });
  dialog.value = "";
}); }

function createCertificate() { void action(async () => {
  if (!canCreate.value) return;
  await certificateRequest("", "POST", { ...form, validation_server_id: form.challenge_type === "dns" ? null : form.validation_server_id || null, provider_id: form.challenge_type === "dns" ? form.provider_id : null, webroot_id: form.challenge_type === "webroot" ? form.webroot_id : null, domains: form.domains.trim().split(/[\s,]+/), eab_kid: form.eab_kid || null, eab_hmac_key: form.eab_hmac_key || null });
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
watch(() => form.challenge_type, selectValidationHost);
watch(webrootOptions, (values) => { if (!values.includes(form.webroot_id)) form.webroot_id = values[0] ?? ""; });
watch(dialog, (value) => { if (!value) { providerForm.credentials = {}; importForm.cert_pem = ""; importForm.key_pem = ""; form.eab_kid = ""; form.eab_hmac_key = ""; accountForm.eab_kid = ""; accountForm.eab_hmac_key = ""; revokeForm.confirm = false; } });
watch(() => accountForm.eab_action, () => { accountForm.eab_kid = ""; accountForm.eab_hmac_key = ""; });
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
        <v-btn prepend-icon="mdi-plus" color="primary" :disabled="busy || !hasChallenge" @click="openCertificate">New certificate</v-btn>
        <v-btn prepend-icon="mdi-import" variant="outlined" :disabled="busy" @click="dialog = 'import'">Import PEM</v-btn>
        <v-chip v-if="!capabilities.available && !validationNodes.length" color="warning" size="small">ACME unavailable</v-chip>
      </div>
      <p v-if="!certificates.length" class="certificate-empty">No certificates</p>
      <article v-for="row in certificates" :key="row.id" class="certificate-row">
        <div class="certificate-identity"><button class="certificate-name" @click="inspect(row)">{{ row.name }}</button><div>{{ row.domains.join(', ') }}</div><div>{{ challengeName(row) }}</div></div>
        <v-chip :color="color(row.status)" size="small">{{ statusLabel(row.status) }}</v-chip>
        <div class="certificate-expiry"><span>Expires</span><time>{{ date(row.expires_at) }}</time></div>
        <div class="certificate-actions">
          <v-btn icon="mdi-folder-open-outline" variant="text" size="small" title="Certificate details" aria-label="Certificate details" :disabled="busy" @click="inspect(row)" />
          <v-btn :icon="row.version_id ? 'mdi-autorenew' : 'mdi-certificate-outline'" variant="text" size="small" :title="needsReissue(row) ? 'Reissue certificate' : row.version_id ? 'Renew certificate' : 'Issue certificate'" :aria-label="needsReissue(row) ? 'Reissue certificate' : row.version_id ? 'Renew certificate' : 'Issue certificate'" :disabled="busy || Boolean(row.active_job_id) || !row.directory_url || !issuerAvailable(row)" @click="queue(row)" />
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
      <v-alert v-if="currentRevocation" :type="currentRevocation.status === 'revoked' ? 'error' : 'warning'" variant="tonal" class="mt-3">{{ currentRevocation.status === 'revoked' ? 'This certificate is revoked.' : 'Revocation is not yet confirmed.' }} Deployed files remain on nodes.</v-alert>
      <div class="certificate-toolbar">
        <v-switch :model-value="detail.certificate.auto_renew" label="Auto-renew" color="primary" hide-details density="compact" :disabled="busy || Boolean(currentRevocation) || !detail.certificate.directory_url" @update:model-value="toggleRenew(detail.certificate, $event)" />
        <v-checkbox :model-value="force || Boolean(currentRevocation)" label="Force renewal" hide-details density="compact" :disabled="busy || Boolean(currentRevocation) || !detail.certificate.directory_url" @update:model-value="force = Boolean($event)" />
        <v-btn icon="mdi-autorenew" :title="currentRevocation ? 'Reissue certificate' : 'Renew now'" :aria-label="currentRevocation ? 'Reissue certificate' : 'Renew now'" variant="text" :disabled="busy || !detail.certificate.version_id || !detail.certificate.directory_url || Boolean(detail.certificate.active_job_id) || !issuerAvailable(detail.certificate)" @click="queue(detail.certificate, force)" />
        <v-btn icon="mdi-certificate-outline" title="Download certificate" aria-label="Download certificate" variant="text" :disabled="busy || !detail.certificate.version_id" @click="download()" />
        <v-btn icon="mdi-key-arrow-right" title="Download private key" aria-label="Download private key" variant="text" :disabled="busy || !detail.certificate.version_id" @click="download(true)" />
      </div>
      <template v-if="detail.account">
        <h3>ACME account</h3>
        <div class="provider-row account-row">
          <div class="certificate-identity"><strong>{{ detail.account.email }}</strong><div v-if="detail.account.pending_email">Requested: {{ detail.account.pending_email }}</div><div>{{ detail.account.eab_configured ? 'EAB configured' : 'No EAB credentials' }}</div></div>
          <v-chip size="small">{{ statusLabel(detail.account.state) }}</v-chip>
          <div class="certificate-actions">
            <v-btn v-if="detail.account.retry_job_id" icon="mdi-replay" title="Retry account update" aria-label="Retry account update" variant="text" :disabled="busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management" @click="action(() => certificateRequest(`/${selected}/account/jobs/${detail?.account?.retry_job_id}/retry`, 'POST'))" />
            <v-btn icon="mdi-account-edit-outline" title="Edit ACME account" aria-label="Edit ACME account" variant="text" :disabled="busy || Boolean(detail.certificate.active_job_id) || !capabilities.account_management" @click="openAccount" />
          </div>
        </div>
      </template>
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
        <div class="certificate-actions"><v-btn icon="mdi-cloud-upload-outline" title="Deploy certificate" aria-label="Deploy certificate" size="small" variant="text" :disabled="busy || Boolean(currentRevocation) || !detail.certificate.version_id" @click="action(() => certificateRequest(`/${selected}/targets/${item.id}/deploy`, 'POST'))" /><v-btn icon="mdi-trash-can-outline" title="Remove target" aria-label="Remove target" size="small" variant="text" :disabled="busy" @click="removeTarget(item.id)" /></div>
      </article>
      <h3>Versions</h3>
      <div v-for="version in detail.versions" :key="version.id" class="version-row">
        <div class="certificate-identity"><strong>{{ date(version.created_at) }}</strong><div class="version-issuer">{{ version.details.issuer }}</div><div class="version-serial">{{ version.details.serial }}</div></div>
        <time>{{ date(version.details.expires_at) }}</time>
        <div class="version-actions">
          <v-chip v-if="version.revocation" :color="color(version.revocation.status)" size="small">{{ version.revocation.status === 'unknown' ? 'Unconfirmed' : version.revocation.status === 'pending' ? 'Revoking' : 'Revoked' }}</v-chip>
          <v-chip v-if="version.id === detail.certificate.version_id" :color="version.revocation ? undefined : 'success'" size="small">{{ version.revocation ? 'Current' : 'Active' }}</v-chip>
          <v-btn v-else icon="mdi-restore" variant="text" title="Activate version" aria-label="Activate version" :disabled="busy || Boolean(version.revocation) || Boolean(detail.certificate.active_job_id)" @click="restoreVersion(version.id)" />
          <v-btn icon="mdi-cancel" color="error" variant="text" :title="version.revocation?.status === 'unknown' ? 'Retry revocation' : 'Revoke version'" :aria-label="version.revocation?.status === 'unknown' ? 'Retry revocation' : 'Revoke version'" :disabled="busy || !capabilities.revocation || Boolean(detail.certificate.active_job_id) || (Boolean(version.revocation) && version.revocation?.status !== 'unknown')" @click="openRevoke(version)" />
        </div>
      </div>
      <h3>Jobs</h3>
      <div v-for="job in detail.jobs" :key="job.id" class="version-row"><div class="certificate-identity"><strong>{{ job.kind }}</strong><div>{{ job.message }}</div><div v-if="job.cleanup_pending" class="cleanup-pending">Node challenge cleanup pending</div></div><time>{{ date(job.created_at) }}</time><v-chip :color="color(job.status)" size="small">{{ job.status }}</v-chip></div>
    </section>

    <v-dialog :model-value="Boolean(dialog)" max-width="620" scrollable :persistent="busy" @update:model-value="(value) => { if (!value) dialog = ''; }">
      <v-card class="certificate-dialog">
        <v-card-title>{{ dialog === 'provider' ? (editingProvider ? 'Rotate DNS credentials' : 'Add DNS provider') : dialog === 'import' ? 'Import PEM' : dialog === 'account' ? 'Edit ACME account' : dialog === 'revoke' ? 'Revoke certificate version' : 'New certificate' }}</v-card-title>
        <v-card-text>
          <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
          <v-form v-if="dialog === 'provider'" id="provider-form" class="certificate-form" @submit.prevent="saveProvider">
            <v-text-field v-model="providerForm.name" label="Provider name" variant="outlined" hide-details />
            <v-select v-model="providerForm.provider" label="DNS provider type" :items="capabilities.providers.map((item) => item.id)" :disabled="Boolean(editingProvider)" variant="outlined" hide-details />
            <v-text-field v-for="field in providerFields?.fields ?? []" :key="field" v-model="providerForm.credentials[field]" :label="field" :type="field.endsWith('ENDPOINT') ? 'url' : 'password'" autocomplete="off" variant="outlined" hide-details />
          </v-form>
          <v-form v-else-if="dialog === 'certificate'" id="certificate-form" class="certificate-form" @submit.prevent="createCertificate">
            <v-text-field v-model="form.name" label="Certificate name" variant="outlined" hide-details />
            <v-textarea v-model="form.domains" label="DNS names" rows="2" variant="outlined" hide-details="auto" :error-messages="wildcardError ? ['Wildcard names require DNS-01'] : []" />
            <v-text-field v-model="form.email" label="Account email" type="email" variant="outlined" hide-details />
            <v-select v-model="form.challenge_type" label="Validation method" :items="challengeOptions" variant="outlined" hide-details />
            <v-select v-if="form.challenge_type !== 'dns'" v-model="form.validation_server_id" label="Validation host" :items="validationOptions" variant="outlined" hide-details />
            <v-select v-if="form.challenge_type === 'dns'" v-model="form.provider_id" label="DNS provider" :items="providerOptions" variant="outlined" hide-details />
            <v-select v-if="form.challenge_type === 'webroot'" v-model="form.webroot_id" label="Webroot" :items="webrootOptions" variant="outlined" hide-details />
            <v-select v-model="form.directory_url" label="ACME directory" :items="capabilities.directories" variant="outlined" hide-details />
            <details><summary>External account binding</summary><div class="certificate-form mt-3"><v-text-field v-model="form.eab_kid" label="EAB key ID" type="password" variant="outlined" hide-details /><v-text-field v-model="form.eab_hmac_key" label="EAB HMAC key" type="password" variant="outlined" hide-details /></div></details>
            <v-switch v-model="form.auto_renew" label="Auto-renew" color="primary" hide-details density="compact" />
            <v-checkbox v-model="form.accept_terms" label="I accept this CA's terms of service" hide-details density="compact" />
          </v-form>
          <v-form v-else-if="dialog === 'account'" id="account-form" class="certificate-form" @submit.prevent="saveAccount">
            <v-text-field v-model="accountForm.email" label="Account email" type="email" variant="outlined" hide-details />
            <v-select v-model="accountForm.eab_action" label="External account binding" :items="[{ title: 'Keep existing', value: 'keep' }, { title: 'Replace credentials', value: 'replace' }, { title: 'Remove credentials', value: 'remove' }]" :disabled="detail?.account?.state === 'registered'" variant="outlined" hide-details />
            <template v-if="accountForm.eab_action === 'replace'">
              <v-text-field v-model="accountForm.eab_kid" label="EAB key ID" type="password" autocomplete="off" variant="outlined" hide-details />
              <v-text-field v-model="accountForm.eab_hmac_key" label="EAB HMAC key" type="password" autocomplete="off" variant="outlined" hide-details />
            </template>
          </v-form>
          <v-form v-else-if="dialog === 'revoke'" id="revoke-form" class="certificate-form" @submit.prevent="revoke">
            <v-alert type="warning" variant="tonal">Revocation is irreversible. Deployed files remain on nodes.</v-alert>
            <div class="certificate-identity"><strong>{{ detail?.certificate.name }}</strong><div class="version-serial">{{ revokeForm.serial }}</div></div>
            <v-select v-model="revokeForm.directory_url" label="Issuing ACME directory" :items="capabilities.directories" :disabled="Boolean(detail?.certificate.directory_url)" variant="outlined" hide-details />
            <v-select v-model="revokeForm.reason" label="Revocation reason" :items="reasons" variant="outlined" hide-details />
            <v-checkbox v-model="revokeForm.confirm" label="I confirm revocation of this version" hide-details density="compact" />
          </v-form>
          <v-form v-else id="import-form" class="certificate-form" @submit.prevent="importCertificate">
            <v-text-field v-model="importForm.name" label="Certificate name" variant="outlined" hide-details />
            <v-textarea v-model="importForm.cert_pem" label="Certificate PEM" rows="5" variant="outlined" hide-details spellcheck="false" />
            <v-textarea v-model="importForm.key_pem" label="Private key PEM" rows="5" variant="outlined" hide-details spellcheck="false" autocomplete="off" />
          </v-form>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn :disabled="busy" @click="dialog = ''">Cancel</v-btn><v-btn v-if="dialog === 'provider'" type="submit" form="provider-form" color="primary" :loading="busy" :disabled="!providerForm.name || !providerFields?.required.every((key) => providerForm.credentials[key])">Save provider</v-btn><v-btn v-else-if="dialog === 'certificate'" type="submit" form="certificate-form" color="primary" :loading="busy" :disabled="!canCreate">Create certificate</v-btn><v-btn v-else-if="dialog === 'account'" type="submit" form="account-form" color="primary" :loading="busy" :disabled="!canSaveAccount">Update account</v-btn><v-btn v-else-if="dialog === 'revoke'" type="submit" form="revoke-form" color="error" :loading="busy" :disabled="!revokeForm.confirm || !revokeForm.directory_url">Revoke version</v-btn><v-btn v-else type="submit" form="import-form" color="primary" :loading="busy" :disabled="!importForm.name || !importForm.cert_pem || !importForm.key_pem">Import certificate</v-btn></v-card-actions>
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
.certificate-row { grid-template-columns: minmax(180px, 1fr) 140px 190px auto; }
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
.cleanup-pending { color: #8b590e !important; }
.certificate-dialog { border-radius: 8px; }
.certificate-dialog :deep(.v-card-title) { white-space: normal; font-size: 20px; }
.certificate-dialog :deep(.v-label) { white-space: normal; }
.version-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.version-serial { font-family: monospace; overflow-wrap: anywhere; font-size: 12px !important; }
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
