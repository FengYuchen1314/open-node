import { reactive } from "vue";

export interface OperatorSession {
  configured: boolean;
  authenticated: boolean;
  username: string | null;
  csrf_token: string | null;
}

export const authState = reactive({
  ready: false,
  error: "",
  session: null as OperatorSession | null,
});

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
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`);
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
    authState.error = error instanceof Error ? error.message : "Unable to connect";
  } finally {
    authState.ready = true;
  }
}

export async function signIn(username: string, password: string, fetcher = fetch) {
  const response = await authRequest("login", {
    method: "POST", body: JSON.stringify({ username, password }),
  }, fetcher);
  authState.session = await response.json() as OperatorSession;
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
