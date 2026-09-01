import { announcementTypes, safeAnnouncementText, type Announcement, type AnnouncementCreate, type AnnouncementsResponse, type AnnouncementType } from "../domain/announcements";
import { authenticatedFetch } from "./auth";
import { accountRequest } from "./subscriber-auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/announcements`;
const messages: Record<string, string> = {
  announcement_invalid_request: "公告内容不正确，请检查后重试。",
  announcement_not_found: "公告不存在或已被删除。",
  announcement_storage_unavailable: "公告暂时不可用，请稍后重试。",
  announcement_rate_limited: "公告操作过于频繁，请稍后重试。",
};
const unknown = "未能确认公告操作结果，请重新读取；不会自动重复提交。";

export class AnnouncementRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(messages, code) ? code : null;
    super(status === 401 ? "管理员会话已失效，请重新登录。" : status === 403 ? "请求校验未通过，请重新登录后再试。" : safe ? messages[safe] : unknown);
    this.name = "AnnouncementRequestError";
    this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

export const announcementErrorMessage = (error: unknown) => error instanceof AnnouncementRequestError ? error.message : unknown;
function invalid(): never { throw new AnnouncementRequestError(null); }
function invalidInput(): never { throw new AnnouncementRequestError(422, "announcement_invalid_request"); }
function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid(); }
function record(value: unknown, keys: string[]): Record<string, unknown> {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function text(value: unknown, maximum: number, multiline = false): string {
  if (typeof value !== "string") return invalid();
  const result = safeAnnouncementText(value, maximum, multiline);
  return result !== null && result.length > 0 && result === value ? result : invalid();
}
function instant(value: unknown): string {
  return typeof value === "string" && value.length <= 40 && /(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)) ? value : invalid();
}
function id(value: unknown): string { return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value) ? value : invalid(); }
function announcement(value: unknown): Announcement {
  const row = record(value, ["id", "type", "title", "body", "created_at", "expires_at"]);
  if (!announcementTypes.includes(row.type as AnnouncementType)) return invalid();
  return { id: id(row.id), type: row.type as AnnouncementType, title: text(row.title, 100),
    body: text(row.body, 2000, true), created_at: instant(row.created_at),
    expires_at: row.expires_at === null ? null : instant(row.expires_at) };
}
function response(value: unknown): AnnouncementsResponse {
  const row = record(value, ["announcements", "license_required"]);
  if (row.license_required !== false || !Array.isArray(row.announcements) || row.announcements.length > 100) return invalid();
  const items = row.announcements.map(announcement);
  if (new Set(items.map(item => item.id)).size !== items.length) return invalid();
  return { announcements: items, license_required: false };
}
async function json(response: Response): Promise<unknown> {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength; if (size > 1024 * 1024) { await reader.cancel(); return invalid(); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function adminRequest<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(base + path, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
    const body = await json(response);
    if (!response.ok) throw new AnnouncementRequestError(response.status, object(body).code);
    return parse(body);
  } catch (error) { if (error instanceof AnnouncementRequestError) throw error; return invalid(); }
}
function createPayload(payload: AnnouncementCreate): AnnouncementCreate {
  if (!announcementTypes.includes(payload.type) || !Number.isSafeInteger(payload.expires_minutes) || payload.expires_minutes < 0 || payload.expires_minutes > 525600) return invalidInput();
  const title = payload.title.trim() ? safeAnnouncementText(payload.title, 100) : "";
  const body = safeAnnouncementText(payload.body, 2000, true);
  if (title === null || body === null || !body) return invalidInput();
  return { ...payload, title, body };
}
export function listAnnouncements(fetcher = authenticatedFetch) { return adminRequest("", {}, response, fetcher); }
export function publishAnnouncement(payload: AnnouncementCreate, fetcher = authenticatedFetch) {
  const body = createPayload(payload);
  return adminRequest("", { method: "POST", body: JSON.stringify(body) }, announcement, fetcher);
}
export function deleteAnnouncement(identifier: string, fetcher = authenticatedFetch) {
  const expected = id(identifier);
  return adminRequest(`/${expected}`, { method: "DELETE" }, value => {
    const row = record(value, ["id", "deleted", "license_required"]);
    if (row.id !== expected || row.deleted !== true || row.license_required !== false) return invalid();
    return expected;
  }, fetcher);
}
export async function accountAnnouncements(fetcher = fetch) {
  return response(await accountRequest<unknown>("announcements", {}, fetcher));
}
