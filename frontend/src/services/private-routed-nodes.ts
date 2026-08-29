import type {
  PrivateRoutedNodeCreate,
  PrivateRoutedNodeMutationResponse,
  PrivateRoutedNodesResponse,
  PrivateRoutedPolicy,
} from "../domain/private-routed-nodes";
import { accountRequest } from "./subscriber-auth";
import { authenticatedFetch } from "./auth";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const adminPath = `${apiBaseUrl}/api/v1/private-routed-nodes`;

async function failure(response: Response) {
  const body = await response.json().catch(() => null);
  return new Error(
    typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`,
  );
}

export const listSubscriberPrivateRoutes = (fetcher = fetch) =>
  accountRequest<PrivateRoutedNodesResponse>("private-routed-nodes", {}, fetcher);

export const createSubscriberPrivateRoute = (
  payload: PrivateRoutedNodeCreate,
  fetcher = fetch,
) => accountRequest<PrivateRoutedNodeMutationResponse>(
  "private-routed-nodes",
  { method: "POST", body: JSON.stringify(payload) },
  fetcher,
);

export const deleteSubscriberPrivateRoute = (identifier: string, fetcher = fetch) =>
  accountRequest<PrivateRoutedNodeMutationResponse>(
    `private-routed-nodes/${encodeURIComponent(identifier)}`,
    { method: "DELETE" },
    fetcher,
  );

export async function listPrivateRoutes(fetcher = authenticatedFetch) {
  const response = await fetcher(adminPath);
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<PrivateRoutedNodesResponse>;
}

export async function updatePrivateRoutePolicy(
  policy: Pick<PrivateRoutedPolicy, "enabled" | "max_nodes" | "daily_limit">,
  fetcher = authenticatedFetch,
) {
  const response = await fetcher(`${adminPath}/policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!response.ok) throw await failure(response);
  return response.json() as Promise<PrivateRoutedPolicy>;
}
