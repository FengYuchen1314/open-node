import { describe, expect, it, vi } from "vitest";
import { listCamouflagePools } from "./camouflage-pools";

describe("camouflage pool API", () => {
  it("loads the authenticated catalog and encodes an optional region filter", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      schema_version: 1, reviewed_at: "2026-09-02", probe_vantage: "192.0.2.1", measurement_notice: "notice",
      sources: {}, pools: [], license_required: false,
    })));
    await listCamouflagePools("united-kingdom", fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/camouflage-pools?region=united-kingdom");
  });

  it("keeps validation failures behind the public request error", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: "internal pool path" }), {
      status: 422, headers: { "Content-Type": "application/json" },
    }));
    await expect(listCamouflagePools(undefined, fetcher)).rejects.toThrow("伪装池");
  });
});
