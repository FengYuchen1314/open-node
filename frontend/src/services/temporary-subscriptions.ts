import type {
  TemporarySubscription,
  TemporarySubscriptionCreate,
  TemporarySubscriptionsResponse,
} from "../domain/temporary-subscriptions";
import { authenticatedFetch } from "./auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const basePath = `${apiBaseUrl}/api/v1/temporary-subscriptions`;

async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`);
}

export async function listTemporarySubscriptions(fetcher = authenticatedFetch) {
  const response = await fetcher(basePath);
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<TemporarySubscriptionsResponse>;
}

export async function createTemporarySubscription(
  payload: TemporarySubscriptionCreate,
  fetcher = authenticatedFetch,
) {
  const response = await fetcher(basePath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<TemporarySubscription>;
}

export async function deleteTemporarySubscription(identifier: string, fetcher = authenticatedFetch) {
  const response = await fetcher(`${basePath}/${encodeURIComponent(identifier)}`, { method: "DELETE" });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<{ id: string; deleted: true; license_required: false }>;
}
