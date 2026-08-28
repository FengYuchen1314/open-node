import { authenticatedFetch } from "./auth";
import { userPath } from "./user-path";
import type { AgentCommand } from "../domain/inventory";
import type { SubscriptionPlan, SubscriptionPlanCreateRequest } from "../domain/subscriptions";

export type PlanOperation = "edit" | "remove" | "unassign";
export type PlanSettings = Required<SubscriptionPlanCreateRequest>;
export interface PlanManagementRead {
  plan: SubscriptionPlan;
  revision: string;
  users: { username: string; display_name: string; is_active: boolean; managed: boolean }[];
  warnings: string[];
}
export interface PlanManagementResult {
  plan: SubscriptionPlan | null;
  revision: string | null;
  affected_users: string[];
  commands: AgentCommand[];
  warnings: string[];
}

export function planSettings(plan: SubscriptionPlan): PlanSettings {
  return {
    name: plan.name, description: plan.description, traffic_limit_gb: plan.traffic_limit_gb,
    cycle_days: plan.cycle_days, is_reset: plan.is_reset, reset_day: plan.reset_day,
    speed_limit_mbps: plan.speed_limit_mbps, device_limit: plan.device_limit,
    traffic_mode: plan.traffic_mode, node_ids: [...plan.node_ids],
    node_multipliers: { ...plan.node_multipliers }, node_speed_limits: { ...plan.node_speed_limits },
    node_device_limits: { ...plan.node_device_limits },
  };
}
const base = import.meta.env.VITE_API_BASE_URL ?? "";
function path(id: string, mode: PlanOperation, action: string) {
  return mode === "unassign" ? userPath(id, "plan/" + action) : `/plans/${encodeURIComponent(id)}/${action}`;
}
async function request<T>(url: string, init?: RequestInit, fetcher = authenticatedFetch): Promise<T> {
  const response = await fetcher(`${base}/api/v1${url}`, init);
  if (!response.ok) {
    const value = await response.json().catch(() => null);
    const detail = value?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((entry: { loc?: unknown[]; msg?: string }) => `${entry.loc?.slice(1).join(".") ?? ""}: ${entry.msg ?? "Invalid value"}`).join("; ") : "";
    throw new Error(message || `Plan request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export function getPlanManagement(id: string, mode: PlanOperation, fetcher = authenticatedFetch) {
  return request<PlanManagementRead>(path(id, mode, mode === "unassign" ? "removal" : "settings"), undefined, fetcher);
}
export function savePlan(id: string, settings: PlanSettings, revision: string, fetcher = authenticatedFetch) {
  return request<PlanManagementResult>(path(id, "edit", "settings"), {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...settings, expected_revision: revision, acknowledge_runtime_restart: true }),
  }, fetcher);
}
export function removePlan(id: string, mode: "remove" | "unassign", revision: string, name: string, fetcher = authenticatedFetch) {
  return request<PlanManagementResult>(path(id, mode, "remove"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: revision, confirm_name: name, acknowledge_runtime_restart: true }),
  }, fetcher);
}
