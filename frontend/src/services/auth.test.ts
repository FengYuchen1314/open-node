import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  acceptOperatorSession,
  administratorSecurity,
  authenticatedFetch,
  authState,
  beginAdministratorTotp,
  changePassword,
  confirmAdministratorTotp,
  disableAdministratorTotp,
  loadSession,
  regenerateAdministratorRecoveryCodes,
  signIn,
  signOut,
  updateAdministratorTotpPolicy,
  verifySignIn,
} from "./auth";

const session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-secret" };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

beforeEach(() => {
  authState.ready = false;
  authState.session = null;
  authState.error = "";
});
afterEach(() => vi.unstubAllGlobals());

describe("operator authentication", () => {
  it("loads the cookie-backed session without browser storage", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json(session));
    await loadSession(fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/auth/session");
    expect(fetcher.mock.calls[0][1]?.credentials).toBe("include");
    expect(authState.session?.authenticated).toBe(true);
    expect(authState.ready).toBe(true);
  });

  it("stays signed out when the session endpoint is unreachable", async () => {
    await loadSession(vi.fn<typeof fetch>().mockRejectedValue(new Error("Offline")));
    expect(authState.ready).toBe(true);
    expect(authState.session).toBeNull();
    expect(authState.error).toBe("连接已断开。");
  });

  it("sends an explicit login header and preserves server errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json({ detail: "Invalid username or password" }, 401));
    await signIn("admin", "password", fetcher);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("X-Open-Node-Client")).toBe("browser");
    expect(authState.session?.username).toBe("admin");
    await expect(signIn("admin", "wrong", fetcher)).rejects.toThrow("用户名或密码错误。");
  });

  it("keeps a password-only challenge outside the authenticated session", async () => {
    const challenge = {
      configured: true, authenticated: false, username: null, csrf_token: null,
      requires_2fa: true, challenge: "short-lived-challenge", enrollment_required: false,
      enrollment: null, recovery_codes: [],
    };
    const result = await signIn("admin", "password", vi.fn<typeof fetch>().mockResolvedValue(json(challenge)));
    expect(result.challenge).toBe("short-lived-challenge");
    expect(authState.session?.authenticated).toBe(false);
    expect(authState.session).not.toHaveProperty("challenge");
  });

  it("verifies the second factor before accepting a session", async () => {
    const result = { ...session, requires_2fa: false, challenge: null, enrollment_required: false, enrollment: null, recovery_codes: [] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json(result));
    await verifySignIn("challenge", "123456", fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/auth/login/verify");
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ challenge: "challenge", code: "123456" });
    expect(authState.session).toEqual(session);
  });

  it("waits for recovery-code acknowledgment without retaining codes in global auth state", async () => {
    const result = { ...session, requires_2fa: false, challenge: null, enrollment_required: false, enrollment: null, recovery_codes: ["one-time-code"] };
    const verified = await verifySignIn("challenge", "123456", vi.fn<typeof fetch>().mockResolvedValue(json(result)));
    expect(authState.session).toBeNull();
    acceptOperatorSession(verified);
    expect(authState.session).toEqual(session);
    expect(authState.session).not.toHaveProperty("recovery_codes");
  });

  it("sends CSRF-protected administrator MFA operations", async () => {
    authState.session = { ...session };
    const security = { totp_enabled: true, totp_available: true, recovery_codes_remaining: 10, require_totp: false };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json(security))
      .mockResolvedValueOnce(json({ secret: "secret", provisioning_uri: "otpauth://totp/admin", expires_at: "2026-08-30T12:00:00Z" }))
      .mockResolvedValueOnce(json({ recovery_codes: ["new-code"] }))
      .mockResolvedValueOnce(json({ recovery_codes: ["replacement-code"] }))
      .mockResolvedValueOnce(json({ ...security, require_totp: true }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await administratorSecurity(fetcher)).toEqual(security);
    expect((await beginAdministratorTotp("password", fetcher)).secret).toBe("secret");
    expect(await confirmAdministratorTotp("123456", fetcher)).toEqual(["new-code"]);
    expect(await regenerateAdministratorRecoveryCodes("password", "code", fetcher)).toEqual(["replacement-code"]);
    expect((await updateAdministratorTotpPolicy(true, "password", "code", fetcher)).require_totp).toBe(true);
    await disableAdministratorTotp("password", "code", fetcher);
    for (const [, init] of fetcher.mock.calls) {
      expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf-secret");
      expect(init?.credentials).toBe("include");
    }
  });

  it("adds CSRF only to writes and includes cookies on management requests", async () => {
    authState.session = { ...session };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetcher);
    await authenticatedFetch("/api/v1/servers");
    await authenticatedFetch("/api/v1/servers", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe("csrf-secret");
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("Content-Type")).toBe("application/json");
    expect(fetcher.mock.calls[1][1]?.credentials).toBe("include");
  });

  it("clears operator state when the server expires a session", async () => {
    authState.session = { ...session };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(json({}, 401)));
    expect((await authenticatedFetch("/api/v1/servers")).status).toBe(401);
    expect(authState.session?.authenticated).toBe(false);
    expect(authState.session?.csrf_token).toBeNull();
  });

  it.each(["logout", "password"])("clears session after %s succeeds", async (operation) => {
    authState.session = { ...session };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    if (operation === "logout") await signOut(fetcher);
    else await changePassword("current", "new-password", fetcher);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/auth/${operation}`);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("X-CSRF-Token")).toBe("csrf-secret");
    expect(authState.session?.authenticated).toBe(false);
  });

  it("does not pretend a failed logout revoked the server session", async () => {
    authState.session = { ...session };
    await expect(signOut(vi.fn<typeof fetch>().mockResolvedValue(json({}, 500)))).rejects.toThrow();
    expect(authState.session?.authenticated).toBe(true);
  });

  it("returns to sign-in when a password change discovers an expired session", async () => {
    authState.session = { ...session };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ detail: "Administrator sign-in required" }, 401));
    await expect(changePassword("current", "replacement", fetcher)).rejects.toThrow();
    expect(authState.session?.authenticated).toBe(false);
  });
});
