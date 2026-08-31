import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

export type BootstrapTransport = "auto" | "websocket" | "http";
export type BootstrapStatus = "not_issued" | "issued" | "claimed" | "expired" | "revoked";

export interface AgentBootstrapState {
  bootstrap: {
    server_id: string;
    server_name: string;
    status: BootstrapStatus;
    issued_at: string | null;
    expires_at: string | null;
    claimed_at: string | null;
    agent_registered: boolean;
    agent_registered_at: string | null;
    agent_last_seen_at: string | null;
    agent_version: string | null;
    server_last_heartbeat: string | null;
  };
  configured: boolean;
  control_url: string | null;
  release: {
    agent_version: string;
    source_commit: string;
    xray_version: string;
    platform: string;
  } | null;
  reason: string | null;
  license_required: false;
}

export interface AgentBootstrapIssued {
  issued: {
    server_id: string;
    server_name: string;
    control_url: string;
    transport: BootstrapTransport;
    issued_at: string;
    expires_at: string;
  };
  command: string;
  license_required: false;
}

const base = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(serverId: string, init: RequestInit, fetcher: typeof fetch): Promise<T> {
  const response = await fetcher(`${base}/api/v1/servers/${encodeURIComponent(serverId)}/bootstrap`, {
    ...init, cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw requestError(typeof body?.detail === "string" ? body.detail : undefined,
      `Agent 安装请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function getAgentBootstrap(serverId: string, fetcher = authenticatedFetch) {
  return request<AgentBootstrapState>(serverId, {}, fetcher);
}

export function issueAgentBootstrap(serverId: string, transport: BootstrapTransport, fetcher = authenticatedFetch) {
  return request<AgentBootstrapIssued>(serverId, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ transport }),
  }, fetcher);
}

export function revokeAgentBootstrap(serverId: string, fetcher = authenticatedFetch) {
  return request<AgentBootstrapState>(serverId, { method: "DELETE" }, fetcher);
}
