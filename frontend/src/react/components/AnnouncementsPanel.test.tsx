// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "../../services/auth";
import { AnnouncementRequestError, deleteAnnouncement, listAnnouncements, publishAnnouncement } from "../../services/announcements";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AnnouncementsPanel from "./AnnouncementsPanel";

vi.mock("../../services/announcements", async original => ({ ...await original<typeof import("../../services/announcements")>(),
  listAnnouncements: vi.fn(), publishAnnouncement: vi.fn(), deleteAnnouncement: vi.fn(),
}));
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" };
const existing = {
  id: "11111111-1111-4111-8111-111111111111", type: "maintenance" as const, title: "维护通知",
  body: "今晚维护", created_at: "2026-09-01T00:00:00Z", expires_at: null,
};
beforeEach(() => {
  vi.resetAllMocks(); installDom(); authState.session = { ...operator };
  vi.mocked(listAnnouncements).mockResolvedValue({ announcements: [existing], license_required: false });
  vi.mocked(publishAnnouncement).mockResolvedValue({ ...existing, id: "22222222-2222-4222-8222-222222222222", type: "general", title: "公告", body: "新的正文" });
  vi.mocked(deleteAnnouncement).mockResolvedValue(existing.id);
});
afterEach(async () => { cleanup(); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount() { const view = renderUi(<AnnouncementsPanel operator={operator} />); await flush(); return view; }

describe("Web announcement administration", () => {
  it("reads active announcements without writing and explains subscriber filtering", async () => {
    await mount();
    expect(screen.getByText("维护通知")).toBeTruthy(); expect(screen.getByText("今晚维护")).toBeTruthy();
    expect(screen.getAllByText(/有生效套餐的用户显示/).length).toBe(2);
    expect(publishAnnouncement).not.toHaveBeenCalled(); expect(deleteAnnouncement).not.toHaveBeenCalled();
  });
  it("requires explicit confirmation and publishes normalized text once", async () => {
    const pending = deferred<Awaited<ReturnType<typeof publishAnnouncement>>>(); vi.mocked(publishAnnouncement).mockReturnValue(pending.promise);
    await mount(); fireEvent.change(screen.getByLabelText("标题（可选）"), { target: { value: "  " } });
    fireEvent.change(screen.getByLabelText("正文"), { target: { value: "  新的正文  " } });
    expect((screen.getByRole("button", { name: "发布公告" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("确认正文将向有生效套餐的用户显示"));
    const button = screen.getByRole("button", { name: "发布公告" }); fireEvent.click(button); fireEvent.click(button); await flush();
    expect(publishAnnouncement).toHaveBeenCalledExactlyOnceWith({ type: "general", title: "", body: "新的正文", expires_minutes: 0 });
    await act(async () => pending.resolve({ ...existing, id: "22222222-2222-4222-8222-222222222222", type: "general", title: "公告", body: "新的正文" }));
    expect(screen.getByText("公告已发布。")).toBeTruthy(); expect((screen.getByLabelText("正文") as HTMLTextAreaElement).value).toBe("");
  });
  it("deletes one confirmed announcement", async () => {
    await mount(); fireEvent.click(screen.getByRole("button", { name: "删除公告：维护通知" })); await flush();
    const confirm = screen.getAllByRole("button", { name: /删\s*除/ }).find(node => node.textContent?.replace(/\s/g, "") === "删除");
    expect(confirm).toBeTruthy(); fireEvent.click(confirm!); await flush();
    expect(deleteAnnouncement).toHaveBeenCalledExactlyOnceWith(existing.id);
    expect(screen.queryByText("今晚维护")).toBeNull(); expect(screen.getByText("公告已删除。")).toBeTruthy();
  });
  it("reconciles an unknown publish outcome without replaying the write", async () => {
    vi.mocked(publishAnnouncement).mockRejectedValue(new AnnouncementRequestError(null));
    vi.mocked(listAnnouncements).mockResolvedValueOnce({ announcements: [existing], license_required: false })
      .mockResolvedValueOnce({ announcements: [{ ...existing, body: "已实际发布" }], license_required: false });
    await mount(); fireEvent.change(screen.getByLabelText("正文"), { target: { value: "新的正文" } });
    fireEvent.click(screen.getByText("确认正文将向有生效套餐的用户显示")); fireEvent.click(screen.getByRole("button", { name: "发布公告" })); await flush();
    expect(publishAnnouncement).toHaveBeenCalledOnce(); expect(listAnnouncements).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/已重新读取当前公告，请核对/)).toBeTruthy(); expect(screen.queryByText("公告已发布。")).toBeNull();
  });
});
