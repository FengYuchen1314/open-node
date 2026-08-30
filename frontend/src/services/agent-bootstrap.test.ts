import { afterEach, describe, expect, it, vi } from "vitest";
import { getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap } from "./agent-bootstrap";
import { authState } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
  authState.session = null;
});

describe("Agent bootstrap API", () => {
  it("separates a read-only status from ticket issuance and revocation", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetcher: typeof fetch = async (url, init) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify({ license_required: false }));
    };
    await getAgentBootstrap("server", fetcher);
    await issueAgentBootstrap("server", "http", fetcher);
    await revokeAgentBootstrap("server", fetcher);
    expect(calls.map(row => row.url)).toEqual(Array(3).fill("/api/v1/servers/server/bootstrap"));
    expect(calls.map(row => row.init?.method ?? "GET")).toEqual(["GET", "POST", "DELETE"]);
    expect(calls.every(row => row.init?.cache === "no-store")).toBe(true);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ transport: "http" });
    expect(calls[0].init?.body).toBeUndefined();
  });

  it("encodes the server path and never puts request data in a query string", async () => {
    const fetcher: typeof fetch = async url => {
      expect(String(url)).toBe("/api/v1/servers/server%2F%3Fsecret/bootstrap");
      return new Response("{}");
    };
    await issueAgentBootstrap("server/?secret", "auto", fetcher);
  });

  it("uses authenticated CSRF requests without caching command responses", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-test" };
    vi.stubGlobal("fetch", vi.fn(async (_url, init: RequestInit) => {
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-test");
      expect(init.credentials).toBe("include");
      expect(init.cache).toBe("no-store");
      return new Response("{}");
    }));
    await issueAgentBootstrap("server", "websocket");
    await revokeAgentBootstrap("server");
  });

  it.each([401, 409, 429, 503])("preserves a safe API failure (%s)", async status => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: "Installation is unavailable" }), { status });
    await expect(issueAgentBootstrap("server", "auto", fetcher)).rejects.toThrow("Installation is unavailable");
  });

  it("does not echo validation input or an HTML error page", async () => {
    const validation: typeof fetch = async () => new Response(JSON.stringify({
      detail: [{ input: "private-ticket", msg: "invalid" }],
    }), { status: 422 });
    await expect(issueAgentBootstrap("server", "auto", validation)).rejects.toThrow("Agent installation request failed (422)");
    const html: typeof fetch = async () => new Response("private proxy response", { status: 502 });
    await expect(getAgentBootstrap("server", html)).rejects.toThrow("Agent installation request failed (502)");
  });
});
