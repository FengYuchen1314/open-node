import { describe, expect, it } from "vitest";

import { createServer } from "./inventory";

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
});
