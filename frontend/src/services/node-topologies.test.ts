import { describe, expect, it, vi } from "vitest";

import type { NodeTopology, NodeTopologyCandidate, NodeTopologyWrite } from "../domain/node-topologies";
import {
  createNodeTopology,
  deleteNodeTopology,
  listNodeTopologies,
  updateNodeTopology,
} from "./node-topologies";

const nodeId = "11111111-1111-4111-8111-111111111111";
const exitId = "22222222-2222-4222-8222-222222222222";
const topologyId = "33333333-3333-4333-8333-333333333333";
const revision = "a".repeat(64);
const now = "2026-09-02T00:00:00Z";
const candidates: NodeTopologyCandidate[] = [
  { id: nodeId, name: "东京入口", kind: "managed", server_id: "44444444-4444-4444-8444-444444444444", server_name: "东京", server_kind: "direct", source_id: null, source_name: null, owner_username: null, protocol: "vless" },
  { id: exitId, name: "洛杉矶出口", kind: "managed", server_id: "55555555-5555-4555-8555-555555555555", server_name: "洛杉矶", server_kind: "residential", source_id: null, source_name: null, owner_username: null, protocol: "trojan" },
  { id: "66666666-6666-4666-8666-666666666666", name: "外部中继", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "77777777-7777-4777-8777-777777777777", source_name: "Alice 订阅", owner_username: "alice", protocol: "ss" },
];
const draft: NodeTopologyWrite = {
  name: "跨境线路", enabled: true, layout: {}, stages: [
    { node_ids: [nodeId], load_balance_strategy: "round-robin" },
    { node_ids: [exitId], load_balance_strategy: "round-robin" },
  ],
};
const topology: NodeTopology = { id: topologyId, ...draft, revision, created_at: now, updated_at: now };
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), {
    status, headers: status === 204 ? undefined : { "Content-Type": "application/json" },
  }));
}

describe("node topologies service", () => {
  it("uses the exact list, create, update and guarded delete contracts", async () => {
    const queue = [
      { topologies: [topology], candidates, license_required: false },
      { topology, license_required: false },
      { topology: { ...topology, enabled: false, revision: "b".repeat(64) }, license_required: false },
      null,
    ];
    const fetcher = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      response(queue.shift(), init?.method === "DELETE" ? 204 : 200));
    expect((await listNodeTopologies(fetcher)).candidates).toEqual(candidates);
    await createNodeTopology(draft, fetcher);
    await updateNodeTopology(topologyId, { ...draft, enabled: false, expected_revision: revision }, fetcher);
    await deleteNodeTopology(topologyId, { expected_revision: revision, confirm_name: draft.name }, fetcher);

    expect(fetcher.mock.calls.map(call => [String(call[0]), call[1]?.method ?? "GET"])).toEqual([
      ["/api/v1/node-topologies", "GET"], ["/api/v1/node-topologies", "POST"],
      [`/api/v1/node-topologies/${topologyId}`, "PUT"], [`/api/v1/node-topologies/${topologyId}`, "DELETE"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(draft);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ ...draft, enabled: false, expected_revision: revision });
    expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ expected_revision: revision, confirm_name: draft.name });
  });

  it("shows known conflicts without echoing arbitrary backend details", async () => {
    await expect(createNodeTopology(draft, () => response({ detail: "A topology cannot revisit or reuse the same server" }, 409)))
      .rejects.toThrow("同一台服务器不能重复经过");
    const secret = "PRIVATE-UPSTREAM-DETAIL";
    await expect(createNodeTopology(draft, () => response({ detail: secret }, 500)))
      .rejects.toThrow("未能确认节点编排操作结果");
  });

  it("localizes external-node conflicts without echoing identifiers or provider details", async () => {
    await expect(createNodeTopology(draft, () => response({ detail: "Topology external nodes must belong to one subscriber" }, 409)))
      .rejects.toThrow("外部节点只能属于同一用户");
    await expect(createNodeTopology(draft, () => response({ detail: "Topology external node configuration is unavailable" }, 409)))
      .rejects.toThrow("外部节点配置不可用");
    await expect(createNodeTopology(draft, () => response({ detail: "Topology node identity is ambiguous: SECRET-ID" }, 409)))
      .rejects.toThrow("候选节点标识发生冲突");
  });

  it("rejects malformed success bodies", async () => {
    await expect(listNodeTopologies(() => response({ topologies: [topology], candidates, license_required: true })))
      .rejects.toThrow("未能确认节点编排操作结果");
  });

  it("rejects candidates whose discriminator and origin fields disagree", async () => {
    const mixed = { ...candidates[0], kind: "external" };
    await expect(listNodeTopologies(() => response({ topologies: [topology], candidates: [mixed], license_required: false })))
      .rejects.toThrow("未能确认节点编排操作结果");
  });
});
