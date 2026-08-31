// @vitest-environment jsdom
import type { ReactNode } from "react";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { act, cleanup, fireEvent, render as renderTesting, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand, AgentCommandCreateResponse, XrayRuntimeInbound } from "../../domain/inventory";
import { newAutoSpeedRule } from "../../domain/auto-speed";
import { listServerCommands, queueAgentOperation } from "../../services/inventory";
import LimiterPanel from "./LimiterPanel";
import { installDom } from "../test-utils";

vi.setConfig({ testTimeout: 30000 });

vi.mock("../../services/inventory", () => ({ listServerCommands: vi.fn(), queueAgentOperation: vi.fn() }));
const revision = "a".repeat(64);
const inbound: XrayRuntimeInbound = { source_index: 0, tag: "vless-443", display_name: "VLESS", protocol: "vless", port: 443, client_count: 1, user_emails: ["alice@example.com", "bob@example.com"], sniffing_enabled: false, sniffing_dest_override: [], sniffing_exclude_domains: [], traffic: { uplink: 0, downlink: 0 }, user_traffic: { uplink: 0, downlink: 0 }, remarks: [] };
const state = () => ({ available: true, revision, inbounds: [{ inbound_tag: "vless-443", node_limit: 1250000, users: [{ uid: 7, email: "alice@example.com", speed_limit: 250000, device_limit: 2, conn_group: "household", auto_speed_rules: [newAutoSpeedRule()] }], auto_speed_rules: [newAutoSpeedRule()] }], conn_counts: { household: 3 }, user_speeds: { "alice@example.com": 125000 }, connection_rejections: { "alice@example.com": 4 }, automatic_limits: { "vless-443\0alice@example.com": { bytes_per_second: 625000, until: "2026-09-01T00:00:00Z" } } });
function command(body: unknown = state(), patch: Partial<AgentCommand> = {}): AgentCommand {
  return { id: "limiter-read", server_id: "edge", request_id: "r", method: "GET", path: "/api/child/limiter", query: "", timeout_ms: 30000, stream: false, status: "succeeded", attempts: 1, result_status: 200, result_body: body, created_at: "2026-08-31T03:00:00Z", updated_at: "2026-08-31T03:00:00Z", ...patch };
}
async function flush() { await act(async () => { for (let i = 0; i < 16; i += 1) await Promise.resolve(); }); }
function render(ui: ReactNode) { return renderTesting(ui, { wrapper: ({ children }) => <ConfigProvider locale={zhCN} theme={{ token: { motion: false } }}>{children}</ConfigProvider> }); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  installDom();
  vi.mocked(queueAgentOperation).mockResolvedValue({ command: command(), license_required: false });
  vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [command()], license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("React native limiter", () => {
  it("reads status and preserves byte rates, connection groups, rejection keys and user rules", async () => {
    const onCommands = vi.fn(); render(<LimiterPanel serverId="edge" inbounds={[inbound]} onCommands={onCommands} />); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "limiter_status", undefined);
    expect(screen.getByText("3 个活跃连接", { selector: ".ant-tag" })).toBeTruthy(); expect(screen.getByText("4 次拒绝")).toBeTruthy();
    expect(screen.getByText(/5.00 Mbps，截止/)).toBeTruthy();
    expect((screen.getByLabelText("每用户限速（Mbps）") as HTMLInputElement).value).toBe("10");
    fireEvent.change(screen.getByLabelText("每用户限速（Mbps）"), { target: { value: "8" } });
    const row = screen.getByLabelText("用户标识（email）").closest("tr")!;
    fireEvent.change(within(row).getByLabelText("限速值（Mbps）"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("连接组"), { target: { value: " family " } });
    const ruleForm = screen.getByLabelText("触发速度（Mbps）").closest("form");
    expect(ruleForm).toBe(screen.getByLabelText("每用户限速（Mbps）").closest("form"));
    fireEvent.submit(ruleForm!); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "limiter", { inbound_tag: "vless-443", expected_revision: revision, node_limit: 1000000, users: [{ uid: 7, email: "alice@example.com", speed_limit: 375000, device_limit: 2, conn_group: "family", auto_speed_rules: [newAutoSpeedRule()] }], auto_speed_rules: [newAutoSpeedRule()] });
    expect(screen.getByText("限制已应用。")).toBeTruthy(); expect(onCommands).toHaveBeenCalledWith("edge", [command()]);
  });
  it("requires remove confirmation and sends the exact revision without a sync payload", async () => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "移除限制" })); expect(queueAgentOperation).toHaveBeenCalledTimes(1);
    // rc-util uses the same test-only ID for Select and Modal; locate its actual title.
    const dialog = within(screen.getByText("移除限制？", { selector: ".ant-modal-title" }).closest('[role="dialog"]')!);
    fireEvent.click(dialog.getByRole("button", { name: "移除" })); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "limiter", { action: "remove", inbound_tag: "vless-443", expected_revision: revision });
    expect(screen.getByText("限制已移除。")).toBeTruthy();
  });
  it("rejects duplicate users and retains automatic rule validation", async () => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "添加限速用户" }));
    const emails = screen.getAllByLabelText("用户标识（email）"); expect((emails[1] as HTMLInputElement).value).toBe("bob@example.com");
    fireEvent.change(emails[1], { target: { value: "alice@example.com" } });
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(emails[1], { target: { value: "bob@example.com" } });
    fireEvent.change(screen.getByLabelText("触发速度（Mbps）"), { target: { value: "" } });
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    expect(queueAgentOperation).toHaveBeenCalledTimes(1);
  });
  it("does not interpret a blank rate or connection cap as unlimited", async () => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    fireEvent.change(screen.getByLabelText("每用户限速（Mbps）"), { target: { value: "" } });
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("每用户限速（Mbps）"), { target: { value: "0" } });
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("连接数"), { target: { value: "" } });
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it.each([
    ["每用户限速（Mbps）", "-1"], ["限速值（Mbps）", "-1"],
    ["连接数", "-1"], ["连接数", "0.4"], ["连接数", "1000001"],
  ])("never clamps invalid %s=%s into an accepted limit on blur or Enter", async (label, value) => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    const row = screen.getByLabelText("用户标识（email）").closest("tr")!;
    const input = label === "限速值（Mbps）" ? within(row).getByLabelText(label) : screen.getByLabelText(label);
    fireEvent.change(input, { target: { value } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    fireEvent.submit(input.closest("form")!); await flush();
    expect(queueAgentOperation).toHaveBeenCalledTimes(1);
    expect((input as HTMLInputElement).value).toBe(value);
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it.each(["-", ".", "not-a-number", "1e-999"])("rejects raw rate draft %s without restoring an old rate or unlimited zero", async (value) => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush(); const input = screen.getByLabelText("每用户限速（Mbps）");
    fireEvent.focus(input); fireEvent.keyDown(input, { key: value.at(-1) }); fireEvent.change(input, { target: { value } }); fireEvent.keyUp(input, { key: value.at(-1) });
    expect((input as HTMLInputElement).value).toBe(value);
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.blur(input);
    fireEvent.submit(input.closest("form")!); await flush();
    expect(queueAgentOperation).toHaveBeenCalledTimes(1);
    expect([value, ""]).toContain((input as HTMLInputElement).value);
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("keeps incomplete rate keystrokes invalid and accepts only an explicitly typed zero", async () => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush(); const input = screen.getByLabelText("每用户限速（Mbps）");
    fireEvent.focus(input);
    for (const [key, value] of [["Backspace", ""], ["-", "-"], ["1", "-1"], ["Backspace", ""]]) {
      fireEvent.keyDown(input, { key }); fireEvent.change(input, { target: { value } }); fireEvent.keyUp(input, { key });
      expect((input as HTMLInputElement).value).toBe(value);
      expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.change(input, { target: { value: "0" } }); fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.blur(input);
    fireEvent.submit(input.closest("form")!); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "limiter", expect.objectContaining({ node_limit: 0 }));
  });
  it("waits at the original 500ms cadence for the queued command", async () => {
    vi.mocked(listServerCommands).mockResolvedValueOnce({ server_id: "edge", commands: [command(null, { status: "pending" })], license_required: false });
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush(); expect(listServerCommands).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(499); }); expect(listServerCommands).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); }); await flush(); expect(listServerCommands).toHaveBeenCalledTimes(2);
    expect(screen.getByText("3 个活跃连接", { selector: ".ant-tag" })).toBeTruthy();
  });
  it.each([{ available: false, message: "Native limiter unavailable." }, { available: true, revision: "invalid", inbounds: [] }])("locks writes for unavailable or malformed snapshots: %j", async (snapshot) => {
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [command(snapshot)], license_required: false });
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    expect((screen.getByRole("button", { name: "保存限制" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "移除限制" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("surfaces revision conflicts without retrying a mutation", async () => {
    render(<LimiterPanel serverId="edge" inbounds={[inbound]} />); await flush();
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [command(null, { status: "failed", result_error: "Limiter revision conflict" })], license_required: false });
    fireEvent.click(screen.getByRole("button", { name: "保存限制" })); await flush();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy(); expect(document.body.textContent).not.toContain("Limiter revision conflict"); expect(queueAgentOperation).toHaveBeenCalledTimes(2);
  });
  it("discards queued work from a replaced server and stops polling on unmount", async () => {
    let resolve!: (response: AgentCommandCreateResponse) => void;
    vi.mocked(queueAgentOperation).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const onCommands = vi.fn(); const view = render(<LimiterPanel serverId="edge" inbounds={[inbound]} onCommands={onCommands} />); await flush();
    view.rerender(<LimiterPanel serverId="other" inbounds={[inbound]} onCommands={onCommands} />); await flush();
    await act(async () => { resolve({ command: command(), license_required: false }); }); await flush();
    expect(onCommands).not.toHaveBeenCalledWith("edge", expect.anything()); expect(onCommands).toHaveBeenCalledWith("other", expect.anything());
    view.unmount(); const calls = vi.mocked(listServerCommands).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); }); expect(listServerCommands).toHaveBeenCalledTimes(calls);
  });
});
