// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NodeTopology, NodeTopologyCandidate } from "../../domain/node-topologies";
import {
  createNodeTopology,
  deleteNodeTopology,
  listNodeTopologies,
  updateNodeTopology,
} from "../../services/node-topologies";
import { flush, installDom, renderUi } from "../test-utils";
import NodeTopologiesView from "./NodeTopologiesView";

vi.mock("../../services/node-topologies", async original => ({
  ...await original<typeof import("../../services/node-topologies")>(),
  createNodeTopology: vi.fn(), deleteNodeTopology: vi.fn(), listNodeTopologies: vi.fn(), updateNodeTopology: vi.fn(),
}));
vi.setConfig({ testTimeout: 30000 });

const ids = {
  entrance: "11111111-1111-4111-8111-111111111111",
  relay: "22222222-2222-4222-8222-222222222222",
  sameServer: "33333333-3333-4333-8333-333333333333",
  exit: "44444444-4444-4444-8444-444444444444",
  topology: "55555555-5555-4555-8555-555555555555",
  externalAlice: "71111111-1111-4111-8111-111111111111",
  externalAliceRelay: "72222222-2222-4222-8222-222222222222",
  externalBob: "73333333-3333-4333-8333-333333333333",
};
const revision = "a".repeat(64);
const candidates: NodeTopologyCandidate[] = [
  { id: ids.entrance, name: "东京入口", kind: "managed", server_id: "61111111-1111-4111-8111-111111111111", server_name: "东京一号", server_kind: "direct", source_id: null, source_name: null, owner_username: null, protocol: "vless" },
  { id: ids.relay, name: "香港中继", kind: "managed", server_id: "62222222-2222-4222-8222-222222222222", server_name: "香港一号", server_kind: "leased-line", source_id: null, source_name: null, owner_username: null, protocol: "vless" },
  { id: ids.sameServer, name: "香港备用", kind: "managed", server_id: "62222222-2222-4222-8222-222222222222", server_name: "香港一号", server_kind: "leased-line", source_id: null, source_name: null, owner_username: null, protocol: "trojan" },
  { id: ids.exit, name: "住宅出口", kind: "managed", server_id: "64444444-4444-4444-8444-444444444444", server_name: "洛杉矶住宅", server_kind: "residential", source_id: null, source_name: null, owner_username: null, protocol: "trojan" },
  { id: ids.externalAlice, name: "Alice 外部入口", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "81111111-1111-4111-8111-111111111111", source_name: "Alice 主线路", owner_username: "alice", protocol: "vless" },
  { id: ids.externalAliceRelay, name: "Alice 外部中继", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "81111111-1111-4111-8111-111111111111", source_name: "Alice 主线路", owner_username: "alice", protocol: "trojan" },
  { id: ids.externalBob, name: "Bob 外部出口", kind: "external", server_id: null, server_name: null, server_kind: null, source_id: "82222222-2222-4222-8222-222222222222", source_name: "Bob 主线路", owner_username: "bob", protocol: "ss" },
];
const existing: NodeTopology = {
  id: ids.topology, name: "现有线路", enabled: true, revision, created_at: "2026-09-02T00:00:00Z", updated_at: "2026-09-02T00:00:00Z", layout: {},
  stages: [
    { node_ids: [ids.entrance], load_balance_strategy: "round-robin" },
    { node_ids: [ids.exit], load_balance_strategy: "round-robin" },
  ],
};

function transfer() {
  const values = new Map<string, string>();
  return {
    effectAllowed: "none",
    setData(type: string, value: string) { values.set(type, value); },
    getData(type: string) { return values.get(type) ?? ""; },
  } as unknown as DataTransfer;
}

async function mount(rows: NodeTopology[] = []) {
  vi.mocked(listNodeTopologies).mockResolvedValue({ topologies: rows, candidates, license_required: false });
  const view = renderUi(<NodeTopologiesView />); await flush(); return view;
}

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(createNodeTopology).mockImplementation(async payload => ({
    id: ids.topology, ...payload, revision, created_at: "2026-09-02T00:00:00Z", updated_at: "2026-09-02T00:00:00Z",
  }));
  vi.mocked(updateNodeTopology).mockImplementation(async (id, payload) => ({
    id, name: payload.name, enabled: payload.enabled, stages: payload.stages, layout: payload.layout,
    revision: "b".repeat(64), created_at: existing.created_at, updated_at: "2026-09-02T01:00:00Z",
  }));
  vi.mocked(deleteNodeTopology).mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("graphical node topology editor", () => {
  it("builds an ordered load-balanced route by drag and drop and blocks loops immediately", async () => {
    await mount();
    expect(screen.getByRole("heading", { name: "节点编排" })).toBeTruthy();
    expect(screen.getByText("节点编排至少需要 2 跳。")).toBeTruthy();

    const dropZone = screen.getByTestId("topology-new-stage-drop");
    for (const id of [ids.entrance, ids.relay]) {
      const data = transfer();
      fireEvent.dragStart(screen.getByTestId(`candidate-${id}`), { dataTransfer: data });
      fireEvent.drop(dropZone, { dataTransfer: data }); await flush();
    }
    expect(screen.getByTestId("topology-stage-0")).toBeTruthy();
    expect(screen.getByTestId("topology-stage-1")).toBeTruthy();

    expect((screen.getByRole("button", { name: "添加 香港备用 为新一跳" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("同服务器已经过")).toBeTruthy();
    expect((screen.getByRole("button", { name: "添加 东京入口 为新一跳" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId("topology-stage-0"));
    fireEvent.click(screen.getByRole("button", { name: "加入当前跳 住宅出口" })); await flush();
    expect(screen.getByText("轮询负载均衡 · 2 节点")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("编排名称"), { target: { value: "  全球优化线路  " } }); await flush();
    expect(screen.getByText("线路结构有效：可保存。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建编排" })); await flush();
    expect(createNodeTopology).toHaveBeenCalledExactlyOnceWith({
      name: "全球优化线路", enabled: true, layout: {}, stages: [
        { node_ids: [ids.entrance, ids.exit], load_balance_strategy: "round-robin" },
        { node_ids: [ids.relay], load_balance_strategy: "round-robin" },
      ],
    });
    expect(screen.getByText(/节点编排已创建/)).toBeTruthy();
  });

  it("shows external source ownership and disables candidates belonging to another owner", async () => {
    await mount();
    const alice = within(screen.getByTestId(`candidate-${ids.externalAlice}`));
    expect(alice.getByText("来源：Alice 主线路 · VLESS")).toBeTruthy();
    expect(alice.getByText("所属用户：alice")).toBeTruthy();
    expect(alice.getByText("外部")).toBeTruthy();

    fireEvent.click(alice.getByRole("button", { name: "添加 Alice 外部入口 为新一跳" })); await flush();
    const bob = within(screen.getByTestId(`candidate-${ids.externalBob}`));
    expect((bob.getByRole("button", { name: "添加 Bob 外部出口 为新一跳" }) as HTMLButtonElement).disabled).toBe(true);
    expect(bob.getByText("外部节点属于其他用户")).toBeTruthy();
    const sameOwner = within(screen.getByTestId(`candidate-${ids.externalAliceRelay}`));
    expect((sameOwner.getByRole("button", { name: "添加 Alice 外部中继 为新一跳" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(sameOwner.getByRole("button", { name: "添加 Alice 外部中继 为新一跳" })); await flush();
    expect(within(screen.getByTestId("topology-stage-1")).getByText("来源：Alice 主线路 · 用户：alice · TROJAN")).toBeTruthy();
  });

  it("updates with the loaded revision and deletes only after an exact name confirmation", async () => {
    await mount([existing]);
    fireEvent.click(screen.getByRole("button", { name: "编辑 现有线路" })); await flush();
    fireEvent.change(screen.getByLabelText("编排名称"), { target: { value: "更新线路" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" })); await flush();
    expect(updateNodeTopology).toHaveBeenCalledExactlyOnceWith(ids.topology, {
      name: "更新线路", enabled: true, stages: existing.stages, layout: {}, expected_revision: revision,
    });

    fireEvent.click(screen.getByRole("button", { name: "删除 更新线路" })); await flush();
    const dialog = within(screen.getByRole("dialog"));
    expect((dialog.getByRole("button", { name: "确认删除" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(dialog.getByLabelText("删除确认名称"), { target: { value: "更新线路" } });
    fireEvent.click(dialog.getByRole("button", { name: "确认删除" })); await flush();
    expect(deleteNodeTopology).toHaveBeenCalledExactlyOnceWith(ids.topology, {
      expected_revision: "b".repeat(64), confirm_name: "更新线路",
    });
    expect(screen.getByText("节点编排已删除。")).toBeTruthy();
  });

});
