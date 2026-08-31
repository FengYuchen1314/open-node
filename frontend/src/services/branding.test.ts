import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultBranding, normalizeBrandingText, type BrandingSettings } from "../domain/branding";
import { authState } from "./auth";
import { BrandingRequestError, brandingErrorMessage, getBrandingSettings, getPublicBranding, updateBrandingSettings } from "./branding";

const settings: BrandingSettings = { site_title: "站点标题 🧭", brand_title: "示例站点", revision: 4, license_required: false };
const publicSettings = { site_title: settings.site_title, brand_title: settings.brand_title, license_required: false };
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
async function failure(promise: Promise<unknown>): Promise<BrandingRequestError> {
  const error: unknown = await promise.catch(value => value);
  expect(error).toBeInstanceOf(BrandingRequestError);
  if (!(error instanceof BrandingRequestError)) throw new Error("Expected a fixed branding failure");
  return error;
}
beforeEach(() => { authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" }; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("branding Unicode contract", () => {
  it.each([
    ["  中文站点  ", "中文站点"], ["\u00a0站点\u3000", "站点"], ["👩‍💻", "👩‍💻"], ["A\u200cB", "A\u200cB"],
    ["!", "!"], ["１２３", "１２３"], ["e\u0301", "e\u0301"], ["<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"],
  ])("preserves valid text without NFC normalization: %j", (value, expected) => {
    expect(normalizeBrandingText(value, 80)).toBe(expected);
  });
  it("counts Unicode code points rather than UTF-16 units or grapheme clusters", () => {
    expect(normalizeBrandingText("😀".repeat(80), 80)).toBe("😀".repeat(80));
    expect(normalizeBrandingText("😀".repeat(81), 80)).toBeNull();
    expect(normalizeBrandingText("中".repeat(40), 40)).toBe("中".repeat(40));
    expect(normalizeBrandingText("中".repeat(41), 40)).toBeNull();
    expect(normalizeBrandingText("👩‍💻".repeat(14), 40)).toBeNull();
  });
  it.each([
    null, undefined, 123, true, [], {}, "", " \u3000 ", "\u200c\u200d", " \u200d ", "\u0301\ufe0f",
    "\nsite", "site\n", "site\r", "site\t", "\u0085site", "site\u0000", "site\u007f", "site\u009f",
    "site\u2028", "site\u2029", "site\u202e", "site\u2066", "\ufeffsite", "site\u00ad", "site\u200b", "site\ud800", "site\udfff",
  ])("rejects invalid original text without trimming away controls: %j", value => {
    expect(normalizeBrandingText(value, 80)).toBeNull();
    expect(normalizeBrandingText(value, 40)).toBeNull();
  });
});

describe("branding requests and strict responses", () => {
  it("reads public branding anonymously from the same origin with no session headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(publicSettings));
    expect(await getPublicBranding(fetcher)).toEqual(publicSettings);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/branding", expect.objectContaining({
      credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", headers: { Accept: "application/json" },
    }));
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("PRIVATE-CSRF");
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined();
  });
  it("uses the existing administrator session and CSRF mechanism for one atomic update", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(settings)).mockResolvedValueOnce(response({ ...settings, revision: 5 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await getBrandingSettings()).toEqual(settings);
    expect(await updateBrandingSettings({ expected_revision: 4, site_title: ` ${settings.site_title} `, brand_title: ` ${settings.brand_title} ` })).toEqual({ ...settings, revision: 5 });
    const [path, init] = fetcher.mock.calls[1];
    expect(path).toBe("/api/v1/system-settings/branding");
    expect(init?.credentials).toBe("include"); expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("PRIVATE-CSRF");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 4, site_title: settings.site_title, brand_title: settings.brand_title });
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("X-CSRF-Token")).toBe(false);
  });
  it.each([null, true, "4", -1, 4.5, Number.MAX_SAFE_INTEGER + 1, Number.POSITIVE_INFINITY])("rejects an invalid revision before sending: %j", async revision => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(updateBrandingSettings({ expected_revision: revision as number, site_title: "Site", brand_title: "Brand" }, fetcher)).rejects.toMatchObject({ code: "branding_invalid_request" });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects an invalid field without echoing it or sending either title", async () => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(updateBrandingSettings({ expected_revision: 0, site_title: "PRIVATE\nSECRET", brand_title: "Brand" }, fetcher)).rejects.toThrow("站点文字不符合要求");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([
    null, [], { ...publicSettings, site_title: true }, { ...publicSettings, brand_title: "\nSECRET" },
    { ...publicSettings, site_title: "\u200d" }, { ...publicSettings, brand_title: "😀".repeat(41) },
    { ...publicSettings, site_title: " Leading space" }, { ...publicSettings, brand_title: "Trailing space " },
    { ...publicSettings, license_required: true }, { ...publicSettings, license_required: 0 },
    { ...publicSettings, revision: 1 }, { ...publicSettings, detail: "PRIVATE" }, { brand_title: "Brand", license_required: false },
  ])("rejects malformed public values: %j", async value => {
    await expect(getPublicBranding(vi.fn<typeof fetch>().mockResolvedValue(response(value)))).rejects.toMatchObject({ status: null, code: null });
  });
  it.each([true, "4", -1, 1.5, Number.MAX_SAFE_INTEGER + 1])("requires an administrator revision to be a safe integer: %j", async revision => {
    await expect(getBrandingSettings(vi.fn<typeof fetch>().mockResolvedValue(response({ ...settings, revision })))).rejects.toMatchObject({ status: null });
  });
  it("does not silently repair a noncanonical administrator response", async () => {
    await expect(getBrandingSettings(vi.fn<typeof fetch>().mockResolvedValue(response({ ...settings, site_title: " Padded " })))).rejects.toMatchObject({ status: null });
  });
  it.each([
    { ...settings, revision: 4 }, { ...settings, revision: 6 }, { ...settings, revision: 5, site_title: "Unrelated" },
    { ...settings, revision: 5, brand_title: "Unrelated" }, { ...settings, revision: 5, brand_title: "SECRET\u202e" },
  ])("does not accept a stale, mismatched or unsafe save receipt: %j", async value => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(value));
    const error = await failure(updateBrandingSettings({ expected_revision: 4, ...publicSettings }, fetcher));
    expect(error).toBeInstanceOf(BrandingRequestError); expect(error.outcomeUnknown).toBe(true);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("accepts a safe maximum revision when reading, and preserves literal markup as text data", async () => {
    const value = { ...defaultBranding, brand_title: "<svg onload=alert(1)>", revision: Number.MAX_SAFE_INTEGER };
    expect(await getBrandingSettings(vi.fn<typeof fetch>().mockResolvedValue(response(value)))).toEqual(value);
  });
  it.each([
    [422, "branding_invalid_request", "站点文字不符合要求"],
    [409, "branding_revision_conflict", "站点文字已被其他操作修改"],
    [503, "branding_storage_unavailable", "站点文字存储暂不可用"],
    [401, "branding_invalid_request", "请重新登录管理员账户"],
    [403, null, "此操作需要管理员权限"],
  ] as const)("maps only fixed error information for HTTP %i", async (status, code, message) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ code, detail: "PRIVATE https://example.test/?token=secret", input: "PRIVATE" }, status));
    const error = await failure(getBrandingSettings(fetcher));
    expect(error).toBeInstanceOf(BrandingRequestError);
    error.message = "PRIVATE MUTATED ERROR";
    expect(brandingErrorMessage(error)).toContain(message);
    expect(brandingErrorMessage(error)).not.toMatch(/PRIVATE|secret|example\.test/);
  });
  it("fails closed for unknown codes and arbitrary Error messages, without replaying any request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ code: "branding_UNKNOWN_PRIVATE", detail: "PRIVATE" }, 500));
    const error = await failure(getBrandingSettings(fetcher));
    expect(brandingErrorMessage(error)).toBe("未能确认站点文字，请重新读取当前配置。");
    expect(brandingErrorMessage(new Error("站点文字已保存。PRIVATE"))).toBe("未能确认站点文字，请重新读取当前配置。");
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it.each([
    () => new Response("<html>PRIVATE</html>", { headers: { "Content-Type": "text/html" } }),
    () => new Response("{\"site_title\":", { headers: { "Content-Type": "application/json" } }),
    () => response({ ...publicSettings, ignored: "PRIVATE".repeat(2000) }),
    () => new Response(new Uint8Array([0xff, 0xfe]), { headers: { "Content-Type": "application/json" } }),
  ])("bounds and validates the response stream instead of rendering an error body", async makeResponse => {
    await expect(getPublicBranding(vi.fn<typeof fetch>().mockResolvedValue(makeResponse()))).rejects.toMatchObject({ status: null, code: null });
  });
  it("sanitizes network failures and aborts an unavailable public read without retrying", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((_path, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("PRIVATE network diagnostic")), { once: true });
    }));
    const result = getPublicBranding(fetcher).catch(value => value as BrandingRequestError);
    await vi.advanceTimersByTimeAsync(15000);
    expect(brandingErrorMessage(await result)).toBe("未能确认站点文字，请重新读取当前配置。");
    expect(fetcher).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
  });
});
