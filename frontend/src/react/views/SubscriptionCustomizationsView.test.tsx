// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CustomRule, OverrideScript, ProxyProvider } from "../../domain/subscription-customizations";
import * as external from "../../services/external-subscriptions";
import * as customizations from "../../services/subscription-customizations";
import * as subscriptions from "../../services/subscriptions";
import { routes } from "../../routes";
import SubscriptionCustomizationsView from "./SubscriptionCustomizationsView";

vi.mock("../../services/external-subscriptions");
vi.mock("../../services/subscription-customizations");
vi.mock("../../services/subscriptions");
const now = "2026-09-01T00:00:00Z";
const rule: CustomRule = {
  id: "rule-id", owner_username: "alice", name: "中国直连", type: "rules",
  mode: "prepend", content: "- DOMAIN-SUFFIX,cn,DIRECT\n", enabled: true,
  revision: 1, created_at: now, updated_at: now,
};
const provider: ProxyProvider = {
  id: "provider-id", owner_username: "alice", external_source_id: "source-id",
  name: "机场节点", type: "http", interval: 3600, proxy: "DIRECT", size_limit: 0,
  health_check_enabled: true, health_check_url: "https://www.gstatic.com/generate_204",
  health_check_interval: 300, health_check_timeout: 5000, health_check_lazy: true,
  health_check_expected_status: 204, filter: "", exclude_filter: "", exclude_type: "",
  override: {}, process_mode: "client", enabled: true, revision: 1,
  created_at: now, updated_at: now,
};
const script: OverrideScript = {
  id: "script-id", owner_username: "alice", name: "整理订阅", hook: "post_fetch",
  content: "function main(config) { return config; }", enabled: true, sort_order: 0,
  revision: 1, created_at: now, updated_at: now,
};
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

describe("SubscriptionCustomizationsView", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
    const getStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
    vi.mocked(subscriptions.listProductUsers).mockResolvedValue({ users: [{ username: "alice", display_name: "Alice", role: "user", is_active: true, is_reset: true, reset_day: 1, created_at: now, updated_at: now }], license_required: false });
    vi.mocked(external.listExternalSources).mockResolvedValue({ sources: [{ id: "source-id", owner_username: "alice", name: "上游快照", enabled: true, revision: 1, has_custom_user_agent: false, node_count: 2, available_node_count: 2, metadata: {}, last_synced_at: now, created_at: now, updated_at: now }], license_required: false });
    vi.mocked(customizations.listCustomRules).mockResolvedValue({ rules: [rule], license_required: false });
    vi.mocked(customizations.listProxyProviders).mockResolvedValue({ providers: [provider], license_required: false });
    vi.mocked(customizations.listOverrideScripts).mockResolvedValue({ scripts: [script], runtime: "quickjs-subprocess", license_required: false });
    vi.mocked(customizations.createCustomRule).mockResolvedValue({ ...rule, id: "new-rule", name: "新规则" });
    vi.mocked(customizations.createOverrideScript).mockResolvedValue({ ...script, id: "new-script", name: "新脚本" });
  });

  it("is routed, loads both official resource types and creates a YAML rule", async () => {
    expect(routes.some(route => route.path === "/subscription-customizations")).toBe(true);
    render(<SubscriptionCustomizationsView />); await flush();
    expect(screen.getByText("中国直连")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Proxy Provider（1）" }));
    expect(screen.getByText("机场节点")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "自定义规则（1）" }));
    fireEvent.click(screen.getByRole("button", { name: /新建规则/ }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("规则名称"), { target: { value: " 新规则 " } });
    fireEvent.change(within(dialog).getByLabelText("规则 YAML 内容"), { target: { value: "- MATCH,Proxy\n" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /保.*存/ })); await flush();
    expect(customizations.createCustomRule).toHaveBeenCalledWith({
      owner_username: "alice", name: "新规则", type: "rules", mode: "prepend",
      content: "- MATCH,Proxy", enabled: true,
    });
    expect(screen.getByText(/下次下载时实时应用/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "覆写脚本（1）" }));
    expect(screen.getByText("整理订阅")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /新建脚本/ }));
    const scriptDialog = screen.getByRole("dialog");
    fireEvent.change(within(scriptDialog).getByLabelText("脚本名称"), { target: { value: " 新脚本 " } });
    fireEvent.change(within(scriptDialog).getByLabelText("脚本内容"), { target: { value: "function main(config) { return config; }\n" } });
    fireEvent.click(within(scriptDialog).getByRole("button", { name: /保存并检查语法/ })); await flush();
    expect(customizations.createOverrideScript).toHaveBeenCalledWith({
      owner_username: "alice", name: "新脚本", hook: "post_fetch",
      content: "function main(config) { return config; }", enabled: true, sort_order: 0,
    });
  });

  it("uses the owner-derived account client and hides sections not granted to the subscriber", async () => {
    const accountCreate = vi.fn().mockResolvedValue({ ...rule, id: "account-rule", name: "账户规则" });
    const accountApi = {
      listCustomRules: vi.fn().mockResolvedValue({ rules: [rule], license_required: false }),
      createCustomRule: accountCreate, updateCustomRule: vi.fn(), deleteCustomRule: vi.fn(),
      listProxyProviders: vi.fn(), createProxyProvider: vi.fn(),
      updateProxyProvider: vi.fn(), deleteProxyProvider: vi.fn(),
      listOverrideScripts: vi.fn().mockResolvedValue({ scripts: [script], runtime: "quickjs-subprocess", license_required: false }),
      createOverrideScript: vi.fn(), updateOverrideScript: vi.fn(), deleteOverrideScript: vi.fn(),
    } as ReturnType<typeof customizations.accountSubscriptionCustomizations>;
    vi.mocked(customizations.accountSubscriptionCustomizations).mockReturnValue(accountApi);
    vi.mocked(external.accountExternalSubscriptions).mockReturnValue({} as ReturnType<typeof external.accountExternalSubscriptions>);
    render(<SubscriptionCustomizationsView subscriberUsername="alice" allowRules allowProviders={false} />);
    await flush();
    expect(screen.getByRole("heading", { name: "我的订阅自定义" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "自定义规则（1）" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Proxy Provider/ })).toBeNull();
    expect(screen.getByRole("tab", { name: "覆写脚本（1）" })).toBeTruthy();
    expect(subscriptions.listProductUsers).not.toHaveBeenCalled();
    expect(customizations.listCustomRules).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /新建规则/ }));
    const dialog = screen.getByRole("dialog");
    expect((within(dialog).getByLabelText("规则所属用户") as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("规则名称"), { target: { value: "账户规则" } });
    fireEvent.change(within(dialog).getByLabelText("规则 YAML 内容"), { target: { value: "- MATCH,DIRECT\n" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /保.*存/ })); await flush();
    expect(accountCreate).toHaveBeenCalledWith({
      owner_username: "alice", name: "账户规则", type: "rules", mode: "prepend",
      content: "- MATCH,DIRECT", enabled: true,
    });
  });
});
