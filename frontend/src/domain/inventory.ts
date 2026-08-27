export type ConnectionMode = "auto" | "websocket" | "http" | "pull";
export type ServerStatus = "pending" | "connected" | "offline";
export type XrayMode = "external" | "embedded";
export type RenewalCycle = "month" | "quarter" | "half_year" | "year";
export type AgentServiceName = "xray" | "nginx";
export type AgentServiceAction = "start" | "stop" | "restart";
export type AgentLogService = "agent" | "xray" | "nginx";
export type AgentCommandStatus = "pending" | "leased" | "succeeded" | "failed";
export type XrayConfigSnapshotStatus = "current" | "old" | "pending_recovery";
export type XrayConfigSnapshotSource = "agent_report" | "master_write" | "manual_accept";
export type AgentOperationKind =
  | "system_info"
  | "traffic"
  | "speed"
  | "domain_latency"
  | "inbounds_list"
  | "inbounds_manage"
  | "outbounds_list"
  | "outbounds_manage"
  | "routing_read"
  | "routing_manage"
  | "batch_apply"
  | "cert_deploy"
  | "nginx_setup_ssl"
  | "nginx_servers_list"
  | "nginx_websites_list"
  | "nginx_website_delete"
  | "return_route_test"
  | "validate_site"
  | "limiter"
  | "services_status"
  | "service_control"
  | "system_nics"
  | "logs"
  | "log_files_list"
  | "log_files_delete"
  | "scan"
  | "xray_test_config"
  | "xray_config_read"
  | "xray_config_write"
  | "xray_system_config_read"
  | "xray_system_config_write"
  | "xray_config_files_list"
  | "xray_config_file_read"
  | "xray_config_file_write"
  | "xray_takeover_external"
  | "xray_install_legacy"
  | "xray_remove_legacy"
  | "xray_install"
  | "xray_remove"
  | "nginx_config_read"
  | "nginx_config_write"
  | "nginx_config_files_list"
  | "nginx_config_file_read"
  | "nginx_config_file_write"
  | "nginx_install_legacy"
  | "nginx_remove_legacy"
  | "nginx_install"
  | "nginx_remove"
  | "nginx_clear_stream_port"
  | "warp_install"
  | "warp_status"
  | "warp_license"
  | "warp_remove"
  | "agent_switch_xray_mode"
  | "agent_switch_listen_port"
  | "agent_probe_master_url"
  | "agent_update_master_url"
  | "agent_upgrade"
  | "agent_uninstall";

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
  region?: string | null;
  region_country?: string | null;
  region_name?: string | null;
  region_city?: string | null;
  provider_name?: string | null;
  provider_url?: string | null;
  expires_at?: string | null;
  renewal_price?: number | null;
  renewal_price_cny?: number | null;
  renewal_cycle?: RenewalCycle | null;
  renewal_currency?: string | null;
  telecom_paid_peer?: boolean | null;
}

export interface ServerProbeMetadataUpdate {
  region?: string | null;
  region_country?: string | null;
  region_name?: string | null;
  region_city?: string | null;
  provider_name?: string | null;
  provider_url?: string | null;
  expires_at?: string | null;
  renewal_price?: number | null;
  renewal_price_cny?: number | null;
  renewal_cycle?: RenewalCycle | null;
  renewal_currency?: string | null;
  telecom_paid_peer?: boolean | null;
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
  region?: string | null;
  region_country?: string | null;
  region_name?: string | null;
  region_city?: string | null;
  provider_name?: string | null;
  provider_url?: string | null;
  expires_at?: string | null;
  renewal_price?: number | null;
  renewal_price_cny?: number | null;
  renewal_cycle?: RenewalCycle | null;
  renewal_currency?: string | null;
  telecom_paid_peer?: boolean | null;
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

export interface ServerResponse {
  server: ServerSummary;
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

export interface AgentScanResult {
  server_id: string;
  xray_running: boolean;
  xray_version?: string | null;
  api_port?: number | null;
  config_path?: string | null;
  inbounds: Record<string, unknown>[];
  device_kicks: Record<string, number>;
  config_modified: boolean;
  config_added_sections: string[];
  message?: string | null;
  reported_at: string;
  updated_at: string;
}

export interface ServerScanResultResponse {
  server_id: string;
  scan?: AgentScanResult | null;
  license_required: false;
}

export interface XrayConfigSnapshot {
  id: string;
  server_id: string;
  source_command_id?: string | null;
  config_hash: string;
  source: XrayConfigSnapshotSource;
  status: XrayConfigSnapshotStatus;
  size_bytes: number;
  config?: string | null;
  created_at: string;
}

export interface ServerXrayConfigSnapshotsResponse {
  server_id: string;
  snapshots: XrayConfigSnapshot[];
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

export interface AgentCommandStreamFrame {
  id: string;
  command_id: string;
  server_id: string;
  request_id: string;
  sequence: number;
  data: string;
  received_at: string;
}

export interface AgentCommandCreateResponse {
  command: AgentCommand;
  license_required: false;
}

export interface AgentDomainLatencyProbeRequest {
  domains: string[];
  timeout_ms?: number;
  allow_icmp?: boolean;
  command_timeout_ms?: number;
}

export interface AgentNginxInstallOperationRequest {
  domain?: string | null;
  command_timeout_ms?: number;
}

export interface AgentServiceControlOperationRequest {
  service: AgentServiceName;
  action: AgentServiceAction;
}

export interface AgentLogsOperationRequest {
  service?: AgentLogService;
  lines?: number;
}

export interface AgentLogFilesDeleteOperationRequest {
  name?: string | null;
  all?: boolean;
  command_timeout_ms?: number;
}

export interface AgentInboundsManageOperationRequest {
  action?: "add" | "remove" | "replace" | "add-client" | "remove-client" | "add-sniffing-exclude";
  inbound?: Record<string, unknown> | null;
  tag?: string | null;
  client?: Record<string, unknown> | null;
  domains?: string[];
  command_timeout_ms?: number;
}

export interface AgentOutboundsManageOperationRequest {
  action?: "add" | "remove" | "update" | "reorder";
  outbound?: Record<string, unknown> | null;
  tag?: string | null;
  tags?: string[];
  command_timeout_ms?: number;
}

export interface AgentRoutingManageOperationRequest {
  action?: "set" | "add_rule" | "remove_rule" | "add_user_to_rule" | "remove_user_from_rule";
  routing?: Record<string, unknown> | null;
  rule?: Record<string, unknown> | null;
  index?: number;
  observatory?: unknown;
  burst_observatory?: unknown;
  marktag?: string | null;
  user_email?: string | null;
  no_restart?: boolean;
  command_timeout_ms?: number;
}

export interface AgentBatchApplyOperationRequest {
  inbound_clients?: Array<{ tag: string; client: Record<string, unknown> }>;
  routing_user_additions?: Array<{
    marktag?: string | null;
    outbound_tag?: string | null;
    user_email: string;
  }>;
  no_restart?: boolean;
  command_timeout_ms?: number;
}

export interface AgentCertDeployOperationRequest {
  domain: string;
  cert_pem: string;
  key_pem: string;
  cert_path: string;
  key_path: string;
  reload?: "nginx" | "xray" | "both" | "none";
  command_timeout_ms?: number;
}

export interface AgentNginxSetupSSLOperationRequest {
  domain: string;
  nginx_config?: string | null;
  domain_config?: string | null;
  command_timeout_ms?: number;
}

export interface AgentNginxWebsiteDeleteOperationRequest {
  domain: string;
  command_timeout_ms?: number;
}

export interface AgentNginxClearStreamPortOperationRequest {
  port: number;
  command_timeout_ms?: number;
}

export interface AgentReturnRouteTarget {
  carrier: "telecom" | "unicom" | "mobile";
  region?: string;
  host: string;
  port?: number;
}

export interface AgentReturnRouteTestOperationRequest {
  ip_version?: 4 | 6;
  timeout_seconds?: number;
  targets: AgentReturnRouteTarget[];
  command_timeout_ms?: number;
}

export interface AgentValidateSiteOperationRequest {
  site_type: "static" | "proxy";
  site_value: string;
  command_timeout_ms?: number;
}

export interface AgentLimiterOperationRequest {
  inbound_tag: string;
  node_limit?: number;
  users?: Array<{
    uid: number;
    email: string;
    speed_limit?: number;
    device_limit?: number;
  }>;
  auto_speed_rules?: Record<string, unknown>[];
  command_timeout_ms?: number;
}

export interface AgentXrayTestConfigOperationRequest {
  config: unknown;
  command_timeout_ms?: number;
}

export interface AgentXrayConfigOperationRequest {
  config: unknown;
  path?: string | null;
  force?: boolean;
  command_timeout_ms?: number;
}

export interface AgentXraySystemConfigOperationRequest {
  metrics_enabled?: boolean;
  metrics_listen?: string;
  stats_enabled?: boolean;
  grpc_enabled?: boolean;
  grpc_port?: number;
  command_timeout_ms?: number;
}

export interface AgentXrayConfigFileReadOperationRequest {
  file: string;
}

export interface AgentXrayConfigFileWriteOperationRequest {
  file: string;
  content: unknown;
  command_timeout_ms?: number;
}

export interface AgentXrayTakeoverExternalOperationRequest {
  command_timeout_ms?: number;
}

export interface AgentNginxConfigOperationRequest {
  config: string;
  path?: string | null;
  command_timeout_ms?: number;
}

export interface AgentNginxConfigFileReadOperationRequest {
  file: string;
}

export interface AgentNginxConfigFileWriteOperationRequest {
  path: string;
  content: string;
  command_timeout_ms?: number;
}

export interface AgentWarpLicenseOperationRequest {
  license: string;
  command_timeout_ms?: number;
}

export interface AgentSwitchXrayModeOperationRequest {
  xray_mode: XrayMode;
  command_timeout_ms?: number;
}

export interface AgentSwitchListenPortOperationRequest {
  listen_port: number;
  command_timeout_ms?: number;
}

export interface AgentProbeMasterURLOperationRequest {
  master_url: string;
  command_timeout_ms?: number;
}

export interface AgentUpdateMasterURLOperationRequest {
  master_url: string;
  only_if_recovery?: boolean;
  command_timeout_ms?: number;
}

export type AgentOperationPayload =
  | AgentDomainLatencyProbeRequest
  | AgentNginxInstallOperationRequest
  | AgentServiceControlOperationRequest
  | AgentLogsOperationRequest
  | AgentLogFilesDeleteOperationRequest
  | AgentInboundsManageOperationRequest
  | AgentOutboundsManageOperationRequest
  | AgentRoutingManageOperationRequest
  | AgentBatchApplyOperationRequest
  | AgentCertDeployOperationRequest
  | AgentNginxSetupSSLOperationRequest
  | AgentNginxWebsiteDeleteOperationRequest
  | AgentNginxClearStreamPortOperationRequest
  | AgentReturnRouteTestOperationRequest
  | AgentValidateSiteOperationRequest
  | AgentLimiterOperationRequest
  | AgentXrayTestConfigOperationRequest
  | AgentXrayConfigOperationRequest
  | AgentXraySystemConfigOperationRequest
  | AgentXrayConfigFileReadOperationRequest
  | AgentXrayConfigFileWriteOperationRequest
  | AgentXrayTakeoverExternalOperationRequest
  | AgentNginxConfigOperationRequest
  | AgentNginxConfigFileReadOperationRequest
  | AgentNginxConfigFileWriteOperationRequest
  | AgentWarpLicenseOperationRequest
  | AgentSwitchXrayModeOperationRequest
  | AgentSwitchListenPortOperationRequest
  | AgentProbeMasterURLOperationRequest
  | AgentUpdateMasterURLOperationRequest;

export interface ServerCommandsResponse {
  server_id: string;
  commands: AgentCommand[];
  license_required: false;
}

export interface AgentCommandStreamFramesResponse {
  server_id: string;
  command_id: string;
  frames: AgentCommandStreamFrame[];
  license_required: false;
}

export const defaultServerCreateRequest = (): ServerCreateRequest => ({
  name: "",
  ip_address: "",
  ip_address_v6: "",
  domain: "",
  domain_v6: "",
  connection_mode: "auto",
  listen_port: 23889,
  pull_port: 0,
  ipv6_enabled: true,
  traffic_limit: 0,
  xray_mode: "external",
  region: "",
  region_country: "",
  region_name: "",
  region_city: "",
  provider_name: "",
  provider_url: "",
  expires_at: "",
  renewal_price: null,
  renewal_price_cny: null,
  renewal_cycle: null,
  renewal_currency: "",
  telecom_paid_peer: null,
});
