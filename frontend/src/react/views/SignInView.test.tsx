// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { acceptOperatorSession, authState, loadSession, signIn, verifySignIn, type OperatorLogin } from "../../services/auth";
import { getPublicBranding } from "../../services/branding";
import { BrandingProvider } from "../hooks/useBranding";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import SignInView from "./SignInView";
import { getInitialSetupStatus } from "../../services/initial-setup";

vi.mock("../../services/auth", async importOriginal => ({ ...await importOriginal<typeof import("../../services/auth")>(), signIn: vi.fn(), verifySignIn: vi.fn(), acceptOperatorSession: vi.fn(), loadSession: vi.fn() }));
vi.mock("../../services/branding", async original => ({ ...await original<typeof import("../../services/branding")>(), getPublicBranding: vi.fn() }));
const { qrDataUrl } = vi.hoisted(() => ({ qrDataUrl: vi.fn<(text: string, options?: object) => Promise<string>>() }));
vi.mock("qrcode", () => ({ default: { toDataURL: qrDataUrl } }));
vi.mock("../../services/initial-setup", async original => ({ ...await original<typeof import("../../services/initial-setup")>(), getInitialSetupStatus: vi.fn() }));
const anonymous = { configured: true, authenticated: false, username: null, csrf_token: null };
const challenge: OperatorLogin = { ...anonymous, requires_2fa: true, challenge: "memory-only-challenge", enrollment_required: false, enrollment: null, recovery_codes: [] };
const authenticated: OperatorLogin = { ...challenge, authenticated: true, username: "admin", csrf_token: "private-csrf", requires_2fa: false, challenge: null };
beforeEach(() => {
  vi.resetAllMocks(); installDom();
  authState.ready = true; authState.error = ""; authState.session = { ...anonymous };
  vi.mocked(signIn).mockResolvedValue(challenge);
  vi.mocked(verifySignIn).mockResolvedValue(authenticated);
  qrDataUrl.mockResolvedValue("data:image/png;base64,cXI=");
  vi.mocked(getInitialSetupStatus).mockResolvedValue({ configured: false, available: true });
});
afterEach(async () => {
  cleanup();
  // Form feedback used to debounce help state for up to 10 ms. Let those
  // presentation timers finish before Vitest disposes the jsdom window.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
function fillCredentials() {
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "administrator-password" } });
}
async function login() { fillCredentials(); fireEvent.click(screen.getByRole("button", { name: "登录" })); await flush(); }
describe("React administrator sign-in", () => {
  it("uses a public brand as plain text without changing credential or MFA semantics", async () => {
    const brand = "<img src=x onerror=evil()>";
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "站点", brand_title: brand, license_required: false });
    renderUi(<BrandingProvider><SignInView /></BrandingProvider>); await flush();
    expect(screen.getByRole("heading", { name: brand }).classList.contains("branding-block-text")).toBe(true);
    expect(document.querySelector("img")).toBeNull(); expect(screen.getByLabelText("密码")).toBeTruthy();
    await login(); expect(signIn).toHaveBeenCalledExactlyOnceWith("admin", "administrator-password");
    expect(screen.getByLabelText("验证器验证码或恢复码")).toBeTruthy();
  });
  it("keeps password and MFA steps distinct and refuses duplicate password submissions", async () => {
    const pending = deferred<OperatorLogin>(); vi.mocked(signIn).mockReturnValue(pending.promise);
    renderUi(<SignInView />); fillCredentials();
    const form = screen.getByLabelText("密码").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(signIn).toHaveBeenCalledExactlyOnceWith("admin", "administrator-password");
    await act(async () => pending.resolve(challenge));
    expect(screen.queryByLabelText("密码")).toBeNull();
    expect(screen.getByLabelText("验证器验证码或恢复码")).toBeTruthy();
    expect(acceptOperatorSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新开始" }));
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
  });
  it("does not accept mandatory enrollment until the user acknowledges the recovery codes", async () => {
    const enrollment = { secret: "PRIVATE-ENROLLMENT-SECRET", provisioning_uri: "otpauth://totp/private", expires_at: "2026-08-31T12:00:00Z" };
    vi.mocked(signIn).mockResolvedValue({ ...challenge, enrollment_required: true, enrollment });
    const result = { ...authenticated, recovery_codes: ["PRIVATE-CODE-ONE", "PRIVATE-CODE-TWO"] };
    vi.mocked(verifySignIn).mockResolvedValue(result);
    renderUi(<SignInView />); await login();
    expect((screen.getByLabelText("验证器密钥") as HTMLInputElement).value).toBe(enrollment.secret);
    fireEvent.change(screen.getByLabelText("验证器验证码"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" })); await flush();
    expect(verifySignIn).toHaveBeenCalledExactlyOnceWith(challenge.challenge, "123456");
    expect(screen.queryByLabelText("验证器密钥")).toBeNull();
    expect(screen.queryByAltText("管理员验证器绑定二维码")).toBeNull();
    expect((screen.getByRole("button", { name: "进入 Open Node" }) as HTMLButtonElement).disabled).toBe(true);
    expect(acceptOperatorSession).not.toHaveBeenCalled();
    expect(JSON.stringify({ ...localStorage, ...sessionStorage, state: authState })).not.toMatch(/PRIVATE-/);
    fireEvent.click(screen.getByRole("checkbox", { name: "我已妥善保存恢复码" }));
    fireEvent.click(screen.getByRole("button", { name: "进入 Open Node" })); await flush();
    expect(acceptOperatorSession).toHaveBeenCalledExactlyOnceWith(result);
    expect(document.body.textContent).not.toContain("PRIVATE-CODE");
  });
  it("clears rejected passwords and rejected one-time codes without dropping the challenge", async () => {
    vi.mocked(signIn).mockRejectedValueOnce(new Error("Invalid password"));
    renderUi(<SignInView />); await login();
    expect(screen.getByRole("alert").textContent).toContain("密码错误");
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
    await login(); vi.mocked(verifySignIn).mockRejectedValue(new Error("Invalid second factor"));
    fireEvent.change(screen.getByLabelText("验证器验证码或恢复码"), { target: { value: "wrong-code" } });
    fireEvent.click(screen.getByRole("button", { name: "验证" })); await flush();
    expect((screen.getByLabelText("验证器验证码或恢复码") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("双重验证失败");
  });
  it("shows connection retry and unconfigured states without a misleading login form", async () => {
    authState.error = "Connection unavailable";
    renderUi(<SignInView />);
    expect(screen.queryByLabelText("密码")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新连接" })); expect(loadSession).toHaveBeenCalledOnce();
    await act(async () => { authState.error = ""; authState.session = { ...anonymous, configured: false }; });
    expect(screen.getByText("尚未配置管理员账户。")).toBeTruthy();
    expect(screen.queryByLabelText("密码")).toBeNull();
  });
  it("discards an enrollment QR completion after the sign-in view is unmounted", async () => {
    const pending = deferred<string>(); qrDataUrl.mockReturnValue(pending.promise);
    vi.mocked(signIn).mockResolvedValue({ ...challenge, enrollment_required: true, enrollment: { secret: "LATE-SECRET", provisioning_uri: "otpauth://late", expires_at: "2026-08-31" } });
    const view = renderUi(<SignInView />); await login(); view.unmount();
    await act(async () => pending.resolve("data:image/png;base64,TEFURQ=="));
    expect(document.body.textContent).not.toContain("LATE-SECRET");
    expect(screen.queryByRole("img")).toBeNull();
  });
});
