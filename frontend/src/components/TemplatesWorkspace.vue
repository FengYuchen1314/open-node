<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type {
  SubscriptionTemplate,
  SubscriptionTemplateFormat,
  SubscriptionTemplatePreview,
  SubscriptionTemplateSettings,
  SubscriptionTemplateWrite,
} from "../domain/subscription-templates";
import { subscriberState } from "../services/subscriber-auth";
import {
  createSubscriptionTemplate,
  getSubscriptionTemplate,
  getSubscriptionTemplateSettings,
  getSubscriptionTemplateStarter,
  listSubscriptionTemplates,
  previewSubscriptionTemplate,
  removeSubscriptionTemplate,
  subscriptionTemplateDownloadUrl,
  updateSubscriptionTemplate,
  updateSubscriptionTemplateSettings,
} from "../services/subscription-templates";
import { listProductUsers } from "../services/subscriptions";

const props = withDefaults(defineProps<{ subscriber?: boolean }>(), { subscriber: false });
const templates = ref<SubscriptionTemplate[]>([]);
const settings = ref<SubscriptionTemplateSettings | null>(null);
const canManage = ref(false);
const users = ref<{ username: string; display_name: string; removal_id?: string | null }[]>([]);
const settingsUsername = ref("");
const selectedId = ref<string | null>(null);
const draft = ref<(SubscriptionTemplateWrite & { id: string | null; revision: string }) | null>(null);
const preview = ref<SubscriptionTemplatePreview | null>(null);
const previewUsername = ref("");
const search = ref("");
const formatFilter = ref<SubscriptionTemplateFormat | "all">("all");
const busy = ref(false);
const settingsBusy = ref(false);
const error = ref("");
const success = ref("");
const editorTab = ref<"source" | "preview">("source");
const removeOpen = ref(false);
const confirmName = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
let version = 0;

const formats: Array<{ title: string; value: SubscriptionTemplateFormat }> = [
  { title: "Clash / Mihomo", value: "clash" },
  { title: "Surge", value: "surge" },
];
const filteredTemplates = computed(() => templates.value.filter((item) =>
  (formatFilter.value === "all" || item.format === formatFilter.value)
  && item.name.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()),
));
const userOptions = computed(() => users.value.filter(user => !user.removal_id).map(user => ({
  title: user.display_name ? `${user.display_name} (${user.username})` : user.username,
  value: user.username,
})));
const ownerOptions = computed(() => [{ title: "System", value: null }, ...userOptions.value]);
const targetUsername = computed(() => props.subscriber ? null : settingsUsername.value || null);
const settingsOwner = computed(() => props.subscriber
  ? subscriberState.session?.username ?? null
  : settingsUsername.value || null);
const settingOptions = (format: SubscriptionTemplateFormat) => templates.value
  .filter(item => item.format === format && (!settingsOwner.value || item.owner_username === settingsOwner.value))
  .map(item => ({ title: item.name, value: item.id }));
const current = computed(() => templates.value.find(item => item.id === selectedId.value) ?? null);
const editable = computed(() => canManage.value && (!draft.value?.id || current.value?.editable));
const downloadUrl = computed(() => draft.value?.id ? subscriptionTemplateDownloadUrl(draft.value.id, props.subscriber) : "");

function clearMessages() {
  error.value = "";
  success.value = "";
}

async function refresh(selectId = selectedId.value) {
  const request = ++version;
  busy.value = true;
  clearMessages();
  try {
    const [library, userResponse] = await Promise.all([
      listSubscriptionTemplates(props.subscriber),
      props.subscriber ? Promise.resolve(null) : listProductUsers(),
    ]);
    if (request !== version) return;
    templates.value = library.templates;
    settings.value = library.settings;
    canManage.value = library.can_manage;
    users.value = userResponse?.users ?? [];
    if (selectId && templates.value.some(item => item.id === selectId)) await selectTemplate(selectId, request);
    else if (draft.value?.id) { selectedId.value = null; draft.value = null; preview.value = null; }
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Template library unavailable";
  } finally {
    if (request === version) busy.value = false;
  }
}

async function selectTemplate(id: string, request = ++version) {
  busy.value = true;
  clearMessages();
  try {
    const item = await getSubscriptionTemplate(id, props.subscriber);
    if (request !== version) return;
    selectedId.value = id;
    draft.value = {
      id: item.id,
      revision: item.revision,
      name: item.name,
      format: item.format,
      content: item.content ?? "",
      owner_username: item.owner_username,
      is_public: item.is_public,
    };
    preview.value = null;
    editorTab.value = "source";
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Template unavailable";
  } finally {
    if (request === version) busy.value = false;
  }
}

async function newTemplate(format: SubscriptionTemplateFormat) {
  const request = ++version;
  busy.value = true;
  clearMessages();
  try {
    const starter = await getSubscriptionTemplateStarter(format, props.subscriber);
    if (request !== version) return;
    selectedId.value = null;
    draft.value = {
      id: null,
      revision: "",
      name: format === "clash" ? "template.yaml" : "template.conf",
      format,
      content: starter.content,
      owner_username: props.subscriber ? null : null,
      is_public: false,
    };
    preview.value = null;
    editorTab.value = "source";
  } catch (failure) {
    if (request === version) error.value = failure instanceof Error ? failure.message : "Starter unavailable";
  } finally {
    if (request === version) busy.value = false;
  }
}

async function save() {
  if (!draft.value || !editable.value || busy.value) return;
  busy.value = true;
  clearMessages();
  try {
    const payload: SubscriptionTemplateWrite = {
      name: draft.value.name.trim(),
      format: draft.value.format,
      content: draft.value.content,
      owner_username: props.subscriber ? null : draft.value.owner_username,
      is_public: props.subscriber ? false : draft.value.is_public,
    };
    const saved = draft.value.id
      ? await updateSubscriptionTemplate(draft.value.id, payload, draft.value.revision, props.subscriber)
      : await createSubscriptionTemplate(payload, props.subscriber);
    selectedId.value = saved.id;
    await refresh(saved.id);
    success.value = `${saved.name} saved`;
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Template save failed";
  } finally {
    busy.value = false;
  }
}

async function runPreview() {
  if (!draft.value || busy.value) return;
  busy.value = true;
  clearMessages();
  try {
    preview.value = await previewSubscriptionTemplate(
      draft.value.format,
      draft.value.content,
      props.subscriber ? null : previewUsername.value || null,
      props.subscriber,
    );
    editorTab.value = "preview";
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Template preview failed";
  } finally {
    busy.value = false;
  }
}

function duplicate() {
  if (!draft.value) return;
  const extension = draft.value.format === "clash" ? ".yaml" : ".conf";
  const base = draft.value.name.replace(/\.(ya?ml|conf)$/i, "");
  selectedId.value = null;
  draft.value = { ...draft.value, id: null, revision: "", name: `${base}-copy${extension}`, is_public: false };
  preview.value = null;
  editorTab.value = "source";
}

async function remove() {
  if (!draft.value?.id || !current.value?.editable || confirmName.value !== draft.value.name) return;
  busy.value = true;
  clearMessages();
  try {
    await removeSubscriptionTemplate(draft.value.id, draft.value.revision, confirmName.value, props.subscriber);
    removeOpen.value = false;
    confirmName.value = "";
    selectedId.value = null;
    draft.value = null;
    await refresh(null);
    success.value = "Template removed";
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Template removal failed";
  } finally {
    busy.value = false;
  }
}

async function loadSettings() {
  settingsBusy.value = true;
  clearMessages();
  try {
    settings.value = await getSubscriptionTemplateSettings(targetUsername.value, props.subscriber);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Template settings unavailable";
  } finally {
    settingsBusy.value = false;
  }
}

async function saveSettings() {
  if (!settings.value || settingsBusy.value) return;
  settingsBusy.value = true;
  clearMessages();
  try {
    settings.value = await updateSubscriptionTemplateSettings(settings.value, targetUsername.value, props.subscriber);
    success.value = settingsUsername.value ? `Settings saved for ${settingsUsername.value}` : "Template defaults saved";
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Template settings save failed";
  } finally {
    settingsBusy.value = false;
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { error.value = "Template files are limited to 2 MiB"; return; }
  const format = /\.conf$/i.test(file.name) ? "surge" : /\.ya?ml$/i.test(file.name) ? "clash" : null;
  if (!format) { error.value = "Choose a .yaml, .yml, or .conf file"; return; }
  selectedId.value = null;
  draft.value = {
    id: null,
    revision: "",
    name: file.name,
    format,
    content: await file.text(),
    owner_username: null,
    is_public: false,
  };
  preview.value = null;
  editorTab.value = "source";
}

watch(settingsUsername, () => { if (!props.subscriber) void loadSettings(); });
watch(() => draft.value?.format, (format) => {
  if (!draft.value || !format || draft.value.id) return;
  const base = draft.value.name.replace(/\.(ya?ml|conf)$/i, "") || "template";
  draft.value.name = `${base}${format === "clash" ? ".yaml" : ".conf"}`;
  preview.value = null;
});
onMounted(() => { void refresh(); });
onBeforeUnmount(() => { ++version; });
</script>

<template>
  <div class="templates-workspace">
    <div class="template-toolbar">
      <div><h2>Subscription templates</h2><span>{{ templates.length }} files</span></div>
      <div class="template-actions">
        <v-tooltip text="Upload template"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-upload" aria-label="Upload template" variant="text" :disabled="busy || !canManage" @click="fileInput?.click()" /></template></v-tooltip>
        <input ref="fileInput" type="file" accept=".yaml,.yml,.conf" hidden @change="upload">
        <v-menu><template #activator="{ props: menu }"><v-tooltip text="New template"><template #activator="{ props: tip }"><v-btn v-bind="{ ...menu, ...tip }" icon="mdi-plus" aria-label="New template" variant="text" :disabled="busy || !canManage" /></template></v-tooltip></template><v-list density="compact"><v-list-item v-for="item in formats" :key="item.value" :title="item.title" @click="newTemplate(item.value)" /></v-list></v-menu>
        <v-tooltip text="Refresh templates"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-refresh" aria-label="Refresh templates" variant="text" :loading="busy" @click="refresh()" /></template></v-tooltip>
      </div>
    </div>

    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <v-alert v-if="success" type="success" variant="tonal" class="mb-4">{{ success }}</v-alert>

    <section class="defaults-band" aria-label="Template defaults">
      <div class="defaults-heading">
        <h3>{{ subscriber ? 'My defaults' : settingsUsername ? 'Subscriber permission' : 'System defaults' }}</h3>
        <v-select v-if="!subscriber" v-model="settingsUsername" :items="userOptions" label="Subscriber" clearable variant="outlined" density="compact" hide-details :disabled="settingsBusy" />
      </div>
      <div v-if="settings" class="defaults-controls">
        <v-switch v-if="!subscriber && settingsUsername" v-model="settings.enabled" label="Allow personal templates" color="primary" density="compact" hide-details :disabled="settingsBusy" />
        <v-chip v-else-if="subscriber" :color="settings.enabled ? 'success' : 'warning'" size="small" variant="tonal">{{ settings.enabled ? 'Editing enabled' : 'Read only' }}</v-chip>
        <v-select v-model="settings.clash_template_id" :items="settingOptions('clash')" label="Clash default" clearable variant="outlined" density="compact" hide-details :disabled="settingsBusy || (subscriber && !settings.enabled)" />
        <v-select v-model="settings.surge_template_id" :items="settingOptions('surge')" label="Surge default" clearable variant="outlined" density="compact" hide-details :disabled="settingsBusy || (subscriber && !settings.enabled)" />
        <v-btn color="primary" prepend-icon="mdi-content-save" variant="tonal" :loading="settingsBusy" :disabled="subscriber && !settings.enabled" @click="saveSettings">Save defaults</v-btn>
      </div>
    </section>

    <div class="template-grid">
      <aside class="template-library" aria-label="Template library">
        <div class="library-filters"><v-text-field v-model="search" label="Search" prepend-inner-icon="mdi-magnify" clearable variant="outlined" density="compact" hide-details /><v-btn-toggle v-model="formatFilter" mandatory color="primary" density="compact" variant="outlined" aria-label="Template format"><v-btn value="all">All</v-btn><v-btn value="clash">Clash</v-btn><v-btn value="surge">Surge</v-btn></v-btn-toggle></div>
        <v-list class="template-list" lines="two" density="compact">
          <v-list-item v-for="item in filteredTemplates" :key="item.id" :active="item.id === selectedId" :title="item.name" :subtitle="`${item.format === 'clash' ? 'Clash' : 'Surge'} · ${item.owner_username || 'System'} · ${Math.max(1, Math.ceil(item.size_bytes / 1024))} KiB`" @click="selectTemplate(item.id)"><template #prepend><v-icon :icon="item.format === 'clash' ? 'mdi-alpha-c-box-outline' : 'mdi-alpha-s-box-outline'" /></template><template #append><v-icon v-if="item.is_public" icon="mdi-earth" size="small" /></template></v-list-item>
          <v-list-item v-if="!filteredTemplates.length" title="No templates" prepend-icon="mdi-file-outline" />
        </v-list>
      </aside>

      <section class="template-editor" aria-label="Template editor">
        <div v-if="draft" class="editor-shell">
          <div class="editor-heading"><div><h3>{{ draft.name || 'Untitled template' }}</h3><span>{{ draft.format === 'clash' ? 'Clash / Mihomo YAML' : 'Surge profile' }}</span></div><div class="editor-actions"><v-tooltip text="Duplicate"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-content-duplicate" aria-label="Duplicate template" variant="text" :disabled="busy || !canManage" @click="duplicate" /></template></v-tooltip><v-tooltip text="Download"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-download" aria-label="Download template" variant="text" :href="downloadUrl || undefined" :disabled="!downloadUrl" download /></template></v-tooltip><v-tooltip text="Remove"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-delete-outline" aria-label="Remove template" color="error" variant="text" :disabled="busy || !current?.editable" @click="removeOpen = true" /></template></v-tooltip></div></div>
          <div class="editor-meta"><v-text-field v-model="draft.name" label="Filename" maxlength="160" variant="outlined" density="compact" hide-details :disabled="busy || !editable" /><v-select v-model="draft.format" :items="formats" label="Format" variant="outlined" density="compact" hide-details :disabled="busy || !editable || !!draft.id" /><v-select v-if="!subscriber" v-model="draft.owner_username" :items="ownerOptions" label="Owner" variant="outlined" density="compact" hide-details :disabled="busy || !editable" /><v-switch v-if="!subscriber" v-model="draft.is_public" label="Public" color="primary" density="compact" hide-details :disabled="busy || !editable" /></div>
          <v-tabs v-model="editorTab" color="primary" density="compact"><v-tab value="source" prepend-icon="mdi-code-braces">Source</v-tab><v-tab value="preview" prepend-icon="mdi-eye-outline">Preview</v-tab></v-tabs>
          <v-window v-model="editorTab">
            <v-window-item value="source"><v-textarea v-model="draft.content" class="template-source" aria-label="Template source" variant="outlined" rows="18" no-resize hide-details :readonly="!editable" spellcheck="false" /></v-window-item>
            <v-window-item value="preview"><v-textarea :model-value="preview?.content ?? ''" class="template-source" aria-label="Rendered preview" variant="outlined" rows="18" no-resize hide-details readonly spellcheck="false" /><div v-if="preview" class="preview-summary"><v-chip size="small" color="success" variant="tonal">{{ preview.included_nodes }} included</v-chip><v-chip v-if="preview.excluded_nodes" size="small" color="warning" variant="tonal">{{ preview.excluded_nodes }} excluded</v-chip><span v-for="warning in preview.warnings" :key="warning">{{ warning }}</span></div></v-window-item>
          </v-window>
          <div class="editor-footer"><v-select v-if="!subscriber" v-model="previewUsername" :items="userOptions" label="Preview subscriber" clearable variant="outlined" density="compact" hide-details /><v-btn prepend-icon="mdi-eye-outline" variant="tonal" :loading="busy" :disabled="!canManage" @click="runPreview">Preview</v-btn><v-btn color="primary" prepend-icon="mdi-content-save" :loading="busy" :disabled="!editable || !draft.name.trim() || !draft.content" @click="save">Save</v-btn></div>
        </div>
        <div v-else class="editor-empty"><v-icon icon="mdi-file-document-edit-outline" size="42" /><span>Select or create a template</span></div>
      </section>
    </div>

    <v-dialog v-model="removeOpen" max-width="440" :persistent="busy"><v-card><v-card-title>Remove template</v-card-title><v-card-text><p class="mb-4">{{ draft?.name }}</p><v-text-field v-model="confirmName" label="Confirm filename" variant="outlined" density="compact" hide-details :disabled="busy" /></v-card-text><v-card-actions><v-btn :disabled="busy" @click="removeOpen = false">Cancel</v-btn><v-spacer /><v-btn color="error" prepend-icon="mdi-delete-outline" :loading="busy" :disabled="confirmName !== draft?.name" @click="remove">Remove</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>

<style scoped>
.templates-workspace, .templates-workspace :deep(*) { letter-spacing: 0; }
.template-toolbar, .editor-heading, .defaults-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.template-toolbar { margin-bottom: 20px; }
.template-toolbar h2 { font-size: 22px; }
.template-toolbar span, .editor-heading span { color: #66736f; font-size: 12px; }
.template-actions, .editor-actions, .editor-footer { display: flex; align-items: center; gap: 4px; }
.defaults-band { border-block: 1px solid #dbe5e0; padding: 20px 0; margin-bottom: 24px; }
.defaults-heading h3, .editor-heading h3 { font-size: 15px; overflow-wrap: anywhere; }
.defaults-heading .v-select { width: min(100%, 320px); }
.defaults-controls { display: grid; grid-template-columns: minmax(130px, .7fr) repeat(2, minmax(180px, 1fr)) auto; align-items: center; gap: 12px; margin-top: 16px; }
.template-grid { display: grid; grid-template-columns: minmax(260px, 320px) minmax(0, 1fr); border: 1px solid #dbe5e0; min-height: 620px; }
.template-library { border-right: 1px solid #dbe5e0; min-width: 0; }
.library-filters { display: grid; gap: 12px; padding: 16px; border-bottom: 1px solid #dbe5e0; }
.library-filters .v-btn-toggle { width: 100%; }
.library-filters .v-btn { flex: 1; }
.template-list { max-height: 540px; overflow: auto; }
.template-list :deep(.v-list-item-title), .template-list :deep(.v-list-item-subtitle) { overflow-wrap: anywhere; white-space: normal; }
.template-editor { min-width: 0; }
.editor-shell { display: grid; gap: 16px; padding: 20px; }
.editor-meta { display: grid; grid-template-columns: minmax(180px, 1.3fr) minmax(130px, .7fr) minmax(160px, 1fr) auto; align-items: center; gap: 12px; }
.template-source :deep(textarea) { min-height: 390px; max-height: 390px; overflow: auto; font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; line-height: 1.55; }
.preview-summary { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding-top: 12px; font-size: 12px; overflow-wrap: anywhere; }
.editor-footer { justify-content: flex-end; }
.editor-footer .v-select { max-width: 280px; }
.editor-empty { min-height: 600px; display: grid; place-content: center; justify-items: center; gap: 12px; color: #66736f; }
@media (max-width: 900px) {
  .template-grid { grid-template-columns: minmax(0, 1fr); }
  .template-library { border-right: 0; border-bottom: 1px solid #dbe5e0; }
  .template-list { max-height: 260px; }
  .editor-meta, .defaults-controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .template-toolbar, .editor-heading, .defaults-heading { align-items: flex-start; flex-wrap: wrap; }
  .template-grid { border-inline: 0; }
  .editor-shell { padding: 16px 0; }
  .editor-meta, .defaults-controls { grid-template-columns: minmax(0, 1fr); }
  .editor-footer { align-items: stretch; flex-direction: column; }
  .editor-footer .v-select { max-width: none; }
  .template-source :deep(textarea) { min-height: 330px; max-height: 330px; }
}
</style>
