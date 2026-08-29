<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import type { LegacyMMWXIdentityBundle, LegacyMMWXImportPreview } from "../domain/legacy-mmwx";
import type { SubscriptionPlan } from "../domain/subscriptions";
import { importLegacyMMWXIdentities, previewLegacyMMWXIdentities } from "../services/legacy-mmwx";

const props = defineProps<{ open: boolean; plans: SubscriptionPlan[] }>();
const emit = defineEmits<{ "update:open": [value: boolean]; imported: [] }>();
const input = ref<HTMLInputElement | null>(null);
const bundle = ref<LegacyMMWXIdentityBundle | null>(null);
const filename = ref("");
const replaceExisting = ref(false);
const preview = ref<LegacyMMWXImportPreview | null>(null);
const confirmUserCount = ref<number | null>(null);
const busy = ref<"preview" | "import" | "">("");
const error = ref("");
const success = ref("");
const packageMappings = reactive<Record<number, string>>({});
let version = 0;

const canImport = computed(() => !!bundle.value && !!preview.value?.ready && !busy.value
  && confirmUserCount.value === preview.value.total_users);

function clearFile() {
  bundle.value = null;
  filename.value = "";
  if (input.value) input.value.value = "";
}

function reset() {
  ++version;
  clearFile();
  replaceExisting.value = false;
  preview.value = null;
  confirmUserCount.value = null;
  busy.value = "";
  error.value = "";
  success.value = "";
  Object.keys(packageMappings).forEach((key) => delete packageMappings[Number(key)]);
}

async function selectFile(event: Event) {
  const current = ++version;
  const file = (event.target as HTMLInputElement).files?.[0];
  bundle.value = null;
  filename.value = "";
  preview.value = null;
  confirmUserCount.value = null;
  error.value = "";
  success.value = "";
  if (!file) return;
  if (file.size > 16 * 1024 * 1024) {
    error.value = "Identity file exceeds 16 MB";
    clearFile();
    return;
  }
  try {
    const value: unknown = JSON.parse(await file.text());
    if (current !== version) return;
    if (!value || typeof value !== "object" || (value as { version?: unknown }).version !== 1
      || !Array.isArray((value as { users?: unknown }).users)) {
      throw new Error("Invalid MMWX identity bundle");
    }
    bundle.value = value as LegacyMMWXIdentityBundle;
    for (const item of bundle.value.packages ?? []) packageMappings[item.source_id] = "";
    filename.value = file.name;
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Unable to read identity file";
    clearFile();
  }
}

async function runPreview() {
  if (!bundle.value || busy.value) return;
  const current = ++version;
  busy.value = "preview";
  preview.value = null;
  confirmUserCount.value = null;
  error.value = "";
  success.value = "";
  try {
    const value = await previewLegacyMMWXIdentities(
      bundle.value, replaceExisting.value, undefined, mappedPackages(),
    );
    if (current === version) preview.value = value;
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "Migration preview failed";
  } finally {
    if (current === version) busy.value = "";
  }
}

async function applyImport() {
  if (!canImport.value || !bundle.value || !preview.value || confirmUserCount.value === null) return;
  const current = ++version;
  busy.value = "import";
  error.value = "";
  success.value = "";
  try {
    const result = await importLegacyMMWXIdentities(
      bundle.value,
      replaceExisting.value,
      preview.value,
      confirmUserCount.value,
      undefined,
      mappedPackages(),
    );
    if (current !== version) return;
    preview.value = result.preview;
    success.value = `Imported ${result.preview.total_users} identities`;
    confirmUserCount.value = null;
    clearFile();
    emit("imported");
  } catch (failure) {
    if (current === version) error.value = failure instanceof Error ? failure.message : "MMWX identity import failed";
  } finally {
    if (current === version) busy.value = "";
  }
}

function mappedPackages(): Record<number, string> {
  return Object.fromEntries(
    Object.entries(packageMappings)
      .filter(([, planId]) => Boolean(planId))
      .map(([sourceId, planId]) => [Number(sourceId), planId]),
  ) as Record<number, string>;
}

watch(replaceExisting, () => {
  preview.value = null;
  confirmUserCount.value = null;
  error.value = "";
  success.value = "";
});
watch(() => props.open, (open) => { if (!open) reset(); });
onBeforeUnmount(reset);
</script>

<template>
  <v-dialog :model-value="open" :persistent="Boolean(busy)" max-width="680" scrollable @update:model-value="emit('update:open', $event)">
    <v-card class="legacy-dialog">
      <v-card-title>Import MMWX identities</v-card-title>
      <v-card-text class="legacy-content">
        <v-progress-linear v-if="busy" indeterminate />
        <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
        <v-alert v-if="success" type="success" variant="tonal">{{ success }}</v-alert>
        <input ref="input" class="file-input" type="file" accept="application/json,.json" @change="selectFile">
        <div class="file-row">
          <v-btn prepend-icon="mdi-file-upload-outline" variant="outlined" :disabled="Boolean(busy)" @click="input?.click()">Select JSON</v-btn>
          <span>{{ filename || "No file selected" }}</span>
        </div>
        <v-switch v-model="replaceExisting" color="warning" hide-details label="Replace existing logins and links" :disabled="Boolean(busy)" />
        <v-alert v-if="replaceExisting" type="warning" variant="tonal">Existing subscriber sessions will be revoked.</v-alert>
        <div v-if="bundle?.packages?.length" class="package-mappings">
          <div class="mapping-heading">Package mappings</div>
          <v-select
            v-for="item in bundle.packages"
            :key="item.source_id"
            v-model="packageMappings[item.source_id]"
            :items="plans"
            item-title="name"
            item-value="id"
            :label="item.name"
            clearable
            density="compact"
            variant="outlined"
            hide-details
            :disabled="Boolean(busy)"
          />
        </div>
        <v-btn color="primary" prepend-icon="mdi-magnify" :loading="busy === 'preview'" :disabled="!bundle || Boolean(busy)" @click="runPreview">Preview</v-btn>

        <template v-if="preview">
          <div class="preview-counts">
            <v-chip variant="tonal">Users {{ preview.total_users }}</v-chip>
            <v-chip color="primary" variant="tonal">New {{ preview.new_users }}</v-chip>
            <v-chip color="secondary" variant="tonal">Existing {{ preview.existing_users }}</v-chip>
            <v-chip color="info" variant="tonal">TOTP {{ preview.imported_totp }}</v-chip>
            <v-chip color="primary" variant="tonal">Logins {{ preview.imported_accounts + preview.replaced_accounts }}</v-chip>
            <v-chip color="secondary" variant="tonal">Links {{ preview.imported_tokens + preview.replaced_tokens }}</v-chip>
            <v-chip color="success" variant="tonal">Profiles {{ preview.imported_profiles + preview.replaced_profiles }}</v-chip>
            <v-chip color="default" variant="tonal">Mappings {{ preview.mapped_packages }}</v-chip>
          </div>
          <v-alert v-for="blocker in preview.blockers" :key="blocker" type="error" variant="tonal">{{ blocker }}</v-alert>
          <v-alert v-for="warning in preview.warnings" :key="warning" type="warning" variant="tonal">{{ warning }}</v-alert>
          <v-text-field
            v-model.number="confirmUserCount"
            type="number"
            min="1"
            :max="preview.total_users"
            :label="`Confirm user count (${preview.total_users})`"
            variant="outlined"
            density="compact"
            hide-details
            :disabled="!preview.ready || Boolean(busy)"
          />
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="Boolean(busy)" @click="emit('update:open', false)">{{ success ? "Close" : "Cancel" }}</v-btn>
        <v-spacer />
        <v-btn color="primary" prepend-icon="mdi-database-import-outline" :loading="busy === 'import'" :disabled="!canImport" @click="applyImport">Import</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.legacy-dialog, .legacy-dialog :deep(*) { letter-spacing: 0; }
.legacy-dialog :deep(.v-card-title) { white-space: normal; overflow-wrap: anywhere; }
.legacy-dialog :deep(.v-alert__content), .file-row span { overflow-wrap: anywhere; }
.legacy-content { display: grid; gap: 16px; min-width: 0; }
.file-input { display: none; }
.file-row { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 12px; align-items: center; min-width: 0; font-size: 13px; }
.preview-counts { display: flex; flex-wrap: wrap; gap: 8px; }
.package-mappings { display: grid; gap: 12px; }
.mapping-heading { font-size: 13px; font-weight: 600; color: #42534d; }
.legacy-dialog :deep(.v-card-actions) { flex-wrap: wrap; }
@media (max-width: 420px) { .file-row { grid-template-columns: minmax(0, 1fr); } }
</style>
