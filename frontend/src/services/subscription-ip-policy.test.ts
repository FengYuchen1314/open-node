import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProductUserIpPolicy, updateProductUserIpPolicy } from "./subscriptions";
import { clearSubscriberSession, subscriberIpPolicy, subscriberState, updateSubscriberIpPolicy } from "./subscriber-auth";

const policy = {
  username: "group/alice",
  enabled: true,
  networks: ["192.0.2.8/32"],
  updated_at: "2026-08-29T08:00:00Z",
  license_required: false as const,
};
const response = () => new Response(JSON.stringify(policy), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  clearSubscriberSession();
  subscriberState.session = { authenticated: true, username: "group/alice", csrf_token: "subscriber-csrf", requires_2fa: false, challenge: null };
});

describe("subscription IP policy services", () => {
  it("uses encoded administrator fallback paths for special usernames", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response());
    expect((await getProductUserIpPolicy("group/alice", fetcher)).networks).toEqual(["192.0.2.8/32"]);
    await updateProductUserIpPolicy("group/alice", ["192.0.2.8"], fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/user-subscription-ip-policy?username=group%2Falice");
    expect(fetcher.mock.calls[1][1]?.method).toBe("PUT");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ networks: ["192.0.2.8"] });
  });

  it("keeps subscriber policy updates in the subscriber cookie and CSRF realm", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response());
    await subscriberIpPolicy(fetcher);
    await updateSubscriberIpPolicy(["192.0.2.8"], fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/account/subscription-ip-policy");
    const update = fetcher.mock.calls[1][1];
    expect(update?.credentials).toBe("include");
    expect(new Headers(update?.headers).get("X-CSRF-Token")).toBe("subscriber-csrf");
    expect(JSON.parse(String(update?.body))).toEqual({ networks: ["192.0.2.8"] });
  });
});
