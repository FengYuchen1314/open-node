<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { useAgentBootstrap } from "../composables/useAgentBootstrap";

const props = defineProps<{ modelValue: boolean; serverId: string; serverName: string }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean]; updated: [] }>();
const { state, command, transport, confirmed, loading, busy, error, canIssue, canRevoke, refresh, issue, revoke }
  = useAgentBootstrap(toRef(props, "modelValue"), toRef(props, "serverId"), () => emit("updated"));
const copyMessage = ref("");
const ticketLabel = computed(() => ({
  not_issued: "No installation ticket", issued: "Ticket ready", claimed: "Ticket claimed",
  expired: "Ticket expired", revoked: "Ticket revoked",
}[state.value?.bootstrap.status ?? "not_issued"]));

function date(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : "Not observed";
}

async function copyCommand() {
  const value = command.value;
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    if (props.modelValue && command.value === value) copyMessage.value = "Copied. Keep your clipboard and shell history private.";
  } catch {
    if (props.modelValue && command.value === value) copyMessage.value = "Clipboard access failed. Select and copy the command manually.";
  }
}

watch(command, () => { copyMessage.value = ""; }, { flush: "sync" });
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="760" scrollable
    @update:model-value="emit('update:modelValue', $event)">
    <v-card class="agent-bootstrap-dialog" title="Install Agent">
      <v-progress-linear v-if="loading || busy" indeterminate color="primary" aria-label="Checking installation" />
      <v-card-text>
        <p class="text-subtitle-1 mb-3">{{ serverName }}</p>
        <p class="mb-4">Generate a command here, run it as root on a new remote host, then wait for the Agent to connect.</p>
        <v-alert v-if="error" type="error" variant="tonal" class="mb-4" data-testid="bootstrap-error">{{ error }}</v-alert>
        <template v-if="state">
          <v-alert v-if="!state.configured" type="warning" variant="tonal" class="mb-4">
            {{ state.reason }}
          </v-alert>
          <dl class="bootstrap-details mb-4">
            <dt>Control plane</dt><dd>{{ state.control_url ?? "HTTPS URL not configured" }}</dd>
            <template v-if="state.release">
              <dt>Pinned release</dt><dd>Agent {{ state.release.agent_version }} (preview) · Xray {{ state.release.xray_version }}</dd>
              <dt>Supported host</dt><dd>{{ state.release.platform }}</dd>
            </template>
          </dl>
          <div class="bootstrap-status mb-3" data-testid="bootstrap-status">
            <v-chip size="small" variant="tonal">{{ ticketLabel }}</v-chip>
            <v-chip size="small" variant="tonal" :color="state.bootstrap.agent_registered ? 'success' : 'warning'">
              {{ state.bootstrap.agent_registered ? 'Agent registered' : 'Agent not yet registered' }}
            </v-chip>
          </div>
          <p v-if="state.bootstrap.agent_registered" class="mb-4">
            {{ state.bootstrap.agent_version }} · Last seen {{ date(state.bootstrap.agent_last_seen_at) }}.
            Registration alone is not proof of a healthy installation; check the installer result and server telemetry.
          </p>
          <v-alert v-else-if="state.bootstrap.claimed_at" type="info" variant="tonal" class="mb-4">
            The host has claimed its credential. This does not mean installation is complete.
            Retry the same command only on that original host after an interruption; do not copy it to a second host.
            A new ticket cannot be issued for this server. Partial installations may require local recovery.
          </v-alert>
          <v-alert v-else-if="state.bootstrap.server_last_heartbeat" type="info" variant="tonal" class="mb-4">
            This server has already reported a heartbeat ({{ date(state.bootstrap.server_last_heartbeat) }}).
            Installation tickets are only available for new servers that have never connected.
            Inspect the existing host before creating a separate server for a new installation.
          </v-alert>
          <template v-if="canIssue">
            <p class="mb-3">
              Installs a non-root managed Agent and a separate official Xray with no public proxy inbound.
              No existing service is adopted. Nginx, WARP, embedded/fork-only protocols and remote root lifecycle are not enabled.
              The host needs Python 3.11+, curl, systemd and outbound HTTPS to this control plane and GitHub.
              Missing Python venv/CA packages may be installed through apt.
            </p>
            <v-select v-model="transport" label="Connection transport" variant="outlined" :disabled="busy"
              :items="[{ title: 'Auto (WebSocket with HTTP fallback)', value: 'auto' },
                { title: 'WebSocket', value: 'websocket' }, { title: 'HTTP polling', value: 'http' }]" />
            <v-checkbox v-model="confirmed" :disabled="busy" hide-details class="mb-3"
              label="I will use a new Debian 12 amd64 host for this server only." />
            <p v-if="state.bootstrap.status === 'issued'" class="text-caption mb-3">
              Generating again invalidates the previous unclaimed command.
            </p>
            <v-btn block class="bootstrap-issue" color="primary" variant="tonal" :disabled="!confirmed || loading || busy" :loading="busy"
              prepend-icon="mdi-console" data-testid="bootstrap-issue" @click="issue">Generate installation command</v-btn>
          </template>
          <div v-if="command" class="mt-4" data-testid="bootstrap-command">
            <v-alert type="warning" variant="tonal" class="mb-3">
              Contains a private 10-minute ticket, not the long-lived Agent credential.
              Expires {{ date(state.bootstrap.expires_at) }}. The first claim permits same-host retries for at most two minutes.
              Closing this dialog clears the displayed command; it does not erase your clipboard or shell history.
            </v-alert>
            <v-textarea :model-value="command" readonly rows="6" no-resize variant="outlined"
              label="Root shell installation command" class="install-command" spellcheck="false" autocomplete="off" />
            <v-btn variant="tonal" prepend-icon="mdi-content-copy" @click="copyCommand">Copy command</v-btn>
            <p v-if="copyMessage" role="status" class="text-caption mt-2">{{ copyMessage }}</p>
          </div>
          <div v-if="canRevoke" class="mt-4">
            <v-btn color="warning" variant="text" :disabled="busy || loading" @click="revoke">Revoke installation ticket</v-btn>
            <p class="text-caption">Revocation stops future claims. It does not revoke an already-delivered Agent credential or disconnect an Agent.</p>
          </div>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn @click="emit('update:modelValue', false)">Close</v-btn>
        <v-spacer />
        <v-btn prepend-icon="mdi-refresh" :disabled="busy || loading" @click="refresh">Refresh status</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.agent-bootstrap-dialog { max-height: 90dvh; }
.agent-bootstrap-dialog :deep(*) { letter-spacing: 0; }
.agent-bootstrap-dialog :deep(.v-card-title) { white-space: normal; overflow-wrap: anywhere; }
.agent-bootstrap-dialog :deep(.v-btn__content) { white-space: normal; }
.bootstrap-issue { height: auto; min-height: 40px; padding-block: 8px; }
.agent-bootstrap-dialog p, .bootstrap-details dd { overflow-wrap: anywhere; }
.bootstrap-details { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; }
.bootstrap-details dt { font-size: 12px; color: rgba(var(--v-theme-on-surface), 0.7); }
.bootstrap-details dd { margin: 0 0 8px; }
.bootstrap-status { display: flex; flex-wrap: wrap; gap: 8px; }
.install-command :deep(textarea) { font-family: monospace; font-size: 12px; overflow-wrap: anywhere; }
</style>
