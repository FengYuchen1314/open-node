<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import type {
  AgentChangeSet,
  AgentChangeSetCreateRequest,
  AgentChangeSetStatus,
  AgentChangeSetStepCreateRequest,
  AgentRoutedOutboundChangeSetCreateRequest,
} from "../domain/changes";
import type { AgentCommand, AgentCommandCreateRequest, ServerSummary } from "../domain/inventory";
import { listServers } from "../services/inventory";
import {
  createChangeSet,
  createRoutedOutboundChangeSet,
  dispatchChangeSet,
  listChangeSets,
  rollbackChangeSet,
} from "../services/changes";

const servers = ref<ServerSummary[]>([]);
const changeSets = ref<AgentChangeSet[]>([]);
const selectedChangeSetId = ref("");
const loading = ref(false);
const savingAction = ref<"create" | "routed" | "dispatch" | "rollback" | "">("");
const errorMessage = ref("");
const successMessage = ref("");
const lastCommands = ref<AgentCommand[]>([]);
const warnings = ref<string[]>([]);
const planMode = ref<"routed" | "raw">("routed");

const inboundProtocolOptions = [
  "vless",
  "vmess",
  "trojan",
  "shadowsocks",
  "anytls",
  "snell",
  "mieru",
  "hysteria",
  "socks",
  "http",
];

const routedForm = reactive({
  server_id: "",
  inbound_tag: "",
  inbound_protocol: "vless",
  label: "direct",
  parent_ref: "",
  admin_username: "admin",
  admin_email: "",
  outbound_tag: "",
  marktag: "",
  node_name: "",
  outboundText: sampleRoutedOutboundText(),
  clientText: "",
  sniffingExcludeDomainsText: "",
  add_reality_sniffing_excludes: true,
  rollback_on_failure: true,
  dispatch: false,
  command_timeout_ms: 30000,
});

const form = reactive({
  name: "",
  description: "",
  rollback_on_failure: true,
  dispatch: false,
  rollbackReason: "",
  stepsText: sampleStepsText(""),
});

const changeStatusMeta: Record<AgentChangeSetStatus, { color: string; icon: string }> = {
  planned: { color: "grey", icon: "mdi-clipboard-text-clock-outline" },
  dispatched: { color: "info", icon: "mdi-send-clock-outline" },
  rollback_queued: { color: "warning", icon: "mdi-undo-variant" },
};

const commandStatusMeta = {
  waiting: { color: "secondary", icon: "mdi-link-variant" },
  pending: { color: "warning", icon: "mdi-clock-outline" },
  leased: { color: "info", icon: "mdi-progress-clock" },
  succeeded: { color: "success", icon: "mdi-check-circle-outline" },
  failed: { color: "error", icon: "mdi-alert-circle-outline" },
  skipped: { color: "grey", icon: "mdi-cancel" },
} as const;

const serverOptions = computed(() =>
  servers.value.map((server) => ({ title: server.name, value: server.id })),
);
const selectedChangeSet = computed(
  () => changeSets.value.find((changeSet) => changeSet.id === selectedChangeSetId.value) ?? null,
);
const lastCommandsJson = computed(() =>
  lastCommands.value.length > 0 ? JSON.stringify(lastCommands.value, null, 2) : "",
);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [serverList, changeSetResponse] = await Promise.all([listServers(), listChangeSets()]);
    servers.value = serverList;
    changeSets.value = changeSetResponse.change_sets;
    syncStepSample();
    syncRoutedServer();
    syncSelectedChangeSet();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
  }
}

async function submitRoutedOutbound() {
  const serverId = routedForm.server_id.trim();
  const inboundTag = routedForm.inbound_tag.trim();
  const label = routedForm.label.trim();
  const timeoutMs = Number(routedForm.command_timeout_ms);
  if (!serverId) {
    errorMessage.value = "Server is required.";
    return;
  }
  if (!inboundTag) {
    errorMessage.value = "Inbound tag is required.";
    return;
  }
  if (!label) {
    errorMessage.value = "Label is required.";
    return;
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    errorMessage.value = "Timeout must be greater than zero.";
    return;
  }

  savingAction.value = "routed";
  errorMessage.value = "";
  successMessage.value = "";
  warnings.value = [];
  try {
    const payload: AgentRoutedOutboundChangeSetCreateRequest = {
      server_id: serverId,
      inbound_tag: inboundTag,
      inbound_protocol: routedForm.inbound_protocol.trim() || "vless",
      label,
      outbound: parseJsonObject(routedForm.outboundText, "Outbound JSON"),
      parent_ref: optionalText(routedForm.parent_ref),
      admin_username: routedForm.admin_username.trim() || "admin",
      admin_email: optionalText(routedForm.admin_email),
      outbound_tag: optionalText(routedForm.outbound_tag),
      marktag: optionalText(routedForm.marktag),
      node_name: optionalText(routedForm.node_name),
      client: parseOptionalJsonObject(routedForm.clientText, "Client JSON"),
      sniffing_exclude_domains: splitTextList(routedForm.sniffingExcludeDomainsText),
      add_reality_sniffing_excludes: routedForm.add_reality_sniffing_excludes,
      command_timeout_ms: timeoutMs,
      rollback_on_failure: routedForm.rollback_on_failure,
      dispatch: routedForm.dispatch,
    };
    const response = await createRoutedOutboundChangeSet(payload);
    selectedChangeSetId.value = response.change_set.id;
    lastCommands.value = response.commands;
    warnings.value = response.warnings;
    successMessage.value = response.commands.length
      ? `Created routed outbound plan and dispatched ${response.commands.length} commands.`
      : "Routed outbound change set created.";
    await refresh();
    selectedChangeSetId.value = response.change_set.id;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function submitChangeSet() {
  const name = form.name.trim();
  if (!name) {
    errorMessage.value = "Name is required.";
    return;
  }

  savingAction.value = "create";
  errorMessage.value = "";
  successMessage.value = "";
  warnings.value = [];
  try {
    const payload: AgentChangeSetCreateRequest = {
      name,
      description: form.description.trim(),
      rollback_on_failure: form.rollback_on_failure,
      dispatch: form.dispatch,
      steps: parseStepsText(),
    };
    const response = await createChangeSet(payload);
    selectedChangeSetId.value = response.change_set.id;
    lastCommands.value = response.commands;
    warnings.value = response.warnings;
    successMessage.value = response.commands.length
      ? `Created and dispatched ${response.commands.length} commands.`
      : "Change set created.";
    resetPlanForm();
    await refresh();
    selectedChangeSetId.value = response.change_set.id;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function dispatchSelected() {
  if (!selectedChangeSet.value) {
    errorMessage.value = "Change set is required.";
    return;
  }

  savingAction.value = "dispatch";
  errorMessage.value = "";
  successMessage.value = "";
  warnings.value = [];
  try {
    const response = await dispatchChangeSet(selectedChangeSet.value.id);
    selectedChangeSetId.value = response.change_set.id;
    lastCommands.value = response.commands;
    successMessage.value = response.commands.length
      ? `Dispatched ${response.commands.length} commands.`
      : "No new forward commands.";
    await refresh();
    selectedChangeSetId.value = response.change_set.id;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function rollbackSelected() {
  if (!selectedChangeSet.value) {
    errorMessage.value = "Change set is required.";
    return;
  }

  savingAction.value = "rollback";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const response = await rollbackChangeSet(selectedChangeSet.value.id, {
      reason: form.rollbackReason.trim(),
    });
    selectedChangeSetId.value = response.change_set.id;
    lastCommands.value = response.commands;
    warnings.value = response.warnings;
    successMessage.value = response.commands.length
      ? `Queued ${response.commands.length} rollback commands.`
      : "No new rollback commands.";
    await refresh();
    selectedChangeSetId.value = response.change_set.id;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

function syncStepSample() {
  const firstServerId = servers.value[0]?.id;
  if (firstServerId && form.stepsText.includes('"server_id": ""')) {
    form.stepsText = sampleStepsText(firstServerId);
  }
}

function syncRoutedServer() {
  if (
    routedForm.server_id &&
    servers.value.some((server) => server.id === routedForm.server_id)
  ) {
    return;
  }
  routedForm.server_id = servers.value[0]?.id ?? "";
}

function syncSelectedChangeSet() {
  if (
    selectedChangeSetId.value &&
    changeSets.value.some((changeSet) => changeSet.id === selectedChangeSetId.value)
  ) {
    return;
  }
  selectedChangeSetId.value = changeSets.value[0]?.id ?? "";
}

function resetPlanForm() {
  Object.assign(form, {
    name: "",
    description: "",
    rollback_on_failure: true,
    dispatch: false,
  });
}

function useSampleSteps() {
  form.stepsText = sampleStepsText(servers.value[0]?.id ?? "");
}

function useServerSample(serverId: unknown) {
  form.stepsText = sampleStepsText(typeof serverId === "string" ? serverId : "");
}

function useRoutedOutboundSample() {
  routedForm.outboundText = sampleRoutedOutboundText();
}

function parseStepsText(): AgentChangeSetStepCreateRequest[] {
  const parsed = JSON.parse(form.stepsText || "[]") as unknown;
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("Steps must be a non-empty JSON array.");
  }

  parsed.forEach((step, index) => {
    if (!isRecord(step)) {
      throw new Error(`Step ${index + 1} must be a JSON object.`);
    }
    if (typeof step.server_id !== "string" || !step.server_id.trim()) {
      throw new Error(`Step ${index + 1} server_id is required.`);
    }
    if (!isRecord(step.forward)) {
      throw new Error(`Step ${index + 1} forward command is required.`);
    }
    if (step.rollback !== undefined && step.rollback !== null && !isRecord(step.rollback)) {
      throw new Error(`Step ${index + 1} rollback must be an object or null.`);
    }
  });

  return parsed as AgentChangeSetStepCreateRequest[];
}

function parseJsonObject(text: string, label: string): Record<string, unknown> {
  const source = text.trim();
  if (!source) {
    throw new Error(`${label} is required.`);
  }
  const parsed = JSON.parse(source) as unknown;
  if (!isRecord(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed;
}

function parseOptionalJsonObject(text: string, label: string): Record<string, unknown> | null {
  return text.trim() ? parseJsonObject(text, label) : null;
}

function optionalText(value: string) {
  const normalized = value.trim();
  return normalized ? normalized : null;
}

function splitTextList(value: string) {
  const seen = new Set<string>();
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item) {
        return false;
      }
      const key = item.toLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function sampleStepsText(serverId: string) {
  const steps: AgentChangeSetStepCreateRequest[] = [
    {
      server_id: serverId,
      label: "Read system info",
      forward: {
        method: "GET",
        path: "/api/child/system/info",
      },
      rollback: null,
    },
  ];
  return JSON.stringify(steps, null, 2);
}

function sampleRoutedOutboundText() {
  return JSON.stringify(
    {
      protocol: "freedom",
      settings: {
        domainStrategy: "UseIPv4",
      },
    },
    null,
    2,
  );
}

function commandText(command: AgentCommandCreateRequest) {
  const method = command.method || "GET";
  const target = command.query ? `${command.path}?${command.query}` : command.path;
  return `${method} ${target}`;
}

function commandStatusColor(command?: AgentCommand | null) {
  return command ? commandStatusMeta[command.status].color : "grey";
}

function commandStatusIcon(command?: AgentCommand | null) {
  return command ? commandStatusMeta[command.status].icon : "mdi-dots-horizontal-circle-outline";
}

function commandStatusText(command?: AgentCommand | null) {
  return command ? command.status : "not queued";
}

function serverName(serverId: string) {
  return servers.value.find((server) => server.id === serverId)?.name ?? "Unknown server";
}

function stepBodyText(command: AgentCommandCreateRequest) {
  if (command.body === undefined || command.body === null) {
    return "";
  }
  return typeof command.body === "string" ? command.body : JSON.stringify(command.body);
}

function formatDate(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readableError(error: unknown) {
  return error instanceof Error ? error.message : "Request failed.";
}
</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">Changes</div>
        <h1 class="page-title">Change sets and rollback</h1>
      </div>

      <v-tooltip text="Refresh change sets">
        <template #activator="{ props }">
          <v-btn
            v-bind="props"
            :loading="loading"
            icon="mdi-refresh"
            variant="text"
            @click="refresh"
          />
        </template>
      </v-tooltip>
    </section>

    <v-alert
      v-if="errorMessage"
      class="status-alert"
      density="comfortable"
      type="error"
      variant="tonal"
    >
      {{ errorMessage }}
    </v-alert>
    <v-alert
      v-if="successMessage"
      class="status-alert"
      color="success"
      density="comfortable"
      variant="tonal"
    >
      {{ successMessage }}
    </v-alert>
    <v-alert
      v-for="warning in warnings"
      :key="warning"
      class="status-alert"
      color="warning"
      density="comfortable"
      variant="tonal"
    >
      {{ warning }}
    </v-alert>

    <section class="change-layout">
      <v-sheet class="section-surface change-plan-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Plan</div>
            <div class="section-subtitle">Forward and rollback command sequence</div>
          </div>
          <v-progress-circular
            v-if="loading"
            color="primary"
            indeterminate
            size="24"
            width="3"
          />
        </div>

        <v-tabs v-model="planMode" class="change-plan-tabs" color="primary" density="comfortable">
          <v-tab prepend-icon="mdi-routes" value="routed">Routed outbound</v-tab>
          <v-tab prepend-icon="mdi-code-json" value="raw">Raw steps</v-tab>
        </v-tabs>

        <v-window v-model="planMode" class="change-mode-window">
          <v-window-item value="routed">
            <v-form class="change-form" @submit.prevent="submitRoutedOutbound">
              <v-select
                v-model="routedForm.server_id"
                :disabled="serverOptions.length === 0"
                :items="serverOptions"
                density="comfortable"
                label="Server"
                prepend-inner-icon="mdi-server-network"
                variant="outlined"
              />

              <div class="form-row">
                <v-text-field
                  v-model="routedForm.inbound_tag"
                  density="comfortable"
                  label="Parent inbound tag"
                  prepend-inner-icon="mdi-transit-connection-variant"
                  variant="outlined"
                />
                <v-select
                  v-model="routedForm.inbound_protocol"
                  :items="inboundProtocolOptions"
                  density="comfortable"
                  label="Protocol"
                  prepend-inner-icon="mdi-shield-key-outline"
                  variant="outlined"
                />
              </div>

              <div class="form-row">
                <v-text-field
                  v-model="routedForm.label"
                  density="comfortable"
                  label="Label"
                  prepend-inner-icon="mdi-label-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="routedForm.parent_ref"
                  density="comfortable"
                  label="Parent ref"
                  prepend-inner-icon="mdi-source-branch"
                  variant="outlined"
                />
              </div>

              <v-text-field
                v-model="routedForm.node_name"
                density="comfortable"
                label="Node name"
                prepend-inner-icon="mdi-nodejs"
                variant="outlined"
              />

              <div class="form-row">
                <v-text-field
                  v-model="routedForm.admin_username"
                  density="comfortable"
                  label="Admin username"
                  prepend-inner-icon="mdi-account-key-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="routedForm.admin_email"
                  density="comfortable"
                  label="Admin email"
                  prepend-inner-icon="mdi-email-outline"
                  variant="outlined"
                />
              </div>

              <div class="form-row">
                <v-text-field
                  v-model="routedForm.outbound_tag"
                  density="comfortable"
                  label="Outbound tag"
                  prepend-inner-icon="mdi-tag-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="routedForm.marktag"
                  density="comfortable"
                  label="Route mark"
                  prepend-inner-icon="mdi-sign-direction"
                  variant="outlined"
                />
              </div>

              <v-textarea
                v-model="routedForm.outboundText"
                auto-grow
                class="config-editor"
                density="comfortable"
                label="Outbound JSON"
                prepend-inner-icon="mdi-code-json"
                rows="9"
                variant="outlined"
              />

              <v-textarea
                v-model="routedForm.clientText"
                auto-grow
                class="config-editor"
                density="comfortable"
                label="Client JSON"
                prepend-inner-icon="mdi-account-cog-outline"
                rows="4"
                variant="outlined"
              />

              <v-textarea
                v-model="routedForm.sniffingExcludeDomainsText"
                auto-grow
                density="comfortable"
                label="Sniffing excludes"
                prepend-inner-icon="mdi-web-minus"
                rows="2"
                variant="outlined"
              />

              <div class="change-toggle-row routed-toggle-row">
                <v-switch
                  v-model="routedForm.rollback_on_failure"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Rollback on failure"
                />
                <v-switch
                  v-model="routedForm.dispatch"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Dispatch now"
                />
                <v-switch
                  v-model="routedForm.add_reality_sniffing_excludes"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Reality SNI excludes"
                />
                <v-text-field
                  v-model.number="routedForm.command_timeout_ms"
                  density="comfortable"
                  hide-details
                  label="Timeout ms"
                  min="1"
                  prepend-inner-icon="mdi-timer-outline"
                  type="number"
                  variant="outlined"
                />
              </div>

              <div class="change-action-row">
                <v-btn
                  :loading="savingAction === 'routed'"
                  color="primary"
                  prepend-icon="mdi-plus"
                  type="submit"
                  variant="flat"
                >
                  Create plan
                </v-btn>
                <v-btn prepend-icon="mdi-code-json" variant="tonal" @click="useRoutedOutboundSample">
                  Sample
                </v-btn>
              </div>
            </v-form>
          </v-window-item>

          <v-window-item value="raw">
            <v-form class="change-form" @submit.prevent="submitChangeSet">
              <v-text-field
                v-model="form.name"
                density="comfortable"
                label="Name"
                prepend-inner-icon="mdi-clipboard-text-outline"
                variant="outlined"
              />
              <v-textarea
                v-model="form.description"
                auto-grow
                density="comfortable"
                label="Description"
                prepend-inner-icon="mdi-text-box-outline"
                rows="2"
                variant="outlined"
              />

              <div class="change-toggle-row">
                <v-switch
                  v-model="form.rollback_on_failure"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Rollback on failure"
                />
                <v-switch
                  v-model="form.dispatch"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Dispatch now"
                />
              </div>

              <v-select
                :disabled="serverOptions.length === 0"
                :items="serverOptions"
                density="comfortable"
                label="Sample server"
                prepend-inner-icon="mdi-server-network"
                variant="outlined"
                @update:model-value="useServerSample"
              />

              <v-textarea
                v-model="form.stepsText"
                auto-grow
                class="config-editor"
                density="comfortable"
                label="Steps JSON"
                prepend-inner-icon="mdi-code-json"
                rows="16"
                variant="outlined"
              />

              <div class="change-action-row">
                <v-btn
                  :loading="savingAction === 'create'"
                  color="primary"
                  prepend-icon="mdi-plus"
                  type="submit"
                  variant="flat"
                >
                  Create
                </v-btn>
                <v-btn prepend-icon="mdi-code-json" variant="tonal" @click="useSampleSteps">
                  Sample
                </v-btn>
              </div>
            </v-form>
          </v-window-item>
        </v-window>
      </v-sheet>

      <v-sheet class="section-surface change-runs-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Runs</div>
            <div class="section-subtitle">{{ changeSets.length }} change sets</div>
          </div>
          <v-chip color="success" size="small" variant="tonal">Free edition</v-chip>
        </div>

        <div v-if="changeSets.length === 0" class="empty-state">
          <v-icon icon="mdi-clipboard-text-clock-outline" size="34" />
          <div>No change sets yet.</div>
        </div>

        <template v-else>
          <v-list class="change-run-list" density="compact" lines="two">
            <v-list-item
              v-for="changeSet in changeSets"
              :key="changeSet.id"
              :active="changeSet.id === selectedChangeSetId"
              rounded="lg"
              @click="selectedChangeSetId = changeSet.id"
            >
              <template #prepend>
                <v-icon
                  :color="changeStatusMeta[changeSet.status].color"
                  :icon="changeStatusMeta[changeSet.status].icon"
                />
              </template>
              <v-list-item-title>{{ changeSet.name }}</v-list-item-title>
              <v-list-item-subtitle>
                {{ changeSet.steps.length }} steps - {{ formatDate(changeSet.updated_at) }}
              </v-list-item-subtitle>
              <template #append>
                <v-chip
                  :color="changeStatusMeta[changeSet.status].color"
                  density="comfortable"
                  size="small"
                  variant="tonal"
                >
                  {{ changeSet.status }}
                </v-chip>
              </template>
            </v-list-item>
          </v-list>

          <v-divider class="command-divider" />

          <div v-if="selectedChangeSet" class="change-detail">
            <div class="section-head">
              <div>
                <div class="section-title">{{ selectedChangeSet.name }}</div>
                <div class="section-subtitle">
                  {{ selectedChangeSet.description || selectedChangeSet.id }}
                </div>
              </div>
              <v-chip
                :color="changeStatusMeta[selectedChangeSet.status].color"
                density="comfortable"
                variant="tonal"
              >
                {{ selectedChangeSet.status }}
              </v-chip>
            </div>

            <div class="change-control-row">
              <v-btn
                :loading="savingAction === 'dispatch'"
                prepend-icon="mdi-send"
                variant="tonal"
                @click="dispatchSelected"
              >
                Dispatch
              </v-btn>
              <v-text-field
                v-model="form.rollbackReason"
                density="comfortable"
                hide-details
                label="Rollback reason"
                prepend-inner-icon="mdi-message-text-outline"
                variant="outlined"
              />
              <v-btn
                :loading="savingAction === 'rollback'"
                color="warning"
                prepend-icon="mdi-undo-variant"
                variant="tonal"
                @click="rollbackSelected"
              >
                Rollback
              </v-btn>
            </div>

            <div class="change-step-list">
              <v-sheet
                v-for="step in selectedChangeSet.steps"
                :key="step.id"
                class="change-step-item"
                border
              >
                <div class="change-step-title-row">
                  <div>
                    <div class="compact-title">{{ step.sequence }}. {{ step.label }}</div>
                    <div class="section-subtitle">{{ serverName(step.server_id) }}</div>
                  </div>
                  <v-chip color="grey" density="comfortable" size="small" variant="tonal">
                    {{ step.server_id.slice(0, 8) }}
                  </v-chip>
                </div>

                <div class="change-command-pair">
                  <div>
                    <div class="detail-label">Forward</div>
                    <div class="detail-value">{{ commandText(step.forward) }}</div>
                    <div v-if="stepBodyText(step.forward)" class="change-body-snippet">
                      {{ stepBodyText(step.forward) }}
                    </div>
                    <v-chip
                      :color="commandStatusColor(step.forward_command)"
                      :prepend-icon="commandStatusIcon(step.forward_command)"
                      class="change-command-chip"
                      density="comfortable"
                      size="small"
                      variant="tonal"
                    >
                      {{ commandStatusText(step.forward_command) }}
                    </v-chip>
                  </div>
                  <div>
                    <div class="detail-label">Rollback</div>
                    <div class="detail-value">
                      {{ step.rollback ? commandText(step.rollback) : "none" }}
                    </div>
                    <div
                      v-if="step.rollback && stepBodyText(step.rollback)"
                      class="change-body-snippet"
                    >
                      {{ stepBodyText(step.rollback) }}
                    </div>
                    <v-chip
                      :color="commandStatusColor(step.rollback_command)"
                      :prepend-icon="commandStatusIcon(step.rollback_command)"
                      class="change-command-chip"
                      density="comfortable"
                      size="small"
                      variant="tonal"
                    >
                      {{ commandStatusText(step.rollback_command) }}
                    </v-chip>
                  </div>
                </div>
              </v-sheet>
            </div>

            <div v-if="lastCommandsJson" class="change-command-output">
              <div class="section-title compact-title">Last commands</div>
              <pre class="command-json">{{ lastCommandsJson }}</pre>
            </div>
          </div>
        </template>
      </v-sheet>
    </section>
  </div>
</template>
