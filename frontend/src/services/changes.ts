import { authenticatedFetch } from "./auth";
import type {
  AgentChangeSetCreateRequest,
  AgentChangeSetResponse,
  AgentChangeSetsResponse,
  AgentChangeSetRollbackRequest,
  AgentRoutedOutboundChangeSetCreateRequest,
} from "../domain/changes";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

const jsonHeaders = {
  "Content-Type": "application/json",
};

export async function listChangeSets(fetcher = authenticatedFetch): Promise<AgentChangeSetsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets`);
  if (!response.ok) {
    throw await apiError(response, "Change set list request failed");
  }
  return response.json() as Promise<AgentChangeSetsResponse>;
}

export async function getChangeSet(
  changeSetId: string,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets/${changeSetId}`);
  if (!response.ok) {
    throw await apiError(response, "Change set request failed");
  }
  return response.json() as Promise<AgentChangeSetResponse>;
}

export async function createChangeSet(
  payload: AgentChangeSetCreateRequest,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Change set create request failed");
  }
  return response.json() as Promise<AgentChangeSetResponse>;
}

export async function createRoutedOutboundChangeSet(
  payload: AgentRoutedOutboundChangeSetCreateRequest,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets/routed-outbound`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Routed outbound change set create request failed");
  }
  return response.json() as Promise<AgentChangeSetResponse>;
}

export async function dispatchChangeSet(
  changeSetId: string,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets/${changeSetId}/dispatch`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await apiError(response, "Change set dispatch request failed");
  }
  return response.json() as Promise<AgentChangeSetResponse>;
}

export async function rollbackChangeSet(
  changeSetId: string,
  payload: AgentChangeSetRollbackRequest,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets/${changeSetId}/rollback`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Change set rollback request failed");
  }
  return response.json() as Promise<AgentChangeSetResponse>;
}

export async function acceptChangeSet(
  changeSetId: string,
  reason: string,
  fetcher = authenticatedFetch,
): Promise<AgentChangeSetResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/change-sets/${changeSetId}/accept`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ acknowledge: true, reason }),
  });
  if (!response.ok) throw await apiError(response, "Change set acceptance failed");
  return response.json() as Promise<AgentChangeSetResponse>;
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
