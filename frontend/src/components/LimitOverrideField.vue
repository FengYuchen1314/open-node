<script setup lang="ts">
import { computed } from "vue";
import { validLimit } from "../domain/user-limits";

const props = withDefaults(defineProps<{
  modelValue: number | null; label: string; unit?: string; maximum: number; minimum: number;
  integer?: boolean; disabled?: boolean; suggested?: number;
}>(), { unit: "", integer: false, disabled: false, suggested: 1 });
const emit = defineEmits<{ "update:modelValue": [value: number | null] }>();
const mode = computed(() => props.modelValue === null ? "inherit" : props.modelValue === 0 ? "unlimited" : "custom");
const choices = [{ title: "Inherit", value: "inherit" }, { title: "Unlimited", value: "unlimited" }, { title: "Custom", value: "custom" }];
const valid = computed(() => validLimit(props.modelValue, props.maximum, props.minimum, props.integer));
function select(value: string) {
  emit("update:modelValue", value === "inherit" ? null : value === "unlimited" ? 0
    : props.suggested > 0 ? props.suggested : 1);
}
function number(value: string) { emit("update:modelValue", value.trim() ? Number(value) : Number.NaN); }
</script>

<template>
  <div class="limit-field">
    <v-select :model-value="mode" :items="choices" :label="`${label} mode`" variant="outlined" density="compact" hide-details :disabled="disabled" @update:model-value="select" />
    <v-text-field v-if="mode === 'custom'" :model-value="Number.isFinite(modelValue) ? modelValue : ''" :label="unit ? `${label} (${unit})` : label" type="number" :min="minimum" :max="maximum" :step="integer ? 1 : 'any'" variant="outlined" density="compact" :error-messages="valid ? [] : ['Enter a valid positive limit']" :hide-details="valid" :disabled="disabled" @update:model-value="number" />
    <div v-else class="limit-value">{{ mode === 'inherit' ? 'Inherited' : 'Unlimited' }}</div>
  </div>
</template>

<style scoped>
.limit-field { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 12px; min-width: 0; }
.limit-field > * { min-width: 0; }
.limit-value { min-height: 40px; display: flex; align-items: center; font-size: 13px; color: rgb(var(--v-theme-on-surface), .65); }
@media (max-width: 480px) { .limit-field { grid-template-columns: minmax(0, 1fr); gap: 8px; } .limit-value { min-height: 20px; } }
</style>
