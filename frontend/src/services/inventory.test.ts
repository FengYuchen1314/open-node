import { describe, expect, it } from "vitest";

import {
  createServer,
  createServerCommand,
  getLatestTelemetry,
  listCommandStreamFrames,
  listServerCommands,
  listServers,
  queueAgentOperation,
} from "./inventory";

describe("inventory API client", () => {
  it("creates servers without sending license headers", async () => {
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server: {
            id: "srv_1",
            name: "edge",
            status: "pending",
            connection_mode: "auto",
            listen_port: 23889,
            xray_mode: "external",
            current_upload_speed: 0,
            current_download_speed: 0,
          },
          agent_token: "agent-token",
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await createServer({ name: "edge" }, fetcher);

    expect(response.license_required).toBe(false);
    expect(headers).toEqual({ "Content-Type": "application/json" });
  });

  it("lists servers without sending license headers", async () => {
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      headers = init?.headers;
      return new Response(
        JSON.stringify([
          {
            id: "srv_1",
            name: "edge",
            status: "connected",
            connection_mode: "websocket",
            listen_port: 23889,
            pull_port: 0,
            ipv6_enabled: true,
            traffic_limit: 0,
            xray_mode: "external",
            current_upload_speed: 1024,
            current_download_speed: 2048,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await listServers(fetcher);

    expect(response[0].name).toBe("edge");
    expect(headers).toBeUndefined();
  });

  it("reads latest telemetry without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          latest: {
            id: "tel_1",
            server_id: "srv_1",
            reported_at: "2026-08-27T00:00:00Z",
            received_at: "2026-08-27T00:00:01Z",
            online_users: {},
            user_speeds: {},
            conn_counts: {},
            latency: [],
          },
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getLatestTelemetry("srv_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/telemetry/latest");
    expect(response.license_required).toBe(false);
    expect(headers).toBeUndefined();
  });

  it("lists queued commands without sending license headers", async () => {
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          commands: [],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await listServerCommands("srv_1", fetcher);

    expect(response.commands).toEqual([]);
    expect(headers).toBeUndefined();
  });

  it("queues commands without sending license headers", async () => {
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_1",
            server_id: "srv_1",
            request_id: "srv_1-abc",
            method: "GET",
            path: "/api/child/system/info",
            query: "",
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await createServerCommand(
      "srv_1",
      { method: "GET", path: "/api/child/system/info" },
      fetcher,
    );

    expect(response.command.status).toBe("pending");
    expect(headers).toEqual({ "Content-Type": "application/json" });
  });

  it("lists command stream frames without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          command_id: "cmd_1",
          frames: [
            {
              id: "frame_1",
              command_id: "cmd_1",
              server_id: "srv_1",
              request_id: "srv_1-abc",
              sequence: 1,
              data: "data: installing\n\n",
              received_at: "2026-08-27T00:00:00Z",
            },
          ],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await listCommandStreamFrames("srv_1", "cmd_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/commands/cmd_1/stream");
    expect(response.frames[0].data).toBe("data: installing\n\n");
    expect(headers).toBeUndefined();
  });

  it("queues preset child operations without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_2",
            server_id: "srv_1",
            request_id: "srv_1-op",
            method: "GET",
            path: "/api/child/system/info",
            query: "",
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await queueAgentOperation("srv_1", "system_info", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/system-info");
    expect(response.license_required).toBe(false);
    expect(headers).toBeUndefined();
  });

  it("queues domain latency operations with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_3",
            server_id: "srv_1",
            request_id: "srv_1-domain",
            method: "POST",
            path: "/api/child/domains/latency",
            query: "",
            body: JSON.parse(body) as unknown,
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    await queueAgentOperation(
      "srv_1",
      "domain_latency",
      { domains: ["example.com"], timeout_ms: 2000, allow_icmp: true },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/domain-latency");
    expect(JSON.parse(body)).toEqual({
      domains: ["example.com"],
      timeout_ms: 2000,
      allow_icmp: true,
    });
  });

  it("queues stream maintenance operations with preset routes", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_4",
            server_id: "srv_1",
            request_id: "srv_1-maintenance",
            method: "POST",
            path: "/api/child/xray/install-stream",
            query: "",
            timeout_ms: 300000,
            stream: true,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await queueAgentOperation("srv_1", "xray_install", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/xray/install");
    expect(response.command.stream).toBe(true);
    expect(headers).toBeUndefined();
  });

  it("queues WARP operations with preset routes", async () => {
    let requestUrl = "";
    const fetcher: typeof fetch = async (input) => {
      requestUrl = input.toString();
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_5",
            server_id: "srv_1",
            request_id: "srv_1-warp",
            method: "GET",
            path: "/api/child/warp/status",
            query: "",
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    await queueAgentOperation("srv_1", "warp_status", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/warp/status");
  });

  it("queues service status diagnostics without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_6",
            server_id: "srv_1",
            request_id: "srv_1-services",
            method: "GET",
            path: "/api/child/services/status",
            query: "",
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    await queueAgentOperation("srv_1", "services_status", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/services/status");
    expect(headers).toBeUndefined();
  });

  it("queues service control operations with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_7",
            server_id: "srv_1",
            request_id: "srv_1-control",
            method: "POST",
            path: "/api/child/services/control",
            query: "",
            body: JSON.parse(body) as unknown,
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    await queueAgentOperation(
      "srv_1",
      "service_control",
      { service: "nginx", action: "restart" },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/services/control");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(body)).toEqual({ service: "nginx", action: "restart" });
  });

  it("queues log operations with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_8",
            server_id: "srv_1",
            request_id: "srv_1-logs",
            method: "GET",
            path: "/api/child/logs",
            query: "service=xray&lines=500",
            timeout_ms: 30000,
            stream: false,
            status: "pending",
            attempts: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    await queueAgentOperation("srv_1", "logs", { service: "xray", lines: 500 }, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/logs");
    expect(JSON.parse(body)).toEqual({ service: "xray", lines: 500 });
  });
});
