// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PrivateRoutedNode, PrivateRoutedNodesResponse } from "../../domain/private-routed-nodes";
import { createSubscriberPrivateRoute, deleteSubscriberPrivateRoute, listSubscriberPrivateRoutes } from "../../services/private-routed-nodes";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import PrivateRoutedNodesPanel from "./PrivateRoutedNodesPanel";

vi.mock("../../services/private-routed-nodes", () => ({ createSubscriberPrivateRoute: vi.fn(), deleteSubscriberPrivateRoute: vi.fn(), listSubscriberPrivateRoutes: vi.fn() }));
const node: PrivateRoutedNode = { id: "private-one", username: "alice", name: "Private exit", status: "active", action: "create", server_id: "entry", protocol: "vless", parent_id: "node-entry", parent_name: "Entry", target_node_id: "node-exit", target_name: "Exit", change_set_id: null, last_error: null, created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z" };
const state: PrivateRoutedNodesResponse = { policy: { enabled: true, max_nodes: 2, daily_limit: 5, updated_at: "2026-08-31T00:00:00Z" }, nodes: [node], used_nodes: 1, actions_today: 1, license_required: false, candidates: [
  { id: "node-entry", name: "Entry choice", server_id: "entry", protocol: "vless", can_parent: true, can_target: true },
  { id: "node-exit", name: "Exit choice", server_id: "exit", protocol: "vless", can_parent: false, can_target: true },
] };
beforeEach(() => { vi.resetAllMocks(); installDom(); vi.mocked(listSubscriberPrivateRoutes).mockResolvedValue(state); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("React private routed nodes", () => {
  it("does not offer creation until a loaded policy explicitly enables it", async () => {
    const pending = deferred<PrivateRoutedNodesResponse>(); vi.mocked(listSubscriberPrivateRoutes).mockReturnValue(pending.promise);
    renderUi(<PrivateRoutedNodesPanel />);
    expect(screen.queryByRole("button", { name: "创建" })).toBeNull();
    await act(async () => pending.resolve({ ...state, policy: { ...state.policy, enabled: false } }));
    expect(screen.getByText("私有路由已停用。")).toBeTruthy();
    expect(createSubscriberPrivateRoute).not.toHaveBeenCalled();
  });
  it("requires a second explicit delete action and blocks duplicate destructive requests", async () => {
    const pending = deferred<Awaited<ReturnType<typeof deleteSubscriberPrivateRoute>>>();
    vi.mocked(deleteSubscriberPrivateRoute).mockReturnValue(pending.promise);
    renderUi(<PrivateRoutedNodesPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "删除私有路由 Private exit" }));
    expect(deleteSubscriberPrivateRoute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消删除私有路由" }));
    expect(deleteSubscriberPrivateRoute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除私有路由 Private exit" }));
    const confirm = screen.getByRole("button", { name: "确认" }); fireEvent.click(confirm); fireEvent.click(confirm); await flush();
    expect(deleteSubscriberPrivateRoute).toHaveBeenCalledExactlyOnceWith(node.id);
    await act(async () => pending.reject(new Error("路由移除被拒绝")));
    expect(screen.getByText("路由移除被拒绝")).toBeTruthy();
    expect(screen.getByText(node.name)).toBeTruthy();
  });
  it("continues bounded polling after an intermediate failure and stops when deployment settles", async () => {
    vi.useFakeTimers();
    vi.mocked(listSubscriberPrivateRoutes)
      .mockResolvedValueOnce({ ...state, nodes: [{ ...node, status: "provisioning" }] })
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValue(state);
    renderUi(<PrivateRoutedNodesPanel />); await flush();
    expect(listSubscriberPrivateRoutes).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("网络暂时不可用")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(listSubscriberPrivateRoutes).toHaveBeenCalledTimes(3);
    expect(screen.getByText("有效")).toBeTruthy();
    expect(screen.queryByText("网络暂时不可用")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(listSubscriberPrivateRoutes).toHaveBeenCalledTimes(3);
  });
  it("never renders a previous user's late route response after disposal", async () => {
    const pending = deferred<PrivateRoutedNodesResponse>(); vi.mocked(listSubscriberPrivateRoutes).mockReturnValue(pending.promise);
    const view = renderUi(<PrivateRoutedNodesPanel />); view.unmount();
    await act(async () => pending.resolve(state));
    expect(screen.queryByText(node.name)).toBeNull();
  });
});
