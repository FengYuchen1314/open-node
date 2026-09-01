import { describe, expect, it, vi } from "vitest";

import type { CustomRule, ProxyProvider } from "../domain/subscription-customizations";
import {
  accountSubscriptionCustomizations,
  createCustomRule,
  createProxyProvider,
  deleteCustomRule,
  deleteProxyProvider,
  listCustomRules,
  listProxyProviders,
  updateCustomRule,
  updateProxyProvider,
} from "./subscription-customizations";
import { subscriberState } from "./subscriber-auth";

const now = "2026-09-01T00:00:00Z";
const rule: CustomRule = {
  id: "rule/id", owner_username: "alice", name: "规则", type: "rules", mode: "prepend",
  content: "- MATCH,Proxy\n", enabled: true, revision: 2, created_at: now, updated_at: now,
};
const provider: ProxyProvider = {
  id: "provider/id", owner_username: "alice", external_source_id: "source-id",
  name: "机场", type: "http", interval: 3600, proxy: "DIRECT", size_limit: 0,
  health_check_enabled: true, health_check_url: "https://www.gstatic.com/generate_204",
  health_check_interval: 300, health_check_timeout: 5000, health_check_lazy: true,
  health_check_expected_status: 204, filter: "", exclude_filter: "", exclude_type: "",
  override: {}, process_mode: "client", enabled: true, revision: 3,
  created_at: now, updated_at: now,
};
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), { status }));
}

describe("subscription customizations service", () => {
  it("uses revision-fenced CRUD routes for rules and providers", async () => {
    const queue = [
      { rules: [rule], license_required: false }, rule, { ...rule, revision: 3 }, null,
      { providers: [provider], license_required: false }, provider,
      { ...provider, revision: 4 }, null,
    ];
    const fetcher = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      response(queue.shift(), init?.method === "POST" && String(_url).endsWith("/delete") ? 204 : 200));
    const ruleWrite = {
      owner_username: rule.owner_username, name: rule.name, type: rule.type, mode: rule.mode,
      content: rule.content, enabled: rule.enabled,
    };
    const providerWrite = {
      ...provider,
      id: undefined,
      revision: undefined,
      created_at: undefined,
      updated_at: undefined,
    };
    delete providerWrite.id; delete providerWrite.revision;
    delete providerWrite.created_at; delete providerWrite.updated_at;
    expect((await listCustomRules(fetcher)).rules).toEqual([rule]);
    await createCustomRule(ruleWrite, fetcher);
    await updateCustomRule(rule, ruleWrite, fetcher);
    await deleteCustomRule(rule, fetcher);
    expect((await listProxyProviders(fetcher)).providers).toEqual([provider]);
    await createProxyProvider(providerWrite, fetcher);
    await updateProxyProvider(provider, providerWrite, fetcher);
    await deleteProxyProvider(provider, fetcher);
    expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual([
      "/api/v1/subscription-customizations/rules",
      "/api/v1/subscription-customizations/rules",
      "/api/v1/subscription-customizations/rules/rule%2Fid",
      "/api/v1/subscription-customizations/rules/rule%2Fid/delete",
      "/api/v1/subscription-customizations/providers",
      "/api/v1/subscription-customizations/providers",
      "/api/v1/subscription-customizations/providers/provider%2Fid",
      "/api/v1/subscription-customizations/providers/provider%2Fid/delete",
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toMatchObject({
      expected_revision: 2,
    });
    expect(JSON.parse(String(fetcher.mock.calls[6][1]?.body))).toMatchObject({
      expected_revision: 3,
    });
  });

  it("uses subscriber routes, derives ownership server-side and fences the session", async () => {
    subscriberState.session = {
      authenticated: true, username: "alice", csrf_token: "account-csrf",
      requires_2fa: false, challenge: null,
    };
    const queue = [
      { rules: [rule], license_required: false }, rule, { ...rule, revision: 3 }, null,
      { providers: [provider], license_required: false }, provider,
      { ...provider, revision: 4 }, null,
    ];
    const fetcher = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      response(queue.shift(), init?.method === "POST" && String(_url).endsWith("/delete") ? 204 : 200));
    const api = accountSubscriptionCustomizations("alice", fetcher);
    const ruleWrite = {
      owner_username: rule.owner_username, name: rule.name, type: rule.type, mode: rule.mode,
      content: rule.content, enabled: rule.enabled,
    };
    const providerWrite = {
      owner_username: provider.owner_username, external_source_id: provider.external_source_id,
      name: provider.name, type: provider.type, interval: provider.interval,
      proxy: provider.proxy, size_limit: provider.size_limit,
      health_check_enabled: provider.health_check_enabled,
      health_check_url: provider.health_check_url,
      health_check_interval: provider.health_check_interval,
      health_check_timeout: provider.health_check_timeout,
      health_check_lazy: provider.health_check_lazy,
      health_check_expected_status: provider.health_check_expected_status,
      filter: provider.filter, exclude_filter: provider.exclude_filter,
      exclude_type: provider.exclude_type, override: provider.override,
      process_mode: provider.process_mode, enabled: provider.enabled,
    };
    await api.listCustomRules(); await api.createCustomRule(ruleWrite);
    await api.updateCustomRule(rule, ruleWrite); await api.deleteCustomRule(rule);
    await api.listProxyProviders(); await api.createProxyProvider(providerWrite);
    await api.updateProxyProvider(provider, providerWrite); await api.deleteProxyProvider(provider);
    expect(fetcher.mock.calls.map(call => String(call[0]))).toEqual([
      "/api/v1/account/subscription-customizations/rules",
      "/api/v1/account/subscription-customizations/rules",
      "/api/v1/account/subscription-customizations/rules/rule%2Fid",
      "/api/v1/account/subscription-customizations/rules/rule%2Fid/delete",
      "/api/v1/account/subscription-customizations/providers",
      "/api/v1/account/subscription-customizations/providers",
      "/api/v1/account/subscription-customizations/providers/provider%2Fid",
      "/api/v1/account/subscription-customizations/providers/provider%2Fid/delete",
    ]);
    for (const index of [1, 2, 5, 6]) {
      expect(JSON.parse(String(fetcher.mock.calls[index][1]?.body))).not.toHaveProperty("owner_username");
      expect(new Headers(fetcher.mock.calls[index][1]?.headers).get("X-CSRF-Token")).toBe("account-csrf");
    }
    subscriberState.session = { ...subscriberState.session, username: "bob" };
    await expect(api.listCustomRules()).rejects.toThrow(/会话已经变化/);
    subscriberState.session = null;
  });
});
