import type { ProbePayload, ProbeSeriesResponse } from "../domain/probe";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ProbeSeriesOptions {
  range?: "1h" | "6h" | "24h";
  metric?: "ping" | "system";
  target?: string;
  all?: boolean;
}

export async function getPublicProbePayload(fetcher = fetch): Promise<ProbePayload> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/public/probe-servers`);
  if (!response.ok) {
    throw await apiError(response, "Public probe request failed");
  }
  return response.json() as Promise<ProbePayload>;
}

export async function getPublicProbeSeries(
  serverIndex: number,
  options: ProbeSeriesOptions = {},
  fetcher = fetch,
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

  const response = await fetcher(`${apiBaseUrl}/api/v1/public/probe-series?${params}`);
  if (!response.ok) {
    throw await apiError(response, "Public probe series request failed");
  }
  return response.json() as Promise<ProbeSeriesResponse>;
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
