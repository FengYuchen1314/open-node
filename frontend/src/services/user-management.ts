import { authenticatedFetch } from "./auth";
import { userPath } from "./user-path";
import type { AgentCommand } from "../domain/inventory";
import type { ProductUser, SubscriptionAccessResponse } from "../domain/subscriptions";
import { copyUserLimits, type UserLimitOverrides, type UserLimitsRead } from "../domain/user-limits";

export type UserOperation = "edit" | "remove";
export interface UserSettings {
  display_name: string;
  email: string | null;
  remark: string;
  is_active: boolean;
  limit_overrides: UserLimitOverrides;
}
export interface UserManagementRead {
  user: ProductUser;
  revision: string;
  credential_count: number;
  blockers: string[];
  warnings: string[];
  access: SubscriptionAccessResponse;
  limits: UserLimitsRead;
}
export interface UserManagementResult extends UserManagementRead {
  commands: AgentCommand[];
}
export interface UserRemoval {
  id: string;
  username: string;
  status: "pending" | "failed" | "completed";
  requested_at: string;
  completed_at: string | null;
  servers: SubscriptionAccessResponse["servers"];
  warnings: string[];
  commands: AgentCommand[];
}

export function userSettings(user: ProductUser): UserSettings {
  return { display_name: user.display_name, email: user.email ?? null, remark: user.remark ?? "", is_active: user.is_active, limit_overrides: copyUserLimits(user.limit_overrides) };
}
const base = import.meta.env.VITE_API_BASE_URL ?? "";
async function request<T>(url: string, init?: RequestInit, fetcher = authenticatedFetch): Promise<T> {
  const response = await fetcher(`${base}/api/v1${url}`, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: unknown[]; msg?: string }) => `${item.loc?.slice(1).join(".") ?? ""}: ${item.msg ?? "Invalid value"}`).join("; ") : "";
    throw new Error(message || `User request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export function getUserManagement(username: string, fetcher = authenticatedFetch) {
  return request<UserManagementRead>(userPath(username, "settings"), undefined, fetcher);
}
export function saveUser(username: string, settings: UserSettings, revision: string, fetcher = authenticatedFetch) {
  return request<UserManagementResult>(userPath(username, "settings"), {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...settings, expected_revision: revision, acknowledge_runtime_restart: true }),
  }, fetcher);
}
export function removeUser(username: string, revision: string, name: string, unmanaged: boolean, fetcher = authenticatedFetch) {
  return request<UserRemoval>(userPath(username, "remove"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: revision, confirm_name: name, acknowledge_runtime_restart: true, acknowledge_unmanaged_credentials: unmanaged }),
  }, fetcher);
}
export function getUserRemoval(id: string, fetcher = authenticatedFetch) {
  return request<UserRemoval>(`/user-removals/${encodeURIComponent(id)}`, undefined, fetcher);
}
export function retryUserRemoval(id: string, fetcher = authenticatedFetch) {
  return request<UserRemoval>(`/user-removals/${encodeURIComponent(id)}/retry`, { method: "POST" }, fetcher);
}
