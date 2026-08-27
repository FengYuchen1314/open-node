import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authenticatedFetch, authState, changePassword, loadSession, signIn, signOut } from "./auth";

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
    expect(authState.error).toBe("Offline");
  });

  it("sends an explicit login header and preserves server errors", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json({ detail: "Invalid username or password" }, 401));
    await signIn("admin", "password", fetcher);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("X-Open-Node-Client")).toBe("browser");
    expect(authState.session?.username).toBe("admin");
    await expect(signIn("admin", "wrong", fetcher)).rejects.toThrow("Invalid username or password");
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
