import type {
  ProbeAccessTokenCreateResponse,
  ProbePayload,
  ProbeSeriesResponse,
  ProbeSettingsResponse,
  ProbeSettingsUpdate,
  ProbeTargetComparisonResponse,
  ProbeTaskCreateRequest,
  ProbeTaskDispatchResponse,
  ProbeTaskListResponse,
  ProbeTaskResponse,
  ProbeTaskUpdateRequest,
} from "../domain/probe";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

interface BrowserLocationLike {
  origin: string;
  protocol: string;
}

export interface ProbeSeriesOptions {
  range?: "1h" | "6h" | "24h";
  metric?: "ping" | "system";
  target?: string;
  all?: boolean;
}

export type ProbeRange = "1h" | "6h" | "24h";

export async function getPublicProbePayload(
  fetcher = fetch,
  accessToken?: string,
): Promise<ProbePayload> {
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/public/probe-servers`,
    probeAccessInit(accessToken),
  );
  if (!response.ok) {
    throw await apiError(response, "Public probe request failed");
  }
  return response.json() as Promise<ProbePayload>;
}

export async function getPublicProbeSettings(fetcher = fetch): Promise<ProbeSettingsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/public/probe-settings`);
  if (!response.ok) {
    throw await apiError(response, "Public probe settings request failed");
  }
  return response.json() as Promise<ProbeSettingsResponse>;
}

export async function updatePublicProbeSettings(
  payload: ProbeSettingsUpdate,
  fetcher = fetch,
): Promise<ProbeSettingsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/public/probe-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Public probe settings update request failed");
  }
  return response.json() as Promise<ProbeSettingsResponse>;
}

export async function getPublicProbeSeries(
  serverIndex: number,
  options: ProbeSeriesOptions = {},
  fetcher = fetch,
  accessToken?: string,
): Promise<ProbeSeriesResponse> {
  const params = new URLSearchParams({
    server: serverIndex.toString(),
    range: options.range ?? "1h",
    metric: options.metric ?? "ping",
  });
  if (options.target) {
    params.set("target", options.target);
  }
  if (options.all) {
    params.set("all", "1");
  }

  const response = await fetcher(
    `${apiBaseUrl}/api/v1/public/probe-series?${params}`,
    probeAccessInit(accessToken),
  );
  if (!response.ok) {
    throw await apiError(response, "Public probe series request failed");
  }
  return response.json() as Promise<ProbeSeriesResponse>;
}

export async function getPublicProbeTargets(
  range: ProbeRange = "1h",
  fetcher = fetch,
  accessToken?: string,
): Promise<ProbeTargetComparisonResponse> {
  const params = new URLSearchParams({ range });
  const response = await fetcher(
    `${apiBaseUrl}/api/v1/public/probe-targets?${params}`,
    probeAccessInit(accessToken),
  );
  if (!response.ok) {
    throw await apiError(response, "Public probe targets request failed");
  }
  return response.json() as Promise<ProbeTargetComparisonResponse>;
}

export async function listProbeTasks(fetcher = fetch): Promise<ProbeTaskListResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/tasks`);
  if (!response.ok) {
    throw await apiError(response, "Probe task list request failed");
  }
  return response.json() as Promise<ProbeTaskListResponse>;
}

export async function createProbeTask(
  payload: ProbeTaskCreateRequest,
  fetcher = fetch,
): Promise<ProbeTaskResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Probe task create request failed");
  }
  return response.json() as Promise<ProbeTaskResponse>;
}

export async function updateProbeTask(
  taskId: string,
  payload: ProbeTaskUpdateRequest,
  fetcher = fetch,
): Promise<ProbeTaskResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await apiError(response, "Probe task update request failed");
  }
  return response.json() as Promise<ProbeTaskResponse>;
}

export async function dispatchDueProbeTasks(
  fetcher = fetch,
): Promise<ProbeTaskDispatchResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/tasks/dispatch-due`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await apiError(response, "Probe task dispatch request failed");
  }
  return response.json() as Promise<ProbeTaskDispatchResponse>;
}

export async function createProbeAccessToken(
  fetcher = fetch,
): Promise<ProbeAccessTokenCreateResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/access-token`, {
    method: "POST",
  });
  if (!response.ok) {
    throw await apiError(response, "Probe access token create request failed");
  }
  return response.json() as Promise<ProbeAccessTokenCreateResponse>;
}

export async function clearProbeAccessToken(
  fetcher = fetch,
): Promise<ProbeSettingsResponse> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/probe/access-token`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw await apiError(response, "Probe access token clear request failed");
  }
  return response.json() as Promise<ProbeSettingsResponse>;
}

export function getPublicProbeStreamUrl(locationLike: BrowserLocationLike = window.location) {
  const url = new URL("/api/v1/public/probe-ws", apiBaseUrl || locationLike.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function probeAccessInit(accessToken?: string): RequestInit | undefined {
  const token = accessToken?.trim();
  if (!token) {
    return undefined;
  }
  return { headers: { "X-MMwx-Probe-Token": token } };
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.length > 0) {
      return new Error(body.detail);
    }
  } catch {
    // Public probe aliases may return compact non-JSON errors through proxies.
  }
  return new Error(`${fallback} with ${response.status}`);
}
