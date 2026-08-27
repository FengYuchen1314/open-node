import type { ProbeDailyTraffic, ProbePingSeries, ProbeServer } from "./probe";

export type ProbeStatusFilter = "all" | "online" | "offline" | "expiring" | "expired" | "renewal";

export interface ProbeRegionOption {
  code: string;
  label: string;
  online: number;
  total: number;
}

export interface ProbeHealth {
  score: number;
  label: "Excellent" | "Good" | "Attention" | "Critical";
  tone: "success" | "info" | "warning" | "error";
  issues: string[];
}

export interface ProbeTrafficHotspot {
  index: number;
  name: string;
  speed: number;
  share: number;
}

export interface ProbeLatencyBucket {
  ms: number;
  loss: number;
  level: "none" | "good" | "warning" | "critical";
}

export interface ProbeDailyTrafficSummary {
  date: string;
  uplink: number;
  downlink: number;
  total: number;
}

export function serverRegionKey(server: ProbeServer): string {
  const value =
    server.region_country?.trim() ||
    parseCountryCode(server.region) ||
    server.region?.trim() ||
    server.region_name?.trim() ||
    server.region_city?.trim() ||
    "UNKNOWN";
  return value.toUpperCase();
}

export function serverRegionLabel(server: ProbeServer): string {
  const region = server.region_city?.trim() || server.region_name?.trim() || server.region?.trim();
  const country = server.region_country?.trim();
  return [region, country].filter(Boolean).join(", ") || "No region";
}

export function buildRegionOptions(servers: ProbeServer[]): ProbeRegionOption[] {
  const grouped = new Map<string, { label: string; online: number; total: number }>();
  for (const server of servers) {
    const code = serverRegionKey(server);
    const current = grouped.get(code) ?? {
      label: serverRegionLabel(server),
      online: 0,
      total: 0,
    };
    current.total += 1;
    if (server.online) {
      current.online += 1;
    }
    grouped.set(code, current);
  }
  return Array.from(grouped, ([code, value]) => ({ code, ...value }));
}

export function filterProbeServers(
  servers: ProbeServer[],
  status: ProbeStatusFilter,
  region: string,
  nowMs = Date.now(),
): ProbeServer[] {
  return servers.filter((server) => {
    const statusMatches =
      status === "all" ||
      (status === "online" && server.online) ||
      (status === "offline" && !server.online) ||
      (status === "expiring" && isExpiring(server, nowMs)) ||
      (status === "expired" && isExpired(server, nowMs)) ||
      (status === "renewal" && (isExpiring(server, nowMs) || isExpired(server, nowMs)));
    return statusMatches && (region === "all" || serverRegionKey(server) === region);
  });
}

export function probeHealth(server: ProbeServer, nowMs = Date.now()): ProbeHealth {
  if (!server.online) {
    return { score: 0, label: "Critical", tone: "error", issues: ["Server is offline"] };
  }

  let score = 100;
  const issues: string[] = [];
  const resources = [
    ["CPU", server.cpu_pct],
    ["Memory", resourcePercent(server.mem_used, server.mem_total)],
    ["Disk", resourcePercent(server.disk_used, server.disk_total)],
  ] as const;
  for (const [name, value] of resources) {
    if (value == null) {
      continue;
    }
    if (value >= 90) {
      score -= 18;
      issues.push(`${name} pressure is high`);
    } else if (value >= 75) {
      score -= 9;
      issues.push(`${name} pressure is rising`);
    }
  }

  const latency = averageLatency(server);
  if (latency !== null && latency >= 250) {
    score -= 18;
    issues.push("Latency is high");
  } else if (latency !== null && latency >= 120) {
    score -= 8;
    issues.push("Latency is rising");
  }

  const loss = averageLoss(server);
  if (loss !== null && loss >= 10) {
    score -= 20;
    issues.push("Packet loss is high");
  } else if (loss !== null && loss >= 3) {
    score -= 9;
    issues.push("Packet loss detected");
  }

  if (server.traffic_limit) {
    const quota = percent(trafficUsed(server), server.traffic_limit);
    if (quota >= 95) {
      score -= 16;
      issues.push("Traffic quota is almost used");
    } else if (quota >= 80) {
      score -= 7;
      issues.push("Traffic quota is elevated");
    }
  }

  if (isExpired(server, nowMs)) {
    score -= 20;
    issues.push("Server has expired");
  } else if (isExpiring(server, nowMs)) {
    score -= 8;
    issues.push("Server expires soon");
  }

  const normalized = Math.max(0, Math.round(score));
  if (normalized >= 90) {
    return { score: normalized, label: "Excellent", tone: "success", issues };
  }
  if (normalized >= 75) {
    return { score: normalized, label: "Good", tone: "info", issues };
  }
  if (normalized >= 55) {
    return { score: normalized, label: "Attention", tone: "warning", issues };
  }
  return { score: normalized, label: "Critical", tone: "error", issues };
}

export function averageLatency(server: ProbeServer): number | null {
  const values = (server.ping ?? [])
    .map((series) => series.current_ms)
    .filter((value) => value >= 0);
  if (values.length === 0) {
    return null;
  }
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}

export function averageLoss(server: ProbeServer): number | null {
  const values = (server.ping ?? [])
    .map((series) => series.loss_pct)
    .filter((value) => value >= 0);
  if (values.length === 0) {
    return null;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function latencyBucketLevels(server: ProbeServer): ProbeLatencyBucket[] {
  const series = server.ping ?? [];
  const bucketCount = Math.max(0, ...series.map((item) => item.buckets.length));
  return Array.from({ length: bucketCount }, (_, index) => {
    const values = series
      .map((item) => item.buckets[item.buckets.length - bucketCount + index])
      .filter((bucket): bucket is NonNullable<ProbePingSeries["buckets"][number]> => !!bucket);
    const latencyValues = values.map((bucket) => bucket.ms).filter((value) => value >= 0);
    const lossValues = values.map((bucket) => bucket.loss).filter((value) => value >= 0);
    const ms = latencyValues.length
      ? latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length
      : -1;
    const loss = lossValues.length
      ? lossValues.reduce((sum, value) => sum + value, 0) / lossValues.length
      : -1;
    return { ms, loss, level: latencyBucketLevel(ms, loss) };
  });
}

export function summarizeSevenDayTraffic(
  servers: ProbeServer[],
): ProbeDailyTrafficSummary[] {
  const dates = servers
    .flatMap((server) => server.daily_traffic ?? [])
    .map((item) => item.date)
    .sort();
  const latest = dates[dates.length - 1];
  if (!latest) {
    return [];
  }

  const end = new Date(`${latest}T00:00:00Z`);
  return Array.from({ length: 7 }, (_, index) => {
    const current = new Date(end);
    current.setUTCDate(end.getUTCDate() - 6 + index);
    const date = current.toISOString().slice(0, 10);
    let uplink = 0;
    let downlink = 0;
    for (const server of servers) {
      const day = server.daily_traffic?.find((item) => item.date === date);
      uplink += day?.uplink ?? 0;
      downlink += day?.downlink ?? 0;
    }
    return { date, uplink, downlink, total: uplink + downlink };
  });
}

export function trafficHotspots(servers: ProbeServer[], limit = 5): ProbeTrafficHotspot[] {
  const ranked = servers
    .map((server, index) => ({
      index,
      name: server.name?.trim() || `Node ${index + 1}`,
      speed: (server.upload_speed ?? 0) + (server.download_speed ?? 0),
    }))
    .sort((left, right) => right.speed - left.speed);
  const total = ranked.reduce((sum, row) => sum + row.speed, 0);
  return ranked.slice(0, limit).map((row) => ({
    ...row,
    share: total > 0 ? (row.speed / total) * 100 : 0,
  }));
}

export function trafficUsed(server: ProbeServer): number {
  if (server.traffic_used_total !== undefined && server.traffic_used_total !== null) {
    return server.traffic_used_total;
  }
  if (server.traffic_used !== undefined && server.traffic_used !== null) {
    return server.traffic_used;
  }
  return (server.traffic_used_up ?? 0) + (server.traffic_used_down ?? 0);
}

export function trafficTotal(rows: ProbeDailyTraffic[]): number {
  return rows.reduce((sum, row) => sum + row.total, 0);
}

export function isExpiring(server: ProbeServer, nowMs = Date.now()): boolean {
  const days = daysUntil(server.expires_at, nowMs);
  return days !== null && days >= 0 && days <= 30;
}

export function isExpired(server: ProbeServer, nowMs = Date.now()): boolean {
  const days = daysUntil(server.expires_at, nowMs);
  return days !== null && days < 0;
}

export function remainingDaysLabel(value?: string | null, nowMs = Date.now()): string {
  const days = daysUntil(value, nowMs);
  if (days === null) {
    return "";
  }
  if (days < 0) {
    return `expired ${Math.abs(days)}d`;
  }
  if (days === 0) {
    return "expires today";
  }
  return `${days}d left`;
}

export function percent(used: number | null | undefined, total: number | null | undefined): number {
  if (!total || total <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, ((used ?? 0) / total) * 100));
}

export function resourcePercent(
  used: number | null | undefined,
  total: number | null | undefined,
): number | null {
  if (used === undefined || used === null || !total || total <= 0) {
    return null;
  }
  return percent(used, total);
}

function latencyBucketLevel(ms: number, loss: number): ProbeLatencyBucket["level"] {
  if (ms < 0 && loss < 0) {
    return "none";
  }
  if (loss >= 10 || ms >= 250) {
    return "critical";
  }
  if (loss > 0 || ms >= 120) {
    return "warning";
  }
  return "good";
}

function daysUntil(value?: string | null, nowMs = Date.now()): number | null {
  if (!value) {
    return null;
  }
  const targetDay = new Date(`${value.slice(0, 10)}T00:00:00Z`).getTime();
  if (!Number.isFinite(targetDay)) {
    return null;
  }
  const currentIsoDay = new Date(nowMs).toISOString().slice(0, 10);
  const nowDay = new Date(`${currentIsoDay}T00:00:00Z`).getTime();
  return Math.round((targetDay - nowDay) / 86_400_000);
}

function parseCountryCode(value?: string | null): string | null {
  const rawCode = value?.trim().split(/[,\s]+/)[0];
  if (!rawCode) {
    return null;
  }

  const flagCode = flagToCountryCode(rawCode);
  if (flagCode) {
    return flagCode;
  }

  const normalized = rawCode.toUpperCase();
  return normalized && /^[A-Z]{2}$/.test(normalized) ? normalized : null;
}

function flagToCountryCode(value: string): string | null {
  const codePoints = [...value].map((character) => character.codePointAt(0) || 0);
  const regionalIndicatorBase = 0x1f1e6;
  if (
    codePoints.length !== 2 ||
    codePoints.some((codePoint) => codePoint < regionalIndicatorBase || codePoint > 0x1f1ff)
  ) {
    return null;
  }
  return codePoints
    .map((codePoint) => String.fromCharCode(codePoint - regionalIndicatorBase + 65))
    .join("");
}
