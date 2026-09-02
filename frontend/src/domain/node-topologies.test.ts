import { describe, expect, it } from "vitest";

import type { NodeTopologyCandidate, NodeTopologyStage } from "./node-topologies";
import {
  insertTopologyCandidate,
  removeTopologyNode,
  reorderTopologyStage,
  validateTopologyDraft,
} from "./node-topologies";

const candidates: NodeTopologyCandidate[] = [
  { id: "node-1", name: "入口", kind: "managed", server_id: "server-1", server_name: "东京", server_kind: "direct", source_id: null, source_name: null, owner_username: null, protocol: "vless" },
  { id: "node-2", name: "中继", kind: "managed", server_id: "server-2", server_name: "香港", server_kind: "leased-line", source_id: null, source_name: null, owner_username: null, protocol: "vless" },
  { id: "node-3", name: "同机备用", kind: "managed", server_id: "server-2", server_name: "香港", server_kind: "leased-line", source_id: null, source_name: null, owner_username: null, protocol: "trojan" },
  { id: "node-4", name: "出口", kind: "managed", server_id: "server-4", server_name: "洛杉矶", server_kind: "residential", source_id: null, source_name: null, owner_username: null, protocol: "trojan" },
  { id: "external-a-1", name: "Alice 外部入口", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "source-a", source_name: "Alice 来源", owner_username: "alice", protocol: "vless" },
  { id: "external-a-2", name: "Alice 外部中继", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "source-a", source_name: "Alice 来源", owner_username: "alice", protocol: "trojan" },
  { id: "external-b-1", name: "Bob 外部出口", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "source-b", source_name: "Bob 来源", owner_username: "bob", protocol: "ss" },
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

  it("allows managed and same-owner external nodes but rejects mixed external owners", () => {
    expect(validateTopologyDraft("线路", stages(["node-1", "external-a-1"], ["external-a-2"]), candidates))
      .toEqual({ valid: true, errors: [] });
    expect(validateTopologyDraft("线路", stages(["external-a-1"], ["external-b-1"]), candidates).errors)
      .toContain("一条节点编排中的外部节点只能属于同一用户。");
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

  it("blocks a cross-owner external insertion without treating same-owner nodes as one server", () => {
    const original = stages(["external-a-1"], ["node-4"]);
    const sameOwner = insertTopologyCandidate(original, candidates[5], candidates, 0);
    expect(sameOwner.error).toBeNull();
    expect(sameOwner.stages[0].node_ids).toEqual(["external-a-1", "external-a-2"]);
    expect(insertTopologyCandidate(sameOwner.stages, candidates[6], candidates, null)).toEqual({
      stages: sameOwner.stages,
      error: "一条节点编排中的外部节点只能属于同一用户。",
    });
  });

  it("reorders stages and removes an empty hop without mutating input", () => {
    const original = stages(["node-1"], ["node-2"], ["node-4"]);
    expect(reorderTopologyStage(original, 2, 0).map(stage => stage.node_ids[0])).toEqual(["node-4", "node-1", "node-2"]);
    expect(removeTopologyNode(original, "node-2").map(stage => stage.node_ids[0])).toEqual(["node-1", "node-4"]);
    expect(original).toHaveLength(3);
  });
});
