<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor.vue";
import { validAutoSpeedRule, type AutoSpeedRule as Rule } from "../domain/auto-speed";

import type { AgentCommand, AgentLimiterOperationRequest, XrayRuntimeInbound } from "../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../services/inventory";

type User = { uid: number; email: string; speed_limit: number; device_limit: number; conn_group?: string; auto_speed_rules?: Rule[] };
type Policy = { inbound_tag: string; node_limit: number; users: User[] | null; auto_speed_rules: Rule[] | null };
type Snapshot = {
  available: boolean; message?: string; revision?: string; inbounds?: Policy[];
  conn_counts?: Record<string, number>; user_speeds?: Record<string, number>;
  connection_rejections?: Record<string, number>;
  automatic_limits?: Record<string, { bytes_per_second: number; until: string }>;
};
type UserRow = { key: number; uid: number; email: string; mbps: number; connections: number; group: string; auto_speed_rules: Rule[] };

const props = defineProps<{ serverId: string; inbounds: XrayRuntimeInbound[] }>();
const emit = defineEmits<{ commands: [serverId: string, commands: AgentCommand[]] }>();
const snapshot = ref<Snapshot | null>(null);
const selectedTag = ref("");
const users = ref<UserRow[]>([]);
const rules = ref<Rule[]>([]);
const nodeMbps = ref(0);
const search = ref("");
const page = ref(1);
const busy = ref(false);
const error = ref("");
const message = ref("");
const removalDialog = ref(false);
let request = 0;
let key = 0;

const inboundOptions = computed(() => [...new Set([
  ...props.inbounds.map((item) => item.tag),
  ...(snapshot.value?.inbounds ?? []).map((item) => item.inbound_tag),
].filter((tag): tag is string => typeof tag === "string" && tag.length > 0))]);
const filtered = computed(() => users.value.filter((user) =>
  (user.email + " " + user.group).toLowerCase().includes(search.value.toLowerCase()),
));
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 8)));
const visibleUsers = computed(() => filtered.value.slice((page.value - 1) * 8, page.value * 8));
const connections = computed(() => Object.values(snapshot.value?.conn_counts ?? {}).reduce((total, value) => total + value, 0));
const hasPolicy = computed(() => snapshot.value?.inbounds?.some((item) => item.inbound_tag === selectedTag.value) ?? false);
function validRate(value: number, unlimited = true) {
  return Number.isFinite(value) && ((unlimited && value === 0) || (value * 125000 >= 1 && value * 125000 <= 2 ** 50));
}
const valid = computed(() => {
  if (!selectedTag.value || !validRate(nodeMbps.value)) return false;
  const emails = new Set<string>();
  for (const user of users.value) {
    if (typeof user.email !== "string" || !user.email.trim() || emails.has(user.email.trim()) ||
        !validRate(user.mbps) ||
        !Number.isInteger(user.connections) || user.connections < 0 || user.connections > 1000000) return false;
    emails.add(user.email.trim());
  }
  return rules.value.length <= 100 && rules.value.every(validAutoSpeedRule);
});

watch(() => props.serverId, () => {
  request += 1;
  snapshot.value = null;
  selectedTag.value = "";
  users.value = [];
  rules.value = [];
  removalDialog.value = false;
  busy.value = false;
  error.value = "";
  message.value = "";
  if (props.serverId) void refresh();
}, { immediate: true });
watch(inboundOptions, (options) => {
  if (!options.includes(selectedTag.value)) selectedTag.value = options[0] ?? "";
});
watch(selectedTag, populate);
watch(search, () => { page.value = 1; });
watch(pages, (count) => { page.value = Math.min(page.value, count); });
onUnmounted(() => { request += 1; });

function populate() {
  const policy = snapshot.value?.inbounds?.find((item) => item.inbound_tag === selectedTag.value);
  const emails = props.inbounds.find((item) => item.tag === selectedTag.value)?.user_emails ?? [];
  nodeMbps.value = (policy?.node_limit ?? 0) / 125000;
  users.value = (policy?.users ?? emails.map((email): User => ({
    uid: 0, email, speed_limit: 0, device_limit: 0, conn_group: "",
  }))).map((user) => ({
    key: ++key, uid: user.uid, email: user.email, mbps: user.speed_limit / 125000,
    connections: user.device_limit, group: user.conn_group ?? "",
    auto_speed_rules: (user.auto_speed_rules ?? []).map(rule => ({ ...rule })),
  }));
  rules.value = (policy?.auto_speed_rules ?? []).map((rule) => ({ ...rule }));
  search.value = "";
  page.value = 1;
}

async function run(kind: "limiter" | "limiter_status", body?: AgentLimiterOperationRequest) {
  const serverId = props.serverId;
  const generation = ++request;
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const queued = await queueAgentOperation(serverId, kind, body);
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (generation !== request || serverId !== props.serverId) return;
      const response = await listServerCommands(serverId);
      if (generation !== request || serverId !== props.serverId) return;
      emit("commands", serverId, response.commands);
      const command = response.commands.find((item) => item.id === queued.command.id);
      if (command?.status === "failed" || command?.status === "skipped") {
        throw new Error(command.result_error || "Limiter command failed.");
      }
      if (command?.status === "succeeded") {
        const value = command.result_body as Snapshot | null;
        if (!value || typeof value.available !== "boolean") throw new Error("Unsupported limiter response.");
        snapshot.value = value;
        if (!value.available) throw new Error(value.message || "Native limiter unavailable.");
        if (!value.revision || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.inbounds)) {
          throw new Error("Invalid limiter state.");
        }
        if (!inboundOptions.value.includes(selectedTag.value)) selectedTag.value = inboundOptions.value[0] ?? "";
        populate();
        if (kind === "limiter") message.value = body?.action === "remove" ? "Limits removed." : "Limits applied.";
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    throw new Error("Limiter command is still pending. Check command history.");
  } catch (failure) {
    if (generation === request) error.value = failure instanceof Error ? failure.message : "Limiter request failed.";
  } finally {
    if (generation === request) busy.value = false;
  }
}

async function refresh() { await run("limiter_status"); }

function addUser() {
  const emails = props.inbounds.find((item) => item.tag === selectedTag.value)?.user_emails ?? [];
  users.value.push({ key: ++key, uid: 0, email: emails.find((email) => !users.value.some((user) => user.email === email)) ?? "",
    mbps: 0, connections: 0, group: "", auto_speed_rules: [] });
  search.value = "";
  page.value = Math.ceil(users.value.length / 8);
}

async function save() {
  if (!valid.value || busy.value || !snapshot.value?.revision) return;
  await run("limiter", {
    inbound_tag: selectedTag.value, expected_revision: snapshot.value.revision,
    node_limit: Math.round(nodeMbps.value * 125000),
    users: users.value.map((user) => ({
      uid: user.uid, email: user.email.trim(), speed_limit: Math.round(user.mbps * 125000),
      device_limit: user.connections, conn_group: user.group.trim(),
      ...(user.auto_speed_rules.length ? { auto_speed_rules: user.auto_speed_rules.map(rule => ({ ...rule })) } : {}),
    })),
    auto_speed_rules: rules.value.map(rule => ({ ...rule })),
  });
}

async function remove() {
  removalDialog.value = false;
  if (busy.value || !snapshot.value?.revision) return;
  await run("limiter", { action: "remove", inbound_tag: selectedTag.value, expected_revision: snapshot.value.revision });
}
</script>

<template>
  <section class="limiter-panel">
    <div class="limiter-toolbar">
      <div class="section-title compact-title">Limits</div>
      <span class="limiter-state">{{ snapshot?.available ? connections + " active connections" : "Unavailable" }}</span>
      <v-spacer />
      <v-tooltip text="Refresh limits"><template #activator="{ props: tip }">
        <v-btn v-bind="tip" icon="mdi-refresh" variant="text" :loading="busy" :disabled="busy || !serverId"
          aria-label="Refresh limits" @click="refresh" />
      </template></v-tooltip>
    </div>
    <v-progress-linear v-if="busy" indeterminate color="primary" />
    <v-alert v-if="error" type="error" variant="tonal" class="my-3">{{ error }}</v-alert>
    <v-alert v-if="message" type="success" variant="tonal" class="my-3">{{ message }}</v-alert>
    <v-form v-if="snapshot?.available" :disabled="busy" @submit.prevent="save">
      <div class="limiter-fields">
        <v-select v-model="selectedTag" :items="inboundOptions" label="Inbound" variant="outlined" density="comfortable" />
        <v-text-field v-model.number="nodeMbps" label="Per-user cap Mbps" type="number" min="0" step="any"
          variant="outlined" density="comfortable" />
      </div>
      <div class="limiter-toolbar">
        <h3 class="section-title compact-title">Users</h3>
        <span>{{ users.length }}</span>
        <v-spacer />
        <v-tooltip text="Add user"><template #activator="{ props: tip }">
          <v-btn v-bind="tip" icon="mdi-plus" variant="text" aria-label="Add limiter user"
            :disabled="busy || users.length >= 1000 || !selectedTag" @click="addUser" />
        </template></v-tooltip>
      </div>
      <v-text-field v-model="search" label="Search users" prepend-inner-icon="mdi-magnify"
        variant="outlined" density="compact" hide-details />
      <div v-for="user in visibleUsers" :key="user.key" class="limiter-user">
        <v-text-field v-model="user.email" label="Email" class="limiter-email" variant="outlined" density="compact" hide-details />
        <v-text-field v-model.number="user.mbps" label="Cap Mbps" type="number" min="0" step="any"
          variant="outlined" density="compact" hide-details />
        <v-text-field v-model.number="user.connections" label="Connections" type="number" min="0" step="1"
          variant="outlined" density="compact" hide-details />
        <v-text-field v-model="user.group" label="Connection group" class="limiter-group"
          variant="outlined" density="compact" hide-details />
        <div class="limiter-user-stats">
          <span>{{ snapshot.conn_counts?.[user.group || user.email] ?? 0 }} active</span>
          <span>{{ ((snapshot.user_speeds?.[user.email] ?? 0) / 125000).toFixed(2) }} Mbps</span>
          <span>{{ snapshot.connection_rejections?.[user.email] ?? 0 }} rejected</span>
          <span v-if="user.auto_speed_rules.length">{{ user.auto_speed_rules.length }} automatic rules</span>
          <span v-if="snapshot.automatic_limits?.[selectedTag + '\0' + user.email]">
            Auto {{ (snapshot.automatic_limits[selectedTag + '\0' + user.email].bytes_per_second / 125000).toFixed(2) }} Mbps
          </span>
          <v-spacer />
          <v-tooltip text="Remove user limit"><template #activator="{ props: tip }">
            <v-btn v-bind="tip" icon="mdi-minus-circle-outline" variant="text" size="small"
              :disabled="busy" :aria-label="'Remove limit for ' + (user.email || 'new user')"
              @click="users = users.filter((item) => item.key !== user.key)" />
          </template></v-tooltip>
        </div>
      </div>
      <p v-if="!visibleUsers.length" class="limiter-empty">No users</p>
      <v-pagination v-if="pages > 1" v-model="page" :length="pages" :total-visible="4" density="compact" />
      <AutoSpeedRuleEditor v-model="rules" :disabled="busy || !selectedTag" />
      <div class="limiter-actions">
        <v-btn type="submit" prepend-icon="mdi-check" color="primary" :disabled="busy || !valid">Save limits</v-btn>
        <v-btn prepend-icon="mdi-delete-outline" variant="text" color="error" :disabled="busy || !hasPolicy"
          @click="removalDialog = true">Remove limits</v-btn>
      </div>
    </v-form>
    <v-dialog v-model="removalDialog" max-width="420">
      <v-card>
        <v-card-title class="text-wrap">Remove limits?</v-card-title>
        <v-card-text class="limiter-tag">{{ selectedTag }}</v-card-text>
        <v-card-actions>
          <v-spacer /><v-btn @click="removalDialog = false">Cancel</v-btn>
          <v-btn color="error" prepend-icon="mdi-delete-outline" @click="remove">Remove</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
