import { authenticatedFetch } from "./auth";
import { userPath } from "./user-path";
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
  SubscriptionAccessResponse,
  SubscriptionCatalogImportRequest,
  SubscriptionCatalogImportResponse,
  SubscriptionClientFormat,
  SubscriptionFormatPreview,
  SubscriptionIpPolicy,
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
  XrayRuntimeCredentialCleanupRequest,
  XrayRuntimeCredentialCleanupResponse,
  XrayRuntimeCredentialReconciliationResponse,
  XrayRuntimeCredentialRepairRequest,
  XrayRuntimeCredentialRepairResponse,
  XrayRuntimeNodeCreateRequest,
  XrayRuntimeNodeDraftsResponse,
  XrayRuntimeNodeImportRequest,
  XrayRuntimeNodeImportResponse,
  XrayRuntimeNodeReconciliationResponse,
  XrayRuntimeNodeSyncRequest,
  XrayRuntimeNodeSyncResponse,
} from "../domain/subscriptions";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

const jsonHeaders = {
  "Content-Type": "application/json",
};

export async function getProductUserIpPolicy(username: string, fetcher = authenticatedFetch): Promise<SubscriptionIpPolicy> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "subscription-ip-policy")}`);
  if (!response.ok) throw await apiError(response, "Subscription IP policy request failed");
  return response.json() as Promise<SubscriptionIpPolicy>;
}

export async function updateProductUserIpPolicy(username: string, networks: string[], fetcher = authenticatedFetch): Promise<SubscriptionIpPolicy> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "subscription-ip-policy")}`, {
    method: "PUT", headers: jsonHeaders, body: JSON.stringify({ networks }),
  });
  if (!response.ok) throw await apiError(response, "Subscription IP policy update failed");
  return response.json() as Promise<SubscriptionIpPolicy>;
}

export async function updateProductUserShortCode(username: string, code: string, revision: string, fetcher = authenticatedFetch): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "subscription-short-code")}`, {
    method: "PUT", headers: jsonHeaders, body: JSON.stringify({ custom_short_code: code, expected_revision: revision }),
  });
  if (!response.ok) throw await apiError(response, "Short code update failed");
  return response.json() as Promise<ProductUserSubscriptionTokenResponse>;
}

export async function getSubscriptionAccess(
  username: string,
  fetcher = authenticatedFetch,
): Promise<SubscriptionAccessResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "access")}`);
  if (!response.ok) throw await apiError(response, "Subscription access request failed");
  return response.json() as Promise<SubscriptionAccessResponse>;
}

export async function syncSubscriptionAccess(
  username: string,
  fetcher = authenticatedFetch,
): Promise<SubscriptionAccessResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "access/sync")}`, { method: "POST" });
  if (!response.ok) throw await apiError(response, "Subscription access sync failed");
  return response.json() as Promise<SubscriptionAccessResponse>;
}

export async function setProductUserActive(
  username: string,
  isActive: boolean,
  fetcher = authenticatedFetch,
): Promise<ProductUserResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "active")}`, {
    method: "PATCH", headers: jsonHeaders, body: JSON.stringify({ is_active: isActive }),
  });
  if (!response.ok) throw await apiError(response, "User status update failed");
  return response.json() as Promise<ProductUserResponse>;
}

export async function getSubscriptionFormatPreview(
  username: string,
  format: SubscriptionClientFormat,
  fetcher = authenticatedFetch,
): Promise<SubscriptionFormatPreview> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "subscription-preview", { format })}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Subscription compatibility request failed");
  }
  return response.json() as Promise<SubscriptionFormatPreview>;
}

export async function listProductUsers(fetcher = authenticatedFetch): Promise<ProductUsersResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/users`);
  if (!response.ok) {
    throw await apiError(response, "Product users request failed");
  }
  return response.json() as Promise<ProductUsersResponse>;
}

export async function createProductUser(
  payload: ProductUserCreateRequest,
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "subscription-token")}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user subscription token request failed");
  }
  return response.json() as Promise<ProductUserSubscriptionTokenResponse>;
}

export async function createProductUserSubscriptionToken(
  username: string,
  fetcher = authenticatedFetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "subscription-token")}`,
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
  fetcher = authenticatedFetch,
): Promise<ProductUserSubscriptionTokenResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "subscription-token/reset")}`,
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
  fetcher = authenticatedFetch,
): Promise<ProductUserCredentialsResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "credentials")}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user credentials request failed");
  }
  return response.json() as Promise<ProductUserCredentialsResponse>;
}

export async function getProductUserTraffic(
  username: string,
  fetcher = authenticatedFetch,
): Promise<ProductUserTrafficResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "traffic")}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user traffic request failed");
  }
  return response.json() as Promise<ProductUserTrafficResponse>;
}

export async function getProductUserQuota(
  username: string,
  now?: string | null,
  fetcher = authenticatedFetch,
): Promise<SubscriptionQuotaStatusResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "quota", now ? { now } : undefined)}`,
  );
  if (!response.ok) {
    throw await apiError(response, "Product user quota request failed");
  }
  return response.json() as Promise<SubscriptionQuotaStatusResponse>;
}

export async function resetProductUserTraffic(
  username: string,
  now?: string | null,
  fetcher = authenticatedFetch,
): Promise<SubscriptionQuotaStatusResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1${userPath(username, "traffic/reset", now ? { now } : undefined)}`,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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

export async function listManagedNodes(fetcher = authenticatedFetch): Promise<ManagedNodesResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/nodes`);
  if (!response.ok) {
    throw await apiError(response, "Managed nodes request failed");
  }
  return response.json() as Promise<ManagedNodesResponse>;
}

export async function createManagedNode(
  payload: ManagedNodeCreateRequest,
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
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

export async function getXrayRuntimeNodeReconciliation(
  serverId: string,
  fetcher = authenticatedFetch,
): Promise<XrayRuntimeNodeReconciliationResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/nodes/reconciliation`,
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime node reconciliation request failed");
  }
  return response.json() as Promise<XrayRuntimeNodeReconciliationResponse>;
}

export async function syncManagedNodeFromRuntime(
  serverId: string,
  nodeId: string,
  payload: XrayRuntimeNodeSyncRequest = {},
  fetcher = authenticatedFetch,
): Promise<XrayRuntimeNodeSyncResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/nodes/${nodeId}/sync`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime node sync request failed");
  }
  return response.json() as Promise<XrayRuntimeNodeSyncResponse>;
}

export async function getXrayRuntimeCredentialReconciliation(
  serverId: string,
  fetcher = authenticatedFetch,
): Promise<XrayRuntimeCredentialReconciliationResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/credentials/reconciliation`,
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime credential reconciliation request failed");
  }
  return response.json() as Promise<XrayRuntimeCredentialReconciliationResponse>;
}

export async function repairMissingXrayRuntimeCredentials(
  serverId: string,
  payload: XrayRuntimeCredentialRepairRequest = {},
  fetcher = authenticatedFetch,
): Promise<XrayRuntimeCredentialRepairResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/credentials/repair-missing`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime credential repair request failed");
  }
  return response.json() as Promise<XrayRuntimeCredentialRepairResponse>;
}

export async function cleanupExtraXrayRuntimeCredentials(
  serverId: string,
  payload: XrayRuntimeCredentialCleanupRequest = {},
  fetcher = authenticatedFetch,
): Promise<XrayRuntimeCredentialCleanupResponse> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/servers/${serverId}/xray/runtime/credentials/cleanup-extra`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw await apiError(response, "Xray runtime credential cleanup request failed");
  }
  return response.json() as Promise<XrayRuntimeCredentialCleanupResponse>;
}

export async function listSubscriptionPlans(
  fetcher = authenticatedFetch,
): Promise<SubscriptionPlansResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/plans`);
  if (!response.ok) {
    throw await apiError(response, "Subscription plans request failed");
  }
  return response.json() as Promise<SubscriptionPlansResponse>;
}

export async function createSubscriptionPlan(
  payload: SubscriptionPlanCreateRequest,
  fetcher = authenticatedFetch,
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
  fetcher = authenticatedFetch,
): Promise<SubscriptionPlanAssignResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1${userPath(username, "plan")}`, {
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
