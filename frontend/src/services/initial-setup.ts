import { normalizeBrandingText } from "../domain/branding";
import { validBackupId, type RestoreArchiveFormat, type RestorePreparedReceipt, type RestoreUploadReceipt } from "../domain/backups";

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
  email: string;
  nickname: string;
  avatar_url: string;
  confirm_new_install: boolean;
}
export interface InitialRestorePrepareInput {
  setup_token: string;
  format: RestoreArchiveFormat;
  identity: string;
  subscriber_totp_key: string;
  confirm_replace_instance: true;
  confirm_trusted_backup: true;
}
const messages: Record<string, string> = {
  setup_invalid_request: "请检查初始化凭证、用户名、密码和站点名称。",
  setup_already_completed: "此实例已初始化。请登录，或在服务器终端恢复管理员密码。",
  setup_ticket_invalid: "初始化凭证无效或已过期，请在安装终端重新生成。",
  setup_unavailable: "初始化暂不可用，请重新读取状态。",
  setup_rate_limited: "尝试过于频繁，请至少等待一分钟后重试。",
  restore_upload_invalid: "恢复文件无效、损坏或超出支持范围。",
  restore_upload_not_found: "恢复上传不存在、已过期或不属于当前初始化凭证。",
  restore_upload_busy: "已有恢复正在准备或等待重启，请勿重复提交。",
  restore_upload_unavailable: "当前部署不支持浏览器恢复，请使用离线恢复命令。",
  restore_prepare_failed: "恢复校验未完成，当前实例没有被覆盖。请检查备份、age 私钥和 TOTP 配置密钥。",
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
  const email = value.email.trim(), nickname = value.nickname.trim().replace(/\s+/g, " "), avatar = value.avatar_url.trim();
  let avatarUrl = "";
  if (avatar) {
    try { const parsed = new URL(avatar); if (parsed.protocol !== "https:" || parsed.username || parsed.password) throw new Error(); avatarUrl = parsed.href; }
    catch { throw new InitialSetupError(422, "setup_invalid_request"); }
  }
  if (typeof value.setup_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.setup_token)
    || typeof value.username !== "string" || !/^[a-zA-Z0-9_.@-]{1,64}$/.test(value.username)
    || typeof value.password !== "string" || Array.from(value.password).length < 12 || Array.from(value.password).length > 1024
    || site === null || brand === null || /[\u0000-\u001f\u007f]/.test(value.nickname) || Array.from(nickname).length > 120
    || (email !== "" && (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || Array.from(email).length > 254))
    || Array.from(avatarUrl).length > 2048 || value.confirm_new_install !== true) throw new InitialSetupError(422, "setup_invalid_request");
  return { setup_token: value.setup_token, username: value.username, password: value.password,
    site_title: site, brand_title: brand, email, nickname, avatar_url: avatarUrl, confirm_new_install: true };
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

function restoreUpload(value: unknown): RestoreUploadReceipt {
  const item = row(value, ["id", "size", "sha256", "expires_at", "license_required"]);
  if (!validBackupId(item.id) || typeof item.size !== "number" || !Number.isSafeInteger(item.size)
    || item.size < 22 || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)
    || typeof item.expires_at !== "string" || !Number.isFinite(Date.parse(item.expires_at))
    || item.license_required !== false) return invalid();
  return item as unknown as RestoreUploadReceipt;
}
function restorePrepared(value: unknown): RestorePreparedReceipt {
  const item = row(value, ["id", "restart_required", "automatic_restart", "license_required"]);
  if (!validBackupId(item.id) || item.restart_required !== true
    || typeof item.automatic_restart !== "boolean" || item.license_required !== false) return invalid();
  return item as unknown as RestorePreparedReceipt;
}
function validRestoreInput(value: InitialRestorePrepareInput): boolean {
  return Boolean(value && /^[A-Za-z0-9_-]{43}$/.test(value.setup_token)
    && ["age", "plain"].includes(value.format) && typeof value.identity === "string"
    && value.identity.length <= 4096 && (value.format === "age") === Boolean(value.identity)
    && typeof value.subscriber_totp_key === "string" && value.subscriber_totp_key.length <= 44
    && value.confirm_replace_instance === true && value.confirm_trusted_backup === true);
}
async function restoreRequest(
  path: string, init: RequestInit, expected: number, fetcher: typeof fetch,
): Promise<unknown> {
  const controller = new AbortController(), timeout = globalThis.setTimeout(() => controller.abort(), 600000);
  try {
    const response = await fetcher(`/api/v1/setup/${path}`, {
      ...init, credentials: "omit", cache: "no-store", redirect: "error",
      referrerPolicy: "no-referrer", signal: controller.signal,
    });
    const body = await readJson(response).catch(() => null);
    if (!response.ok) throw new InitialSetupError(response.status,
      body && typeof body === "object" ? (body as Record<string, unknown>).code : null);
    if (response.status !== expected || body === null) return invalid();
    return body;
  } catch (error) {
    if (error instanceof InitialSetupError) throw error;
    return invalid();
  } finally { globalThis.clearTimeout(timeout); }
}
export async function uploadInitialRestore(
  file: Blob, setupToken: string, fetcher = fetch,
): Promise<RestoreUploadReceipt> {
  if (!(file instanceof Blob) || file.size < 22 || !/^[A-Za-z0-9_-]{43}$/.test(setupToken)) {
    throw new InitialSetupError(422, "setup_invalid_request");
  }
  const value = restoreUpload(await restoreRequest("restore-uploads", {
    method: "POST", body: file, headers: {
      Accept: "application/json", "Content-Type": "application/octet-stream",
      "X-Open-Node-Client": "browser", "X-Open-Node-Setup-Token": setupToken,
    },
  }, 201, fetcher));
  if (value.size !== file.size) return invalid();
  return value;
}
export async function prepareInitialRestore(
  uploadId: string, payload: InitialRestorePrepareInput, fetcher = fetch,
): Promise<RestorePreparedReceipt> {
  if (!validBackupId(uploadId) || !validRestoreInput(payload)) {
    throw new InitialSetupError(422, "setup_invalid_request");
  }
  const body: InitialRestorePrepareInput = {
    setup_token: payload.setup_token, format: payload.format, identity: payload.identity,
    subscriber_totp_key: payload.subscriber_totp_key,
    confirm_replace_instance: true, confirm_trusted_backup: true,
  };
  return restorePrepared(await restoreRequest(`restore-uploads/${uploadId}/prepare`, {
    method: "POST", body: JSON.stringify(body), headers: {
      Accept: "application/json", "Content-Type": "application/json",
      "X-Open-Node-Client": "browser",
    },
  }, 200, fetcher));
}
