// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProbePayload } from "../../domain/probe";
import { getPublicProbePayload, getPublicProbeSeries, getPublicProbeTargets } from "../../services/probe-public";
import { flush, installDom, renderUi } from "../test-utils";
import ProbeView from "./ProbeView";

vi.mock("../../services/probe-public", () => ({
  getPublicProbePayload: vi.fn(), getPublicProbeSeries: vi.fn(), getPublicProbeTargets: vi.fn(),
  getPublicProbeStreamUrl: vi.fn(() => "wss://probe.example/api/public/probe-stream"),
}));
const payload: ProbePayload = {
  enabled: true, license_required: false, title: "Custom Status Title", description: "Original operator description",
  refresh_interval_sec: 60, require_access_token: true, show_return_route: true,
  servers: [
    { name: "Tokyo Edge", online: true, region: "Tokyo", region_country: "JP", cpu_pct: 20,
      mem_used: 1024, mem_total: 4096, upload_speed: 512, download_speed: 1024,
      ping: [{ label: "example.com", current_ms: 18, loss_pct: 0, buckets: [{ ms: 18, loss: 0 }] }],
      return_routes: [{ carrier: "telecom", route_type: "CN2 GIA", region: "Shanghai" }] },
    { name: "Offline Edge", online: false, region: "Berlin", region_country: "DE" },
  ],
};
beforeEach(() => {
  vi.resetAllMocks(); installDom(); vi.stubGlobal("WebSocket", undefined);
  vi.mocked(getPublicProbePayload).mockResolvedValue(payload);
  vi.mocked(getPublicProbeTargets).mockResolvedValue({ success: true, targets: [], license_required: false });
  vi.mocked(getPublicProbeSeries).mockResolvedValue({ success: true, series: null, all_series: [], license_required: false });
});
afterEach(async () => {
  cleanup();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function mount() { renderUi(<ProbeView publicOnly />); await flush(); }

describe("Chinese public probe", () => {
  it("localizes system labels but preserves operator titles, host names and route identifiers", async () => {
    await mount();
    expect(screen.getByRole("heading", { name: "Custom Status Title" })).toBeTruthy();
    expect(screen.getByText("Original operator description")).toBeTruthy();
    expect(screen.getByText("公共探针")).toBeTruthy();
    expect(screen.getByText("Tokyo Edge")).toBeTruthy();
    expect(screen.getByText("电信 CN2 GIA")).toBeTruthy();
    expect(screen.getByText(/100.*优秀/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Excellent|Critical|Public read-only view/);
    expect(screen.queryByText("探针设置")).toBeNull();
    expect(screen.queryByText("Worker 访问")).toBeNull();
    expect(screen.queryByText("定时探针")).toBeNull();
    expect(getPublicProbePayload).toHaveBeenCalledExactlyOnceWith(fetch, undefined);
    expect(getPublicProbeTargets).toHaveBeenCalledExactlyOnceWith("1h", fetch, undefined);
  });
  it("filters nodes through Chinese controls without changing the underlying status values", async () => {
    await mount();
    fireEvent.click(screen.getByRole("radio", { name: "在线 1" })); await flush();
    expect(screen.getByText("Tokyo Edge")).toBeTruthy();
    expect(screen.queryByText("Offline Edge")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "离线 1" })); await flush();
    expect(screen.queryByText("Tokyo Edge")).toBeNull();
    expect(screen.getByText("Offline Edge")).toBeTruthy();
    expect(payload.servers?.map(server => server.online)).toEqual([true, false]);
  });
  it("keeps history ranges and metrics in their original API form under Chinese labels", async () => {
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "查看 Tokyo Edge 的探针详情" })); await flush();
    const detail = within(screen.getByRole("dialog"));
    expect(detail.getByText("探针详情")).toBeTruthy();
    expect(getPublicProbeSeries).toHaveBeenLastCalledWith(0, { range: "1h", metric: "ping", all: true }, fetch, undefined);
    fireEvent.click(detail.getByRole("radio", { name: "系统" })); await flush();
    expect(getPublicProbeSeries).toHaveBeenLastCalledWith(0, { range: "1h", metric: "system", all: false }, fetch, undefined);
    fireEvent.click(detail.getByRole("radio", { name: "6 小时" })); await flush();
    expect(getPublicProbeSeries).toHaveBeenLastCalledWith(0, { range: "6h", metric: "system", all: false }, fetch, undefined);
    expect(detail.getByText("暂无系统指标样本。")).toBeTruthy();
  });
  it("renders Chinese empty states and does not echo an unknown upstream response", async () => {
    vi.mocked(getPublicProbePayload).mockRejectedValue(new Error("provider body https://example.test/?token=PRIVATE"));
    await mount();
    expect(screen.getByRole("heading", { name: "Open Node 探针" })).toBeTruthy();
    expect(screen.getByText("无法获取探针状态。")).toBeTruthy();
    expect(screen.getByText("暂无公开探针节点。")).toBeTruthy();
    expect(screen.getByText("暂无目标对比样本。")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/provider body|PRIVATE/);
  });
});
