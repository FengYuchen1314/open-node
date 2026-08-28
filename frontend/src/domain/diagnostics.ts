import type { AgentReturnRouteTarget } from "./inventory";

export const diagnosticPaths = new Set([
  "/api/child/domains/latency",
  "/api/child/network/return-route-test",
  "/api/child/logs",
  "/api/child/logs/files",
]);

export function routeTargets(): AgentReturnRouteTarget[] {
  return (["telecom", "unicom", "mobile"] as const).map(carrier => ({
    carrier, host: "", region: "", port: 80,
  }));
}

export function selectedRouteTargets(targets: AgentReturnRouteTarget[]) {
  return targets.filter(target => target.host.trim()).map(target => ({
    ...target, host: target.host.trim(), region: (target.region ?? "").trim(),
    port: Number(target.port ?? 80),
  }));
}

export function latencyCommandTimeout(count: number, timeout: number, icmp: boolean) {
  return Math.min(300_000, Math.max(30_000,
    Math.ceil(count / 16) * (timeout * (icmp ? 2 : 1) + 250) + 5000));
}

export function resultObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function resultRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(resultObject) : [];
}
