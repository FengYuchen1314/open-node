// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentChangeSet, AgentChangeSetsResponse } from "../../domain/changes";
import type { AgentCommand, ServerSummary } from "../../domain/inventory";
import { listServers } from "../../services/inventory";
import { acceptChangeSet, createChangeSet, createRoutedOutboundChangeSet, dispatchChangeSet, listChangeSets, rollbackChangeSet } from "../../services/changes";
import ChangesView from "./ChangesView";
import { installDom, renderUi as render } from "../test-utils";

vi.setConfig({ testTimeout: 30000 });

vi.mock("../../services/inventory", () => ({ listServers: vi.fn() }));
vi.mock("../../services/changes", () => ({ acceptChangeSet: vi.fn(), createChangeSet: vi.fn(), createRoutedOutboundChangeSet: vi.fn(), dispatchChangeSet: vi.fn(), listChangeSets: vi.fn(), rollbackChangeSet: vi.fn() }));
vi.mock("../components/CommandInspector", () => ({ default: ({ commands }: { commands: AgentCommand[] }) => <div data-testid="commands">{commands.map((command) => `${command.id}:${command.status}`).join(",")}</div> }));
const server: ServerSummary = { id: "edge", name: "Edge", status: "connected", connection_mode: "http", listen_port: 0, pull_port: 0, ipv6_enabled: false, traffic_limit: 0, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0, created_at: "2026-08-31", updated_at: "2026-08-31" };
function change(patch: Partial<AgentChangeSet> = {}): AgentChangeSet {
  return { id: "plan", name: "Existing plan", description: "Dependency plan", status: "planned", rollback_on_failure: true, rollback_reason: "", steps: [{ id: "step", change_set_id: "plan", sequence: 1, server_id: "edge", label: "Configure", forward: { method: "POST", path: "/api/child/outbounds", body: { action: "add" } }, rollback: { method: "POST", path: "/api/child/outbounds", body: { action: "delete" } }, created_at: "2026-08-31T03:00:00Z", updated_at: "2026-08-31T03:00:00Z" }], created_at: "2026-08-31T03:00:00Z", updated_at: "2026-08-31T03:00:00Z", ...patch };
}
const response = (row: AgentChangeSet) => ({ change_set: row, commands: [], warnings: [], license_required: false as const });
async function flush() { await act(async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); }); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  installDom();
  vi.mocked(listServers).mockResolvedValue([server]); vi.mocked(listChangeSets).mockResolvedValue({ change_sets: [change()], license_required: false });
  vi.mocked(createRoutedOutboundChangeSet).mockResolvedValue(response(change())); vi.mocked(createChangeSet).mockResolvedValue(response(change()));
  vi.mocked(dispatchChangeSet).mockResolvedValue(response(change({ status: "dispatched" })));
  vi.mocked(rollbackChangeSet).mockResolvedValue(response(change({ status: "rollback_queued" })));
  vi.mocked(acceptChangeSet).mockResolvedValue(response(change({ status: "accepted" })));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("React dependent change sets", () => {
  it("loads after StrictMode remount rather than retaining the cancelled request's loading lock", async () => {
    render(<StrictMode><ChangesView /></StrictMode>); await flush();
    expect(screen.getByRole("button", { name: "Existing plan" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "下发" }) as HTMLButtonElement).disabled).toBe(false);
  });
  it("creates a complete routed plan and deduplicates excludes without inventing client settings", async () => {
    render(<ChangesView />); await flush();
    fireEvent.change(screen.getByLabelText("父级入站标签"), { target: { value: " parent-vless " } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: " direct-v4 " } });
    fireEvent.change(screen.getByLabelText("嗅探排除域名"), { target: { value: "Example.com,example.COM\nother.example" } });
    fireEvent.click(screen.getByRole("switch", { name: "立即下发" }));
    fireEvent.click(screen.getByRole("button", { name: "创建方案" })); await flush();
    expect(createRoutedOutboundChangeSet).toHaveBeenCalledWith({ server_id: "edge", inbound_tag: "parent-vless", inbound_protocol: "vless", label: "direct-v4", outbound: { protocol: "freedom", settings: { domainStrategy: "UseIPv4" } }, parent_ref: null, admin_username: "admin", admin_email: null, outbound_tag: null, marktag: null, node_name: null, client: null, sniffing_exclude_domains: ["Example.com", "other.example"], add_reality_sniffing_excludes: true, command_timeout_ms: 30000, rollback_on_failure: true, dispatch: true });
  });
  it("validates required inbound and non-object outbound without sending commands", async () => {
    render(<ChangesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "创建方案" })); await flush();
    expect(screen.getByText("请填写入站标签。")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("父级入站标签"), { target: { value: "inbound" } });
    fireEvent.change(screen.getByLabelText("出站 JSON"), { target: { value: "[]" } });
    fireEvent.click(screen.getByRole("button", { name: "创建方案" })); await flush();
    expect(screen.getByText("出站 JSON 必须是 JSON 对象。")).toBeTruthy(); expect(createRoutedOutboundChangeSet).not.toHaveBeenCalled();
  });
  it("never clamps a negative timeout before validating a routed plan", async () => {
    render(<ChangesView />); await flush(); fireEvent.change(screen.getByLabelText("父级入站标签"), { target: { value: "parent" } });
    const input = screen.getByLabelText("超时时间（毫秒）");
    fireEvent.change(input, { target: { value: "-1" } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
    expect(createRoutedOutboundChangeSet).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("-1");
    expect(screen.getByText("超时时间必须大于 0。")).toBeTruthy();
  });
  it("keeps an incomplete timeout exponent invalid until its final digit is entered", async () => {
    render(<ChangesView />); await flush(); fireEvent.change(screen.getByLabelText("父级入站标签"), { target: { value: "parent" } }); const input = screen.getByLabelText("超时时间（毫秒）");
    fireEvent.focus(input);
    for (const value of ["", "1", "1e"]) {
      const key = value.at(-1) || "Backspace";
      fireEvent.keyDown(input, { key }); fireEvent.change(input, { target: { value } }); fireEvent.keyUp(input, { key });
      if (value !== "1") { fireEvent.submit(input.closest("form")!); await flush(); expect(createRoutedOutboundChangeSet).not.toHaveBeenCalled(); }
      expect((input as HTMLInputElement).value).toBe(value);
    }
    fireEvent.focus(input); fireEvent.keyDown(input, { key: "3", code: "Digit3" }); fireEvent.change(input, { target: { value: (input as HTMLInputElement).value + "3" } }); fireEvent.keyUp(input, { key: "3", code: "Digit3" });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.blur(input); fireEvent.submit(input.closest("form")!); await flush();
    expect(createRoutedOutboundChangeSet).toHaveBeenCalledWith(expect.objectContaining({ command_timeout_ms: 1000 }));
  });
  it("retains raw forward/rollback steps and rejects invalid step shapes", async () => {
    render(<ChangesView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "原始步骤" }));
    const panel = within(screen.getByRole("tabpanel", { name: "原始步骤" }));
    fireEvent.change(panel.getByLabelText("变更集名称"), { target: { value: " raw plan " } });
    fireEvent.change(panel.getByLabelText("步骤 JSON"), { target: { value: '[{"server_id":"edge","forward":{"path":"/api/child/system/info"},"rollback":[]}]' } });
    fireEvent.click(panel.getByRole("button", { name: "创建" })); await flush();
    expect(screen.getByText("第 1 步的 rollback 必须是对象或 null。")).toBeTruthy(); expect(createChangeSet).not.toHaveBeenCalled();
    const steps = [{ server_id: "edge", label: "Read", forward: { method: "GET", path: "/api/child/system/info" }, rollback: null }];
    fireEvent.change(panel.getByLabelText("步骤 JSON"), { target: { value: JSON.stringify(steps) } });
    fireEvent.click(panel.getByRole("button", { name: "创建" })); await flush();
    expect(createChangeSet).toHaveBeenCalledWith({ name: "raw plan", description: "", rollback_on_failure: true, dispatch: false, steps });
  });
  it("does not allow a pre-dispatch list request to restore the old state", async () => {
    render(<ChangesView />); await flush();
    let resolve!: (value: AgentChangeSetsResponse) => void;
    vi.mocked(listChangeSets).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "刷新变更集" })); await flush();
    fireEvent.click(screen.getByRole("button", { name: "下发" })); await flush();
    await act(async () => { resolve({ change_sets: [change()], license_required: false }); }); await flush();
    expect((screen.getByRole("button", { name: "下发" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText("已下发").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "回滚" })).toBeTruthy();
  });
  it("keeps node reservations blocked until command results arrive", async () => {
    vi.mocked(listChangeSets).mockResolvedValue({ change_sets: [change({ status: "rollback_failed", held_server_ids: ["edge"], blocking_command_ids: ["pending-compensation"], warnings: ["Rollback needs review"] })], license_required: false });
    render(<ChangesView />); await flush();
    expect(screen.queryByRole("button", { name: "接受当前状态" })).toBeNull();
    expect(screen.getByText("等待命令结果：pending-compensation")).toBeTruthy();
    expect(screen.getByText("已预留 1 个节点")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("回滚原因"), { target: { value: " retry verified " } });
    fireEvent.click(screen.getByRole("button", { name: "重试回滚" })); await flush();
    expect(rollbackChangeSet).toHaveBeenCalledWith("plan", { reason: "retry verified" });
  });
  it("requires both acceptance reason and explicit acknowledgement", async () => {
    vi.mocked(listChangeSets).mockResolvedValue({ change_sets: [change({ status: "needs_review", held_server_ids: ["edge"] })], license_required: false });
    render(<ChangesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "接受当前状态" }));
    const dialog = within(screen.getByText("接受当前状态", { selector: ".ui-dialog-title" }).closest('[role="dialog"]')!);
    expect((dialog.getByRole("button", { name: "接受状态" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(dialog.getByLabelText("处理原因"), { target: { value: "  Verified nodes  " } });
    expect((dialog.getByRole("button", { name: "接受状态" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "我已检查节点并接受所有剩余变更" }));
    fireEvent.click(dialog.getByRole("button", { name: "接受状态" })); await flush();
    expect(acceptChangeSet).toHaveBeenCalledWith("plan", "Verified nodes");
    expect(screen.getByText("已接受当前状态并释放节点预留。")).toBeTruthy();
  });
  it("locks all mutation actions for archived steps", async () => {
    const archived = change({ status: "rollback_failed" }); archived.steps[0].archived = true; archived.steps[0].server_name = "Deleted server";
    vi.mocked(listChangeSets).mockResolvedValue({ change_sets: [archived], license_required: false });
    render(<ChangesView />); await flush();
    expect(screen.getByText("Deleted server（已归档）")).toBeTruthy();
    expect((screen.getByRole("button", { name: "下发" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "回滚" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "接受当前状态" })).toBeNull();
  });
  it("polls active runs every 2500ms and cancels its timer on unmount", async () => {
    vi.mocked(listChangeSets).mockResolvedValue({ change_sets: [change({ status: "dispatched" })], license_required: false });
    const view = render(<ChangesView />); await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(2499); }); expect(listChangeSets).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); }); await flush(); expect(listChangeSets).toHaveBeenCalledTimes(2);
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(listChangeSets).toHaveBeenCalledTimes(2);
  });
});
