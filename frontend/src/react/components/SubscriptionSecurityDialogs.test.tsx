// @vitest-environment jsdom
import { act, cleanup, fireEvent, render as renderUi, screen } from "@testing-library/react";
import { ConfigProvider } from "../../ui";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubscriptionShortCodeDialog from "./SubscriptionShortCodeDialog";
import UserLoginDialog from "./UserLoginDialog";
import SubscriptionIpPolicyDialog from "./SubscriptionIpPolicyDialog";
import TemporarySubscriptionDialog from "./TemporarySubscriptionDialog";
import RegistrationInvitationsDialog from "./RegistrationInvitationsDialog";
import { getProductUserIpPolicy, getProductUserSubscriptionToken, updateProductUserIpPolicy, updateProductUserShortCode } from "../../services/subscriptions";
import { subscriberAccount, subscriberSecurity, subscriberShortCode, subscriberToken } from "../../services/subscriber-auth";
import { createTemporarySubscription } from "../../services/temporary-subscriptions";
import { createRegistrationInvitation, listRegistrationInvitations, revokeRegistrationInvitation } from "../../services/registration-invitations";
import type { ProductUserSubscriptionToken, SubscriptionPlan } from "../../domain/subscriptions";
import type { TemporarySubscription } from "../../domain/temporary-subscriptions";
import type { RegistrationInvitation } from "../../domain/registration-invitations";

const render = (ui: Parameters<typeof renderUi>[0]) => renderUi(ui, { wrapper: ({ children }) => <ConfigProvider>{children}</ConfigProvider> });

vi.mock("../../services/subscriptions", () => ({ getProductUserIpPolicy: vi.fn(), getProductUserSubscriptionToken: vi.fn(), updateProductUserIpPolicy: vi.fn(), updateProductUserShortCode: vi.fn() }));
vi.mock("../../services/subscriber-auth", () => ({ subscriberAccount: vi.fn(), subscriberSecurity: vi.fn(), subscriberShortCode: vi.fn(), subscriberToken: vi.fn(), subscriberIpPolicy: vi.fn(), updateSubscriberIpPolicy: vi.fn() }));
vi.mock("../../services/temporary-subscriptions", () => ({ createTemporarySubscription: vi.fn() }));
vi.mock("../../services/registration-invitations", () => ({ createRegistrationInvitation: vi.fn(), listRegistrationInvitations: vi.fn(), revokeRegistrationInvitation: vi.fn() }));
const token: ProductUserSubscriptionToken = { username: "alice", token: "private-token", short_code: "System12", generated_short_code: "System12", custom_short_code: null, revision: "rev-1", subscription_url: "https://sub.example/private-token", short_url: "https://sub.example/s/System12", short_links_enabled: true, created_at: "", updated_at: "" };
const temporary: TemporarySubscription = { id: "tmp", username: "alice", label: "Temporary subscription", node_ids: ["a"], max_access: 1, access_count: 0, expires_at: "2026-09-01T00:00:00Z", status: "active", subscription_url: "https://sub.example/t/private-once", created_at: "", updated_at: "" };
const invitation: RegistrationInvitation = { id: "invite", token_hint: "hint", plan_id: "p", plan_name: "Basic", status: "active", used_by: null, expires_at: "2026-09-01T00:00:00Z", used_at: null, revoked_at: null, created_at: "" };
async function flush() { await act(async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); }); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(getProductUserSubscriptionToken).mockResolvedValue({ subscription: token, license_required: false });
  vi.mocked(subscriberToken).mockResolvedValue(token);
  vi.mocked(subscriberSecurity).mockResolvedValue({ totp_enabled: true, recovery_codes_remaining: 5 } as Awaited<ReturnType<typeof subscriberSecurity>>);
  vi.mocked(subscriberAccount).mockResolvedValue({ username: "alice", configured: true, totp_enabled: true, revision: "account-r1" } as Awaited<ReturnType<typeof subscriberAccount>>);
  vi.mocked(getProductUserIpPolicy).mockResolvedValue({ username: "alice", enabled: false, networks: [], updated_at: null, license_required: false });
  vi.mocked(createTemporarySubscription).mockResolvedValue(temporary);
  vi.mocked(listRegistrationInvitations).mockResolvedValue({ invitations: [], license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("React subscription security dialogs", { timeout: 20_000 }, () => {
  it("requires password and MFA proof, passes the revision, and clears proof after failure", async () => {
    vi.mocked(subscriberShortCode).mockRejectedValue(new Error("验证凭据无效"));
    render(<SubscriptionShortCodeDialog open username="alice" subscriber onOpenChange={vi.fn()} />); await flush();
    fireEvent.change(screen.getByLabelText("自定义短码"), { target: { value: "NewCode1" } });
    expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: "private-password" } });
    fireEvent.change(screen.getByLabelText("验证器验证码或恢复码"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(subscriberShortCode).toHaveBeenCalledWith("NewCode1", "rev-1", { password: "private-password", code: "123456" });
    expect((screen.getByLabelText("当前密码") as HTMLInputElement).value).toBe(""); expect((screen.getByLabelText("验证器验证码或恢复码") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("验证凭据无效")).toBeTruthy();
  });
  it("does not write a late short-code response into another user's open dialog", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof updateProductUserShortCode>>) => void;
    vi.mocked(updateProductUserShortCode).mockReturnValue(new Promise(done => { resolve = done; }));
    const props = { open: true, username: "alice", onOpenChange: vi.fn(), onSaved: vi.fn() };
    const { rerender } = render(<SubscriptionShortCodeDialog {...props} />); await flush();
    fireEvent.change(screen.getByLabelText("自定义短码"), { target: { value: "NewCode1" } }); fireEvent.click(screen.getByRole("button", { name: "保存" }));
    vi.mocked(getProductUserSubscriptionToken).mockResolvedValue({ subscription: { ...token, username: "bob", short_url: "https://sub.example/s/bob-code" }, license_required: false });
    rerender(<SubscriptionShortCodeDialog {...props} username="bob" />); await flush();
    await act(async () => resolve({ subscription: { ...token, short_url: "https://sub.example/s/late-secret" }, license_required: false }));
    expect(props.onSaved).not.toHaveBeenCalled(); expect((screen.getByLabelText("订阅短链接") as HTMLInputElement).value).toContain("bob-code");
  });
  it("keeps password reset behind matching confirmation and session-revocation acknowledgement", async () => {
    render(<UserLoginDialog open username="alice" onOpenChange={vi.fn()} />); await flush();
    fireEvent.change(screen.getByLabelText("新登录密码"), { target: { value: "a-long-password" } }); fireEvent.change(screen.getByLabelText("确认登录密码"), { target: { value: "a-long-password" } });
    expect((screen.getByRole("button", { name: "保存密码" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "撤销该用户的所有已有会话" })); fireEvent.click(screen.getByRole("checkbox", { name: "重置双因素认证及恢复码" }));
    fireEvent.click(screen.getByRole("button", { name: "保存密码" })); await flush();
    expect(subscriberAccount).toHaveBeenLastCalledWith("alice", { expected_revision: "account-r1", new_password: "a-long-password", reset_totp: true });
    expect((screen.getByLabelText("新登录密码") as HTMLInputElement).value).toBe(""); expect((screen.getByLabelText("确认登录密码") as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/已有会话已全部撤销/)).toBeTruthy();
  });
  it("keeps rejected network policies open and saves parsed CIDRs without erasing them", async () => {
    vi.mocked(updateProductUserIpPolicy).mockRejectedValue(new Error("CIDR 网段无效")); const onOpenChange = vi.fn();
    render(<SubscriptionIpPolicyDialog open username="alice" onOpenChange={onOpenChange} />); await flush();
    fireEvent.change(screen.getByLabelText("允许的 IP 地址和 CIDR 网段"), { target: { value: "192.0.2.0/24, 2001:db8::/32" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(updateProductUserIpPolicy).toHaveBeenCalledWith("alice", ["192.0.2.0/24", "2001:db8::/32"]); expect(onOpenChange).not.toHaveBeenCalled(); expect(screen.getByText("CIDR 网段无效")).toBeTruthy();
  });
  it("destroys temporary URLs on close and rejects a stale node scope", async () => {
    const props = { open: true, username: "alice", nodes: [{ title: "Alpha", value: "a" }], onOpenChange: vi.fn() };
    const { rerender } = render(<TemporarySubscriptionDialog {...props} />);
    rerender(<TemporarySubscriptionDialog {...props} nodes={[]} />); expect((screen.getByRole("button", { name: "创建" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<TemporarySubscriptionDialog {...props} />); fireEvent.click(screen.getByRole("button", { name: "创建" })); await flush();
    expect(createTemporarySubscription).toHaveBeenCalledWith({ username: "alice", label: "临时订阅", node_ids: ["a"], max_access: 1, expires_in_seconds: 300 });
    expect((screen.getByLabelText("临时订阅链接") as HTMLInputElement).value).toBe(temporary.subscription_url);
    rerender(<TemporarySubscriptionDialog {...props} open={false} />); expect(screen.queryByLabelText("临时订阅链接")).toBeNull();
    rerender(<TemporarySubscriptionDialog {...props} />); expect(screen.queryByLabelText("临时订阅链接")).toBeNull();
  });
  it("discards late temporary-link creation after close", async () => {
    let resolve!: (value: TemporarySubscription) => void; vi.mocked(createTemporarySubscription).mockReturnValue(new Promise(done => { resolve = done; }));
    const props = { open: true, username: "alice", nodes: [{ title: "Alpha", value: "a" }], onOpenChange: vi.fn(), onCreated: vi.fn() };
    const { rerender } = render(<TemporarySubscriptionDialog {...props} />); fireEvent.click(screen.getByRole("button", { name: "创建" })); rerender(<TemporarySubscriptionDialog {...props} open={false} />);
    await act(async () => resolve(temporary)); expect(props.onCreated).not.toHaveBeenCalled(); expect(screen.queryByLabelText("临时订阅链接")).toBeNull();
  });
  it("does not create a temporary link from invalid download counts after blur or Enter", async () => {
    render(<TemporarySubscriptionDialog open username="alice" nodes={[{ title: "Alpha", value: "a" }]} onOpenChange={vi.fn()} />);
    const input = screen.getByLabelText("下载次数"), create = screen.getByRole("button", { name: "创建" }) as HTMLButtonElement;
    for (const value of ["0", "101", "0.4", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "1" } }); fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
      expect(create.disabled).toBe(true);
      fireEvent.click(create); await flush(); expect(createTemporarySubscription).not.toHaveBeenCalled();
    }
  });
  it("shows invitation secrets once and requires confirmation before revoking", async () => {
    vi.mocked(createRegistrationInvitation).mockResolvedValue({ invitation, registration_url: "https://sub.example/register/private-invite", license_required: false });
    vi.mocked(revokeRegistrationInvitation).mockResolvedValue({ ...invitation, status: "revoked" });
    const props = { open: true, plans: [{ id: "p", name: "Basic" }] as SubscriptionPlan[], onOpenChange: vi.fn() };
    const { rerender } = render(<RegistrationInvitationsDialog {...props} />); await flush(); fireEvent.click(screen.getByRole("button", { name: "创建注册邀请" })); await flush();
    expect((screen.getByLabelText("注册链接") as HTMLInputElement).value).toContain("private-invite");
    fireEvent.click(screen.getByRole("button", { name: "撤销 Basic 的邀请" })); expect(revokeRegistrationInvitation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "撤销" })); await flush(); expect(revokeRegistrationInvitation).toHaveBeenCalledWith("invite"); expect(screen.queryByLabelText("注册链接")).toBeNull();
    rerender(<RegistrationInvitationsDialog {...props} open={false} />); rerender(<RegistrationInvitationsDialog {...props} />); await flush(); expect(screen.queryByLabelText("注册链接")).toBeNull();
  });
});
