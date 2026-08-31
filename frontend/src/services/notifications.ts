import {
  apiNotificationErrorCodes, notificationCodes, validNotificationChatId, validNotificationTimezone, validNotificationToken,
  type NotificationAttemptRead, type NotificationCandidate, type NotificationCode, type NotificationDeliveriesResponse,
  type NotificationDeliveryDetail, type NotificationDeliveryRead, type NotificationPreviewRead, type NotificationRetryRequest,
  type NotificationRevisionRequest, type NotificationSettingsRead, type NotificationSettingsUpdate, type NotificationState,
  type NotificationTestRequest,
} from "../domain/notifications";
import { authenticatedFetch } from "./auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/notifications`;
const unknownOutcome = "无法确认通知请求的结果。请先查询原请求，不要另建请求重复发送。";
const safeCodes = new Set<string>(notificationCodes);
const apiErrorCodes = new Set<string>(apiNotificationErrorCodes);
const messages: Record<NotificationCode, string> = {
  notification_invalid_request: "通知请求无效，请检查输入内容。",
  notification_revision_conflict: "通知设置已发生变化，请重新读取已保存配置。",
  notification_request_conflict: "此请求 ID 已用于另一项操作，请查询原请求。",
  notification_attempt_conflict: "投递尝试已发生变化，请刷新投递记录后确认。",
  notification_not_found: "未找到此通知投递，请刷新记录。",
  notification_request_not_found: "尚未找到此请求的回执。这不代表消息未发送，请稍后继续查询。",
  notification_not_configured: "发送前请先保存 Bot Token 和 Chat ID。",
  notification_disabled: "套餐临期提醒已关闭。",
  notification_storage_unavailable: "通知密钥存储不可用，请检查服务配置。",
  notification_storage_key_missing: "通知密钥缺失，请恢复原通知密钥后继续。",
  notification_storage_key_invalid: "通知密钥无法解密已保存配置，请核对数据库与密钥备份。",
  notification_storage_permissions: "通知密钥存储的权限或所有者不正确，请联系管理员检查。",
  notification_retry_not_allowed: "当前投递状态不允许重试，请刷新记录。",
  notification_retry_too_early: "上一次发送的等待期限尚未结束，请稍后刷新记录。",
  notification_duplicate_risk_required: "重试前须确认可能重复发送的风险。",
  notification_no_longer_eligible: "此套餐临期事件已不符合提醒条件。",
  notification_database_unavailable: "通知记录存储暂时不可用，请稍后查询当前状态。",
  notification_worker_interrupted: "通知执行已中断，发送结果尚未确认；不会自动重新发送。",
  notification_claim_expired: "任务领取期限已过，本次尚未发送；是否重试以当前投递状态为准。",
  notification_transport_failure: "通知发送发生异常，结果尚未确认；不会自动重新发送。",
  notification_attempt_expired: "发送尝试已超过等待期限，结果尚未确认；不会自动重新发送。",
  notification_invalid_response: "未收到有效的发送回执，结果尚未确认。",
  notification_already_accepted: "已存在 Telegram 接受回执，尚未发送的重复队列已取消。",
  telegram_accepted: "Telegram 已接受消息，不代表收件人已读。",
  telegram_invalid_token: "Bot Token 格式无效，请重新配置。",
  telegram_invalid_chat_id: "Chat ID 无效，请核对保存的目标。",
  telegram_invalid_text: "通知正文不符合发送要求。",
  telegram_tls_failed: "Telegram TLS 验证失败，请检查服务器网络与证书。",
  telegram_bad_request: "Telegram 拒绝了请求，请检查通知配置。",
  telegram_unauthorized: "Telegram 未通过身份验证，请检查 Bot Token。",
  telegram_forbidden: "Telegram 禁止向此目标发送，请检查机器人权限。",
  telegram_rejected: "Telegram 拒绝了此次发送。",
  telegram_connect_timeout: "连接 Telegram 超时，请等待投递状态更新。",
  telegram_connect_failed: "未能建立 Telegram 连接，请等待投递状态更新。",
  telegram_rate_limited: "Telegram 要求等待后再发送，请遵守投递记录中的等待时间。",
  telegram_send_timeout: "发送请求时超时，消息可能已经发出；结果尚未确认。",
  telegram_response_timeout: "等待 Telegram 回执超时，消息可能已经发出；结果尚未确认。",
  telegram_connection_lost: "连接中断，消息可能已经发出；结果尚未确认。",
  telegram_redirect_blocked: "Telegram 返回了不允许的重定向，发送结果尚未确认。",
  telegram_server_error: "Telegram 服务返回异常，发送结果尚未确认。",
  telegram_invalid_response: "未收到有效的 Telegram 接受回执，发送结果尚未确认。",
  telegram_response_too_large: "Telegram 回应超出大小限制，发送结果尚未确认。",
  telegram_transport_failure: "Telegram 传输发生异常，发送结果尚未确认。",
  notification_unknown_error: "通知操作发生异常，请查询当前投递状态。",
};

export function notificationCodeMessage(code: unknown): string {
  return typeof code === "string" && Object.hasOwn(messages, code) ? messages[code as NotificationCode] : messages.notification_unknown_error;
}
function errorMessage(status: number | null, code: unknown): string {
  if (typeof code === "string" && apiErrorCodes.has(code)) return notificationCodeMessage(code);
  switch (status) {
    case 401: return "请重新登录管理员账户后管理通知。";
    case 403: return "此操作需要管理员权限和有效的请求验证。";
    case 404: return "尚未找到通知记录，请查询原请求；未找到不代表未发送。";
    case 409: return "通知配置或投递状态已变化，请刷新后核实原请求。";
    case 413: return "通知请求超出大小限制。";
    case 415: return "通知请求必须使用 JSON。";
    case 422: return messages.notification_invalid_request;
    case 429: return "通知请求过于频繁，请稍后查询原请求。";
    default: return unknownOutcome;
  }
}
export class NotificationRequestError extends Error {
  readonly status: number | null;
  readonly code: NotificationCode | null;
  constructor(status: number | null, code?: unknown) {
    const safe = typeof code === "string" && apiErrorCodes.has(code) ? code as NotificationCode : null;
    super(errorMessage(status, safe));
    this.name = "NotificationRequestError"; this.status = status; this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}
export function notificationErrorMessage(error: unknown): string {
  // Even a mutated Error.message or an unexpected Chinese provider error is not displayable.
  return error instanceof NotificationRequestError ? errorMessage(error.status, error.code) : unknownOutcome;
}

function invalid(): never { throw new NotificationRequestError(null); }
function invalidInput(): never { throw new NotificationRequestError(422, "notification_invalid_request"); }
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
}
function string(value: unknown, multiline = false): string {
  if (typeof value !== "string" || value.length > 4096 || (multiline ? /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/u : /[\u0000-\u001f\u007f-\u009f]/u).test(value)) invalid();
  return value;
}
function displayName(value: unknown): string {
  // Existing user/plan names can contain whitespace and control characters.
  // Keep bounded display metadata intact for React text rendering, never error text or API identifiers.
  return typeof value === "string" && value.length <= 4096 ? value : invalid();
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : invalid();
}
function boolean(value: unknown): boolean { return typeof value === "boolean" ? value : invalid(); }
function free(value: unknown): false { return value === false ? false : invalid(); }
function array<T>(value: unknown, parse: (row: unknown) => T, maximum: number): T[] {
  return Array.isArray(value) && value.length <= maximum ? value.map(parse) : invalid();
}
const isUuid = (value: unknown): value is string => typeof value === "string" && value.length === 36 && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function uuid(value: unknown): string { return isUuid(value) ? value : invalid(); }
function instant(value: unknown): string {
  const text = string(value);
  return text.length <= 64 && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(text) && Number.isFinite(Date.parse(text)) ? text : invalid();
}
function nullable<T>(value: unknown, parse: (row: unknown) => T): T | null { return value === null ? null : parse(value); }
function code(value: unknown): NotificationCode | null {
  if (value === null) return null;
  if (typeof value !== "string") invalid();
  return safeCodes.has(value) ? value as NotificationCode : "notification_unknown_error";
}
function chat(value: unknown, allowEmpty = false): string {
  const text = string(value); return validNotificationChatId(text, allowEmpty) ? text : invalid();
}
function timezone(value: unknown): string {
  const text = string(value); return validNotificationTimezone(text) ? text : invalid();
}
function localTime(value: unknown): "09:00" { return value === "09:00" ? value : invalid(); }
function settings(value: unknown): NotificationSettingsRead {
  const row = record(value);
  return { revision: integer(row.revision), enabled: boolean(row.enabled), has_token: boolean(row.has_token), chat_id: chat(row.chat_id, true),
    advance_days: integer(row.advance_days, 1, 365), timezone: timezone(row.timezone), local_time: localTime(row.local_time),
    destination_revision: integer(row.destination_revision), storage_ready: boolean(row.storage_ready), storage_error: code(row.storage_error), license_required: free(row.license_required) };
}
function candidate(value: unknown): NotificationCandidate {
  const row = record(value); return { username: displayName(row.username), plan_id: uuid(row.plan_id), plan_name: displayName(row.plan_name), expires_at: instant(row.expires_at) };
}
function preview(value: unknown, revision: number): NotificationPreviewRead {
  const row = record(value);
  const result = { revision: integer(row.revision), as_of: instant(row.as_of), timezone: timezone(row.timezone), local_time: localTime(row.local_time),
    enabled: boolean(row.enabled), chat_id: chat(row.chat_id, true), total: integer(row.total), candidates: array(row.candidates, candidate, 20),
    sample_message: string(row.sample_message, true), is_sample: boolean(row.is_sample), license_required: free(row.license_required) };
  if (result.revision !== revision || result.candidates.length > result.total || (result.is_sample && result.total !== 0)) invalid();
  return result;
}
function state(value: unknown): NotificationState {
  return ["queued", "sending", "accepted", "failed", "unknown", "cancelled"].includes(String(value)) && typeof value === "string" ? value as NotificationState : invalid();
}
function delivery(value: unknown, expectedId?: string): NotificationDeliveryRead {
  const row = record(value), id = uuid(row.id), kind = row.kind;
  if ((expectedId && id !== expectedId) || (kind !== "test" && kind !== "package_expiry")) invalid();
  return { id, kind, state: state(row.state), config_revision: integer(row.config_revision), destination_revision: integer(row.destination_revision),
    request_id: nullable(row.request_id, uuid), chat_id: chat(row.chat_id), username: nullable(row.username, displayName), plan_id: nullable(row.plan_id, uuid),
    plan_name: nullable(row.plan_name, displayName), expires_at: nullable(row.expires_at, instant), last_attempt_id: nullable(row.last_attempt_id, uuid),
    attempt_count: integer(row.attempt_count), created_at: instant(row.created_at), updated_at: instant(row.updated_at),
    next_attempt_at: nullable(row.next_attempt_at, instant), retry_available_at: nullable(row.retry_available_at, instant),
    manual_retry_allowed: boolean(row.manual_retry_allowed), code: code(row.code), message_id: nullable(row.message_id, value => integer(value, 1)), license_required: free(row.license_required) };
}
function attempt(value: unknown, deliveryId: string): NotificationAttemptRead {
  const row = record(value), parsedState = state(row.state);
  if (row.delivery_id !== deliveryId || parsedState === "queued" || parsedState === "cancelled") invalid();
  return { id: uuid(row.id), delivery_id: deliveryId, state: parsedState, attempt_number: integer(row.attempt_number, 1),
    config_revision: integer(row.config_revision), destination_revision: integer(row.destination_revision), chat_id: chat(row.chat_id),
    started_at: instant(row.started_at), deadline_at: instant(row.deadline_at), finished_at: nullable(row.finished_at, instant),
    code: code(row.code), message_id: nullable(row.message_id, value => integer(value, 1)), retry_after: nullable(row.retry_after, value => integer(value, 1, 86400)),
    retryable: boolean(row.retryable), late_receipt_at: nullable(row.late_receipt_at ?? null, instant) };
}
function detail(value: unknown, expectedId?: string): NotificationDeliveryDetail {
  const row = record(value), entry = delivery(row.delivery, expectedId);
  return { delivery: entry, attempts: array(row.attempts, value => attempt(value, entry.id), 1000), license_required: free(row.license_required) };
}

async function json(response: Response): Promise<unknown> {
  const reader = response.body?.getReader(); if (!reader) return invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) { const next = await reader.read(); if (next.done) break; size += next.value.byteLength; if (size > 262144) { await reader.cancel(); return invalid(); } chunks.push(next.value); }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } finally { reader.releaseLock(); }
}
async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  const controller = new AbortController(), timer = globalThis.setTimeout(() => controller.abort(), 15000);
  try {
    let response: Response;
    try { response = await fetcher(`${base}${path}`, { ...init, signal: controller.signal,
      headers: { Accept: "application/json", ...(init.body === undefined ? {} : { "Content-Type": "application/json" }) },
      cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" }); }
    catch { throw new NotificationRequestError(null); }
    if (!response.ok) {
      const body = await json(response).catch(() => null);
      const safe = body !== null && typeof body === "object" && !Array.isArray(body) && "code" in body ? body.code : undefined;
      throw new NotificationRequestError(response.status, safe);
    }
    try { return parse(await json(response)); } catch { throw new NotificationRequestError(null); }
  } finally { globalThis.clearTimeout(timer); }
}
function revision(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : invalidInput();
}
function identifier(value: string): string { return isUuid(value) ? encodeURIComponent(value) : invalidInput(); }
const write = (method: string, body: object): RequestInit => ({ method, body: JSON.stringify(body) });

export function getNotificationSettings(fetcher = authenticatedFetch): Promise<NotificationSettingsRead> {
  return request("/settings", {}, settings, fetcher);
}
export async function updateNotificationSettings(payload: NotificationSettingsUpdate, fetcher = authenticatedFetch): Promise<NotificationSettingsRead> {
  const { enabled, chat_id, advance_days, timezone, local_time, token_action } = payload;
  if (typeof enabled !== "boolean" || typeof chat_id !== "string" || !validNotificationChatId(chat_id) || !Number.isSafeInteger(advance_days) || advance_days < 1 || advance_days > 365
    || !validNotificationTimezone(timezone) || local_time !== "09:00" || !["keep", "replace", "clear"].includes(token_action)
    || (enabled && (!chat_id || token_action === "clear")) || (token_action === "replace" && (typeof payload.token !== "string" || !validNotificationToken(payload.token)))) invalidInput();
  return request("/settings", write("PUT", { expected_revision: revision(payload.expected_revision), enabled, chat_id, advance_days, timezone, local_time, token_action,
    ...(token_action === "replace" ? { token: payload.token } : {}) }), settings, fetcher);
}
export async function previewNotifications(payload: NotificationRevisionRequest, fetcher = authenticatedFetch): Promise<NotificationPreviewRead> {
  const expected_revision = revision(payload.expected_revision);
  return request("/preview", write("POST", { expected_revision }), value => preview(value, expected_revision), fetcher);
}
export async function testNotification(payload: NotificationTestRequest, fetcher = authenticatedFetch): Promise<NotificationDeliveryDetail> {
  identifier(payload.request_id);
  return request("/test", write("POST", { expected_revision: revision(payload.expected_revision), request_id: payload.request_id }), value => {
    const result = detail(value); if (result.delivery.kind !== "test" || result.delivery.request_id !== payload.request_id) invalid(); return result;
  }, fetcher);
}
export async function listNotificationDeliveries(limit = 50, fetcher = authenticatedFetch): Promise<NotificationDeliveriesResponse> {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 50) invalidInput();
  return request(`/deliveries?limit=${limit}`, {}, value => { const row = record(value); return { deliveries: array(row.deliveries, value => delivery(value), limit), license_required: free(row.license_required) }; }, fetcher);
}
export async function getNotificationDelivery(id: string, fetcher = authenticatedFetch): Promise<NotificationDeliveryDetail> {
  return request(`/deliveries/${identifier(id)}`, {}, value => detail(value, id), fetcher);
}
export async function getNotificationRequest(requestId: string, fetcher = authenticatedFetch): Promise<NotificationDeliveryRead> {
  // A retry UUID maps to the original delivery; delivery.request_id can differ.
  return request(`/requests/${identifier(requestId)}`, {}, value => delivery(value), fetcher);
}
export async function retryNotificationDelivery(id: string, payload: NotificationRetryRequest, fetcher = authenticatedFetch): Promise<NotificationDeliveryDetail> {
  identifier(payload.request_id); identifier(payload.expected_attempt_id);
  if (payload.confirm_duplicate_risk !== true) invalidInput();
  return request(`/deliveries/${identifier(id)}/retry`, write("POST", { expected_revision: revision(payload.expected_revision), request_id: payload.request_id,
    expected_attempt_id: payload.expected_attempt_id, confirm_duplicate_risk: true }), value => detail(value, id), fetcher);
}
