import { afterEach, describe, expect, it, vi } from "vitest";
import { getPublicProbePayload, getPublicProbeSeries, getPublicProbeStreamUrl, getPublicProbeTargets } from "./probe-public";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());
describe("anonymous public Probe client", () => {
  it("does not send either cookie domain, credentials, a referrer or a cacheable request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ enabled: true, servers: [], license_required: false }));
    await getPublicProbePayload(fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/public/probe-servers", {
      credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer", redirect: "error",
    });
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("X-CSRF-Token")).toBe(false);
  });
  it("sends a deliberately supplied Worker token only in its header", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({ targets: [] }));
    await getPublicProbeTargets("6h", fetcher, " private-worker-token ");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/public/probe-targets?range=6h");
    expect(url).not.toContain("private-worker-token");
    expect(new Headers(init?.headers).get("X-MMwx-Probe-Token")).toBe("private-worker-token");
    expect(init?.credentials).toBe("omit");
  });
  it("encodes history filters and never makes a management request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ success: true }));
    await getPublicProbeSeries(2, { range: "24h", metric: "ping", target: "a&b", all: true }, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/public/probe-series?server=2&range=24h&metric=ping&target=a%26b&all=1");
  });
  it("keeps public stream URLs free of tokens and browser state", () => {
    expect(getPublicProbeStreamUrl({ origin: "https://probe.example.test" })).toBe("wss://probe.example.test/api/v1/public/probe-ws");
    expect(getPublicProbeStreamUrl({ origin: "https://1.1.1.1:58090" })).toBe("wss://1.1.1.1:58090/api/v1/public/probe-ws");
    expect(getPublicProbeStreamUrl({ origin: "http://localhost:8000" })).toBe("ws://localhost:8000/api/v1/public/probe-ws");
  });
  it("preserves bounded API errors and handles a non-JSON denial", async () => {
    await expect(getPublicProbePayload(vi.fn<typeof fetch>().mockResolvedValue(json({ detail: "Probe access denied" }, 403)))).rejects.toThrow("无权访问此探针。");
    await expect(getPublicProbePayload(vi.fn<typeof fetch>().mockResolvedValue(new Response("Unavailable", { status: 503 })))).rejects.toThrow("503");
  });
});
