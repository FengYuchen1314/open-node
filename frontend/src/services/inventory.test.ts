import { describe, expect, it } from "vitest";

import {
  acceptXrayConfigPendingRecovery,
  applyXrayConfigRecovery,
  createXrayRuntimeTunnelChain,
  createServer,
  createServerCommand,
  deleteXrayRuntimeTunnel,
  deployXrayRuntimeTunnel,
  getXrayConfigSnapshotRecovery,
  getLatestScanResult,
  getLatestTelemetry,
  getAgentIdentity,
  getXrayRuntimeInventory,
  getXrayRuntimeTunnelInventory,
  listCommandStreamFrames,
  listAgents,
  listServerCommands,
  listServers,
  listXrayConfigSnapshots,
  queueAgentOperation,
  restoreXrayConfigSnapshot,
  updateServerProbeMetadata,
  getServerTraffic,
  updateServerTraffic,
  resetServerTraffic,
} from "./inventory";

describe("inventory API client", () => {
  it("reads, updates and resets server billing through authenticated routes", async () => {
    const payload = { traffic_limit: 1024, traffic_reset_day: 31, traffic_source: "system" as const, traffic_stats_mode: "max" as const };
    let count = 0;
    const fetcher: typeof fetch = async (input, init) => {
      expect(input.toString()).toBe("/api/v1/servers/edge/traffic" + (count === 2 ? "/reset" : ""));
      expect(init?.method ?? "GET").toBe(["GET", "PUT", "POST"][count]);
      if (count === 1) expect(JSON.parse(String(init?.body))).toEqual(payload);
      count += 1;
      return new Response(JSON.stringify({ ...payload, used: 120 }));
    };
    expect((await getServerTraffic("edge", fetcher)).used).toBe(120);
    expect((await updateServerTraffic("edge", payload, fetcher)).traffic_reset_day).toBe(31);
    expect((await resetServerTraffic("edge", fetcher)).used).toBe(120);
    expect(count).toBe(3);
  });

  it("preserves server traffic permission errors", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: "Permission denied" }), { status: 403 });
    await expect(getServerTraffic("edge", fetcher)).rejects.toThrow("Permission denied");
    await expect(resetServerTraffic("edge", fetcher)).rejects.toThrow("Permission denied");
    await expect(updateServerTraffic("edge", { traffic_limit: 0, traffic_reset_day: 0, traffic_source: "xray", traffic_stats_mode: "both" }, fetcher)).rejects.toThrow("Permission denied");
  });

  it.each(["limiter", "limiter_status"] as const)("queues %s with its native policy contract", async (operation) => {
    const payload = operation === "limiter"
      ? { inbound_tag: "edge", action: "remove" as const, expected_revision: "a".repeat(64) }
      : undefined;
    const fetcher: typeof fetch = async (input, init) => {
      expect(input.toString()).toBe("/api/v1/servers/edge/operations/limiter" + (operation === "limiter_status" ? "/status" : ""));
      expect(init?.method).toBe("POST");
      expect(init?.body ? JSON.parse(init.body.toString()) : undefined).toEqual(payload);
      return new Response(JSON.stringify({ command: { id: "limiter-command" } }), { status: 201 });
    };
    expect((await queueAgentOperation("edge", operation, payload, fetcher)).command.id).toBe("limiter-command");
  });

  it.each(["agent_upgrade", "agent_rollback", "agent_uninstall", "agent_lifecycle"] as const)(
    "queues the %s lifecycle operation", async (operation) => {
      const payload = operation === "agent_upgrade"
        ? { version: "0.2.0", sha256: "a".repeat(64) }
        : operation === "agent_lifecycle" ? undefined : { confirm: true as const };
      const fetcher: typeof fetch = async (input, init) => {
        expect(input.toString()).toBe(
          "/api/v1/servers/srv_1/operations/agent/" + operation.slice(6),
        );
        expect(init?.method).toBe("POST");
        expect(init?.body ? JSON.parse(init.body.toString()) : undefined).toEqual(payload);
        return new Response(JSON.stringify({ command: { id: "lifecycle-command" } }), { status: 201 });
      };
      expect((await queueAgentOperation("srv_1", operation, payload, fetcher)).command.id)
        .toBe("lifecycle-command");
    },
  );

  it.each(["xray_install", "xray_release", "xray_rollback"] as const)("queues %s with version pins", async (operation) => {
    const payload = operation === "xray_install" ? { version: "v26.2.6", sha256: "a".repeat(64), start: false } : undefined;
    const fetcher: typeof fetch = async (input, init) => {
      expect(input.toString()).toBe(`/api/v1/servers/srv_1/operations/xray/${operation.slice(5)}`);
      expect(init?.method).toBe("POST");
      expect(init?.body ? JSON.parse(init.body.toString()) : undefined).toEqual(payload);
      return new Response(JSON.stringify({ command: { id: "release-command" } }), { status: 201 });
    };
    expect((await queueAgentOperation("srv_1", operation, payload, fetcher)).command.id).toBe("release-command");
  });

  it("queues the complete Xray system configuration contract", async () => {
    const payload = {
      log_level: "debug" as const,
      dns: { servers: ["1.1.1.1"] },
      policy: { levels: { "0": { bufferSize: 0 } } },
      metrics_enabled: true,
      metrics_listen: "127.0.0.1:11111",
      stats_enabled: true,
      grpc_enabled: true,
      grpc_port: 46736,
      expected_sha256: "a".repeat(64),
    };
    const fetcher: typeof fetch = async (input, init) => {
      expect(input.toString()).toBe(
        "/api/v1/servers/srv_1/operations/xray/system-config/write",
      );
      expect(init?.body ? JSON.parse(init.body.toString()) : undefined).toEqual(payload);
      return new Response(JSON.stringify({ command: { id: "system-config-command" } }), {
        status: 201,
      });
    };

    expect(
      (await queueAgentOperation("srv_1", "xray_system_config_write", payload, fetcher))
        .command.id,
    ).toBe("system-config-command");
  });

  it("reads only public Agent identity metadata", async () => {
    const identity = { enabled: true, protocol: "securechan-v1", public_key: "public-key",
      fingerprint: "fingerprint", license_required: false };
    const fetcher: typeof fetch = async (input) => {
      expect(input.toString()).toBe("/api/v1/agents/identity");
      return new Response(JSON.stringify(identity));
    };
    expect(await getAgentIdentity(fetcher)).toEqual(identity);
  });

  it("lists registered Agent capabilities for feature gating", async () => {
    const agents = [{
      id: "agent-1",
      server_id: "server-1",
      hostname: "edge",
      connection_mode: "websocket",
      listen_port: 0,
      xray_mode: "external",
      capabilities: { xray_config_workspace: true },
      warp_installed: false,
      registered_at: "2026-08-29T00:00:00Z",
      last_seen_at: "2026-08-29T00:00:00Z",
    }];
    const fetcher: typeof fetch = async (input) => {
      expect(input.toString()).toBe("/api/v1/agents");
      return new Response(JSON.stringify(agents));
    };

    expect((await listAgents(fetcher))[0].capabilities.xray_config_workspace).toBe(true);
  });

  it("preserves identity access failures", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: "Sign in required" }), { status: 401 });
    await expect(getAgentIdentity(fetcher)).rejects.toThrow("Sign in required");
  });

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
            xray_capabilities: { mieru_udp_target: 1 },
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
          xray_capabilities: { mieru_udp_target: 1 },
          api_port: 46736,
          config_path: "/usr/local/etc/xray/config.json",
          config_modified: false,
          config_added_sections: [],
          inbound_count: 1,
          client_count: 2,
          protocol_counts: { vless: 1 },
          traffic: { uplink: 100, downlink: 200 },
          user_traffic: { uplink: 12, downlink: 34 },
          traffic_reported_at: "2026-08-27T00:00:02Z",
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
              traffic: { uplink: 100, downlink: 200 },
              user_traffic: { uplink: 12, downlink: 34 },
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
    expect(response.xray_capabilities.mieru_udp_target).toBe(1);
    expect(response.inbounds[0]?.client_count).toBe(2);
    expect(response.traffic.uplink).toBe(100);
    expect(response.inbounds[0]?.user_traffic.downlink).toBe(34);
  });

  it("gets Xray runtime tunnel inventory without license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          has_config: true,
          source_snapshot_id: "snap_1",
          tunnel_count: 1,
          chain_count: 1,
          tunnels: [
            {
              kind: "routed",
              tag: "tunnel-routed",
              listen_port: 443,
              target_address: "2001:db8::10",
              target_port: 443,
              network: null,
              inbound_tag: "vless-443",
              match_domains: ["example.com"],
              match_ips: ["1.1.1.1"],
              rule_index: 0,
            },
          ],
          chains: [
            {
              label: "relay",
              entry_port: 19000,
              final_target: "10.0.0.3:9001",
              hops: [
                {
                  tag: "tunnel-relay-h0",
                  listen_port: 19000,
                  target_address: "10.0.0.2",
                  target_port: 9000,
                },
              ],
            },
          ],
          warnings: [],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await getXrayRuntimeTunnelInventory("srv_1", fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/xray/runtime/tunnels");
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.tunnels[0]?.target_address).toBe("2001:db8::10");
    expect(response.chains[0]?.label).toBe("relay");
  });

  it("queues Xray runtime tunnel deletes with JSON body", async () => {
    let requestUrl = "";
    let method = "";
    let headers: HeadersInit | undefined;
    let body: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      method = init?.method ?? "";
      headers = init?.headers;
      body = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          has_config: true,
          source_snapshot_id: "snap_1",
          target_kind: "routed",
          target_tag: "tunnel-routed",
          target_label: null,
          command_previews: [
            {
              method: "POST",
              path: "/api/child/routing",
              body: { action: "remove_rule", index: 0 },
            },
            {
              method: "POST",
              path: "/api/child/outbounds",
              body: { action: "remove", tag: "tunnel-routed" },
            },
          ],
          commands: [
            {
              id: "cmd_1",
              server_id: "srv_1",
              request_id: "srv_1-tunnel-delete",
              method: "POST",
              path: "/api/child/routing",
              query: "",
              body: { action: "remove_rule", index: 0 },
              timeout_ms: 60000,
              stream: false,
              status: "pending",
              attempts: 0,
              created_at: "2026-08-27T00:00:00Z",
              updated_at: "2026-08-27T00:00:00Z",
            },
          ],
          scan_command: null,
          command_count: 2,
          warnings: [],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await deleteXrayRuntimeTunnel(
      "srv_1",
      {
        kind: "routed",
        tag: "tunnel-routed",
        rule_index: 0,
        queue_agent_commands: true,
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/xray/runtime/tunnels/delete");
    expect(method).toBe("POST");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(body).toEqual({
      kind: "routed",
      tag: "tunnel-routed",
      rule_index: 0,
      queue_agent_commands: true,
    });
    expect(response.license_required).toBe(false);
    expect(response.command_previews[1]?.body).toEqual({
      action: "remove",
      tag: "tunnel-routed",
    });
  });

  it("queues Xray runtime tunnel chain creates with cross-server previews", async () => {
    let requestUrl = "";
    let method = "";
    let headers: HeadersInit | undefined;
    let body: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      method = init?.method ?? "";
      headers = init?.headers;
      body = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return new Response(
        JSON.stringify({
          label: "relay",
          entry_server_id: "srv_1",
          entry_host: "198.51.100.10",
          entry_port: 19000,
          final_target: "service.internal:443",
          hops: [
            {
              server_id: "srv_1",
              server_name: "entry",
              tag: "tunnel-relay-h0",
              listen_port: 19000,
              target_address: "middle.example.com",
              target_port: 20000,
            },
          ],
          command_previews: [
            {
              server_id: "srv_1",
              server_name: "entry",
              hop_index: 0,
              method: "POST",
              path: "/api/child/inbounds",
              body: {
                action: "add",
                inbound: {
                  tag: "tunnel-relay-h0",
                  protocol: "tunnel",
                  port: 19000,
                  settings: {
                    address: "middle.example.com",
                    port: 20000,
                    network: "tcp,udp",
                  },
                },
              },
            },
          ],
          commands: [],
          scan_commands: [],
          command_count: 1,
          warnings: [],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await createXrayRuntimeTunnelChain(
      {
        label: "relay",
        server_ids: ["srv_1", "srv_2"],
        entry_port: 19000,
        target_address: "service.internal",
        target_port: 443,
        queue_agent_commands: true,
        queue_scan_after_apply: true,
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/xray/runtime/tunnel-chains");
    expect(method).toBe("POST");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(body).toEqual({
      label: "relay",
      server_ids: ["srv_1", "srv_2"],
      entry_port: 19000,
      target_address: "service.internal",
      target_port: 443,
      queue_agent_commands: true,
      queue_scan_after_apply: true,
    });
    expect(response.license_required).toBe(false);
    expect(response.command_previews[0]?.body).toEqual({
      action: "add",
      inbound: {
        tag: "tunnel-relay-h0",
        protocol: "tunnel",
        port: 19000,
        settings: {
          address: "middle.example.com",
          port: 20000,
          network: "tcp,udp",
        },
      },
    });
  });

  it("queues Xray runtime tunnel deploy plans for one server", async () => {
    let requestUrl = "";
    let method = "";
    let headers: HeadersInit | undefined;
    let body: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      method = init?.method ?? "";
      headers = init?.headers;
      body = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          server_name: "edge",
          domain: "gateway.example.com",
          proxy_domain: "proxy.example.com",
          cert_name: "_.example.com",
          nginx_config: "http { include servers/*.conf; }",
          domain_config: "server { server_name gateway.example.com; }",
          xray_config: "{\"inbounds\":[]}",
          command_previews: [
            {
              step: "setup_tunnel_nginx",
              method: "POST",
              path: "/api/child/nginx/setup-ssl",
              body: { domain: "gateway.example.com" },
            },
          ],
          commands: [],
          scan_command_preview: { step: "scan_runtime", method: "POST", path: "/api/child/scan" },
          scan_command: null,
          command_count: 1,
          warnings: ["current_config_has_user_content"],
          license_required: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const response = await deployXrayRuntimeTunnel(
      "srv_1",
      {
        domain: "gateway.example.com",
        proxy_domain: "proxy.example.com",
        site_type: "proxy",
        site_value: "http://127.0.0.1:12889",
        cert_name: "*.example.com",
        clear_stream_port: true,
        restart_xray: true,
        force: true,
        queue_agent_commands: true,
        queue_scan_after_apply: true,
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/servers/srv_1/xray/runtime/tunnel-deploy");
    expect(method).toBe("POST");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(body).toEqual({
      domain: "gateway.example.com",
      proxy_domain: "proxy.example.com",
      site_type: "proxy",
      site_value: "http://127.0.0.1:12889",
      cert_name: "*.example.com",
      clear_stream_port: true,
      restart_xray: true,
      force: true,
      queue_agent_commands: true,
      queue_scan_after_apply: true,
    });
    expect(response.license_required).toBe(false);
    expect(response.command_previews[0]?.path).toBe("/api/child/nginx/setup-ssl");
    expect(response.scan_command_preview?.path).toBe("/api/child/scan");
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

  it("handles Xray config recovery status, accept, and apply requests", async () => {
    const requests: Array<{ url: string; method: string; headers?: HeadersInit; body?: unknown }> =
      [];
    const fetcher: typeof fetch = async (input, init) => {
      requests.push({
        url: input.toString(),
        method: init?.method ?? "GET",
        headers: init?.headers,
        body: init?.body ? JSON.parse(init.body.toString()) : undefined,
      });
      if (input.toString().endsWith("/recovery?with_config=true")) {
        return new Response(
          JSON.stringify({
            server_id: "srv_1",
            has_pending: true,
            has_current: true,
            pending: {
              id: "snap_pending",
              server_id: "srv_1",
              config_hash: "pendinghash",
              source: "agent_report",
              status: "pending_recovery",
              size_bytes: 48,
              config: "{\"inbounds\":[]}",
              created_at: "2026-08-27T00:01:00Z",
            },
            current: {
              id: "snap_current",
              server_id: "srv_1",
              config_hash: "currenthash",
              source: "master_write",
              status: "current",
              size_bytes: 42,
              config: "{\"inbounds\":[{\"tag\":\"vless-443\"}]}",
              created_at: "2026-08-27T00:00:00Z",
            },
            license_required: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (input.toString().endsWith("/recovery/accept")) {
        return new Response(
          JSON.stringify({
            server_id: "srv_1",
            current: {
              id: "snap_pending",
              server_id: "srv_1",
              config_hash: "pendinghash",
              source: "manual_accept",
              status: "current",
              size_bytes: 48,
              created_at: "2026-08-27T00:01:00Z",
            },
            snapshots: [],
            license_required: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          server_id: "srv_1",
          snapshot: {
            id: "snap_current",
            server_id: "srv_1",
            config_hash: "currenthash",
            source: "master_write",
            status: "current",
            size_bytes: 42,
            created_at: "2026-08-27T00:00:00Z",
          },
          commands: [
            {
              id: "cmd_test",
              server_id: "srv_1",
              request_id: "srv_1-test",
              method: "POST",
              path: "/api/child/xray/test-config",
              query: "",
              body: { config: "{\"inbounds\":[{\"tag\":\"vless-443\"}]}" },
              timeout_ms: 45000,
              stream: false,
              status: "pending",
              attempts: 0,
              created_at: "2026-08-27T00:00:00Z",
              updated_at: "2026-08-27T00:00:00Z",
            },
          ],
          command_count: 1,
          merged_agent_only_count: 1,
          warnings: [],
          license_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    };

    const statusResponse = await getXrayConfigSnapshotRecovery(
      "srv_1",
      { withConfig: true },
      fetcher,
    );
    const acceptResponse = await acceptXrayConfigPendingRecovery("srv_1", fetcher);
    const applyResponse = await applyXrayConfigRecovery(
      "srv_1",
      { restart_xray: true, merge_agent_only: true, command_timeout_ms: 45_000 },
      fetcher,
    );

    expect(statusResponse.has_pending).toBe(true);
    expect(statusResponse.pending?.status).toBe("pending_recovery");
    expect(acceptResponse.current.source).toBe("manual_accept");
    expect(applyResponse.commands[0]?.path).toBe("/api/child/xray/test-config");
    expect(requests).toEqual([
      {
        url: "/api/v1/servers/srv_1/xray/config-snapshots/recovery?with_config=true",
        method: "GET",
        headers: undefined,
        body: undefined,
      },
      {
        url: "/api/v1/servers/srv_1/xray/config-snapshots/recovery/accept",
        method: "POST",
        headers: undefined,
        body: undefined,
      },
      {
        url: "/api/v1/servers/srv_1/xray/config-snapshots/recovery/apply",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: { restart_xray: true, merge_agent_only: true, command_timeout_ms: 45_000 },
      },
    ]);
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

    await queueAgentOperation("srv_1", "xray_takeover_external", { confirm: true, expected_sha256: "a".repeat(64) }, fetcher);

    expect(requestUrl).toBe("/api/v1/servers/srv_1/operations/xray/takeover-external");
    expect(JSON.parse(body)).toEqual({ confirm: true, expected_sha256: "a".repeat(64) });
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
