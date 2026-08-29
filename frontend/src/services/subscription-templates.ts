import type {
  SubscriptionTemplate,
  SubscriptionTemplateFormat,
  SubscriptionTemplateList,
  SubscriptionTemplatePreview,
  SubscriptionTemplateSettings,
  SubscriptionTemplateWrite,
} from "../domain/subscription-templates";
import { authenticatedFetch } from "./auth";
import { accountRequest } from "./subscriber-auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

async function adminRequest<T>(path: string, init: RequestInit = {}, fetcher = authenticatedFetch) {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetcher(`${apiBaseUrl}/api/v1/subscription-templates${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((entry: { loc?: unknown[]; msg?: string }) => `${entry.loc?.slice(1).join(".") ?? ""}: ${entry.msg ?? "Invalid value"}`).join("; ")
        : "";
    throw new Error(message || `Template request failed (${response.status})`);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

function request<T>(path: string, init: RequestInit, subscriber: boolean, fetcher?: typeof fetch) {
  return subscriber
    ? accountRequest<T>(`subscription-templates${path}`, init, fetcher ?? fetch)
    : adminRequest<T>(path, init, fetcher ?? authenticatedFetch);
}

export const listSubscriptionTemplates = (subscriber = false, fetcher?: typeof fetch) =>
  request<SubscriptionTemplateList>("", {}, subscriber, fetcher);

export const getSubscriptionTemplate = (id: string, subscriber = false, fetcher?: typeof fetch) =>
  request<SubscriptionTemplate>(`/${encodeURIComponent(id)}`, {}, subscriber, fetcher);

export const getSubscriptionTemplateStarter = (
  format: SubscriptionTemplateFormat,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<{ format: SubscriptionTemplateFormat; content: string }>(
  `/starter?${new URLSearchParams({ format })}`,
  {},
  subscriber,
  fetcher,
);

export const createSubscriptionTemplate = (
  payload: SubscriptionTemplateWrite,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<SubscriptionTemplate>("", { method: "POST", body: JSON.stringify(payload) }, subscriber, fetcher);

export const updateSubscriptionTemplate = (
  id: string,
  payload: SubscriptionTemplateWrite,
  expectedRevision: string,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<SubscriptionTemplate>(`/${encodeURIComponent(id)}`, {
  method: "PUT",
  body: JSON.stringify({ ...payload, expected_revision: expectedRevision }),
}, subscriber, fetcher);

export const removeSubscriptionTemplate = (
  id: string,
  expectedRevision: string,
  confirmName: string,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<void>(`/${encodeURIComponent(id)}/remove`, {
  method: "POST",
  body: JSON.stringify({ expected_revision: expectedRevision, confirm_name: confirmName }),
}, subscriber, fetcher);

export const previewSubscriptionTemplate = (
  format: SubscriptionTemplateFormat,
  content: string,
  username: string | null,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<SubscriptionTemplatePreview>("/preview", {
  method: "POST",
  body: JSON.stringify({ format, content, username }),
}, subscriber, fetcher);

export const getSubscriptionTemplateSettings = (
  username: string | null,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<SubscriptionTemplateSettings>(
  `/settings${username ? `?${new URLSearchParams({ username })}` : ""}`,
  {},
  subscriber,
  fetcher,
);

export const updateSubscriptionTemplateSettings = (
  settings: SubscriptionTemplateSettings,
  username: string | null,
  subscriber = false,
  fetcher?: typeof fetch,
) => request<SubscriptionTemplateSettings>(
  `/settings${username ? `?${new URLSearchParams({ username })}` : ""}`,
  {
    method: "PUT",
    body: JSON.stringify({
      clash_template_id: settings.clash_template_id,
      surge_template_id: settings.surge_template_id,
      enabled: settings.enabled,
      expected_revision: settings.revision,
    }),
  },
  subscriber,
  fetcher,
);

export function subscriptionTemplateDownloadUrl(id: string, subscriber = false) {
  return `${apiBaseUrl}/api/v1/${subscriber ? "account/" : ""}subscription-templates/${encodeURIComponent(id)}/file`;
}
