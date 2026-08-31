import { renewalStatuses, validRenewalId, validRenewalPassphrase, type AccountRenewals, type RenewalCreate, type RenewalDecision, type RenewalRequest, type RenewalReviewResult, type RenewalsPage, type RenewalStatus } from "../domain/renewals";
import { authenticatedFetch } from "./auth";
import { clearSubscriberSession, getSubscriberSnapshot } from "./subscriber-auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1`;
const messages: Record<string, string> = {
  renewal_invalid_request: "续费申请内容不正确，请检查后重试。",
  renewal_not_found: "未找到续费申请。若提交回执丢失，请保留原申请编号并稍后查询。",
  renewal_unavailable: "当前没有可续费的套餐。",
  renewal_pending: "已有待审核的续费申请，请先查看处理结果。",
  renewal_conflict: "申请已处理或套餐已变更，请刷新后核对。",
  renewal_wrong_passphrase: "续费口令不匹配，请与用户核对。",
  renewal_access_conflict: "套餐访问权限无法安全更新，请先检查节点和用户状态。",
  renewal_rate_limited: "续费操作过于频繁，请稍后重试。",
};
const unknown = "未能确认续费操作结果，请查询原申请；不会自动重新提交。";
export const renewalCodeMessage = (code: string | null) => code && Object.hasOwn(messages, code) ? messages[code]! : unknown;
export class RenewalRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(messages, code) ? code : null;
    super(status === 401 ? "会话已失效，请重新登录。" : status === 403 ? "请求校验未通过，请重新登录后再试。" : renewalCodeMessage(safe));
    this.name = "RenewalRequestError"; this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}
export const renewalErrorMessage = (error: unknown) => error instanceof RenewalRequestError ? new RenewalRequestError(error.status, error.code).message : unknown;
function invalid(): never { throw new RenewalRequestError(null); }
function invalidInput(): never { throw new RenewalRequestError(422, "renewal_invalid_request"); }
function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid(); }
function text(value: unknown, maximum = 120): string { return typeof value === "string" && value.length <= maximum ? value : invalid(); }
function integer(value: unknown, minimum = 0, maximum = 1_000_000_000): number { return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid(); }
function instant(value: unknown): string { return typeof value === "string" && value.length <= 40 && /(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)) ? value : invalid(); }
function nullable<T>(value: unknown, read: (value: unknown) => T): T | null { return value === null ? null : read(value); }
function uuid(value: unknown): string { return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value) ? value : invalid(); }
function readRequest(value: unknown, expectedId?: string): RenewalRequest {
  const row = object(value);
  if (!validRenewalId(row.id) || (expectedId && row.id !== expectedId) || !renewalStatuses.includes(row.status as RenewalStatus)) return invalid();
  return { id: row.id, username: text(row.username, 80), plan_id: uuid(row.plan_id), plan_name: text(row.plan_name),
    previous_end_date: nullable(row.previous_end_date, instant), renew_days: integer(row.renew_days, 1),
    status: row.status as RenewalStatus, created_at: instant(row.created_at), reviewed_at: nullable(row.reviewed_at, instant),
    reviewed_by: nullable(row.reviewed_by, value => text(value, 64)), new_end_date: nullable(row.new_end_date, instant) };
}
function readPage(value: unknown): RenewalsPage {
  const row = object(value);
  if (!Array.isArray(row.requests) || row.requests.length > 100 || row.license_required !== false) return invalid();
  const requests = row.requests.map(value => readRequest(value));
  if (new Set(requests.map(item => item.id)).size !== requests.length) return invalid();
  return { requests, total: integer(row.total), limit: integer(row.limit, 1, 100), offset: integer(row.offset), license_required: false };
}
function readAccount(value: unknown): AccountRenewals {
  const row = object(value), page = readPage(value);
  if (typeof row.eligible !== "boolean") return invalid();
  return { ...page, eligible: row.eligible, unavailable_code: nullable(row.unavailable_code, value => text(value, 80)),
    plan_id: nullable(row.plan_id, uuid), plan_name: nullable(row.plan_name, value => text(value)),
    renew_days: nullable(row.renew_days, value => integer(value, 1)), plan_expires_at: nullable(row.plan_expires_at, instant) };
}
async function readJson(response: Response): Promise<unknown> {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength;
      if (size > 262144) { await reader.cancel(); return invalid(); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function request<T>(path: string, init: RequestInit, subscriber: boolean, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  const session = getSubscriberSnapshot().session;
  const headers = new Headers({ Accept: "application/json", "Content-Type": "application/json" });
  if (subscriber && session?.csrf_token && init.method && init.method !== "GET") headers.set("X-CSRF-Token", session.csrf_token);
  const controller = new AbortController(), timer = globalThis.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetcher(`${base}${path}`, { ...init, headers, signal: controller.signal, credentials: "include", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" });
    if (!response.ok) {
      if (subscriber && response.status === 401 && getSubscriberSnapshot().session === session) clearSubscriberSession();
      const body = await readJson(response).catch(() => null);
      throw new RenewalRequestError(response.status, body && typeof body === "object" ? (body as Record<string, unknown>).code : null);
    }
    return parse(await readJson(response));
  } catch (error) { if (error instanceof RenewalRequestError) throw error; return invalid(); }
  finally { globalThis.clearTimeout(timer); }
}
const pathId = (id: string) => validRenewalId(id) ? id : invalidInput();
export function getAccountRenewals(offset = 0, fetcher = fetch) { return request(`/account/renewals?limit=20&offset=${integer(offset)}`, {}, true, readAccount, fetcher); }
export function getAccountRenewal(id: string, fetcher = fetch) { return request(`/account/renewals/${pathId(id)}`, {}, true, value => readRequest(value, id), fetcher); }
export function submitRenewal(payload: RenewalCreate, fetcher = fetch) {
  if (!validRenewalId(payload.request_id) || !validRenewalPassphrase(payload.passphrase)) return invalidInput();
  const body = { request_id: payload.request_id, passphrase: payload.passphrase.trim() };
  return request("/account/renewals", { method: "POST", body: JSON.stringify(body) }, true, value => readRequest(value, body.request_id), fetcher);
}
export function cancelRenewal(id: string, fetcher = fetch) { return request(`/account/renewals/${pathId(id)}/cancel`, { method: "POST" }, true, value => readRequest(value, id), fetcher); }
export function listRenewals(status: RenewalStatus | "all" = "pending", offset = 0, fetcher = authenticatedFetch) {
  if (status !== "all" && !renewalStatuses.includes(status)) return invalidInput();
  const query = new URLSearchParams({ limit: "20", offset: String(integer(offset)) });
  if (status !== "all") query.set("status", status);
  return request(`/renewals?${query}`, {}, false, readPage, fetcher);
}
export function reviewRenewal(id: string, payload: RenewalDecision, fetcher = authenticatedFetch): Promise<RenewalReviewResult> {
  if (payload.confirm_reviewed !== true || (payload.decision === "approve" && !validRenewalPassphrase(payload.passphrase))) return invalidInput();
  const body = payload.decision === "approve" ? { decision: "approve", confirm_reviewed: true, passphrase: payload.passphrase.trim() } : { decision: "reject", confirm_reviewed: true };
  return request(`/renewals/${pathId(id)}/review`, { method: "POST", body: JSON.stringify(body) }, false, value => {
    const row = object(value);
    if (typeof row.processed !== "boolean" || !Array.isArray(row.commands) || !Array.isArray(row.warnings) || row.license_required !== false) return invalid();
    return { request: readRequest(row.request, id), processed: row.processed, command_count: row.commands.length, warnings_count: row.warnings.length };
  }, fetcher);
}
export function newRenewalRequestId(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 15) | 64; bytes[8] = (bytes[8]! & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
