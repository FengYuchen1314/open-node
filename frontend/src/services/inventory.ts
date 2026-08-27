import type {
  AgentCommandCreateRequest,
  AgentCommandCreateResponse,
  AgentCommandStreamFramesResponse,
  AgentDomainLatencyProbeRequest,
  AgentOperationPayload,
  AgentOperationKind,
  ServerCommandsResponse,
  ServerTelemetryResponse,
  ServerCreateRequest,
  ServerCreateResponse,
  ServerSummary,
} from "../domain/inventory";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

const jsonHeaders = {
  "Content-Type": "application/json",
};

const operationPaths: Record<AgentOperationKind, string> = {
  system_info: "system-info",
  traffic: "traffic",
  speed: "speed",
  domain_latency: "domain-latency",
  services_status: "services/status",
  service_control: "services/control",
  system_nics: "system/nics",
  logs: "logs",
  scan: "scan",
  xray_test_config: "xray/test-config",
  xray_config_read: "xray/config/read",
  xray_config_write: "xray/config/write",
  xray_system_config_read: "xray/system-config/read",
  xray_system_config_write: "xray/system-config/write",
  xray_config_files_list: "xray/config-files/list",
  xray_config_file_read: "xray/config-files/read",
  xray_config_file_write: "xray/config-files/write",
  xray_install: "xray/install",
  xray_remove: "xray/remove",
  nginx_config_read: "nginx/config/read",
  nginx_config_write: "nginx/config/write",
  nginx_config_files_list: "nginx/config-files/list",
  nginx_config_file_read: "nginx/config-files/read",
  nginx_config_file_write: "nginx/config-files/write",
  nginx_install: "nginx/install",
  nginx_remove: "nginx/remove",
  warp_install: "warp/install",
  warp_status: "warp/status",
  warp_license: "warp/license",
  warp_remove: "warp/remove",
  agent_switch_xray_mode: "agent/switch-xray-mode",
  agent_switch_listen_port: "agent/switch-listen-port",
  agent_probe_master_url: "agent/probe-master-url",
  agent_update_master_url: "agent/update-master-url",
  agent_upgrade: "agent/upgrade",
  agent_uninstall: "agent/uninstall",
};

export async function listServers(fetcher = fetch): Promise<ServerSummary[]> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers`);
  if (!response.ok) {
    throw await apiError(response, "Server list request failed");
  }
  return response.json() as Promise<ServerSummary[]>;
}

export async function createServer(
  payload: ServerCreateRequest,
  fetcher = fetch,
): Promise<ServerCreateResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Server create request failed");
  }
  return response.json() as Promise<ServerCreateResponse>;
}

export async function getLatestTelemetry(
  serverId: string,
  fetcher = fetch,
): Promise<ServerTelemetryResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers/${serverId}/telemetry/latest`);
  if (!response.ok) {
    throw await apiError(response, "Server telemetry request failed");
  }
  return response.json() as Promise<ServerTelemetryResponse>;
}

export async function listServerCommands(
  serverId: string,
  fetcher = fetch,
): Promise<ServerCommandsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers/${serverId}/commands`);
  if (!response.ok) {
    throw await apiError(response, "Server command list request failed");
  }
  return response.json() as Promise<ServerCommandsResponse>;
}

export async function createServerCommand(
  serverId: string,
  payload: AgentCommandCreateRequest,
  fetcher = fetch,
): Promise<AgentCommandCreateResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers/${serverId}/commands`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Server command create request failed");
  }
  return response.json() as Promise<AgentCommandCreateResponse>;
}

export async function queueAgentOperation(
  serverId: string,
  operation: AgentOperationKind,
  payload?: AgentOperationPayload,
  fetcher = fetch,
): Promise<AgentCommandCreateResponse> {
  const request: RequestInit = { method: "POST" };
  if (payload) {
    request.headers = jsonHeaders;
    request.body = JSON.stringify(payload);
  }

  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/operations/${operationPaths[operation]}`,
    request,
  );
  if (!response.ok) {
    throw await apiError(response, "Server operation request failed");
  }
  return response.json() as Promise<AgentCommandCreateResponse>;
}

export async function listCommandStreamFrames(
  serverId: string,
  commandId: string,
  fetcher = fetch,
): Promise<AgentCommandStreamFramesResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/commands/${commandId}/stream`,
  );
  if (!response.ok) {
    throw await apiError(response, "Command stream frame request failed");
  }
  return response.json() as Promise<AgentCommandStreamFramesResponse>;
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.length > 0) {
      return new Error(body.detail);
    }
  } catch {
    // The backend normally returns JSON errors, but network proxies may not.
  }
  return new Error(`${fallback} with ${response.status}`);
}
