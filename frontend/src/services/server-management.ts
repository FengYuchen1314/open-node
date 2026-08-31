import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";
import type { ServerSummary } from "../domain/inventory";

export interface ServerSettings {
  name: string;
  ip_address: string | null;
  ip_address_v6: string | null;
  domain: string | null;
  domain_v6: string | null;
  ipv6_enabled: boolean;
}
export interface SettingsResponse {
  server: ServerSummary;
  revision: string;
  updated_node_ids: string[];
  license_required: false;
}
export interface RemovalPreview {
  server_id: string;
  server_name: string;
  revision: string;
  nodes: { id: string; name: string }[];
  plans: { id: string; name: string }[];
  change_sets: { id: string; name: string }[];
  certificates: { id: string; name: string }[];
  command_count: number;
  unfinished_command_count: number;
  telemetry_count: number;
  user_count: number;
  blockers: string[];
}
const base = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(id: string, path: string, init?: RequestInit, fetcher = authenticatedFetch): Promise<T> {
  const response = await fetcher(`${base}/api/v1/servers/${id}/${path}`, init);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw requestError(error?.detail, `服务器请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}
export function getServerSettings(id: string, fetcher = authenticatedFetch) {
  return request<SettingsResponse>(id, "settings", undefined, fetcher);
}
export function updateServerSettings(id: string, settings: ServerSettings, revision: string, syncHosts: boolean, fetcher = authenticatedFetch) {
  return request<SettingsResponse>(id, "settings", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...settings, expected_revision: revision, sync_node_hosts: syncHosts }) }, fetcher);
}
export function getServerRemoval(id: string, fetcher = authenticatedFetch) {
  return request<RemovalPreview>(id, "removal", undefined, fetcher);
}
export function removeServer(id: string, preview: RemovalPreview, name: string, fetcher = authenticatedFetch) {
  return request<{ server_id: string; removed_node_count: number; updated_plan_count: number }>(id, "remove", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      expected_revision: preview.revision, confirm_name: name, acknowledge_remote_runtime: true,
    }),
  }, fetcher);
}
