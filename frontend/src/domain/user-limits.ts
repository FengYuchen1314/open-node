export interface UserLimitOverrides {
  traffic_limit_gb: number | null;
  speed_limit_mbps: number | null;
  device_limit: number | null;
  node_speed_limits: Record<string, number>;
  node_device_limits: Record<string, number>;
}
export type LimitSource = "user_node" | "user_parent" | "user" | "plan_node" | "plan_parent" | "plan" | "unlimited" | "shared";
export interface UserEffectiveLimits {
  speed_limit_mbps: number;
  device_limit: number;
  speed_source: LimitSource;
  device_source: LimitSource;
}
export interface UserNodeLimits extends UserEffectiveLimits { node_id: string; name: string; enabled: boolean }
export interface UserLimitsRead extends UserEffectiveLimits {
  traffic_limit_bytes: number;
  nodes: UserNodeLimits[];
  warnings: string[];
}
export const maxSpeed = 2 ** 50 / 125000;
export const maxTraffic = Number.MAX_SAFE_INTEGER / 1024 ** 3;

export function copyUserLimits(value?: Partial<UserLimitOverrides> | null): UserLimitOverrides {
  return {
    traffic_limit_gb: value?.traffic_limit_gb ?? null,
    speed_limit_mbps: value?.speed_limit_mbps ?? null,
    device_limit: value?.device_limit ?? null,
    node_speed_limits: { ...value?.node_speed_limits },
    node_device_limits: { ...value?.node_device_limits },
  };
}
export function validLimit(value: number | null, maximum: number, minimum: number, integer = false): boolean {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= maximum
    && (value === 0 || value >= minimum) && (!integer || Number.isInteger(value)));
}
export function validUserLimits(value?: UserLimitOverrides): boolean {
  return !value || (validLimit(value.traffic_limit_gb, maxTraffic, 1 / 1024 ** 3)
    && validLimit(value.speed_limit_mbps, maxSpeed, 1 / 125000)
    && validLimit(value.device_limit, 1000000, 1, true)
    && Object.keys(value.node_speed_limits).length <= 1000 && Object.keys(value.node_device_limits).length <= 1000
    && Object.values(value.node_speed_limits).every(item => item !== null && validLimit(item, maxSpeed, 1 / 125000))
    && Object.values(value.node_device_limits).every(item => item !== null && validLimit(item, 1000000, 1, true)));
}
export function limitSource(source: LimitSource): string {
  return { user_node: "User node", user_parent: "User parent", user: "User default", plan_node: "Plan node", plan_parent: "Plan parent", plan: "Plan default", unlimited: "Unlimited", shared: "Shared credential" }[source];
}
