import { afterEach, describe, expect, it, vi } from "vitest";
import { authState } from "./auth";
import { certificateRequest } from "./certificates";

afterEach(() => { vi.unstubAllGlobals(); authState.session = null; });

describe("certificate requests", () => {
  it("uses authenticated writes and never puts credential values in the URL", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: "provider" })));
    vi.stubGlobal("fetch", fetcher);
    const body = { credentials: { CF_DNS_API_TOKEN: "private-token" } };
    expect(await certificateRequest("/providers", "POST", body)).toEqual({ id: "provider" });
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/certificates/providers");
    const options = fetcher.mock.calls[0][1];
    expect(options?.credentials).toBe("include");
    expect(new Headers(options?.headers).get("X-CSRF-Token")).toBe("csrf");
    expect(JSON.parse(options?.body as string)).toEqual(body);
  });

  it("does not expose nested validation inputs", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      detail: [{ input: "private-key" }],
    }), { status: 422 })));
    await expect(certificateRequest("/import", "POST", {})).rejects.toThrow("证书请求无效。");
  });

  it("retains actionable safe server errors", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      detail: "A certificate job is already active",
    }), { status: 409 })));
    await expect(certificateRequest("/id/renew", "POST", {})).rejects.toThrow("已有证书任务正在执行。");
  });

  it.each([
    "上游密钥 private-key", "A certificate job is already active private-key",
    [{ msg: "A certificate job is already active", input: "private-key" }],
  ].map(detail => ({ detail })))("keeps the certificate string-only error boundary before localization", async ({ detail }) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422 })));
    await expect(certificateRequest("/import", "POST", {})).rejects.toThrow("证书请求无效。");
  });
});
