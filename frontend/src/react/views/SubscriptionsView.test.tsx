// @vitest-environment jsdom
import { act, cleanup, fireEvent, render as renderAnt, screen } from "@testing-library/react";
import zhCN from "antd/locale/zh_CN";
import { ConfigProvider } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubscriptionsView from "./SubscriptionsView";
import * as subscriptions from "../../services/subscriptions";
import { listServers } from "../../services/inventory";
import { listSubscriptionTemplates } from "../../services/subscription-templates";
import { listSubscriptionProfiles } from "../../services/subscription-profiles";
import { listTemporarySubscriptions } from "../../services/temporary-subscriptions";
import { listPrivateRoutes } from "../../services/private-routed-nodes";
import { fetchAppMeta } from "../../services/api";
import { createExternalPreview, listExternalSources } from "../../services/external-subscriptions";
import { listCamouflagePools } from "../../services/camouflage-pools";
import type { ManagedNode, ManagedNodeCreationMetadataResponse, ProductUser, ProductUserSubscriptionToken, SubscriptionCredential, SubscriptionPlan, SubscriptionTemplatePreset } from "../../domain/subscriptions";

const render = (ui: Parameters<typeof renderAnt>[0]) => renderAnt(ui, { wrapper: ({ children }) => <ConfigProvider locale={zhCN}>{children}</ConfigProvider> });

vi.mock("../../services/subscriptions", async importOriginal => {
  const original = await importOriginal<typeof import("../../services/subscriptions")>();
  return { ...original, assignSubscriptionPlan: vi.fn(), createManagedNode: vi.fn(), createManagedNodeFromPreset: vi.fn(), createProductUser: vi.fn(), createProductUserSubscriptionToken: vi.fn(), createSubscriptionPlan: vi.fn(), exportSubscriptionCatalog: vi.fn(), getManagedNodeCreationMetadata: vi.fn(), getProductUserQuota: vi.fn(), getProductUserTraffic: vi.fn(), getSubscriptionFormatPreview: vi.fn(), importSubscriptionCatalog: vi.fn(), listProductUserCredentials: vi.fn(), listManagedNodes: vi.fn(), listProductUsers: vi.fn(), listSubscriptionPlans: vi.fn(), listSubscriptionTemplatePresets: vi.fn(), resetDueProductUserTraffic: vi.fn(), resetProductUserTraffic: vi.fn(), resetProductUserSubscriptionToken: vi.fn() };
});
vi.mock("../../services/camouflage-pools", () => ({ listCamouflagePools: vi.fn() }));
vi.mock("../../services/inventory", () => ({ listServers: vi.fn() }));
vi.mock("../../services/subscription-templates", () => ({ listSubscriptionTemplates: vi.fn() }));
vi.mock("../../services/subscription-profiles", () => ({ listSubscriptionProfiles: vi.fn() }));
vi.mock("../../services/temporary-subscriptions", () => ({ listTemporarySubscriptions: vi.fn(), deleteTemporarySubscription: vi.fn() }));
vi.mock("../../services/private-routed-nodes", () => ({ listPrivateRoutes: vi.fn() }));
vi.mock("../../services/api", () => ({ fetchAppMeta: vi.fn() }));
vi.mock("../../services/external-subscriptions", async importOriginal => ({ ...await importOriginal<typeof import("../../services/external-subscriptions")>(), listExternalSources: vi.fn(), createExternalPreview: vi.fn() }));
vi.mock("../components/SubscriptionAccessPanel", () => ({ default: ({ username }: { username: string }) => <span>Access for {username}</span> }));
const user = (username: string): ProductUser => ({ username, display_name: username === "alice" ? "Alice" : "Bob", role: "user", is_active: true, current_plan_id: "p", is_reset: true, reset_day: 1, created_at: "", updated_at: "" });
const plan: SubscriptionPlan = { id: "p", name: "Basic", description: "", traffic_limit_gb: 30, traffic_limit_bytes: 30 * 1024 ** 3, cycle_days: 30, is_reset: true, reset_day: 1, node_ids: ["a"], node_multipliers: {}, node_name_overrides: {}, node_name_override_enabled: false, auto_speed_rules: [], node_speed_limits: {}, node_device_limits: {}, speed_limit_mbps: 0, device_limit: 0, traffic_mode: "twoway", created_at: "", updated_at: "" };
const node: ManagedNode = { id: "a", name: "Alpha", server_id: "edge", protocol: "vless", node_type: "physical", tags: [], enabled: true, config: {}, client_template: {}, created_at: "", updated_at: "" };
const creationMetadata: ManagedNodeCreationMetadataResponse = {
  server_kinds: { direct: "公网直连", "leased-line": "专线", residential: "家宽落地" },
  profiles: [
    { profile: "vless-reality-vision", protocol: "vless", label: "VLESS Reality Vision", description: "Vision", allowed_server_kinds: ["direct"], fixed_port: 443, requires_camouflage_pool: true, requires_domestic_entry: false, warning: null, warning_server_kinds: [] },
    { profile: "vless-xhttp-reality-xmux", protocol: "vless", label: "VLESS XHTTP Reality XMUX", description: "XHTTP", allowed_server_kinds: ["direct"], fixed_port: 443, requires_camouflage_pool: true, requires_domestic_entry: false, warning: null, warning_server_kinds: [] },
    { profile: "anytls-shadowtls", protocol: "anytls", label: "AnyTLS + ShadowTLS", description: "AnyTLS", allowed_server_kinds: ["direct"], fixed_port: 443, requires_camouflage_pool: true, requires_domestic_entry: false, warning: null, warning_server_kinds: [] },
    { profile: "mieru", protocol: "mieru", label: "Mieru", description: "Mieru", allowed_server_kinds: ["direct", "leased-line"], fixed_port: null, requires_camouflage_pool: false, requires_domestic_entry: true, warning: null, warning_server_kinds: [] },
    { profile: "socks5", protocol: "socks", label: "SOCKS5", description: "SOCKS", allowed_server_kinds: ["direct", "residential"], fixed_port: null, requires_camouflage_pool: false, requires_domestic_entry: false, warning: "极度不推荐，除非您知道您要做什么", warning_server_kinds: ["direct"] },
  ],
  mieru_mapping_modes: { "one-to-one": "国内入口端口与 IX 端口一一对应", manual: "手动填写 IX 端口；请同时完成国内入口到 IX 端口的转发" }, license_required: false,
};
const nodePresets: SubscriptionTemplatePreset[] = creationMetadata.profiles.map(option => ({ id: option.profile, name: option.label,
  description: option.description, protocol: option.protocol, protocol_profile: option.profile, node_type: "physical", inbound_tag: `${option.protocol}-in`,
  tag: option.protocol, tags: [option.protocol], config: { type: option.protocol, port: option.fixed_port ?? (option.profile === "socks5" ? 1080 : 2999) },
  client_template: { email: `{username}__${option.protocol}` } }));
const token = (username: string): ProductUserSubscriptionToken => ({ username, token: `${username}-secret`, short_code: "System12", generated_short_code: "System12", custom_short_code: null, revision: "r1", subscription_url: `https://sub.example/${username}-secret`, short_url: `https://sub.example/s/${username}`, short_links_enabled: true, created_at: "", updated_at: "" });
async function flush() { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); }); }
async function selectBob() { fireEvent.mouseDown(screen.getByRole("combobox", { name: "订阅用户" })); fireEvent.click(screen.getByText("Bob", { selector: ".ant-select-item-option-content" })); await flush(); }
async function selectOption(label: string, option: string) { fireEvent.mouseDown(screen.getByRole("combobox", { name: label })); await flush(); fireEvent.click(screen.getByText(option, { selector: ".ant-select-item-option-content" })); await flush(); }
beforeEach(() => {
  vi.resetAllMocks(); vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(listServers).mockResolvedValue([{ id: "edge", name: "Edge", server_kind: "direct" }] as Awaited<ReturnType<typeof listServers>>);
  vi.mocked(subscriptions.listProductUsers).mockResolvedValue({ users: [user("alice"), user("bob")], license_required: false });
  vi.mocked(subscriptions.listManagedNodes).mockResolvedValue({ nodes: [node], license_required: false });
  vi.mocked(subscriptions.listSubscriptionPlans).mockResolvedValue({ plans: [plan], license_required: false });
  vi.mocked(subscriptions.listSubscriptionTemplatePresets).mockResolvedValue({ presets: nodePresets, license_required: false });
  vi.mocked(subscriptions.getManagedNodeCreationMetadata).mockResolvedValue(creationMetadata);
  vi.mocked(listCamouflagePools).mockResolvedValue({ schema_version: 1, reviewed_at: "2026-09-02", probe_vantage: "192.0.2.1", measurement_notice: "创建前重新检查。", sources: {}, pools: [
    { id: "tokyo-sony", region: "tokyo", region_label: "东京", label: "Sony", server_name: "www.sony.jp", target: "www.sony.jp:443", tls_version: "TLSv1.3", alpn: "h2", cloudflare: false, gfw_verdict: "not_blocked", gfw_last_tested: "2026-09-01" },
  ], license_required: false });
  vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [], settings: { enabled: true, clash_template_id: null, surge_template_id: null, revision: "" }, can_manage: true, license_required: false });
  vi.mocked(listSubscriptionProfiles).mockResolvedValue({ profiles: [], license_required: false });
  vi.mocked(listTemporarySubscriptions).mockResolvedValue({ subscriptions: [], license_required: false });
  vi.mocked(listPrivateRoutes).mockResolvedValue({ nodes: [], candidates: [], used_nodes: 0, actions_today: 0, policy: { enabled: false, max_nodes: 2, daily_limit: 5, updated_at: "" }, license_required: false });
  vi.mocked(fetchAppMeta).mockResolvedValue({ short_links_enabled: true } as Awaited<ReturnType<typeof fetchAppMeta>>);
  vi.mocked(listExternalSources).mockResolvedValue({ sources: [], license_required: false });
  vi.mocked(subscriptions.getSubscriptionFormatPreview).mockImplementation(async (username, format) => ({ username, client_format: format, nodes: [{ node_id: "a", name: "Alpha", protocol: "vless", available: true, reason: null }], warnings: [], license_required: false }));
  vi.mocked(subscriptions.listProductUserCredentials).mockResolvedValue({ username: "alice", credentials: [], license_required: false });
  vi.mocked(subscriptions.getProductUserTraffic).mockResolvedValue({ username: "alice", upload: 0, download: 0, total: 0, weighted_upload: 0, weighted_download: 0, charged_usage_bytes: 0, entries: [], license_required: false });
  vi.mocked(subscriptions.getProductUserQuota).mockResolvedValue({ quota: { username: "alice", is_active: true, has_plan: true, available: true, expired: false, over_quota: false, reset_enabled: true, reset_due: false, upload: 0, download: 0, weighted_upload: 0, weighted_download: 0, charged_usage_bytes: 0, traffic_limit_bytes: 1000, remaining_bytes: 1000, percent_used: 0, reset_day: 1 }, license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("React subscriptions view", { timeout: 40_000 }, () => {
  it("opens the external workspace explicitly and unmounts it when closed", async () => {
    render(<SubscriptionsView />); await flush();
    const toggle = screen.getByRole("button", { name: "管理外部订阅" });
    expect(listExternalSources).not.toHaveBeenCalled();
    expect(screen.queryByTestId("external-subscriptions-panel")).toBeNull();
    fireEvent.click(toggle); await flush();
    expect(listExternalSources).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("external-subscriptions-panel")).toBeTruthy();
    fireEvent.click(toggle); await flush();
    expect(screen.queryByTestId("external-subscriptions-panel")).toBeNull();
    expect(listExternalSources).toHaveBeenCalledTimes(1);
    expect(createExternalPreview).not.toHaveBeenCalled();
  });
  it("does not expose a late subscription link after switching users", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.createProductUserSubscriptionToken>>) => void;
    vi.mocked(subscriptions.createProductUserSubscriptionToken).mockReturnValue(new Promise(done => { resolve = done; }));
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "获取链接" })); await selectBob();
    await act(async () => resolve({ subscription: token("alice"), license_required: false }));
    expect(screen.queryByLabelText("订阅链接")).toBeNull(); expect(screen.getByText("Access for bob")).toBeTruthy();
    vi.mocked(subscriptions.createProductUserSubscriptionToken).mockResolvedValue({ subscription: token("bob"), license_required: false }); fireEvent.click(screen.getByRole("button", { name: "获取链接" })); await flush();
    expect((screen.getByLabelText("订阅链接") as HTMLInputElement).value).toBe("https://sub.example/bob-secret");
  });
  it("drops late credential reads and clears an already shown secret on user change", async () => {
    const credential = { id: "c", username: "alice", email: "alice-client", node_id: "a", server_id: "edge", protocol: "vless", credential: { id: "alice-credential-secret" }, created_at: "", updated_at: "" } satisfies SubscriptionCredential;
    vi.mocked(subscriptions.listProductUserCredentials).mockResolvedValue({ username: "alice", credentials: [credential], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "凭据" })); await flush(); expect(screen.getByText("alice-credential-secret")).toBeTruthy();
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.listProductUserCredentials>>) => void; vi.mocked(subscriptions.listProductUserCredentials).mockReturnValue(new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "凭据" })); await selectBob(); await act(async () => resolve({ username: "alice", credentials: [credential], license_required: false }));
    expect(screen.queryByText("alice-credential-secret")).toBeNull();
  });
  it("offers only the five managed profiles and binds camouflage SNI to the selected catalog pool", async () => {
    vi.mocked(subscriptions.listSubscriptionTemplatePresets).mockResolvedValue({ presets: nodePresets.map((preset, index) => index ? preset : { ...preset, node_type: "routed" }), license_required: false });
    vi.mocked(subscriptions.createManagedNode).mockResolvedValue({ node, license_required: false }); render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "节点" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "协议档案" })); await flush();
    for (const label of creationMetadata.profiles.map(option => option.label)) expect(screen.getByText(label, { selector: ".ant-select-item-option-content" })).toBeTruthy();
    expect(screen.queryByText("Trojan", { selector: ".ant-select-item-option-content" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("combobox", { name: "协议档案" }), { key: "Escape" });
    expect((screen.getByLabelText("类型") as HTMLInputElement).value).toBe("物理节点（受管运行时）");
    await selectOption("伪装池", "东京 · Sony · www.sony.jp");
    expect((screen.getByLabelText("伪装 SNI") as HTMLInputElement).value).toBe("www.sony.jp");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Tokyo Vision" } });
    fireEvent.click(screen.getByRole("button", { name: "创建节点" })); await flush();
    expect(subscriptions.createManagedNode).toHaveBeenCalledWith(expect.objectContaining({ name: "Tokyo Vision", server_id: "edge", protocol: "vless",
      protocol_profile: "vless-reality-vision", node_type: "physical", camouflage_pool_id: "tokyo-sony", camouflage_sni: "www.sony.jp", config: expect.objectContaining({ port: 443 }) }));
  });
  it("restricts a leased-line server to Mieru and submits manual IX forwarding fields", async () => {
    vi.mocked(listServers).mockResolvedValue([{ id: "edge", name: "Edge", server_kind: "direct" }, { id: "leased", name: "Leased", server_kind: "leased-line" }] as Awaited<ReturnType<typeof listServers>>);
    vi.mocked(subscriptions.createManagedNode).mockResolvedValue({ node: { ...node, server_id: "leased", protocol: "mieru", protocol_profile: "mieru" }, license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "节点" }));
    await selectOption("服务器", "Leased（专线）");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "协议档案" })); await flush();
    expect(screen.getByText("Mieru", { selector: ".ant-select-item-option-content" })).toBeTruthy();
    expect(screen.queryByText("SOCKS5", { selector: ".ant-select-item-option-content" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("combobox", { name: "协议档案" }), { key: "Escape" });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Leased Mieru" } });
    fireEvent.change(screen.getByLabelText("国内入口 IP"), { target: { value: "203.0.113.8" } });
    fireEvent.change(screen.getByLabelText("国内入口端口"), { target: { value: "32000" } });
    await selectOption("端口映射模式", "手动填写 IX 端口；请同时完成国内入口到 IX 端口的转发");
    fireEvent.change(screen.getByLabelText("IX 端口"), { target: { value: "41000" } });
    expect(screen.getByText("请完成国内入口端口转发")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建节点" })); await flush();
    expect(subscriptions.createManagedNode).toHaveBeenCalledWith(expect.objectContaining({ server_id: "leased", protocol: "mieru", protocol_profile: "mieru",
      domestic_entry_ip: "203.0.113.8", domestic_entry_port: 32000, mieru_port_mapping_mode: "manual", ix_port: 41000 }));
  });
  it("shows the SOCKS5 extreme warning only for a matching non-residential server kind", async () => {
    vi.mocked(listServers).mockResolvedValue([{ id: "edge", name: "Edge", server_kind: "direct" }, { id: "home", name: "Home", server_kind: "residential" }] as Awaited<ReturnType<typeof listServers>>);
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "节点" }));
    await selectOption("协议档案", "SOCKS5");
    expect(screen.getByText("极度不推荐，除非您知道您要做什么")).toBeTruthy();
    await selectOption("服务器", "Home（家宽落地）");
    expect(screen.queryByText("极度不推荐，除非您知道您要做什么")).toBeNull();
  });
  it("preserves assignment dates and defaults to synchronizing real Agent accounts", async () => {
    vi.mocked(subscriptions.assignSubscriptionPlan).mockResolvedValue({ user: user("alice"), plan, commands: [], provisioning_batches: [], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "分配" }));
    expect(screen.getByRole("switch", { name: "同步真实节点账号" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-09-01" } }); fireEvent.change(screen.getByLabelText("到期日期"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("命令超时（毫秒）"), { target: { value: "30000" } }); fireEvent.click(screen.getByRole("button", { name: "分配套餐" })); await flush();
    expect(subscriptions.assignSubscriptionPlan).toHaveBeenCalledWith("alice", { plan_id: "p", start_date: "2026-09-01", expire_date: "2026-10-01", queue_agent_commands: true, no_restart: false, command_timeout_ms: 30000 });
    expect(screen.getByLabelText("配置下发批次")).toBeTruthy(); expect(screen.getByText(/命令进入队列并不代表 Agent 已应用/)).toBeTruthy();
  });
  it("refreshes credentials after an assignment even when an earlier credential read is in flight", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.listProductUserCredentials>>) => void;
    vi.mocked(subscriptions.listProductUserCredentials).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    vi.mocked(subscriptions.assignSubscriptionPlan).mockResolvedValue({ user: user("alice"), plan, commands: [], provisioning_batches: [], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "凭据" }));
    fireEvent.click(screen.getByRole("tab", { name: "分配" })); fireEvent.click(screen.getByRole("button", { name: "分配套餐" })); await flush();
    expect(subscriptions.listProductUserCredentials).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ username: "alice", credentials: [], license_required: false })); await flush();
    expect(subscriptions.listProductUserCredentials).toHaveBeenCalledTimes(2); expect(subscriptions.getProductUserQuota).toHaveBeenCalledWith("alice");
  });
  it("requires confirmation before resetting a link and retains format preview filtering", async () => {
    vi.mocked(subscriptions.resetProductUserSubscriptionToken).mockResolvedValue({ subscription: token("alice"), license_required: false });
    vi.mocked(subscriptions.getSubscriptionFormatPreview).mockResolvedValue({ username: "alice", client_format: "clash", nodes: [{ node_id: "a", name: "Unsupported node", protocol: "test", available: false, reason: "该格式不支持此协议" }], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "重置" })); expect(subscriptions.resetProductUserSubscriptionToken).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认重置" })); await flush();
    expect(subscriptions.resetProductUserSubscriptionToken).toHaveBeenCalledWith("alice"); expect((screen.getByLabelText("对应格式链接") as HTMLInputElement).value).toBe(""); expect(screen.getByText("该格式不支持此协议")).toBeTruthy();
  });
  it("exports credential opt-in and confirms mapped catalog import", async () => {
    const catalog = { version: 1, users: [], nodes: [], plans: [], credentials: [] };
    vi.mocked(subscriptions.exportSubscriptionCatalog).mockResolvedValue({ catalog, license_required: false });
    vi.mocked(subscriptions.importSubscriptionCatalog).mockResolvedValue({ summary: { created_users: 0, updated_users: 0, created_nodes: 0, updated_nodes: 0, created_plans: 0, updated_plans: 0, imported_credentials: 0, warnings: [] }, license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("switch", { name: "导出凭据" })); fireEvent.click(screen.getByRole("button", { name: "导出" })); await flush();
    expect(subscriptions.exportSubscriptionCatalog).toHaveBeenCalledWith(true);
    fireEvent.change(screen.getByLabelText("服务器映射 JSON"), { target: { value: '{"legacy-edge":"edge"}' } }); fireEvent.click(screen.getByRole("button", { name: "导入" })); expect(subscriptions.importSubscriptionCatalog).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "导入订阅目录" })); await flush();
    expect(subscriptions.importSubscriptionCatalog).toHaveBeenCalledWith({ catalog, server_map: { "legacy-edge": "edge" }, import_credentials: false });
  });
});
