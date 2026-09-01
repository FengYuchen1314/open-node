import {
  validPermissionQuota,
  validPermissionRevision,
  validSubscriberFeatures,
  type SubscriberPermissionsAccount,
  type SubscriberPermissionsSettings,
  type SubscriberPermissionsUpdate,
  type SubscriberQuotaUsage,
} from "../domain/subscriber-permissions";
import { authenticatedFetch } from "./auth";
import { accountRequest } from "./subscriber-auth";

const path = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/subscriber-permissions`;
const messages: Record<string, string> = {
  subscriber_permissions_invalid_request: "请检查用户功能和数量上限。",
  subscriber_permissions_revision_conflict: "用户权限已被其他操作修改，请重新读取。",
  subscriber_permissions_storage_unavailable: "用户权限暂时不可用，请稍后重新读取。",
  subscriber_feature_disabled: "管理员未开放此账户功能。",
  subscriber_quota_exceeded: "已达到管理员设置的数量上限。",
};
const unknown = "未能确认用户权限设置，请重新读取。";

export class SubscriberPermissionsRequestError extends Error {
  readonly code: string | null;
  constructor(readonly status: number | null, code?: unknown) {
    const safe = typeof code === "string" && Object.hasOwn(messages, code) ? code : null;
    super(safe ? messages[safe] : unknown); this.code = safe;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

function invalid(): never { throw new SubscriberPermissionsRequestError(null); }
function record(value: unknown, keys: string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return invalid();
  return row;
}
function settings(value: unknown): SubscriberPermissionsSettings {
  const row = record(value, ["revision", "pages", "template_quota", "external_source_quota", "license_required"]);
  if (!validPermissionRevision(row.revision) || !validSubscriberFeatures(row.pages)
    || !validPermissionQuota(row.template_quota) || !validPermissionQuota(row.external_source_quota)
    || row.license_required !== false) return invalid();
  return row as unknown as SubscriberPermissionsSettings;
}
function usage(value: unknown): SubscriberQuotaUsage {
  const row = record(value, ["used", "maximum"]);
  if (!validPermissionRevision(row.used) || !validPermissionQuota(row.maximum)) return invalid();
  return row as unknown as SubscriberQuotaUsage;
}
function account(value: unknown): SubscriberPermissionsAccount {
  const row = record(value, ["pages", "templates", "external_sources", "license_required"]);
  if (!validSubscriberFeatures(row.pages) || row.license_required !== false) return invalid();
  return { pages: row.pages, templates: usage(row.templates), external_sources: usage(row.external_sources), license_required: false };
}
async function adminRequest(init: RequestInit, fetcher: typeof fetch) {
  try {
    const response = await fetcher(path, { ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) } });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new SubscriberPermissionsRequestError(response.status, body?.code);
    return settings(body);
  } catch (error) {
    if (error instanceof SubscriberPermissionsRequestError) throw error;
    return invalid();
  }
}
export function getSubscriberPermissions(fetcher = authenticatedFetch) {
  return adminRequest({}, fetcher);
}
export function updateSubscriberPermissions(value: SubscriberPermissionsUpdate, fetcher = authenticatedFetch) {
  if (!validPermissionRevision(value.expected_revision) || !validSubscriberFeatures(value.pages)
    || !validPermissionQuota(value.template_quota) || !validPermissionQuota(value.external_source_quota)
    || value.license_required !== false) return Promise.reject(new SubscriberPermissionsRequestError(422, "subscriber_permissions_invalid_request"));
  return adminRequest({ method: "PUT", body: JSON.stringify(value) }, fetcher);
}
export async function getAccountSubscriberPermissions(fetcher = fetch) {
  return account(await accountRequest<unknown>("permissions", {}, fetcher));
}
