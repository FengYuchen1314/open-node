import type {
  CustomRule,
  CustomRulesResponse,
  CustomRuleWrite,
  ProxyProvider,
  ProxyProvidersResponse,
  ProxyProviderWrite,
} from "../domain/subscription-customizations";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";
import { accountRequest, subscriberState } from "./subscriber-auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const adminBase = `${apiBaseUrl}/api/v1/subscription-customizations`;

async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return requestError(
    typeof body?.detail === "string" ? body.detail : undefined,
    `订阅自定义请求失败（${response.status}）`,
  );
}

async function request<T>(path: string, init: RequestInit = {}, fetcher = authenticatedFetch) {
  const response = await fetcher(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function listCustomRules(fetcher = authenticatedFetch) {
  return request<CustomRulesResponse>(`${adminBase}/rules`, {}, fetcher);
}

export function createCustomRule(value: CustomRuleWrite, fetcher = authenticatedFetch) {
  return request<CustomRule>(
    `${adminBase}/rules`,
    { method: "POST", body: JSON.stringify(value) },
    fetcher,
  );
}

export function updateCustomRule(
  value: CustomRule,
  changes: CustomRuleWrite,
  fetcher = authenticatedFetch,
) {
  const { owner_username: _owner, ...payload } = changes;
  return request<CustomRule>(
    `${adminBase}/rules/${encodeURIComponent(value.id)}`,
    { method: "PUT", body: JSON.stringify({ ...payload, expected_revision: value.revision }) },
    fetcher,
  );
}

export function deleteCustomRule(value: CustomRule, fetcher = authenticatedFetch) {
  return request<void>(
    `${adminBase}/rules/${encodeURIComponent(value.id)}/delete`,
    { method: "POST", body: JSON.stringify({ expected_revision: value.revision }) },
    fetcher,
  );
}

export function listProxyProviders(fetcher = authenticatedFetch) {
  return request<ProxyProvidersResponse>(`${adminBase}/providers`, {}, fetcher);
}

export function createProxyProvider(value: ProxyProviderWrite, fetcher = authenticatedFetch) {
  return request<ProxyProvider>(
    `${adminBase}/providers`,
    { method: "POST", body: JSON.stringify(value) },
    fetcher,
  );
}

export function updateProxyProvider(
  value: ProxyProvider,
  changes: ProxyProviderWrite,
  fetcher = authenticatedFetch,
) {
  const { owner_username: _owner, ...payload } = changes;
  return request<ProxyProvider>(
    `${adminBase}/providers/${encodeURIComponent(value.id)}`,
    { method: "PUT", body: JSON.stringify({ ...payload, expected_revision: value.revision }) },
    fetcher,
  );
}

export function deleteProxyProvider(value: ProxyProvider, fetcher = authenticatedFetch) {
  return request<void>(
    `${adminBase}/providers/${encodeURIComponent(value.id)}/delete`,
    { method: "POST", body: JSON.stringify({ expected_revision: value.revision }) },
    fetcher,
  );
}

/** Subscriber-cookie client. Ownership is derived by the server, never trusted from a form. */
export function accountSubscriptionCustomizations(username: string, fetcher = fetch) {
  const sessionToken = subscriberState.session?.csrf_token;
  const current = () => subscriberState.session?.authenticated
    && subscriberState.session.username === username
    && subscriberState.session.csrf_token === sessionToken;
  const ensureCurrent = () => {
    if (!current()) throw requestError(undefined, "用户会话已经变化，请返回用户中心重新读取。");
  };
  const owned = <T extends CustomRule | ProxyProvider>(value: T) => {
    ensureCurrent();
    if (value.owner_username !== username) {
      throw requestError(undefined, "服务器返回了不属于当前用户的订阅资源。");
    }
    return value;
  };
  return {
    listCustomRules: async () => {
      ensureCurrent();
      const result = await accountRequest<CustomRulesResponse>(
        "subscription-customizations/rules", {}, fetcher,
      );
      result.rules.forEach(owned);
      return result;
    },
    createCustomRule: async (value: CustomRuleWrite) => {
      ensureCurrent();
      const { owner_username: _owner, ...payload } = value;
      return owned(await accountRequest<CustomRule>(
        "subscription-customizations/rules",
        { method: "POST", body: JSON.stringify(payload) },
        fetcher,
      ));
    },
    updateCustomRule: async (value: CustomRule, changes: CustomRuleWrite) => {
      owned(value);
      const { owner_username: _owner, ...payload } = changes;
      return owned(await accountRequest<CustomRule>(
        `subscription-customizations/rules/${encodeURIComponent(value.id)}`,
        { method: "PUT", body: JSON.stringify({ ...payload, expected_revision: value.revision }) },
        fetcher,
      ));
    },
    deleteCustomRule: async (value: CustomRule) => {
      owned(value);
      return accountRequest<void>(
        `subscription-customizations/rules/${encodeURIComponent(value.id)}/delete`,
        { method: "POST", body: JSON.stringify({ expected_revision: value.revision }) },
        fetcher,
      );
    },
    listProxyProviders: async () => {
      ensureCurrent();
      const result = await accountRequest<ProxyProvidersResponse>(
        "subscription-customizations/providers", {}, fetcher,
      );
      result.providers.forEach(owned);
      return result;
    },
    createProxyProvider: async (value: ProxyProviderWrite) => {
      ensureCurrent();
      const { owner_username: _owner, ...payload } = value;
      return owned(await accountRequest<ProxyProvider>(
        "subscription-customizations/providers",
        { method: "POST", body: JSON.stringify(payload) },
        fetcher,
      ));
    },
    updateProxyProvider: async (value: ProxyProvider, changes: ProxyProviderWrite) => {
      owned(value);
      const { owner_username: _owner, ...payload } = changes;
      return owned(await accountRequest<ProxyProvider>(
        `subscription-customizations/providers/${encodeURIComponent(value.id)}`,
        { method: "PUT", body: JSON.stringify({ ...payload, expected_revision: value.revision }) },
        fetcher,
      ));
    },
    deleteProxyProvider: async (value: ProxyProvider) => {
      owned(value);
      return accountRequest<void>(
        `subscription-customizations/providers/${encodeURIComponent(value.id)}/delete`,
        { method: "POST", body: JSON.stringify({ expected_revision: value.revision }) },
        fetcher,
      );
    },
  };
}
