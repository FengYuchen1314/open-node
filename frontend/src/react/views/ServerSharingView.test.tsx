// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FederatedServer, FederationCommand, ServerShare } from "../../domain/server-sharing";
import * as inventory from "../../services/inventory";
import * as sharing from "../../services/server-sharing";
import ServerSharingView from "./ServerSharingView";

vi.mock("../../services/inventory");
vi.mock("../../services/server-sharing");
const now = "2026-09-01T00:00:00Z";
const server = { id: "22222222-2222-4222-8222-222222222222", name: "边缘一号", status: "connected", connection_mode: "websocket", listen_port: 23889, pull_port: 0, ipv6_enabled: false, traffic_limit: 0, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0, created_at: now, updated_at: now } as const;
const share: ServerShare = { id: "11111111-1111-4111-8111-111111111111", server_id: server.id, label: "租户甲", allow_manage_xray: false, revision: 0, created_at: now, license_required: false };
const imported: FederatedServer = {
  id: "33333333-3333-4333-8333-333333333333", name: "异地节点", owner_url: "https://owner.example", prefix: "site-", revision: 0,
  info: { name: "上游节点", status: "connected", ip_address: "198.51.100.2", ip_address_v6: null, domain: "edge.example", domain_v6: null, ipv6_enabled: false, xray_mode: "external", traffic_limit: 0, traffic_reset_day: 0, traffic_used: 10, current_upload_speed: 1, current_download_speed: 2, xray_running: true, xray_version: "26.3.27", nginx: null, probe_sys: null, last_heartbeat: now, allow_manage_xray: false, license_required: false },
  last_synced_at: now, created_at: now, license_required: false,
};
const command: FederationCommand = { id: "44444444-4444-4444-8444-444444444444", method: "GET", path: "/api/child/inbounds", status: "succeeded", result_status: 200, result_body: { inbounds: [] }, failed: false, created_at: now, completed_at: now, license_required: false };
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

describe("ServerSharingView", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
    const getStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    vi.mocked(inventory.listServers).mockResolvedValue([server]);
    vi.mocked(sharing.listServerShares).mockResolvedValue({ shares: [share], license_required: false });
    vi.mocked(sharing.listFederatedServers).mockResolvedValue({ servers: [imported], license_required: false });
    vi.mocked(sharing.createServerShare).mockResolvedValue({ share: { ...share, id: "55555555-5555-4555-8555-555555555555" }, share_token: "A".repeat(43), license_required: false });
    vi.mocked(sharing.addFederatedServer).mockResolvedValue({ ...imported, id: "66666666-6666-4666-8666-666666666666" });
    vi.mocked(sharing.refreshFederatedServer).mockResolvedValue({ ...imported, revision: 1 });
    vi.mocked(sharing.manageFederatedServer).mockResolvedValue(command);
    vi.mocked(sharing.serverSharingErrorMessage).mockImplementation(() => "共享操作失败");
  });

  it("creates an owner share and displays the token exactly once", async () => {
    render(<ServerSharingView />); await flush();
    fireEvent.click(screen.getByRole("button", { name: /创建分享$/ }));
    const label = screen.getByLabelText("分享用途标签");
    const modal = label.closest('[role="dialog"]') as HTMLElement;
    fireEvent.change(label, { target: { value: " 新租户 " } });
    fireEvent.click(within(modal).getByText("允许接收方查看和修改完整 Xray 配置"));
    fireEvent.click(within(modal).getByRole("button", { name: "创建并显示令牌" })); await flush();
    expect(sharing.createServerShare).toHaveBeenCalledWith(server.id, "新租户", true);
    expect((screen.getByLabelText("一次性分享令牌") as HTMLInputElement).value).toBe("A".repeat(43));
    fireEvent.click(screen.getByRole("button", { name: "我已安全保存" }));
    await waitFor(() => expect(screen.queryByLabelText("一次性分享令牌")).toBeNull());
  });

  it("clears the entered token after importing and sends federated commands", async () => {
    render(<ServerSharingView />); await flush();
    fireEvent.click(screen.getByRole("tab", { name: "接入的共享服务器" }));
    fireEvent.click(screen.getByRole("button", { name: /接入服务器$/ }));
    const owner = screen.getByLabelText("拥有方地址");
    const add = owner.closest('[role="dialog"]') as HTMLElement;
    fireEvent.change(owner, { target: { value: " https://owner.example " } });
    fireEvent.change(within(add).getByLabelText("接入分享令牌"), { target: { value: "Z".repeat(43) } });
    fireEvent.change(within(add).getByLabelText("入站标签前缀"), { target: { value: " site- " } });
    fireEvent.click(within(add).getByRole("button", { name: "验证并接入" })); await flush();
    expect(sharing.addFederatedServer).toHaveBeenCalledWith({ owner_url: "https://owner.example", share_token: "Z".repeat(43), name: "", prefix: "site-" });
    expect(screen.queryByLabelText("接入分享令牌")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /管理$/ })[0]);
    const manage = screen.getByLabelText("联邦 Agent 路径").closest('[role="dialog"]') as HTMLElement;
    fireEvent.click(within(manage).getByRole("button", { name: "发送命令" })); await flush();
    expect(sharing.manageFederatedServer).toHaveBeenCalledWith(imported, { method: "GET", path: "/api/child/inbounds", body: null, timeout_ms: 30000 });
    expect((within(manage).getByLabelText("联邦命令结果") as HTMLTextAreaElement).value).toBe('{\n  "inbounds": []\n}');
  });

  it("synchronizes the ordinary runtime inbound snapshot with server status", async () => {
    render(<ServerSharingView />); await flush();
    fireEvent.click(screen.getByRole("tab", { name: "接入的共享服务器" }));
    fireEvent.click(screen.getByRole("button", { name: /同步状态与入站/ })); await flush();
    expect(sharing.refreshFederatedServer).toHaveBeenCalledWith(imported);
    expect(sharing.manageFederatedServer).toHaveBeenCalledWith(
      { ...imported, revision: 1 },
      { method: "GET", path: "/api/child/inbounds", body: null, timeout_ms: 30000 },
    );
    expect(screen.getByText(/可在配置页运行时清单中创建节点/)).toBeTruthy();
  });
});
