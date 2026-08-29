import { describe, expect, it, vi } from "vitest";
import type { TemporarySubscriptionCreate } from "../domain/temporary-subscriptions";
import {
  createTemporarySubscription,
  deleteTemporarySubscription,
  listTemporarySubscriptions,
} from "./temporary-subscriptions";

const share = {
  id: "share-id",
  username: "alice",
  label: "Weekend",
  node_ids: ["node-id"],
  max_access: 2,
  access_count: 0,
  expires_at: "2026-08-29T05:00:00Z",
  status: "active",
  subscription_url: "https://panel.example/t/private-code",
  created_at: "2026-08-29T04:00:00Z",
  updated_at: "2026-08-29T04:00:00Z",
} as const;

describe("temporary subscription service", () => {
  it("lists, creates and deletes shares", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      calls.push([url, init]);
      return new Response(JSON.stringify(init?.method === "DELETE"
        ? { id: share.id, deleted: true, license_required: false }
        : init?.method === "POST" ? share : { subscriptions: [share], license_required: false }), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const payload: TemporarySubscriptionCreate = {
      username: "alice", label: "Weekend", node_ids: ["node-id"], max_access: 2,
      expires_in_seconds: 300,
    };
    expect((await listTemporarySubscriptions(fetcher)).subscriptions).toEqual([share]);
    expect(await createTemporarySubscription(payload, fetcher)).toEqual(share);
    expect((await deleteTemporarySubscription(share.id, fetcher)).deleted).toBe(true);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/temporary-subscriptions",
      "/api/v1/temporary-subscriptions",
      "/api/v1/temporary-subscriptions/share-id",
    ]);
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual(payload);
    expect(calls[2][1]?.method).toBe("DELETE");
  });
});
