// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubscriptionsView from "./SubscriptionsView";
import * as subscriptions from "../../services/subscriptions";
import { listServers } from "../../services/inventory";
import { listSubscriptionTemplates } from "../../services/subscription-templates";
import { listSubscriptionProfiles } from "../../services/subscription-profiles";
import { listTemporarySubscriptions } from "../../services/temporary-subscriptions";
import { listPrivateRoutes } from "../../services/private-routed-nodes";
import { fetchAppMeta } from "../../services/api";
import type { ManagedNode, ProductUser, ProductUserSubscriptionToken, SubscriptionCredential, SubscriptionPlan } from "../../domain/subscriptions";

vi.mock("../../services/subscriptions", async importOriginal => {
  const original = await importOriginal<typeof import("../../services/subscriptions")>();
  return { ...original, assignSubscriptionPlan: vi.fn(), createManagedNode: vi.fn(), createManagedNodeFromPreset: vi.fn(), createProductUser: vi.fn(), createProductUserSubscriptionToken: vi.fn(), createSubscriptionPlan: vi.fn(), exportSubscriptionCatalog: vi.fn(), getProductUserQuota: vi.fn(), getProductUserTraffic: vi.fn(), getSubscriptionFormatPreview: vi.fn(), importSubscriptionCatalog: vi.fn(), listProductUserCredentials: vi.fn(), listManagedNodes: vi.fn(), listProductUsers: vi.fn(), listSubscriptionPlans: vi.fn(), listSubscriptionTemplatePresets: vi.fn(), resetDueProductUserTraffic: vi.fn(), resetProductUserTraffic: vi.fn(), resetProductUserSubscriptionToken: vi.fn() };
});
vi.mock("../../services/inventory", () => ({ listServers: vi.fn() }));
vi.mock("../../services/subscription-templates", () => ({ listSubscriptionTemplates: vi.fn() }));
vi.mock("../../services/subscription-profiles", () => ({ listSubscriptionProfiles: vi.fn() }));
vi.mock("../../services/temporary-subscriptions", () => ({ listTemporarySubscriptions: vi.fn(), deleteTemporarySubscription: vi.fn() }));
vi.mock("../../services/private-routed-nodes", () => ({ listPrivateRoutes: vi.fn() }));
vi.mock("../../services/api", () => ({ fetchAppMeta: vi.fn() }));
vi.mock("../components/SubscriptionAccessPanel", () => ({ default: ({ username }: { username: string }) => <span>Access for {username}</span> }));
const user = (username: string): ProductUser => ({ username, display_name: username === "alice" ? "Alice" : "Bob", role: "user", is_active: true, current_plan_id: "p", is_reset: true, reset_day: 1, created_at: "", updated_at: "" });
const plan: SubscriptionPlan = { id: "p", name: "Basic", description: "", traffic_limit_gb: 30, traffic_limit_bytes: 30 * 1024 ** 3, cycle_days: 30, is_reset: true, reset_day: 1, node_ids: ["a"], node_multipliers: {}, node_name_overrides: {}, node_name_override_enabled: false, auto_speed_rules: [], node_speed_limits: {}, node_device_limits: {}, speed_limit_mbps: 0, device_limit: 0, traffic_mode: "twoway", created_at: "", updated_at: "" };
const node: ManagedNode = { id: "a", name: "Alpha", server_id: "edge", protocol: "vless", node_type: "physical", tags: [], enabled: true, config: {}, client_template: {}, created_at: "", updated_at: "" };
const token = (username: string): ProductUserSubscriptionToken => ({ username, token: `${username}-secret`, short_code: "System12", generated_short_code: "System12", custom_short_code: null, revision: "r1", subscription_url: `https://sub.example/${username}-secret`, short_url: `https://sub.example/s/${username}`, short_links_enabled: true, created_at: "", updated_at: "" });
async function flush() { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); }); }
async function selectBob() { fireEvent.mouseDown(screen.getByRole("combobox", { name: "Subscription user" })); fireEvent.click(screen.getByText("Bob", { selector: ".ant-select-item-option-content" })); await flush(); }
beforeEach(() => {
  vi.resetAllMocks(); vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(listServers).mockResolvedValue([{ id: "edge", name: "Edge" }] as Awaited<ReturnType<typeof listServers>>);
  vi.mocked(subscriptions.listProductUsers).mockResolvedValue({ users: [user("alice"), user("bob")], license_required: false });
  vi.mocked(subscriptions.listManagedNodes).mockResolvedValue({ nodes: [node], license_required: false });
  vi.mocked(subscriptions.listSubscriptionPlans).mockResolvedValue({ plans: [plan], license_required: false });
  vi.mocked(subscriptions.listSubscriptionTemplatePresets).mockResolvedValue({ presets: [{ id: "preset", name: "VLESS preset", description: "", protocol: "vless", node_type: "physical", inbound_tag: "in", tags: ["preset"], config: { server: "example.net", port: 443 }, client_template: { id: "{username}" } }], license_required: false });
  vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [], settings: { enabled: true, clash_template_id: null, surge_template_id: null, revision: "" }, can_manage: true, license_required: false });
  vi.mocked(listSubscriptionProfiles).mockResolvedValue({ profiles: [], license_required: false });
  vi.mocked(listTemporarySubscriptions).mockResolvedValue({ subscriptions: [], license_required: false });
  vi.mocked(listPrivateRoutes).mockResolvedValue({ nodes: [], candidates: [], used_nodes: 0, actions_today: 0, policy: { enabled: false, max_nodes: 2, daily_limit: 5, updated_at: "" }, license_required: false });
  vi.mocked(fetchAppMeta).mockResolvedValue({ short_links_enabled: true } as Awaited<ReturnType<typeof fetchAppMeta>>);
  vi.mocked(subscriptions.getSubscriptionFormatPreview).mockImplementation(async (username, format) => ({ username, client_format: format, nodes: [{ node_id: "a", name: "Alpha", protocol: "vless", available: true, reason: null }], warnings: [], license_required: false }));
  vi.mocked(subscriptions.listProductUserCredentials).mockResolvedValue({ username: "alice", credentials: [], license_required: false });
  vi.mocked(subscriptions.getProductUserTraffic).mockResolvedValue({ username: "alice", upload: 0, download: 0, total: 0, weighted_upload: 0, weighted_download: 0, charged_usage_bytes: 0, entries: [], license_required: false });
  vi.mocked(subscriptions.getProductUserQuota).mockResolvedValue({ quota: { username: "alice", is_active: true, has_plan: true, available: true, expired: false, over_quota: false, reset_enabled: true, reset_due: false, upload: 0, download: 0, weighted_upload: 0, weighted_download: 0, charged_usage_bytes: 0, traffic_limit_bytes: 1000, remaining_bytes: 1000, percent_used: 0, reset_day: 1 }, license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("React subscriptions view", { timeout: 20_000 }, () => {
  it("does not expose a late subscription link after switching users", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.createProductUserSubscriptionToken>>) => void;
    vi.mocked(subscriptions.createProductUserSubscriptionToken).mockReturnValue(new Promise(done => { resolve = done; }));
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Link" })); await selectBob();
    await act(async () => resolve({ subscription: token("alice"), license_required: false }));
    expect(screen.queryByLabelText("Subscription URL")).toBeNull(); expect(screen.getByText("Access for bob")).toBeTruthy();
    vi.mocked(subscriptions.createProductUserSubscriptionToken).mockResolvedValue({ subscription: token("bob"), license_required: false }); fireEvent.click(screen.getByRole("button", { name: "Link" })); await flush();
    expect((screen.getByLabelText("Subscription URL") as HTMLInputElement).value).toBe("https://sub.example/bob-secret");
  });
  it("drops late credential reads and clears an already shown secret on user change", async () => {
    const credential = { id: "c", username: "alice", email: "alice-client", node_id: "a", server_id: "edge", protocol: "vless", credential: { id: "alice-credential-secret" }, created_at: "", updated_at: "" } satisfies SubscriptionCredential;
    vi.mocked(subscriptions.listProductUserCredentials).mockResolvedValue({ username: "alice", credentials: [credential], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Creds" })); await flush(); expect(screen.getByText("alice-credential-secret")).toBeTruthy();
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.listProductUserCredentials>>) => void; vi.mocked(subscriptions.listProductUserCredentials).mockReturnValue(new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "Creds" })); await selectBob(); await act(async () => resolve({ username: "alice", credentials: [credential], license_required: false }));
    expect(screen.queryByText("alice-credential-secret")).toBeNull();
  });
  it("retains preset filling and structured node creation parameters", async () => {
    vi.mocked(subscriptions.createManagedNode).mockResolvedValue({ node, license_required: false }); render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "Nodes" }));
    fireEvent.change(screen.getByLabelText("Host"), { target: { value: "edge.example" } }); fireEvent.change(screen.getByLabelText("Port"), { target: { value: "8443" } }); fireEvent.click(screen.getByRole("button", { name: "Fill" }));
    expect((screen.getByLabelText("Client template") as HTMLTextAreaElement).value).toContain("{username}");
    fireEvent.click(screen.getByRole("button", { name: "Create node" })); await flush();
    expect(subscriptions.createManagedNode).toHaveBeenCalledWith(expect.objectContaining({ name: "VLESS preset", server_id: "edge", protocol: "vless", node_type: "physical", parent_id: null, target_node_id: null, inbound_tag: "in", tags: ["preset"], config: { server: "edge.example", port: 8443 } }));
  });
  it("rejects invalid preset ports instead of falling back to a valid default", async () => {
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "Nodes" }));
    const input = screen.getByLabelText("Port"), fill = screen.getByRole("button", { name: "Fill" }), create = screen.getByRole("button", { name: "Preset" });
    const config = screen.getByLabelText("Node config") as HTMLTextAreaElement;
    for (const value of ["-1", "0", "0.4", "65536", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "443" } }); fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
      fireEvent.click(fill); fireEvent.click(create); await flush();
      expect(subscriptions.createManagedNodeFromPreset).not.toHaveBeenCalled(); expect(config.value).toBe("{}");
      expect(screen.getByText("Port must be an integer from 1 to 65535, or empty to use the preset.")).toBeTruthy();
    }
    fireEvent.change(input, { target: { value: "443" } }); fireEvent.change(input, { target: { value: "" } });
    vi.mocked(subscriptions.createManagedNodeFromPreset).mockResolvedValue({ node, license_required: false });
    fireEvent.click(create); await flush();
    expect(subscriptions.createManagedNodeFromPreset).toHaveBeenCalledWith("preset", expect.objectContaining({ port: null }));
  });
  it("preserves assignment dates, timeout and explicit Agent apply choice", async () => {
    vi.mocked(subscriptions.assignSubscriptionPlan).mockResolvedValue({ user: user("alice"), plan, commands: [], provisioning_batches: [], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "Assign" }));
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-09-01" } }); fireEvent.change(screen.getByLabelText("Expire date"), { target: { value: "2026-10-01" } }); fireEvent.click(screen.getByRole("switch", { name: "Apply to nodes (restart Xray)" }));
    fireEvent.change(screen.getByLabelText("Command timeout"), { target: { value: "30000" } }); fireEvent.click(screen.getByRole("button", { name: "Assign plan" })); await flush();
    expect(subscriptions.assignSubscriptionPlan).toHaveBeenCalledWith("alice", { plan_id: "p", start_date: "2026-09-01", expire_date: "2026-10-01", queue_agent_commands: true, no_restart: false, command_timeout_ms: 30000 });
    expect(screen.getByLabelText("Provisioning batches")).toBeTruthy(); expect(screen.getByText(/A queued command is not confirmation/)).toBeTruthy();
  });
  it("refreshes credentials after an assignment even when an earlier credential read is in flight", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof subscriptions.listProductUserCredentials>>) => void;
    vi.mocked(subscriptions.listProductUserCredentials).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    vi.mocked(subscriptions.assignSubscriptionPlan).mockResolvedValue({ user: user("alice"), plan, commands: [], provisioning_batches: [], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Creds" }));
    fireEvent.click(screen.getByRole("tab", { name: "Assign" })); fireEvent.click(screen.getByRole("button", { name: "Assign plan" })); await flush();
    expect(subscriptions.listProductUserCredentials).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ username: "alice", credentials: [], license_required: false })); await flush();
    expect(subscriptions.listProductUserCredentials).toHaveBeenCalledTimes(2); expect(subscriptions.getProductUserQuota).toHaveBeenCalledWith("alice");
  });
  it("requires confirmation before resetting a link and retains format preview filtering", async () => {
    vi.mocked(subscriptions.resetProductUserSubscriptionToken).mockResolvedValue({ subscription: token("alice"), license_required: false });
    vi.mocked(subscriptions.getSubscriptionFormatPreview).mockResolvedValue({ username: "alice", client_format: "clash", nodes: [{ node_id: "a", name: "Unsupported node", protocol: "test", available: false, reason: "Format does not support protocol" }], warnings: [], license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Reset" })); expect(subscriptions.resetProductUserSubscriptionToken).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm reset" })); await flush();
    expect(subscriptions.resetProductUserSubscriptionToken).toHaveBeenCalledWith("alice"); expect((screen.getByLabelText("Format URL") as HTMLInputElement).value).toBe(""); expect(screen.getByText("Format does not support protocol")).toBeTruthy();
  });
  it("exports credential opt-in and confirms mapped catalog import", async () => {
    const catalog = { version: 1, users: [], nodes: [], plans: [], credentials: [] };
    vi.mocked(subscriptions.exportSubscriptionCatalog).mockResolvedValue({ catalog, license_required: false });
    vi.mocked(subscriptions.importSubscriptionCatalog).mockResolvedValue({ summary: { created_users: 0, updated_users: 0, created_nodes: 0, updated_nodes: 0, created_plans: 0, updated_plans: 0, imported_credentials: 0, warnings: [] }, license_required: false });
    render(<SubscriptionsView />); await flush(); fireEvent.click(screen.getByRole("switch", { name: "Export creds" })); fireEvent.click(screen.getByRole("button", { name: "Export" })); await flush();
    expect(subscriptions.exportSubscriptionCatalog).toHaveBeenCalledWith(true);
    fireEvent.change(screen.getByLabelText("Server map JSON"), { target: { value: '{"legacy-edge":"edge"}' } }); fireEvent.click(screen.getByRole("button", { name: "Import" })); expect(subscriptions.importSubscriptionCatalog).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Import catalog" })); await flush();
    expect(subscriptions.importSubscriptionCatalog).toHaveBeenCalledWith({ catalog, server_map: { "legacy-edge": "edge" }, import_credentials: false });
  });
});
