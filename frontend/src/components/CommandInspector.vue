<script setup lang="ts">
import type { AgentCommand, AgentCommandStreamFrame } from "../domain/inventory";

defineProps<{
  commands: AgentCommand[];
  streamFramesByCommand: Record<string, AgentCommandStreamFrame[]>;
  emptyText?: string;
}>();

const commandStatusMeta = {
  pending: { color: "warning", icon: "mdi-clock-outline" },
  leased: { color: "info", icon: "mdi-progress-clock" },
  succeeded: { color: "success", icon: "mdi-check-circle-outline" },
  failed: { color: "error", icon: "mdi-alert-circle-outline" },
} as const;

function commandSubtitle(command: AgentCommand, frames: AgentCommandStreamFrame[]) {
  const status = command.result_status ? `status ${command.result_status}` : "waiting";
  const stream = command.stream ? `, ${frames.length} stream frames` : "";
  return `${command.attempts} attempts, ${status}${stream}`;
}

function commandTarget(command: AgentCommand) {
  return command.query ? `${command.path}?${command.query}` : command.path;
}

function formatJson(value: unknown) {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function framesText(frames: AgentCommandStreamFrame[]) {
  return frames.map((frame) => frame.data.trimEnd()).join("\n");
}
</script>

<template>
  <v-expansion-panels
    v-if="commands.length > 0"
    class="command-inspector"
    density="comfortable"
    variant="accordion"
  >
    <v-expansion-panel v-for="command in commands" :key="command.id">
      <v-expansion-panel-title>
        <div class="command-title-row">
          <v-icon
            :color="commandStatusMeta[command.status].color"
            :icon="commandStatusMeta[command.status].icon"
            size="22"
          />
          <div class="command-title-text">
            <div class="command-title">{{ command.method }} {{ commandTarget(command) }}</div>
            <div class="command-subtitle">
              {{ commandSubtitle(command, streamFramesByCommand[command.id] ?? []) }}
            </div>
          </div>
          <v-chip
            :color="commandStatusMeta[command.status].color"
            density="comfortable"
            size="small"
            variant="tonal"
          >
            {{ command.status }}
          </v-chip>
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <div class="command-detail-grid">
          <div>
            <div class="detail-label">Request</div>
            <div class="detail-value">{{ command.request_id }}</div>
          </div>
          <div>
            <div class="detail-label">Timeout</div>
            <div class="detail-value">{{ command.timeout_ms }} ms</div>
          </div>
          <div>
            <div class="detail-label">Created</div>
            <div class="detail-value">{{ command.created_at }}</div>
          </div>
          <div>
            <div class="detail-label">Updated</div>
            <div class="detail-value">{{ command.updated_at }}</div>
          </div>
        </div>

        <pre v-if="command.body !== null && command.body !== undefined" class="command-json">{{
          formatJson(command.body)
        }}</pre>

        <v-alert
          v-if="command.result_error"
          class="command-result-alert"
          density="compact"
          type="error"
          variant="tonal"
        >
          {{ command.result_error }}
        </v-alert>

        <pre
          v-if="command.result_body !== null && command.result_body !== undefined"
          class="command-json"
        >{{ formatJson(command.result_body) }}</pre>

        <pre
          v-if="(streamFramesByCommand[command.id] ?? []).length > 0"
          class="command-stream-block"
        >{{ framesText(streamFramesByCommand[command.id] ?? []) }}</pre>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
  <div v-else class="empty-command">{{ emptyText ?? "No commands queued." }}</div>
</template>
