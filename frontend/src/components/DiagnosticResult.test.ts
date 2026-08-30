import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import DiagnosticResult from "../react/components/DiagnosticResult";
import { latencyCommandTimeout, routeTargets, selectedRouteTargets } from "../domain/diagnostics";

async function render(path: string, body: unknown) {
  return renderToStaticMarkup(createElement(DiagnosticResult, { path, body }));
}

describe("diagnostic results", () => {
  it("distinguishes host reachability from an open TCP port", async () => {
    const html = await render("/api/child/domains/latency", { results: [
      { target: "localhost:80", success: true, latency_ms: 0, method: "icmp", tcp_error: "TCP refused" },
      { target: "localhost:443", success: true, latency_ms: 2, method: "tcp" },
      { target: "host.invalid", success: false, method: "tcp", error: "DNS resolution failed" },
    ] });
    expect(html).toContain("ICMP host reachable");
    expect(html).toContain("TCP port open");
    expect(html).toContain("TCP refused");
    expect(html).toContain("0 ms");
    expect(html).toContain("DNS resolution failed");
  });
  it("shows unknown route evidence without fabricating a premium route", async () => {
    const html = await render("/api/child/network/return-route-test", { results: [{
      carrier: "telecom", target: "::1", route_type: "Unknown", reached: true,
      hops: [{ hop: 1, ip: "::1", rtt_ms: 0.2, asn: "" }],
    }] });
    expect(html).toContain("Unknown");
    expect(html).toContain("Target reached");
    expect(html).toContain("ASN unavailable");
    expect(html).not.toContain("GIA");
  });
  it("escapes logs and identifies active files", async () => {
    const html = await render("/api/child/logs", { logs: "<script>alert(1)</script>" });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    const list = await render("/api/child/logs/files", { total_size: 12, files: [
      { name: "agent.log", size: 12, active: true },
    ] });
    expect(list).toContain("12 bytes");
    expect(list).toContain("Active");
  });
  it("ignores blank carrier targets and budgets bounded batches", () => {
    const targets = routeTargets();
    targets[1].host = " ::1 ";
    expect(selectedRouteTargets(targets)).toEqual([{ carrier: "unicom", host: "::1", region: "", port: 80 }]);
    expect(latencyCommandTimeout(200, 10000, true)).toBeGreaterThan(260000);
    expect(latencyCommandTimeout(200, 10000, true)).toBeLessThanOrEqual(300000);
  });
});
