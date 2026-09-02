import type { AgentCommand, AgentCommandStatus } from "./inventory";

export const sharedIngressProfiles = [
  "vless-reality-vision",
  "vless-xhttp-reality-xmux",
  "anytls-shadowtls",
] as const;
export type SharedIngressProfile = typeof sharedIngressProfiles[number];

export interface SharedIngressRoute {
  node_id: string;
  profile: SharedIngressProfile;
  sni: string;
  upstream_address: "127.0.0.1" | "::1";
  upstream_port: number;
}

export interface SharedIngressWebsite {
  sni: string;
  upstream_url: string;
  tls_address: "127.0.0.1" | "::1";
  tls_port: number;
  certificate_name: string;
  redirect_http: boolean;
}

export interface SharedIngressConfiguration {
  listen_port: 443;
  listen_ipv6: boolean;
  routes: SharedIngressRoute[];
  website: SharedIngressWebsite | null;
}

export interface SharedIngressState {
  server_id: string;
  configuration: SharedIngressConfiguration | null;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
  license_required: false;
}

export interface SharedIngressMutationResponse {
  state: SharedIngressState;
  command: AgentCommand;
  license_required: false;
}

export interface SharedIngressApplyRequest {
  configuration: SharedIngressConfiguration;
  expected_revision: number;
  command_timeout_ms: number;
}

export interface SharedIngressDeleteRequest {
  expected_revision: number;
  command_timeout_ms: number;
}

export interface SharedIngressWebsiteDraft {
  enabled: boolean;
  sni: string;
  upstream_url: string;
  certificate_name: string;
  redirect_http: boolean;
  tls_address: "127.0.0.1" | "::1";
  tls_port: number;
}

const hostLabel = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export function normalizeSharedIngressSni(value: string): string | null {
  const source = value.trim().replace(/\.+$/, "");
  if (!source || source.length > 253 || source.includes("*") || /[\s\u0000-\u001f]/.test(source)) return null;
  try {
    const normalized = new URL(`https://${source}`).hostname.toLowerCase().replace(/\.+$/, "");
    if (!normalized || normalized.length > 253 || normalized.split(".").some(label => !hostLabel.test(label))) return null;
    return normalized;
  } catch { return null; }
}

export function normalizeSharedIngressUpstream(value: string): string | null {
  const source = value.trim();
  if (!source || source.length > 2_048 || /[\s\u0000-\u001f$;{}#]/.test(source)) return null;
  try {
    const parsed = new URL(source);
    if (!(["http:", "https:"] as string[]).includes(parsed.protocol) || !parsed.hostname
      || parsed.username || parsed.password || parsed.hash) return null;
    return source;
  } catch { return null; }
}

export function nextSharedIngressTlsPort(routes: SharedIngressRoute[]): number {
  const used = new Set(routes.map(route => route.upstream_port));
  for (let port = 62_044; port <= 65_535; port += 1) if (!used.has(port)) return port;
  return 65_535;
}

export function sharedIngressWebsiteDraft(configuration: SharedIngressConfiguration | null): SharedIngressWebsiteDraft {
  const website = configuration?.website;
  return website ? { enabled: true, ...website } : {
    enabled: false, sni: "", upstream_url: "", certificate_name: "", redirect_http: true,
    tls_address: "127.0.0.1", tls_port: nextSharedIngressTlsPort(configuration?.routes ?? []),
  };
}

export function validateSharedIngressDraft(
  routes: SharedIngressRoute[],
  website: SharedIngressWebsiteDraft,
): string[] {
  const errors: string[] = [];
  const nodeIds = new Set<string>();
  const ports = new Set<number>();
  const snis = new Set<string>();
  for (const route of routes) {
    if (!sharedIngressProfiles.includes(route.profile)) errors.push("节点路由包含不受支持的协议配置。");
    if (nodeIds.has(route.node_id)) errors.push("同一个节点只能声明一条 443 路由。");
    nodeIds.add(route.node_id);
    if (ports.has(route.upstream_port)) errors.push("节点路由的内部运行端口必须唯一。");
    ports.add(route.upstream_port);
    const normalized = normalizeSharedIngressSni(route.sni);
    if (!normalized) errors.push("节点路由包含无效的 SNI。");
    else if (snis.has(normalized)) errors.push("所有节点和网站必须使用唯一 SNI。");
    else snis.add(normalized);
  }
  if (!website.enabled) {
    if (!routes.length) errors.push("请至少保留一条节点路由，或启用网站反向代理。");
    return [...new Set(errors)];
  }
  const websiteSni = normalizeSharedIngressSni(website.sni);
  if (!websiteSni) errors.push("请输入有效且不含通配符的网站 SNI。");
  else if (snis.has(websiteSni)) errors.push("网站 SNI 与节点路由重复，请更换域名。");
  if (!normalizeSharedIngressUpstream(website.upstream_url)) errors.push("上游必须是无凭据、无片段的绝对 HTTP(S) URL。");
  if (!/^[a-zA-Z0-9_.-]{1,255}$/.test(website.certificate_name)) errors.push("证书名称只能包含字母、数字、点、下划线和连字符。");
  if (!Number.isInteger(website.tls_port) || website.tls_port < 49_152 || website.tls_port > 65_535
    || ports.has(website.tls_port)) errors.push("网站内部 TLS 端口无效或与节点运行端口冲突。");
  return [...new Set(errors)];
}

export function sharedIngressConfiguration(
  current: SharedIngressConfiguration | null,
  routes: SharedIngressRoute[],
  website: SharedIngressWebsiteDraft,
): SharedIngressConfiguration | null {
  if (validateSharedIngressDraft(routes, website).length) return null;
  const sni = normalizeSharedIngressSni(website.sni);
  const upstream = normalizeSharedIngressUpstream(website.upstream_url);
  return {
    listen_port: 443,
    listen_ipv6: current?.listen_ipv6 ?? true,
    routes: routes.map(route => ({ ...route })),
    website: website.enabled && sni && upstream ? {
      sni, upstream_url: upstream, certificate_name: website.certificate_name,
      redirect_http: website.redirect_http, tls_address: website.tls_address, tls_port: website.tls_port,
    } : null,
  };
}

export function sharedIngressCommandLabel(status: AgentCommandStatus) {
  return ({ waiting: "等待前置命令", pending: "等待 Agent", leased: "Agent 执行中", succeeded: "应用成功", failed: "应用失败", skipped: "未执行" } as const)[status];
}
