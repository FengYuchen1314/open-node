import { describe, expect, it } from "vitest";
import { copyUserLimits, maxSpeed, maxTraffic, validUserLimits } from "./user-limits";

describe("user limit overrides", () => {
  it("preserves explicit unlimited and copies both node maps", () => {
    const original = copyUserLimits({ traffic_limit_gb: 0, speed_limit_mbps: 0, device_limit: 0, node_speed_limits: { node: 0 }, node_device_limits: { node: 3 } });
    const edited = copyUserLimits(original);
    expect(edited).toEqual(original);
    edited.node_speed_limits.node = 20;
    delete edited.node_device_limits.node;
    expect(original.node_speed_limits.node).toBe(0);
    expect(original.node_device_limits.node).toBe(3);
  });
  it("inherits missing values without converting them to unlimited", () => {
    expect(copyUserLimits().traffic_limit_gb).toBeNull();
    expect(copyUserLimits().speed_limit_mbps).toBeNull();
    expect(validUserLimits(copyUserLimits())).toBe(true);
  });
  it.each([
    { traffic_limit_gb: -1 }, { traffic_limit_gb: Number.NaN }, { traffic_limit_gb: Infinity },
    { traffic_limit_gb: 1e-12 }, { traffic_limit_gb: maxTraffic + 1 },
    { speed_limit_mbps: -1 }, { speed_limit_mbps: maxSpeed + 1 }, { speed_limit_mbps: 1e-9 },
    { device_limit: 1.5 }, { device_limit: 1000001 },
    { node_speed_limits: { node: Number.NaN } }, { node_speed_limits: { node: 1e-10 } },
    { node_device_limits: { node: 0.5 } }, { node_device_limits: { node: -1 } },
  ])("blocks invalid limits before JSON serialization: %j", change => {
    expect(validUserLimits(copyUserLimits(change))).toBe(false);
  });
  it("accepts bounded values and zero-valued node overrides", () => {
    expect(validUserLimits(copyUserLimits({ traffic_limit_gb: maxTraffic, speed_limit_mbps: maxSpeed, device_limit: 1000000,
      node_speed_limits: { node: 0 }, node_device_limits: { node: 0 } }))).toBe(true);
  });
});
