// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand } from "../../domain/inventory";
import { listServerCommands, queueAgentOperation } from "../../services/inventory";
import AgentLifecycleDialog, { type AgentLifecycleAction } from "./AgentLifecycleDialog";
vi.mock("../../services/inventory", () => ({ listServerCommands: vi.fn(), queueAgentOperation: vi.fn() }));
const host = { enabled: true, installation_status: "installed", recovery_required: false, release_base_url: "https://github.com/example/releases",
  current: { version: "0.3.0a0", sha256: "a".repeat(64) }, previous: { version: "0.2.0", sha256: "b".repeat(64) }, jobs: [] as { status: string }[] };
function command(overrides: Partial<AgentCommand> = {}): AgentCommand {
  return { id: "cmd", server_id: "edge", request_id: "request", method: "GET", path: "/api/child/agent/lifecycle", query: "", timeout_ms: 30000,
    stream: false, status: "succeeded", attempts: 1, created_at: "2026-08-31", updated_at: "2026-08-31", result_body: host, ...overrides };
}
async function flush() { await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); }); }
async function tick(ms = 1000) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [], license_required: false });
  vi.mocked(queueAgentOperation).mockResolvedValue({ command: command(), license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
function mount(action: AgentLifecycleAction = "agent_upgrade") {
  const props = { open: true, serverId: "edge", serverName: "Edge", action, onOpenChange: vi.fn(), onUpdated: vi.fn() };
  return { ...render(<AgentLifecycleDialog {...props} />), props };
}
function fillUpgrade() {
  fireEvent.click(screen.getByRole("checkbox", { name: "确认重启 Agent" }));
}
describe("React Agent lifecycle", () => {
  it("reads host status and lets the panel choose the verified upgrade release", async () => {
    mount(); await flush(); expect(queueAgentOperation).toHaveBeenCalledWith("edge", "agent_lifecycle");
    expect((screen.getByRole("button", { name: "升级" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "确认重启 Agent" }));
    vi.mocked(queueAgentOperation).mockResolvedValue({ command: command({ path: "/api/child/agent/upgrade" }), license_required: false });
    fireEvent.click(screen.getByRole("button", { name: "升级" })); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_upgrade", { confirm: true });
    expect(screen.getByText("已完成")).toBeTruthy();
  });
  it("observes an existing pending operation instead of creating a duplicate", async () => {
    const pending = command({ path: "/api/child/agent/rollback", status: "pending", result_body: null });
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [pending], license_required: false });
    const { props } = mount(); await flush(); expect(queueAgentOperation).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "回退 Agent" })).toBeTruthy(); expect(screen.getByText("排队中")).toBeTruthy();
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [{ ...pending, status: "succeeded", result_body: host }], license_required: false });
    await tick(); expect(screen.getByText("已完成")).toBeTruthy(); expect(props.onUpdated).toHaveBeenCalledOnce();
  });
  it.each([
    ["disabled", { ...host, enabled: false }], ["recovery", { ...host, recovery_required: true }],
    ["running job", { ...host, jobs: [{ status: "running" }] }], ["removed", { ...host, installation_status: "removed" }],
  ])("fails closed for a host with %s state", async (_label, next) => {
    vi.mocked(queueAgentOperation).mockResolvedValue({ command: command({ result_body: next }), license_required: false }); mount(); await flush();
    if (screen.queryByRole("checkbox", { name: "确认重启 Agent" })) fillUpgrade();
    const button = screen.queryByRole("button", { name: "升级" });
    expect(!button || (button as HTMLButtonElement).disabled).toBe(true); expect(queueAgentOperation).toHaveBeenCalledTimes(1);
  });
  it("requires a previous release to roll back", async () => {
    vi.mocked(queueAgentOperation).mockResolvedValue({ command: command({ result_body: { ...host, previous: null } }), license_required: false });
    mount("agent_rollback"); await flush(); fireEvent.click(screen.getByRole("checkbox", { name: "确认重启 Agent" }));
    expect((screen.getByRole("button", { name: "回退" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("clears pending observation timers and discards late results after close", async () => {
    const pending = command({ path: "/api/child/agent/uninstall", status: "leased", result_body: null });
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [pending], license_required: false });
    const { props, rerender } = mount(); await flush();
    let resolve!: (value: Awaited<ReturnType<typeof listServerCommands>>) => void;
    vi.mocked(listServerCommands).mockReturnValueOnce(new Promise(done => { resolve = done; })); await tick();
    rerender(<AgentLifecycleDialog {...props} open={false} />);
    await act(async () => { resolve({ server_id: "edge", commands: [{ ...pending, status: "succeeded" }], license_required: false }); });
    await tick(10000); expect(props.onUpdated).not.toHaveBeenCalled(); expect(listServerCommands).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("已完成")).toBeNull();
  });
  it("blocks duplicate upgrade submission while a request is in flight", async () => {
    mount(); await flush(); fillUpgrade();
    let resolve!: (value: Awaited<ReturnType<typeof queueAgentOperation>>) => void;
    vi.mocked(queueAgentOperation).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const button = screen.getByRole("button", { name: "升级" }); fireEvent.click(button); fireEvent.click(button); await flush();
    expect(queueAgentOperation).toHaveBeenCalledTimes(2);
    await act(async () => { resolve({ command: command({ path: "/api/child/agent/upgrade" }), license_required: false }); });
    expect(screen.getByText("已完成")).toBeTruthy();
  });
  it("requires explicit removal confirmation and does not offer host refresh after uninstall", async () => {
    mount("agent_uninstall"); await flush();
    expect((screen.getByRole("button", { name: "卸载" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "确认卸载 Agent" }));
    vi.mocked(queueAgentOperation).mockResolvedValue({ command: command({ path: "/api/child/agent/uninstall", result_body: { ...host, installation_status: "removed" } }), license_required: false });
    fireEvent.click(screen.getByRole("button", { name: "卸载" })); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_uninstall", { confirm: true });
    expect(screen.queryByRole("button", { name: "刷新 Agent 状态" })).toBeNull();
  });
  it("reports a missing operation without submitting another mutation", async () => {
    vi.mocked(listServerCommands).mockResolvedValueOnce({ server_id: "edge", commands: [command({ status: "pending", path: "/api/child/agent/upgrade" })], license_required: false });
    mount(); await flush(); await tick(); expect(screen.getByText("Agent 命令已不可用。")).toBeTruthy();
    expect(queueAgentOperation).not.toHaveBeenCalled();
  });
});
