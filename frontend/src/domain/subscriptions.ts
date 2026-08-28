import type { AgentCommand } from "./inventory";

export type ProductUserRole = "admin" | "user";
export type ManagedNodeType = "physical" | "routed";
export type SubscriptionTrafficMode = "oneway" | "twoway";

export interface SubscriptionAccessResponse {
  username: string;
  managed: boolean;
  servers: Array<{
    server_id: string;
    server_name: string;
    status: "pending" | "applied" | "failed";
    command_id: string | null;
    error: string | null;
    updated_at: string;
    entries: Array<{ inbound_tag: string; email: string; enabled: boolean; reason: string }>;
  }>;
  license_required: false;
}
export type SubscriptionClientFormat = "clash" | "sing-box" | "xray" | "uri-list" | "base64";

export interface SubscriptionFormatPreview {
  username: string;
  client_format: SubscriptionClientFormat;
  nodes: Array<{ node_id: string; name: string; protocol: string; available: boolean; reason: string | null }>;
  warnings: string[];
  license_required: false;
}

export interface ProductUserCreateRequest {
  username: string;
  email?: string | null;
  display_name?: string | null;
  role?: ProductUserRole;
  is_active?: boolean;
}

export interface ProductUser {
  username: string;
  email?: string | null;
  display_name: string;
  role: ProductUserRole;
  is_active: boolean;
  current_plan_id?: string | null;
  plan_started_at?: string | null;
  plan_expires_at?: string | null;
  is_reset: boolean;
  reset_day: number;
  last_traffic_reset_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProductUserSubscriptionToken {
  username: string;
  token: string;
  short_code: string;
  subscription_url: string;
  short_url: string;
  created_at: string;
  updated_at: string;
}

export interface ManagedNodeCreateRequest {
  name: string;
  server_id: string;
  protocol: string;
  node_type?: ManagedNodeType;
  inbound_tag?: string | null;
  routed_outbound_tag?: string | null;
  routed_rule_marktag?: string | null;
  tag?: string | null;
  tags?: string[];
  enabled?: boolean;
  client_template?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

export interface ManagedNode extends ManagedNodeCreateRequest {
  id: string;
  node_type: ManagedNodeType;
  tags: string[];
  enabled: boolean;
  client_template: Record<string, unknown>;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionPlanCreateRequest {
  name: string;
  description?: string;
  traffic_limit_gb: number;
  cycle_days?: number;
  is_reset?: boolean;
  reset_day?: number;
  node_ids?: string[];
  node_multipliers?: Record<string, number>;
  node_speed_limits?: Record<string, number>;
  node_device_limits?: Record<string, number>;
  speed_limit_mbps?: number;
  device_limit?: number;
  traffic_mode?: SubscriptionTrafficMode;
}

export interface SubscriptionPlan extends SubscriptionPlanCreateRequest {
  id: string;
  description: string;
  cycle_days: number;
  is_reset: boolean;
  reset_day: number;
  node_ids: string[];
  node_multipliers: Record<string, number>;
  node_speed_limits: Record<string, number>;
  node_device_limits: Record<string, number>;
  speed_limit_mbps: number;
  device_limit: number;
  traffic_mode: SubscriptionTrafficMode;
  traffic_limit_bytes: number;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionPlanAssignRequest {
  plan_id: string;
  start_date?: string | null;
  expire_date?: string | null;
  is_reset?: boolean | null;
  reset_day?: number | null;
  queue_agent_commands?: boolean;
  no_restart?: boolean;
  command_timeout_ms?: number;
}

export interface SubscriptionProvisionBatch {
  server_id: string;
  server_name: string;
  body: Record<string, unknown>;
}

export interface SubscriptionCredential {
  id: string;
  username: string;
  node_id: string;
  server_id: string;
  inbound_tag?: string | null;
  protocol: string;
  email: string;
  credential: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionTrafficEntry {
  server_name?: string | null;
  archived?: boolean;
  username: string;
  server_id: string;
  email: string;
  upload: number;
  download: number;
  total: number;
  last_reported_at?: string | null;
  updated_at: string;
}

export interface ProductUsersResponse {
  users: ProductUser[];
  license_required: false;
}

export interface ProductUserResponse {
  user: ProductUser;
  license_required: false;
}

export interface ProductUserSubscriptionTokenResponse {
  subscription: ProductUserSubscriptionToken;
  license_required: false;
}

export interface ProductUserCredentialsResponse {
  username: string;
  credentials: SubscriptionCredential[];
  license_required: false;
}

export interface ProductUserTrafficResponse {
  username: string;
  upload: number;
  download: number;
  total: number;
  entries: SubscriptionTrafficEntry[];
  license_required: false;
}

export interface SubscriptionQuotaStatus {
  username: string;
  is_active: boolean;
  has_plan: boolean;
  available: boolean;
  expired: boolean;
  over_quota: boolean;
  reset_enabled: boolean;
  reset_due: boolean;
  upload: number;
  download: number;
  charged_usage_bytes: number;
  traffic_limit_bytes: number;
  remaining_bytes: number;
  percent_used: number;
  reset_day: number;
  plan_id?: string | null;
  plan_name?: string | null;
  traffic_mode?: SubscriptionTrafficMode | null;
  plan_started_at?: string | null;
  plan_expires_at?: string | null;
  reset_due_at?: string | null;
  next_reset_at?: string | null;
  last_traffic_reset_at?: string | null;
}

export interface SubscriptionQuotaStatusResponse {
  quota: SubscriptionQuotaStatus;
  license_required: false;
}

export interface SubscriptionDueTrafficResetRequest {
  now?: string | null;
  dry_run?: boolean;
}

export interface SubscriptionDueTrafficResetSummary {
  checked_users: number;
  reset_users: number;
  skipped_users: number;
  usernames: string[];
  dry_run: boolean;
  warnings: string[];
}

export interface SubscriptionDueTrafficResetResponse {
  summary: SubscriptionDueTrafficResetSummary;
  license_required: false;
}

export interface SubscriptionTemplatePreset {
  id: string;
  name: string;
  description: string;
  protocol: string;
  node_type: ManagedNodeType;
  inbound_tag?: string | null;
  routed_outbound_tag?: string | null;
  routed_rule_marktag?: string | null;
  tag?: string | null;
  tags: string[];
  client_template: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface SubscriptionTemplatePresetsResponse {
  presets: SubscriptionTemplatePreset[];
  license_required: false;
}

export interface SubscriptionTemplatePresetApplyRequest {
  server_id: string;
  name?: string | null;
  host?: string | null;
  port?: number | null;
  inbound_tag?: string | null;
  routed_outbound_tag?: string | null;
  routed_rule_marktag?: string | null;
  tag?: string | null;
  tags?: string[] | null;
  enabled?: boolean;
}

export interface SubscriptionCatalogUserEntry {
  username: string;
  email?: string | null;
  display_name?: string | null;
  role: ProductUserRole;
  is_active: boolean;
  current_plan_name?: string | null;
  plan_started_at?: string | null;
  plan_expires_at?: string | null;
  is_reset: boolean;
  reset_day: number;
  last_traffic_reset_at?: string | null;
}

export interface SubscriptionCatalogNodeEntry {
  name: string;
  server_name: string;
  protocol: string;
  node_type: ManagedNodeType;
  inbound_tag?: string | null;
  routed_outbound_tag?: string | null;
  routed_rule_marktag?: string | null;
  tag?: string | null;
  tags: string[];
  enabled: boolean;
  client_template: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface SubscriptionCatalogPlanEntry {
  name: string;
  description: string;
  traffic_limit_gb: number;
  cycle_days: number;
  is_reset: boolean;
  reset_day: number;
  node_names: string[];
  node_multipliers: Record<string, number>;
  node_speed_limits: Record<string, number>;
  node_device_limits: Record<string, number>;
  speed_limit_mbps: number;
  device_limit: number;
  traffic_mode: SubscriptionTrafficMode;
}

export interface SubscriptionCatalogCredentialEntry {
  username: string;
  node_name: string;
  server_name: string;
  inbound_tag?: string | null;
  protocol: string;
  email: string;
  credential: Record<string, unknown>;
}

export interface SubscriptionCatalogBundle {
  version: number;
  exported_at?: string | null;
  users: SubscriptionCatalogUserEntry[];
  nodes: SubscriptionCatalogNodeEntry[];
  plans: SubscriptionCatalogPlanEntry[];
  credentials: SubscriptionCatalogCredentialEntry[];
}

export interface SubscriptionCatalogExportResponse {
  catalog: SubscriptionCatalogBundle;
  license_required: false;
}

export interface SubscriptionCatalogImportRequest {
  catalog: SubscriptionCatalogBundle;
  server_map?: Record<string, string>;
  import_credentials?: boolean;
}

export interface SubscriptionCatalogImportSummary {
  created_users: number;
  updated_users: number;
  created_nodes: number;
  updated_nodes: number;
  created_plans: number;
  updated_plans: number;
  imported_credentials: number;
  warnings: string[];
}

export interface SubscriptionCatalogImportResponse {
  summary: SubscriptionCatalogImportSummary;
  license_required: false;
}

export interface ManagedNodesResponse {
  nodes: ManagedNode[];
  license_required: false;
}

export interface ManagedNodeResponse {
  node: ManagedNode;
  license_required: false;
}

export interface XrayRuntimeNodeDraft {
  source_index: number;
  source_tag?: string | null;
  source_display_name: string;
  draft: ManagedNodeCreateRequest;
  create_available: boolean;
  existing_node_id?: string | null;
  warnings: string[];
}

export interface XrayRuntimeNodeDraftsResponse {
  server_id: string;
  has_scan: boolean;
  drafts: XrayRuntimeNodeDraft[];
  license_required: false;
}

export interface XrayRuntimeNodeCreateRequest {
  source_index?: number | null;
  inbound_tag?: string | null;
  display_name?: string | null;
  name?: string | null;
  host?: string | null;
  tags?: string[] | null;
  enabled?: boolean;
}

export interface XrayRuntimeNodeImportRequest {
  source_indexes?: number[] | null;
  host?: string | null;
  extra_tags?: string[];
  enabled?: boolean;
}

export interface XrayRuntimeNodeImportSkipped {
  source_index: number;
  source_tag?: string | null;
  source_display_name: string;
  warnings: string[];
}

export interface XrayRuntimeNodeImportResponse {
  server_id: string;
  has_scan: boolean;
  created_nodes: ManagedNode[];
  existing_nodes: ManagedNode[];
  skipped: XrayRuntimeNodeImportSkipped[];
  created_count: number;
  existing_count: number;
  skipped_count: number;
  license_required: false;
}

export interface XrayRuntimeNodeReconciliationDrift {
  field: string;
  runtime_value?: string | number | boolean | string[] | null;
  managed_value?: string | number | boolean | string[] | null;
}

export interface XrayRuntimeNodeReconciliationRuntimeEntry {
  source_index: number;
  source_tag?: string | null;
  source_display_name: string;
  protocol: string;
  port?: number | null;
  status: "managed" | "unmanaged" | "unavailable";
  managed_node_id?: string | null;
  managed_node_name?: string | null;
  warnings: string[];
}

export interface XrayRuntimeNodeReconciliationManagedEntry {
  node_id: string;
  node_name: string;
  protocol: string;
  node_type: ManagedNodeType;
  inbound_tag?: string | null;
  enabled: boolean;
  status: "in_sync" | "stale" | "missing_runtime" | "catalog_only";
  runtime_source_index?: number | null;
  runtime_display_name?: string | null;
  drifts: XrayRuntimeNodeReconciliationDrift[];
}

export interface XrayRuntimeNodeReconciliationResponse {
  server_id: string;
  has_scan: boolean;
  runtime_count: number;
  managed_node_count: number;
  managed_runtime_count: number;
  unmanaged_runtime_count: number;
  unavailable_runtime_count: number;
  in_sync_count: number;
  stale_count: number;
  missing_runtime_count: number;
  catalog_only_count: number;
  runtime_entries: XrayRuntimeNodeReconciliationRuntimeEntry[];
  managed_entries: XrayRuntimeNodeReconciliationManagedEntry[];
  license_required: false;
}

export interface XrayRuntimeNodeSyncRequest {
  source_index?: number | null;
}

export interface XrayRuntimeNodeSyncResponse {
  server_id: string;
  node: ManagedNode;
  source_index: number;
  source_tag?: string | null;
  source_display_name: string;
  updated_fields: string[];
  drifts_before: XrayRuntimeNodeReconciliationDrift[];
  drifts_after: XrayRuntimeNodeReconciliationDrift[];
  license_required: false;
}

export interface XrayRuntimeCredentialReconciliationEntry {
  node_id: string;
  node_name: string;
  protocol: string;
  inbound_tag?: string | null;
  enabled: boolean;
  runtime_source_index?: number | null;
  runtime_display_name?: string | null;
  expected_emails: string[];
  runtime_emails: string[];
  missing_runtime_emails: string[];
  extra_runtime_emails: string[];
  status:
    | "in_sync"
    | "missing_runtime"
    | "missing_runtime_clients"
    | "extra_runtime_clients"
    | "drift";
}

export interface XrayRuntimeCredentialReconciliationResponse {
  server_id: string;
  has_scan: boolean;
  node_count: number;
  expected_credential_count: number;
  matched_runtime_client_count: number;
  in_sync_count: number;
  missing_runtime_count: number;
  out_of_sync_count: number;
  missing_runtime_client_count: number;
  extra_runtime_client_count: number;
  entries: XrayRuntimeCredentialReconciliationEntry[];
  license_required: false;
}

export interface XrayRuntimeCredentialRepairRequest {
  node_ids?: string[] | null;
  queue_agent_commands?: boolean;
  queue_scan_after_apply?: boolean;
  no_restart?: boolean;
  command_timeout_ms?: number;
}

export interface XrayRuntimeCredentialRepairEntry {
  node_id: string;
  node_name: string;
  protocol: string;
  inbound_tag: string;
  runtime_source_index: number;
  runtime_display_name: string;
  emails: string[];
}

export interface XrayRuntimeCredentialRepairResponse {
  server_id: string;
  has_scan: boolean;
  entries: XrayRuntimeCredentialRepairEntry[];
  provisioning_batches: SubscriptionProvisionBatch[];
  commands: AgentCommand[];
  scan_command?: AgentCommand | null;
  planned_client_count: number;
  batch_count: number;
  warnings: string[];
  license_required: false;
}

export interface XrayRuntimeCredentialCleanupRequest {
  node_ids?: string[] | null;
  queue_agent_commands?: boolean;
  queue_scan_after_apply?: boolean;
  command_timeout_ms?: number;
}

export interface XrayRuntimeCredentialCleanupEntry {
  node_id: string;
  node_name: string;
  protocol: string;
  inbound_tag: string;
  runtime_source_index: number;
  runtime_display_name: string;
  emails: string[];
}

export interface XrayRuntimeCredentialCleanupCommand {
  node_id: string;
  node_name: string;
  body: Record<string, unknown>;
}

export interface XrayRuntimeCredentialCleanupResponse {
  server_id: string;
  has_scan: boolean;
  entries: XrayRuntimeCredentialCleanupEntry[];
  command_previews: XrayRuntimeCredentialCleanupCommand[];
  commands: AgentCommand[];
  scan_command?: AgentCommand | null;
  planned_client_count: number;
  command_count: number;
  warnings: string[];
  license_required: false;
}

export interface SubscriptionPlansResponse {
  plans: SubscriptionPlan[];
  license_required: false;
}

export interface SubscriptionPlanResponse {
  plan: SubscriptionPlan;
  license_required: false;
}

export interface SubscriptionPlanAssignResponse {
  user: ProductUser;
  plan: SubscriptionPlan;
  provisioning_batches: SubscriptionProvisionBatch[];
  commands: AgentCommand[];
  warnings: string[];
  license_required: false;
}
