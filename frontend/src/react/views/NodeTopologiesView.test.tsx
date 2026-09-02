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
};
const revision = "a".repeat(64);
const candidates: NodeTopologyCandidate[] = [
  { id: ids.entrance, name: "东京入口", server_id: "61111111-1111-4111-8111-111111111111", server_name: "东京一号", server_kind: "direct", protocol: "vless" },
  { id: ids.relay, name: "香港中继", server_id: "62222222-2222-4222-8222-222222222222", server_name: "香港一号", server_kind: "leased-line", protocol: "vless" },
  { id: ids.sameServer, name: "香港备用", server_id: "62222222-2222-4222-8222-222222222222", server_name: "香港一号", server_kind: "leased-line", protocol: "trojan" },
  { id: ids.exit, name: "住宅出口", server_id: "64444444-4444-4444-8444-444444444444", server_name: "洛杉矶住宅", server_kind: "residential", protocol: "trojan" },
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

    fireEvent.click(screen.getByRole("button", { name: "添加 香港备用 为新一跳" })); await flush();
    expect(screen.getByText(/服务器“香港一号”已经经过/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "添加 东京入口 为新一跳" })); await flush();
    expect(screen.getByText("节点“东京入口”已经在编排中。")).toBeTruthy();

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
