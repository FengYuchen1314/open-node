import { afterEach, describe, expect, it } from "vitest";
import { updateProductUserShortCode } from "./subscriptions";
import { clearSubscriberSession, subscriberFormatUrl, subscriberShortCode, subscriberState } from "./subscriber-auth";
import type { ProductUserSubscriptionToken } from "../domain/subscriptions";

afterEach(clearSubscriberSession);
describe("subscription short code requests", () => {
  it("sends the revision and explicit clear through the exact operator username alias", async () => {
    const fetcher: typeof fetch = async (input, init) => {
      expect(input).toBe("/api/v1/user-subscription-short-code?username=a%2Fb");
      expect(init?.method).toBe("PUT");
      expect(JSON.parse(String(init?.body))).toEqual({ custom_short_code: "", expected_revision: "revision" });
      return new Response('{"subscription":{"short_code":"abc"}}');
    };
    expect((await updateProductUserShortCode("a/b", "", "revision", fetcher)).subscription.short_code).toBe("abc");
  });
  it("sends subscriber proof and CSRF without choosing a target account", async () => {
    subscriberState.session = { authenticated: true, username: "alice", csrf_token: "csrf", requires_2fa: false, challenge: null };
    const fetcher: typeof fetch = async (input, init) => {
      expect(input).toBe("/api/v1/account/subscription-short-code");
      expect(init?.method).toBe("PUT");
      expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
      expect(init?.cache).toBe("no-store");
      expect(JSON.parse(String(init?.body))).toEqual({ custom_short_code: "my-link", expected_revision: "revision", password: "private", code: "proof" });
      return new Response('{"subscription":{"short_code":"my-link"}}');
    };
    expect((await subscriberShortCode("my-link", "revision", { password: "private", code: "proof" }, fetcher)).short_code).toBe("my-link");
  });
  it("preserves node selection while choosing short download URLs and formats", () => {
    const token = { subscription_url: "https://panel.example/api/v1/subscribe/long?node_id=node", short_url: "https://panel.example/api/v1/subscribe/custom?node_id=node" } as ProductUserSubscriptionToken;
    expect(subscriberFormatUrl(token, "xray", true)).toBe("https://panel.example/api/v1/subscribe/custom?node_id=node&format=xray");
    expect(subscriberFormatUrl(token, "clash")).toBe("https://panel.example/api/v1/subscribe/long?node_id=node&format=clash");
  });
  it("reports stale revisions and collisions without hiding the server error", async () => {
    const fetcher: typeof fetch = async () => new Response('{"detail":"Subscription links changed; reload before saving"}', { status: 409 });
    await expect(updateProductUserShortCode("alice", "code", "old", fetcher)).rejects.toThrow("请重新加载后保存");
  });
});
