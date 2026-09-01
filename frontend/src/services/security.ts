import type { SecurityBan, SecurityEvent, SecurityEventKind, SecurityEvents, SecuritySettings } from "../domain/security";
import { authenticatedFetch } from "./auth";

const messages: Record<string, string> = {
  security_invalid_request: "安全管理请求无效。",
  security_unavailable: "安全管理暂时不可用。",
  security_revision_conflict: "安全设置已被其他会话修改，请重新读取。",
  security_ban_not_found: "该 IP 当前没有生效的封禁。",
};
const unknown = "安全管理响应无效，请重新读取。";

export class SecurityRequestError extends Error {
  constructor(readonly status: number | null, readonly code: string | null = null) {
    super(code && Object.hasOwn(messages, code) ? messages[code] : unknown);
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new SecurityRequestError(null);
  return value as Record<string, unknown>;
}
function exact(row: Record<string, unknown>, keys: string[]) {
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) throw new SecurityRequestError(null);
}
function integer(value: unknown, minimum = 0) {
  if (!Number.isInteger(value) || (value as number) < minimum) throw new SecurityRequestError(null);
  return value as number;
}
function date(value: unknown, nullable = false) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) throw new SecurityRequestError(null);
  return value;
}
const kinds = new Set<SecurityEventKind>(["probe", "ban", "unban", "ban_manual", "login_fail", "login_locked"]);

function event(value: unknown): SecurityEvent {
  const row = object(value), keys = ["id", "at", "ip", "kind", "path", "username", "detail", "actor"];
  exact(row, keys);
  if (typeof row.ip !== "string" || typeof row.kind !== "string" || !kinds.has(row.kind as SecurityEventKind)
    || typeof row.path !== "string" || typeof row.username !== "string" || typeof row.detail !== "string" || typeof row.actor !== "string") throw new SecurityRequestError(null);
  return { id: integer(row.id, 1), at: date(row.at)!, ip: row.ip, kind: row.kind as SecurityEventKind,
    path: row.path, username: row.username, detail: row.detail, actor: row.actor };
}
function ban(value: unknown): SecurityBan {
  const row = object(value), keys = ["ip", "reason", "banned_at", "expires_at", "permanent", "fail_count", "actor"];
  exact(row, keys);
  if (typeof row.ip !== "string" || !["brute_force", "manual"].includes(String(row.reason))
    || typeof row.permanent !== "boolean" || typeof row.actor !== "string") throw new SecurityRequestError(null);
  return { ip: row.ip, reason: row.reason as SecurityBan["reason"], banned_at: date(row.banned_at)!,
    expires_at: date(row.expires_at, true), permanent: row.permanent, fail_count: integer(row.fail_count), actor: row.actor };
}
function settings(value: unknown): SecuritySettings {
  const row = object(value), keys = ["revision", "brute_force_enabled", "brute_force_max_failures", "brute_force_window_minutes", "brute_force_block_minutes", "skip_local_ip", "license_required"];
  exact(row, keys);
  if (typeof row.brute_force_enabled !== "boolean" || typeof row.skip_local_ip !== "boolean" || row.license_required !== false) throw new SecurityRequestError(null);
  return { revision: integer(row.revision), brute_force_enabled: row.brute_force_enabled,
    brute_force_max_failures: integer(row.brute_force_max_failures, 2),
    brute_force_window_minutes: integer(row.brute_force_window_minutes, 1),
    brute_force_block_minutes: integer(row.brute_force_block_minutes, 1),
    skip_local_ip: row.skip_local_ip, license_required: false };
}

async function request(path: string, init: RequestInit = {}, fetcher = authenticatedFetch): Promise<unknown> {
  const response = await fetcher(`/api/v1/security${path}`, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const row = body && typeof body === "object" ? body as Record<string, unknown> : null;
    throw new SecurityRequestError(response.status, typeof row?.code === "string" ? row.code : null);
  }
  return body;
}

export async function loadSecuritySettings(fetcher = authenticatedFetch) { return settings(await request("/settings", {}, fetcher)); }
export async function saveSecuritySettings(value: SecuritySettings, fetcher = authenticatedFetch) {
  return settings(await request("/settings", { method: "PUT", body: JSON.stringify({
    expected_revision: value.revision,
    brute_force_enabled: value.brute_force_enabled,
    brute_force_max_failures: value.brute_force_max_failures,
    brute_force_window_minutes: value.brute_force_window_minutes,
    brute_force_block_minutes: value.brute_force_block_minutes,
    skip_local_ip: value.skip_local_ip,
  }) }, fetcher));
}
export async function loadSecurityBans(fetcher = authenticatedFetch) {
  const row = object(await request("/bans", {}, fetcher)); exact(row, ["bans", "license_required"]);
  if (!Array.isArray(row.bans) || row.license_required !== false) throw new SecurityRequestError(null);
  return row.bans.map(ban);
}
export async function createSecurityBan(ip: string, permanent: boolean, fetcher = authenticatedFetch) {
  return ban(await request("/bans", { method: "POST", body: JSON.stringify({ ip, permanent }) }, fetcher));
}
export async function removeSecurityBan(ip: string, fetcher = authenticatedFetch) {
  await request(`/bans/${encodeURIComponent(ip)}`, { method: "DELETE" }, fetcher);
}
export async function loadSecurityEvents(filters: { kind?: SecurityEventKind; ip?: string; limit?: number; offset?: number } = {}, fetcher = authenticatedFetch): Promise<SecurityEvents> {
  const parameters = new URLSearchParams();
  if (filters.kind) parameters.set("kind", filters.kind);
  if (filters.ip?.trim()) parameters.set("ip", filters.ip.trim());
  parameters.set("limit", String(filters.limit ?? 100)); parameters.set("offset", String(filters.offset ?? 0));
  const row = object(await request(`/events?${parameters}`, {}, fetcher));
  exact(row, ["events", "offset", "limit", "has_more", "license_required"]);
  if (!Array.isArray(row.events) || typeof row.has_more !== "boolean" || row.license_required !== false) throw new SecurityRequestError(null);
  return { events: row.events.map(event), offset: integer(row.offset), limit: integer(row.limit, 1), has_more: row.has_more, license_required: false };
}

export function securityError(error: unknown) { return error instanceof SecurityRequestError ? error.message : unknown; }
