// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState, loadSession } from "../services/auth";
import { defaultBranding } from "../domain/branding";
import { getPublicBranding } from "../services/branding";
import { getInitialSetupStatus } from "../services/initial-setup";
import { getPublicAppearance } from "../services/appearance";
import { deferred, flush, installDom } from "./test-utils";
import App from "./App";

const { routeState } = vi.hoisted(() => ({ routeState: { broken: false } }));
vi.mock("../routes", () => ({ routes: [
  { path: "/servers", component: () => { if (routeState.broken) throw new Error("PRIVATE-TEST-FAILURE"); return <div>Servers workspace</div>; } },
  { path: "/nodes", component: () => <div>Nodes workspace</div> },
  { path: "/templates", component: () => <div>Templates workspace</div> },
  { path: "/plans", component: () => <div>Plans workspace</div> },
  { path: "/users", component: () => <div>Users workspace</div> },
  { path: "/certificates", component: () => <div>Certificates workspace</div> },
  { path: "/system-settings", component: () => <div>System settings workspace</div> },
  { path: "/account", component: () => <div>Separate subscriber portal</div>, meta: { subscriber: true } },
  { path: "/account/external-subscriptions", component: () => <div>Subscriber external sources</div>, meta: { subscriber: true } },
  { path: "/account/renewals", component: () => <div>Subscriber renewals</div>, meta: { subscriber: true } },
], legacyRouteRedirects: [
  { path: "/", to: "/servers" },
  { path: "/notifications", to: "/system-settings?tab=notifications" },
] }));
vi.mock("../services/auth", async importOriginal => ({ ...await importOriginal<typeof import("../services/auth")>(), loadSession: vi.fn(), signOut: vi.fn() }));
vi.mock("../services/branding", async original => ({ ...await original<typeof import("../services/branding")>(), getPublicBranding: vi.fn() }));
vi.mock("../services/initial-setup", async original => ({ ...await original<typeof import("../services/initial-setup")>(), getInitialSetupStatus: vi.fn() }));
vi.mock("../services/appearance", async original => ({ ...await original<typeof import("../services/appearance")>(), getPublicAppearance: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks(); installDom(); routeState.broken = false;
  authState.ready = true; authState.error = "";
  authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
  vi.mocked(getPublicBranding).mockResolvedValue({ ...defaultBranding });
  vi.mocked(getInitialSetupStatus).mockResolvedValue({ configured: false, available: true });
  vi.mocked(getPublicAppearance).mockResolvedValue({ default_theme: "light", logo_url: "", wallpaper_url: "", license_required: false });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network request in application test"); }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount(path = "/") { return render(<StrictMode><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></StrictMode>); }
describe("React application shell", () => {
  it("applies the public Logo, login background and site default theme", async () => {
    vi.mocked(getPublicAppearance).mockResolvedValue({
      default_theme: "dark", logo_url: "https://cdn.example.test/logo.png",
      wallpaper_url: "https://cdn.example.test/background.webp", license_required: false,
    });
    mount(); await flush();
    const logo = screen.getByAltText("站点 Logo") as HTMLImageElement;
    expect(logo.src).toBe("https://cdn.example.test/logo.png");
    const wallpaper = document.querySelector(".auth-wallpaper") as HTMLImageElement;
    expect(wallpaper.src).toBe("https://cdn.example.test/background.webp");
    expect(logo.crossOrigin).toBe("anonymous");
    expect(wallpaper.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(document.documentElement.dataset.openNodeTheme).toBe("dark");
    expect(screen.getByLabelText("页面主题")).toBeTruthy();
  });
  it("routes unconfigured administrators to Chinese first-run setup", async () => {
    authState.session = { configured: false, authenticated: false, username: null, csrf_token: null };
    mount("/system-settings"); await flush();
    expect(screen.getByRole("heading", { name: "首次初始化" })).toBeTruthy();
    expect(document.title).toBe("首次初始化 - Open Node");
    expect(screen.getByLabelText("管理员用户名")).toBeTruthy();
    expect(screen.queryByLabelText("初始化凭证")).toBeNull();
    expect(screen.queryByText("System settings workspace")).toBeNull();
    expect(screen.queryByLabelText("密码")).toBeNull();
  });
  it("never mounts a management workspace before administrator authentication", async () => {
    mount(); await flush();
    expect(screen.getByRole("heading", { name: "管理员登录" })).toBeTruthy();
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.title).toBe("管理员登录 - Open Node");
    expect(screen.getByRole("link", { name: "用户登录" })).toBeTruthy();
    expect(screen.queryByText("Servers workspace")).toBeNull();
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
    expect(document.title).toBe("服务器管理 - <script>not-executed</script>"); expect(document.querySelector("script")).toBeNull();
  });
  it("uses the public title for the subscriber route without checking the administrator session", async () => {
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "用户站点", brand_title: "用户品牌", license_required: false });
    authState.ready = false; authState.session = null; mount("/account"); await flush();
    expect(document.title).toBe("用户中心 - 用户站点"); expect(loadSession).not.toHaveBeenCalled(); expect(getPublicBranding).toHaveBeenCalledOnce();
  });
  it("keeps a removed notification deep link private and redirects it into system settings", async () => {
    const first = mount("/notifications"); await flush();
    expect(screen.queryByText("System settings workspace")).toBeNull();
    expect(screen.getByRole("heading", { name: "管理员登录" })).toBeTruthy();
    first.unmount();
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount("/notifications"); await flush();
    expect(screen.getByText("System settings workspace")).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "通知设置" })).toBeNull();
    expect(document.title).toBe("系统设置 - Open Node");
  });
  it.each([
    ["/account", "Separate subscriber portal"],
    ["/account/external-subscriptions", "Subscriber external sources"],
    ["/account/renewals", "Subscriber renewals"],
  ])("does not request an administrator session on %s", async (path, content) => {
    authState.ready = false; authState.session = null; mount(path); await flush();
    expect(screen.getByText(content)).toBeTruthy();
    expect(loadSession).not.toHaveBeenCalled();
    expect(screen.queryByText("管理员登录")).toBeNull();
  });
  it("keeps standard mobile navigation wired to the authenticated routes", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "test-csrf" };
    mount(); await flush(); expect(screen.getByText("Servers workspace")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "切换导航菜单" })); await flush();
    expect(screen.getAllByRole("menuitem").map(item => item.textContent)).toEqual(["服务器管理", "节点管理", "模板管理", "套餐管理", "用户管理", "证书管理", "系统设置"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "节点管理" })); await flush();
    expect(screen.getByText("Nodes workspace")).toBeTruthy();
    expect(document.title).toBe("节点管理 - Open Node");
    expect(screen.queryByText("Servers workspace")).toBeNull();
    expect(screen.getByRole("menuitem", { name: "证书管理" })).toBeTruthy();
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
    fireEvent.click(screen.getByRole("menuitem", { name: "节点管理" })); await flush();
    expect(screen.getByText("Nodes workspace")).toBeTruthy();
    expect(screen.queryByText("无法加载此工作区")).toBeNull();
  });
});
