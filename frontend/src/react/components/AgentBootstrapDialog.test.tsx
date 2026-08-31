// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentBootstrap, issueAgentBootstrap, revokeAgentBootstrap, type AgentBootstrapState } from "../../services/agent-bootstrap";
import AgentBootstrapDialog from "./AgentBootstrapDialog";
vi.mock("../../services/agent-bootstrap", () => ({ getAgentBootstrap: vi.fn(), issueAgentBootstrap: vi.fn(), revokeAgentBootstrap: vi.fn() }));
const issuedAt = "2026-08-31T03:00:00Z";
function state(overrides: Partial<AgentBootstrapState["bootstrap"]> = {}): AgentBootstrapState {
  return { configured: true, control_url: "https://control.example", reason: null, license_required: false,
    release: { agent_version: "0.3.0a0", source_commit: "a".repeat(40), xray_version: "v26.3.27", platform: "Debian 12 amd64" },
    bootstrap: { server_id: "edge", server_name: "Edge", status: "not_issued", issued_at: null, expires_at: null, claimed_at: null,
      agent_registered: false, agent_registered_at: null, agent_last_seen_at: null, agent_version: null, server_last_heartbeat: null, ...overrides } };
}
const issued = { issued: { server_id: "edge", server_name: "Edge", control_url: "https://control.example", transport: "auto" as const,
  issued_at: issuedAt, expires_at: "2026-08-31T03:10:00Z" }, command: "private-short-lived-command", license_required: false as const };
async function flush() { await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); }); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(getAgentBootstrap).mockResolvedValue(state()); vi.mocked(issueAgentBootstrap).mockResolvedValue(issued);
  vi.mocked(revokeAgentBootstrap).mockResolvedValue(state({ status: "revoked" }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount() {
  const props = { open: true, serverId: "edge", serverName: "Edge", onOpenChange: vi.fn(), onUpdated: vi.fn() };
  return { ...render(<AgentBootstrapDialog {...props} />), props };
}
async function showCommand() {
  const model = mount(); await flush();
  vi.mocked(getAgentBootstrap).mockResolvedValue(state({ status: "issued", issued_at: issuedAt, expires_at: issued.issued.expires_at }));
  fireEvent.click(screen.getByRole("checkbox", { name: "我确认使用一台全新的 Debian 12 amd64 服务器，且仅用于此服务器记录。" }));
  fireEvent.click(screen.getByTestId("bootstrap-issue")); await flush();
  return model;
}
describe("React Agent bootstrap dialog", () => {
  it("opens read-only and explains the bounded non-root installation", async () => {
    mount(); await flush(); expect(issueAgentBootstrap).not.toHaveBeenCalled();
    expect((screen.getByTestId("bootstrap-issue") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/以非 root 身份运行的托管 Agent/)).toBeTruthy(); expect(screen.getByText(/不创建公开代理入站/)).toBeTruthy();
    expect(screen.getByTestId("bootstrap-status").textContent).toContain("Agent 尚未注册");
  });
  it("removes command DOM on close and does not recover it on reopen", async () => {
    const { props, rerender } = await showCommand();
    expect((screen.getByLabelText("root shell 安装命令") as HTMLTextAreaElement).value).toBe(issued.command);
    expect(screen.getByLabelText("root shell 安装命令").closest(".ant-form-item")?.classList.contains("ant-form-item-vertical")).toBe(true);
    expect(screen.getByText(/有效期为 10 分钟的私密票据/)).toBeTruthy();
    rerender(<AgentBootstrapDialog {...props} open={false} />);
    expect(screen.queryByTestId("bootstrap-command")).toBeNull(); expect(document.body.textContent).not.toContain(issued.command);
    rerender(<AgentBootstrapDialog {...props} />); await flush();
    expect(screen.queryByTestId("bootstrap-command")).toBeNull(); expect(issueAgentBootstrap).toHaveBeenCalledOnce();
  });
  it("reports clipboard denial without exposing the command in an error message", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error(issued.command)) } });
    await showCommand(); fireEvent.click(screen.getByRole("button", { name: "复制命令" })); await flush();
    expect(screen.getByRole("status").textContent).toBe("无法访问剪贴板，请选中命令后手动复制。");
  });
  it("discards late clipboard completion after close", async () => {
    let resolve!: () => void;
    const writeText = vi.fn().mockReturnValue(new Promise<void>(done => { resolve = done; }));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { props, rerender } = await showCommand(); fireEvent.click(screen.getByRole("button", { name: "复制命令" }));
    rerender(<AgentBootstrapDialog {...props} open={false} />); await act(async () => resolve());
    rerender(<AgentBootstrapDialog {...props} />); await flush();
    expect(screen.queryByText(/已复制。请妥善保护/)).toBeNull(); expect(writeText).toHaveBeenCalledWith(issued.command);
  });
  it("does not describe a claimed ticket as a completed installation", async () => {
    vi.mocked(getAgentBootstrap).mockResolvedValue(state({ status: "claimed", claimed_at: issuedAt })); mount(); await flush();
    expect(screen.getByText(/安装尚未确认完成/)).toBeTruthy(); expect(screen.queryByTestId("bootstrap-issue")).toBeNull();
    expect(screen.getByTestId("bootstrap-status").textContent).toContain("Agent 尚未注册");
    expect(screen.getByText(/不会撤销已交付的/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "撤销安装票据" })); await flush();
    expect(revokeAgentBootstrap).toHaveBeenCalledWith("edge");
  });
  it("displays configuration errors without an issue control", async () => {
    vi.mocked(getAgentBootstrap).mockResolvedValue({ ...state(), configured: false, reason: "Configure canonical HTTPS" }); mount(); await flush();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy(); expect(document.body.textContent).not.toContain("Configure canonical HTTPS"); expect(screen.queryByTestId("bootstrap-issue")).toBeNull();
  });
});
