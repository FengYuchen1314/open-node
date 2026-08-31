import {
  brandingErrorCodes, normalizeBrandingText, validBrandingRevision,
  type BrandingErrorCode, type BrandingSettings, type BrandingUpdate, type PublicBranding,
} from "../domain/branding";
import { authenticatedFetch } from "./auth";

const administratorPath = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/system-settings/branding`;
const safeCodes = new Set<string>(brandingErrorCodes);
const messages: Record<BrandingErrorCode, string> = {
  branding_invalid_request: "站点文字不符合要求，请检查名称长度和字符。",
  branding_revision_conflict: "站点文字已被其他操作修改，请重新读取后再保存。",
  branding_storage_unavailable: "站点文字存储暂不可用，请稍后重新读取。",
};
const unconfirmed = "未能确认站点文字，请重新读取当前配置。";

function errorMessage(status: number | null, code: BrandingErrorCode | null): string {
  if (status === 401) return "请重新登录管理员账户后管理站点文字。";
  if (status === 403) return "此操作需要管理员权限和有效的请求验证。";
  if (code && Object.hasOwn(messages, code)) return messages[code];
  return unconfirmed;
}

export class BrandingRequestError extends Error {
  readonly status: number | null;
  readonly code: BrandingErrorCode | null;
  constructor(status: number | null, code?: unknown) {
    const safe = typeof code === "string" && safeCodes.has(code) ? code as BrandingErrorCode : null;
    super(errorMessage(status, safe));
    this.name = "BrandingRequestError"; this.status = status; this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

export function brandingErrorMessage(error: unknown): string {
  // A server detail, rejected input or mutated Error.message is never display text.
  return error instanceof BrandingRequestError ? errorMessage(error.status, error.code) : unconfirmed;
}

function invalid(): never { throw new BrandingRequestError(null); }
function invalidInput(): never { throw new BrandingRequestError(422, "branding_invalid_request"); }
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function titles(row: Record<string, unknown>): PublicBranding {
  const site = normalizeBrandingText(row.site_title, 80), brand = normalizeBrandingText(row.brand_title, 40);
  if (site === null || brand === null || site !== row.site_title || brand !== row.brand_title || row.license_required !== false) return invalid();
  return { site_title: site, brand_title: brand, license_required: false };
}
function publicBranding(value: unknown): PublicBranding {
  return titles(record(value, ["site_title", "brand_title", "license_required"]));
}
function settings(value: unknown): BrandingSettings {
  const row = record(value, ["site_title", "brand_title", "license_required", "revision"]);
  if (!validBrandingRevision(row.revision)) return invalid();
  return { ...titles(row), revision: row.revision };
}

async function json(response: Response): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength;
      if (size > 8192) { await reader.cancel(); return invalid(); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}

async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  const controller = new AbortController(), timer = globalThis.setTimeout(() => controller.abort(), 15000);
  try {
    let response: Response;
    try {
      response = await fetcher(path, { ...init, signal: controller.signal, cache: "no-store", redirect: "error", referrerPolicy: "no-referrer",
        headers: { Accept: "application/json", ...(init.body === undefined ? {} : { "Content-Type": "application/json" }) } });
    } catch { throw new BrandingRequestError(null); }
    if (!response.ok) {
      const body = await json(response).catch(() => null);
      const code = body && typeof body === "object" && !Array.isArray(body) && Object.hasOwn(body, "code") ? (body as Record<string, unknown>).code : null;
      throw new BrandingRequestError(response.status, code);
    }
    try { return parse(await json(response)); } catch { return invalid(); }
  } finally { globalThis.clearTimeout(timer); }
}

export function getPublicBranding(fetcher = fetch): Promise<PublicBranding> {
  // Branding on both login forms is deliberately anonymous and same-origin.
  return request("/api/v1/branding", { credentials: "omit" }, publicBranding, fetcher);
}

export function getBrandingSettings(fetcher = authenticatedFetch): Promise<BrandingSettings> {
  return request(administratorPath, {}, settings, fetcher);
}

export async function updateBrandingSettings(payload: BrandingUpdate, fetcher = authenticatedFetch): Promise<BrandingSettings> {
  if (!payload || !validBrandingRevision(payload.expected_revision)) return invalidInput();
  const site = normalizeBrandingText(payload.site_title, 80), brand = normalizeBrandingText(payload.brand_title, 40);
  if (site === null || brand === null) return invalidInput();
  const body: BrandingUpdate = { expected_revision: payload.expected_revision, site_title: site, brand_title: brand };
  return request(administratorPath, { method: "PUT", body: JSON.stringify(body) }, value => {
    const result = settings(value);
    if (result.revision !== body.expected_revision + 1 || result.site_title !== site || result.brand_title !== brand) return invalid();
    return result;
  }, fetcher);
}
