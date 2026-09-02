// @vitest-environment jsdom
import { act, cleanup, fireEvent, render as renderAnt, screen } from "@testing-library/react";
import zhCN from "antd/locale/zh_CN";
import { ConfigProvider } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlanManagementDialog from "./PlanManagementDialog";
import UserManagementDialog from "./UserManagementDialog";
import NodeManagementDialog from "./NodeManagementDialog";
import SubscriptionProfileDialog from "./SubscriptionProfileDialog";
import PrivateRoutedPolicyDialog from "./PrivateRoutedPolicyDialog";
import SubscriptionAccessPanel from "./SubscriptionAccessPanel";
import { getPlanManagement, removePlan, savePlan } from "../../services/plan-management";
import { getUserManagement, removeUser, saveUser } from "../../services/user-management";
import { getNodeManagement, removeNode, retryNodeRemoval, saveNode } from "../../services/node-management";
import { getSubscriptionAccess, setProductUserActive } from "../../services/subscriptions";
import { listSubscriptionTemplates } from "../../services/subscription-templates";
import { updateSubscriptionProfile } from "../../services/subscription-profiles";
import { listCustomRules, listOverrideScripts, listProxyProviders } from "../../services/subscription-customizations";
import { updatePrivateRoutePolicy } from "../../services/private-routed-nodes";
import type { ManagedNode, ProductUser, SubscriptionAccessResponse, SubscriptionPlan } from "../../domain/subscriptions";
import type { SubscriptionProfile } from "../../domain/subscription-profiles";

const render = (ui: Parameters<typeof renderAnt>[0]) => renderAnt(ui, { wrapper: ({ children }) => <ConfigProvider locale={zhCN}>{children}</ConfigProvider> });

vi.mock("../../services/plan-management", async importOriginal => ({ ...await importOriginal<typeof import("../../services/plan-management")>(), getPlanManagement: vi.fn(), removePlan: vi.fn(), savePlan: vi.fn() }));
vi.mock("../../services/user-management", async importOriginal => ({ ...await importOriginal<typeof import("../../services/user-management")>(), getUserManagement: vi.fn(), getUserRemoval: vi.fn(), retryUserRemoval: vi.fn(), removeUser: vi.fn(), saveUser: vi.fn() }));
vi.mock("../../services/node-management", async importOriginal => ({ ...await importOriginal<typeof import("../../services/node-management")>(), getNodeManagement: vi.fn(), getNodeRemoval: vi.fn(), retryNodeRemoval: vi.fn(), removeNode: vi.fn(), saveNode: vi.fn() }));
vi.mock("../../services/subscriptions", () => ({ getSubscriptionAccess: vi.fn(), syncSubscriptionAccess: vi.fn(), setProductUserActive: vi.fn() }));
vi.mock("../../services/subscription-templates", () => ({ listSubscriptionTemplates: vi.fn() }));
vi.mock("../../services/subscription-profiles", () => ({ updateSubscriptionProfile: vi.fn() }));
vi.mock("../../services/subscription-customizations", () => ({ listCustomRules: vi.fn(), listOverrideScripts: vi.fn(), listProxyProviders: vi.fn() }));
vi.mock("../../services/private-routed-nodes", () => ({ updatePrivateRoutePolicy: vi.fn() }));
const plan: SubscriptionPlan = { id: "p", name: "Basic", description: "", traffic_limit_gb: 30, traffic_limit_bytes: 30 * 1024 ** 3, cycle_days: 30, is_reset: true, reset_day: 1, node_ids: ["a"], node_multipliers: { a: 1.5 }, node_name_overrides: { a: "Fast" }, node_name_override_enabled: true, auto_speed_rules: [], node_speed_limits: { a: 10 }, node_device_limits: { a: 2 }, speed_limit_mbps: 5, device_limit: 1, traffic_mode: "oneway", created_at: "", updated_at: "" };
const node: ManagedNode = { id: "a", name: "Alpha", server_id: "edge", protocol: "vless", node_type: "physical", tags: [], enabled: true, config: { port: 443 }, client_template: { id: "client-{username}" }, created_at: "", updated_at: "" };
const user: ProductUser = { username: "alice", display_name: "Alice", role: "user", is_active: true, is_reset: true, reset_day: 1, created_at: "", updated_at: "" };
const access: SubscriptionAccessResponse = { username: "alice", managed: true, servers: [{ server_id: "edge", server_name: "Edge", status: "pending", command_id: "cmd", error: null, updated_at: "", entries: [{ inbound_tag: "vless", email: "alice", enabled: false, reason: "inactive" }] }], license_required: false };
const userDetail = { user, revision: "user-r1", credential_count: 1, blockers: [] as string[], warnings: [] as string[], access, limits: { traffic_limit_bytes: 0, speed_limit_mbps: 5, device_limit: 1, speed_source: "plan" as const, device_source: "plan" as const, nodes: [], warnings: [] } };
const nodeDetail = { node, revision: "node-r1", nodes: [{ id: "a", name: "Alpha" }], plans: [{ id: "p", name: "Basic" }], credential_count: 1, servers: [], blockers: [] as string[], warnings: [] as string[], access: [access] };
async function flush() { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); }
beforeEach(() => {
  vi.resetAllMocks(); vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(getPlanManagement).mockResolvedValue({ plan, revision: "plan-r1", users: [{ username: "alice", display_name: "Alice", is_active: true, managed: true }], warnings: [] });
  vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [], settings: { enabled: true, clash_template_id: null, surge_template_id: null, revision: "" }, can_manage: true, license_required: false });
  vi.mocked(listCustomRules).mockResolvedValue({ rules: [], license_required: false });
  vi.mocked(listProxyProviders).mockResolvedValue({ providers: [], license_required: false });
  vi.mocked(listOverrideScripts).mockResolvedValue({ scripts: [], runtime: "quickjs-subprocess", license_required: false });
  vi.mocked(getSubscriptionAccess).mockResolvedValue(access); vi.mocked(getUserManagement).mockResolvedValue(userDetail); vi.mocked(getNodeManagement).mockResolvedValue(nodeDetail);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("React subscription management dialogs", { timeout: 20_000 }, () => {
  it("preserves plan node overrides and sends the loaded revision, without claiming pending access applied", async () => {
    vi.mocked(savePlan).mockResolvedValue({ plan, revision: "plan-r2", affected_users: ["alice"], commands: [], warnings: [] });
    render(<PlanManagementDialog open id="p" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    expect(screen.getByLabelText("套餐名称").closest("form")?.style.paddingInline).toBe("8px");
    fireEvent.change(screen.getByLabelText("Alpha：计费倍率"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(savePlan).toHaveBeenCalledWith("p", expect.objectContaining({ node_multipliers: { a: 2 }, node_speed_limits: { a: 10 }, node_device_limits: { a: 2 }, node_name_overrides: { a: "Fast" } }), "plan-r1");
    expect(screen.getByText("待处理")).toBeTruthy(); expect(screen.queryByText("已应用")).toBeNull();
  });
  it("refuses to save a plan after all nodes are removed", async () => {
    render(<PlanManagementDialog open id="p" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    const remove = document.querySelector(".ant-select-selection-item-remove") as HTMLElement;
    expect(remove).toBeTruthy(); fireEvent.mouseDown(remove); fireEvent.click(remove); await flush();
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ }));
    expect(screen.getByText("套餐至少需要一个节点。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存" }) as HTMLButtonElement).disabled).toBe(true);
    expect(savePlan).not.toHaveBeenCalled();
  });
  it("requires username confirmation for unassignment, not the plan name", async () => {
    vi.mocked(removePlan).mockResolvedValue({ plan: null, revision: null, affected_users: [], commands: [], warnings: [] });
    render(<PlanManagementDialog open id="alice" mode="unassign" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ }));
    const input = screen.getByLabelText("确认用户名"); fireEvent.change(input, { target: { value: "Basic" } }); expect((screen.getByRole("button", { name: "取消分配" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "alice" } }); fireEvent.click(screen.getByRole("button", { name: "取消分配" })); await flush();
    expect(removePlan).toHaveBeenCalledWith("alice", "unassign", "plan-r1", "alice");
  });
  it("sends an invalid plan quota unchanged for backend rejection after blur and Enter", async () => {
    vi.mocked(savePlan).mockRejectedValue(new Error("traffic_limit_gb：输入值应大于 0。"));
    render(<PlanManagementDialog open id="p" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    const input = screen.getByLabelText("流量配额（GiB）");
    fireEvent.change(input, { target: { value: "-1" } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(savePlan).toHaveBeenCalledWith("p", expect.objectContaining({ traffic_limit_gb: -1 }), "plan-r1");
    expect(screen.getByText("traffic_limit_gb：输入值应大于 0。")).toBeTruthy();
  });
  it("keeps the exact Save accessible name while loading and after a rejected request", async () => {
    let rejectSave: (reason: Error) => void = () => { throw new Error("Save not started"); };
    vi.mocked(savePlan).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSave = reject; }));
    render(<PlanManagementDialog open id="p" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    const pending = screen.getByRole("button", { name: "保存" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true); expect(pending.getAttribute("aria-busy")).toBe("true");
    await act(async () => { rejectSave(new Error("版本已变化，请重新加载")); }); await flush();
    const retry = screen.getByRole("button", { name: "保存" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false); expect(retry.getAttribute("aria-busy")).toBe("false");
    expect(screen.getByText("版本已变化，请重新加载")).toBeTruthy();
  });
  it("keeps malformed per-node overrides distinct from an explicitly cleared inheritance", async () => {
    vi.mocked(savePlan).mockRejectedValue(new Error("计费倍率无效"));
    render(<PlanManagementDialog open id="p" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    const input = screen.getByLabelText("Alpha：计费倍率");
    fireEvent.change(input, { target: { value: "-" } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByRole("checkbox", { name: /我接受/ })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(savePlan).toHaveBeenLastCalledWith("p", expect.objectContaining({ node_multipliers: { a: Number.NaN } }), "plan-r1");
    fireEvent.change(input, { target: { value: "2" } }); fireEvent.change(input, { target: { value: "" } }); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(savePlan).toHaveBeenLastCalledWith("p", expect.objectContaining({ node_multipliers: {} }), "plan-r1");
  });
  it("honors user removal blockers and preserves disabled admin status controls", async () => {
    vi.mocked(getUserManagement).mockResolvedValue({ ...userDetail, blockers: ["不能移除管理员"] });
    const { rerender } = render(<UserManagementDialog open username="alice" mode="remove" removalId={null} nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    fireEvent.change(screen.getByLabelText("确认用户名"), { target: { value: "alice" } }); fireEvent.click(screen.getByRole("checkbox", { name: "我接受运行时重启及变更待确认的影响" }));
    expect((screen.getByRole("button", { name: "移除" }) as HTMLButtonElement).disabled).toBe(true); expect(removeUser).not.toHaveBeenCalled();
    vi.mocked(getUserManagement).mockResolvedValue({ ...userDetail, user: { ...user, role: "admin" } }); rerender(<UserManagementDialog open username="alice" mode="edit" removalId={null} nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    expect((screen.getByRole("switch", { name: "启用用户" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("saves user fields at the loaded revision and leaves conflicts visible", async () => {
    vi.mocked(saveUser).mockRejectedValue(new Error("版本已变化，请重新加载")); const onUpdated = vi.fn();
    render(<UserManagementDialog open username="alice" mode="edit" removalId={null} nodes={[node]} onOpenChange={vi.fn()} onUpdated={onUpdated} />); await flush();
    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "Support note" } }); fireEvent.click(screen.getByRole("checkbox", { name: "我接受运行时重启及变更待确认的影响" })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(saveUser).toHaveBeenCalledWith("alice", expect.objectContaining({ remark: "Support note" }), "user-r1"); expect(onUpdated).not.toHaveBeenCalled(); expect(screen.getByText("版本已变化，请重新加载")).toBeTruthy();
  });
  it("validates node JSON before write and retains the original revision on retry", async () => {
    vi.mocked(saveNode).mockResolvedValue({ ...nodeDetail, commands: [] }); render(<NodeManagementDialog open id="a" mode="edit" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    fireEvent.change(screen.getByLabelText("节点配置"), { target: { value: "[]" } }); fireEvent.click(screen.getByRole("checkbox", { name: /我接受 Xray/ })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(saveNode).not.toHaveBeenCalled(); expect(screen.getByText("节点配置 必须是 JSON 对象")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("节点配置"), { target: { value: '{"port":8443}' } }); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(saveNode).toHaveBeenCalledWith("a", expect.objectContaining({ config: { port: 8443 }, client_template: { id: "client-{username}" } }), "node-r1");
  });
  it("does not report a queued node removal as complete and offers a scoped retry", async () => {
    const job = { id: "job", node_id: "a", name: "Alpha", node_ids: ["a"], status: "failed" as const, servers: [], requested_at: "", completed_at: null, warnings: ["Agent 离线"], commands: [] };
    vi.mocked(removeNode).mockResolvedValue(job); vi.mocked(retryNodeRemoval).mockResolvedValue({ ...job, status: "pending" });
    const { getNodeRemoval } = await import("../../services/node-management"); vi.mocked(getNodeRemoval).mockResolvedValue(job);
    render(<NodeManagementDialog open id="a" mode="remove" nodes={[node]} onOpenChange={vi.fn()} />); await flush();
    fireEvent.change(screen.getByLabelText("确认节点名称"), { target: { value: "Alpha" } }); fireEvent.click(screen.getByRole("checkbox", { name: /我接受 Xray/ })); fireEvent.click(screen.getByRole("button", { name: "移除" })); await flush();
    expect(removeNode).toHaveBeenCalledWith("a", "node-r1", "Alpha", false); expect(screen.getByText("移除操作需要处理")).toBeTruthy(); expect(screen.queryByText("节点已移除")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试移除节点" })); await flush(); expect(retryNodeRemoval).toHaveBeenCalledWith("job"); expect(screen.getByText("正在等待 Agent 确认移除")).toBeTruthy();
  });
  it("keeps profile node subset, assignments and templates scoped in a revisioned update", async () => {
    const profile = { id: "profile", name: "Travel", description: "Subset", node_ids: ["a"], assigned_usernames: ["alice"], clash_template_id: "clash1", surge_template_id: null, enabled: false, revision: "profile-r1", migration_warnings: ["规则需要设置"] } as SubscriptionProfile;
    vi.mocked(updateSubscriptionProfile).mockResolvedValue(profile);
    render(<SubscriptionProfileDialog open profile={profile} nodes={[node]} users={[user]} templates={[]} onOpenChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("switch", { name: "已启用" })); fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(updateSubscriptionProfile).toHaveBeenCalledWith("profile", { name: "Travel", description: "Subset", node_ids: ["a"], assigned_usernames: ["alice"], clash_template_id: "clash1", surge_template_id: null, custom_rules_enabled: false, selected_custom_rule_ids: [], proxy_providers_enabled: false, selected_proxy_provider_ids: [], override_scripts_enabled: false, selected_override_script_ids: [], enabled: true, expected_revision: "profile-r1" });
    expect(screen.getByText("规则需要设置")).toBeTruthy();
  });
  it("validates private-route policy bounds before updating", async () => {
    render(<PrivateRoutedPolicyDialog open policy={null} onOpenChange={vi.fn()} />);
    const input = screen.getByLabelText("每位用户的路由数"), save = screen.getByRole("button", { name: "保存" }) as HTMLButtonElement;
    for (const value of ["2.5", "0", "21", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "2" } }); fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
      expect(save.disabled).toBe(true);
      fireEvent.click(save); await flush(); expect(updatePrivateRoutePolicy).not.toHaveBeenCalled();
    }
  });
  it("requires a confirmation before disabling a subscriber", async () => {
    vi.mocked(setProductUserActive).mockResolvedValue({ user: { ...user, is_active: false }, license_required: false });
    const updated = vi.fn(); render(<SubscriptionAccessPanel username="alice" isActive onUpdated={updated} />); await flush();
    fireEvent.click(screen.getByRole("switch", { name: "启用账户" })); expect(setProductUserActive).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "停用" })); await flush(); expect(setProductUserActive).toHaveBeenCalledWith("alice", false); expect(updated).toHaveBeenCalledWith({ ...user, is_active: false });
  });
});
