import { beforeEach, describe, expect, it, vi } from "vitest";
import { subscriberState } from "./subscriber-auth";
import { getAccountSubscriberPermissions, getSubscriberPermissions, updateSubscriberPermissions } from "./subscriber-permissions";

const settings = { revision: 2, pages: ["templates", "renewals"], template_quota: 3, external_source_quota: 2, license_required: false } as const;
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  vi.restoreAllMocks(); subscriberState.session = { authenticated: true, username: "alice", csrf_token: "csrf", requires_2fa: false, challenge: null };
});

describe("subscriber permissions service", () => {
  it("strictly reads and saves the canonical administrator policy", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => response(settings));
    expect(await getSubscriberPermissions(fetcher)).toEqual(settings);
    expect(await updateSubscriberPermissions({ expected_revision: 2, pages: ["templates", "renewals"], template_quota: 3, external_source_quota: 2, license_required: false }, fetcher)).toEqual(settings);
    expect(fetcher.mock.calls[1]?.[0]).toContain("/api/v1/subscriber-permissions");
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ expected_revision: 2, pages: ["templates", "renewals"], template_quota: 3, external_source_quota: 2, license_required: false });
  });
  it("rejects reordered, duplicate, excessive and extra response values", async () => {
    for (const value of [
      { ...settings, pages: ["renewals", "templates"] },
      { ...settings, pages: ["templates", "templates"] },
      { ...settings, template_quota: 1001 },
      { ...settings, PRIVATE: "secret" },
    ]) await expect(getSubscriberPermissions(vi.fn(() => response(value)))).rejects.toThrow("未能确认");
  });
  it("reads account usage without accepting an invalid shape", async () => {
    const value = { pages: ["external_subscriptions"], templates: { used: 2, maximum: 3 }, external_sources: { used: 1, maximum: 0 }, license_required: false };
    expect(await getAccountSubscriberPermissions(vi.fn(() => response(value)))).toEqual(value);
    await expect(getAccountSubscriberPermissions(vi.fn(() => response({ ...value, templates: { used: -1, maximum: 3 } })))).rejects.toThrow("未能确认");
  });
});
