import { describe, expect, it } from "vitest";
import { getSubscriptionAccess, setProductUserActive, syncSubscriptionAccess } from "./subscriptions";

describe("subscription access", () => {
  it("encodes user names and preserves explicit enable/disable requests", async () => {
    const calls: Array<{ url: string; method?: string; body?: string | null }> = [];
    const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method, body: init?.body as string | undefined });
      return new Response(JSON.stringify({ username: "alice@example.com", managed: true, servers: [] }));
    };
    expect((await getSubscriptionAccess("alice@example.com", fetcher)).managed).toBe(true);
    await syncSubscriptionAccess("alice@example.com", fetcher);
    await setProductUserActive("alice@example.com", false, fetcher);
    await setProductUserActive("alice@example.com", true, fetcher);
    expect(calls).toEqual([
      { url: "/api/v1/users/alice%40example.com/access" },
      { url: "/api/v1/users/alice%40example.com/access/sync", method: "POST" },
      { url: "/api/v1/users/alice%40example.com/active", method: "PATCH", body: '{"is_active":false}' },
      { url: "/api/v1/users/alice%40example.com/active", method: "PATCH", body: '{"is_active":true}' },
    ]);
  });

  it("surfaces access and authorization failures", async () => {
    const fetcher = async () => new Response(JSON.stringify({ detail: "Access denied" }), { status: 403 });
    await expect(getSubscriptionAccess("alice", fetcher)).rejects.toThrow("Access denied");
    await expect(syncSubscriptionAccess("alice", fetcher)).rejects.toThrow("Access denied");
    await expect(setProductUserActive("alice", false, fetcher)).rejects.toThrow("Access denied");
  });
});
