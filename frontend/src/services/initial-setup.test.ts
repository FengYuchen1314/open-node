import { afterEach, describe, expect, it, vi } from "vitest";
import { completeInitialSetup, getInitialSetupStatus, InitialSetupError, setupErrorMessage, validateSetupInput, type InitialSetupInput } from "./initial-setup";

const input: InitialSetupInput = { setup_token: "a".repeat(43), username: "admin", password: "  private-password  ", site_title: "  中文 🧭 ", brand_title: "站点", confirm_new_install: true };
const ready = { configured: false, available: true, expires_at: "2026-09-01T12:00:00Z", token_required: true };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("first-run setup transport", () => {
  it("uses anonymous same-origin requests without placing secrets in URLs or headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(ready)).mockResolvedValueOnce(response({ configured: true, login_required: true }, 201));
    expect(await getInitialSetupStatus(fetcher)).toEqual(ready);
    await completeInitialSetup(input, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const [url, options] of fetcher.mock.calls) {
      expect(url).toBe("/api/v1/setup");
      expect(options).toMatchObject({ credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" });
      expect(JSON.stringify(options?.headers)).not.toContain(input.setup_token);
    }
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({ ...input, site_title: "中文 🧭" });
  });
  it.each([
    { confirm_new_install: false }, { confirm_new_install: "true" }, { setup_token: "中".repeat(43) },
    { username: "invalid space" }, { password: "short" }, { password: "a".repeat(1025) },
    { site_title: "\u202eabc" }, { brand_title: "a".repeat(41) },
  ])("rejects invalid input before sending %j", async changes => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(completeInitialSetup({ ...input, ...changes } as InitialSetupInput, fetcher)).rejects.toBeInstanceOf(InitialSetupError);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("counts password Unicode code points and preserves spaces", () => {
    expect(validateSetupInput({ ...input, password: "😀".repeat(12) }).password).toBe("😀".repeat(12));
    expect(validateSetupInput(input).password).toBe(input.password);
  });
  it.each([
    { ...ready, secret: "PRIVATE" }, { ...ready, configured: true }, { ...ready, token_required: false },
    { ...ready, expires_at: null }, { ...ready, available: false }, { configured: false }, [],
  ])("rejects inconsistent or unexpected status DTOs %j", async body => {
    await expect(getInitialSetupStatus(vi.fn<typeof fetch>().mockResolvedValue(response(body)))).rejects.toBeInstanceOf(InitialSetupError);
  });
  it("requires the exact completion contract and never follows with another POST", async () => {
    for (const body of [{ configured: true }, { configured: true, login_required: false }, { configured: true, login_required: true, token: "PRIVATE" }]) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body, 201));
      await expect(completeInitialSetup(input, fetcher)).rejects.toBeInstanceOf(InitialSetupError);
      expect(fetcher).toHaveBeenCalledOnce();
    }
  });
  it("never displays server text or mutated exception messages", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ code: "setup_ticket_invalid", detail: "PRIVATE" }, 403));
    const error = await completeInitialSetup(input, fetcher).catch(value => value as InitialSetupError);
    expect(error).toBeInstanceOf(InitialSetupError);
    (error as InitialSetupError).message = "PRIVATE";
    expect(setupErrorMessage(error)).toContain("凭证无效或已过期");
    expect(setupErrorMessage(new Error("PRIVATE"))).not.toContain("PRIVATE");
  });
  it("bounds response size and discards HTML responses", async () => {
    for (const value of [response("PRIVATE".repeat(2000)), new Response("PRIVATE", { headers: { "Content-Type": "text/html" } })]) {
      const error = await getInitialSetupStatus(vi.fn<typeof fetch>().mockResolvedValue(value)).catch(value => value);
      expect(error).toBeInstanceOf(InitialSetupError);
      expect(setupErrorMessage(error)).not.toContain("PRIVATE");
    }
  });
  it("times out a stalled request without replay", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener("abort", () => reject(new Error("PRIVATE")));
    }));
    const result = completeInitialSetup(input, fetcher).catch(value => value);
    await vi.advanceTimersByTimeAsync(15000);
    expect(await result).toBeInstanceOf(InitialSetupError);
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
