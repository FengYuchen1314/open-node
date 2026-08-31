// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState, loadSession } from "../services/auth";
import { defaultBranding } from "../domain/branding";
import { getPublicBranding } from "../services/branding";
import { deferred, flush, installDom } from "./test-utils";
import App from "./App";

const { routeState } = vi.hoisted(() => ({ routeState: { broken: false } }));
vi.mock("../routes", () => ({ routes: [
  { path: "/", component: () => { if (routeState.broken) throw new Error("PRIVATE-TEST-FAILURE"); return <div>Inventory workspace</div>; } },
  { path: "/subscriptions", component: () => <div>Subscriptions workspace</div> },
  { path: "/notifications", component: () => <div>Notifications workspace</div> },
  { path: "/system-settings", component: () => <div>System settings workspace</div> },
  { path: "/account", component: () => <div>Separate subscriber portal</div> },
] }));
vi.mock("../services/auth", async importOriginal => ({ ...await importOriginal<typeof import("../services/auth")>(), loadSession: vi.fn(), signOut: vi.fn() }));
vi.mock("../services/branding", async original => ({ ...await original<typeof import("../services/branding")>(), getPublicBranding: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks(); installDom(); routeState.broken = false;
  authState.ready = true; authState.error = "";
  authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
  vi.mocked(getPublicBranding).mockResolvedValue({ ...defaultBranding });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network request in application test"); }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount(path = "/") { return render(<StrictMode><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></StrictMode>); }
describe("React application shell", () => {
  it("never mounts a management workspace before administrator authentication", async () => {
    mount(); await flush();
    expect(screen.getByRole("heading", { name: "管理员登录" })).toBeTruthy();
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.title).toBe("管理员登录 - Open Node");
    expect(screen.getByRole("link", { name: "用户登录" })).toBeTruthy();
    expect(screen.queryByText("Inventory workspace")).toBeNull();
    expect(screen.queryByRole("button", { name: "退出登录" })).toBeNull();
  });
  it("deduplicates the pending session check during StrictMode replay", async () => {
    const pending = deferred<void>(); vi.mocked(loadSession).mockReturnValue(pending.promise);
    authState.ready = false; authState.session = null; mount(); await flush();
    expect(loadSession).toHaveBeenCalledOnce();
    expect(screen.getByRole("status", { name: "正在加载会话" })).toBeTruthy();
    await act(async () => { authState.ready = true; pending.resolve(); });
    expect(screen.getByLabelText("用户名")).toBeTruthy();
  });
  it("keeps system settings private and wires the Chinese navigation and browser title", async () => {
    const first = mount("/system-settings"); await flush();
    expect(screen.queryByText("System settings workspace")).toBeNull(); first.unmount();
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush(); fireEvent.click(screen.getByRole("button", { name: "切换导航菜单" })); await flush();
    fireEvent.click(screen.getByRole("menuitem", { name: "系统设置" })); await flush();
    expect(screen.getByText("System settings workspace")).toBeTruthy(); expect(document.title).toBe("系统设置 - Open Node");
  });
  it("loads public branding once without blocking the administrator login form", async () => {
    const pending = deferred<typeof defaultBranding>(); vi.mocked(getPublicBranding).mockReturnValue(pending.promise);
    mount(); await flush(); expect(screen.getByLabelText("密码")).toBeTruthy(); expect(getPublicBranding).toHaveBeenCalledOnce();
    await act(async () => pending.resolve({ site_title: "自定义站点", brand_title: "自定义品牌", license_required: false }));
    expect(screen.getByRole("heading", { name: "自定义品牌" })).toBeTruthy(); expect(document.title).toBe("管理员登录 - 自定义站点");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("keeps both login paths usable when public branding fails", async () => {
    vi.mocked(getPublicBranding).mockRejectedValue(new Error("PRIVATE branding body"));
    mount(); await flush(); expect(screen.getByLabelText("密码")).toBeTruthy();
    expect(screen.getByRole("link", { name: "用户登录" })).toBeTruthy(); expect(document.title).toBe("管理员登录 - Open Node");
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it("renders a long literal brand without changing navigation or logout controls", async () => {
    const brand = "中文站点🧭".repeat(8);
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "<script>not-executed</script>", brand_title: brand, license_required: false });
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush();
    expect(screen.getByRole("heading", { name: brand }).classList.contains("branding-header-text")).toBe(true);
    expect(screen.getByRole("button", { name: "切换导航菜单" })).toBeTruthy(); expect(screen.getByRole("button", { name: "退出登录" })).toBeTruthy();
    expect(document.title).toBe("概览 - <script>not-executed</script>"); expect(document.querySelector("script")).toBeNull();
  });
  it("uses the public title for the subscriber route without checking the administrator session", async () => {
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "用户站点", brand_title: "用户品牌", license_required: false });
    authState.ready = false; authState.session = null; mount("/account"); await flush();
    expect(document.title).toBe("用户中心 - 用户站点"); expect(loadSession).not.toHaveBeenCalled(); expect(getPublicBranding).toHaveBeenCalledOnce();
  });
  it("keeps notification settings private and uses Chinese navigation and title", async () => {
    const first = mount("/notifications"); await flush();
    expect(screen.queryByText("Notifications workspace")).toBeNull();
    expect(screen.getByRole("heading", { name: "管理员登录" })).toBeTruthy();
    first.unmount();
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush();
    fireEvent.click(screen.getByRole("button", { name: "切换导航菜单" })); await flush();
    fireEvent.click(screen.getByRole("menuitem", { name: "通知设置" })); await flush();
    expect(screen.getByText("Notifications workspace")).toBeTruthy();
    expect(document.title).toBe("通知设置 - Open Node");
  });
  it("does not request an administrator session on the separate account route", async () => {
    authState.ready = false; authState.session = null; mount("/account"); await flush();
    expect(screen.getByText("Separate subscriber portal")).toBeTruthy();
    expect(loadSession).not.toHaveBeenCalled();
    expect(screen.queryByText("管理员登录")).toBeNull();
  });
  it("keeps standard mobile navigation wired to the authenticated routes", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush(); expect(screen.getByText("Inventory workspace")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "切换导航菜单" })); await flush();
    fireEvent.click(screen.getByRole("menuitem", { name: "订阅管理" })); await flush();
    expect(screen.getByText("Subscriptions workspace")).toBeTruthy();
    expect(document.title).toBe("订阅管理 - Open Node");
    expect(screen.queryByText("Inventory workspace")).toBeNull();
  });
  it("contains a broken workspace without exposing raw errors or disabling navigation", async () => {
    routeState.broken = true;
    vi.spyOn(console, "error").mockImplementation(() => {});
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush();
    expect(screen.getByText("无法加载此工作区")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新加载应用" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("PRIVATE-TEST-FAILURE");
    fireEvent.click(screen.getByRole("button", { name: "切换导航菜单" })); await flush();
    fireEvent.click(screen.getByRole("menuitem", { name: "订阅管理" })); await flush();
    expect(screen.getByText("Subscriptions workspace")).toBeTruthy();
    expect(screen.queryByText("无法加载此工作区")).toBeNull();
  });
});
