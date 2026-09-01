import { authenticatedFetch } from "./auth";
import { defaultAppearance, validImageUrl, validRevision, type AppearanceSettings, type AppearanceUpdate, type PublicAppearance } from "../domain/appearance";

const adminPath = "/api/v1/system-settings/appearance", publicPath = "/api/v1/appearance";
const messages: Record<string, string> = {
  appearance_invalid_request: "请检查主题和图片地址。",
  appearance_revision_conflict: "外观设置已被其他操作修改，请重新读取。",
  appearance_invalid_image: "图片格式无效、内容不受支持或超过大小限制。",
  appearance_storage_unavailable: "外观设置暂不可用，请稍后重新读取。",
  appearance_asset_missing: "已上传的图片不存在，请重新上传或清空地址。",
};
const unknown = "未能确认外观设置，请重新读取当前配置。";
export class AppearanceRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(messages, code) ? code : null;
    super(safe ? messages[safe] : unknown); this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}
export function appearanceErrorMessage(error: unknown) {
  return error instanceof AppearanceRequestError && error.code ? messages[error.code] : unknown;
}
function invalid(): never { throw new AppearanceRequestError(null); }
function record(value: unknown, keys: string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function publicValue(value: unknown): PublicAppearance {
  const row = record(value, ["default_theme", "logo_url", "wallpaper_url", "license_required"]);
  if (!(["light", "dark", "system"] as unknown[]).includes(row.default_theme)
    || !validImageUrl(row.logo_url, "logo") || !validImageUrl(row.wallpaper_url, "wallpaper")
    || row.license_required !== false) return invalid();
  return row as unknown as PublicAppearance;
}
function settings(value: unknown): AppearanceSettings {
  const row = record(value, ["default_theme", "logo_url", "wallpaper_url", "license_required", "revision"]);
  if (!validRevision(row.revision)) return invalid();
  return { ...publicValue({ default_theme: row.default_theme, logo_url: row.logo_url,
    wallpaper_url: row.wallpaper_url, license_required: row.license_required }), revision: row.revision };
}
async function json(response: Response) {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength; if (size > 8192) { await reader.cancel(); return invalid(); }
      parts.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function request(path: string, init: RequestInit, parser: (value: unknown) => AppearanceSettings | PublicAppearance, fetcher: typeof fetch) {
  const controller = new AbortController(), timeout = globalThis.setTimeout(() => controller.abort(), init.body instanceof Blob ? 60000 : 15000);
  try {
    let response: Response;
    try { response = await fetcher(path, { ...init, signal: controller.signal, cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" }); }
    catch { throw new AppearanceRequestError(null); }
    if (!response.ok) {
      const body = await json(response).catch(() => null);
      throw new AppearanceRequestError(response.status, body && typeof body === "object" ? (body as Record<string, unknown>).code : null);
    }
    return parser(await json(response));
  } finally { globalThis.clearTimeout(timeout); }
}
export function getPublicAppearance(fetcher = fetch): Promise<PublicAppearance> {
  return request(publicPath, { credentials: "omit", headers: { Accept: "application/json" } }, publicValue, fetcher) as Promise<PublicAppearance>;
}
export function getAppearanceSettings(fetcher = authenticatedFetch): Promise<AppearanceSettings> {
  return request(adminPath, { headers: { Accept: "application/json" } }, settings, fetcher) as Promise<AppearanceSettings>;
}
export function updateAppearance(value: AppearanceUpdate, fetcher = authenticatedFetch): Promise<AppearanceSettings> {
  if (!validRevision(value.expected_revision) || !validImageUrl(value.logo_url, "logo")
    || !validImageUrl(value.wallpaper_url, "wallpaper") || !(["light", "dark", "system"] as unknown[]).includes(value.default_theme)) return Promise.reject(new AppearanceRequestError(422, "appearance_invalid_request"));
  const payload = { ...value, license_required: false };
  return request(adminPath, { method: "PUT", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: JSON.stringify(payload) }, settings, fetcher) as Promise<AppearanceSettings>;
}
export function uploadAppearanceImage(slot: "logo" | "wallpaper", revision: number, file: File, fetcher = authenticatedFetch): Promise<AppearanceSettings> {
  const maximum = slot === "logo" ? 2 * 1024 * 1024 : 10 * 1024 * 1024;
  if (!validRevision(revision) || !(file instanceof Blob) || !file.size || file.size > maximum) return Promise.reject(new AppearanceRequestError(422, "appearance_invalid_image"));
  return request(`${adminPath}/${slot}`, { method: "POST", headers: { Accept: "application/json", "Content-Type": file.type || "application/octet-stream", "X-Appearance-Revision": String(revision) }, body: file }, settings, fetcher) as Promise<AppearanceSettings>;
}
export { defaultAppearance };
