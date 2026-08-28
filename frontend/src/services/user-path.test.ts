import { describe, expect, it } from "vitest";
import { userPath } from "./user-path";
import { getPlanManagement, removePlan } from "./plan-management";
import { getProductUserQuota, getSubscriptionFormatPreview } from "./subscriptions";
import { getUserManagement, removeUser } from "./user-management";

describe("subscriber request paths", () => {
  it("retains existing paths for ordinary usernames", () => {
    expect(userPath("a+b", "settings")).toBe("/users/a%2Bb/settings");
    expect(userPath("alice", "quota", { now: "2026-08-29T00:00:00Z" })).toBe("/users/alice/quota?now=2026-08-29T00%3A00%3A00Z");
  });
  it("keeps slash usernames outside operation path segments", () => {
    expect(userPath("alice/plan", "remove")).toBe("/user-remove?username=alice%2Fplan");
    expect(userPath("alice/plan", "plan/remove")).toBe("/user-plan/remove?username=alice%2Fplan");
  });
  it.each([".", ".."])('keeps the literal username "%s" out of URL normalization', (username) => {
    expect(userPath(username, "settings")).toBe(`/user-settings?username=${username}`);
  });
  it("uses query aliases for account, plan and subscription requests", async () => {
    const urls: string[] = [];
    const fetcher: typeof fetch = async (input) => { urls.push(String(input)); return new Response("{}"); };
    await getUserManagement("a/b", fetcher);
    await removeUser("a/b", "r", "a/b", false, fetcher);
    await getPlanManagement("a/b", "unassign", fetcher);
    await removePlan("a/b", "unassign", "r", "a/b", fetcher);
    await getProductUserQuota("a/b", "2026-08-29T00:00:00Z", fetcher);
    await getSubscriptionFormatPreview("a/b", "xray", fetcher);
    expect(urls.slice(0, 4)).toEqual(["/api/v1/user-settings?username=a%2Fb", "/api/v1/user-remove?username=a%2Fb", "/api/v1/user-plan/removal?username=a%2Fb", "/api/v1/user-plan/remove?username=a%2Fb"]);
    expect(new URL(urls[4], "https://example.test").searchParams.get("now")).toBe("2026-08-29T00:00:00Z");
    expect(new URL(urls[5], "https://example.test").searchParams.get("username")).toBe("a/b");
    expect(new URL(urls[5], "https://example.test").searchParams.get("format")).toBe("xray");
  });
});
