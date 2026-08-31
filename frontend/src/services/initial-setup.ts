import { normalizeBrandingText } from "../domain/branding";

export interface InitialSetupStatus {
  configured: boolean;
  available: boolean;
  expires_at: string | null;
  token_required: true;
}
export interface InitialSetupInput {
  setup_token: string;
  username: string;
  password: string;
  site_title: string;
  brand_title: string;
  confirm_new_install: boolean;
}
const messages: Record<string, string> = {
  setup_invalid_request: "请检查初始化凭证、用户名、密码和站点名称。",
  setup_already_completed: "此实例已初始化。请登录，或在服务器终端恢复管理员密码。",
  setup_ticket_invalid: "初始化凭证无效或已过期，请在安装终端重新生成。",
  setup_unavailable: "初始化暂不可用，请重新读取状态。",
  setup_rate_limited: "尝试过于频繁，请至少等待一分钟后重试。",
};
const unknown = "未能确认初始化结果。请重新读取状态，勿重复提交。";
export class InitialSetupError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(messages, code) ? code : null;
    super(safe ? messages[safe] : unknown); this.code = safe;
  }
}
export function setupErrorMessage(error: unknown): string {
  return error instanceof InitialSetupError && error.code && Object.hasOwn(messages, error.code) ? messages[error.code] : unknown;
}
function invalid(): never { throw new InitialSetupError(null); }
function row(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) return invalid();
  return result;
}
export function validateSetupInput(value: InitialSetupInput): InitialSetupInput {
  const site = normalizeBrandingText(value.site_title, 80), brand = normalizeBrandingText(value.brand_title, 40);
  if (typeof value.setup_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.setup_token)
    || typeof value.username !== "string" || !/^[a-zA-Z0-9_.@-]{1,64}$/.test(value.username)
    || typeof value.password !== "string" || Array.from(value.password).length < 12 || Array.from(value.password).length > 1024
    || site === null || brand === null || value.confirm_new_install !== true) throw new InitialSetupError(422, "setup_invalid_request");
  return { setup_token: value.setup_token, username: value.username, password: value.password,
    site_title: site, brand_title: brand, confirm_new_install: true };
}
async function readJson(response: Response): Promise<unknown> {
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
async function request(method: "GET" | "POST", payload: InitialSetupInput | null, fetcher: typeof fetch): Promise<unknown> {
  const controller = new AbortController(), timeout = globalThis.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetcher("/api/v1/setup", { method, credentials: "omit", cache: "no-store", redirect: "error",
      referrerPolicy: "no-referrer", signal: controller.signal,
      headers: { Accept: "application/json", "X-Open-Node-Client": "browser", ...(payload ? { "Content-Type": "application/json" } : {}) },
      ...(payload ? { body: JSON.stringify(payload) } : {}) });
    const body = await readJson(response).catch(() => null);
    if (!response.ok) throw new InitialSetupError(response.status, body && typeof body === "object" ? (body as Record<string, unknown>).code : null);
    if (response.status !== (method === "GET" ? 200 : 201) || body === null) return invalid();
    return body;
  } catch (error) {
    if (error instanceof InitialSetupError) throw error;
    return invalid();
  } finally { globalThis.clearTimeout(timeout); }
}
export async function getInitialSetupStatus(fetcher = fetch): Promise<InitialSetupStatus> {
  const value = row(await request("GET", null, fetcher), ["configured", "available", "expires_at", "token_required"]);
  if (typeof value.configured !== "boolean" || typeof value.available !== "boolean" || value.token_required !== true
    || (value.configured && value.available)
    || (value.available ? typeof value.expires_at !== "string" || !Number.isFinite(Date.parse(value.expires_at)) : value.expires_at !== null)) return invalid();
  return value as unknown as InitialSetupStatus;
}
export async function completeInitialSetup(payload: InitialSetupInput, fetcher = fetch): Promise<void> {
  const value = row(await request("POST", validateSetupInput(payload), fetcher), ["configured", "login_required"]);
  if (value.configured !== true || value.login_required !== true) return invalid();
}
