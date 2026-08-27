<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import type { ServerSummary } from "../domain/inventory";
import type {
  ManagedNode,
  ManagedNodeCreateRequest,
  ManagedNodeType,
  ProductUser,
  ProductUserSubscriptionToken,
  SubscriptionCredential,
  ProductUserCreateRequest,
  ProductUserRole,
  SubscriptionPlan,
  SubscriptionPlanAssignRequest,
  SubscriptionPlanAssignResponse,
  SubscriptionPlanCreateRequest,
  SubscriptionTrafficMode,
} from "../domain/subscriptions";
import { listServers } from "../services/inventory";
import {
  assignSubscriptionPlan,
  createManagedNode,
  createProductUser,
  createProductUserSubscriptionToken,
  createSubscriptionPlan,
  listProductUserCredentials,
  listManagedNodes,
  listProductUsers,
  listSubscriptionPlans,
  resetProductUserSubscriptionToken,
} from "../services/subscriptions";

const servers = ref<ServerSummary[]>([]);
const users = ref<ProductUser[]>([]);
const nodes = ref<ManagedNode[]>([]);
const plans = ref<SubscriptionPlan[]>([]);
const loading = ref(false);
const savingAction = ref<"assign" | "credentials" | "node" | "plan" | "token" | "user" | "">("");
const errorMessage = ref("");
const successMessage = ref("");
const activeTab = ref("users");
const lastAssignment = ref<SubscriptionPlanAssignResponse | null>(null);
const subscriptionToken = ref<ProductUserSubscriptionToken | null>(null);
const subscriptionCredentials = ref<SubscriptionCredential[]>([]);

const userForm = reactive({
  username: "",
  email: "",
  display_name: "",
  role: "user" as ProductUserRole,
  is_active: true,
});
const nodeForm = reactive({
  name: "",
  server_id: "",
  protocol: "vless",
  node_type: "physical" as ManagedNodeType,
  inbound_tag: "",
  routed_outbound_tag: "",
  routed_rule_marktag: "",
  tag: "",
  tagsText: "",
  enabled: true,
  clientTemplateText: '{\n  "id": "client-{username}",\n  "email": "{username}__default"\n}',
  configText: "{}",
});
const planForm = reactive({
  name: "",
  description: "",
  traffic_limit_gb: 128,
  cycle_days: 30,
  is_reset: true,
  reset_day: 1,
  speed_limit_mbps: 0,
  device_limit: 0,
  traffic_mode: "twoway" as SubscriptionTrafficMode,
  node_ids: [] as string[],
});
const assignForm = reactive({
  username: "",
  plan_id: "",
  start_date: "",
  expire_date: "",
  queue_agent_commands: false,
  no_restart: true,
  command_timeout_ms: 60_000,
});

const roleOptions: Array<{ title: string; value: ProductUserRole }> = [
  { title: "User", value: "user" },
  { title: "Admin", value: "admin" },
];
const nodeTypeOptions: Array<{ title: string; value: ManagedNodeType }> = [
  { title: "Physical", value: "physical" },
  { title: "Routed", value: "routed" },
];
const trafficModeOptions: Array<{ title: string; value: SubscriptionTrafficMode }> = [
  { title: "One way", value: "oneway" },
  { title: "Two way", value: "twoway" },
];

const serverOptions = computed(() =>
  servers.value.map((server) => ({ title: server.name, value: server.id })),
);
const userOptions = computed(() =>
  users.value.map((user) => ({ title: user.display_name || user.username, value: user.username })),
);
const nodeOptions = computed(() =>
  nodes.value.map((node) => ({ title: `${node.name} (${node.protocol})`, value: node.id })),
);
const planOptions = computed(() =>
  plans.value.map((plan) => ({ title: plan.name, value: plan.id })),
);
const assignmentJson = computed(() =>
  lastAssignment.value ? JSON.stringify(lastAssignment.value.provisioning_batches, null, 2) : "",
);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [serverList, userResponse, nodeResponse, planResponse] = await Promise.all([
      listServers(),
      listProductUsers(),
      listManagedNodes(),
      listSubscriptionPlans(),
    ]);
    servers.value = serverList;
    users.value = userResponse.users;
    nodes.value = nodeResponse.nodes;
    plans.value = planResponse.plans;
    syncDefaults();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
  }
}

async function submitUser() {
  const username = userForm.username.trim();
  if (!username) {
    errorMessage.value = "Username is required.";
    return;
  }

  savingAction.value = "user";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const payload: ProductUserCreateRequest = {
      username,
      email: blankToNull(userForm.email),
      display_name: blankToNull(userForm.display_name),
      role: userForm.role,
      is_active: userForm.is_active,
    };
    await createProductUser(payload);
    successMessage.value = `Created user ${username}.`;
    resetUserForm();
    await refresh();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function submitNode() {
  if (!nodeForm.server_id) {
    errorMessage.value = "Server is required.";
    return;
  }
  const name = nodeForm.name.trim();
  if (!name) {
    errorMessage.value = "Node name is required.";
    return;
  }

  savingAction.value = "node";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const payload: ManagedNodeCreateRequest = {
      name,
      server_id: nodeForm.server_id,
      protocol: nodeForm.protocol.trim(),
      node_type: nodeForm.node_type,
      inbound_tag: blankToNull(nodeForm.inbound_tag),
      routed_outbound_tag: blankToNull(nodeForm.routed_outbound_tag),
      routed_rule_marktag: blankToNull(nodeForm.routed_rule_marktag),
      tag: blankToNull(nodeForm.tag),
      tags: splitCsv(nodeForm.tagsText),
      enabled: nodeForm.enabled,
      client_template: parseJsonObject(nodeForm.clientTemplateText, "Client template"),
      config: parseJsonObject(nodeForm.configText, "Node config"),
    };
    const response = await createManagedNode(payload);
    successMessage.value = `Created node ${response.node.name}.`;
    resetNodeForm(response.node.server_id);
    await refresh();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function submitPlan() {
  const name = planForm.name.trim();
  if (!name) {
    errorMessage.value = "Plan name is required.";
    return;
  }

  savingAction.value = "plan";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const payload: SubscriptionPlanCreateRequest = {
      name,
      description: planForm.description.trim(),
      traffic_limit_gb: planForm.traffic_limit_gb,
      cycle_days: planForm.cycle_days,
      is_reset: planForm.is_reset,
      reset_day: planForm.is_reset ? planForm.reset_day : 0,
      node_ids: [...planForm.node_ids],
      node_multipliers: Object.fromEntries(planForm.node_ids.map((nodeId) => [nodeId, 1])),
      speed_limit_mbps: planForm.speed_limit_mbps,
      device_limit: planForm.device_limit,
      traffic_mode: planForm.traffic_mode,
    };
    const response = await createSubscriptionPlan(payload);
    successMessage.value = `Created plan ${response.plan.name}.`;
    resetPlanForm();
    await refresh();
    assignForm.plan_id = response.plan.id;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function submitAssignment() {
  if (!assignForm.username || !assignForm.plan_id) {
    errorMessage.value = "User and plan are required.";
    return;
  }

  savingAction.value = "assign";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const payload: SubscriptionPlanAssignRequest = {
      plan_id: assignForm.plan_id,
      start_date: blankToNull(assignForm.start_date),
      expire_date: blankToNull(assignForm.expire_date),
      queue_agent_commands: assignForm.queue_agent_commands,
      no_restart: assignForm.no_restart,
      command_timeout_ms: assignForm.command_timeout_ms,
    };
    const response = await assignSubscriptionPlan(assignForm.username, payload);
    lastAssignment.value = response;
    successMessage.value = response.commands.length
      ? `Assigned ${response.plan.name} and queued ${response.commands.length} command.`
      : `Assigned ${response.plan.name}.`;
    await loadSubscriptionCredentials(false);
    await refresh();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function createToken() {
  if (!assignForm.username) {
    errorMessage.value = "User is required.";
    return;
  }

  savingAction.value = "token";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const response = await createProductUserSubscriptionToken(assignForm.username);
    subscriptionToken.value = response.subscription;
    successMessage.value = `Subscription link ready for ${response.subscription.username}.`;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function resetToken() {
  if (!assignForm.username) {
    errorMessage.value = "User is required.";
    return;
  }

  savingAction.value = "token";
  errorMessage.value = "";
  successMessage.value = "";
  try {
    const response = await resetProductUserSubscriptionToken(assignForm.username);
    subscriptionToken.value = response.subscription;
    successMessage.value = `Subscription link reset for ${response.subscription.username}.`;
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

async function loadSubscriptionCredentials(showSuccess = true) {
  if (!assignForm.username) {
    errorMessage.value = "User is required.";
    return;
  }

  savingAction.value = "credentials";
  errorMessage.value = "";
  if (showSuccess) {
    successMessage.value = "";
  }
  try {
    const response = await listProductUserCredentials(assignForm.username);
    subscriptionCredentials.value = response.credentials;
    if (showSuccess) {
      successMessage.value = `Loaded ${response.credentials.length} credentials.`;
    }
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingAction.value = "";
  }
}

function syncDefaults() {
  if (!nodeForm.server_id && servers.value.length > 0) {
    nodeForm.server_id = servers.value[0].id;
  }
  if (!assignForm.username && users.value.length > 0) {
    assignForm.username = users.value[0].username;
  }
  if (!assignForm.plan_id && plans.value.length > 0) {
    assignForm.plan_id = plans.value[0].id;
  }
}

function resetUserForm() {
  Object.assign(userForm, {
    username: "",
    email: "",
    display_name: "",
    role: "user" as ProductUserRole,
    is_active: true,
  });
}

function resetNodeForm(serverId: string) {
  Object.assign(nodeForm, {
    name: "",
    server_id: serverId,
    protocol: "vless",
    node_type: "physical" as ManagedNodeType,
    inbound_tag: "",
    routed_outbound_tag: "",
    routed_rule_marktag: "",
    tag: "",
    tagsText: "",
    enabled: true,
    clientTemplateText: '{\n  "id": "client-{username}",\n  "email": "{username}__default"\n}',
    configText: "{}",
  });
}

function resetPlanForm() {
  Object.assign(planForm, {
    name: "",
    description: "",
    traffic_limit_gb: 128,
    cycle_days: 30,
    is_reset: true,
    reset_day: 1,
    speed_limit_mbps: 0,
    device_limit: 0,
    traffic_mode: "twoway" as SubscriptionTrafficMode,
    node_ids: [],
  });
}

function parseJsonObject(value: string, fieldName: string): Record<string, unknown> {
  const parsed = JSON.parse(value || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function splitCsv(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function blankToNull(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function readableError(error: unknown) {
  return error instanceof Error ? error.message : "Request failed.";
}

function serverName(serverId: string) {
  return servers.value.find((server) => server.id === serverId)?.name ?? "Unknown server";
}

function formatTraffic(plan: SubscriptionPlan) {
  return `${plan.traffic_limit_gb.toFixed(plan.traffic_limit_gb >= 10 ? 0 : 1)} GB`;
}

function formatDate(value?: string | null) {
  return value ? value.slice(0, 10) : "Not set";
}

function credentialIdentifier(credential: SubscriptionCredential) {
  const source = credential.credential;
  const value = source.id ?? source.password ?? source.auth ?? source.psk ?? source.pass;
  return typeof value === "string" ? value : credential.email;
}
</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">Subscriptions</div>
        <h1 class="page-title">Catalog and user binding</h1>
      </div>

      <v-tooltip text="Refresh subscription catalog">
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

    <section class="subscription-layout">
      <v-sheet class="section-surface subscription-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Workflow</div>
            <div class="section-subtitle">Users, managed nodes, plans, assignment</div>
          </div>
          <v-progress-circular
            v-if="loading"
            color="primary"
            indeterminate
            size="24"
            width="3"
          />
        </div>

        <v-tabs v-model="activeTab" class="subscription-tabs" density="comfortable">
          <v-tab prepend-icon="mdi-account-plus-outline" value="users">Users</v-tab>
          <v-tab prepend-icon="mdi-vector-link" value="nodes">Nodes</v-tab>
          <v-tab prepend-icon="mdi-package-variant-closed" value="plans">Plans</v-tab>
          <v-tab prepend-icon="mdi-account-sync-outline" value="assign">Assign</v-tab>
        </v-tabs>

        <v-window v-model="activeTab" class="subscription-window">
          <v-window-item value="users">
            <v-form class="subscription-form" @submit.prevent="submitUser">
              <div class="form-row">
                <v-text-field
                  v-model="userForm.username"
                  density="comfortable"
                  label="Username"
                  prepend-inner-icon="mdi-account-outline"
                  variant="outlined"
                />
                <v-select
                  v-model="userForm.role"
                  :items="roleOptions"
                  density="comfortable"
                  label="Role"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="userForm.email"
                  density="comfortable"
                  label="Email"
                  prepend-inner-icon="mdi-email-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="userForm.display_name"
                  density="comfortable"
                  label="Display name"
                  prepend-inner-icon="mdi-card-account-details-outline"
                  variant="outlined"
                />
              </div>
              <v-switch
                v-model="userForm.is_active"
                color="primary"
                density="comfortable"
                hide-details
                label="Active"
              />
              <v-btn
                :loading="savingAction === 'user'"
                color="primary"
                prepend-icon="mdi-plus"
                type="submit"
                variant="flat"
              >
                Create user
              </v-btn>
            </v-form>
          </v-window-item>

          <v-window-item value="nodes">
            <v-form class="subscription-form" @submit.prevent="submitNode">
              <div class="form-row">
                <v-text-field
                  v-model="nodeForm.name"
                  density="comfortable"
                  label="Name"
                  prepend-inner-icon="mdi-vector-link"
                  variant="outlined"
                />
                <v-select
                  v-model="nodeForm.server_id"
                  :disabled="serverOptions.length === 0"
                  :items="serverOptions"
                  density="comfortable"
                  label="Server"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="nodeForm.protocol"
                  density="comfortable"
                  label="Protocol"
                  prepend-inner-icon="mdi-protocol"
                  variant="outlined"
                />
                <v-select
                  v-model="nodeForm.node_type"
                  :items="nodeTypeOptions"
                  density="comfortable"
                  label="Type"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="nodeForm.inbound_tag"
                  density="comfortable"
                  label="Inbound tag"
                  prepend-inner-icon="mdi-tag-outline"
                  variant="outlined"
                />
                <v-text-field
                  v-model="nodeForm.routed_outbound_tag"
                  density="comfortable"
                  label="Outbound tag"
                  prepend-inner-icon="mdi-source-branch"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="nodeForm.routed_rule_marktag"
                  density="comfortable"
                  label="Route mark"
                  prepend-inner-icon="mdi-routes"
                  variant="outlined"
                />
                <v-text-field
                  v-model="nodeForm.tagsText"
                  density="comfortable"
                  label="Tags"
                  prepend-inner-icon="mdi-label-multiple-outline"
                  variant="outlined"
                />
              </div>
              <v-textarea
                v-model="nodeForm.clientTemplateText"
                class="config-editor"
                density="comfortable"
                label="Client template"
                rows="5"
                variant="outlined"
              />
              <v-textarea
                v-model="nodeForm.configText"
                class="config-editor"
                density="comfortable"
                label="Node config"
                rows="4"
                variant="outlined"
              />
              <v-switch
                v-model="nodeForm.enabled"
                color="primary"
                density="comfortable"
                hide-details
                label="Enabled"
              />
              <v-btn
                :disabled="serverOptions.length === 0"
                :loading="savingAction === 'node'"
                color="primary"
                prepend-icon="mdi-plus"
                type="submit"
                variant="flat"
              >
                Create node
              </v-btn>
            </v-form>
          </v-window-item>

          <v-window-item value="plans">
            <v-form class="subscription-form" @submit.prevent="submitPlan">
              <div class="form-row">
                <v-text-field
                  v-model="planForm.name"
                  density="comfortable"
                  label="Name"
                  prepend-inner-icon="mdi-package-variant-closed"
                  variant="outlined"
                />
                <v-select
                  v-model="planForm.traffic_mode"
                  :items="trafficModeOptions"
                  density="comfortable"
                  label="Traffic mode"
                  variant="outlined"
                />
              </div>
              <v-textarea
                v-model="planForm.description"
                auto-grow
                density="comfortable"
                label="Description"
                rows="2"
                variant="outlined"
              />
              <div class="form-row">
                <v-text-field
                  v-model.number="planForm.traffic_limit_gb"
                  density="comfortable"
                  label="Traffic GB"
                  min="1"
                  type="number"
                  variant="outlined"
                />
                <v-text-field
                  v-model.number="planForm.cycle_days"
                  density="comfortable"
                  label="Cycle days"
                  min="1"
                  type="number"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model.number="planForm.speed_limit_mbps"
                  density="comfortable"
                  label="Speed Mbps"
                  min="0"
                  type="number"
                  variant="outlined"
                />
                <v-text-field
                  v-model.number="planForm.device_limit"
                  density="comfortable"
                  label="Devices"
                  min="0"
                  type="number"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-switch
                  v-model="planForm.is_reset"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="Reset monthly"
                />
                <v-text-field
                  v-model.number="planForm.reset_day"
                  :disabled="!planForm.is_reset"
                  density="comfortable"
                  label="Reset day"
                  max="31"
                  min="1"
                  type="number"
                  variant="outlined"
                />
              </div>
              <v-select
                v-model="planForm.node_ids"
                :items="nodeOptions"
                chips
                density="comfortable"
                label="Nodes"
                multiple
                variant="outlined"
              />
              <v-btn
                :loading="savingAction === 'plan'"
                color="primary"
                prepend-icon="mdi-plus"
                type="submit"
                variant="flat"
              >
                Create plan
              </v-btn>
            </v-form>
          </v-window-item>

          <v-window-item value="assign">
            <v-form class="subscription-form" @submit.prevent="submitAssignment">
              <div class="form-row">
                <v-select
                  v-model="assignForm.username"
                  :disabled="userOptions.length === 0"
                  :items="userOptions"
                  density="comfortable"
                  label="User"
                  variant="outlined"
                />
                <v-select
                  v-model="assignForm.plan_id"
                  :disabled="planOptions.length === 0"
                  :items="planOptions"
                  density="comfortable"
                  label="Plan"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-text-field
                  v-model="assignForm.start_date"
                  density="comfortable"
                  label="Start date"
                  type="date"
                  variant="outlined"
                />
                <v-text-field
                  v-model="assignForm.expire_date"
                  density="comfortable"
                  label="Expire date"
                  type="date"
                  variant="outlined"
                />
              </div>
              <div class="form-row">
                <v-switch
                  v-model="assignForm.queue_agent_commands"
                  color="warning"
                  density="comfortable"
                  hide-details
                  label="Queue commands"
                />
                <v-switch
                  v-model="assignForm.no_restart"
                  color="primary"
                  density="comfortable"
                  hide-details
                  label="No restart"
                />
              </div>
              <v-text-field
                v-model.number="assignForm.command_timeout_ms"
                density="comfortable"
                label="Command timeout"
                max="300000"
                min="1000"
                type="number"
                variant="outlined"
              />
              <v-btn
                :disabled="userOptions.length === 0 || planOptions.length === 0"
                :loading="savingAction === 'assign'"
                color="secondary"
                prepend-icon="mdi-account-sync-outline"
                type="submit"
                variant="flat"
              >
                Assign plan
              </v-btn>
            </v-form>
          </v-window-item>
        </v-window>
      </v-sheet>

      <v-sheet class="section-surface catalog-surface" border>
        <div class="section-head">
          <div>
            <div class="section-title">Catalog state</div>
            <div class="section-subtitle">{{ users.length }} users, {{ plans.length }} plans</div>
          </div>
          <v-chip color="success" prepend-icon="mdi-lock-open-check-outline" variant="tonal">
            Free
          </v-chip>
        </div>

        <div class="catalog-list">
          <div class="section-title compact-title">Subscription link</div>
          <v-select
            v-model="assignForm.username"
            :disabled="userOptions.length === 0"
            :items="userOptions"
            density="comfortable"
            label="User"
            variant="outlined"
          />
          <div class="subscription-action-row">
            <v-btn
              :disabled="!assignForm.username"
              :loading="savingAction === 'token'"
              color="primary"
              prepend-icon="mdi-link-variant-plus"
              size="small"
              variant="tonal"
              @click="createToken"
            >
              Link
            </v-btn>
            <v-btn
              :disabled="!assignForm.username"
              :loading="savingAction === 'token'"
              color="warning"
              prepend-icon="mdi-link-variant-off"
              size="small"
              variant="tonal"
              @click="resetToken"
            >
              Reset
            </v-btn>
            <v-btn
              :disabled="!assignForm.username"
              :loading="savingAction === 'credentials'"
              color="secondary"
              prepend-icon="mdi-key-chain"
              size="small"
              variant="tonal"
              @click="loadSubscriptionCredentials()"
            >
              Creds
            </v-btn>
          </div>
          <template v-if="subscriptionToken">
            <v-text-field
              :model-value="subscriptionToken.subscription_url"
              density="comfortable"
              label="Subscription URL"
              prepend-inner-icon="mdi-link-variant"
              readonly
              variant="outlined"
            />
            <v-text-field
              :model-value="subscriptionToken.short_url"
              density="comfortable"
              label="Short URL"
              prepend-inner-icon="mdi-link"
              readonly
              variant="outlined"
            />
          </template>
          <div v-if="subscriptionCredentials.length > 0" class="credential-list">
            <div
              v-for="credential in subscriptionCredentials"
              :key="credential.id"
              class="catalog-item"
            >
              <div>
                <div class="server-name">{{ credential.email }}</div>
                <div class="server-subline">{{ credentialIdentifier(credential) }}</div>
              </div>
              <v-chip color="secondary" size="small" variant="tonal">
                {{ credential.protocol }}
              </v-chip>
            </div>
          </div>

          <v-divider />

          <div class="section-title compact-title">Users</div>
          <div v-if="users.length === 0" class="empty-command">No users yet.</div>
          <div v-for="user in users" :key="user.username" class="catalog-item">
            <div>
              <div class="server-name">{{ user.display_name || user.username }}</div>
              <div class="server-subline">{{ user.username }}</div>
            </div>
            <v-chip
              :color="user.current_plan_id ? 'primary' : 'default'"
              size="small"
              variant="tonal"
            >
              {{ formatDate(user.plan_expires_at) }}
            </v-chip>
          </div>

          <v-divider />

          <div class="section-title compact-title">Plans</div>
          <div v-if="plans.length === 0" class="empty-command">No plans yet.</div>
          <div v-for="plan in plans" :key="plan.id" class="catalog-item">
            <div>
              <div class="server-name">{{ plan.name }}</div>
              <div class="server-subline">
                {{ formatTraffic(plan) }} / {{ plan.cycle_days }} days
              </div>
            </div>
            <v-chip color="secondary" size="small" variant="tonal">
              {{ plan.node_ids.length }} nodes
            </v-chip>
          </div>

          <v-divider />

          <div class="section-title compact-title">Nodes</div>
          <div v-if="nodes.length === 0" class="empty-command">No nodes yet.</div>
          <div v-for="node in nodes" :key="node.id" class="catalog-item">
            <div>
              <div class="server-name">{{ node.name }}</div>
              <div class="server-subline">{{ serverName(node.server_id) }}</div>
            </div>
            <v-chip :color="node.enabled ? 'success' : 'warning'" size="small" variant="tonal">
              {{ node.node_type }}
            </v-chip>
          </div>
        </div>

        <div v-if="lastAssignment" class="assignment-summary">
          <v-divider />
          <div class="section-title compact-title">Last assignment</div>
          <div class="catalog-meta">
            {{ lastAssignment.user.username }} -> {{ lastAssignment.plan.name }}
          </div>
          <div class="settings-action-row">
            <v-chip color="primary" prepend-icon="mdi-server-network" variant="tonal">
              {{ lastAssignment.provisioning_batches.length }} batches
            </v-chip>
            <v-chip color="secondary" prepend-icon="mdi-send-outline" variant="tonal">
              {{ lastAssignment.commands.length }} commands
            </v-chip>
          </div>
          <v-alert
            v-if="lastAssignment.warnings.length > 0"
            color="warning"
            density="compact"
            variant="tonal"
          >
            {{ lastAssignment.warnings.join(", ") }}
          </v-alert>
          <pre class="command-json">{{ assignmentJson }}</pre>
        </div>
      </v-sheet>
    </section>
  </div>
</template>
