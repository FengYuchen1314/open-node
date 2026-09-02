import { beforeEach, describe, expect, it } from "vitest";

import { subscriberState } from "./subscriber-auth";
import {
  createSubscriptionTemplate,
  getSubscriptionTemplateSettings,
  removeSubscriptionTemplate,
  updateSubscriptionTemplate,
  updateSubscriptionTemplateSettings,
} from "./subscription-templates";

describe("subscription templates", () => {
  beforeEach(() => {
    subscriberState.session = { authenticated: true, username: "alice", csrf_token: "csrf", requires_2fa: false, challenge: null };
  });

  it("uses revision guards for administrator writes", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      const removed = init?.method === "POST" && String(input).endsWith("/remove");
      return new Response(removed ? null : JSON.stringify({ id: "id" }), { status: removed ? 204 : 200 });
    };
    const draft = { name: "a.yaml", format: "clash" as const, content: "proxies: []", owner_username: null, is_public: true };
    await createSubscriptionTemplate(draft, false, fetcher);
    await updateSubscriptionTemplate("a+b", draft, "revision", false, fetcher);
    await removeSubscriptionTemplate("a+b", "revision", "a.yaml", false, fetcher);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/subscription-templates",
      "/api/v1/subscription-templates/a%2Bb",
      "/api/v1/subscription-templates/a%2Bb/remove",
    ]);
    expect(JSON.parse(String(calls[1][1]?.body))).toMatchObject({ expected_revision: "revision" });
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({ expected_revision: "revision", confirm_name: "a.yaml" });
  });

  it("uses the subscriber route and CSRF token for personal defaults", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify({ clash_template_id: null, enabled: true, revision: "new" }));
    };
    const settings = await getSubscriptionTemplateSettings(null, true, fetcher);
    await updateSubscriptionTemplateSettings(settings, null, true, fetcher);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/account/subscription-templates/settings",
      "/api/v1/account/subscription-templates/settings",
    ]);
    expect(new Headers(calls[1][1]?.headers).get("X-CSRF-Token")).toBe("csrf");
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      clash_template_id: null,
      enabled: true,
      expected_revision: "new",
    });
  });

  it("keeps server validation details visible", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: [{ loc: ["body", "content"], msg: "Invalid YAML" }] }), { status: 422 });
    await expect(createSubscriptionTemplate({
      name: "a.yaml", format: "clash", content: "x", owner_username: null, is_public: false,
    }, false, fetcher)).rejects.toThrow("content: YAML 格式无效。");
  });
});
