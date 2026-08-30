// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { acceptOperatorSession, authState, loadSession, signIn, verifySignIn, type OperatorLogin } from "../../services/auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import SignInView from "./SignInView";

vi.mock("../../services/auth", async importOriginal => ({ ...await importOriginal<typeof import("../../services/auth")>(), signIn: vi.fn(), verifySignIn: vi.fn(), acceptOperatorSession: vi.fn(), loadSession: vi.fn() }));
const { qrDataUrl } = vi.hoisted(() => ({ qrDataUrl: vi.fn<(text: string, options?: object) => Promise<string>>() }));
vi.mock("qrcode", () => ({ default: { toDataURL: qrDataUrl } }));
const anonymous = { configured: true, authenticated: false, username: null, csrf_token: null };
const challenge: OperatorLogin = { ...anonymous, requires_2fa: true, challenge: "memory-only-challenge", enrollment_required: false, enrollment: null, recovery_codes: [] };
const authenticated: OperatorLogin = { ...challenge, authenticated: true, username: "admin", csrf_token: "private-csrf", requires_2fa: false, challenge: null };
beforeEach(() => {
  vi.resetAllMocks(); installDom();
  authState.ready = true; authState.error = ""; authState.session = { ...anonymous };
  vi.mocked(signIn).mockResolvedValue(challenge);
  vi.mocked(verifySignIn).mockResolvedValue(authenticated);
  qrDataUrl.mockResolvedValue("data:image/png;base64,cXI=");
});
afterEach(async () => {
  cleanup();
  // Ant Design Form debounces help state for up to 10 ms. Let those
  // presentation timers finish before Vitest disposes the jsdom window.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
function fillCredentials() {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "administrator-password" } });
}
async function login() { fillCredentials(); fireEvent.click(screen.getByRole("button", { name: "Sign In" })); await flush(); }
describe("React administrator sign-in", () => {
  it("keeps password and MFA steps distinct and refuses duplicate password submissions", async () => {
    const pending = deferred<OperatorLogin>(); vi.mocked(signIn).mockReturnValue(pending.promise);
    renderUi(<SignInView />); fillCredentials();
    const form = screen.getByLabelText("Password").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(signIn).toHaveBeenCalledExactlyOnceWith("admin", "administrator-password");
    await act(async () => pending.resolve(challenge));
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.getByLabelText("Authenticator or recovery code")).toBeTruthy();
    expect(acceptOperatorSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
  });
  it("does not accept mandatory enrollment until the user acknowledges the recovery codes", async () => {
    const enrollment = { secret: "PRIVATE-ENROLLMENT-SECRET", provisioning_uri: "otpauth://totp/private", expires_at: "2026-08-31T12:00:00Z" };
    vi.mocked(signIn).mockResolvedValue({ ...challenge, enrollment_required: true, enrollment });
    const result = { ...authenticated, recovery_codes: ["PRIVATE-CODE-ONE", "PRIVATE-CODE-TWO"] };
    vi.mocked(verifySignIn).mockResolvedValue(result);
    renderUi(<SignInView />); await login();
    expect((screen.getByLabelText("Authenticator secret") as HTMLInputElement).value).toBe(enrollment.secret);
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" })); await flush();
    expect(verifySignIn).toHaveBeenCalledExactlyOnceWith(challenge.challenge, "123456");
    expect(screen.queryByLabelText("Authenticator secret")).toBeNull();
    expect(screen.queryByAltText("Administrator authenticator enrollment QR code")).toBeNull();
    expect((screen.getByRole("button", { name: "Continue to Open Node" }) as HTMLButtonElement).disabled).toBe(true);
    expect(acceptOperatorSession).not.toHaveBeenCalled();
    expect(JSON.stringify({ ...localStorage, ...sessionStorage, state: authState })).not.toMatch(/PRIVATE-/);
    fireEvent.click(screen.getByRole("checkbox", { name: "I have stored the recovery codes securely" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue to Open Node" })); await flush();
    expect(acceptOperatorSession).toHaveBeenCalledExactlyOnceWith(result);
    expect(document.body.textContent).not.toContain("PRIVATE-CODE");
  });
  it("clears rejected passwords and rejected one-time codes without dropping the challenge", async () => {
    vi.mocked(signIn).mockRejectedValueOnce(new Error("Invalid password"));
    renderUi(<SignInView />); await login();
    expect(screen.getByRole("alert").textContent).toContain("Invalid password");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    await login(); vi.mocked(verifySignIn).mockRejectedValue(new Error("Invalid second factor"));
    fireEvent.change(screen.getByLabelText("Authenticator or recovery code"), { target: { value: "wrong-code" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" })); await flush();
    expect((screen.getByLabelText("Authenticator or recovery code") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("Invalid second factor");
  });
  it("shows connection retry and unconfigured states without a misleading login form", async () => {
    authState.error = "Connection unavailable";
    renderUi(<SignInView />);
    expect(screen.queryByLabelText("Password")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry connection" })); expect(loadSession).toHaveBeenCalledOnce();
    await act(async () => { authState.error = ""; authState.session = { ...anonymous, configured: false }; });
    expect(screen.getByText("Administrator account is not configured.")).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
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
