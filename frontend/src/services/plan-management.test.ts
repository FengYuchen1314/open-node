import { describe, expect, it } from "vitest";
import { getPlanManagement, planSettings, removePlan, savePlan } from "./plan-management";
import type { SubscriptionPlan } from "../domain/subscriptions";
import { newAutoSpeedRule } from "../domain/auto-speed";

describe("plan management", () => {
  const plan = { id: "id", name: "Plan", description: "", traffic_limit_gb: 1, cycle_days: 30,
    is_reset: true, reset_day: 31, node_ids: ["node"], node_multipliers: { node: 2 },
    node_name_overrides: { node: "Tokyo" }, node_name_override_enabled: true,
    auto_speed_rules: [newAutoSpeedRule()],
    node_speed_limits: { node: 0 }, node_device_limits: { node: 4 }, speed_limit_mbps: 10,
    device_limit: 1, traffic_mode: "twoway", created_at: "old", updated_at: "now", traffic_limit_bytes: 1024 ** 3 } as SubscriptionPlan;
  it("preserves settings and zero-valued overrides without sending read-only fields", () => {
    const settings = planSettings(plan);
    settings.node_speed_limits.node = 1;
    expect(plan.node_speed_limits.node).toBe(0);
    expect(settings).not.toHaveProperty("id");
    expect(settings.reset_day).toBe(31);
    expect(settings.node_device_limits).toEqual({ node: 4 });
    settings.node_name_overrides.node = "Osaka";
    expect(plan.node_name_overrides.node).toBe("Tokyo");
    expect(settings.node_name_override_enabled).toBe(true);
    expect(settings.clash_template_id).toBeNull();
    expect(settings.surge_template_id).toBeNull();
    settings.auto_speed_rules[0].limit_mbps = 20;
    expect(plan.auto_speed_rules[0].limit_mbps).toBe(10);
  });
  it("uses revisions and explicit runtime acknowledgment for changes", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify({ plan, revision: "r" }));
    };
    await getPlanManagement("id", "edit", fetcher);
    await savePlan("id", planSettings(plan), "r", fetcher);
    await removePlan("id", "remove", "r", "Plan", fetcher);
    await getPlanManagement("a+b", "unassign", fetcher);
    await removePlan("a+b", "unassign", "r", "a+b", fetcher);
    expect(calls.map(([url]) => url)).toEqual(["/api/v1/plans/id/settings", "/api/v1/plans/id/settings", "/api/v1/plans/id/remove", "/api/v1/users/a%2Bb/plan/removal", "/api/v1/users/a%2Bb/plan/remove"]);
    expect(JSON.parse(String(calls[1][1]?.body))).toMatchObject({ expected_revision: "r", acknowledge_runtime_restart: true, node_name_overrides: { node: "Tokyo" }, node_name_override_enabled: true });
    expect(JSON.parse(String(calls[4][1]?.body))).toEqual({ expected_revision: "r", acknowledge_runtime_restart: true, confirm_name: "a+b" });
  });
  it("keeps conflict and validation details visible", async () => {
    await expect(getPlanManagement("id", "edit", async () => new Response(JSON.stringify({ detail: "Reload first" }), { status: 409 }))).rejects.toThrow("请先重新加载。");
    await expect(getPlanManagement("id", "edit", async () => new Response(JSON.stringify({ detail: [{ loc: ["body", "name"], msg: "Required" }] }), { status: 422 }))).rejects.toThrow("name: 此项必填。");
  });
});
