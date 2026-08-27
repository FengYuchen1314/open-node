import type {
  ServerCreateRequest,
  ServerCreateResponse,
  ServerSummary,
} from "../domain/inventory";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

const jsonHeaders = {
  "Content-Type": "application/json",
};

export async function listServers(fetcher = fetch): Promise<ServerSummary[]> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers`);
  if (!response.ok) {
    throw await apiError(response, "Server list request failed");
  }
  return response.json() as Promise<ServerSummary[]>;
}

export async function createServer(
  payload: ServerCreateRequest,
  fetcher = fetch,
): Promise<ServerCreateResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/servers`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Server create request failed");
  }
  return response.json() as Promise<ServerCreateResponse>;
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
