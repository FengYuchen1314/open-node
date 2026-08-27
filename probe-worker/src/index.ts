export interface Env {
  ASSETS?: Fetcher;
  MMWX_ORIGIN?: string;
  PROBE_TOKEN?: string;
}

const ROUTES = new Map<string, string>([
  ["/api/probe", "/api/v1/public/probe-servers"],
  ["/api/series", "/api/v1/public/probe-series"],
  ["/api/targets", "/api/v1/public/probe-targets"],
  ["/api/stream", "/api/v1/public/probe-ws"],
  ["/api/public/probe-servers", "/api/v1/public/probe-servers"],
  ["/api/public/probe-series", "/api/v1/public/probe-series"],
  ["/api/public/probe-targets", "/api/v1/public/probe-targets"],
  ["/api/public/probe-ws", "/api/v1/public/probe-ws"],
  ["/api/v1/public/probe-servers", "/api/v1/public/probe-servers"],
  ["/api/v1/public/probe-series", "/api/v1/public/probe-series"],
  ["/api/v1/public/probe-targets", "/api/v1/public/probe-targets"],
  ["/api/v1/public/probe-ws", "/api/v1/public/probe-ws"],
]);

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requestUrl = new URL(request.url);
    const route = ROUTES.get(normalizePath(requestUrl.pathname));

    if (!route) {
      if (requestUrl.pathname === "/login") {
        return redirectToOriginLogin(env.MMWX_ORIGIN);
      }
      return env.ASSETS?.fetch(request) ?? jsonError("not found", 404);
    }

    if (request.method !== "GET") {
      return jsonError("method not allowed", 405, { Allow: "GET" });
    }
    if (!env.MMWX_ORIGIN || !env.PROBE_TOKEN) {
      return jsonError("probe worker is not configured", 503);
    }

    const upstream = buildUpstreamUrl(env.MMWX_ORIGIN, route, requestUrl.search);
    if (!upstream) {
      return jsonError("invalid origin", 503);
    }
    const headers = buildUpstreamHeaders(request, env.PROBE_TOKEN);
    const response = await fetch(upstream, {
      headers,
      method: "GET",
      redirect: "manual",
    });

    if (response.status === 101) {
      return response;
    }

    const proxied = new Response(response.body, response);
    proxied.headers.set("Cache-Control", "no-store");
    return proxied;
  },
} satisfies ExportedHandler<Env>;

function buildUpstreamUrl(origin: string, path: string, search: string): string | null {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return null;
  }
  if (!originIsAllowed(url)) {
    return null;
  }

  const basePath = url.pathname.replace(/\/+$/, "");
  url.pathname = `${basePath}${path}`;
  url.search = search;
  return url.toString();
}

function buildUpstreamHeaders(request: Request, token: string): Headers {
  const requestUrl = new URL(request.url);
  const headers = new Headers(request.headers);
  headers.delete("authorization");
  headers.delete("cookie");
  headers.delete("host");
  headers.set("X-Forwarded-Host", requestUrl.host);
  headers.set("X-MMwx-Probe-Token", token);
  return headers;
}

function redirectToOriginLogin(origin: string | undefined): Response {
  if (!origin) {
    return jsonError("probe worker is not configured", 503);
  }
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return jsonError("invalid origin", 503);
  }
  if (!originIsAllowed(url)) {
    return jsonError("invalid origin", 503);
  }
  const basePath = url.pathname.replace(/\/+$/, "");
  url.pathname = `${basePath}/login`;
  url.search = "";
  return Response.redirect(url.toString(), 302);
}

function normalizePath(pathname: string): string {
  const normalized = pathname.replace(/\/+$/, "");
  return normalized || "/";
}

function originIsAllowed(url: URL): boolean {
  if (url.protocol === "https:") {
    return true;
  }
  if (url.protocol !== "http:") {
    return false;
  }
  return url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "::1";
}

function jsonError(message: string, status: number, headers?: HeadersInit): Response {
  return Response.json(
    {
      success: false,
      error: message,
      license_required: false,
    },
    { status, headers },
  );
}
