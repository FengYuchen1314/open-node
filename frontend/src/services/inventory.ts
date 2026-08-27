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
    throw new Error(`Server list request failed with ${response.status}`);
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
    throw new Error(`Server create request failed with ${response.status}`);
  }
  return response.json() as Promise<ServerCreateResponse>;
}
