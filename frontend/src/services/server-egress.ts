import type {
  ServerEgressApplyRequest,
  ServerEgressApplyResponse,
  ServerEgressCatalog,
  ServerEgressPreview,
  ServerEgressPreviewRequest,
  ServerEgressRemovePreviewRequest,
  ServerEgressRemoveRequest,
} from "../domain/server-egress";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(serverId: string, suffix: string, init: RequestInit = {}, fetcher = authenticatedFetch) {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers/${encodeURIComponent(serverId)}/egress${suffix}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw requestError(body?.detail, `服务器出口请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const getServerEgressCatalog = (serverId: string, fetcher = authenticatedFetch) =>
  request<ServerEgressCatalog>(serverId, "", {}, fetcher);

export const previewServerEgress = (serverId: string, payload: ServerEgressPreviewRequest, fetcher = authenticatedFetch) =>
  request<ServerEgressPreview>(serverId, "/preview", { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const applyServerEgress = (serverId: string, payload: ServerEgressApplyRequest, fetcher = authenticatedFetch) =>
  request<ServerEgressApplyResponse>(serverId, "/apply", { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const previewServerEgressRemoval = (serverId: string, payload: ServerEgressRemovePreviewRequest, fetcher = authenticatedFetch) =>
  request<ServerEgressPreview>(serverId, "/remove/preview", { method: "POST", body: JSON.stringify(payload) }, fetcher);

export const removeServerEgress = (serverId: string, payload: ServerEgressRemoveRequest, fetcher = authenticatedFetch) =>
  request<ServerEgressApplyResponse>(serverId, "/remove", { method: "POST", body: JSON.stringify(payload) }, fetcher);
