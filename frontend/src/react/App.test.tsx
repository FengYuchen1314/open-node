// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState, loadSession } from "../services/auth";
import { deferred, flush, installDom } from "./test-utils";
import App from "./App";

const { routeState } = vi.hoisted(() => ({ routeState: { broken: false } }));
vi.mock("../routes", () => ({ routes: [
  { path: "/", component: () => { if (routeState.broken) throw new Error("PRIVATE-TEST-FAILURE"); return <div>Inventory workspace</div>; } },
  { path: "/subscriptions", component: () => <div>Subscriptions workspace</div> },
  { path: "/account", component: () => <div>Separate subscriber portal</div> },
] }));
vi.mock("../services/auth", async importOriginal => ({ ...await importOriginal<typeof import("../services/auth")>(), loadSession: vi.fn(), signOut: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks(); installDom(); routeState.broken = false;
  authState.ready = true; authState.error = "";
  authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
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
