import { createObservableState } from "./observable-state";
import { requestError, requestFailureMessage } from "./request-error";

export interface OperatorSession {
  configured: boolean;
  authenticated: boolean;
  username: string | null;
  csrf_token: string | null;
}

export interface AdministratorTotpEnrollment {
  secret: string;
  provisioning_uri: string;
  expires_at: string;
}

export interface OperatorLogin extends OperatorSession {
  requires_2fa: boolean;
  challenge: string | null;
  enrollment_required: boolean;
  enrollment: AdministratorTotpEnrollment | null;
  recovery_codes: string[];
}

export interface AdministratorSecurity {
  totp_enabled: boolean;
  totp_available: boolean;
  recovery_codes_remaining: number;
  require_totp: boolean;
}

export interface AdministratorProfile {
  username: string;
  email: string;
  nickname: string;
  avatar_url: string;
  revision: number;
}

const authStore = createObservableState({
  ready: false,
  error: "",
  session: null as OperatorSession | null,
});

export const authState = authStore.state;
export const getAuthSnapshot = authStore.getSnapshot;
export const subscribeAuthState = authStore.subscribe;

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

async function authRequest(path: string, init: RequestInit = {}, fetcher = fetch) {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Open-Node-Client", "browser");
  if (authState.session?.csrf_token) headers.set("X-CSRF-Token", authState.session.csrf_token);
  const response = await fetcher(`${apiBaseUrl}/api/v1/auth/${path}`, {
    ...init, headers, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401 && path !== "login") clearSession();
    const body = await response.json().catch(() => null);
    throw requestError(typeof body?.detail === "string" ? body.detail : undefined, `身份验证请求失败（${response.status}）`);
  }
  return response;
}

export async function loadSession(fetcher = fetch) {
  authState.error = "";
  try {
    const response = await authRequest("session", {}, fetcher);
    authState.session = await response.json() as OperatorSession;
  } catch (error) {
    authState.session = null;
    authState.error = requestFailureMessage(error, "无法连接服务器。");
  } finally {
    authState.ready = true;
  }
}

export async function signIn(username: string, password: string, fetcher = fetch) {
  const response = await authRequest("login", {
    method: "POST", body: JSON.stringify({ username, password }),
  }, fetcher);
  const result = await response.json() as OperatorLogin;
  if (result.authenticated) acceptOperatorSession(result);
  else clearSession();
  return result;
}

export async function verifySignIn(challenge: string, code: string, fetcher = fetch) {
  const response = await authRequest("login/verify", {
    method: "POST", body: JSON.stringify({ challenge, code }),
  }, fetcher);
  const result = await response.json() as OperatorLogin;
  if (result.authenticated && result.recovery_codes.length === 0) acceptOperatorSession(result);
  return result;
}

export function acceptOperatorSession(session: OperatorLogin) {
  if (!session.authenticated) throw new Error("身份验证尚未完成。");
  authState.session = {
    configured: session.configured,
    authenticated: session.authenticated,
    username: session.username,
    csrf_token: session.csrf_token,
  };
}

function clearSession() {
  authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
}

export async function signOut(fetcher = fetch) {
  await authRequest("logout", { method: "POST" }, fetcher);
  clearSession();
}

export async function changePassword(currentPassword: string, newPassword: string, fetcher = fetch) {
  await authRequest("password", {
    method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }, fetcher);
  clearSession();
}

export async function administratorSecurity(fetcher = fetch) {
  const response = await authRequest("security", {}, fetcher);
  return await response.json() as AdministratorSecurity;
}

export async function beginAdministratorTotp(password: string, fetcher = fetch) {
  const response = await authRequest("totp/setup", {
    method: "POST", body: JSON.stringify({ password, code: "" }),
  }, fetcher);
  return await response.json() as AdministratorTotpEnrollment;
}

export async function confirmAdministratorTotp(code: string, fetcher = fetch) {
  const response = await authRequest("totp/confirm", {
    method: "POST", body: JSON.stringify({ code }),
  }, fetcher);
  return (await response.json() as { recovery_codes: string[] }).recovery_codes;
}

export async function disableAdministratorTotp(password: string, code: string, fetcher = fetch) {
  await authRequest("totp/disable", {
    method: "POST", body: JSON.stringify({ password, code }),
  }, fetcher);
}

export async function regenerateAdministratorRecoveryCodes(password: string, code: string, fetcher = fetch) {
  const response = await authRequest("totp/recovery-codes", {
    method: "POST", body: JSON.stringify({ password, code }),
  }, fetcher);
  return (await response.json() as { recovery_codes: string[] }).recovery_codes;
}

export async function updateAdministratorTotpPolicy(required: boolean, password: string, code: string, fetcher = fetch) {
  const response = await authRequest("security/policy", {
    method: "PUT", body: JSON.stringify({ required, password, code }),
  }, fetcher);
  return await response.json() as AdministratorSecurity;
}

function administratorProfile(value: unknown): AdministratorProfile {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("管理员资料响应无效。");
  const row = value as Record<string, unknown>, keys = ["username", "email", "nickname", "avatar_url", "revision"];
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))
    || typeof row.username !== "string" || typeof row.email !== "string" || typeof row.nickname !== "string"
    || typeof row.avatar_url !== "string" || !Number.isInteger(row.revision) || (row.revision as number) < 0) {
    throw new Error("管理员资料响应无效。");
  }
  return row as unknown as AdministratorProfile;
}

export async function loadAdministratorProfile(fetcher = fetch) {
  return administratorProfile(await (await authRequest("profile", {}, fetcher)).json());
}

export async function saveAdministratorProfile(
  payload: Pick<AdministratorProfile, "email" | "nickname" | "avatar_url" | "revision">,
  fetcher = fetch,
) {
  const response = await authRequest("profile", { method: "PUT", body: JSON.stringify({
    email: payload.email, nickname: payload.nickname, avatar_url: payload.avatar_url,
    expected_revision: payload.revision,
  }) }, fetcher);
  return administratorProfile(await response.json());
}

export const authenticatedFetch: typeof fetch = async (input, init = {}) => {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && authState.session?.csrf_token) {
    headers.set("X-CSRF-Token", authState.session.csrf_token);
  }
  const response = await fetch(input, { ...init, headers, credentials: "include", cache: "no-store" });
  if (response.status === 401) clearSession();
  return response;
};
