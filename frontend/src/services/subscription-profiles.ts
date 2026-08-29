import type {
  SubscriptionProfile,
  SubscriptionProfilesResponse,
  SubscriptionProfileUpdate,
} from "../domain/subscription-profiles";
import { authenticatedFetch } from "./auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const basePath = `${apiBaseUrl}/api/v1/subscription-profiles`;

async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`);
}

export async function listSubscriptionProfiles(fetcher = authenticatedFetch) {
  const response = await fetcher(basePath);
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<SubscriptionProfilesResponse>;
}

export async function updateSubscriptionProfile(
  identifier: string,
  payload: SubscriptionProfileUpdate,
  fetcher = authenticatedFetch,
) {
  const response = await fetcher(`${basePath}/${encodeURIComponent(identifier)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<SubscriptionProfile>;
}
