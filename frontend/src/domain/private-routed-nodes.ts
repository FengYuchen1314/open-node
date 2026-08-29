import type { AgentCommand } from "./inventory";

export type PrivateRoutedNodeStatus = "provisioning" | "active" | "removing" | "failed";
export type PrivateRoutedNodeAction = "create" | "delete";

export interface PrivateRoutedPolicy {
  enabled: boolean;
  max_nodes: number;
  daily_limit: number;
  updated_at: string;
}

export interface PrivateRoutedCandidate {
  id: string;
  name: string;
  server_id: string;
  protocol: string;
  can_parent: boolean;
  can_target: boolean;
}

export interface PrivateRoutedNode {
  id: string;
  username: string;
  name: string;
  status: PrivateRoutedNodeStatus;
  action: PrivateRoutedNodeAction;
  server_id: string;
  protocol: string;
  parent_id: string;
  parent_name: string;
  target_node_id: string;
  target_name: string;
  change_set_id: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrivateRoutedNodesResponse {
  policy: PrivateRoutedPolicy;
  nodes: PrivateRoutedNode[];
  candidates: PrivateRoutedCandidate[];
  used_nodes: number;
  actions_today: number;
  license_required: false;
}

export interface PrivateRoutedNodeCreate {
  label: string;
  parent_id: string;
  target_node_id: string;
}

export interface PrivateRoutedNodeMutationResponse {
  node: PrivateRoutedNode | null;
  deleted_id: string | null;
  commands: AgentCommand[];
  license_required: false;
}
