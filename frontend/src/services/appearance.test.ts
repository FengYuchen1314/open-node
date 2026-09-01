import { afterEach, describe, expect, it, vi } from "vitest";
import { AppearanceRequestError, appearanceErrorMessage, getAppearanceSettings, getPublicAppearance, updateAppearance, uploadAppearanceImage } from "./appearance";

const saved = { default_theme: "dark" as const, logo_url: "https://cdn.example.test/logo.png", wallpaper_url: "", license_required: false as const, revision: 3 };
const publicSaved = (({ revision: _revision, ...rest }) => rest)(saved);
const response = (value: unknown, status = 200, type = "application/json") => new Response(JSON.stringify(value), { status, headers: { "Content-Type": type } });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("appearance service contracts", () => {
  it("reads the public projection without session credentials", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(publicSaved));
    expect(await getPublicAppearance(fetcher)).toEqual(publicSaved);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/appearance", expect.objectContaining({ credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer" }));
  });
  it("uses exact versioned JSON for administrator saves", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(saved));
    expect(await updateAppearance({ ...publicSaved, expected_revision: 2 }, fetcher)).toEqual(saved);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/system-settings/appearance", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ ...publicSaved, expected_revision: 2 }),
      headers: { Accept: "application/json", "Content-Type": "application/json" },
    }));
  });
  it.each([
    { default_theme: "pixel" }, { expected_revision: 1.5 }, { logo_url: "http://example.test/a" },
    { logo_url: "https://localhost/a" }, { logo_url: "https://cdn.example.test:8443/a" },
    { logo_url: "https://[2001:db8::1]/a" },
    { wallpaper_url: "/api/v1/appearance/assets/logo/" + "a".repeat(64) },
  ])("rejects unsafe input before sending %j", async change => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(updateAppearance({ ...publicSaved, expected_revision: 2, ...change } as never, fetcher)).rejects.toBeInstanceOf(AppearanceRequestError);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("uploads raw bounded file data without using filename or a multipart envelope", async () => {
    const file = new File(["binary-image"], "PRIVATE-FILENAME.png", { type: "image/png" });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(saved));
    await uploadAppearanceImage("logo", 2, file, fetcher);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/system-settings/appearance/logo", expect.objectContaining({
      method: "POST", body: file, headers: { Accept: "application/json", "Content-Type": "image/png", "X-Appearance-Revision": "2" },
    }));
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("PRIVATE-FILENAME");
  });
  it.each([["logo", 2 * 1024 * 1024], ["wallpaper", 10 * 1024 * 1024]] as const)("enforces the %s client upload limit", async (slot, limit) => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(uploadAppearanceImage(slot, 0, new File([new Uint8Array(limit + 1)], "x"), fetcher)).rejects.toBeInstanceOf(AppearanceRequestError);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([
    { ...publicSaved, extra: "PRIVATE" }, { ...publicSaved, license_required: true },
    { ...publicSaved, default_theme: "pixel" }, { ...publicSaved, logo_url: "javascript:PRIVATE" },
    { ...publicSaved, revision: 1 }, [], null,
  ])("rejects unexpected public response %j", async value => {
    await expect(getPublicAppearance(vi.fn<typeof fetch>().mockResolvedValue(response(value)))).rejects.toBeInstanceOf(AppearanceRequestError);
  });
  it("requires exact settings response fields and safe error codes", async () => {
    const extra = vi.fn<typeof fetch>().mockResolvedValue(response({ ...saved, secret: "PRIVATE" }));
    await expect(getAppearanceSettings(extra)).rejects.toBeInstanceOf(AppearanceRequestError);
    const denied = vi.fn<typeof fetch>().mockResolvedValue(response({ code: "appearance_invalid_image", detail: "PRIVATE" }, 422));
    const error = await uploadAppearanceImage("logo", 0, new File(["x"], "x"), denied).catch(value => value as AppearanceRequestError);
    expect(appearanceErrorMessage(error)).toContain("格式无效");
    (error as AppearanceRequestError).message = "PRIVATE";
    expect(appearanceErrorMessage(error)).not.toContain("PRIVATE");
    expect(appearanceErrorMessage(new Error("PRIVATE"))).not.toContain("PRIVATE");
  });
  it("bounds JSON responses and does not replay a timed-out upload", async () => {
    await expect(getPublicAppearance(vi.fn<typeof fetch>().mockResolvedValue(response("PRIVATE".repeat(3000))))).rejects.toBeInstanceOf(AppearanceRequestError);
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_yes, no) => init?.signal?.addEventListener("abort", () => no(new Error("PRIVATE")))));
    const result = uploadAppearanceImage("logo", 0, new File(["x"], "x"), fetcher).catch(value => value);
    await vi.advanceTimersByTimeAsync(60000);
    expect(await result).toBeInstanceOf(AppearanceRequestError); expect(fetcher).toHaveBeenCalledOnce();
  });
});
