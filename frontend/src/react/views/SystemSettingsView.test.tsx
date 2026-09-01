// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultBranding, type BrandingSettings, type PublicBranding } from "../../domain/branding";
import { authState } from "../../services/auth";
import { BrandingRequestError, getBrandingSettings, getPublicBranding, updateBrandingSettings } from "../../services/branding";
import { BrandingProvider, useBranding } from "../hooks/useBranding";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import SystemSettingsView from "./SystemSettingsView";

vi.mock("../../services/branding", async original => ({ ...await original<typeof import("../../services/branding")>(),
  getBrandingSettings: vi.fn(), getPublicBranding: vi.fn(), updateBrandingSettings: vi.fn(),
}));
vi.mock("../components/AppearancePanel", () => ({ default: () => <div>外观设置工作区</div> }));
vi.mock("../components/AnnouncementsPanel", () => ({ default: () => <div>公告设置工作区</div> }));
vi.mock("../components/ApplicationUpdatePanel", () => ({ default: () => <div>应用更新工作区</div> }));
vi.mock("../components/SubscriberPermissionsPanel", () => ({ default: () => <div>用户权限设置工作区</div> }));
const initial: BrandingSettings = { site_title: "已保存的标题", brand_title: "已保存的品牌", revision: 4, license_required: false };
const administrator = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" };
let stored: BrandingSettings;
function Shell() {
  const { branding, acceptSaved } = useBranding();
  const [open, setOpen] = useState(true);
  useEffect(() => { document.title = `系统设置 - ${branding.site_title}`; }, [branding.site_title]);
  return <><output aria-label="当前全局品牌">{branding.brand_title}</output>
    <button onClick={() => setOpen(false)}>关闭设置视图</button>
    <button onClick={() => acceptSaved({ ...initial, site_title: "其他操作的新标题", brand_title: "其他操作的新品牌", revision: 9 })}>模拟其他成功回执</button>
    {open && <SystemSettingsView />}</>;
}
function input(label: string) { return screen.getByLabelText(label) as HTMLInputElement; }
function fill(label: string, value: string) { fireEvent.change(input(label), { target: { value } }); }
function button(label: string) { return screen.getByRole("button", { name: label }) as HTMLButtonElement; }
async function click(label: string) { fireEvent.click(button(label)); await flush(); }
async function mount() { const view = renderUi(<BrandingProvider><Shell /></BrandingProvider>); await flush(); return view; }
function changeBoth() { fill("浏览器标题", "新的标题 🧭"); fill("页面品牌文字", "新的品牌"); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); installDom(); stored = { ...initial };
  authState.ready = true; authState.error = ""; authState.session = { ...administrator };
  vi.mocked(getPublicBranding).mockResolvedValue({ ...defaultBranding });
  vi.mocked(getBrandingSettings).mockImplementation(async () => ({ ...stored }));
  vi.mocked(updateBrandingSettings).mockImplementation(async payload => {
    stored = { site_title: payload.site_title.trim(), brand_title: payload.brand_title.trim(), revision: payload.expected_revision + 1, license_required: false };
    return { ...stored };
  });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network request"); }));
  localStorage.clear(); sessionStorage.clear();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("administrator system settings", () => {
  it("requires an administrator even when mounted outside the application route", async () => {
    authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
    renderUi(<SystemSettingsView />); await flush();
    expect(screen.getByText("请登录管理员账户后管理系统设置。")).toBeTruthy();
    expect(getBrandingSettings).not.toHaveBeenCalled(); expect(getPublicBranding).not.toHaveBeenCalled();
    expect(updateBrandingSettings).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
  it("reads saved settings and explains their public scope without writing", async () => {
    await mount();
    expect(input("浏览器标题").value).toBe(initial.site_title); expect(input("页面品牌文字").value).toBe(initial.brand_title);
    expect(screen.getByRole("heading", { name: "系统设置" })).toBeTruthy();
    expect(screen.getByText("这两项文字会公开显示在登录页和其他页面，请勿填写密码、Token 或其他秘密。")).toBeTruthy();
    expect(screen.getByText(/不改变 Open Node 的技术标识、TOTP 或公共探针标题/)).toBeTruthy();
    expect(button("保存站点文字").disabled).toBe(true);
    expect(updateBrandingSettings).not.toHaveBeenCalled(); expect(document.title).toBe(`系统设置 - ${initial.site_title}`);
  });
  it("keeps draft edits and restoring defaults separate from global display and persistence", async () => {
    await mount(); changeBoth();
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(initial.brand_title);
    expect(document.title).toBe(`系统设置 - ${initial.site_title}`);
    await click("恢复默认草稿");
    expect(input("浏览器标题").value).toBe("Open Node"); expect(input("页面品牌文字").value).toBe("Open Node");
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(initial.brand_title);
    expect(updateBrandingSettings).not.toHaveBeenCalled(); expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
  it("saves both normalized titles with one CAS and synchronizes the confirmed result", async () => {
    await mount(); fill("浏览器标题", "  新的标题 🧭  "); fill("页面品牌文字", "  新的品牌  ");
    await click("保存站点文字");
    expect(updateBrandingSettings).toHaveBeenCalledExactlyOnceWith({ expected_revision: 4, site_title: "新的标题 🧭", brand_title: "新的品牌" });
    expect(screen.getByText("站点文字已保存。")).toBeTruthy();
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe("新的品牌");
    expect(document.title).toBe("系统设置 - 新的标题 🧭"); expect(button("保存站点文字").disabled).toBe(true);
  });
  it("refuses duplicate submit events while the save receipt is pending", async () => {
    const pending = deferred<BrandingSettings>(); vi.mocked(updateBrandingSettings).mockReturnValue(pending.promise);
    await mount(); changeBoth(); const form = input("浏览器标题").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(updateBrandingSettings).toHaveBeenCalledOnce(); expect(input("浏览器标题").disabled).toBe(true);
    expect(button("重新读取站点文字").disabled).toBe(true); expect(button("恢复默认草稿").disabled).toBe(true);
    await act(async () => pending.resolve({ ...initial, site_title: "新的标题 🧭", brand_title: "新的品牌", revision: 5 }));
    expect(screen.getByText("站点文字已保存。")).toBeTruthy();
  });
  it.each([
    ["浏览器标题", "😀".repeat(81)], ["页面品牌文字", "😀".repeat(41)],
    ["浏览器标题", "\u202ePRIVATE"], ["页面品牌文字", "\u200c\u200d"], ["页面品牌文字", "  "],
  ])("blocks invalid draft text for %s", async (label, value) => {
    await mount(); fill(label, value); fireEvent.submit(input("浏览器标题").closest("form")!); await flush();
    expect(button("保存站点文字").disabled).toBe(true); expect(updateBrandingSettings).not.toHaveBeenCalled();
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(initial.brand_title);
  });
  it("accepts 80 astral code points and a visible joiner sequence without truncating UTF-16", async () => {
    await mount(); fill("浏览器标题", "😀".repeat(80)); fill("页面品牌文字", "👩‍💻 示例"); await click("保存站点文字");
    expect(updateBrandingSettings).toHaveBeenCalledExactlyOnceWith({ expected_revision: 4, site_title: "😀".repeat(80), brand_title: "👩‍💻 示例" });
  });
  it("renders markup-shaped names as plain text", async () => {
    stored = { ...initial, site_title: "<script>evil()</script>", brand_title: "<img src=x onerror=evil()>" };
    await mount();
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(stored.brand_title);
    expect(document.querySelector("img,script")).toBeNull();
    expect(document.title).toBe(`系统设置 - ${stored.site_title}`);
  });
  it("cannot be reverted by a public response that arrives after a successful save", async () => {
    const old = deferred<PublicBranding>(); vi.mocked(getPublicBranding).mockReturnValue(old.promise);
    await mount(); changeBoth(); await click("保存站点文字");
    await act(async () => old.resolve({ ...defaultBranding }));
    expect(document.title).toBe("系统设置 - 新的标题 🧭"); expect(screen.getByLabelText("当前全局品牌").textContent).toBe("新的品牌");
  });
  it("rejects an admin read captured before another confirmed save", async () => {
    const old = deferred<BrandingSettings>(); vi.mocked(getBrandingSettings).mockReturnValueOnce(old.promise);
    await mount(); await click("模拟其他成功回执");
    await act(async () => old.resolve(initial));
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe("其他操作的新品牌");
    expect(document.title).toBe("系统设置 - 其他操作的新标题"); expect(button("保存站点文字").disabled).toBe(true);
    expect(screen.getByText(/站点文字已被其他操作修改/)).toBeTruthy();
  });
  it("reconciles a missing save receipt through GET without automatically repeating PUT or claiming success", async () => {
    vi.mocked(updateBrandingSettings).mockImplementation(async payload => {
      stored = { ...initial, site_title: payload.site_title, brand_title: payload.brand_title, revision: 5 };
      throw new Error("PRIVATE disconnected after commit");
    });
    await mount(); changeBoth(); await click("保存站点文字");
    expect(updateBrandingSettings).toHaveBeenCalledOnce(); expect(getBrandingSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/未收到有效的保存回执，保存结果尚未确认。已重新读取当前配置，请核对；没有自动重新提交/)).toBeTruthy();
    expect(screen.queryByText("站点文字已保存。")).toBeNull(); expect(document.body.textContent).not.toContain("PRIVATE");
    expect(document.title).toBe("系统设置 - 新的标题 🧭");
    await click("重新读取站点文字"); expect(updateBrandingSettings).toHaveBeenCalledOnce();
  });
  it("requires a successful read after an unknown save whose reconciliation also failed", async () => {
    await mount(); vi.mocked(updateBrandingSettings).mockRejectedValue(new BrandingRequestError(null));
    vi.mocked(getBrandingSettings).mockRejectedValueOnce(new Error("PRIVATE read error"));
    changeBoth(); await click("保存站点文字");
    expect(screen.getByText(/重新读取仍未成功，请手动重新读取/)).toBeTruthy(); expect(button("保存站点文字").disabled).toBe(true);
    fill("页面品牌文字", "另一个草稿"); fireEvent.submit(input("浏览器标题").closest("form")!); await flush();
    expect(updateBrandingSettings).toHaveBeenCalledOnce();
    await click("重新读取站点文字"); expect(input("页面品牌文字").value).toBe(initial.brand_title);
    fill("页面品牌文字", "重新核对后编辑"); expect(button("保存站点文字").disabled).toBe(false);
  });
  it("reloads the current CAS version on conflict without retrying the rejected draft", async () => {
    vi.mocked(updateBrandingSettings).mockImplementation(async () => {
      stored = { ...initial, brand_title: "另一管理员的保存", revision: 7 };
      throw new BrandingRequestError(409, "branding_revision_conflict");
    });
    await mount(); changeBoth(); await click("保存站点文字");
    expect(updateBrandingSettings).toHaveBeenCalledOnce(); expect(input("页面品牌文字").value).toBe("另一管理员的保存");
    expect(screen.getByText(/站点文字已被其他操作修改/)).toBeTruthy(); expect(screen.queryByText("站点文字已保存。")).toBeNull();
  });
  it("uses only the fixed permission error, never a modified Error.message", async () => {
    const error = new BrandingRequestError(403); error.message = "PRIVATE rejected body";
    vi.mocked(updateBrandingSettings).mockRejectedValue(error);
    await mount(); changeBoth(); await click("保存站点文字");
    expect(screen.getByText("此操作需要管理员权限和有效的请求验证。")).toBeTruthy();
    expect(document.body.textContent).not.toContain("PRIVATE"); expect(getBrandingSettings).toHaveBeenCalledOnce();
  });
  it("keeps an unavailable settings read from enabling a default-value overwrite", async () => {
    vi.mocked(getBrandingSettings).mockRejectedValue(new BrandingRequestError(503, "branding_storage_unavailable"));
    await mount();
    expect(screen.getByText("站点文字存储暂不可用，请稍后重新读取。")).toBeTruthy();
    expect(button("保存站点文字").disabled).toBe(true); expect(button("恢复默认草稿").disabled).toBe(true);
    expect(input("浏览器标题").disabled).toBe(true); expect(button("重新读取站点文字").disabled).toBe(false);
    expect(updateBrandingSettings).not.toHaveBeenCalled();
  });
  it("discards a save that resolves after its view has closed", async () => {
    const pending = deferred<BrandingSettings>(); vi.mocked(updateBrandingSettings).mockReturnValue(pending.promise);
    await mount(); changeBoth(); await click("保存站点文字"); await click("关闭设置视图");
    await act(async () => pending.resolve({ ...initial, brand_title: "过期回执", revision: 5 }));
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(initial.brand_title);
    expect(screen.queryByText("站点文字已保存。")).toBeNull(); expect(getBrandingSettings).toHaveBeenCalledOnce();
  });
  it.each(["other-admin", "admin"])("fences late work when identity or its CSRF session changes: %s", async username => {
    const pending = deferred<BrandingSettings>(); vi.mocked(updateBrandingSettings).mockReturnValue(pending.promise);
    await mount(); changeBoth(); await click("保存站点文字");
    stored = { ...initial, brand_title: "新会话读取的品牌", revision: 6 };
    await act(async () => { authState.session = { ...administrator, username, csrf_token: "NEW-PRIVATE-CSRF" }; }); await flush();
    await act(async () => pending.resolve({ ...initial, brand_title: "旧会话回执", revision: 5 }));
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe(stored.brand_title);
    expect(input("页面品牌文字").value).toBe(stored.brand_title); expect(screen.queryByText("站点文字已保存。")).toBeNull();
    expect(updateBrandingSettings).toHaveBeenCalledOnce(); expect(getBrandingSettings).toHaveBeenCalledTimes(2);
  });
  it("never applies the old administrator's pending read after logout", async () => {
    const pending = deferred<BrandingSettings>(); vi.mocked(getBrandingSettings).mockReturnValue(pending.promise);
    await mount();
    await act(async () => { authState.session = { configured: true, authenticated: false, username: null, csrf_token: null }; });
    await act(async () => pending.resolve(initial));
    expect(screen.getByLabelText("当前全局品牌").textContent).toBe("Open Node");
    expect(screen.queryByLabelText("浏览器标题")).toBeNull(); expect(updateBrandingSettings).not.toHaveBeenCalled();
  });
});
