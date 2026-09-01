import type { DDNSConfigInput, DDNSProvider, DDNSServer, DDNSWorkspace } from "../domain/ddns";
import { authenticatedFetch } from "./auth";

const root = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/ddns`;
const messages: Record<string, string> = {
  ddns_server_not_found: "服务器不存在，请刷新列表。",
  ddns_invalid_request: "DDNS 设置不正确，请检查域名和服务商。",
  ddns_revision_conflict: "设置已被其他页面修改，请刷新后重试。",
  ddns_provider_not_found: "DNS 服务商不存在，请先到证书管理页检查。",
  ddns_provider_unsupported: "该 DNS 服务商不支持动态 A/AAAA 更新。",
  ddns_provider_credentials_invalid: "DNS 服务商凭据不完整或无法解密。",
  ddns_domain_invalid: "请输入完整域名，不能使用 IP、通配符或单段名称。",
  ddns_not_enabled: "请先启用这台服务器的 DDNS。",
  ddns_no_public_address: "Agent 尚未上报可同步的公网 IPv4 或 IPv6。",
  ddns_provider_cannot_manage: "没有 DNS 服务商可以管理该域名。",
  ddns_provider_unavailable: "DNS 服务商暂时不可用，系统会自动重试。",
  ddns_provider_rejected: "DNS 服务商拒绝更新，请检查域名权限和凭据。",
  ddns_provider_invalid_response: "DNS 服务商响应异常，系统会自动重试。",
  ddns_busy: "同步正在执行，请稍后刷新。",
  ddns_storage_unavailable: "DDNS 状态暂时不可用。",
};
const unknown = "未能确认 DDNS 操作结果，请刷新状态；系统不会重复提交当前表单。";

export class DDNSRequestError extends Error {
  constructor(readonly status: number | null, readonly code: string | null) {
    super(code && Object.hasOwn(messages, code) ? messages[code] : unknown);
  }
}
function invalid(): never { throw new DDNSRequestError(null, null); }
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
}
function exact(value: unknown, keys: string[]) {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) invalid();
  return row;
}
function uuid(value: unknown) {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) ? value : invalid();
}
function text(value: unknown, max: number, required = false) {
  return typeof value === "string" && value.length <= max && (!required || value.length > 0) ? value : invalid();
}
function nullableText(value: unknown, max: number) { return value === null ? null : text(value, max); }
function instant(value: unknown) { return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value)) ? value : invalid(); }
function nullableInstant(value: unknown) { return value === null ? null : instant(value); }
function integer(value: unknown) { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : invalid(); }
function license(value: unknown) { if (value !== false) invalid(); return false as const; }
function provider(value: unknown): DDNSProvider {
  const row = exact(value, ["id", "name", "provider", "supported"]);
  if (typeof row.supported !== "boolean") invalid();
  return { id: uuid(row.id), name: text(row.name, 120, true), provider: text(row.provider, 32, true), supported: row.supported };
}
function server(value: unknown): DDNSServer {
  const row = exact(value, ["server_id", "server_name", "server_status", "enabled", "provider_id", "provider_name", "provider_type", "pull_address", "pull_address_v6", "ip_address", "ip_address_v6", "ipv6_enabled", "last_synced_at", "last_error", "pending", "revision", "license_required"]);
  if (typeof row.enabled !== "boolean" || typeof row.ipv6_enabled !== "boolean" || typeof row.pending !== "boolean") invalid();
  return {
    server_id: uuid(row.server_id), server_name: text(row.server_name, 120, true), server_status: text(row.server_status, 24, true),
    enabled: row.enabled, provider_id: row.provider_id === null ? null : uuid(row.provider_id),
    provider_name: nullableText(row.provider_name, 120), provider_type: nullableText(row.provider_type, 32),
    pull_address: nullableText(row.pull_address, 255), pull_address_v6: nullableText(row.pull_address_v6, 255),
    ip_address: nullableText(row.ip_address, 255), ip_address_v6: nullableText(row.ip_address_v6, 255), ipv6_enabled: row.ipv6_enabled,
    last_synced_at: nullableInstant(row.last_synced_at), last_error: nullableText(row.last_error, 120), pending: row.pending,
    revision: integer(row.revision), license_required: license(row.license_required),
  };
}
async function json(response: Response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) invalid();
  const source = await response.text(); if (new TextEncoder().encode(source).byteLength > 512 * 1024) invalid();
  try { return JSON.parse(source) as unknown; } catch { return invalid(); }
}
async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(root + path, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) }, cache: "no-store" });
    const value = await json(response);
    if (!response.ok) { const code = object(value).code; throw new DDNSRequestError(response.status, typeof code === "string" ? code : null); }
    return parse(value);
  } catch (error) { if (error instanceof DDNSRequestError) throw error; return invalid(); }
}
export function loadDDNS(fetcher = authenticatedFetch) {
  return request("", {}, value => {
    const row = exact(value, ["servers", "providers", "license_required"]);
    if (!Array.isArray(row.servers) || !Array.isArray(row.providers)) invalid();
    return { servers: row.servers.map(server), providers: row.providers.map(provider), license_required: license(row.license_required) } as DDNSWorkspace;
  }, fetcher);
}
export function saveDDNS(item: DDNSServer, input: Omit<DDNSConfigInput, "expected_revision">, fetcher = authenticatedFetch) {
  return request(`/${encodeURIComponent(uuid(item.server_id))}`, { method: "PUT", body: JSON.stringify({ ...input, expected_revision: item.revision }) }, server, fetcher);
}
export function syncDDNS(item: DDNSServer, fetcher = authenticatedFetch) {
  return request(`/${encodeURIComponent(uuid(item.server_id))}/sync`, { method: "POST" }, value => {
    const row = exact(value, ["server", "queued", "license_required"]); if (row.queued !== true) invalid(); license(row.license_required);
    return server(row.server);
  }, fetcher);
}
export function ddnsError(error: unknown) { return error instanceof DDNSRequestError ? error.message : unknown; }
export function ddnsStatusMessage(code: string) { return messages[code] ?? "上次 DDNS 同步失败，系统会自动重试。"; }
