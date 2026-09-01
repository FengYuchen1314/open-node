import type {
  MihomoStatus,
  SpeedTester,
  SpeedTesterSecret,
  SpeedTestResult,
  SpeedTestRunInput,
} from "../domain/speedtests";
import { authenticatedFetch } from "./auth";

const root = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/speedtest`;
const messages: Record<string, string> = {
  speedtest_node_not_found: "节点不存在，请刷新列表。",
  speedtest_node_unavailable: "节点已停用或没有可用代理配置。",
  speedtest_credential_unavailable: "该节点没有已下发的真实用户凭据，请先分配套餐并同步节点。",
  speedtest_invalid_request: "测速参数不正确。",
  speedtest_tester_not_found: "测速端不存在，请刷新列表。",
  speedtest_tester_offline: "测速端当前不在线。",
  speedtest_tester_busy: "测速端正在执行其他任务。",
  speedtest_runtime_unavailable: "本机测速核心暂不可用。",
  speedtest_download_failed: "测速下载失败，请检查节点连通性。",
  speedtest_latency_failed: "延迟测试失败，请检查节点连通性。",
  speedtest_dispatch_failed: "测速任务发送失败，请检查测速端连接。",
  speedtest_timeout: "测速任务超时。",
  speedtest_storage_unavailable: "测速记录暂时不可用。",
};
const fallback = "未能确认测速操作结果，请刷新状态，不要重复提交正在运行的任务。";

export class SpeedTestRequestError extends Error {
  constructor(readonly status: number | null, readonly code: string | null) {
    super(code && Object.hasOwn(messages, code) ? messages[code] : fallback);
  }
}
function invalid(): never { throw new SpeedTestRequestError(null, null); }
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : invalid();
}
function exact(value: unknown, keys: string[]) {
  const row = object(value);
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) invalid();
  return row;
}
function uuid(value: unknown) {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
    ? value : invalid();
}
function text(value: unknown, max: number, required = false) {
  return typeof value === "string" && value.length <= max && (!required || value.length > 0)
    ? value : invalid();
}
function nullableText(value: unknown, max: number) { return value === null ? null : text(value, max); }
function number(value: unknown, maximum: number) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= maximum
    ? value : invalid();
}
function nullableNumber(value: unknown, maximum: number) { return value === null ? null : number(value, maximum); }
function instant(value: unknown) {
  return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value))
    ? value : invalid();
}
function nullableInstant(value: unknown) { return value === null ? null : instant(value); }
function license(value: unknown) { if (value !== false) invalid(); return false as const; }
function result(value: unknown): SpeedTestResult {
  const row = exact(value, ["id", "node_id", "node_name", "source", "tester_id", "tester_name",
    "status", "down_mbps", "latency_ms", "egress_ip", "bytes", "error_code", "created_at",
    "completed_at", "license_required"]);
  if (row.source !== "master" && row.source !== "tester") invalid();
  if (row.status !== "running" && row.status !== "ok" && row.status !== "failed") invalid();
  return {
    id: uuid(row.id), node_id: uuid(row.node_id), node_name: text(row.node_name, 120, true),
    source: row.source, tester_id: row.tester_id === null ? null : uuid(row.tester_id),
    tester_name: nullableText(row.tester_name, 120), status: row.status,
    down_mbps: nullableNumber(row.down_mbps, 1_000_000),
    latency_ms: nullableNumber(row.latency_ms, 600_000), egress_ip: nullableText(row.egress_ip, 64),
    bytes: number(row.bytes, 2_147_483_648), error_code: nullableText(row.error_code, 80),
    created_at: instant(row.created_at), completed_at: nullableInstant(row.completed_at),
    license_required: license(row.license_required),
  };
}
function tester(value: unknown): SpeedTester {
  const row = exact(value, ["id", "name", "online", "caps", "version", "last_seen_at", "created_at",
    "created_by", "license_required"]);
  if (typeof row.online !== "boolean" || !Array.isArray(row.caps) || row.caps.length > 32) invalid();
  return {
    id: uuid(row.id), name: text(row.name, 120, true), online: row.online,
    caps: row.caps.map(item => text(item, 40, true)), version: nullableText(row.version, 80),
    last_seen_at: nullableInstant(row.last_seen_at), created_at: instant(row.created_at),
    created_by: text(row.created_by, 80, true), license_required: license(row.license_required),
  };
}
async function body(response: Response) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") ?? "")) invalid();
  const source = await response.text();
  if (new TextEncoder().encode(source).byteLength > 512 * 1024) invalid();
  try { return JSON.parse(source) as unknown; } catch { return invalid(); }
}
async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T,
  fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(root + path, { ...init, headers: {
      Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}),
    }, cache: "no-store" });
    const value = response.status === 204 ? null : await body(response);
    if (!response.ok) {
      const code = object(value).code;
      throw new SpeedTestRequestError(response.status, typeof code === "string" ? code : null);
    }
    return parse(value);
  } catch (error) {
    if (error instanceof SpeedTestRequestError) throw error;
    return invalid();
  }
}
function secret(value: unknown): SpeedTesterSecret {
  const row = exact(value, ["tester", "token", "websocket_path", "license_required"]);
  return { tester: tester(row.tester), token: text(row.token, 128, true),
    websocket_path: text(row.websocket_path, 120, true), license_required: license(row.license_required) };
}

export function loadLatestSpeedTests(fetcher = authenticatedFetch) {
  return request("/results?latest=true", {}, value => {
    const row = exact(value, ["results", "license_required"]); license(row.license_required);
    if (!Array.isArray(row.results)) invalid(); return row.results.map(result);
  }, fetcher);
}
export function loadSpeedTestHistory(nodeId: string, fetcher = authenticatedFetch) {
  return request(`/results?node_id=${encodeURIComponent(uuid(nodeId))}&limit=200`, {}, value => {
    const row = exact(value, ["results", "license_required"]); license(row.license_required);
    if (!Array.isArray(row.results)) invalid(); return row.results.map(result);
  }, fetcher);
}
export function runSpeedTest(input: SpeedTestRunInput, fetcher = authenticatedFetch) {
  return request("/run", { method: "POST", body: JSON.stringify(input) }, value => {
    const row = exact(value, ["result", "queued", "license_required"]);
    if (row.queued !== true) invalid(); license(row.license_required); return result(row.result);
  }, fetcher);
}
export function loadSpeedTesters(fetcher = authenticatedFetch) {
  return request("/testers", {}, value => {
    const row = exact(value, ["testers", "license_required"]); license(row.license_required);
    if (!Array.isArray(row.testers)) invalid(); return row.testers.map(tester);
  }, fetcher);
}
export function createSpeedTester(name: string, fetcher = authenticatedFetch) {
  return request("/testers/create", { method: "POST", body: JSON.stringify({ name }) }, secret, fetcher);
}
export function rotateSpeedTester(id: string, fetcher = authenticatedFetch) {
  return request("/testers/rotate-token", { method: "POST", body: JSON.stringify({ id: uuid(id) }) }, secret, fetcher);
}
export function revokeSpeedTester(id: string, fetcher = authenticatedFetch) {
  return request("/testers/revoke", { method: "POST", body: JSON.stringify({ id: uuid(id) }) },
    value => { if (value !== null) invalid(); }, fetcher);
}
export function loadMihomoStatus(fetcher = authenticatedFetch) {
  return request("/mihomo-status", {}, value => {
    const row = exact(value, ["supported", "ready", "version", "platform", "downloading", "message",
      "license_required"]);
    if (typeof row.supported !== "boolean" || typeof row.ready !== "boolean"
      || typeof row.downloading !== "boolean") invalid();
    return { supported: row.supported, ready: row.ready, version: text(row.version, 40, true),
      platform: text(row.platform, 40, true), downloading: row.downloading,
      message: text(row.message, 200, true), license_required: license(row.license_required) } as MihomoStatus;
  }, fetcher);
}
export function speedTestError(error: unknown) {
  return error instanceof SpeedTestRequestError ? error.message : fallback;
}
export function speedTestStatusMessage(code: string | null) {
  return code && messages[code] ? messages[code] : "测速失败。";
}
