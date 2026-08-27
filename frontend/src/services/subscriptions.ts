import type {
  ManagedNodeCreateRequest,
  ManagedNodeResponse,
  ManagedNodesResponse,
  ProductUserCredentialsResponse,
  ProductUserCreateRequest,
  ProductUserResponse,
  ProductUserSubscriptionTokenResponse,
  ProductUserTrafficResponse,
  ProductUsersResponse,
  SubscriptionCatalogExportResponse,
  SubscriptionCatalogImportRequest,
  SubscriptionCatalogImportResponse,
  SubscriptionDueTrafficResetRequest,
  SubscriptionDueTrafficResetResponse,
  SubscriptionPlanAssignRequest,
  SubscriptionPlanAssignResponse,
  SubscriptionPlanCreateRequest,
  SubscriptionPlanResponse,
  SubscriptionPlansResponse,
  SubscriptionQuotaStatusResponse,
  SubscriptionTemplatePresetApplyRequest,
  SubscriptionTemplatePresetsResponse,
  XrayRuntimeNodeCreateRequest,
  XrayRuntimeNodeDraftsResponse,
  XrayRuntimeNodeImportRequest,
  XrayRuntimeNodeImportResponse,
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

export async function getProductUserSubscriptionToken(
  username: string,
  fetcher = fetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/subscription-token`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user subscription token request failed");
  }
  return response.json() as Promise<ProductUserSubscriptionTokenResponse>;
}

export async function createProductUserSubscriptionToken(
  username: string,
  fetcher = fetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/subscription-token`,
    {
      method: "POST",
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Product user subscription token create request failed");
  }
  return response.json() as Promise<ProductUserSubscriptionTokenResponse>;
}

export async function resetProductUserSubscriptionToken(
  username: string,
  fetcher = fetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/subscription-token/reset`,
    {
      method: "POST",
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Product user subscription token reset request failed");
  }
  return response.json() as Promise<ProductUserSubscriptionTokenResponse>;
}

export async function listProductUserCredentials(
  username: string,
  fetcher = fetch,
): Promise<ProductUserCredentialsResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/credentials`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user credentials request failed");
  }
  return response.json() as Promise<ProductUserCredentialsResponse>;
}

export async function getProductUserTraffic(
  username: string,
  fetcher = fetch,
): Promise<ProductUserTrafficResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/traffic`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user traffic request failed");
  }
  return response.json() as Promise<ProductUserTrafficResponse>;
}

export async function getProductUserQuota(
  username: string,
  now?: string | null,
  fetcher = fetch,
): Promise<SubscriptionQuotaStatusResponse> {
  const query = now ? `?now=${encodeURIComponent(now)}` : "";
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/quota${query}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user quota request failed");
  }
  return response.json() as Promise<SubscriptionQuotaStatusResponse>;
}

export async function resetProductUserTraffic(
  username: string,
  now?: string | null,
  fetcher = fetch,
): Promise<SubscriptionQuotaStatusResponse> {
  const query = now ? `?now=${encodeURIComponent(now)}` : "";
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/users/${encodeURIComponent(username)}/traffic/reset${query}`,
    {
      method: "POST",
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Product user traffic reset request failed");
  }
  return response.json() as Promise<SubscriptionQuotaStatusResponse>;
}

export async function resetDueProductUserTraffic(
  payload: SubscriptionDueTrafficResetRequest = {},
  fetcher = fetch,
): Promise<SubscriptionDueTrafficResetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/traffic/reset-due`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Due traffic reset request failed");
  }
  return response.json() as Promise<SubscriptionDueTrafficResetResponse>;
}

export async function listSubscriptionTemplatePresets(
  fetcher = fetch,
): Promise<SubscriptionTemplatePresetsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/node-presets`);
  if (!response.ok) {
    throw await apiError(response, "Subscription template preset request failed");
  }
  return response.json() as Promise<SubscriptionTemplatePresetsResponse>;
}

export async function createManagedNodeFromPreset(
  presetId: string,
  payload: SubscriptionTemplatePresetApplyRequest,
  fetcher = fetch,
): Promise<ManagedNodeResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/node-presets/${encodeURIComponent(presetId)}/nodes`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Subscription template preset apply request failed");
  }
  return response.json() as Promise<ManagedNodeResponse>;
}

export async function exportSubscriptionCatalog(
  includeCredentials = false,
  fetcher = fetch,
): Promise<SubscriptionCatalogExportResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/catalog/export?include_credentials=${includeCredentials}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Subscription catalog export request failed");
  }
  return response.json() as Promise<SubscriptionCatalogExportResponse>;
}

export async function importSubscriptionCatalog(
  payload: SubscriptionCatalogImportRequest,
  fetcher = fetch,
): Promise<SubscriptionCatalogImportResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/catalog/import`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Subscription catalog import request failed");
  }
  return response.json() as Promise<SubscriptionCatalogImportResponse>;
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

export async function listXrayRuntimeNodeDrafts(
  serverId: string,
  fetcher = fetch,
): Promise<XrayRuntimeNodeDraftsResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/node-drafts`,
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime node drafts request failed");
  }
  return response.json() as Promise<XrayRuntimeNodeDraftsResponse>;
}

export async function createManagedNodeFromRuntimeInbound(
  serverId: string,
  payload: XrayRuntimeNodeCreateRequest,
  fetcher = fetch,
): Promise<ManagedNodeResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/nodes`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Xray runtime node create request failed");
  }
  return response.json() as Promise<ManagedNodeResponse>;
}

export async function importManagedNodesFromRuntimeInbounds(
  serverId: string,
  payload: XrayRuntimeNodeImportRequest = {},
  fetcher = fetch,
): Promise<XrayRuntimeNodeImportResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/nodes/import`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime nodes import request failed");
  }
  return response.json() as Promise<XrayRuntimeNodeImportResponse>;
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
