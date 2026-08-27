import { describe, expect, it } from "vitest";

import {
  createProbeTask,
  dispatchDueProbeTasks,
  getPublicProbePayload,
  getPublicProbeSeries,
  getPublicProbeSettings,
  getPublicProbeStreamUrl,
  getPublicProbeTargets,
  listProbeTasks,
  updateProbeTask,
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

  it("loads public probe target comparisons", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return jsonResponse({
        success: true,
        targets: [
          {
            key: "ct-shanghai",
            label: "ct-shanghai",
            server_count: 2,
            healthy_count: 1,
            average_ms: 42,
            best_ms: 31,
            worst_ms: 57,
            average_loss_pct: 50,
            servers: [
              {
                server_index: 0,
                server_name: "edge-a",
                current_ms: 31,
                loss_pct: 0,
                buckets: [],
              },
            ],
          },
        ],
        bucket_sec: 300,
        generated_at: 1798330000,
        license_required: false,
      });
    };

    const response = await getPublicProbeTargets("6h", fetcher);

    expect(requestUrl).toBe("/api/v1/public/probe-targets?range=6h");
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.targets[0].servers[0].server_index).toBe(0);
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
            show_renewal_timeline: true,
            show_return_route: true,
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
        show_renewal_timeline: true,
        show_return_route: true,
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
          show_renewal_timeline: true,
          show_return_route: true,
          appearance: { theme: "compact", color_mode: "dark" },
        },
      },
    ]);
  });

  it("manages scheduled probe tasks without license headers", async () => {
    const calls: Array<{
      body?: unknown;
      headers: HeadersInit | undefined;
      method?: string;
      url: string;
    }> = [];
    const task = {
      id: "task-1",
      server_id: "server-1",
      kind: "domain_latency",
      enabled: true,
      interval_sec: 300,
      domains: ["example.com"],
      domain_timeout_ms: 500,
      allow_icmp: true,
      return_route_targets: [],
      return_route_timeout_seconds: 25,
      ip_version: 4,
      command_timeout_ms: 12_000,
      next_run_at: "2026-08-27T09:00:00Z",
      created_at: "2026-08-27T09:00:00Z",
      updated_at: "2026-08-27T09:00:00Z",
    };
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({
        url,
        method: init?.method,
        headers: init?.headers,
        body: init?.body ? JSON.parse(init.body.toString()) : undefined,
      });
      if (url.endsWith("/dispatch-due")) {
        return jsonResponse({
          checked_at: "2026-08-27T09:00:00Z",
          dispatched: [],
          license_required: false,
        });
      }
      if (init?.method === "PATCH") {
        return jsonResponse({ task: { ...task, enabled: false }, license_required: false });
      }
      if (init?.method === "POST") {
        return jsonResponse({ task, license_required: false });
      }
      return jsonResponse({ tasks: [task], license_required: false });
    };

    const listed = await listProbeTasks(fetcher);
    const created = await createProbeTask(
      {
        server_id: "server-1",
        kind: "domain_latency",
        domains: ["example.com"],
        domain_timeout_ms: 500,
        allow_icmp: true,
      },
      fetcher,
    );
    const updated = await updateProbeTask("task-1", { enabled: false }, fetcher);
    const dispatched = await dispatchDueProbeTasks(fetcher);

    expect(listed.tasks[0].id).toBe("task-1");
    expect(created.license_required).toBe(false);
    expect(updated.task.enabled).toBe(false);
    expect(dispatched.dispatched).toEqual([]);
    expect(calls).toEqual([
      {
        url: "/api/v1/probe/tasks",
        method: undefined,
        headers: undefined,
        body: undefined,
      },
      {
        url: "/api/v1/probe/tasks",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: {
          server_id: "server-1",
          kind: "domain_latency",
          domains: ["example.com"],
          domain_timeout_ms: 500,
          allow_icmp: true,
        },
      },
      {
        url: "/api/v1/probe/tasks/task-1",
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: { enabled: false },
      },
      {
        url: "/api/v1/probe/tasks/dispatch-due",
        method: "POST",
        headers: undefined,
        body: undefined,
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

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
