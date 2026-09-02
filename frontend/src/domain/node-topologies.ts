interface NodeTopologyCandidateBase {
  id: string;
  name: string;
  protocol: string;
}

export interface ManagedNodeTopologyCandidate extends NodeTopologyCandidateBase {
  kind: "managed";
  server_id: string;
  server_name: string;
  server_kind: "direct" | "leased-line" | "residential";
  source_id: null;
  source_name: null;
  owner_username: null;
}

export interface ExternalNodeTopologyCandidate extends NodeTopologyCandidateBase {
  kind: "external";
  server_id: null;
  server_name: null;
  server_kind: null;
  source_id: string;
  source_name: string;
  owner_username: string;
}

export type NodeTopologyCandidate = ManagedNodeTopologyCandidate | ExternalNodeTopologyCandidate;

export interface NodeTopologyPoint {
  x: number;
  y: number;
}

export interface NodeTopologyStage {
  node_ids: string[];
  load_balance_strategy: "round-robin";
}

export type NodeTopologyLayout = Record<string, NodeTopologyPoint>;

export interface NodeTopology {
  id: string;
  name: string;
  enabled: boolean;
  stages: NodeTopologyStage[];
  layout: NodeTopologyLayout;
  revision: string;
  created_at: string;
  updated_at: string;
}

export interface NodeTopologiesResponse {
  topologies: NodeTopology[];
  candidates: NodeTopologyCandidate[];
  license_required: false;
}

export interface NodeTopologyWrite {
  name: string;
  enabled: boolean;
  stages: NodeTopologyStage[];
  layout: NodeTopologyLayout;
}

export interface NodeTopologyUpdate extends NodeTopologyWrite {
  expected_revision: string;
}

export interface NodeTopologyDelete {
  expected_revision: string;
  confirm_name: string;
}

export interface TopologyDraftValidation {
  valid: boolean;
  errors: string[];
}

export interface TopologyDraftMutation {
  stages: NodeTopologyStage[];
  error: string | null;
}

export function copyTopologyStages(stages: NodeTopologyStage[]): NodeTopologyStage[] {
  return stages.map(stage => ({
    node_ids: [...stage.node_ids],
    load_balance_strategy: "round-robin",
  }));
}

function nodes(stages: NodeTopologyStage[]) {
  return stages.flatMap(stage => stage.node_ids);
}

export function validateTopologyDraft(
  name: string,
  stages: NodeTopologyStage[],
  candidates: NodeTopologyCandidate[],
): TopologyDraftValidation {
  const errors: string[] = [];
  const normalizedName = name.trim();
  if (!normalizedName) errors.push("请输入编排名称。");
  else if (normalizedName.length > 120) errors.push("编排名称不能超过 120 个字符。");
  if (stages.length < 2) errors.push("节点编排至少需要 2 跳。");
  if (stages.length > 8) errors.push("节点编排最多支持 8 跳。");
  stages.forEach((stage, index) => {
    if (!stage.node_ids.length) errors.push(`第 ${index + 1} 跳至少需要一个节点。`);
    if (stage.node_ids.length > 16) errors.push(`第 ${index + 1} 跳最多支持 16 个节点。`);
    if (stage.load_balance_strategy !== "round-robin") errors.push(`第 ${index + 1} 跳仅支持轮询负载均衡。`);
  });
  if (stages.length && stages.at(-1)!.node_ids.length !== 1) {
    errors.push("最终出口必须且只能包含一个节点。");
  }

  const byId = new Map(candidates.map(candidate => [candidate.id, candidate]));
  const nodeIds = nodes(stages);
  const seenNodes = new Set<string>();
  const seenServers = new Set<string>();
  const externalOwners = new Set<string>();
  for (const nodeId of nodeIds) {
    if (seenNodes.has(nodeId)) {
      errors.push("同一个节点不能在编排中重复出现。");
      break;
    }
    seenNodes.add(nodeId);
  }
  for (const nodeId of nodeIds) {
    const candidate = byId.get(nodeId);
    if (!candidate) {
      errors.push("编排包含已停用或不存在的节点，请移除后再保存。");
      continue;
    }
    if (candidate.kind === "managed") {
      if (seenServers.has(candidate.server_id)) {
        errors.push("同一台服务器不能重复经过，已阻止回还环路。");
        break;
      }
      seenServers.add(candidate.server_id);
    } else {
      externalOwners.add(candidate.owner_username);
    }
  }
  if (externalOwners.size > 1) errors.push("一条节点编排中的外部节点只能属于同一用户。");
  return { valid: errors.length === 0, errors: [...new Set(errors)] };
}

export function insertTopologyCandidate(
  stages: NodeTopologyStage[],
  candidate: NodeTopologyCandidate,
  candidates: NodeTopologyCandidate[],
  stageIndex: number | null,
): TopologyDraftMutation {
  const used = new Set(nodes(stages));
  if (used.has(candidate.id)) return { stages, error: `节点“${candidate.name}”已经在编排中。` };
  const byId = new Map(candidates.map(item => [item.id, item]));
  const usedCandidates = [...used].map(id => byId.get(id)).filter((item): item is NodeTopologyCandidate => Boolean(item));
  if (candidate.kind === "managed") {
    const reusedServer = usedCandidates.find(item => item.kind === "managed" && item.server_id === candidate.server_id);
    if (reusedServer) {
      return { stages, error: `服务器“${candidate.server_name}”已经经过，不能形成重复路径或回还环路。` };
    }
  } else {
    const externalOwners = new Set(usedCandidates.filter(item => item.kind === "external").map(item => item.owner_username));
    if (externalOwners.size && (!externalOwners.has(candidate.owner_username) || externalOwners.size > 1)) {
      return { stages, error: "一条节点编排中的外部节点只能属于同一用户。" };
    }
  }
  if (stageIndex === null) {
    if (stages.length >= 8) return { stages, error: "节点编排最多支持 8 跳。" };
    return { stages: [...copyTopologyStages(stages), { node_ids: [candidate.id], load_balance_strategy: "round-robin" }], error: null };
  }
  if (!Number.isInteger(stageIndex) || stageIndex < 0 || stageIndex >= stages.length) {
    return { stages, error: "目标跳数已经变化，请重新选择。" };
  }
  if (stages[stageIndex].node_ids.length >= 16) return { stages, error: "每一跳最多支持 16 个节点。" };
  const updated = copyTopologyStages(stages);
  updated[stageIndex].node_ids.push(candidate.id);
  return { stages: updated, error: null };
}

export function removeTopologyNode(stages: NodeTopologyStage[], nodeId: string): NodeTopologyStage[] {
  return stages
    .map(stage => ({ ...stage, node_ids: stage.node_ids.filter(id => id !== nodeId) }))
    .filter(stage => stage.node_ids.length > 0);
}

export function removeTopologyStage(stages: NodeTopologyStage[], index: number): NodeTopologyStage[] {
  return copyTopologyStages(stages).filter((_stage, stageIndex) => stageIndex !== index);
}

export function reorderTopologyStage(stages: NodeTopologyStage[], from: number, to: number): NodeTopologyStage[] {
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || to < 0
    || from >= stages.length || to >= stages.length || from === to) return copyTopologyStages(stages);
  const updated = copyTopologyStages(stages);
  const [moved] = updated.splice(from, 1);
  updated.splice(to, 0, moved);
  return updated;
}

export function topologyLayoutForStages(layout: NodeTopologyLayout, stages: NodeTopologyStage[]) {
  const selected = new Set(nodes(stages));
  return Object.fromEntries(Object.entries(layout).filter(([nodeId]) => selected.has(nodeId)));
}
