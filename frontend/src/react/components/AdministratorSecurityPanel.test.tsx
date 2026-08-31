// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { administratorSecurity, beginAdministratorTotp, confirmAdministratorTotp, disableAdministratorTotp, regenerateAdministratorRecoveryCodes, updateAdministratorTotpPolicy } from "../../services/auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AdministratorSecurityPanel from "./AdministratorSecurityPanel";

vi.mock("../../services/auth", () => ({ administratorSecurity: vi.fn(), beginAdministratorTotp: vi.fn(), confirmAdministratorTotp: vi.fn(), disableAdministratorTotp: vi.fn(), regenerateAdministratorRecoveryCodes: vi.fn(), updateAdministratorTotpPolicy: vi.fn() }));
const { qrDataUrl } = vi.hoisted(() => ({ qrDataUrl: vi.fn<(text: string, options?: object) => Promise<string>>() }));
vi.mock("qrcode", () => ({ default: { toDataURL: qrDataUrl } }));
const disabled = { totp_enabled: false, totp_available: true, recovery_codes_remaining: 0, require_totp: false };
const enabled = { ...disabled, totp_enabled: true, recovery_codes_remaining: 10 };
const enrollment = { secret: "PRIVATE-ADMIN-SECRET", provisioning_uri: "otpauth://totp/private", expires_at: "2026-08-31T12:00:00Z" };
const codes = ["PRIVATE-ADMIN-CODE-ONE", "PRIVATE-ADMIN-CODE-TWO"];
beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(administratorSecurity).mockResolvedValue(disabled);
  vi.mocked(beginAdministratorTotp).mockResolvedValue(enrollment);
  vi.mocked(confirmAdministratorTotp).mockResolvedValue(codes);
  vi.mocked(regenerateAdministratorRecoveryCodes).mockResolvedValue(codes);
  qrDataUrl.mockResolvedValue("data:image/png;base64,cXI=");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function modal() { return within(screen.getByRole("dialog")); }
function password() { fireEvent.change(modal().getByLabelText("当前密码"), { target: { value: "private-password" } }); }
async function proof() {
  password(); fireEvent.change(modal().getByLabelText("验证器验证码或恢复码"), { target: { value: "proof-code" } });
  fireEvent.click(modal().getByRole("button", { name: "确认" })); await flush();
}
describe("React administrator security", () => {
  it("keeps recovery codes local and blocks dismissal until acknowledgment", async () => {
    renderUi(<AdministratorSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "启用" })); password();
    fireEvent.click(modal().getByRole("button", { name: "开始设置" })); await flush();
    expect(beginAdministratorTotp).toHaveBeenCalledExactlyOnceWith("private-password");
    expect((modal().getByLabelText("验证器密钥") as HTMLInputElement).value).toBe(enrollment.secret);
    fireEvent.change(modal().getByLabelText("验证器验证码"), { target: { value: "123456" } });
    vi.mocked(administratorSecurity).mockResolvedValue(enabled);
    fireEvent.click(modal().getByRole("button", { name: "确认" })); await flush();
    expect(confirmAdministratorTotp).toHaveBeenCalledExactlyOnceWith("123456");
    expect(modal().queryByLabelText("验证器密钥")).toBeNull();
    expect(screen.queryByAltText("管理员验证器绑定二维码")).toBeNull();
    expect((modal().getByRole("button", { name: "完成" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" }); expect(screen.getByText(codes[0])).toBeTruthy();
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toMatch(/PRIVATE-/);
    fireEvent.click(modal().getByRole("checkbox", { name: "我已妥善保存恢复码" }));
    fireEvent.click(modal().getByRole("button", { name: "完成" })); await flush();
    expect(screen.queryByText(codes[0])).toBeNull();
  });
  it("requires explicit proof for policy changes and prevents disablement while mandatory", async () => {
    vi.mocked(administratorSecurity).mockResolvedValue(enabled);
    vi.mocked(updateAdministratorTotpPolicy).mockResolvedValue({ ...enabled, require_totp: true });
    renderUi(<AdministratorSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "强制双因素认证" }));
    password(); expect((modal().getByRole("button", { name: "确认" }) as HTMLButtonElement).disabled).toBe(true);
    vi.mocked(administratorSecurity).mockResolvedValue({ ...enabled, require_totp: true });
    await proof();
    expect(updateAdministratorTotpPolicy).toHaveBeenCalledExactlyOnceWith(true, "private-password", "proof-code");
    expect((screen.getByRole("button", { name: "停用" }) as HTMLButtonElement).disabled).toBe(true);
    expect(disableAdministratorTotp).not.toHaveBeenCalled();
  });
  it("refuses concurrent mutations and clears sensitive proof after a failed operation", async () => {
    const pending = deferred<string[]>(); vi.mocked(regenerateAdministratorRecoveryCodes).mockReturnValue(pending.promise);
    vi.mocked(administratorSecurity).mockResolvedValue(enabled);
    renderUi(<AdministratorSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成新恢复码" })); password();
    const input = modal().getByLabelText("验证器验证码或恢复码");
    fireEvent.change(input, { target: { value: "proof-code" } });
    fireEvent.submit(input.closest("form")!); fireEvent.submit(input.closest("form")!); await flush();
    expect(regenerateAdministratorRecoveryCodes).toHaveBeenCalledExactlyOnceWith("private-password", "proof-code");
    expect((modal().getByRole("button", { name: "取消" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => pending.reject(new Error("验证凭据被拒绝")));
    expect(modal().getByText("验证凭据被拒绝")).toBeTruthy();
    expect((modal().getByLabelText("当前密码") as HTMLInputElement).value).toBe("");
    expect((modal().getByLabelText("验证器验证码或恢复码") as HTMLInputElement).value).toBe("");
  });
  it("does not carry a late clipboard failure into a newly opened dialog", async () => {
    vi.mocked(administratorSecurity).mockResolvedValue(enabled);
    const clipboard = deferred<void>(); vi.mocked(navigator.clipboard.writeText).mockReturnValue(clipboard.promise);
    renderUi(<AdministratorSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成新恢复码" })); await proof();
    fireEvent.click(modal().getByRole("button", { name: "复制" }));
    fireEvent.click(modal().getByRole("checkbox", { name: "我已妥善保存恢复码" }));
    fireEvent.click(modal().getByRole("button", { name: "完成" })); await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成新恢复码" }));
    await act(async () => clipboard.reject(new Error("Clipboard refused")));
    expect(screen.queryByText("无法复制恢复码")).toBeNull();
    expect((modal().getByLabelText("当前密码") as HTMLInputElement).value).toBe("");
    expect(screen.queryByText(codes[0])).toBeNull();
  });
});
