import type { AgentCommand, AgentCommandStatus } from "../domain/inventory";
import type {
  SharedIngressApplyRequest,
  SharedIngressConfiguration,
  SharedIngressDeleteRequest,
  SharedIngressMutationResponse,
  SharedIngressProfile,
  SharedIngressRoute,
  SharedIngressState,
  SharedIngressWebsite,
} from "../domain/shared-ingress";
import { sharedIngressProfiles, validateSharedIngressDraft } from "../domain/shared-ingress";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

const base = import.meta.env.VITE_API_BASE_URL ?? "";
const fallback = "未能确认 443 分流操作结果，请重新读取当前配置后再操作。";
const statuses: AgentCommandStatus[] = ["waiting", "pending", "leased", "succeeded", "failed", "skipped"];

export class SharedIngressRequestError extends Error {
  constructor(readonly status: number | null, message = fallback) { super(message); }
}

function invalid(): never { throw new SharedIngressRequestError(null); }
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : invalid();
}
function exact(value: unknown, keys: string[]) {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) invalid();
  return row;
}
function text(value: unknown, maximum: number, required = false) {
  return typeof value === "string" && value.length <= maximum && (!required || value.length > 0)
    ? value : invalid();
}
function nullableText(value: unknown, maximum: number) { return value === null ? null : text(value, maximum); }
function uuid(value: unknown) {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
    ? value : invalid();
}
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum
    ? Number(value) : invalid();
}
function instant(value: unknown) {
  return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value)) ? value : invalid();
}
function nullableInstant(value: unknown) { return value === null ? null : instant(value); }
function license(value: unknown) { if (value !== false) invalid(); return false as const; }

function route(value: unknown): SharedIngressRoute {
  const row = exact(value, ["node_id", "profile", "sni", "upstream_address", "upstream_port"]);
  if (!(sharedIngressProfiles as readonly unknown[]).includes(row.profile)
    || (row.upstream_address !== "127.0.0.1" && row.upstream_address !== "::1")) invalid();
  return {
    node_id: uuid(row.node_id), profile: row.profile as SharedIngressProfile,
    sni: text(row.sni, 253, true), upstream_address: row.upstream_address,
    upstream_port: integer(row.upstream_port, 49_152, 65_535),
  };
}
function website(value: unknown): SharedIngressWebsite {
  const row = exact(value, ["sni", "upstream_url", "tls_address", "tls_port", "certificate_name", "redirect_http"]);
  if ((row.tls_address !== "127.0.0.1" && row.tls_address !== "::1") || typeof row.redirect_http !== "boolean") invalid();
  return {
    sni: text(row.sni, 253, true), upstream_url: text(row.upstream_url, 2_048, true),
    tls_address: row.tls_address, tls_port: integer(row.tls_port, 49_152, 65_535),
    certificate_name: text(row.certificate_name, 255, true), redirect_http: row.redirect_http,
  };
}
function configuration(value: unknown): SharedIngressConfiguration {
  const row = exact(value, ["listen_port", "listen_ipv6", "routes", "website"]);
  if (row.listen_port !== 443 || typeof row.listen_ipv6 !== "boolean" || !Array.isArray(row.routes) || row.routes.length > 32) invalid();
  const routes = row.routes.map(route);
  const site = row.website === null ? null : website(row.website);
  const validation = validateSharedIngressDraft(routes, site ? { enabled: true, ...site } : {
    enabled: false, sni: "", upstream_url: "", certificate_name: "", redirect_http: true,
    tls_address: "127.0.0.1", tls_port: 62_044,
  });
  if (validation.length) invalid();
  return { listen_port: 443, listen_ipv6: row.listen_ipv6, routes, website: site };
}
function state(value: unknown): SharedIngressState {
  const row = exact(value, ["server_id", "configuration", "revision", "created_at", "updated_at", "license_required"]);
  return {
    server_id: uuid(row.server_id), configuration: row.configuration === null ? null : configuration(row.configuration),
    revision: integer(row.revision), created_at: nullableInstant(row.created_at), updated_at: nullableInstant(row.updated_at),
    license_required: license(row.license_required),
  };
}
function command(value: unknown): AgentCommand {
  const row = exact(value, ["id", "server_id", "request_id", "method", "path", "query", "body", "timeout_ms", "stream", "status", "depends_on_command_id", "attempts", "result_status", "result_body", "result_error", "created_at", "leased_at", "completed_at", "updated_at"]);
  if (!(statuses as unknown[]).includes(row.status) || typeof row.stream !== "boolean"
    || !["GET", "POST", "PUT", "PATCH", "DELETE"].includes(String(row.method))) invalid();
  return {
    id: uuid(row.id), server_id: uuid(row.server_id), request_id: text(row.request_id, 255, true),
    method: row.method as string, path: text(row.path, 255, true), query: text(row.query, 2_048), body: row.body,
    timeout_ms: integer(row.timeout_ms, 1_000, 300_000), stream: row.stream,
    status: row.status as AgentCommandStatus,
    depends_on_command_id: row.depends_on_command_id === null ? null : uuid(row.depends_on_command_id),
    attempts: integer(row.attempts), result_status: row.result_status === null ? null : integer(row.result_status, 0, 999),
    result_body: row.result_body, result_error: nullableText(row.result_error, 4_096),
    created_at: instant(row.created_at), leased_at: nullableInstant(row.leased_at), completed_at: nullableInstant(row.completed_at), updated_at: instant(row.updated_at),
  };
}
async function json(response: Response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) invalid();
  const source = await response.text();
  if (new TextEncoder().encode(source).byteLength > 512 * 1024) invalid();
  try { return JSON.parse(source) as unknown; } catch { return invalid(); }
}
function knownError(detail: unknown, status: number) {
  if (typeof detail === "string") {
    const message = detail.startsWith("shared ingress revision changed:")
      ? "443 分流配置已发生变化，请重新读取后再保存。"
      : detail.startsWith("server not found:")
        ? "服务器不存在，请刷新服务器列表。"
        : detail.startsWith("managed node not found:")
          ? "自动节点路由已失效，请重新生成节点配置。"
          : detail.startsWith("managed node belongs to a different server:")
            ? "节点路由不属于当前服务器。"
            : detail.startsWith("managed node is disabled:")
              ? "节点路由已停用，请先更新节点。"
              : detail.startsWith("only physical managed nodes may own ingress routes:")
                ? "443 分流只能绑定当前服务器的物理节点。"
                : detail.startsWith("managed node profile does not match route:")
                  ? "节点协议配置已经变化，请重新生成节点路由。"
                  : detail.startsWith("managed node is not configured for the shared public 443 entry:")
                    ? "节点没有配置为共享公网 443 入口。"
                    : detail.startsWith("managed node camouflage SNI does not match route:")
                      ? "节点伪装 SNI 已经变化，请重新生成节点路由。"
                      : detail.startsWith("Shared servers are controlled through the owner's federation interface")
                        ? "共享服务器必须由拥有方控制，不能在此修改 443 分流。" : null;
    if (message) return new SharedIngressRequestError(status, message);
  }
  return new SharedIngressRequestError(status, requestError(detail, `${fallback}（${status}）`).message);
}
async function request<T>(serverId: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch) {
  try {
    const id = uuid(serverId);
    const response = await fetcher(`${base}/api/v1/servers/${encodeURIComponent(id)}/shared-ingress`, {
      ...init, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) }, cache: "no-store",
    });
    const value = await json(response);
    if (!response.ok) throw knownError(object(value).detail, response.status);
    return parse(value);
  } catch (failure) {
    if (failure instanceof SharedIngressRequestError) throw failure;
    return invalid();
  }
}
function mutation(value: unknown, serverId: string, method: "PUT" | "DELETE"): SharedIngressMutationResponse {
  const row = exact(value, ["state", "command", "license_required"]);
  const parsedState = state(row.state), parsedCommand = command(row.command);
  if (parsedState.server_id !== serverId || parsedCommand.server_id !== serverId
    || parsedCommand.method !== method || parsedCommand.path !== "/api/child/nginx/shared-ingress" || parsedCommand.query !== "") invalid();
  if (method === "PUT") {
    const commandConfiguration = configuration(parsedCommand.body);
    if (JSON.stringify(commandConfiguration) !== JSON.stringify(parsedState.configuration)) invalid();
    parsedCommand.body = commandConfiguration;
  } else if (parsedCommand.body !== null) invalid();
  return { state: parsedState, command: parsedCommand, license_required: license(row.license_required) };
}

export function getSharedIngress(serverId: string, fetcher = authenticatedFetch) {
  return request(serverId, {}, value => {
    const parsed = state(value); if (parsed.server_id !== serverId) invalid(); return parsed;
  }, fetcher);
}

export function applySharedIngress(serverId: string, payload: SharedIngressApplyRequest, fetcher = authenticatedFetch) {
  return request(serverId, { method: "PUT", body: JSON.stringify(payload) }, value => mutation(value, serverId, "PUT"), fetcher);
}

export function disableSharedIngress(serverId: string, payload: SharedIngressDeleteRequest, fetcher = authenticatedFetch) {
  return request(serverId, { method: "DELETE", body: JSON.stringify(payload) }, value => mutation(value, serverId, "DELETE"), fetcher);
}

export function sharedIngressErrorMessage(failure: unknown) {
  return failure instanceof SharedIngressRequestError ? failure.message : fallback;
}
