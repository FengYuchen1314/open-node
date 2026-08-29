import { beforeEach, describe, expect, it, vi } from "vitest";
import { subscriberState } from "./subscriber-auth";
import {
  createSubscriberPrivateRoute,
  deleteSubscriberPrivateRoute,
  listPrivateRoutes,
  listSubscriberPrivateRoutes,
  updatePrivateRoutePolicy,
} from "./private-routed-nodes";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

beforeEach(() => {
  subscriberState.session = {
    authenticated: true,
    username: "alice",
    csrf_token: "subscriber-csrf",
    requires_2fa: false,
    challenge: null,
  };
});

describe("private routed nodes", () => {
  it("uses the isolated subscriber realm for self-service lifecycle", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({ nodes: [] }));
    await listSubscriberPrivateRoutes(fetcher);
    await createSubscriberPrivateRoute(
      { label: "My-Exit", parent_id: "parent", target_node_id: "target" },
      fetcher,
    );
    await deleteSubscriberPrivateRoute("route/id", fetcher);
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      "/api/v1/account/private-routed-nodes",
      "/api/v1/account/private-routed-nodes",
      "/api/v1/account/private-routed-nodes/route%2Fid",
    ]);
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe(
      "subscriber-csrf",
    );
    expect(fetcher.mock.calls[2][1]?.method).toBe("DELETE");
  });

  it("loads routes and updates policy in the administrator realm", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({ nodes: [] }));
    await listPrivateRoutes(fetcher);
    await updatePrivateRoutePolicy(
      { enabled: true, max_nodes: 2, daily_limit: 5 },
      fetcher,
    );
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/private-routed-nodes");
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/private-routed-nodes/policy");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
      enabled: true,
      max_nodes: 2,
      daily_limit: 5,
    });
  });
});
