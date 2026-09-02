import { afterEach, describe, expect, it, vi } from "vitest";
import { completeInitialSetup, getInitialSetupStatus, InitialSetupError, prepareInitialRestore, setupErrorMessage, uploadInitialRestore, validateSetupInput, type InitialSetupInput } from "./initial-setup";

const input: InitialSetupInput = { username: "admin", password: "  private-password  ", site_title: "  中文 🧭 ", brand_title: "站点", email: " operator@example.test ", nickname: " 运维  管理员 ", avatar_url: "https://cdn.example.test/avatar.png", confirm_new_install: true };
const ready = { configured: false, available: true };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("first-run setup transport", () => {
  it("uses anonymous same-origin requests without an initialization credential", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(ready)).mockResolvedValueOnce(response({ configured: true, login_required: true }, 201));
    expect(await getInitialSetupStatus(fetcher)).toEqual(ready);
    await completeInitialSetup(input, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const [url, options] of fetcher.mock.calls) {
      expect(url).toBe("/api/v1/setup");
      expect(options).toMatchObject({ credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" });
      expect(new Headers(options?.headers).has("X-Open-Node-Setup-Token")).toBe(false);
    }
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({ ...input, site_title: "中文 🧭", email: "operator@example.test", nickname: "运维 管理员" });
  });
  it.each([
    { confirm_new_install: false }, { confirm_new_install: "true" },
    { username: "invalid space" }, { password: "short" }, { password: "a".repeat(1025) },
    { site_title: "\u202eabc" }, { brand_title: "a".repeat(41) }, { email: "invalid" },
    { nickname: "a".repeat(121) }, { avatar_url: "http://example.test/avatar.png" },
    { nickname: "控制\n字符" }, { avatar_url: "https://user:secret@example.test/avatar.png" },
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
  it("uses the setup credential for a raw restore upload and one explicit preparation", async () => {
    const id = "01234567-89ab-4cde-8fab-0123456789ab", restored = "11234567-89ab-4cde-8fab-0123456789ab";
    const upload = { id, size: 22, sha256: "a".repeat(64), expires_at: "2026-09-01T12:00:00Z", license_required: false };
    const prepared = { id: restored, restart_required: true, automatic_restart: true, license_required: false };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(upload, 201)).mockResolvedValueOnce(response(prepared));
    const file = new Blob([new Uint8Array(22)]), token = "a".repeat(43);
    expect(await uploadInitialRestore(file, token, fetcher)).toEqual(upload);
    const payload = { setup_token: token, format: "plain" as const, identity: "", subscriber_totp_key: "",
      confirm_replace_instance: true as const, confirm_trusted_backup: true as const };
    expect(await prepareInitialRestore(id, payload, fetcher)).toEqual(prepared);
    expect(fetcher.mock.calls[0]![0]).toBe("/api/v1/setup/restore-uploads");
    expect(fetcher.mock.calls[0]![1]?.body).toBe(file);
    expect(new Headers(fetcher.mock.calls[0]![1]?.headers).get("X-Open-Node-Setup-Token")).toBe(token);
    expect(fetcher.mock.calls[1]![0]).toBe(`/api/v1/setup/restore-uploads/${id}/prepare`);
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual(payload);
    expect(String(fetcher.mock.calls[0]![0]) + String(fetcher.mock.calls[1]![0])).not.toContain(token);
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
