import { describe, expect, it, vi } from "vitest";

import type { FederatedServer, FederationCommand, ServerShare } from "../domain/server-sharing";
import {
  addFederatedServer,
  createServerShare,
  deleteFederatedServer,
  getFederatedCommand,
  listFederatedServers,
  listServerShares,
  manageFederatedServer,
  refreshFederatedServer,
  revokeServerShare,
} from "./server-sharing";

const now = "2026-09-01T00:00:00Z";
const share: ServerShare = {
  id: "11111111-1111-4111-8111-111111111111",
  server_id: "22222222-2222-4222-8222-222222222222",
  label: "租户甲",
  allow_manage_xray: false,
  revision: 0,
  created_at: now,
  license_required: false,
};
const imported: FederatedServer = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "异地节点",
  owner_url: "https://owner.example",
  prefix: "site-",
  revision: 0,
  info: {
    name: "上游节点", status: "connected", ip_address: "198.51.100.2",
    ip_address_v6: null, domain: "edge.example", domain_v6: null, ipv6_enabled: false,
    xray_mode: "external", traffic_limit: 0, traffic_reset_day: 0, traffic_used: 10,
    current_upload_speed: 1, current_download_speed: 2, xray_running: true,
    xray_version: "26.3.27", last_heartbeat: now, allow_manage_xray: false, license_required: false,
  },
  last_synced_at: now,
  created_at: now,
  license_required: false,
};
const command: FederationCommand = {
  id: "44444444-4444-4444-8444-444444444444",
  method: "GET",
  path: "/api/child/inbounds",
  status: "succeeded",
  result_status: 200,
  result_body: { inbounds: [] },
  failed: false,
  created_at: now,
  completed_at: now,
  license_required: false,
};
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), {
    status, headers: status === 204 ? undefined : { "Content-Type": "application/json" },
  }));
}

describe("server sharing service", () => {
  it("uses encoded private routes and parses every owner/consumer mutation", async () => {
    const queue = [
      { shares: [share], license_required: false },
      { share, share_token: "A".repeat(43), license_required: false },
      { revoked: true, cleanup_commands: [command], license_required: false },
      { servers: [imported], license_required: false },
      imported, { ...imported, revision: 1 }, command, command, null,
    ];
    const fetcher = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      response(queue.shift(), init?.method === "POST" && String(_url).endsWith("/delete") ? 204 : 200));
    expect((await listServerShares(share.server_id, fetcher)).shares).toEqual([share]);
    expect((await createServerShare(share.server_id, "租户甲", false, fetcher)).share_token).toBe("A".repeat(43));
    expect((await revokeServerShare(share, true, fetcher)).cleanup_commands).toEqual([command]);
    expect((await listFederatedServers(fetcher)).servers).toEqual([imported]);
    expect((await addFederatedServer({ owner_url: imported.owner_url, share_token: "A".repeat(43), name: imported.name, prefix: imported.prefix }, fetcher)).id).toBe(imported.id);
    expect((await refreshFederatedServer(imported, fetcher)).revision).toBe(1);
    expect((await manageFederatedServer(imported, { method: "GET", path: "/api/child/inbounds", body: null, timeout_ms: 30000 }, fetcher)).id).toBe(command.id);
    expect((await getFederatedCommand(imported, command.id, fetcher)).status).toBe("succeeded");
    await deleteFederatedServer(imported, fetcher);
    expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual([
      `/api/v1/server-shares?server_id=${share.server_id}`,
      "/api/v1/server-shares", `/api/v1/server-shares/${share.id}/revoke`,
      "/api/v1/server-federation", "/api/v1/server-federation",
      `/api/v1/server-federation/${imported.id}/refresh`,
      `/api/v1/server-federation/${imported.id}/manage`,
      `/api/v1/server-federation/${imported.id}/commands/${command.id}`,
      `/api/v1/server-federation/${imported.id}/delete`,
    ]);
    const addBody = JSON.parse(String(fetcher.mock.calls[4][1]?.body));
    expect(addBody.share_token).toBe("A".repeat(43));
    expect(String(fetcher.mock.calls[4][0])).not.toContain(addBody.share_token);
  });

  it("maps only fixed error codes and never echoes an owner response or token", async () => {
    const secret = "SECRET-UPSTREAM-TOKEN";
    const fetcher = vi.fn(() => response({ code: "unknown", detail: secret }, 502));
    await expect(listFederatedServers(fetcher)).rejects.toThrow("未能确认服务器共享操作结果");
    try { await listFederatedServers(fetcher); } catch (error) { expect(String(error)).not.toContain(secret); }
  });

  it("rejects malformed success bodies instead of accepting partial state", async () => {
    const fetcher = vi.fn(() => response({ servers: [{ ...imported, token_secret: "leak" }], license_required: false }));
    await expect(listFederatedServers(fetcher)).rejects.toThrow("未能确认服务器共享操作结果");
  });
});
