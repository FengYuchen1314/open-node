import { describe, expect, it } from "vitest";

import {
  createServer,
  createServerCommand,
  getLatestScanResult,
  getLatestTelemetry,
  getXrayRuntimeInventory,
  listCommandStreamFrames,
  listServerCommands,
  listServers,
  listXrayConfigSnapshots,
  queueAgentOperation,
  restoreXrayConfigSnapshot,
  updateServerProbeMetadata,
} from "./inventory";

describe("inventory API client", () => {
  it("creates servers without sending license headers", async () => {
    let body: unknown;
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      headers = init?.headers;
      body = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return new Response(
        JSON.stringify({
          server: {
            id: "srv_1",
            name: "edge",
            status: "pending",
            connection_mode: "auto",
            listen_port: 23889,
            xray_mode: "external",
            region_city: "Tokyo",
            current_upload_speed: 0,
            current_download_speed: 0,
          },
          agent_token: "agent-token",
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await createServer({ name: "edge", region_city: "Tokyo" }, fetcher);

    expect(response.license_required).toBe(false);
    expect(response.server.region_city).toBe("Tokyo");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(body).toEqual({ name: "edge", region_city: "Tokyo" });
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

  it("updates server probe metadata with JSON only", async () => {
    let requestUrl = "";
    let requestBody: unknown;
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      requestBody = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return new Response(
        JSON.stringify({
          server: {
            id: "srv_1",
            name: "edge",
            status: "connected",
            connection_mode: "websocket",
            listen_port: 23889,
            pull_port: 0,
            ipv6_enabled: true,
            traffic_limit: 0,
            xray_mode: "external",
            region_city: "Osaka",
            provider_name: "Example Cloud",
            renewal_cycle: "month",
            current_upload_speed: 0,
            current_download_speed: 0,
            created_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:00Z",
          },
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await updateServerProbeMetadata(
      "srv_1",
      {
        region_city: "Osaka",
        provider_name: "Example Cloud",
        renewal_cycle: "month",
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/probe-metadata");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(requestBody).toEqual({
      region_city: "Osaka",
      provider_name: "Example Cloud",
      renewal_cycle: "month",
    });
    expect(response.license_required).toBe(false);
    expect(response.server.region_city).toBe("Osaka");
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

  it("reads latest scan results without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          scan: {
            server_id: "srv_1",
            xray_running: true,
            xray_version: "Xray 1.8.24",
            api_port: 46736,
            config_path: "/usr/local/etc/xray/config.json",
            inbounds: [{ tag: "vless-443", port: 443 }],
            device_kicks: { "alice@example.com": 2 },
            config_modified: true,
            config_added_sections: ["api", "stats"],
            message: "Xray is running, found 1 inbound(s)",
            reported_at: "2026-08-27T00:00:00Z",
            updated_at: "2026-08-27T00:00:01Z",
          },
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getLatestScanResult("srv_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/scan/latest");
    expect(response.license_required).toBe(false);
    expect(response.scan?.xray_running).toBe(true);
    expect(response.scan?.inbounds[0]?.tag).toBe("vless-443");
    expect(headers).toBeUndefined();
  });

  it("reads Xray runtime inventory without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          has_scan: true,
          xray_running: true,
          xray_version: "Xray 1.8.24",
          api_port: 46736,
          config_path: "/usr/local/etc/xray/config.json",
          config_modified: false,
          config_added_sections: [],
          inbound_count: 1,
          client_count: 2,
          protocol_counts: { vless: 1 },
          inbounds: [
            {
              source_index: 0,
              tag: "vless-443",
              display_name: "vless-443",
              protocol: "vless",
              port: 443,
              network: "tcp",
              security: "reality",
              client_container: "clients",
              client_count: 2,
              user_emails: ["alice@example.com"],
              sniffing_enabled: true,
              sniffing_dest_override: ["http", "tls"],
              sniffing_exclude_domains: ["example.com"],
              remarks: [],
            },
          ],
          reported_at: "2026-08-27T00:00:00Z",
          updated_at: "2026-08-27T00:00:01Z",
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getXrayRuntimeInventory("srv_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/xray/runtime");
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.inbounds[0]?.client_count).toBe(2);
  });

  it("lists Xray config snapshots with optional config bodies", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          snapshots: [
            {
              id: "snap_1",
              server_id: "srv_1",
              source_command_id: "cmd_1",
              config_hash: "abcdef012345",
              source: "agent_report",
              status: "current",
              size_bytes: 42,
              config: "{\"inbounds\":[]}",
              created_at: "2026-08-27T00:00:00Z",
            },
          ],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await listXrayConfigSnapshots(
      "srv_1",
      { limit: 8, withConfig: true },
      fetcher,
    );

    expect(requestUrl).toBe(
      "/api/v1/servers/srv_1/xray/config-snapshots?limit=8&with_config=true",
    );
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.snapshots[0]?.status).toBe("current");
    expect(response.snapshots[0]?.config).toBe("{\"inbounds\":[]}");
  });

  it("queues Xray config snapshot restores without license headers", async () => {
    let requestUrl = "";
    let method = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      method = init?.method ?? "";
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_restore",
            server_id: "srv_1",
            request_id: "srv_1-restore",
            method: "POST",
            path: "/api/child/xray/config",
            query: "",
            body: { config: "{\"inbounds\":[]}" },
            timeout_ms: 60000,
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

    const response = await restoreXrayConfigSnapshot("srv_1", "snap_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/xray/config-snapshots/snap_1/restore");
    expect(method).toBe("POST");
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.command.path).toBe("/api/child/xray/config");
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

  it("queues legacy maintenance operations with preset routes", async () => {
    const requestUrls: string[] = [];
    const bodies: string[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      requestUrls.push(input.toString());
      bodies.push(init?.body?.toString() ?? "");
      return new Response(
        JSON.stringify({
          command: {
            id: `cmd_legacy_${requestUrls.length}`,
            server_id: "srv_1",
            request_id: `srv_1-legacy-${requestUrls.length}`,
            method: "POST",
            path: "/api/child/xray/install",
            query: "",
            timeout_ms: 300000,
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

    await queueAgentOperation("srv_1", "xray_install_legacy", undefined, fetcher);
    await queueAgentOperation("srv_1", "xray_remove_legacy", undefined, fetcher);
    await queueAgentOperation(
      "srv_1",
      "nginx_install_legacy",
      { domain: "panel.example.com" },
      fetcher,
    );
    await queueAgentOperation("srv_1", "nginx_remove_legacy", undefined, fetcher);

    expect(requestUrls).toEqual([
      "/api/v1/servers/srv_1/operations/xray/install-legacy",
      "/api/v1/servers/srv_1/operations/xray/remove-legacy",
      "/api/v1/servers/srv_1/operations/nginx/install-legacy",
      "/api/v1/servers/srv_1/operations/nginx/remove-legacy",
    ]);
    expect(bodies).toEqual(["", "", JSON.stringify({ domain: "panel.example.com" }), ""]);
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

  it("queues log file list and delete operations", async () => {
    const requestUrls: string[] = [];
    const bodies: string[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      requestUrls.push(input.toString());
      bodies.push(init?.body?.toString() ?? "");
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_8a",
            server_id: "srv_1",
            request_id: "srv_1-log-files",
            method: init?.body ? "DELETE" : "GET",
            path: "/api/child/logs/files",
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

    await queueAgentOperation("srv_1", "log_files_list", undefined, fetcher);
    await queueAgentOperation("srv_1", "log_files_delete", { name: "mmw-agent.log.1" }, fetcher);

    expect(requestUrls).toEqual([
      "/api/v1/servers/srv_1/operations/logs/files/list",
      "/api/v1/servers/srv_1/operations/logs/files/delete",
    ]);
    expect(bodies).toEqual(["", JSON.stringify({ name: "mmw-agent.log.1" })]);
  });

  it("queues nginx stream-port cleanup with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_8b",
            server_id: "srv_1",
            request_id: "srv_1-clear-stream",
            method: "POST",
            path: "/api/child/nginx/clear-stream-port",
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

    await queueAgentOperation("srv_1", "nginx_clear_stream_port", { port: 443 }, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/nginx/clear-stream-port");
    expect(JSON.parse(body)).toEqual({ port: 443 });
  });

  it("queues config read operations with preset routes", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_9",
            server_id: "srv_1",
            request_id: "srv_1-xray-config",
            method: "GET",
            path: "/api/child/xray/config",
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

    await queueAgentOperation("srv_1", "xray_config_read", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/xray/config/read");
    expect(headers).toBeUndefined();
  });

  it("queues high-level agent operations with preset routes", async () => {
    const requestUrls: string[] = [];
    const fetcher: typeof fetch = async (input) => {
      requestUrls.push(input.toString());
      return new Response(
        JSON.stringify({
          command: {
            id: `cmd_${requestUrls.length}`,
            server_id: "srv_1",
            request_id: `srv_1-${requestUrls.length}`,
            method: "GET",
            path: "/api/child/inbounds",
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

    await queueAgentOperation("srv_1", "inbounds_list", undefined, fetcher);
    await queueAgentOperation("srv_1", "outbounds_list", undefined, fetcher);
    await queueAgentOperation("srv_1", "routing_read", undefined, fetcher);
    await queueAgentOperation("srv_1", "nginx_servers_list", undefined, fetcher);
    await queueAgentOperation("srv_1", "nginx_websites_list", undefined, fetcher);

    expect(requestUrls).toEqual([
      "/api/v1/servers/srv_1/operations/inbounds/list",
      "/api/v1/servers/srv_1/operations/outbounds/list",
      "/api/v1/servers/srv_1/operations/routing/read",
      "/api/v1/servers/srv_1/operations/nginx/servers-list",
      "/api/v1/servers/srv_1/operations/nginx/websites/list",
    ]);
  });

  it("queues Xray config write operations with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_10",
            server_id: "srv_1",
            request_id: "srv_1-xray-write",
            method: "POST",
            path: "/api/child/xray/config",
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
      "xray_config_write",
      { config: { inbounds: [], outbounds: [] }, force: true },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/xray/config/write");
    expect(JSON.parse(body)).toEqual({
      config: { inbounds: [], outbounds: [] },
      force: true,
    });
  });

  it("queues Xray external takeover with preset route", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_10a",
            server_id: "srv_1",
            request_id: "srv_1-takeover",
            method: "POST",
            path: "/api/child/external-xray/takeover",
            query: "",
            timeout_ms: 120000,
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

    await queueAgentOperation("srv_1", "xray_takeover_external", undefined, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/xray/takeover-external");
    expect(body).toBe("");
  });

  it("queues high-level agent operations with JSON bodies", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_10b",
            server_id: "srv_1",
            request_id: "srv_1-routing",
            method: "POST",
            path: "/api/child/routing",
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
      "routing_manage",
      {
        action: "add_user_to_rule",
        marktag: "route-proxy",
        user_email: "user@example.com",
        no_restart: true,
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/routing/manage");
    expect(JSON.parse(body)).toEqual({
      action: "add_user_to_rule",
      marktag: "route-proxy",
      user_email: "user@example.com",
      no_restart: true,
    });
  });

  it("queues WARP credential operations with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_11",
            server_id: "srv_1",
            request_id: "srv_1-warp-license",
            method: "POST",
            path: "/api/child/warp/license",
            query: "",
            body: JSON.parse(body) as unknown,
            timeout_ms: 60000,
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

    await queueAgentOperation("srv_1", "warp_license", { license: "warp-plus-key" }, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/warp/license");
    expect(JSON.parse(body)).toEqual({ license: "warp-plus-key" });
  });

  it("queues agent master URL updates with JSON body", async () => {
    let requestUrl = "";
    let body = "";
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      body = init?.body?.toString() ?? "";
      return new Response(
        JSON.stringify({
          command: {
            id: "cmd_12",
            server_id: "srv_1",
            request_id: "srv_1-master",
            method: "POST",
            path: "/api/child/agent/update-master-url",
            query: "",
            body: JSON.parse(body) as unknown,
            timeout_ms: 60000,
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
      "agent_update_master_url",
      { master_url: "https://panel.example.com", only_if_recovery: true },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/agent/update-master-url");
    expect(JSON.parse(body)).toEqual({
      master_url: "https://panel.example.com",
      only_if_recovery: true,
    });
  });
});
