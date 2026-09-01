import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "./auth";
import { AnnouncementRequestError, accountAnnouncements, deleteAnnouncement, listAnnouncements, publishAnnouncement } from "./announcements";

const item = {
  id: "11111111-1111-4111-8111-111111111111", type: "general" as const, title: "公告",
  body: "纯文本正文", created_at: "2026-09-01T00:00:00Z", expires_at: null,
};
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
async function failure(promise: Promise<unknown>) {
  const error: unknown = await promise.catch(value => value);
  expect(error).toBeInstanceOf(AnnouncementRequestError);
  if (!(error instanceof AnnouncementRequestError)) throw new Error("Expected a fixed announcement failure");
  return error;
}
beforeEach(() => { authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" }; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("announcement requests", () => {
  it("reads the bounded administrator projection through the existing session", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ announcements: [item], license_required: false }));
    expect(await listAnnouncements(fetcher)).toEqual({ announcements: [item], license_required: false });
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/announcements", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });
  it("publishes normalized plain text once and lets the backend supply an empty default title", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json(item, 201));
    expect(await publishAnnouncement({ type: "general", title: "  ", body: "  第一行\r\n第二行  ", expires_minutes: 60 }, fetcher)).toEqual(item);
    expect(fetcher).toHaveBeenCalledOnce();
    const [, init] = fetcher.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ type: "general", title: "", body: "第一行\n第二行", expires_minutes: 60 });
  });
  it("rejects invalid drafts before sending and does not echo private input", async () => {
    const fetcher = vi.fn<typeof fetch>();
    let error: unknown;
    try { publishAnnouncement({ type: "general", title: "", body: "PRIVATE\u0000", expires_minutes: 0 }, fetcher); }
    catch (value) { error = value; }
    expect(error).toBeInstanceOf(AnnouncementRequestError);
    if (!(error instanceof AnnouncementRequestError)) throw new Error("Expected a fixed announcement failure");
    expect(error).toMatchObject({ status: 422, code: "announcement_invalid_request" });
    expect(error.message).not.toContain("PRIVATE"); expect(fetcher).not.toHaveBeenCalled();
  });
  it("counts Unicode code points consistently with the backend", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ ...item, title: "😀".repeat(100) }, 201));
    await expect(publishAnnouncement({ type: "general", title: "😀".repeat(100), body: "正文", expires_minutes: 0 }, fetcher)).resolves.toMatchObject({ title: "😀".repeat(100) });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(() => publishAnnouncement({ type: "general", title: "😀".repeat(101), body: "正文", expires_minutes: 0 }, vi.fn<typeof fetch>())).toThrow("公告内容不正确");
  });
  it.each([
    null, [], { announcements: [item], license_required: true },
    { announcements: [{ ...item, extra: "PRIVATE" }], license_required: false },
    { announcements: [{ ...item, title: " padded " }], license_required: false },
    { announcements: [{ ...item, body: "bad\u0000" }], license_required: false },
    { announcements: [item, item], license_required: false },
    { announcements: [item], license_required: false, detail: "PRIVATE" },
  ])("fails closed for malformed responses: %j", async value => {
    await expect(listAnnouncements(vi.fn<typeof fetch>().mockResolvedValue(json(value)))).rejects.toMatchObject({ status: null });
  });
  it("deletes one UUID with CSRF and validates the exact receipt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ id: item.id, deleted: true, license_required: false }));
    vi.stubGlobal("fetch", fetcher);
    expect(await deleteAnnouncement(item.id)).toBe(item.id);
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe(`/api/v1/announcements/${item.id}`); expect(init?.method).toBe("DELETE");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("PRIVATE-CSRF");
  });
  it("uses the subscriber account boundary and validates active announcements", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ announcements: [item], license_required: false }));
    expect(await accountAnnouncements(fetcher)).toEqual({ announcements: [item], license_required: false });
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/api/v1/account/announcements", expect.objectContaining({ credentials: "include", cache: "no-store" }));
  });
  it("maps only fixed server errors and treats missing write receipts as unknown", async () => {
    const known = await failure(publishAnnouncement({ type: "general", title: "", body: "正文", expires_minutes: 0 },
      vi.fn<typeof fetch>().mockResolvedValue(json({ code: "announcement_rate_limited", detail: "PRIVATE" }, 429))));
    expect(known.message).toBe("公告操作过于频繁，请稍后重试。"); expect(known.outcomeUnknown).toBe(false);
    const unknown = await failure(deleteAnnouncement(item.id, vi.fn<typeof fetch>().mockRejectedValue(new Error("PRIVATE"))));
    expect(unknown.message).not.toContain("PRIVATE"); expect(unknown.outcomeUnknown).toBe(true);
  });
  it("bounds administrator response bytes without rendering them", async () => {
    const large = JSON.stringify({ announcements: [], license_required: false, padding: "PRIVATE".repeat(150_000) });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(large, { headers: { "Content-Type": "application/json" } }));
    const error = await failure(listAnnouncements(fetcher));
    expect(error.message).not.toContain("PRIVATE"); expect(fetcher).toHaveBeenCalledOnce();
  });
});
