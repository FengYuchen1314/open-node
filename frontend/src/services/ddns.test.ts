import { describe, expect, it, vi } from "vitest";

import type { DDNSServer } from "../domain/ddns";
import { loadDDNS, saveDDNS, syncDDNS } from "./ddns";

const now = "2026-09-01T00:00:00Z";
const item: DDNSServer = {
  server_id: "11111111-1111-4111-8111-111111111111", server_name: "动态节点",
  server_status: "connected", enabled: true,
  provider_id: "22222222-2222-4222-8222-222222222222", provider_name: "主 DNS",
  provider_type: "cloudflare", pull_address: "edge.example.com", pull_address_v6: null,
  ip_address: "203.0.113.2", ip_address_v6: "2001:db8::2", ipv6_enabled: true,
  last_synced_at: now, last_error: null, pending: false, revision: 1,
  license_required: false,
};
const workspace = { servers: [item], providers: [{ id: item.provider_id!, name: "主 DNS", provider: "cloudflare", supported: true }], license_required: false as const };
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

describe("DDNS service", () => {
  it("parses workspace, uses revision-fenced saves and queues sync", async () => {
    const fetcher = vi.fn()
      .mockImplementationOnce(() => response(workspace))
      .mockImplementationOnce(() => response({ ...item, revision: 2 }))
      .mockImplementationOnce(() => response({ server: { ...item, pending: true }, queued: true, license_required: false }));
    expect((await loadDDNS(fetcher)).servers[0]).toEqual(item);
    expect((await saveDDNS(item, { enabled: true, provider_id: item.provider_id, pull_address: item.pull_address, pull_address_v6: null }, fetcher)).revision).toBe(2);
    expect((await syncDDNS(item, fetcher)).pending).toBe(true);
    expect(fetcher.mock.calls.map(call => [String(call[0]), call[1]?.method])).toEqual([
      ["/api/v1/ddns", undefined], [`/api/v1/ddns/${item.server_id}`, "PUT"],
      [`/api/v1/ddns/${item.server_id}/sync`, "POST"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).expected_revision).toBe(1);
  });

  it("maps only fixed errors and does not echo provider bodies", async () => {
    const secret = "UPSTREAM-PROVIDER-SECRET";
    const fetcher = vi.fn(() => response({ code: "unknown", detail: secret }, 502));
    await expect(loadDDNS(fetcher)).rejects.toThrow("未能确认 DDNS 操作结果");
    try { await loadDDNS(fetcher); } catch (error) { expect(String(error)).not.toContain(secret); }
  });

  it("rejects extra or partial success fields", async () => {
    const fetcher = vi.fn(() => response({ ...workspace, providers: [{ ...workspace.providers[0], credential: "leak" }] }));
    await expect(loadDDNS(fetcher)).rejects.toThrow("未能确认 DDNS 操作结果");
  });
});
