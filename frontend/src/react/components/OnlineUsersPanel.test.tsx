// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OnlineCollectionStatus, ServerTelemetryResponse } from "../../domain/inventory";
import { authState } from "../../services/auth";
import { getLatestTelemetry } from "../../services/inventory";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import OnlineUsersPanel from "./OnlineUsersPanel";

vi.mock("../../services/inventory", () => ({ getLatestTelemetry: vi.fn() }));
function report(status: OnlineCollectionStatus = "ready", serverId = "edge"): ServerTelemetryResponse {
  return { server_id: serverId, license_required: false, latest: {
    id: "sample", server_id: serverId, received_at: new Date().toISOString(), reported_at: new Date().toISOString(),
    online_collection: { status, source: "xray_stats_api", received_at: new Date().toISOString(), expires_at: new Date(Date.now() + 90_000).toISOString() },
    online_users: { alice: ["198.51.100.2", "2001:db8::1"], bob: ["198.51.100.2"] }, user_speeds: {}, conn_counts: {}, latency: [],
  } };
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); installDom();
  authState.ready = true; authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "CSRF" };
  vi.mocked(getLatestTelemetry).mockResolvedValue(report());
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });

describe("online users", () => {
  it("shows user identities and unique IPs without equating IPs to devices", async () => {
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    expect(screen.getByText("2 个在线用户")).toBeTruthy(); expect(screen.getByText("2 个不同 IP")).toBeTruthy();
    expect(screen.getByText("2001:db8::1")).toBeTruthy(); expect(screen.getByText(/不能据此计算设备数量/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索在线用户或 IP"), { target: { value: "2001:db8" } });
    expect(screen.getByText("alice")).toBeTruthy(); expect(screen.queryByText("bob")).toBeNull();
  });
  it.each(["unknown", "unsupported", "not_configured", "error", "stopped", "stale"] as const)("does not show zero or old IPs for %s", async status => {
    vi.mocked(getLatestTelemetry).mockResolvedValue(report(status));
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    expect(screen.queryByText("2001:db8::1")).toBeNull(); expect(screen.queryByText("0 个在线用户")).toBeNull();
  });
  it("reports complete empty separately from a limited sample", async () => {
    const empty = report(); empty.latest!.online_users = {};
    vi.mocked(getLatestTelemetry).mockResolvedValue(empty);
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    expect(screen.getByText("0 个在线用户")).toBeTruthy(); expect(screen.getByText("暂无在线用户")).toBeTruthy();
    vi.mocked(getLatestTelemetry).mockResolvedValue(report("limited"));
    fireEvent.click(screen.getByRole("button", { name: "刷新在线用户" })); await flush();
    expect(screen.getByText("2 个已采样用户")).toBeTruthy(); expect(screen.getByText(/不是完整总数/)).toBeTruthy();
  });
  it("hides an expired sample even when a refresh remains pending", async () => {
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    vi.mocked(getLatestTelemetry).mockReturnValue(new Promise(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(91_000); });
    expect(screen.queryByText("2001:db8::1")).toBeNull(); expect(screen.getByText(/数据已过期/)).toBeTruthy();
    expect(getLatestTelemetry).toHaveBeenCalledTimes(2);
  });
  it("does not retain data on read errors", async () => {
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    vi.mocked(getLatestTelemetry).mockRejectedValue(new Error("private error"));
    fireEvent.click(screen.getByRole("button", { name: "刷新在线用户" })); await flush();
    expect(screen.queryByText("2001:db8::1")).toBeNull(); expect(screen.getByText(/无法读取在线报告/)).toBeTruthy();
    expect(screen.queryByText("private error")).toBeNull();
  });
  it("ignores a delayed response from the previous server", async () => {
    const old = deferred<ServerTelemetryResponse>(); vi.mocked(getLatestTelemetry).mockReturnValueOnce(old.promise);
    const view = renderUi(<OnlineUsersPanel serverId="old" />); await flush();
    const current = report("ready", "new"); current.latest!.online_users = { carol: ["203.0.113.1"] };
    vi.mocked(getLatestTelemetry).mockResolvedValue(current);
    view.rerender(<OnlineUsersPanel serverId="new" />); await flush();
    await act(async () => old.resolve(report("ready", "old")));
    expect(screen.getByText("carol")).toBeTruthy(); expect(screen.queryByText("alice")).toBeNull();
  });
  it("clears data on administrator logout and does not fetch while signed out", async () => {
    renderUi(<OnlineUsersPanel serverId="edge" />); await flush();
    await act(async () => { authState.session = null; });
    expect(screen.queryByText("2001:db8::1")).toBeNull(); expect(screen.getByText(/请登录管理员账户/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(getLatestTelemetry).toHaveBeenCalledTimes(1);
  });
});
