export interface AutoSpeedRule {
  type: "sustained" | "burst";
  threshold_mbps: number;
  sustained_seconds: number;
  window_seconds: number;
  burst_count: number;
  limit_mbps: number;
  limit_duration: number;
}

export function validAutoSpeedRule(rule: AutoSpeedRule): boolean {
  const rate = (value: number) => Number.isFinite(value) && value * 125000 >= 1 && value * 125000 <= 2 ** 50;
  const integer = (value: number, min: number, max: number) => Number.isInteger(value) && value >= min && value <= max;
  return (rule.type === "sustained" || rule.type === "burst") && rate(rule.threshold_mbps) && rate(rule.limit_mbps)
    && integer(rule.sustained_seconds, 1, 86400) && integer(rule.limit_duration, 1, 86400)
    && integer(rule.window_seconds, 0, 86400) && integer(rule.burst_count, 0, 10000)
    && (rule.type !== "burst" || (rule.window_seconds >= rule.sustained_seconds && rule.burst_count >= 1));
}

export function newAutoSpeedRule(): AutoSpeedRule {
  return { type: "sustained", threshold_mbps: 50, sustained_seconds: 30,
    window_seconds: 300, burst_count: 3, limit_mbps: 10, limit_duration: 60 };
}
