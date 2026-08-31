import type { ProbePayload, ProbeSeriesResponse, ProbeTargetComparisonResponse } from "../domain/probe";
import { requestError } from "./request-error";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
export type ProbeRange = "1h" | "6h" | "24h";
export interface PublicProbeSeriesOptions { range?: ProbeRange; metric?: "ping" | "system"; target?: string; all?: boolean }

/** Public data-plane requests never inherit administrator or subscriber cookies. */
async function readPublic<T>(path: string, fetcher: typeof fetch, accessToken?: string): Promise<T> {
  const token = accessToken?.trim();
  const response = await fetcher(`${apiBaseUrl}/api/v1/public/${path}`, {
    credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer", redirect: "error",
    ...(token ? { headers: { "X-MMwx-Probe-Token": token } } : {}),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw requestError(typeof body?.detail === "string" && body.detail ? body.detail : undefined,
      `公开探针请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}
export const getPublicProbePayload = (fetcher = fetch, accessToken?: string) => readPublic<ProbePayload>("probe-servers", fetcher, accessToken);
export const getPublicProbeTargets = (range: ProbeRange = "1h", fetcher = fetch, accessToken?: string) => readPublic<ProbeTargetComparisonResponse>(`probe-targets?${new URLSearchParams({ range })}`, fetcher, accessToken);
export function getPublicProbeSeries(serverIndex: number, options: PublicProbeSeriesOptions = {}, fetcher = fetch, accessToken?: string) {
  const query = new URLSearchParams({ server: String(serverIndex), range: options.range ?? "1h", metric: options.metric ?? "ping" });
  if (options.target) query.set("target", options.target);
  if (options.all) query.set("all", "1");
  return readPublic<ProbeSeriesResponse>(`probe-series?${query}`, fetcher, accessToken);
}
export function getPublicProbeStreamUrl(locationLike: Pick<Location, "origin"> = window.location) {
  const url = new URL("/api/v1/public/probe-ws", apiBaseUrl || locationLike.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
