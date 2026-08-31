import type {
  ExternalConfirmationRead, ExternalNodeChange, ExternalNodeRead, ExternalNodeUpdate,
  ExternalPreviewCancelResponse, ExternalPreviewConfirm, ExternalPreviewNode, ExternalPreviewRead,
  ExternalRevisionRequest, ExternalSourceCreate, ExternalSourceDelete, ExternalSourceDeleteResponse,
  ExternalSourceDetail, ExternalSourceRead, ExternalSourcesResponse, ExternalSourceUpdate,
} from "../domain/external-subscriptions";
import { authenticatedFetch } from "./auth";

const base = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/external-subscriptions`;
const unknownOutcome = "无法完成外部订阅请求。再次写入前，请先核实当前状态。";
// Never display arbitrary validation input, upstream response text or fetch exceptions.
const safeDetails: Record<string, string> = {
  "Cancel an existing preview before fetching again": "已有 3 个未确认的预览。请恢复并取消其中一个，或等待其过期。",
  "External subscription preview expired; fetch again": "此预览已过期。请关闭预览，并手动获取新预览。",
  "External source changed after this preview": "来源在预览后发生了变化。请刷新来源状态，再手动获取并检查新预览。",
  "Preview was confirmed with a different selection": "此预览已按另一组选择确认，请查看确认回执。",
  "Preview is already confirmed; its receipt is retained": "此预览已确认，仍可查看确认回执。",
  "Subscriber removal is in progress": "正在删除此来源所属的用户，请刷新用户状态。",
  "Subscriber external source limit reached": "此来源所属用户的外部订阅来源数量已达上限。",
  "External source saved-node limit reached": "此来源已保存的节点数量已达上限。",
};

function errorMessage(status: number | null, detail?: unknown): string {
  if (typeof detail === "string" && Object.hasOwn(safeDetails, detail)) return safeDetails[detail]!;
  switch (status) {
    case null: return unknownOutcome;
    case 401: return "请重新登录后管理外部订阅。";
    case 403: return "此操作需要管理员权限和有效的请求验证。";
    case 404: return "外部订阅来源或预览已不可用，请刷新状态。";
    case 409: return "来源或预览已发生变化。再次写入前，请先刷新状态。";
    case 410: return "此预览已过期。请关闭预览，并手动获取新预览。";
    case 413: return "外部订阅请求超出大小限制。";
    case 415: return "外部订阅请求必须使用 JSON。";
    case 422: return "外部订阅设置或预览无效，请检查输入内容和支持的来源格式。";
    case 429: return "外部订阅请求过于频繁，请稍后重试。";
    case 503: return "外部订阅存储或抓取服务不可用。再次写入前，请先检查服务状态。";
    default: return `外部订阅请求失败（${status}）。再次写入前，请先核实当前状态。`;
  }
}

export class ExternalSubscriptionsError extends Error {
  readonly status: number | null;
  constructor(status: number | null, detail?: unknown) {
    super(errorMessage(status, detail));
    this.name = "ExternalSubscriptionsError";
    this.status = status;
  }
  get outcomeUnknown() { return this.status === null || this.status >= 500; }
}

export function externalSubscriptionsErrorMessage(failure: unknown): string {
  return failure instanceof ExternalSubscriptionsError ? failure.message : unknownOutcome;
}

// Project only the frozen public DTO. Unexpected fields (including credentials)
// never enter panel state; malformed successful replies are an unknown outcome.
function invalid(): never { throw new ExternalSubscriptionsError(null); }
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
}
function string(value: unknown): string { return typeof value === "string" ? value : invalid(); }
function number(value: unknown): number { return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value) ? value : invalid(); }
function boolean(value: unknown): boolean { return typeof value === "boolean" ? value : invalid(); }
function nullableString(value: unknown): string | null { return value === null ? null : string(value); }
function array<T>(value: unknown, parse: (entry: unknown) => T): T[] { return Array.isArray(value) ? value.map(parse) : invalid(); }
function free(value: unknown): false { return value === false ? false : invalid(); }
function metadata(value: unknown): Record<string, number> {
  const row = record(value), result: Record<string, number> = {};
  for (const key of ["upload", "download", "total", "expire"] as const) {
    const entry = row[key];
    // The backend can represent signed int64 values that JS cannot display
    // precisely. Omit that field, without rejecting the otherwise valid source.
    if (typeof entry === "number" && Number.isSafeInteger(entry) && entry >= 0) result[key] = entry;
  }
  return result;
}
function source(value: unknown): ExternalSourceRead {
  const row = record(value);
  return {
    id: string(row.id), owner_username: string(row.owner_username), name: string(row.name), enabled: boolean(row.enabled),
    revision: number(row.revision), has_custom_user_agent: boolean(row.has_custom_user_agent),
    node_count: number(row.node_count), available_node_count: number(row.available_node_count), metadata: metadata(row.metadata),
    last_synced_at: nullableString(row.last_synced_at), created_at: string(row.created_at), updated_at: string(row.updated_at),
  };
}
function node(value: unknown): ExternalNodeRead {
  const row = record(value);
  return {
    id: string(row.id), source_id: string(row.source_id), upstream_name: string(row.upstream_name), name: string(row.name),
    protocol: string(row.protocol), enabled: boolean(row.enabled), present: boolean(row.present), available: boolean(row.available),
    reason: nullableString(row.reason),
  };
}
function detail(value: unknown, sourceId: string): ExternalSourceDetail {
  const row = record(value), result = { source: source(row.source), nodes: array(row.nodes, node), license_required: free(row.license_required) };
  if (result.source.id !== sourceId || result.nodes.some(entry => entry.source_id !== sourceId)) invalid();
  return result;
}
function previewNode(value: unknown): ExternalPreviewNode {
  const row = record(value), change = string(row.change);
  if (!["new", "updated", "unchanged", "missing", "unavailable"].includes(change)) invalid();
  return {
    node_id: string(row.node_id), upstream_name: string(row.upstream_name), name: string(row.name), protocol: string(row.protocol),
    change: change as ExternalNodeChange, existing: boolean(row.existing), selectable: boolean(row.selectable),
    reason: row.reason === undefined ? null : nullableString(row.reason), changed_fields: array(row.changed_fields ?? [], string),
  };
}
function receipt(value: unknown, sourceId: string, previewId: string): ExternalConfirmationRead {
  const row = record(value);
  const result = {
    source_id: string(row.source_id), preview_id: string(row.preview_id), revision: number(row.revision),
    imported_count: number(row.imported_count), updated_count: number(row.updated_count), missing_count: number(row.missing_count),
    applied_at: string(row.applied_at),
  };
  if (result.source_id !== sourceId || result.preview_id !== previewId) invalid();
  return result;
}
function preview(value: unknown, sourceId: string, previewId?: string): ExternalPreviewRead {
  const row = record(value), id = string(row.id);
  if (row.source_id !== sourceId || (previewId !== undefined && previewId !== id)) invalid();
  return {
    id, source_id: sourceId, source_revision: number(row.source_revision), created_at: string(row.created_at), expires_at: string(row.expires_at),
    metadata: metadata(row.metadata), nodes: array(row.nodes, previewNode),
    receipt: row.receipt == null ? null : receipt(row.receipt, sourceId, id), license_required: free(row.license_required),
  };
}

async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(`${base}${path}`, {
      ...init, headers: { Accept: "application/json", ...(init.body === undefined ? {} : { "Content-Type": "application/json" }) },
      cache: "no-store", redirect: "error", referrerPolicy: "no-referrer",
    });
  } catch { throw new ExternalSubscriptionsError(null); }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const message = body !== null && typeof body === "object" && "detail" in body ? body.detail : undefined;
    throw new ExternalSubscriptionsError(response.status, message);
  }
  try { return parse(await response.json()); }
  catch { throw new ExternalSubscriptionsError(null); }
}

const sourcePath = (sourceId: string) => `/${encodeURIComponent(sourceId)}`;
const previewPath = (sourceId: string, previewId: string) => `${sourcePath(sourceId)}/previews/${encodeURIComponent(previewId)}`;

export function listExternalSources(fetcher = authenticatedFetch): Promise<ExternalSourcesResponse> {
  return request("", {}, value => {
    const row = record(value);
    return { sources: array(row.sources, source), license_required: free(row.license_required) };
  }, fetcher);
}
export function createExternalSource(payload: ExternalSourceCreate, fetcher = authenticatedFetch): Promise<ExternalSourceRead> {
  const { owner_username, name, url, user_agent = "", enabled = true } = payload;
  return request("", { method: "POST", body: JSON.stringify({ owner_username, name, url, user_agent, enabled }) }, value => {
    const result = source(value);
    if (result.owner_username !== owner_username) invalid();
    return result;
  }, fetcher);
}
export function getExternalSource(sourceId: string, fetcher = authenticatedFetch): Promise<ExternalSourceDetail> {
  return request(sourcePath(sourceId), {}, value => detail(value, sourceId), fetcher);
}
export function updateExternalSource(sourceId: string, payload: ExternalSourceUpdate, fetcher = authenticatedFetch): Promise<ExternalSourceRead> {
  const { expected_revision, name, enabled, url = null, user_agent = null } = payload;
  return request(sourcePath(sourceId), { method: "PUT", body: JSON.stringify({ expected_revision, name, enabled, url, user_agent }) }, value => {
    const result = source(value);
    if (result.id !== sourceId) invalid();
    return result;
  }, fetcher);
}
export function deleteExternalSource(sourceId: string, payload: ExternalSourceDelete, fetcher = authenticatedFetch): Promise<ExternalSourceDeleteResponse> {
  return request(`${sourcePath(sourceId)}/delete`, { method: "POST", body: JSON.stringify({ expected_revision: payload.expected_revision, confirm: payload.confirm }) }, value => {
    const row = record(value);
    if (row.deleted !== true) invalid();
    return { deleted: true, license_required: free(row.license_required) };
  }, fetcher);
}
export function updateExternalNode(sourceId: string, nodeId: string, payload: ExternalNodeUpdate, fetcher = authenticatedFetch): Promise<ExternalSourceDetail> {
  return request(`${sourcePath(sourceId)}/nodes/${encodeURIComponent(nodeId)}`, {
    method: "PUT", body: JSON.stringify({ expected_revision: payload.expected_revision, name: payload.name, enabled: payload.enabled }),
  }, value => detail(value, sourceId), fetcher);
}
export function createExternalPreview(sourceId: string, payload: ExternalRevisionRequest, fetcher = authenticatedFetch): Promise<ExternalPreviewRead> {
  return request(`${sourcePath(sourceId)}/previews`, { method: "POST", body: JSON.stringify({ expected_revision: payload.expected_revision }) }, value => preview(value, sourceId), fetcher);
}
export function getExternalPreview(sourceId: string, previewId: string, fetcher = authenticatedFetch): Promise<ExternalPreviewRead> {
  return request(previewPath(sourceId, previewId), {}, value => preview(value, sourceId, previewId), fetcher);
}
export function confirmExternalPreview(sourceId: string, previewId: string, payload: ExternalPreviewConfirm, fetcher = authenticatedFetch): Promise<ExternalConfirmationRead> {
  return request(`${previewPath(sourceId, previewId)}/confirm`, {
    method: "POST", body: JSON.stringify({ expected_revision: payload.expected_revision, selected_node_ids: payload.selected_node_ids, accept_changes: payload.accept_changes }),
  }, value => receipt(value, sourceId, previewId), fetcher);
}
export function cancelExternalPreview(sourceId: string, previewId: string, fetcher = authenticatedFetch): Promise<ExternalPreviewCancelResponse> {
  return request(previewPath(sourceId, previewId), { method: "DELETE" }, value => {
    const row = record(value);
    if (row.cancelled !== true) invalid();
    return { cancelled: true, license_required: free(row.license_required) };
  }, fetcher);
}
