import { describe, expect, it } from "vitest";

import {
  getPublicProbePayload,
  getPublicProbeSeries,
  getPublicProbeSettings,
  getPublicProbeStreamUrl,
  updatePublicProbeSettings,
} from "./probe";

describe("public probe API client", () => {
  it("loads public probe payload without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          enabled: true,
          title: "Open Node Probe",
          servers: [{ name: "edge", online: true }],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getPublicProbePayload(fetcher);

    expect(requestUrl).toBe("/api/v1/public/probe-servers");
    expect(response.license_required).toBe(false);
    expect(response.servers?.[0].online).toBe(true);
    expect(headers).toBeUndefined();
  });

  it("loads public probe series by public server index", async () => {
    let requestUrl = "";
    const fetcher: typeof fetch = async (input) => {
      requestUrl = input.toString();
      return new Response(
        JSON.stringify({
          success: true,
          series: {
            key: "ct-shanghai",
            label: "ct-shanghai",
            current_ms: 42,
            loss_pct: 0,
            buckets: [],
          },
          all_series: [],
          bucket_sec: 1800,
          generated_at: 1798330000,
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getPublicProbeSeries(
      2,
      { range: "24h", target: "ct-shanghai", all: true },
      fetcher,
    );

    expect(requestUrl).toBe(
      "/api/v1/public/probe-series?server=2&range=24h&metric=ping&target=ct-shanghai&all=1",
    );
    expect(response.series).toMatchObject({ current_ms: 42 });
    expect(response.license_required).toBe(false);
  });

  it("loads and updates public probe settings", async () => {
    const calls: Array<{ body?: unknown; headers: HeadersInit | undefined; method?: string; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({
        url,
        method: init?.method,
        headers: init?.headers,
        body: init?.body ? JSON.parse(init.body.toString()) : undefined,
      });
      return new Response(
        JSON.stringify({
          settings: {
            enabled: true,
            title: "MMWX Public Status",
            description: "Public node telemetry",
            refresh_interval_sec: 3,
            appearance: { theme: "compact", color_mode: "dark", revision: "probe-r2" },
          },
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const loaded = await getPublicProbeSettings(fetcher);
    const updated = await updatePublicProbeSettings(
      {
        enabled: true,
        title: "MMWX Public Status",
        refresh_interval_sec: 3,
        appearance: { theme: "compact", color_mode: "dark" },
      },
      fetcher,
    );

    expect(loaded.settings.title).toBe("MMWX Public Status");
    expect(updated.license_required).toBe(false);
    expect(calls).toEqual([
      {
        url: "/api/v1/public/probe-settings",
        method: undefined,
        headers: undefined,
        body: undefined,
      },
      {
        url: "/api/v1/public/probe-settings",
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: {
          enabled: true,
          title: "MMWX Public Status",
          refresh_interval_sec: 3,
          appearance: { theme: "compact", color_mode: "dark" },
        },
      },
    ]);
  });

  it("builds a same-origin public probe websocket URL", () => {
    expect(
      getPublicProbeStreamUrl({
        origin: "https://probe.example",
        protocol: "https:",
      }),
    ).toBe("wss://probe.example/api/v1/public/probe-ws");
    expect(
      getPublicProbeStreamUrl({
        origin: "http://localhost:5173",
        protocol: "http:",
      }),
    ).toBe("ws://localhost:5173/api/v1/public/probe-ws");
  });
});
