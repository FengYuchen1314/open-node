import { describe, expect, it } from "vitest";

import type { NodeTopologyCandidate, NodeTopologyStage } from "./node-topologies";
import {
  insertTopologyCandidate,
  removeTopologyNode,
  reorderTopologyStage,
  validateTopologyDraft,
} from "./node-topologies";

const candidates: NodeTopologyCandidate[] = [
  { id: "node-1", name: "入口", server_id: "server-1", server_name: "东京", server_kind: "direct", protocol: "vless" },
  { id: "node-2", name: "中继", server_id: "server-2", server_name: "香港", server_kind: "leased-line", protocol: "vless" },
  { id: "node-3", name: "同机备用", server_id: "server-2", server_name: "香港", server_kind: "leased-line", protocol: "trojan" },
  { id: "node-4", name: "出口", server_id: "server-4", server_name: "洛杉矶", server_kind: "residential", protocol: "trojan" },
];
const stages = (...ids: string[][]): NodeTopologyStage[] => ids.map(node_ids => ({ node_ids, load_balance_strategy: "round-robin" }));

describe("node topology draft", () => {
  it("requires two hops and one final exit", () => {
    expect(validateTopologyDraft("线路", stages(["node-1"]), candidates).errors).toContain("节点编排至少需要 2 跳。");
    expect(validateTopologyDraft("线路", stages(["node-1"], ["node-2", "node-4"]), candidates).errors)
      .toContain("最终出口必须且只能包含一个节点。");
    expect(validateTopologyDraft("线路", stages(["node-1", "node-2"], ["node-4"]), candidates)).toEqual({ valid: true, errors: [] });
  });

  it("rejects a duplicate node and every same-server revisit", () => {
    expect(validateTopologyDraft("线路", stages(["node-1"], ["node-1"]), candidates).errors)
      .toContain("同一个节点不能在编排中重复出现。");
    expect(validateTopologyDraft("线路", stages(["node-2"], ["node-3"]), candidates).errors)
      .toContain("同一台服务器不能重复经过，已阻止回还环路。");
  });

  it("blocks invalid insertion before changing the ordered stages", () => {
    const original = stages(["node-1"], ["node-2"]);
    expect(insertTopologyCandidate(original, candidates[0], candidates, null)).toEqual({ stages: original, error: "节点“入口”已经在编排中。" });
    expect(insertTopologyCandidate(original, candidates[2], candidates, 0).error).toContain("不能形成重复路径");
    const inserted = insertTopologyCandidate(original, candidates[3], candidates, 0);
    expect(inserted.error).toBeNull();
    expect(inserted.stages[0].node_ids).toEqual(["node-1", "node-4"]);
    expect(original[0].node_ids).toEqual(["node-1"]);
  });

  it("reorders stages and removes an empty hop without mutating input", () => {
    const original = stages(["node-1"], ["node-2"], ["node-4"]);
    expect(reorderTopologyStage(original, 2, 0).map(stage => stage.node_ids[0])).toEqual(["node-4", "node-1", "node-2"]);
    expect(removeTopologyNode(original, "node-2").map(stage => stage.node_ids[0])).toEqual(["node-1", "node-4"]);
    expect(original).toHaveLength(3);
  });
});
