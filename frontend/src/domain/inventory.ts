export type ConnectionMode = "auto" | "websocket" | "http" | "pull";
export type ServerStatus = "pending" | "connected" | "offline";
export type XrayMode = "external" | "embedded";
export type RenewalCycle = "month" | "quarter" | "half_year" | "year";
export type AgentServiceName = "xray" | "nginx";
export type AgentServiceAction = "start" | "stop" | "restart";
export type AgentLogService = "agent" | "xray" | "nginx";
export type AgentCommandStatus = "waiting" | "pending" | "leased" | "succeeded" | "failed" | "skipped";
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
  | "limiter_status"
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
  | "xray_release"
  | "xray_rollback"
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
  | "agent_uninstall"
  | "agent_rollback"
  | "agent_lifecycle";

export interface ServerCreateRequest {
  name: string;
  ip_address?: string | null;
  ip_address_v6?: string | null;
  domain?: string | null;
  domain_v6?: string | null;
  connection_mode?: ConnectionMode;
  listen_port?: number;
  pull_address?: string | null;
  pull_address_v6?: string | null;
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
  pull_address?: string | null;
  pull_address_v6?: string | null;
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
  http01?: {
    version: 1;
    standalone: boolean;
    webroots: string[];
    cleanup_error: string | null;
  } | null;
  nginx?: {
    tunnel_deploy?: number;
    running: boolean;
    installed: boolean;
    available: boolean;
    mode: "managed";
    config_path: string;
    certificate_dir: string;
    html_path: string;
  } | null;
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

export interface XrayRuntimeInbound {
  source_index: number;
  tag?: string | null;
  display_name: string;
  protocol: string;
  port?: number | null;
  listen?: string | null;
  network?: string | null;
  security?: string | null;
  client_container?: string | null;
  client_count: number;
  user_emails: string[];
  sniffing_enabled: boolean;
  sniffing_dest_override: string[];
  sniffing_exclude_domains: string[];
  traffic: TrafficData;
  user_traffic: TrafficData;
  remarks: string[];
}

export interface XrayRuntimeInventoryResponse {
  server_id: string;
  has_scan: boolean;
  xray_running: boolean;
  xray_version?: string | null;
  api_port?: number | null;
  config_path?: string | null;
  config_modified: boolean;
  config_added_sections: string[];
  message?: string | null;
  inbound_count: number;
  client_count: number;
  protocol_counts: Record<string, number>;
  traffic: TrafficData;
  user_traffic: TrafficData;
  traffic_reported_at?: string | null;
  inbounds: XrayRuntimeInbound[];
  reported_at?: string | null;
  updated_at?: string | null;
  license_required: false;
}

export interface XrayRuntimeTunnel {
  kind: "inbound" | "routed";
  tag: string;
  listen_port?: number | null;
  target_address?: string | null;
  target_port?: number | null;
  network?: string | null;
  inbound_tag?: string | null;
  match_domains: string[];
  match_ips: string[];
  rule_index?: number | null;
}

export interface XrayRuntimeTunnelHop {
  tag: string;
  listen_port?: number | null;
  target_address?: string | null;
  target_port?: number | null;
}

export interface XrayRuntimeTunnelChain {
  label: string;
  hops: XrayRuntimeTunnelHop[];
  entry_port?: number | null;
  final_target?: string | null;
}

export interface XrayRuntimeTunnelInventoryResponse {
  server_id: string;
  has_config: boolean;
  source_snapshot_id?: string | null;
  tunnel_count: number;
  chain_count: number;
  tunnels: XrayRuntimeTunnel[];
  chains: XrayRuntimeTunnelChain[];
  warnings: string[];
  license_required: false;
}

export interface XrayRuntimeTunnelDeleteRequest {
  kind: "inbound" | "routed" | "chain";
  tag?: string | null;
  label?: string | null;
  rule_index?: number | null;
  queue_agent_commands?: boolean;
  queue_scan_after_apply?: boolean;
  command_timeout_ms?: number;
}

export interface XrayRuntimeTunnelDeleteCommand {
  method: "POST";
  path: string;
  body: Record<string, unknown>;
}

export interface XrayRuntimeTunnelDeleteResponse {
  server_id: string;
  has_config: boolean;
  source_snapshot_id?: string | null;
  target_kind: "inbound" | "routed" | "chain";
  target_tag?: string | null;
  target_label?: string | null;
  command_previews: XrayRuntimeTunnelDeleteCommand[];
  commands: AgentCommand[];
  scan_command?: AgentCommand | null;
  command_count: number;
  warnings: string[];
  license_required: false;
}

export interface XrayRuntimeTunnelChainCreateRequest {
  label: string;
  server_ids: string[];
  entry_port?: number;
  target_address: string;
  target_port: number;
  queue_agent_commands?: boolean;
  queue_scan_after_apply?: boolean;
  command_timeout_ms?: number;
}

export interface XrayRuntimeTunnelChainHop {
  server_id: string;
  server_name: string;
  tag: string;
  listen_port: number;
  target_address: string;
  target_port: number;
}

export interface XrayRuntimeTunnelChainCreateCommand {
  server_id: string;
  server_name: string;
  hop_index: number;
  method: "POST";
  path: "/api/child/inbounds";
  body: Record<string, unknown>;
}

export interface XrayRuntimeTunnelChainCreateResponse {
  label: string;
  entry_server_id: string;
  entry_host: string;
  entry_port: number;
  final_target: string;
  hops: XrayRuntimeTunnelChainHop[];
  command_previews: XrayRuntimeTunnelChainCreateCommand[];
  commands: AgentCommand[];
  scan_commands: AgentCommand[];
  command_count: number;
  warnings: string[];
  license_required: false;
}

export interface XrayRuntimeTunnelDeployRequest {
  domain?: string | null;
  proxy_domain?: string | null;
  site_type?: "static" | "proxy";
  site_value?: string | null;
  listen_address?: string;
  listen_port?: number;
  nginx_port?: number;
  forward_port?: number;
  api_port?: number;
  metrics_port?: number;
  cert_name?: string | null;
  clear_stream_port?: boolean;
  restart_xray?: boolean;
  force?: boolean;
  queue_agent_commands?: boolean;
  queue_scan_after_apply?: boolean;
  command_timeout_ms?: number;
}

export interface XrayRuntimeTunnelDeployCommand {
  step: string;
  method: "POST";
  path: string;
  body?: Record<string, unknown> | null;
}

export interface XrayRuntimeTunnelDeployResponse {
  runtime_profile?: "legacy" | "open-node";
  server_id: string;
  server_name: string;
  domain: string;
  proxy_domain?: string | null;
  cert_name: string;
  nginx_config: string;
  domain_config: string;
  xray_config: string;
  command_previews: XrayRuntimeTunnelDeployCommand[];
  commands: AgentCommand[];
  scan_command_preview?: XrayRuntimeTunnelDeployCommand | null;
  scan_command?: AgentCommand | null;
  command_count: number;
  warnings: string[];
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

export interface XrayConfigSnapshotRecoveryStatusResponse {
  server_id: string;
  has_pending: boolean;
  has_current: boolean;
  pending?: XrayConfigSnapshot | null;
  current?: XrayConfigSnapshot | null;
  license_required: false;
}

export interface XrayConfigSnapshotRecoveryAcceptResponse {
  server_id: string;
  current: XrayConfigSnapshot;
  snapshots: XrayConfigSnapshot[];
  license_required: false;
}

export interface XrayConfigSnapshotRecoveryApplyRequest {
  restart_xray?: boolean;
  merge_agent_only?: boolean;
  command_timeout_ms?: number;
}

export interface XrayConfigSnapshotRecoveryApplyResponse {
  server_id: string;
  snapshot: XrayConfigSnapshot;
  commands: AgentCommand[];
  command_count: number;
  merged_agent_only_count: number;
  warnings: string[];
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
  depends_on_command_id?: string | null;
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

export interface AgentUpgradeOperationRequest {
  version: string;
  sha256: string;
}

export interface AgentLifecycleConfirmationRequest {
  confirm: true;
}

export interface AgentXrayInstallOperationRequest {
  version?: string;
  sha256?: string;
  start?: boolean;
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
  limiter_users?: Array<{ inbound_tag: string; user: AgentLimiterUser }>;
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

export interface AgentLimiterUser {
  uid: number;
  email: string;
  speed_limit?: number;
  device_limit?: number;
  conn_group?: string;
}

export interface AgentLimiterOperationRequest {
  action?: "sync" | "remove";
  expected_revision?: string;
  inbound_tag: string;
  node_limit?: number;
  users?: AgentLimiterUser[];
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
  preview?: boolean;
  confirm?: true;
  expected_sha256?: string;
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

export interface AgentWarpInstallOperationRequest {
  accept_terms: boolean;
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
  | AgentUpgradeOperationRequest
  | AgentLifecycleConfirmationRequest
  | AgentXrayInstallOperationRequest
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
  | AgentWarpInstallOperationRequest
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
export interface AgentIdentityInfo {
  enabled: boolean;
  protocol: string;
  public_key: string | null;
  fingerprint: string | null;
  license_required: false;
}
