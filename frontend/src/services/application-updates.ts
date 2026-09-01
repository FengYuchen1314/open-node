import { applicationUpdateStatuses, type ApplicationUpdateAccepted, type ApplicationUpdateState } from "../domain/application-updates";
import { authenticatedFetch } from "./auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/application-update`;
const errors: Record<string, string> = {
  application_update_invalid_request: "更新请求不正确，请重新检查后再试。",
  application_update_unavailable: "当前部署没有可用的宿主机更新助手，请使用安装脚本更新。",
  application_update_busy: "已有更新操作正在处理，请等待当前操作完成。",
  application_update_target_changed: "目标版本已经变化，请重新检查更新。",
  application_update_rate_limited: "更新操作过于频繁，请稍后重试。",
  application_update_state_unavailable: "更新状态暂时不可用，请稍后重新读取。",
};
const unknown = "未能确认更新操作结果，请重新读取状态；不会自动重复提交。";

export class ApplicationUpdateRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(errors, code) ? code : null;
    super(status === 401 ? "管理员会话已失效，请重新登录。" : status === 403 ? "请求校验未通过，请重新登录后再试。" : safe ? errors[safe] : unknown);
    this.name = "ApplicationUpdateRequestError";
    this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

function invalid(): never { throw new ApplicationUpdateRequestError(null); }
function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid(); }
function record(value: unknown, keys: string[]) {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function identifier(value: unknown): string {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value) ? value : invalid();
}
function revision(value: unknown, allowUnknown = false): string {
  return typeof value === "string" && (/^[0-9a-f]{40}$/.test(value) || (allowUnknown && value === "unknown")) ? value : invalid();
}
function instant(value: unknown): string {
  return typeof value === "string" && value.length <= 40 && /(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)) ? value : invalid();
}
function optionalInstant(value: unknown) { return value === null ? null : instant(value); }
function message(value: unknown): string { return typeof value === "string" && value.length >= 1 && Array.from(value).length <= 200 ? value : invalid(); }

function state(value: unknown): ApplicationUpdateState {
  const row = record(value, ["schema_version", "managed", "status", "request_id", "current_revision", "latest_revision", "has_update", "checked_at", "started_at", "completed_at", "message", "release_url", "license_required"]);
  if (row.schema_version !== 1 || typeof row.managed !== "boolean" || !applicationUpdateStatuses.includes(row.status as never) || row.license_required !== false) return invalid();
  if (row.has_update !== null && typeof row.has_update !== "boolean") return invalid();
  const latest = row.latest_revision === null ? null : revision(row.latest_revision);
  const release = latest === null ? null : `https://github.com/FengYuchen1314/open-node/commit/${latest}`;
  if (row.release_url !== release) return invalid();
  return {
    schema_version: 1, managed: row.managed, status: row.status as ApplicationUpdateState["status"],
    request_id: row.request_id === null ? null : identifier(row.request_id),
    current_revision: revision(row.current_revision, true), latest_revision: latest,
    has_update: row.has_update, checked_at: optionalInstant(row.checked_at),
    started_at: optionalInstant(row.started_at), completed_at: optionalInstant(row.completed_at),
    message: message(row.message), release_url: release, license_required: false,
  };
}
function accepted(value: unknown): ApplicationUpdateAccepted {
  const row = record(value, ["accepted", "request_id", "action", "license_required"]);
  if (row.accepted !== true || !["check", "apply"].includes(String(row.action)) || row.license_required !== false) return invalid();
  return { accepted: true, request_id: identifier(row.request_id), action: row.action as "check" | "apply", license_required: false };
}
async function json(response: Response): Promise<unknown> {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) return invalid();
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength; if (size > 64 * 1024) { await reader.cancel(); return invalid(); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function request<T>(path: string, init: RequestInit, parser: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(base + path, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
    const body = await json(response);
    if (!response.ok) throw new ApplicationUpdateRequestError(response.status, object(body).code);
    return parser(body);
  } catch (error) { if (error instanceof ApplicationUpdateRequestError) throw error; return invalid(); }
}

export function getApplicationUpdate(fetcher = authenticatedFetch) { return request("", {}, state, fetcher); }
export function checkApplicationUpdate(fetcher = authenticatedFetch) { return request("/check", { method: "POST" }, accepted, fetcher); }
export function applyApplicationUpdate(targetRevision: string, fetcher = authenticatedFetch) {
  const target = revision(targetRevision);
  return request("/apply", { method: "POST", body: JSON.stringify({ target_revision: target, confirmed: true }) }, accepted, fetcher);
}
