<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import type { AgentCommand } from "../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../services/inventory";

type Action = "agent_upgrade" | "agent_rollback" | "agent_uninstall";
type Release = { version: string; sha256: string };
type HostStatus = {
  enabled: boolean;
  installation_status: string;
  current: Release | null;
  previous: Release | null;
  release_base_url: string;
  recovery_required: boolean;
  jobs?: { status: string }[];
};
const props = defineProps<{
  modelValue: boolean;
  serverId: string;
  serverName: string;
  action: Action;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  updated: [];
}>();
const host = ref<HostStatus | null>(null);
const phase = ref<"loading" | "ready" | "working" | "finished" | "error">("loading");
const operation = ref<AgentCommand | null>(null);
const errorMessage = ref("");
const version = ref("");
const checksum = ref("");
const confirmed = ref(false);
const submitting = ref(false);
let generation = 0;
const displayedAction = computed(() => {
  if (!operation.value || !["working", "finished"].includes(phase.value)) return props.action;
  if (operation.value.path.includes("/uninstall")) return "agent_uninstall";
  if (operation.value.path.endsWith("/rollback")) return "agent_rollback";
  return "agent_upgrade";
});
const removed = computed(() => {
  const result = operation.value?.result_body as Partial<HostStatus> | null;
  return result?.installation_status === "removed";
});
const title = computed(() => ({
  agent_upgrade: "Upgrade Agent",
  agent_rollback: "Roll back Agent",
  agent_uninstall: "Uninstall Agent",
}[displayedAction.value]));
const commandLabel = computed(() => ({
  agent_upgrade: "Upgrade",
  agent_rollback: "Roll back",
  agent_uninstall: "Uninstall",
}[props.action]));
const running = computed(() => phase.value === "loading" || phase.value === "working");
const valid = computed(() => confirmed.value && host.value?.enabled
  && host.value.installation_status === "installed"
  && !host.value.recovery_required
  && !host.value.jobs?.some(job => ["queued", "running"].includes(job.status))
  && (props.action !== "agent_rollback" || Boolean(host.value.previous))
  && (props.action !== "agent_upgrade" || (
    /^[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?$/.test(version.value.trim())
    && /^[0-9a-f]{64}$/.test(checksum.value.trim())
  )));

function current(run: number) {
  return generation === run && props.modelValue;
}

async function waitForCommand(command: AgentCommand, serverId: string, run: number) {
  let latest = command;
  while (current(run)) {
    if (["succeeded", "failed", "skipped"].includes(latest.status)) return latest;
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (!current(run)) return null;
    const rows = await listServerCommands(serverId);
    const found = rows.commands.find(row => row.id === command.id);
    if (!found) throw new Error("Agent command is no longer available.");
    latest = found;
    if (phase.value === "working" && current(run)) operation.value = latest;
  }
  return null;
}

async function loadStatus() {
  const run = ++generation;
  const serverId = props.serverId;
  phase.value = "loading";
  errorMessage.value = "";
  host.value = null;
  operation.value = null;
  confirmed.value = false;
  submitting.value = false;
  version.value = "";
  checksum.value = "";
  try {
    const rows = await listServerCommands(serverId);
    if (!current(run)) return;
    const pending = rows.commands.find(command =>
      ["pending", "leased", "waiting"].includes(command.status)
      && /^\/api\/child\/agent\/(upgrade(?:-stream)?|uninstall(?:-stream)?|rollback)$/.test(command.path));
    if (pending) {
      operation.value = pending;
      phase.value = "working";
      await pollOperation(run, serverId, pending);
      return;
    }
    const queued = await queueAgentOperation(serverId, "agent_lifecycle");
    const result = await waitForCommand(queued.command, serverId, run);
    if (!result || !current(run)) return;
    if (result.status !== "succeeded") throw new Error(result.result_error || "Agent status failed.");
    host.value = result.result_body as HostStatus;
    if (!host.value?.enabled) throw new Error("Remote Agent lifecycle is not enabled.");
    phase.value = "ready";
    emit("updated");
  } catch (error) {
    if (current(run)) {
      errorMessage.value = error instanceof Error ? error.message : "Agent status is unavailable.";
      phase.value = "error";
    }
  }
}

async function pollOperation(run: number, serverId: string, command: AgentCommand) {
  try {
    const result = await waitForCommand(command, serverId, run);
    if (!result || !current(run)) return;
    operation.value = result;
    if (host.value && result.result_body && typeof result.result_body === "object") {
      host.value = { ...host.value, ...result.result_body };
    }
    phase.value = "finished";
    if (result.status !== "succeeded") errorMessage.value = result.result_error || "Agent operation failed.";
    emit("updated");
  } catch (error) {
    if (current(run)) {
      errorMessage.value = error instanceof Error ? error.message : "Command status is unavailable.";
    }
  }
}

async function submit() {
  if (!valid.value || submitting.value || phase.value !== "ready") return;
  const run = generation;
  const serverId = props.serverId;
  const action = props.action;
  submitting.value = true;
  errorMessage.value = "";
  try {
    const payload = action === "agent_upgrade"
      ? { version: version.value.trim(), sha256: checksum.value.trim() }
      : { confirm: true as const };
    const queued = await queueAgentOperation(serverId, action, payload);
    emit("updated");
    if (!current(run)) return;
    operation.value = queued.command;
    phase.value = "working";
    await pollOperation(run, serverId, queued.command);
  } catch (error) {
    if (current(run)) errorMessage.value = error instanceof Error ? error.message : "Agent request failed.";
  } finally {
    if (current(run)) submitting.value = false;
  }
}

async function refresh() {
  errorMessage.value = "";
  if (phase.value === "working" && operation.value) {
    await pollOperation(++generation, props.serverId, operation.value);
  } else {
    await loadStatus();
  }
}

watch(() => props.modelValue, open => {
  if (open) void loadStatus();
  else generation += 1;
}, { immediate: true });
onUnmounted(() => { generation += 1; });
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="560"
    @update:model-value="emit('update:modelValue', $event)">
    <v-card :title="title" class="agent-lifecycle-dialog">
      <v-progress-linear v-if="running" indeterminate color="primary"
        :aria-label="phase === 'loading' ? 'Checking Agent' : 'Agent operation in progress'" />
      <v-card-text>
        <p class="mb-4">{{ serverName }}</p>
        <v-alert v-if="errorMessage" type="error" variant="tonal" class="mb-4">{{ errorMessage }}</v-alert>
        <v-alert v-else-if="phase === 'finished' && operation?.status === 'succeeded'"
          type="success" variant="tonal" class="mb-4">Completed</v-alert>
        <dl v-if="host" class="host-versions mb-4">
          <dt>Current version</dt><dd>{{ host.current?.version ?? "Removed" }}</dd>
          <dt>Previous version</dt><dd>{{ host.previous?.version ?? "None" }}</dd>
          <dt>Release source</dt><dd>{{ host.release_base_url }}</dd>
        </dl>
        <v-alert v-if="host?.recovery_required" type="warning" variant="tonal" class="mb-4">
          Host recovery required
        </v-alert>
        <v-form v-if="phase === 'ready'" id="agent-lifecycle-form" @submit.prevent="submit">
          <template v-if="action === 'agent_upgrade'">
            <v-text-field v-model="version" label="Agent version" variant="outlined"
              maxlength="64" :disabled="submitting" />
            <v-textarea v-model="checksum" label="Wheel SHA-256" variant="outlined"
              class="wheel-checksum" rows="2" auto-grow no-resize maxlength="64" :disabled="submitting" />
          </template>
          <v-checkbox v-model="confirmed" hide-details :disabled="submitting"
            :label="action === 'agent_uninstall' ? 'Confirm Agent removal' : 'Confirm Agent restart'" />
        </v-form>
        <v-chip v-if="operation && phase === 'working'" size="small" variant="tonal">
          {{ operation.status === "pending" ? "Queued" : "Running" }}
        </v-chip>
      </v-card-text>
      <v-card-actions>
        <v-btn @click="emit('update:modelValue', false)">Close</v-btn>
        <v-btn v-if="!removed && (errorMessage || phase === 'finished')" icon="mdi-refresh" variant="text"
          title="Refresh Agent status" aria-label="Refresh Agent status" @click="refresh" />
        <v-spacer />
        <v-btn v-if="phase === 'ready'" type="submit" form="agent-lifecycle-form"
          :color="action === 'agent_uninstall' ? 'error' : 'warning'"
          :prepend-icon="action === 'agent_uninstall' ? 'mdi-power-plug-off-outline' : 'mdi-update'"
          :loading="submitting" :disabled="!valid || submitting">{{ commandLabel }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.agent-lifecycle-dialog :deep(*) { letter-spacing: 0; }
.agent-lifecycle-dialog :deep(.v-card-title) { white-space: normal; overflow-wrap: anywhere; }
.agent-lifecycle-dialog p, .host-versions dd { overflow-wrap: anywhere; }
.host-versions { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; }
.host-versions dt { font-size: 12px; color: rgba(var(--v-theme-on-surface), 0.7); }
.host-versions dd { margin: 0 0 8px; font-size: 14px; }
.wheel-checksum :deep(textarea) { font-family: monospace; font-size: 13px; overflow-wrap: anywhere; }
</style>
