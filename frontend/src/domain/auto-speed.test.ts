import { describe, expect, it } from "vitest";
import { newAutoSpeedRule, validAutoSpeedRule } from "./auto-speed";

describe("automatic speed rules", () => {
  it("accepts sustained and burst rules without sharing defaults", () => {
    const first = newAutoSpeedRule();
    expect(validAutoSpeedRule(first)).toBe(true);
    first.type = "burst";
    expect(validAutoSpeedRule(first)).toBe(true);
    expect(newAutoSpeedRule().type).toBe("sustained");
  });
  it.each([
    { threshold_mbps: 0 }, { limit_mbps: 0.0000001 }, { limit_mbps: Infinity },
    { threshold_mbps: NaN }, { limit_mbps: 1e20 }, { sustained_seconds: 0 },
    { sustained_seconds: 1.5 }, { limit_duration: 86401 }, { burst_count: -1 },
    { window_seconds: -1 }, { window_seconds: 86401 },
    { type: "burst" as const, window_seconds: 1 }, { type: "burst" as const, burst_count: 0 },
  ])("rejects invalid parameters", changes => {
    expect(validAutoSpeedRule({ ...newAutoSpeedRule(), ...changes })).toBe(false);
  });
  it("accepts the minimum representable rate and inclusive limits", () => {
    expect(validAutoSpeedRule({ ...newAutoSpeedRule(), limit_mbps: 1 / 125000,
      threshold_mbps: 2 ** 50 / 125000, sustained_seconds: 86400, limit_duration: 86400 })).toBe(true);
  });
});
