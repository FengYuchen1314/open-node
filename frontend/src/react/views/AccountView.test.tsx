// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProductUserSubscriptionToken } from "../../domain/subscriptions";
import { getPublicBranding } from "../../services/branding";
import { accountAnnouncements } from "../../services/announcements";
import { getAccountSubscriberPermissions } from "../../services/subscriber-permissions";
import { BrandingProvider } from "../hooks/useBranding";
import {
  loadSubscriberSession, subscriberProfile, subscriberProfiles, subscriberRegister,
  subscriberSignIn, subscriberState, subscriberToken, verifySubscriberLogin,
  type SubscriberProfile, type SubscriberSession,
} from "../../services/subscriber-auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AccountView from "./AccountView";

vi.mock("../../services/subscriber-auth", async original => ({
  ...await original<typeof import("../../services/subscriber-auth")>(),
  loadSubscriberSession: vi.fn(), subscriberSignIn: vi.fn(), verifySubscriberLogin: vi.fn(),
  subscriberRegister: vi.fn(), subscriberProfile: vi.fn(), subscriberProfiles: vi.fn(), subscriberToken: vi.fn(),
}));
vi.mock("../../services/branding", async original => ({ ...await original<typeof import("../../services/branding")>(), getPublicBranding: vi.fn() }));
vi.mock("../../services/announcements", async original => ({ ...await original<typeof import("../../services/announcements")>(), accountAnnouncements: vi.fn() }));
vi.mock("../../services/subscriber-permissions", async original => ({ ...await original<typeof import("../../services/subscriber-permissions")>(), getAccountSubscriberPermissions: vi.fn() }));
vi.mock("../components/PrivateRoutedNodesPanel", () => ({ default: () => <div>用户路由工作区</div> }));
vi.mock("../components/SubscriberSecurityPanel", () => ({ default: () => <div>账户安全工作区</div> }));
vi.mock("../components/TemplatesWorkspace", () => ({ default: () => <div>订阅模板工作区</div> }));
vi.mock("../components/SubscriptionShortCodeDialog", () => ({ default: () => null }));
vi.mock("./SubscriptionCustomizationsView", () => ({ default: () => <div>用户订阅自定义工作区</div> }));

const anonymous: SubscriberSession = { authenticated: false, username: null, csrf_token: null, requires_2fa: false, challenge: null };
const session: SubscriberSession = { ...anonymous, authenticated: true, username: "alice", csrf_token: "test-session-csrf" };
const challenge: SubscriberSession = { ...anonymous, requires_2fa: true, challenge: "private-login-challenge" };
const token: ProductUserSubscriptionToken = {
  username: "alice", token: "fixture-token", short_code: "fixture-code", generated_short_code: "fixture-code",
  custom_short_code: null, revision: "token-revision", subscription_url: "https://control.example/sub/fixture-token",
  short_url: "https://control.example/s/fixture-code", short_links_enabled: true, created_at: "", updated_at: "",
};
const profile: SubscriberProfile = {
  username: "alice", display_name: "Alice Custom Name", email: null, speed_limit_mbps: 0, device_limit: 0, node_limits: [],
  quota: {
    username: "alice", is_active: true, has_plan: true, available: true, expired: false, over_quota: false,
    reset_enabled: true, reset_due: false, upload: 1024, download: 2048, weighted_upload: 1024, weighted_download: 2048,
    charged_usage_bytes: 3072, traffic_limit_bytes: 10240, remaining_bytes: 7168, percent_used: 30, reset_day: 1,
    plan_name: "English Plan Name", plan_expires_at: "2027-01-01T00:00:00Z", next_reset_at: "2026-09-01T00:00:00Z",
  },
};
beforeEach(() => {
  vi.resetAllMocks(); installDom(); window.history.replaceState({}, "", "/account");
  subscriberState.ready = true; subscriberState.error = ""; subscriberState.session = { ...anonymous };
  vi.mocked(loadSubscriberSession).mockImplementation(async () => { subscriberState.ready = true; });
  vi.mocked(subscriberSignIn).mockResolvedValue(challenge);
  vi.mocked(verifySubscriberLogin).mockResolvedValue(session);
  vi.mocked(subscriberProfile).mockResolvedValue(profile);
  vi.mocked(subscriberProfiles).mockResolvedValue({ profiles: [], license_required: false });
  vi.mocked(subscriberToken).mockResolvedValue(token);
  vi.mocked(accountAnnouncements).mockResolvedValue({ announcements: [], license_required: false });
  vi.mocked(getAccountSubscriberPermissions).mockResolvedValue({
    pages: ["templates", "external_subscriptions", "private_routes", "renewals"],
    templates: { used: 0, maximum: 0 }, external_sources: { used: 0, maximum: 0 },
    license_required: false,
  });
});
afterEach(async () => {
  cleanup();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  window.history.replaceState({}, "", "/account"); vi.restoreAllMocks(); vi.unstubAllGlobals();
});
function input(label: string) { return screen.getByLabelText(label) as HTMLInputElement; }
function fill(label: string, value: string) { fireEvent.change(input(label), { target: { value } }); }
async function mount() { const view = renderUi(<AccountView />); await flush(); return view; }
async function login() {
  fill("用户名", "alice"); fill("密码", "private-fixture-password");
  fireEvent.click(screen.getByRole("button", { name: "登录" })); await flush();
}

describe("Chinese subscriber portal", () => {
  it("uses the same public brand for subscriber login and the signed-in header", async () => {
    const brand = "站点🧭".repeat(10);
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "用户中心标题", brand_title: brand, license_required: false });
    renderUi(<BrandingProvider><AccountView /></BrandingProvider>); await flush();
    expect(screen.getByRole("heading", { name: brand }).classList.contains("branding-block-text")).toBe(true);
    expect(screen.getByRole("heading", { name: "用户登录" })).toBeTruthy();
    await act(async () => { subscriberState.session = { ...session }; }); await flush();
    expect(screen.getByRole("heading", { name: brand }).classList.contains("branding-header-text")).toBe(true);
    expect(screen.getByRole("button", { name: "退出登录" })).toBeTruthy(); expect(screen.getByRole("heading", { name: "Alice Custom Name" })).toBeTruthy();
  });
  it("shows Chinese login and retry controls while preserving the separate account session", async () => {
    subscriberState.error = "Connection unavailable";
    await mount();
    expect(screen.getByRole("heading", { name: "用户登录" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "管理员登录" }).getAttribute("href")).toBe("/");
    expect(screen.queryByLabelText("密码")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新连接账户" })); await flush();
    expect(loadSubscriberSession).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).not.toContain("Connection unavailable");
  });
  it("submits the original credentials once and keeps the second factor separate", async () => {
    const pending = deferred<SubscriberSession>(); vi.mocked(subscriberSignIn).mockReturnValue(pending.promise);
    await mount(); fill("用户名", "alice"); fill("密码", "private-fixture-password");
    const form = input("密码").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(subscriberSignIn).toHaveBeenCalledExactlyOnceWith("alice", "private-fixture-password");
    await act(async () => pending.resolve(challenge));
    expect(screen.getByRole("heading", { name: "双重验证" })).toBeTruthy();
    expect(screen.queryByLabelText("密码")).toBeNull();
    expect(verifySubscriberLogin).not.toHaveBeenCalled();
    fill("验证器验证码或恢复码", "123456");
    fireEvent.click(screen.getByRole("button", { name: "验证" })); await flush();
    expect(verifySubscriberLogin).toHaveBeenCalledExactlyOnceWith("private-login-challenge", "123456");
    expect(document.body.textContent).not.toContain("private-login-challenge");
  });
  it("translates rejected credentials and clears the password without translating the submitted values", async () => {
    vi.mocked(subscriberSignIn).mockRejectedValue(new Error("Invalid username or password"));
    await mount(); await login();
    expect(screen.getByRole("alert").textContent).toContain("用户名或密码错误");
    expect(input("密码").value).toBe("");
    expect(input("用户名").value).toBe("alice");
    expect(document.body.textContent).not.toContain("Invalid username or password");
  });
  it("keeps the MFA challenge but forgets a rejected one-time code", async () => {
    vi.mocked(verifySubscriberLogin).mockRejectedValue(new Error("Invalid second factor"));
    await mount(); await login(); fill("验证器验证码或恢复码", "wrong-one-time-code");
    fireEvent.click(screen.getByRole("button", { name: "验证" })); await flush();
    expect(screen.getByRole("heading", { name: "双重验证" })).toBeTruthy();
    expect(input("验证器验证码或恢复码").value).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("双重验证失败");
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain("private-login-challenge");
  });
  it("preserves invitation data and distinguishes a created account from a failed automatic login", async () => {
    window.history.replaceState({}, "", "/account#invite=private-invitation");
    vi.mocked(subscriberRegister).mockResolvedValue({} as Awaited<ReturnType<typeof subscriberRegister>>);
    vi.mocked(subscriberSignIn).mockRejectedValue(new Error("Connection unavailable"));
    await mount();
    expect(screen.getByRole("heading", { name: "创建用户账户" })).toBeTruthy();
    fill("用户名", " newuser "); fill("显示名称", " Original Name "); fill("邮箱", " new@example.test ");
    fill("密码", "new-private-password"); fill("确认密码", "different-password");
    expect((screen.getByRole("button", { name: "创建账户" }) as HTMLButtonElement).disabled).toBe(true);
    fill("确认密码", "new-private-password");
    fireEvent.submit(input("密码").closest("form")!); await flush();
    expect(subscriberRegister).toHaveBeenCalledExactlyOnceWith({
      token: "private-invitation", username: "newuser", password: "new-private-password",
      email: "new@example.test", display_name: "Original Name",
    });
    expect(screen.getByRole("alert").textContent).toContain("账户已创建，但未能登录。");
    expect(input("密码").value).toBe(""); expect(window.location.hash).toBe("");
    expect(document.body.textContent).not.toContain("private-invitation");
  });
  it("renders Chinese quota and navigation labels without translating user-supplied names or subscription formats", async () => {
    subscriberState.session = { ...session }; await mount();
    expect(screen.getByRole("heading", { name: "Alice Custom Name" })).toBeTruthy();
    expect(screen.getByText("English Plan Name")).toBeTruthy();
    expect(screen.getByRole("region", { name: "当前套餐" })).toBeTruthy();
    expect(screen.getByText("下次流量重置")).toBeTruthy();
    for (const name of ["订阅", "路由", "订阅自定义", "安全设置"]) expect(screen.getByRole("tab", { name })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "模板" })).toBeNull();
    const url = new URL(input("订阅地址").value);
    expect(url.pathname).toBe("/sub/fixture-token"); expect(url.searchParams.get("format")).toBe("clash");
    fireEvent.click(screen.getByRole("radio", { name: "短链接" })); await flush();
    expect(new URL(input("订阅地址").value).pathname).toBe("/s/fixture-code");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "客户端格式" }));
    fireEvent.click(screen.getByText("URI 列表", { selector: ".ant-select-item-option-content" })); await flush();
    expect(new URL(input("订阅地址").value).searchParams.get("format")).toBe("uri-list");
    fireEvent.click(screen.getByRole("button", { name: "复制订阅链接" })); await flush();
    expect(navigator.clipboard.writeText).toHaveBeenCalledExactlyOnceWith(input("订阅地址").value);
    fireEvent.click(screen.getByRole("tab", { name: "安全设置" })); await flush();
    expect(screen.getByText("账户安全工作区")).toBeTruthy();
  });
  it("shows active Web announcements as plain text without blocking the account", async () => {
    subscriberState.session = { ...session };
    vi.mocked(accountAnnouncements).mockResolvedValue({ announcements: [{
      id: "11111111-1111-4111-8111-111111111111", type: "maintenance", title: "维护 <b>通知</b>",
      body: "第一行\n<script>不会执行</script>", created_at: "2026-09-01T00:00:00Z", expires_at: null,
    }], license_required: false });
    await mount();
    expect(screen.getByText("维护 <b>通知</b>")).toBeTruthy();
    expect(screen.getByText(/<script>不会执行<\/script>/)).toBeTruthy();
    expect(document.querySelector("script, b")).toBeNull();
  });
  it("keeps account data available when the optional announcement read fails", async () => {
    subscriberState.session = { ...session };
    vi.mocked(accountAnnouncements).mockRejectedValue(new Error("PRIVATE upstream body"));
    await mount();
    expect(screen.getByRole("heading", { name: "Alice Custom Name" })).toBeTruthy();
    expect(screen.getByText(/暂时无法读取公告/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it("hides disabled optional features while retaining subscription and security", async () => {
    subscriberState.session = { ...session };
    vi.mocked(getAccountSubscriberPermissions).mockResolvedValue({
      pages: ["renewals"], templates: { used: 3, maximum: 3 },
      external_sources: { used: 1, maximum: 1 }, license_required: false,
    });
    await mount();
    expect(screen.getByRole("tab", { name: "订阅" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "安全设置" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "路由" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "模板" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "订阅自定义" })).toBeNull();
    expect(screen.queryByRole("link", { name: /外部订阅/ })).toBeNull();
    expect(screen.getByRole("link", { name: "申请续费" })).toBeTruthy();
  });
  it("fails closed for optional features when their permission snapshot is unavailable", async () => {
    subscriberState.session = { ...session };
    vi.mocked(getAccountSubscriberPermissions).mockRejectedValue(new Error("PRIVATE policy body"));
    await mount();
    expect(screen.getByText(/暂时无法读取可选功能权限/)).toBeTruthy();
    expect(screen.getByRole("tab", { name: "订阅" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "安全设置" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "模板" })).toBeNull();
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it.each([
    [{ has_plan: false }, "尚未分配订阅套餐"],
    [{ expired: true }, "你的套餐已到期"],
    [{ over_quota: true }, "你的流量额度已用尽"],
  ] as const)("keeps unavailable quota state distinct: %j", async (overrides, message) => {
    subscriberState.session = { ...session };
    vi.mocked(subscriberProfile).mockResolvedValue({ ...profile, quota: { ...profile.quota, ...overrides, available: false } });
    await mount();
    expect(screen.getByText(message).closest('[role="alert"]')).toBeTruthy();
    const download = document.querySelector('[aria-label="下载订阅"]')!;
    expect(download.classList.contains("ant-btn-disabled") || download.hasAttribute("disabled")).toBe(true);
  });
  it("does not turn a malformed subscription URL into an executable link", async () => {
    subscriberState.session = { ...session };
    vi.mocked(subscriberToken).mockResolvedValue({ ...token, subscription_url: "javascript:alert(1)" });
    await mount();
    expect(input("订阅地址").value).toBe("");
    expect((screen.getByRole("button", { name: "复制订阅链接" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.querySelector('[aria-label="下载订阅"]')?.getAttribute("href")).toBeNull();
  });
  it("uses a Chinese fallback instead of rendering an unknown upstream error body", async () => {
    subscriberState.session = { ...session };
    vi.mocked(subscriberProfile).mockRejectedValue(new Error("upstream body https://example.test/?token=PRIVATE"));
    await mount();
    expect(screen.getByRole("alert").textContent).toContain("暂时无法加载账户信息。");
    expect(document.body.textContent).not.toMatch(/upstream body|PRIVATE/);
  });
});
