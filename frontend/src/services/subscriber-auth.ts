import { reactive } from "vue";
import type { ProductUserSubscriptionToken, SubscriptionClientFormat, SubscriptionQuotaStatus } from "../domain/subscriptions";
import { authenticatedFetch } from "./auth";
import type { UserNodeLimits } from "../domain/user-limits";
import type { SubscriberSubscriptionProfilesResponse } from "../domain/subscription-profiles";

export interface SubscriberSession {
  authenticated: boolean;
  username: string | null;
  csrf_token: string | null;
  requires_2fa: boolean;
  challenge: string | null;
}
export interface SubscriberProfile {
  username: string;
  display_name: string;
  email: string | null;
  quota: SubscriptionQuotaStatus;
  speed_limit_mbps: number;
  device_limit: number;
  node_limits: UserNodeLimits[];
}
export interface SubscriberDevice {
  id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  peer: string;
  user_agent: string;
}
export interface SubscriberSecurity { totp_enabled: boolean; totp_available: boolean; recovery_codes_remaining: number }
export interface SubscriberEnrollment { secret: string; provisioning_uri: string; expires_at: string }
export interface SubscriberAccount { username: string; configured: boolean; totp_enabled: boolean; revision: string }
export interface SubscriberProof { password: string; code?: string }

export const subscriberState = reactive({ ready: false, error: "", session: null as SubscriberSession | null });
let epoch = 0;
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export function clearSubscriberSession() {
  ++epoch;
  subscriberState.session = null;
}

export async function accountRequest<T>(path: string, init: RequestInit = {}, fetcher = fetch): Promise<T> {
  const current = epoch;
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Open-Node-Client", "browser");
  if (!["GET", "HEAD"].includes((init.method ?? "GET").toUpperCase()) && subscriberState.session?.csrf_token) {
    headers.set("X-CSRF-Token", subscriberState.session.csrf_token);
  }
  const response = await fetcher(`${apiBaseUrl}/api/v1/account/${path}`, {
    ...init, headers, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401 && current === epoch && !path.startsWith("login")) clearSubscriberSession();
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export async function loadSubscriberSession(fetcher = fetch) {
  const current = ++epoch;
  subscriberState.error = "";
  try {
    const session = await accountRequest<SubscriberSession>("session", {}, fetcher);
    if (current === epoch) subscriberState.session = session;
  } catch (error) {
    if (current === epoch) {
      subscriberState.session = null;
      subscriberState.error = error instanceof Error ? error.message : "Connection failed";
    }
  } finally { subscriberState.ready = true; }
}

export async function subscriberSignIn(username: string, password: string, fetcher = fetch) {
  const current = ++epoch;
  const result = await accountRequest<SubscriberSession>("login", { method: "POST", body: JSON.stringify({ username, password }) }, fetcher);
  if (current === epoch && result.authenticated) subscriberState.session = result;
  return result;
}

export async function verifySubscriberLogin(challenge: string, code: string, fetcher = fetch) {
  const current = ++epoch;
  const result = await accountRequest<SubscriberSession>("login/verify", { method: "POST", body: JSON.stringify({ challenge, code }) }, fetcher);
  if (current === epoch && result.authenticated) subscriberState.session = result;
  return result;
}

export async function subscriberSignOut(fetcher = fetch) {
  await accountRequest<void>("logout", { method: "POST" }, fetcher);
  clearSubscriberSession();
}

export async function subscriberChangePassword(proof: SubscriberProof, newPassword: string, fetcher = fetch) {
  await accountRequest<void>("password", { method: "POST", body: JSON.stringify({ ...proof, new_password: newPassword }) }, fetcher);
  clearSubscriberSession();
}

export const subscriberProfile = () => accountRequest<SubscriberProfile>("me");
export const subscriberProfiles = (fetcher = fetch) => accountRequest<SubscriberSubscriptionProfilesResponse>("subscription-profiles", {}, fetcher);
export const subscriberSecurity = () => accountRequest<SubscriberSecurity>("security");
export const subscriberDevices = () => accountRequest<SubscriberDevice[]>("sessions");
export async function subscriberToken(proof?: SubscriberProof) {
  const result = await accountRequest<{ subscription: ProductUserSubscriptionToken }>(proof ? "subscription-token/reset" : "subscription-token", {
    method: "POST", ...(proof ? { body: JSON.stringify(proof) } : {}),
  });
  return result.subscription;
}
export const revokeSubscriberDevice = (id?: string) => accountRequest<void>(id ? `sessions/${encodeURIComponent(id)}` : "sessions", { method: "DELETE" });
export const beginSubscriberTotp = (password: string) => accountRequest<SubscriberEnrollment>("totp/setup", { method: "POST", body: JSON.stringify({ password }) });
export const confirmSubscriberTotp = (code: string) => accountRequest<{ recovery_codes: string[] }>("totp/confirm", { method: "POST", body: JSON.stringify({ code }) });
export const updateSubscriberTotp = (proof: SubscriberProof, disable = false) => accountRequest<{ recovery_codes: string[] } | undefined>(disable ? "totp/disable" : "totp/recovery-codes", { method: "POST", body: JSON.stringify(proof) });

export async function subscriberShortCode(code: string, revision: string, proof: SubscriberProof, fetcher = fetch) {
  const result = await accountRequest<{ subscription: ProductUserSubscriptionToken }>("subscription-short-code", {
    method: "PUT", body: JSON.stringify({ ...proof, custom_short_code: code, expected_revision: revision }),
  }, fetcher);
  return result.subscription;
}

export function subscriberFormatUrl(subscription: ProductUserSubscriptionToken, format: SubscriptionClientFormat, short = false) {
  const url = new URL(short ? subscription.short_url : subscription.subscription_url);
  url.searchParams.set("format", format);
  return url.toString();
}

export async function subscriberAccount(username: string, update?: { expected_revision: string; new_password: string; reset_totp: boolean }, fetcher = authenticatedFetch): Promise<SubscriberAccount> {
  const query = new URLSearchParams({ username });
  const response = await fetcher(`${apiBaseUrl}/api/v1/subscriber-accounts?${query}`, update ? {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update),
  } : {});
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<SubscriberAccount>;
}
