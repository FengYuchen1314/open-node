import { describe, expect, it } from "vitest";
import type { SubscriptionProfile, SubscriptionProfileUpdate } from "../domain/subscription-profiles";
import { listSubscriptionProfiles, updateSubscriptionProfile } from "./subscription-profiles";

const profile = {
  id: "profile-id",
  name: "Mobile",
  revision: "a".repeat(64),
} as SubscriptionProfile;

describe("subscription profile management", () => {
  it("lists and updates profiles through administrator routes", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify(init ? profile : { profiles: [profile], license_required: false }));
    };
    const payload: SubscriptionProfileUpdate = {
      name: "Mobile",
      description: "Phone",
      node_ids: [],
      clash_template_id: null,
      custom_rules_enabled: false,
      selected_custom_rule_ids: [],
      proxy_providers_enabled: false,
      selected_proxy_provider_ids: [],
      override_scripts_enabled: false,
      selected_override_script_ids: [],
      assigned_usernames: ["alice"],
      enabled: true,
      expected_revision: profile.revision,
    };
    expect((await listSubscriptionProfiles(fetcher)).profiles).toEqual([profile]);
    expect(await updateSubscriptionProfile("a/b", payload, fetcher)).toEqual(profile);
    expect(calls.map(([url]) => url)).toEqual([
      "/api/v1/subscription-profiles",
      "/api/v1/subscription-profiles/a%2Fb",
    ]);
    expect(calls[1][1]?.method).toBe("PUT");
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual(payload);
  });
});
