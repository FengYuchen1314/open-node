export type ConnectionMode = "auto" | "websocket" | "http" | "pull";
export type ServerStatus = "pending" | "connected" | "offline";
export type XrayMode = "external" | "embedded";
export type AgentCommandStatus = "pending" | "leased" | "succeeded" | "failed";

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

export interface TrafficData {
  uplink: number;
  downlink: number;
}

export interface XrayStats {
  inbound: Record<string, TrafficData>;
  outbound: Record<string, TrafficData>;
  user: Record<string, TrafficData>;
}

export interface SystemTraffic {
  rx_total: number;
  tx_total: number;
  boot_time_unix: number;
}

export interface ProbeSysMetrics {
  cpu_pct: number;
  loadavg: string;
  mem_used: number;
  mem_total: number;
  disk_used: number;
  disk_total: number;
  uptime: number;
  cpu_model: string;
  cpu_cores: number;
  cpu_threads: number;
  os: string;
  kernel: string;
  arch: string;
  has_cpu: boolean;
  has_mem: boolean;
  has_disk: boolean;
}

export interface ProbeLatencySample {
  key: string;
  success: boolean;
  latency_ms: number;
  at?: number | null;
}

export interface AgentTelemetry {
  id: string;
  server_id: string;
  reported_at: string;
  received_at: string;
  stats?: XrayStats | null;
  online_users: Record<string, string[]>;
  user_speeds: Record<string, number>;
  conn_counts: Record<string, number>;
  system?: SystemTraffic | null;
  sysmetrics?: ProbeSysMetrics | null;
  latency: ProbeLatencySample[];
}

export interface ServerTelemetryResponse {
  server_id: string;
  latest?: AgentTelemetry | null;
  license_required: false;
}

export interface AgentCommandCreateRequest {
  method: string;
  path: string;
  query?: string;
  body?: unknown;
  timeout_ms?: number;
  stream?: boolean;
}

export interface AgentCommand {
  id: string;
  server_id: string;
  request_id: string;
  method: string;
  path: string;
  query: string;
  body?: unknown;
  timeout_ms: number;
  stream: boolean;
  status: AgentCommandStatus;
  attempts: number;
  result_status?: number | null;
  result_body?: unknown;
  result_error?: string | null;
  created_at: string;
  leased_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface AgentCommandCreateResponse {
  command: AgentCommand;
  license_required: false;
}

export interface ServerCommandsResponse {
  server_id: string;
  commands: AgentCommand[];
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
