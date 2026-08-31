import {
  backupErrorCodes, backupStatuses, validBackupId, validBackupRecipient,
  type BackupCreateRequest, type BackupDisplayCode, type BackupErrorCode,
  type BackupJob, type BackupsOverview, type BackupStatus,
  type RestoreReviewRequest, type RestoreStatus,
} from "../domain/backups";
import { authenticatedFetch } from "./auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/backups`;
const codes = new Set<string>(backupErrorCodes);
const uncertain = "未能确认备份请求的结果，请查询原请求；不会自动重新创建。";
const messages: Record<BackupDisplayCode, string> = {
  backup_not_found: "未找到此备份任务。若创建回执丢失，这不代表任务从未执行，请保留原请求 ID 查询。",
  backup_busy: "已有备份任务或停写操作正在进行，请稍后刷新。",
  backup_not_ready: "备份尚未准备好，当前不能下载。",
  backup_request_conflict: "此请求 ID 已对应另一项备份请求，请查询原任务。",
  backup_worker_unavailable: "备份服务当前不可用，请检查服务器配置后刷新。",
  backup_authorization_expired: "备份授权已失效，或密码、验证码不正确，请重新验证。",
  backup_creation_failed: "备份创建失败，没有可下载的备份文件。请检查服务器配置后重试。",
  backup_expired: "此备份已过期，不能继续下载。",
  backup_invalid_request: "备份请求无效，请检查公钥和身份验证信息。",
  backup_rate_limited: "备份身份验证请求过于频繁，请稍后再试；不会自动重新提交。",
  backup_unknown_error: "备份状态暂时无法确认，请刷新任务状态。",
};

export function backupCodeMessage(value: unknown): string {
  return typeof value === "string" && Object.hasOwn(messages, value)
    ? messages[value as BackupDisplayCode] : messages.backup_unknown_error;
}
function message(status: number | null, code: BackupErrorCode | null): string {
  if (status === 401) return "请重新登录管理员账户后管理备份。";
  if (code) return backupCodeMessage(code);
  if (status === 403) return "身份验证或请求校验未通过，请重新登录后确认。";
  if (status === 429) return "备份请求过于频繁，请稍后查询原请求。";
  return uncertain;
}
export class BackupRequestError extends Error {
  readonly status: number | null;
  readonly code: BackupErrorCode | null;
  constructor(status: number | null, code?: unknown) {
    const safe = typeof code === "string" && codes.has(code) ? code as BackupErrorCode : null;
    super(message(status, safe)); this.name = "BackupRequestError"; this.status = status; this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500 || this.code === "backup_request_conflict"; }
}
export const backupErrorMessage = (error: unknown) => error instanceof BackupRequestError
  ? message(error.status, error.code) : uncertain;
function invalid(): never { throw new BackupRequestError(null); }
function invalidInput(): never { throw new BackupRequestError(422, "backup_invalid_request"); }
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
}
function code(value: unknown): BackupDisplayCode | null {
  if (value === null) return null;
  if (typeof value !== "string" || value.length > 128) return invalid();
  return codes.has(value) ? value as BackupErrorCode : "backup_unknown_error";
}
function instant(value: unknown): string {
  return typeof value === "string" && value.length <= 40
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
    && Number.isFinite(Date.parse(value)) ? value : invalid();
}
function job(value: unknown, expectedId?: string): BackupJob {
  const row = record(value);
  if (!validBackupId(row.id) || (expectedId && row.id !== expectedId)
    || typeof row.status !== "string" || !(backupStatuses as readonly string[]).includes(row.status)
    || row.restoration_ready !== false) return invalid();
  const size = row.size === null ? null : typeof row.size === "number" && Number.isSafeInteger(row.size) && row.size > 0 ? row.size : invalid();
  const sha256 = row.sha256 === null ? null : typeof row.sha256 === "string" && /^[0-9a-f]{64}$/.test(row.sha256) ? row.sha256 : invalid();
  if (row.status === "ready" && (size === null || sha256 === null)) return invalid();
  const result: BackupJob = {
    id: row.id, status: row.status as BackupStatus, created_at: instant(row.created_at), expires_at: instant(row.expires_at),
    size, sha256, error_code: code(row.error_code), restoration_ready: false,
  };
  if (Date.parse(result.expires_at) < Date.parse(result.created_at)) return invalid();
  return result;
}
function overview(value: unknown): BackupsOverview {
  const row = record(value);
  if (typeof row.available !== "boolean" || typeof row.requires_two_factor !== "boolean"
    || row.max_completed !== 2 || row.ttl_seconds !== 900 || row.restoration_supported !== false
    || !Array.isArray(row.jobs) || row.jobs.length > 64) return invalid();
  const jobs = row.jobs.map(value => job(value));
  if (new Set(jobs.map(item => item.id)).size !== jobs.length) return invalid();
  return { available: row.available, unavailable_code: code(row.unavailable_code), jobs,
    max_completed: 2, ttl_seconds: 900, requires_two_factor: row.requires_two_factor, restoration_supported: false,
    ...(row.offline_restoration_supported === true ? { offline_restoration_supported: true as const } : {}),
    ...(row.recovery === undefined ? {} : { recovery: restoreStatus(row.recovery) }) };
}
function restoreStatus(value: unknown): RestoreStatus {
  const row = record(value);
  if (typeof row.blocked !== "boolean" || typeof row.restart_required !== "boolean") return invalid();
  if (row.record === null) {
    if (row.blocked || row.restart_required) return invalid();
    return { blocked: false, restart_required: false, record: null };
  }
  const detail = record(row.record);
  if (detail.version !== 1 || !validBackupId(detail.id)
    || !["review_required", "reviewed"].includes(detail.status as string)
    || typeof detail.archive_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(detail.archive_sha256)) return invalid();
  for (const key of ["invalidated_sessions", "cancelled_agent_commands", "cancelled_certificate_jobs", "quarantined_files"]) {
    if (typeof detail[key] !== "number" || !Number.isSafeInteger(detail[key]) || detail[key] < 0) return invalid();
  }
  const result: RestoreStatus = { blocked: row.blocked, restart_required: row.restart_required, record: {
    version: 1, id: detail.id, status: detail.status as "review_required" | "reviewed",
    created_at: instant(detail.created_at), archive_sha256: detail.archive_sha256,
    invalidated_sessions: detail.invalidated_sessions as number,
    cancelled_agent_commands: detail.cancelled_agent_commands as number,
    cancelled_certificate_jobs: detail.cancelled_certificate_jobs as number,
    quarantined_files: detail.quarantined_files as number,
    reviewed_at: detail.reviewed_at === null ? null : instant(detail.reviewed_at),
  } };
  if (result.restart_required !== (result.blocked && detail.status === "reviewed")
    || (detail.status === "review_required" && !result.blocked)) return invalid();
  return result;
}
async function json(response: Response): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const item = await reader.read(); if (item.done) break;
      size += item.value.byteLength;
      if (size > 65536) { await reader.cancel(); return invalid(); }
      chunks.push(item.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function request<T>(path: string, init: RequestInit, status: number, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  const controller = new AbortController(), timer = globalThis.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetcher(path, { ...init, signal: controller.signal, cache: "no-store", redirect: "error", referrerPolicy: "no-referrer",
      headers: { Accept: "application/json", ...(init.body === undefined ? {} : { "Content-Type": "application/json" }) } });
    if (!response.ok) {
      const body = await json(response).catch(() => null);
      throw new BackupRequestError(response.status, body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>).code : null);
    }
    if (response.status !== status) return invalid();
    return parse(status === 204 ? null : await json(response));
  } catch (error) {
    if (error instanceof BackupRequestError) throw error;
    return invalid();
  } finally { globalThis.clearTimeout(timer); }
}

export const getBackups = (fetcher = authenticatedFetch): Promise<BackupsOverview> => request(base, {}, 200, overview, fetcher);
export const getRestoreStatus = (fetcher = authenticatedFetch): Promise<RestoreStatus> => request(`${base}/restore-review`, {}, 200, restoreStatus, fetcher);
export function reviewRestore(payload: RestoreReviewRequest, fetcher = authenticatedFetch): Promise<RestoreStatus> {
  if (!validBackupId(payload.id) || !payload.password || payload.password.length > 1024 || payload.code.length > 64
    || payload.confirm_original_stopped !== true || payload.confirm_configuration !== true || payload.confirm_trusted_backup !== true) return invalidInput();
  const body = { id: payload.id, password: payload.password, code: payload.code,
    confirm_original_stopped: true, confirm_configuration: true, confirm_trusted_backup: true };
  return request(`${base}/restore-review`, { method: "POST", body: JSON.stringify(body) }, 200, value => {
    const result = restoreStatus(value);
    if (result.record?.id !== payload.id || !result.restart_required) return invalid();
    return result;
  }, fetcher);
}
export function getBackupJob(id: string, fetcher = authenticatedFetch): Promise<BackupJob> {
  if (!validBackupId(id)) return invalidInput();
  return request(`${base}/${id}`, {}, 200, value => job(value, id), fetcher);
}
export function createBackup(payload: BackupCreateRequest, fetcher = authenticatedFetch): Promise<BackupJob> {
  if (!payload || !validBackupId(payload.request_id) || !validBackupRecipient(payload.recipient)
    || typeof payload.password !== "string" || !payload.password || payload.password.length > 1024
    || typeof payload.code !== "string" || payload.code.length > 64) return invalidInput();
  // Project exact fields; neither credentials nor rejected input enter an Error or URL.
  const body = { request_id: payload.request_id, recipient: payload.recipient, password: payload.password, code: payload.code };
  return request(base, { method: "POST", body: JSON.stringify(body) }, 202, value => job(value, body.request_id), fetcher);
}
export function deleteBackup(id: string, fetcher = authenticatedFetch): Promise<void> {
  if (!validBackupId(id)) return invalidInput();
  return request(`${base}/${id}`, { method: "DELETE" }, 204, () => undefined, fetcher);
}
export function newBackupRequestId(): string {
  try {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6]! & 15) | 64; bytes[8] = (bytes[8]! & 63) | 128;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  } catch { return invalid(); }
}
export function backupDownloadUrl(id: string): string {
  if (!validBackupId(id)) return invalidInput();
  const url = new URL(`${base}/${id}/download`, window.location.origin);
  if (url.origin !== window.location.origin || url.username || url.password) return invalidInput();
  return url.href;
}
