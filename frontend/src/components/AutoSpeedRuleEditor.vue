<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";
import { newAutoSpeedRule, validAutoSpeedRule, type AutoSpeedRule } from "../domain/auto-speed";

const props = defineProps<{ modelValue: AutoSpeedRule[]; disabled?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: AutoSpeedRule[]]; valid: [value: boolean] }>();
const rows = ref<(AutoSpeedRule & { key: number })[]>([]);
let key = 0;
let published: AutoSpeedRule[] | undefined;
watch(() => props.modelValue, value => {
  if (toRaw(value) !== published) rows.value = value.map(rule => ({ ...rule, key: ++key }));
}, { immediate: true });
const valid = computed(() => rows.value.length <= 100 && rows.value.every(validAutoSpeedRule));
watch(valid, value => emit("valid", value), { immediate: true });
function publish() {
  published = rows.value.map(({ key: _key, ...rule }) => rule);
  emit("update:modelValue", published);
}
function add() {
  if (props.disabled || rows.value.length >= 100) return;
  rows.value.push({ ...newAutoSpeedRule(), key: ++key });
  publish();
}
function remove(index: number) { rows.value.splice(index, 1); publish(); }
function move(index: number, direction: number) {
  const next = index + direction;
  if (next < 0 || next >= rows.value.length) return;
  [rows.value[index], rows.value[next]] = [rows.value[next], rows.value[index]];
  publish();
}
</script>

<template>
  <section class="auto-speed-editor" aria-label="Automatic limits">
    <div class="rule-toolbar">
      <h3>Automatic limits</h3>
      <v-spacer />
      <v-tooltip text="Add automatic rule"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-plus" variant="text" aria-label="Add automatic rule" :disabled="disabled || rows.length >= 100" @click="add" /></template></v-tooltip>
    </div>
    <section v-for="(rule, index) in rows" :key="rule.key" class="automatic-rule" :aria-label="`Automatic rule ${index + 1}`">
      <div class="rule-toolbar">
        <v-btn-toggle v-model="rule.type" mandatory variant="outlined" density="compact" :disabled="disabled" @update:model-value="publish">
          <v-btn value="sustained">Sustained</v-btn><v-btn value="burst">Burst</v-btn>
        </v-btn-toggle>
        <v-spacer />
        <div class="rule-actions">
          <v-tooltip text="Move rule up"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-arrow-up" variant="text" size="small" :aria-label="`Move rule ${index + 1} up`" :disabled="disabled || index === 0" @click="move(index, -1)" /></template></v-tooltip>
          <v-tooltip text="Move rule down"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-arrow-down" variant="text" size="small" :aria-label="`Move rule ${index + 1} down`" :disabled="disabled || index === rows.length - 1" @click="move(index, 1)" /></template></v-tooltip>
          <v-tooltip text="Remove automatic rule"><template #activator="{ props: tip }"><v-btn v-bind="tip" icon="mdi-delete-outline" variant="text" size="small" :aria-label="`Remove automatic rule ${index + 1}`" :disabled="disabled" @click="remove(index)" /></template></v-tooltip>
        </div>
      </div>
      <div class="rule-fields">
        <v-text-field v-model.number="rule.threshold_mbps" label="Trigger Mbps" type="number" min="0.000008" step="any" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
        <v-text-field v-model.number="rule.sustained_seconds" label="Hold seconds" type="number" min="1" max="86400" step="1" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
        <v-text-field v-model.number="rule.limit_mbps" label="Cap Mbps" type="number" min="0.000008" step="any" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
        <v-text-field v-model.number="rule.limit_duration" label="Duration seconds" type="number" min="1" max="86400" step="1" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
        <v-text-field v-if="rule.type === 'burst'" v-model.number="rule.window_seconds" label="Window seconds" type="number" :min="rule.sustained_seconds" max="86400" step="1" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
        <v-text-field v-if="rule.type === 'burst'" v-model.number="rule.burst_count" label="Bursts" type="number" min="1" max="10000" step="1" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="publish" />
      </div>
      <p v-if="!validAutoSpeedRule(rule)" class="text-error">Invalid automatic limit rule</p>
    </section>
    <p v-if="!rows.length" class="rules-empty">No automatic rules</p>
  </section>
</template>

<style scoped>
.auto-speed-editor { min-width: 0; letter-spacing: 0; }
.rule-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.rule-toolbar h3 { font-size: 14px; }
.rule-actions { display: flex; flex-shrink: 0; }
.automatic-rule { border-top: 1px solid rgb(var(--v-theme-on-surface), .12); padding-block: 12px; min-width: 0; }
.rule-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.rules-empty { font-size: 13px; opacity: .7; margin-block: 8px; }
.auto-speed-editor :deep(.v-field__input) { min-width: 0; }
@media (max-width: 600px) { .rule-fields { grid-template-columns: minmax(0, 1fr); } }
</style>
