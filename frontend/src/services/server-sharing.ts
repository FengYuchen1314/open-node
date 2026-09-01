import type {
  FederatedServer,
  FederatedServersResponse,
  FederationCommand,
  FederationCommandCreate,
  FederationServerInfo,
  ServerShare,
  ServerShareCreated,
  ServerShareRevoked,
  ServerSharesResponse,
} from "../domain/server-sharing";
import { authenticatedFetch } from "./auth";

const root = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1`;
const errors: Record<string, string> = {
  server_share_invalid_request: "服务器共享请求不正确，请检查地址、令牌和命令内容。",
  server_share_not_found: "服务器分享不存在、已被删除或已被吊销。",
  server_share_token_invalid: "分享令牌无效或已被吊销。",
  server_share_forbidden: "此分享无权执行该服务器操作。",
  server_share_conflict: "服务器共享记录已变化，请重新读取后再操作。",
  server_share_storage_unavailable: "服务器共享数据暂时不可用，请稍后重新读取。",
  server_share_owner_unavailable: "无法安全连接拥有方主控，请检查公网 HTTPS 地址。",
  server_share_owner_response_invalid: "拥有方返回了无法识别的联邦响应。",
  server_share_busy: "服务器共享操作过于频繁，请稍后重试。",
};
const unknown = "未能确认服务器共享操作结果，请重新读取；不会自动重复提交。";

export class ServerSharingRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(errors, code) ? code : null;
    super(safe ? errors[safe] : unknown); this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

function invalid(): never { throw new ServerSharingRequestError(null); }
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : invalid();
}
function exact(value: unknown, keys: string[]) {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function uuid(value: unknown) {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
    ? value : invalid();
}
function text(value: unknown, maximum: number, empty = true) {
  return typeof value === "string" && value.length <= maximum && (empty || value.length > 0)
    ? value : invalid();
}
function integer(value: unknown) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : invalid();
}
function instant(value: unknown) {
  return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value))
    ? value : invalid();
}
function nullableText(value: unknown, maximum: number) { return value === null ? null : text(value, maximum); }
function nullableInstant(value: unknown) { return value === null ? null : instant(value); }
function license(value: unknown) { if (value !== false) invalid(); return false as const; }

function info(value: unknown): FederationServerInfo {
  const row = exact(value, ["name", "status", "ip_address", "ip_address_v6", "domain", "domain_v6", "ipv6_enabled", "xray_mode", "traffic_limit", "traffic_reset_day", "traffic_used", "current_upload_speed", "current_download_speed", "xray_running", "xray_version", "last_heartbeat", "allow_manage_xray", "license_required"]);
  if (!["pending", "connected", "offline"].includes(String(row.status))
    || !["external", "embedded"].includes(String(row.xray_mode))
    || typeof row.ipv6_enabled !== "boolean"
    || typeof row.allow_manage_xray !== "boolean"
    || row.xray_running !== null && typeof row.xray_running !== "boolean") invalid();
  return {
    name: text(row.name, 120, false), status: row.status as FederationServerInfo["status"],
    ip_address: nullableText(row.ip_address, 255), ip_address_v6: nullableText(row.ip_address_v6, 255),
    domain: nullableText(row.domain, 255), domain_v6: nullableText(row.domain_v6, 255),
    ipv6_enabled: row.ipv6_enabled, xray_mode: row.xray_mode as FederationServerInfo["xray_mode"],
    traffic_limit: integer(row.traffic_limit), traffic_reset_day: integer(row.traffic_reset_day), traffic_used: integer(row.traffic_used),
    current_upload_speed: integer(row.current_upload_speed), current_download_speed: integer(row.current_download_speed),
    xray_running: row.xray_running as boolean | null, xray_version: nullableText(row.xray_version, 120),
    last_heartbeat: nullableInstant(row.last_heartbeat), allow_manage_xray: row.allow_manage_xray, license_required: license(row.license_required),
  };
}
function share(value: unknown): ServerShare {
  const row = exact(value, ["id", "server_id", "label", "allow_manage_xray", "revision", "created_at", "license_required"]);
  if (typeof row.allow_manage_xray !== "boolean") invalid();
  return { id: uuid(row.id), server_id: uuid(row.server_id), label: text(row.label, 80), allow_manage_xray: row.allow_manage_xray,
    revision: integer(row.revision), created_at: instant(row.created_at), license_required: license(row.license_required) };
}
function federated(value: unknown): FederatedServer {
  const row = exact(value, ["id", "name", "owner_url", "prefix", "revision", "info", "last_synced_at", "created_at", "license_required"]);
  return { id: uuid(row.id), name: text(row.name, 120, false), owner_url: text(row.owner_url, 2048, false), prefix: text(row.prefix, 40),
    revision: integer(row.revision), info: info(row.info), last_synced_at: instant(row.last_synced_at), created_at: instant(row.created_at), license_required: license(row.license_required) };
}
function command(value: unknown): FederationCommand {
  const row = exact(value, ["id", "method", "path", "status", "result_status", "result_body", "failed", "created_at", "completed_at", "license_required"]);
  if (!["GET", "POST"].includes(String(row.method)) || !["waiting", "pending", "leased", "succeeded", "failed", "skipped"].includes(String(row.status))
    || typeof row.failed !== "boolean" || row.result_status !== null && (!Number.isInteger(row.result_status) || Number(row.result_status) < 0 || Number(row.result_status) > 999)) invalid();
  return { id: uuid(row.id), method: row.method as FederationCommand["method"], path: text(row.path, 255, false), status: row.status as FederationCommand["status"],
    result_status: row.result_status as number | null, result_body: row.result_body, failed: row.failed, created_at: instant(row.created_at),
    completed_at: nullableInstant(row.completed_at), license_required: license(row.license_required) };
}
async function json(response: Response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) return invalid();
  const source = await response.text(); if (new TextEncoder().encode(source).byteLength > 512 * 1024) return invalid();
  try { return JSON.parse(source) as unknown; } catch { return invalid(); }
}
async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(root + path, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) }, cache: "no-store" });
    const body = response.status === 204 ? null : await json(response);
    if (!response.ok) throw new ServerSharingRequestError(response.status, object(body).code);
    return parse(body);
  } catch (error) { if (error instanceof ServerSharingRequestError) throw error; return invalid(); }
}
const body = (value: unknown) => ({ method: "POST", body: JSON.stringify(value) });

export function listServerShares(serverId: string, fetcher = authenticatedFetch) {
  return request(`/server-shares?server_id=${encodeURIComponent(uuid(serverId))}`, {}, value => {
    const row = exact(value, ["shares", "license_required"]); if (!Array.isArray(row.shares)) invalid();
    return { shares: row.shares.map(share), license_required: license(row.license_required) } as ServerSharesResponse;
  }, fetcher);
}
export function createServerShare(serverId: string, label: string, allowManageXray: boolean, fetcher = authenticatedFetch) {
  return request("/server-shares", body({ server_id: uuid(serverId), label, allow_manage_xray: allowManageXray }), value => {
    const row = exact(value, ["share", "share_token", "license_required"]);
    if (typeof row.share_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(row.share_token)) invalid();
    return { share: share(row.share), share_token: row.share_token, license_required: license(row.license_required) } as ServerShareCreated;
  }, fetcher);
}
export function revokeServerShare(item: ServerShare, deleteInbounds: boolean, fetcher = authenticatedFetch) {
  return request(`/server-shares/${encodeURIComponent(uuid(item.id))}/revoke`, body({ expected_revision: integer(item.revision), delete_inbounds: deleteInbounds }), value => {
    const row = exact(value, ["revoked", "cleanup_commands", "license_required"]); if (row.revoked !== true || !Array.isArray(row.cleanup_commands)) invalid();
    return { revoked: true, cleanup_commands: row.cleanup_commands.map(command), license_required: license(row.license_required) } as ServerShareRevoked;
  }, fetcher);
}
export function listFederatedServers(fetcher = authenticatedFetch) {
  return request("/server-federation", {}, value => {
    const row = exact(value, ["servers", "license_required"]); if (!Array.isArray(row.servers)) invalid();
    return { servers: row.servers.map(federated), license_required: license(row.license_required) } as FederatedServersResponse;
  }, fetcher);
}
export function addFederatedServer(value: { owner_url: string; share_token: string; name: string; prefix: string }, fetcher = authenticatedFetch) {
  return request("/server-federation", body(value), federated, fetcher);
}
export function refreshFederatedServer(item: FederatedServer, fetcher = authenticatedFetch) {
  return request(`/server-federation/${encodeURIComponent(uuid(item.id))}/refresh`, body({ expected_revision: integer(item.revision) }), federated, fetcher);
}
export function deleteFederatedServer(item: FederatedServer, fetcher = authenticatedFetch) {
  return request(`/server-federation/${encodeURIComponent(uuid(item.id))}/delete`, body({ expected_revision: integer(item.revision), confirm: true }), value => {
    if (value !== null) invalid(); return undefined;
  }, fetcher);
}
export function manageFederatedServer(item: FederatedServer, payload: FederationCommandCreate, fetcher = authenticatedFetch) {
  return request(`/server-federation/${encodeURIComponent(uuid(item.id))}/manage`, body(payload), command, fetcher);
}
export function getFederatedCommand(item: FederatedServer, commandId: string, fetcher = authenticatedFetch) {
  return request(`/server-federation/${encodeURIComponent(uuid(item.id))}/commands/${encodeURIComponent(uuid(commandId))}`, {}, command, fetcher);
}

export function serverSharingErrorMessage(error: unknown) {
  return error instanceof ServerSharingRequestError ? error.message : unknown;
}
