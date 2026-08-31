import { describe, expect, it } from "vitest";

import type { ProbeServer } from "./probe";
import {
  buildRegionOptions,
  buildSparkline,
  filterProbeServers,
  latencyBucketLevels,
  probeHealth,
  remainingDaysLabel,
  returnRouteBadges,
  summarizeSevenDayTraffic,
  trafficHotspots,
  trafficUsed,
} from "./probe-insights";

const now = Date.parse("2026-08-27T00:00:00Z");
const jpFlag = String.fromCodePoint(0x1f1ef, 0x1f1f5);

describe("probe insights", () => {
  it("uses billed traffic for quotas even when both directions are also available", () => {
    expect(trafficUsed({ name: "edge", online: true, traffic_used: 60, traffic_used_total: 100 })).toBe(60);
    expect(trafficUsed({ name: "edge", online: true, traffic_used: 0, traffic_used_total: 100 })).toBe(0);
    expect(trafficUsed({ name: "legacy", online: true, traffic_used_total: 100 })).toBe(100);
  });
  it("groups regions and filters by status or renewal state", () => {
    const servers: ProbeServer[] = [
      { name: "tokyo", online: true, region_country: "JP", region_city: "Tokyo" },
      { name: "osaka", online: false, region_country: "JP", region_city: "Osaka" },
      { name: "la", online: true, region: "US", expires_at: "2026-09-05" },
      { name: "emoji", online: true, region: `${jpFlag} Tokyo` },
      { name: "old", online: true, region: "SG", expires_at: "2026-08-20" },
    ];

    expect(buildRegionOptions(servers)).toEqual([
      { code: "JP", label: "Tokyo, JP", online: 2, total: 3 },
      { code: "US", label: "US", online: 1, total: 1 },
      { code: "SG", label: "SG", online: 1, total: 1 },
    ]);
    expect(filterProbeServers(servers, "offline", "all", now).map((server) => server.name)).toEqual([
      "osaka",
    ]);
    expect(filterProbeServers(servers, "renewal", "all", now).map((server) => server.name)).toEqual([
      "la",
      "old",
    ]);
    expect(filterProbeServers(servers, "all", "JP", now).map((server) => server.name)).toEqual([
      "tokyo",
      "osaka",
      "emoji",
    ]);
  });

  it("scores public health from resources, latency, quota, and expiry", () => {
    const healthy = probeHealth(
      {
        name: "healthy",
        online: true,
        cpu_pct: 22,
        mem_used: 2,
        mem_total: 8,
        disk_used: 10,
        disk_total: 100,
        ping: [{ label: "ct", current_ms: 45, loss_pct: 0, buckets: [] }],
      },
      now,
    );
    const strained = probeHealth(
      {
        name: "strained",
        online: true,
        cpu_pct: 96,
        mem_used: 9,
        mem_total: 10,
        disk_used: 91,
        disk_total: 100,
        traffic_used_total: 950,
        traffic_limit: 1000,
        expires_at: "2026-08-28",
        ping: [{ label: "ct", current_ms: 280, loss_pct: 11, buckets: [] }],
      },
      now,
    );

    expect(healthy).toMatchObject({ score: 100, label: "Excellent", tone: "success" });
    expect(strained.score).toBeLessThan(40);
    expect(strained.tone).toBe("error");
    expect(strained.issues).toContain("服务器即将到期");
  });

  it("aggregates seven-day traffic and ranks live hotspots", () => {
    const servers: ProbeServer[] = [
      {
        name: "tokyo",
        online: true,
        upload_speed: 100,
        download_speed: 300,
        daily_traffic: [
          { date: "2026-08-25", uplink: 10, downlink: 20, total: 30 },
          { date: "2026-08-27", uplink: 30, downlink: 40, total: 70 },
        ],
      },
      {
        name: "seattle",
        online: true,
        upload_speed: 50,
        download_speed: 50,
        daily_traffic: [{ date: "2026-08-27", uplink: 5, downlink: 6, total: 11 }],
      },
    ];

    const traffic = summarizeSevenDayTraffic(servers);
    expect(traffic).toHaveLength(7);
    expect(traffic.at(-1)).toEqual({
      date: "2026-08-27",
      uplink: 35,
      downlink: 46,
      total: 81,
    });
    expect(traffic[4]).toMatchObject({ date: "2026-08-25", total: 30 });
    expect(trafficHotspots(servers)).toEqual([
      { index: 0, name: "tokyo", speed: 400, share: 80 },
      { index: 1, name: "seattle", speed: 100, share: 20 },
    ]);
  });

  it("averages ping buckets into display levels", () => {
    const buckets = latencyBucketLevels({
      name: "edge",
      online: true,
      ping: [
        {
          label: "ct",
          current_ms: 80,
          loss_pct: 0,
          buckets: [
            { ms: 50, loss: 0 },
            { ms: 150, loss: 0 },
          ],
        },
        {
          label: "cu",
          current_ms: 300,
          loss_pct: 15,
          buckets: [
            { ms: -1, loss: -1 },
            { ms: 300, loss: 20 },
          ],
        },
      ],
    });

    expect(buckets).toEqual([
      { ms: 50, loss: 0, level: "good" },
      { ms: 225, loss: 10, level: "critical" },
    ]);
    expect(remainingDaysLabel("2026-08-27", now)).toBe("今天到期");
    expect(remainingDaysLabel("2026-08-25", now)).toBe("已到期 2 天");
    expect(remainingDaysLabel("2026-08-30", now)).toBe("剩余 3 天");
  });

  it("normalizes return route badges for three-carrier display", () => {
    const badges = returnRouteBadges({
      name: "edge",
      online: true,
      telecom_paid_peer: true,
      return_routes: [
        {
          carrier: "telecom",
          region: "Guangzhou",
          route_type: "163",
          tested_at: "2026-08-27T01:02:03Z",
        },
        { carrier: "mobile", region: "Shanghai", route_type: "CMIN" },
      ],
    });

    expect(badges).toEqual([
      {
        carrier: "telecom",
        carrierLabel: "电信",
        routeType: "163 PP",
        region: "Guangzhou",
        testedAt: "2026-08-27T01:02:03Z",
        premium: true,
        missing: false,
      },
      {
        carrier: "unicom",
        carrierLabel: "联通",
        routeType: "未知",
        region: "",
        testedAt: "",
        premium: false,
        missing: true,
      },
      {
        carrier: "mobile",
        carrierLabel: "移动",
        routeType: "CMI",
        region: "Shanghai",
        testedAt: "",
        premium: false,
        missing: false,
      },
    ]);
  });

  it("builds stable sparkline geometry for probe drilldowns", () => {
    const chart = buildSparkline([10, -1, 30], 100, 50, 5);

    expect(chart).toMatchObject({ min: 10, max: 30, latest: 30, empty: false });
    expect(chart.points).toBe("5.0,45.0 95.0,5.0");
    expect(chart.areaPoints).toBe("5.0,45.0 5.0,45.0 95.0,5.0 95.0,45.0");

    expect(buildSparkline([-1, null, undefined]).empty).toBe(true);
    expect(buildSparkline([42], 100, 50, 5).points).toBe("5.0,5.0 6.0,5.0");
  });
});
