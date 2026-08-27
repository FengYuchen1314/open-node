import { describe, expect, it } from "vitest";

import { createServer, listServers } from "./inventory";

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
});
