import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "./auth";
import {
  accountRequest, clearSubscriberSession, loadSubscriberSession, subscriberAccount,
  subscriberChangePassword, subscriberFormatUrl, subscriberSignIn, subscriberSignOut,
  subscriberProfiles, subscriberRegister, subscriberState, verifySubscriberLogin,
} from "./subscriber-auth";
import type { ProductUserSubscriptionToken } from "../domain/subscriptions";

const session = { authenticated: true, username: "alice", csrf_token: "subscriber-csrf", requires_2fa: false, challenge: null };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => { clearSubscriberSession(); subscriberState.ready = false; subscriberState.error = ""; });
afterEach(() => vi.unstubAllGlobals());

describe("subscriber authentication", () => {
  it("keeps operator and subscriber cookies and CSRF state separate", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "operator-csrf" };
    subscriberState.session = { ...session };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({}));
    await accountRequest("me", {}, fetcher);
    await accountRequest("subscription-token", { method: "POST" }, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/account/me");
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("X-CSRF-Token")).toBe("subscriber-csrf");
    expect(fetcher.mock.calls[1][1]?.credentials).toBe("include");
    expect(fetcher.mock.calls[1][1]?.cache).toBe("no-store");
    clearSubscriberSession();
    expect(authState.session.authenticated).toBe(true);
  });

  it("does not authenticate a password-only second-factor challenge", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(json({ ...session, authenticated: false, csrf_token: null, requires_2fa: true, challenge: "pending" }))
      .mockResolvedValueOnce(json(session));
    const result = await subscriberSignIn("alice", "password", fetcher);
    expect(result.challenge).toBe("pending");
    expect(subscriberState.session).toBeNull();
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("X-Open-Node-Client")).toBe("browser");
    await verifySubscriberLogin("pending", "123456", fetcher);
    expect(subscriberState.session?.authenticated).toBe(true);
    expect(JSON.parse(fetcher.mock.calls[1][1]?.body as string)).toEqual({ challenge: "pending", code: "123456" });
  });

  it("registers an invited subscriber without changing session state", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({
      user: { username: "alice" }, plan: { id: "plan-id" }, commands: [], warnings: [],
      license_required: false,
    }, 201));
    const payload = {
      token: "invitation-secret",
      username: "alice",
      password: "subscriber-password",
      email: "alice@example.com",
      display_name: "Alice",
    };
    await subscriberRegister(payload, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/account/register");
    expect(fetcher.mock.calls[0][1]?.method).toBe("POST");
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("X-Open-Node-Client")).toBe("browser");
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual(payload);
    expect(subscriberState.session).toBeNull();
  });

  it("restores a persistent session and reports network failure", async () => {
    await loadSubscriberSession(vi.fn<typeof fetch>().mockResolvedValue(json(session)));
    expect(subscriberState.session?.username).toBe("alice");
    expect(subscriberState.ready).toBe(true);
    await loadSubscriberSession(vi.fn<typeof fetch>().mockRejectedValue(new Error("Offline")));
    expect(subscriberState.session).toBeNull();
    expect(subscriberState.error).toBe("连接已断开。");
  });

  it("cannot restore an old load after logout", async () => {
    let finish!: (response: Response) => void;
    const pending = loadSubscriberSession(vi.fn<typeof fetch>().mockReturnValue(new Promise(resolve => { finish = resolve; })));
    clearSubscriberSession(); finish(json(session)); await pending;
    expect(subscriberState.session).toBeNull();
  });

  it("does not let an old unauthorized response clear a newer sign-in", async () => {
    let finish!: (response: Response) => void;
    const pending = accountRequest("me", {}, vi.fn<typeof fetch>().mockReturnValue(new Promise(resolve => { finish = resolve; })));
    await subscriberSignIn("alice", "password", vi.fn<typeof fetch>().mockResolvedValue(json(session)));
    finish(json({ detail: "Subscriber sign-in required" }, 401));
    await expect(pending).rejects.toThrow("请先登录订阅用户账户。");
    expect(subscriberState.session?.authenticated).toBe(true);
  });

  it.each(["logout", "password"])("clears the session only after %s succeeds", async (mode) => {
    subscriberState.session = { ...session };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    if (mode === "logout") await subscriberSignOut(fetcher);
    else await subscriberChangePassword({ password: "old", code: "123456" }, "replacement-password", fetcher);
    expect(subscriberState.session).toBeNull();
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/account/${mode}`);
    if (mode === "password") expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ password: "old", code: "123456", new_password: "replacement-password" });
  });

  it("retains a valid session after incorrect reauthentication or failed logout", async () => {
    subscriberState.session = { ...session };
    await expect(accountRequest("password", {}, vi.fn<typeof fetch>().mockResolvedValue(json({ detail: "Invalid credentials" }, 400)))).rejects.toThrow("登录凭据错误。");
    await expect(subscriberSignOut(vi.fn<typeof fetch>().mockResolvedValue(json({}, 503)))).rejects.toThrow();
    expect(subscriberState.session?.authenticated).toBe(true);
    await expect(accountRequest("me", {}, vi.fn<typeof fetch>().mockResolvedValue(json({}, 401)))).rejects.toThrow();
    expect(subscriberState.session).toBeNull();
  });

  it("encodes slash-containing usernames in administrative account requests", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ configured: true }));
    await subscriberAccount("group/alice+test@example.com", { expected_revision: "revision", new_password: "replacement-password", reset_totp: false }, fetcher);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/subscriber-accounts?username=group%2Falice%2Btest%40example.com");
    expect(fetcher.mock.calls[0][1]?.method).toBe("PUT");
  });

  it("builds each format URL using the issued token URL", () => {
    const subscription = { subscription_url: "https://panel.example/api/v1/subscribe/private-token" } as ProductUserSubscriptionToken;
    for (const format of ["clash", "surge", "sing-box", "xray", "uri-list", "base64"] as const) {
      expect(subscriberFormatUrl(subscription, format)).toBe(`https://panel.example/api/v1/subscribe/private-token?format=${format}`);
    }
  });

  it("loads assigned subscription profiles from the subscriber realm", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json({ profiles: [], license_required: false }));
    expect((await subscriberProfiles(fetcher)).profiles).toEqual([]);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/account/subscription-profiles");
    expect(fetcher.mock.calls[0][1]?.credentials).toBe("include");
  });
});
