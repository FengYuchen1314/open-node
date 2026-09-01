import type { AgentCommandStatus, AgentNginxScan, ProbeSysMetrics, ServerStatus, XrayMode } from "./inventory";

export interface FederationProbeSys extends ProbeSysMetrics {
  upload_speed: number;
  download_speed: number;
  cumulative_up: number;
  cumulative_down: number;
  has_network: boolean;
  at: number;
}

export interface ServerShare {
  id: string;
  server_id: string;
  label: string;
  allow_manage_xray: boolean;
  revision: number;
  created_at: string;
  license_required: false;
}

export interface ServerShareCreated {
  share: ServerShare;
  share_token: string;
  license_required: false;
}

export interface ServerSharesResponse {
  shares: ServerShare[];
  license_required: false;
}

export interface FederationServerInfo {
  name: string;
  status: ServerStatus;
  ip_address: string | null;
  ip_address_v6: string | null;
  domain: string | null;
  domain_v6: string | null;
  ipv6_enabled: boolean;
  xray_mode: XrayMode;
  traffic_limit: number;
  traffic_reset_day: number;
  traffic_used: number;
  current_upload_speed: number;
  current_download_speed: number;
  xray_running: boolean | null;
  xray_version: string | null;
  nginx: AgentNginxScan | null;
  probe_sys: FederationProbeSys | null;
  last_heartbeat: string | null;
  allow_manage_xray: boolean;
  license_required: false;
}

export interface FederatedServer {
  id: string;
  name: string;
  owner_url: string;
  prefix: string;
  revision: number;
  info: FederationServerInfo;
  last_synced_at: string;
  created_at: string;
  license_required: false;
}

export interface FederatedServersResponse {
  servers: FederatedServer[];
  license_required: false;
}

export interface FederationCommand {
  id: string;
  method: "GET" | "POST";
  path: string;
  status: AgentCommandStatus;
  result_status: number | null;
  result_body: unknown;
  failed: boolean;
  created_at: string;
  completed_at: string | null;
  license_required: false;
}

export interface FederationCommandCreate {
  method: "GET" | "POST";
  path: string;
  body: Record<string, unknown> | null;
  timeout_ms: number;
}

export interface ServerShareRevoked {
  revoked: true;
  cleanup_commands: FederationCommand[];
  license_required: false;
}
