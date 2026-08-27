<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import CommandInspector from "../components/CommandInspector.vue";
import type {
  AgentCommand,
  AgentCommandStreamFrame,
  AgentOperationKind,
  AgentOperationPayload,
  ServerSummary,
} from "../domain/inventory";
import {
  listCommandStreamFrames,
  listServerCommands,
  listServers,
  queueAgentOperation,
} from "../services/inventory";

const servers = ref<ServerSummary[]>([]);
const selectedServerId = ref("");
const commandsByServer = ref<Record<string, AgentCommand[]>>({});
const streamFramesByCommand = ref<Record<string, AgentCommandStreamFrame[]>>({});
const loading = ref(false);
const savingOperation = ref<AgentOperationKind | "">("");
const errorMessage = ref("");
const activeTab = ref("xray");

const xrayConfigForm = reactive({
  path: "",
  configText: '{\n  "inbounds": [],\n  "outbounds": []\n}',
  force: false,
});
const xraySystemForm = reactive({
  metrics_enabled: false,
  metrics_listen: "127.0.0.1:11111",
  stats_enabled: true,
  grpc_enabled: true,
  grpc_port: 46736,
});
const xrayFileForm = reactive({
  file: "config.json",
  content: "{\n}\n",
});
const nginxConfigForm = reactive({
  path: "",
  configText: "events {}\nhttp {}\n",
});
const nginxFileForm = reactive({
  file: "/etc/nginx/conf.d/site.conf",
  path: "/etc/nginx/conf.d/site.conf",
  content: "server {\n    listen 80;\n}\n",
});

const serverOptions = computed(() =>
  servers.value.map((server) => ({ title: server.name, value: server.id })),
);
const selectedCommands = computed(() => commandsByServer.value[selectedServerId.value] ?? []);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    servers.value = await listServers();
    if (!selectedServerId.value && servers.value.length > 0) {
      selectedServerId.value = servers.value[0].id;
    }
    await refreshCommands();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
  }
}

async function refreshCommands() {
  if (servers.value.length === 0) {
    commandsByServer.value = {};
    streamFramesByCommand.value = {};
    return;
  }
  const entries = await Promise.all(
    servers.value.map(async (server) => {
      try {
        const response = await listServerCommands(server.id);
        return [server.id, response.commands] as const;
      } catch {
        return [server.id, []] as const;
      }
    }),
  );
  const nextCommandsByServer: Record<string, AgentCommand[]> = Object.fromEntries(entries);
  commandsByServer.value = nextCommandsByServer;
  await refreshStreamFrames(Object.values(nextCommandsByServer).flat());
}

async function refreshStreamFrames(commands: AgentCommand[]) {
  const streamCommands = commands.filter((command) => command.stream);
  const entries = await Promise.all(
    streamCommands.map(async (command) => {
      try {
        const response = await listCommandStreamFrames(command.server_id, command.id);
        return [command.id, response.frames] as const;
      } catch {
        return [command.id, []] as const;
      }
    }),
  );
  streamFramesByCommand.value = Object.fromEntries(entries);
}

async function queueOperation(kind: AgentOperationKind, payload?: AgentOperationPayload) {
  if (!selectedServerId.value) {
    errorMessage.value = "Target server is required.";
    return false;
  }

  savingOperation.value = kind;
  errorMessage.value = "";
  try {
    await queueAgentOperation(selectedServerId.value, kind, payload);
    await refreshCommands();
    return true;
  } catch (error) {
    errorMessage.value = readableError(error);
    return false;
  } finally {
    savingOperation.value = "";
  }
}

async function testXrayConfig() {
  await queueOperation("xray_test_config", { config: xrayConfigForm.configText });
}

async function writeXrayConfig() {
  await queueOperation("xray_config_write", {
    config: xrayConfigForm.configText,
    path: blankToNull(xrayConfigForm.path),
    force: xrayConfigForm.force,
  });
}

async function writeXraySystemConfig() {
  await queueOperation("xray_system_config_write", {
    ...xraySystemForm,
  });
}

async function readXrayFile() {
  await queueOperation("xray_config_file_read", { file: xrayFileForm.file.trim() });
}

async function writeXrayFile() {
  await queueOperation("xray_config_file_write", {
    file: xrayFileForm.file.trim(),
    content: xrayFileForm.content,
  });
}

async function writeNginxConfig() {
  await queueOperation("nginx_config_write", {
    config: nginxConfigForm.configText,
    path: blankToNull(nginxConfigForm.path),
  });
}

async function readNginxFile() {
  await queueOperation("nginx_config_file_read", { file: nginxFileForm.file.trim() });
}

async function writeNginxFile() {
  await queueOperation("nginx_config_file_write", {
    path: nginxFileForm.path.trim(),
    content: nginxFileForm.content,
  });
}

function useLatestXrayConfig() {
  const body = latestResultRecord("/api/child/xray/config");
  if (!body || typeof body.config !== "string") {
    errorMessage.value = "No completed Xray config result.";
    return;
  }
  xrayConfigForm.configText = body.config;
  if (typeof body.path === "string") {
    xrayConfigForm.path = body.path;
  }
}

function useLatestXraySystemConfig() {
  const body = latestResultRecord("/api/child/xray/system-config");
  const config = asRecord(body?.config);
  if (!config) {
    errorMessage.value = "No completed Xray system config result.";
    return;
  }
  if (typeof config.metrics_enabled === "boolean") {
    xraySystemForm.metrics_enabled = config.metrics_enabled;
  }
  if (typeof config.metrics_listen === "string") {
    xraySystemForm.metrics_listen = config.metrics_listen;
  }
  if (typeof config.stats_enabled === "boolean") {
    xraySystemForm.stats_enabled = config.stats_enabled;
  }
  if (typeof config.grpc_enabled === "boolean") {
    xraySystemForm.grpc_enabled = config.grpc_enabled;
  }
  if (typeof config.grpc_port === "number") {
    xraySystemForm.grpc_port = config.grpc_port;
  }
}

function useLatestXrayFile() {
  const body = latestResultRecordWithContent("/api/child/xray/config-files");
  if (!body || typeof body.content !== "string") {
    errorMessage.value = "No completed Xray file result.";
    return;
  }
  xrayFileForm.content = body.content;
  if (typeof body.path === "string") {
    xrayFileForm.file = body.path.split(/[\\/]/).pop() ?? xrayFileForm.file;
  }
}

function useLatestNginxConfig() {
  const body = latestResultRecord("/api/child/nginx/config");
  if (!body || typeof body.config !== "string") {
    errorMessage.value = "No completed Nginx config result.";
    return;
  }
  nginxConfigForm.configText = body.config;
  if (typeof body.path === "string") {
    nginxConfigForm.path = body.path;
  }
}

function useLatestNginxFile() {
  const body = latestResultRecordWithContent("/api/child/nginx/config-files");
  if (!body || typeof body.content !== "string") {
    errorMessage.value = "No completed Nginx file result.";
    return;
  }
  nginxFileForm.content = body.content;
  if (typeof body.path === "string") {
    nginxFileForm.file = body.path;
    nginxFileForm.path = body.path;
  }
}

function latestResultRecord(path: string) {
  const command = selectedCommands.value.find(
    (item) => item.path === path && item.result_body !== null && item.result_body !== undefined,
  );
  return asRecord(command?.result_body);
}

function latestResultRecordWithContent(path: string) {
  const command = selectedCommands.value.find((item) => {
    const body = asRecord(item.result_body);
    return item.path === path && typeof body?.content === "string";
  });
  return asRecord(command?.result_body);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function blankToNull(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function readableError(error: unknown) {
  return error instanceof Error ? error.message : "Request failed.";
}
</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">Agent config</div>
        <h1 class="page-title">Configuration workspace</h1>
      </div>

      <v-tooltip text="Refresh config commands">
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

    <section class="config-layout">
      <v-sheet class="section-surface config-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Workspace</div>
            <div class="section-subtitle">MMW agent child config operations</div>
          </div>
          <v-select
            v-model="selectedServerId"
            :disabled="serverOptions.length === 0"
            :items="serverOptions"
            class="config-server-select"
            density="compact"
            label="Target server"
            variant="outlined"
          />
        </div>

        <v-tabs v-model="activeTab" class="config-tabs" density="comfortable">
          <v-tab prepend-icon="mdi-alpha-x-circle-outline" value="xray">Xray</v-tab>
          <v-tab prepend-icon="mdi-tune-variant" value="system">System</v-tab>
          <v-tab prepend-icon="mdi-alpha-n-circle-outline" value="nginx">Nginx</v-tab>
          <v-tab prepend-icon="mdi-folder-cog-outline" value="files">Files</v-tab>
        </v-tabs>

        <v-window v-model="activeTab" class="config-window">
          <v-window-item value="xray">
            <v-form class="config-form" @submit.prevent="writeXrayConfig">
              <div class="config-action-row">
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'xray_config_read'"
                  color="secondary"
                  prepend-icon="mdi-file-search-outline"
                  size="small"
                  variant="tonal"
                  @click="queueOperation('xray_config_read')"
                >
                  Read
                </v-btn>
                <v-btn
                  color="secondary"
                  prepend-icon="mdi-tray-arrow-down"
                  size="small"
                  variant="tonal"
                  @click="useLatestXrayConfig"
                >
                  Use latest
                </v-btn>
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'xray_test_config'"
                  color="info"
                  prepend-icon="mdi-check-decagram-outline"
                  size="small"
                  variant="tonal"
                  @click="testXrayConfig"
                >
                  Test
                </v-btn>
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'xray_config_write'"
                  color="primary"
                  prepend-icon="mdi-content-save-outline"
                  size="small"
                  type="submit"
                  variant="flat"
                >
                  Write
                </v-btn>
              </div>
              <v-text-field
                v-model="xrayConfigForm.path"
                density="comfortable"
                label="Path"
                prepend-inner-icon="mdi-file-cog-outline"
                variant="outlined"
              />
              <v-switch
                v-model="xrayConfigForm.force"
                color="warning"
                density="comfortable"
                hide-details
                label="Force"
              />
              <v-textarea
                v-model="xrayConfigForm.configText"
                class="config-editor"
                density="comfortable"
                label="Xray config"
                rows="16"
                variant="outlined"
              />
            </v-form>
          </v-window-item>

          <v-window-item value="system">
            <v-form class="config-form" @submit.prevent="writeXraySystemConfig">
              <div class="config-action-row">
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'xray_system_config_read'"
                  color="secondary"
                  prepend-icon="mdi-file-search-outline"
                  size="small"
                  variant="tonal"
                  @click="queueOperation('xray_system_config_read')"
                >
                  Read
                </v-btn>
                <v-btn
                  color="secondary"
                  prepend-icon="mdi-tray-arrow-down"
                  size="small"
                  variant="tonal"
                  @click="useLatestXraySystemConfig"
                >
                  Use latest
                </v-btn>
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'xray_system_config_write'"
                  color="primary"
                  prepend-icon="mdi-content-save-outline"
                  size="small"
                  type="submit"
                  variant="flat"
                >
                  Write
                </v-btn>
              </div>
              <div class="system-config-grid">
                <v-switch
                  v-model="xraySystemForm.metrics_enabled"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Metrics"
                />
                <v-switch
                  v-model="xraySystemForm.stats_enabled"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Stats"
                />
                <v-switch
                  v-model="xraySystemForm.grpc_enabled"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="gRPC"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="xraySystemForm.metrics_listen"
                  density="comfortable"
                  label="Metrics listen"
                  variant="outlined"
                />
                <v-text-field
                  v-model.number="xraySystemForm.grpc_port"
                  density="comfortable"
                  label="gRPC port"
                  min="1"
                  max="65535"
                  type="number"
                  variant="outlined"
                />
              </div>
            </v-form>
          </v-window-item>

          <v-window-item value="nginx">
            <v-form class="config-form" @submit.prevent="writeNginxConfig">
              <div class="config-action-row">
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'nginx_config_read'"
                  color="secondary"
                  prepend-icon="mdi-file-search-outline"
                  size="small"
                  variant="tonal"
                  @click="queueOperation('nginx_config_read')"
                >
                  Read
                </v-btn>
                <v-btn
                  color="secondary"
                  prepend-icon="mdi-tray-arrow-down"
                  size="small"
                  variant="tonal"
                  @click="useLatestNginxConfig"
                >
                  Use latest
                </v-btn>
                <v-btn
                  :disabled="serverOptions.length === 0"
                  :loading="savingOperation === 'nginx_config_write'"
                  color="primary"
                  prepend-icon="mdi-content-save-outline"
                  size="small"
                  type="submit"
                  variant="flat"
                >
                  Write
                </v-btn>
              </div>
              <v-text-field
                v-model="nginxConfigForm.path"
                density="comfortable"
                label="Path"
                prepend-inner-icon="mdi-file-cog-outline"
                variant="outlined"
              />
              <v-textarea
                v-model="nginxConfigForm.configText"
                class="config-editor"
                density="comfortable"
                label="Nginx config"
                rows="16"
                variant="outlined"
              />
            </v-form>
          </v-window-item>

          <v-window-item value="files">
            <div class="config-file-grid">
              <v-form class="config-form" @submit.prevent="writeXrayFile">
                <div class="section-title compact-title">Xray file</div>
                <div class="config-action-row">
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'xray_config_files_list'"
                    color="secondary"
                    prepend-icon="mdi-folder-search-outline"
                    size="small"
                    variant="tonal"
                    @click="queueOperation('xray_config_files_list')"
                  >
                    List
                  </v-btn>
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'xray_config_file_read'"
                    color="secondary"
                    prepend-icon="mdi-file-search-outline"
                    size="small"
                    variant="tonal"
                    @click="readXrayFile"
                  >
                    Read
                  </v-btn>
                  <v-btn
                    color="secondary"
                    prepend-icon="mdi-tray-arrow-down"
                    size="small"
                    variant="tonal"
                    @click="useLatestXrayFile"
                  >
                    Use latest
                  </v-btn>
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'xray_config_file_write'"
                    color="primary"
                    prepend-icon="mdi-content-save-outline"
                    size="small"
                    type="submit"
                    variant="flat"
                  >
                    Write
                  </v-btn>
                </div>
                <v-text-field
                  v-model="xrayFileForm.file"
                  density="comfortable"
                  label="File"
                  prepend-inner-icon="mdi-file-code-outline"
                  variant="outlined"
                />
                <v-textarea
                  v-model="xrayFileForm.content"
                  class="config-editor"
                  density="comfortable"
                  label="Content"
                  rows="12"
                  variant="outlined"
                />
              </v-form>

              <v-form class="config-form" @submit.prevent="writeNginxFile">
                <div class="section-title compact-title">Nginx file</div>
                <div class="config-action-row">
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'nginx_config_files_list'"
                    color="secondary"
                    prepend-icon="mdi-folder-search-outline"
                    size="small"
                    variant="tonal"
                    @click="queueOperation('nginx_config_files_list')"
                  >
                    List
                  </v-btn>
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'nginx_config_file_read'"
                    color="secondary"
                    prepend-icon="mdi-file-search-outline"
                    size="small"
                    variant="tonal"
                    @click="readNginxFile"
                  >
                    Read
                  </v-btn>
                  <v-btn
                    color="secondary"
                    prepend-icon="mdi-tray-arrow-down"
                    size="small"
                    variant="tonal"
                    @click="useLatestNginxFile"
                  >
                    Use latest
                  </v-btn>
                  <v-btn
                    :disabled="serverOptions.length === 0"
                    :loading="savingOperation === 'nginx_config_file_write'"
                    color="primary"
                    prepend-icon="mdi-content-save-outline"
                    size="small"
                    type="submit"
                    variant="flat"
                  >
                    Write
                  </v-btn>
                </div>
                <v-text-field
                  v-model="nginxFileForm.file"
                  density="comfortable"
                  label="Read path"
                  prepend-inner-icon="mdi-file-search-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="nginxFileForm.path"
                  density="comfortable"
                  label="Write path"
                  prepend-inner-icon="mdi-file-cog-outline"
                  variant="outlined"
                />
                <v-textarea
                  v-model="nginxFileForm.content"
                  class="config-editor"
                  density="comfortable"
                  label="Content"
                  rows="12"
                  variant="outlined"
                />
              </v-form>
            </div>
          </v-window-item>
        </v-window>
      </v-sheet>

      <v-sheet class="section-surface config-results-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Command results</div>
            <div class="section-subtitle">Selected server history</div>
          </div>
          <v-btn
            :loading="loading"
            icon="mdi-refresh"
            size="small"
            variant="text"
            @click="refreshCommands"
          />
        </div>
        <CommandInspector
          :commands="selectedCommands"
          :stream-frames-by-command="streamFramesByCommand"
          empty-text="No config commands yet."
        />
      </v-sheet>
    </section>
  </div>
</template>
