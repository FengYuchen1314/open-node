import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";
import type { AgentCommand } from "../domain/inventory";
import type { ManagedNode, SubscriptionAccessResponse } from "../domain/subscriptions";

export type NodeOperation = "edit" | "remove";
export interface NodeSettings {
  name: string;
  tag: string | null;
  tags: string[];
  enabled: boolean;
  parent_id: string | null;
  target_node_id: string | null;
  client_template: Record<string, unknown>;
  config: Record<string, unknown>;
}
export interface NodeRemovalServer {
  server_id: string;
  server_name: string;
  inbound_tags: string[];
  outbound_tags: string[];
  retained_inbound_tags: string[];
  retained_outbound_tags: string[];
  phase: "withdrawing" | "preview" | "apply" | "inspect" | "completed";
  command_id: string | null;
  error: string | null;
  impact: Record<string, unknown> | null;
}
export interface NodeManagementRead {
  node: ManagedNode;
  revision: string;
  nodes: { id: string; name: string }[];
  plans: { id: string; name: string }[];
  credential_count: number;
  servers: NodeRemovalServer[];
  blockers: string[];
  warnings: string[];
  access: SubscriptionAccessResponse[];
}
export interface NodeManagementResult extends NodeManagementRead { commands: AgentCommand[] }
export interface NodeRemoval {
  id: string;
  node_id: string;
  name: string;
  node_ids: string[];
  status: "pending" | "failed" | "completed";
  servers: NodeRemovalServer[];
  requested_at: string;
  completed_at: string | null;
  warnings: string[];
  commands: AgentCommand[];
}
export function nodeSettings(node: ManagedNode): NodeSettings {
  return {
    name: node.name, tag: node.tag ?? null, tags: [...node.tags], enabled: node.enabled,
    parent_id: node.parent_id ?? null, target_node_id: node.target_node_id ?? null,
    config: structuredClone(node.config), client_template: structuredClone(node.client_template),
  };
}
export function parseNodeObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error(label + " 必须是有效的 JSON"); }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(label + " 必须是 JSON 对象");
  return parsed as Record<string, unknown>;
}
const base = import.meta.env.VITE_API_BASE_URL ?? "";
async function request<T>(path: string, init?: RequestInit, fetcher = authenticatedFetch): Promise<T> {
  const response = await fetcher(base + "/api/v1" + path, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    throw requestError(detail, `节点请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}
export function getNodeManagement(id: string, fetcher = authenticatedFetch) {
  return request<NodeManagementRead>(`/nodes/${encodeURIComponent(id)}/settings`, undefined, fetcher);
}
export function saveNode(id: string, settings: NodeSettings, revision: string, fetcher = authenticatedFetch) {
  return request<NodeManagementResult>(`/nodes/${encodeURIComponent(id)}/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...settings, expected_revision: revision, acknowledge_runtime_restart: true }),
  }, fetcher);
}
export function removeNode(id: string, revision: string, name: string, unmanaged: boolean, fetcher = authenticatedFetch) {
  return request<NodeRemoval>(`/nodes/${encodeURIComponent(id)}/remove`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: revision, confirm_name: name, acknowledge_runtime_restart: true, acknowledge_unmanaged_resources: unmanaged }),
  }, fetcher);
}
export function getNodeRemoval(id: string, fetcher = authenticatedFetch) {
  return request<NodeRemoval>(`/node-removals/${encodeURIComponent(id)}`, undefined, fetcher);
}
export function retryNodeRemoval(id: string, fetcher = authenticatedFetch) {
  return request<NodeRemoval>(`/node-removals/${encodeURIComponent(id)}/retry`, { method: "POST" }, fetcher);
}
