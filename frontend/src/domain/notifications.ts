/** The read contracts deliberately contain no bot token or token hint. */
export const apiNotificationErrorCodes = [
  "notification_invalid_request", "notification_revision_conflict", "notification_request_conflict",
  "notification_attempt_conflict", "notification_not_found", "notification_request_not_found",
  "notification_not_configured", "notification_disabled", "notification_storage_unavailable",
  "notification_storage_key_missing", "notification_storage_key_invalid", "notification_storage_permissions",
  "notification_retry_not_allowed", "notification_retry_too_early", "notification_duplicate_risk_required",
  "notification_no_longer_eligible", "notification_database_unavailable",
] as const;
export const notificationCodes = [
  ...apiNotificationErrorCodes, "notification_worker_interrupted",
  "notification_claim_expired", "notification_transport_failure", "notification_attempt_expired",
  "notification_invalid_response", "notification_already_accepted",
  "telegram_accepted", "telegram_invalid_token", "telegram_invalid_chat_id", "telegram_invalid_text",
  "telegram_tls_failed", "telegram_bad_request", "telegram_unauthorized", "telegram_forbidden", "telegram_rejected",
  "telegram_connect_timeout", "telegram_connect_failed", "telegram_rate_limited", "telegram_send_timeout",
  "telegram_response_timeout", "telegram_connection_lost", "telegram_redirect_blocked", "telegram_server_error",
  "telegram_invalid_response", "telegram_response_too_large", "telegram_transport_failure",
] as const;
export type NotificationCode = typeof notificationCodes[number] | "notification_unknown_error";
export type NotificationState = "queued" | "sending" | "accepted" | "failed" | "unknown" | "cancelled";
export type NotificationTokenAction = "keep" | "replace" | "clear";

export interface NotificationSettingsDraft {
  enabled: boolean;
  chat_id: string;
  advance_days: number;
  timezone: string;
  local_time: "09:00";
}
export interface NotificationRevisionRequest { expected_revision: number }
export interface NotificationSettingsUpdate extends NotificationSettingsDraft, NotificationRevisionRequest {
  token_action: NotificationTokenAction;
  /** Only replace accepts a token; neither keep nor clear reads it back. */
  token?: string | null;
}
export interface NotificationSettingsRead extends NotificationSettingsDraft {
  revision: number;
  has_token: boolean;
  destination_revision: number;
  storage_ready: boolean;
  storage_error: NotificationCode | null;
  license_required: false;
}
export interface NotificationCandidate {
  username: string;
  plan_id: string;
  plan_name: string;
  expires_at: string;
}
export interface NotificationPreviewRead {
  revision: number;
  as_of: string;
  timezone: string;
  local_time: "09:00";
  enabled: boolean;
  chat_id: string;
  total: number;
  candidates: NotificationCandidate[];
  sample_message: string;
  is_sample: boolean;
  license_required: false;
}
export interface NotificationTestRequest extends NotificationRevisionRequest { request_id: string }
export interface NotificationRetryRequest extends NotificationTestRequest {
  expected_attempt_id: string;
  confirm_duplicate_risk: boolean;
}
export interface NotificationDeliveryRead {
  id: string;
  kind: "package_expiry" | "test";
  state: NotificationState;
  config_revision: number;
  destination_revision: number;
  request_id: string | null;
  chat_id: string;
  username: string | null;
  plan_id: string | null;
  plan_name: string | null;
  expires_at: string | null;
  last_attempt_id: string | null;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  next_attempt_at: string | null;
  retry_available_at: string | null;
  manual_retry_allowed: boolean;
  code: NotificationCode | null;
  message_id: number | null;
  license_required: false;
}
export interface NotificationAttemptRead {
  id: string;
  delivery_id: string;
  state: "sending" | "accepted" | "failed" | "unknown";
  attempt_number: number;
  config_revision: number;
  destination_revision: number;
  chat_id: string;
  started_at: string;
  deadline_at: string;
  finished_at: string | null;
  code: NotificationCode | null;
  message_id: number | null;
  retry_after: number | null;
  retryable: boolean;
  late_receipt_at: string | null;
}
export interface NotificationDeliveryDetail {
  delivery: NotificationDeliveryRead;
  attempts: NotificationAttemptRead[];
  license_required: false;
}
export interface NotificationDeliveriesResponse { deliveries: NotificationDeliveryRead[]; license_required: false }

export const notificationDefaults: NotificationSettingsDraft = {
  enabled: false, chat_id: "", advance_days: 7, timezone: "Asia/Shanghai", local_time: "09:00",
};
export function validNotificationChatId(value: string, allowEmpty = true): boolean {
  if (typeof value !== "string") return false;
  if (value === "") return allowEmpty;
  if (/[\r\n]/.test(value) || !/^-?[1-9][0-9]{0,18}$/.test(value)) return false;
  const number = BigInt(value), maximum = (1n << 52n) - 1n;
  return number >= -maximum && number <= maximum;
}
export function validNotificationToken(value: string): boolean {
  return typeof value === "string" && !/[\r\n]/.test(value) && /^[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,128}$/.test(value);
}
export function validNotificationTimezone(value: string): boolean {
  if (typeof value !== "string" || value.length > 100 || !/^[A-Za-z][A-Za-z0-9_+-]*(?:\/[A-Za-z][A-Za-z0-9_+-]*)*$/.test(value)) return false;
  try { new Intl.DateTimeFormat("zh-CN", { timeZone: value }); return true; }
  catch { return false; }
}
