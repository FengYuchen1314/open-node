import type { AgentCommand } from "./inventory";

export type ProductUserRole = "admin" | "user";
export type ManagedNodeType = "physical" | "routed";
export type SubscriptionTrafficMode = "oneway" | "twoway";
export type SubscriptionClientFormat = "clash" | "sing-box" | "uri-list" | "base64";

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

export interface ManagedNodesResponse {
  nodes: ManagedNode[];
  license_required: false;
}

export interface ManagedNodeResponse {
  node: ManagedNode;
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
