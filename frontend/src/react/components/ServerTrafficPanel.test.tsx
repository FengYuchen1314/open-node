// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerSummary, ServerTraffic } from "../../domain/inventory";
import { getServerTraffic, resetServerTraffic, updateServerTraffic } from "../../services/inventory";
import ServerTrafficPanel from "./ServerTrafficPanel";
vi.mock("../../services/inventory", () => ({ getServerTraffic: vi.fn(), resetServerTraffic: vi.fn(), updateServerTraffic: vi.fn() }));
const server: ServerSummary = { id: "edge", name: "Edge", status: "connected", connection_mode: "http", listen_port: 0, pull_port: 0,
  ipv6_enabled: true, traffic_limit: 1024 ** 3, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0,
  created_at: "2026-08-31", updated_at: "2026-08-31" };
const traffic: ServerTraffic = { server_id: "edge", traffic_limit: 1024 ** 3, traffic_reset_day: 15, traffic_source: "xray", traffic_stats_mode: "both",
  upload: 512, download: 512, used: 1024, cumulative_upload: 10240, cumulative_download: 10240,
  last_reported_at: "2026-08-31T03:00:00Z", last_reset_at: "2026-08-15T00:00:00Z", next_reset_at: "2026-09-15T00:00:00Z", license_required: false };
async function flush() { await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); }); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(getServerTraffic).mockResolvedValue(traffic); vi.mocked(updateServerTraffic).mockResolvedValue(traffic);
  vi.mocked(resetServerTraffic).mockResolvedValue({ ...traffic, used: 0, upload: 0, download: 0 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("React server traffic settings", () => {
  it("loads counts and UTC reset dates and converts GiB quotas to bytes", async () => {
    render(<ServerTrafficPanel servers={[server]} />); await flush();
    expect(screen.getByTestId("server-traffic-used").textContent).toBe("1.00 KiB"); expect(screen.getByText("2026/09/15 00:00 UTC")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("流量限额（GiB，0 表示不限额）"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(updateServerTraffic).toHaveBeenCalledWith("edge", { traffic_limit: 2.5 * 1024 ** 3, traffic_reset_day: 15, traffic_source: "xray", traffic_stats_mode: "both" });
  });
  it("rejects blank and unsafe quotas without issuing a mutation", async () => {
    render(<ServerTrafficPanel servers={[server]} />); await flush();
    const quota = screen.getByLabelText("流量限额（GiB，0 表示不限额）");
    fireEvent.change(quota, { target: { value: "" } }); expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(quota, { target: { value: "9007199254740991" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); expect(updateServerTraffic).not.toHaveBeenCalled();
  });
  it("polls counters without replacing unsaved quota edits and stops on unmount", async () => {
    const { unmount } = render(<ServerTrafficPanel servers={[server]} />); await flush();
    fireEvent.change(screen.getByLabelText("流量限额（GiB，0 表示不限额）"), { target: { value: "3" } });
    vi.mocked(getServerTraffic).mockResolvedValue({ ...traffic, used: 2048 });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.getByTestId("server-traffic-used").textContent).toBe("2.00 KiB");
    expect((screen.getByLabelText("流量限额（GiB，0 表示不限额）") as HTMLInputElement).value).toBe("3");
    unmount(); const calls = vi.mocked(getServerTraffic).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); }); expect(getServerTraffic).toHaveBeenCalledTimes(calls);
  });
  it("requires an explicit cycle-reset dialog and preserves historical-counter warning", async () => {
    render(<ServerTrafficPanel servers={[server]} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "重置周期" }));
    expect(resetServerTraffic).not.toHaveBeenCalled(); expect(screen.getByText(/历史计数和用户流量限额保持不变/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重置" })); await flush();
    expect(resetServerTraffic).toHaveBeenCalledWith("edge"); expect(screen.getByTestId("server-traffic-used").textContent).toBe("0 B");
  });
  it("ignores an old target response and closes its reset confirmation on replacement", async () => {
    let resolve!: (value: ServerTraffic) => void;
    vi.mocked(getServerTraffic).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { rerender } = render(<ServerTrafficPanel servers={[server]} />); await flush();
    vi.mocked(getServerTraffic).mockResolvedValue({ ...traffic, server_id: "other", used: 4096 });
    rerender(<ServerTrafficPanel servers={[{ ...server, id: "other", name: "Other" }]} />); await flush();
    await act(async () => { resolve(traffic); }); expect(screen.getByTestId("server-traffic-used").textContent).toBe("4.00 KiB");
    fireEvent.click(screen.getByRole("button", { name: "重置周期" }));
    rerender(<ServerTrafficPanel servers={[server]} />); await flush();
    expect(screen.queryByRole("dialog", { name: "重置服务器流量？" })).toBeNull();
  });
  it("shows failed saves without discarding draft input", async () => {
    vi.mocked(updateServerTraffic).mockRejectedValue(new Error("Traffic revision rejected")); render(<ServerTrafficPanel servers={[server]} />); await flush();
    fireEvent.change(screen.getByLabelText("流量限额（GiB，0 表示不限额）"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy(); expect(document.body.textContent).not.toContain("Traffic revision rejected"); expect((screen.getByLabelText("流量限额（GiB，0 表示不限额）") as HTMLInputElement).value).toBe("4");
  });
  it.each(["blur", "Enter"] as const)("does not coerce invalid or sub-byte quotas into unlimited after %s", async finish => {
    render(<ServerTrafficPanel servers={[server]} />); await flush();
    const input = screen.getByLabelText("流量限额（GiB，0 表示不限额）") as HTMLInputElement;
    const save = screen.getByRole("button", { name: "保存" }) as HTMLButtonElement;
    for (const value of ["", "-1", "-", "$1", "0x10", "1e", "1e999", "1e-999", "0.00000000001", "9007199254740991", " "]) {
      fireEvent.focus(input); fireEvent.keyDown(input, { key: "1" }); fireEvent.change(input, { target: { value } }); fireEvent.keyUp(input, { key: "1" });
      if (finish === "blur") fireEvent.blur(input); else fireEvent.keyDown(input, { key: "Enter" });
      expect(save.disabled).toBe(true); fireEvent.click(save);
      fireEvent.submit(input.closest("form")!); await flush();
      expect(updateServerTraffic).not.toHaveBeenCalled();
    }
    fireEvent.change(input, { target: { value: "0" } }); fireEvent.keyDown(input, { key: "Enter" });
    expect(save.disabled).toBe(false); fireEvent.click(save); await flush();
    expect(updateServerTraffic).toHaveBeenCalledWith("edge", expect.objectContaining({ traffic_limit: 0 }));
  });
  it("keeps legitimate fractional GiB quotas through both blur and Enter", async () => {
    render(<ServerTrafficPanel servers={[server]} />); await flush();
    const input = screen.getByLabelText("流量限额（GiB，0 表示不限额）") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2.5" } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
    expect(input.value).toBe("2.5"); fireEvent.submit(input.closest("form")!); await flush();
    expect(updateServerTraffic).toHaveBeenCalledWith("edge", expect.objectContaining({ traffic_limit: 2.5 * 1024 ** 3 }));
  });
});
