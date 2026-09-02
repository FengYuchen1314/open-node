// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DDNSServer, DDNSWorkspace } from "../../domain/ddns";
import * as ddns from "../../services/ddns";
import DDNSView from "./DDNSView";

vi.mock("../../services/ddns");
const item: DDNSServer = {
  server_id: "11111111-1111-4111-8111-111111111111", server_name: "动态节点",
  server_status: "connected", is_federated: false, enabled: false,
  provider_id: null, provider_name: null, provider_type: null,
  pull_address: null, pull_address_v6: null, ip_address: "203.0.113.2",
  ip_address_v6: "2001:db8::2", ipv6_enabled: true, last_synced_at: null,
  last_error: null, pending: false, revision: 0, license_required: false,
};
const provider = { id: "22222222-2222-4222-8222-222222222222", name: "主 DNS", provider: "cloudflare", supported: true };
const workspace: DDNSWorkspace = { servers: [item], providers: [provider], license_required: false };
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

describe("DDNSView", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
    const getStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
    vi.mocked(ddns.loadDDNS).mockResolvedValue(workspace);
    vi.mocked(ddns.saveDDNS).mockResolvedValue({ ...item, enabled: true, provider_id: provider.id, provider_name: provider.name, provider_type: provider.provider, pull_address: "edge.example.com", revision: 1 });
    vi.mocked(ddns.syncDDNS).mockResolvedValue({ ...item, enabled: true, pending: true });
    vi.mocked(ddns.ddnsError).mockReturnValue("DDNS 操作失败");
    vi.mocked(ddns.ddnsStatusMessage).mockImplementation(code => code);
  });

  it("saves a Chinese DDNS configuration with the current revision", async () => {
    render(<DDNSView />); await flush();
    fireEvent.click(screen.getByRole("button", { name: /设置$/ }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("switch"));
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    fireEvent.click(await screen.findByText("主 DNS · Cloudflare"));
    fireEvent.change(within(dialog).getByPlaceholderText("edge.example.com"), { target: { value: " edge.example.com " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确定" })); await flush();
    expect(ddns.saveDDNS).toHaveBeenCalledWith(item, {
      enabled: true, provider_id: provider.id, pull_address: "edge.example.com", pull_address_v6: null,
    });
    expect(screen.getByText(/后台正在等待或执行首次同步/)).toBeTruthy();
  });

  it("queues one manual sync and shows the pending state", async () => {
    vi.mocked(ddns.loadDDNS).mockResolvedValue({ ...workspace, servers: [{ ...item, enabled: true }] });
    render(<DDNSView />); await flush();
    fireEvent.click(screen.getByRole("button", { name: /同步$/ })); await flush();
    expect(ddns.syncDDNS).toHaveBeenCalledWith({ ...item, enabled: true });
    expect(screen.getByText(/已排队手动同步/)).toBeTruthy();
  });
});
