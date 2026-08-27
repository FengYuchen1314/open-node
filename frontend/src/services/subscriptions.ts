import type {
  ManagedNodeCreateRequest,
  ManagedNodeResponse,
  ManagedNodesResponse,
  ProductUserCreateRequest,
  ProductUserResponse,
  ProductUsersResponse,
  SubscriptionPlanAssignRequest,
  SubscriptionPlanAssignResponse,
  SubscriptionPlanCreateRequest,
  SubscriptionPlanResponse,
  SubscriptionPlansResponse,
} from "../domain/subscriptions";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

const jsonHeaders = {
  "Content-Type": "application/json",
};

export async function listProductUsers(fetcher = fetch): Promise<ProductUsersResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/users`);
  if (!response.ok) {
    throw await apiError(response, "Product users request failed");
  }
  return response.json() as Promise<ProductUsersResponse>;
}

export async function createProductUser(
  payload: ProductUserCreateRequest,
  fetcher = fetch,
): Promise<ProductUserResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/users`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Product user create request failed");
  }
  return response.json() as Promise<ProductUserResponse>;
}

export async function listManagedNodes(fetcher = fetch): Promise<ManagedNodesResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/nodes`);
  if (!response.ok) {
    throw await apiError(response, "Managed nodes request failed");
  }
  return response.json() as Promise<ManagedNodesResponse>;
}

export async function createManagedNode(
  payload: ManagedNodeCreateRequest,
  fetcher = fetch,
): Promise<ManagedNodeResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/nodes`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Managed node create request failed");
  }
  return response.json() as Promise<ManagedNodeResponse>;
}

export async function listSubscriptionPlans(
  fetcher = fetch,
): Promise<SubscriptionPlansResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/plans`);
  if (!response.ok) {
    throw await apiError(response, "Subscription plans request failed");
  }
  return response.json() as Promise<SubscriptionPlansResponse>;
}

export async function createSubscriptionPlan(
  payload: SubscriptionPlanCreateRequest,
  fetcher = fetch,
): Promise<SubscriptionPlanResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/plans`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Subscription plan create request failed");
  }
  return response.json() as Promise<SubscriptionPlanResponse>;
}

export async function assignSubscriptionPlan(
  username: string,
  payload: SubscriptionPlanAssignRequest,
  fetcher = fetch,
): Promise<SubscriptionPlanAssignResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/plan`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Subscription plan assignment request failed");
  }
  return response.json() as Promise<SubscriptionPlanAssignResponse>;
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.length > 0) {
      return new Error(body.detail);
    }
  } catch {
    // The backend normally returns JSON errors, but network proxies may not.
  }
  return new Error(`${fallback} with ${response.status}`);
}
