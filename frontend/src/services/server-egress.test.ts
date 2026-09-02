import { describe, expect, it, vi } from "vitest";

import {
  applyServerEgress,
  getServerEgressCatalog,
  previewServerEgress,
  previewServerEgressRemoval,
  removeServerEgress,
} from "./server-egress";

const ok = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));

describe("server egress service", () => {
  it("uses the catalog, preview, apply and revision-bound removal contracts", async () => {
    const fetcher = vi.fn().mockImplementation((_url: string, init?: RequestInit) => ok(init?.body ? JSON.parse(String(init.body)) : { candidates: [] }));
    await getServerEgressCatalog("server/id", fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/servers/server%2Fid/egress");
    const preview = { target_node_id: "node-id", promote_to_default: true, routing: { domains: ["geosite:cn"], ips: [], inbound_tags: [], users: [], protocols: [] } };
    await previewServerEgress("server-id", preview, fetcher);
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/servers/server-id/egress/preview");
    expect(JSON.parse(String(fetcher.mock.calls[1][1].body))).toEqual(preview);
    await applyServerEgress("server-id", { ...preview, expected_preview_revision: "a".repeat(64), dispatch: true }, fetcher);
    expect(fetcher.mock.calls[2][0]).toBe("/api/v1/servers/server-id/egress/apply");
    expect(JSON.parse(String(fetcher.mock.calls[2][1].body))).toEqual({ ...preview, expected_preview_revision: "a".repeat(64), dispatch: true });
    await previewServerEgressRemoval("server-id", { target_node_id: "node-id" }, fetcher);
    expect(fetcher.mock.calls[3][0]).toBe("/api/v1/servers/server-id/egress/remove/preview");
    expect(JSON.parse(String(fetcher.mock.calls[3][1].body))).toEqual({ target_node_id: "node-id" });
    await removeServerEgress("server-id", { target_node_id: "node-id", expected_preview_revision: "b".repeat(64), dispatch: true }, fetcher);
    expect(fetcher.mock.calls[4][0]).toBe("/api/v1/servers/server-id/egress/remove");
    expect(JSON.parse(String(fetcher.mock.calls[4][1].body))).toEqual({ target_node_id: "node-id", expected_preview_revision: "b".repeat(64), dispatch: true });
  });
});
