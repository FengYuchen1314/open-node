export type ConnectionMode = "auto" | "websocket" | "http" | "pull";
export type ServerStatus = "pending" | "connected" | "offline";
export type XrayMode = "external" | "embedded";

export interface ServerCreateRequest {
  name: string;
  ip_address?: string | null;
  ip_address_v6?: string | null;
  domain?: string | null;
  domain_v6?: string | null;
  connection_mode?: ConnectionMode;
  listen_port?: number;
  pull_port?: number;
  ipv6_enabled?: boolean;
  traffic_limit?: number;
  xray_mode?: XrayMode;
}

export interface ServerSummary {
  id: string;
  name: string;
  status: ServerStatus;
  ip_address?: string | null;
  ip_address_v6?: string | null;
  domain?: string | null;
  domain_v6?: string | null;
  connection_mode: ConnectionMode;
  listen_port: number;
  pull_port: number;
  ipv6_enabled: boolean;
  traffic_limit: number;
  xray_mode: XrayMode;
  current_upload_speed: number;
  current_download_speed: number;
  last_heartbeat?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ServerCreateResponse {
  server: ServerSummary;
  agent_token: string;
  license_required: false;
}

export const defaultServerCreateRequest = (): ServerCreateRequest => ({
  name: "",
  connection_mode: "auto",
  listen_port: 23889,
  ipv6_enabled: true,
  traffic_limit: 0,
  xray_mode: "external",
});
