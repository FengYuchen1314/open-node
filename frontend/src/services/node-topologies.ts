import type {
  NodeTopologiesResponse,
  NodeTopology,
  NodeTopologyCandidate,
  NodeTopologyDelete,
  NodeTopologyLayout,
  NodeTopologyStage,
  NodeTopologyUpdate,
  NodeTopologyWrite,
} from "../domain/node-topologies";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

const root = `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/node-topologies`;
const fallback = "未能确认节点编排操作结果，请刷新列表后重试。";
const fixedErrors: Record<string, string> = {
  "A topology cannot revisit or reuse the same server": "同一台服务器不能重复经过，已阻止回还环路。",
  "A node topology with this name already exists": "已有同名节点编排，请使用其他名称。",
  "Node topology changed; reload before saving": "节点编排已被其他操作修改，请刷新后重新编辑。",
  "Node topology changed; reload before deleting": "节点编排已被其他操作修改，请刷新后再删除。",
  "Topology name confirmation does not match": "确认名称与节点编排名称不一致。",
  "Topology external nodes must belong to one subscriber": "一条节点编排中的外部节点只能属于同一用户。",
  "Topology external node is unavailable": "编排中的外部节点已停用或不可用，请刷新候选节点。",
  "Topology external node configuration is unavailable": "编排中的外部节点配置不可用，请刷新外部订阅后重试。",
  "Topology external nodes belong to another assigned subscriber": "外部节点归属与已分配此编排的订阅用户不一致。",
  "Remove this topology from subscription plans before deleting it": "请先从订阅套餐中移除此编排，再执行删除。",
};

export class NodeTopologyRequestError extends Error {
  constructor(readonly status: number | null, message = fallback) { super(message); }
}

function invalid(): never { throw new NodeTopologyRequestError(null); }
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
function uuid(value: unknown) {
  return typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
    ? value : invalid();
}
function revision(value: unknown) {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value) ? value : invalid();
}
function instant(value: unknown) {
  return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value))
    ? value : invalid();
}
function license(value: unknown) { if (value !== false) invalid(); return false as const; }
function nil(value: unknown) { if (value !== null) invalid(); return null; }
function stage(value: unknown): NodeTopologyStage {
  const row = exact(value, ["node_ids", "load_balance_strategy"]);
  if (!Array.isArray(row.node_ids) || row.node_ids.length < 1 || row.node_ids.length > 16
    || row.load_balance_strategy !== "round-robin") invalid();
  return { node_ids: row.node_ids.map(uuid), load_balance_strategy: "round-robin" };
}
function layout(value: unknown): NodeTopologyLayout {
  const row = object(value);
  if (Object.keys(row).length > 128) invalid();
  return Object.fromEntries(Object.entries(row).map(([nodeId, point]) => {
    const coordinates = exact(point, ["x", "y"]);
    if (!Number.isFinite(coordinates.x) || !Number.isFinite(coordinates.y)
      || Math.abs(Number(coordinates.x)) > 100_000 || Math.abs(Number(coordinates.y)) > 100_000) invalid();
    return [uuid(nodeId), { x: Number(coordinates.x), y: Number(coordinates.y) }];
  }));
}
function candidate(value: unknown): NodeTopologyCandidate {
  const row = exact(value, [
    "id", "name", "kind", "protocol", "server_id", "server_name", "server_kind",
    "source_id", "source_name", "owner_username",
  ]);
  const base = { id: uuid(row.id), name: text(row.name, 160, true), protocol: text(row.protocol, 80, true) };
  if (row.kind === "managed") {
    if (!(["direct", "leased-line", "residential"] as unknown[]).includes(row.server_kind)) invalid();
    return {
      ...base, kind: "managed", server_id: uuid(row.server_id), server_name: text(row.server_name, 120, true),
      server_kind: row.server_kind as "direct" | "leased-line" | "residential",
      source_id: nil(row.source_id), source_name: nil(row.source_name), owner_username: nil(row.owner_username),
    };
  }
  if (row.kind === "external") {
    return {
      ...base, kind: "external", server_id: nil(row.server_id), server_name: nil(row.server_name),
      server_kind: nil(row.server_kind), source_id: uuid(row.source_id),
      source_name: text(row.source_name, 160, true), owner_username: text(row.owner_username, 80, true),
    };
  }
  return invalid();
}
function topology(value: unknown): NodeTopology {
  const row = exact(value, ["name", "enabled", "stages", "layout", "id", "revision", "created_at", "updated_at"]);
  if (typeof row.enabled !== "boolean" || !Array.isArray(row.stages)
    || row.stages.length < 2 || row.stages.length > 8) invalid();
  return {
    id: uuid(row.id), name: text(row.name, 120, true), enabled: row.enabled,
    stages: row.stages.map(stage), layout: layout(row.layout), revision: revision(row.revision),
    created_at: instant(row.created_at), updated_at: instant(row.updated_at),
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
    const fixed = fixedErrors[detail]
      ?? (detail.startsWith("Node topology not found:") ? "节点编排不存在，请刷新列表。" : null)
      ?? (detail.startsWith("Topology node not found:") ? "编排中的节点不存在，请刷新候选节点。" : null)
      ?? (detail.startsWith("Topology node identity is ambiguous:") ? "候选节点标识发生冲突，请刷新后重试。" : null)
      ?? (detail.startsWith("Topology node is not an active physical node:") ? "编排中的节点已停用或不可用。" : null)
      ?? (detail.startsWith("Topology node has no server:") ? "编排中的节点没有可用服务器。" : null);
    if (fixed) return new NodeTopologyRequestError(status, fixed);
  }
  return new NodeTopologyRequestError(status, requestError(detail, `${fallback}（${status}）`).message);
}
async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, fetcher: typeof fetch): Promise<T> {
  try {
    const response = await fetcher(root + path, {
      ...init,
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store",
    });
    const value = response.status === 204 ? null : await json(response);
    if (!response.ok) throw knownError(object(value).detail, response.status);
    return parse(value);
  } catch (failure) {
    if (failure instanceof NodeTopologyRequestError) throw failure;
    return invalid();
  }
}
function mutation(value: unknown) {
  const row = exact(value, ["topology", "license_required"]);
  license(row.license_required);
  return topology(row.topology);
}

export function listNodeTopologies(fetcher = authenticatedFetch) {
  return request("", {}, value => {
    const row = exact(value, ["topologies", "candidates", "license_required"]);
    if (!Array.isArray(row.topologies) || !Array.isArray(row.candidates)) invalid();
    license(row.license_required);
    return { topologies: row.topologies.map(topology), candidates: row.candidates.map(candidate), license_required: false } as NodeTopologiesResponse;
  }, fetcher);
}

export function createNodeTopology(payload: NodeTopologyWrite, fetcher = authenticatedFetch) {
  return request("", { method: "POST", body: JSON.stringify(payload) }, mutation, fetcher);
}

export function updateNodeTopology(id: string, payload: NodeTopologyUpdate, fetcher = authenticatedFetch) {
  return request(`/${encodeURIComponent(uuid(id))}`, { method: "PUT", body: JSON.stringify(payload) }, mutation, fetcher);
}

export function deleteNodeTopology(id: string, payload: NodeTopologyDelete, fetcher = authenticatedFetch) {
  return request(`/${encodeURIComponent(uuid(id))}`, { method: "DELETE", body: JSON.stringify(payload) },
    value => { if (value !== null) invalid(); }, fetcher);
}

export function nodeTopologyErrorMessage(failure: unknown) {
  return failure instanceof NodeTopologyRequestError ? failure.message : fallback;
}
