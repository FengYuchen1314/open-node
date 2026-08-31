// @vitest-environment jsdom
import { act, cleanup, configure, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand, AgentRead, ServerSummary } from "../../domain/inventory";
import { getPublicBranding } from "../../services/branding";
import { BrandingProvider } from "../hooks/useBranding";
import { createServer, createServerCommand, getLatestScanResult, getLatestTelemetry, listAgents, listCommandStreamFrames,
  listServerCommands, listServers, queueAgentOperation, updateServerProbeMetadata } from "../../services/inventory";
import DashboardView from "./DashboardView";
vi.mock("../../services/inventory", () => ({ createServer: vi.fn(), createServerCommand: vi.fn(), getLatestScanResult: vi.fn(), getLatestTelemetry: vi.fn(),
  listAgents: vi.fn(), listCommandStreamFrames: vi.fn(), listServerCommands: vi.fn(), listServers: vi.fn(), queueAgentOperation: vi.fn(), updateServerProbeMetadata: vi.fn() }));
vi.mock("../../services/branding", async original => ({ ...await original<typeof import("../../services/branding")>(), getPublicBranding: vi.fn() }));
vi.mock("../components/ServerTrafficPanel", () => ({ default: ({ servers }: { servers: ServerSummary[] }) => <div data-testid="traffic-panel">{servers.length}</div> }));
vi.mock("../components/AgentBootstrapDialog", () => ({ default: ({ open, serverId }: { open: boolean; serverId: string }) => open ? <div data-testid="bootstrap-dialog-target">{serverId}</div> : null }));
vi.mock("../components/AgentLifecycleDialog", () => ({ default: ({ open, serverId, action }: { open: boolean; serverId: string; action: string }) => open ? <div data-testid="lifecycle-dialog-target">{serverId}:{action}</div> : null }));
vi.mock("../components/ServerManagementDialog", () => ({ default: ({ open, serverId, mode }: { open: boolean; serverId: string; mode: string }) => open ? <div data-testid="management-dialog-target">{serverId}:{mode}</div> : null }));
vi.mock("../components/CommandInspector", () => ({ default: ({ commands }: { commands: AgentCommand[] }) => <div data-testid="dashboard-commands">{commands.map(command => `${command.id}:${command.status}`).join(",")}</div> }));
const edge: ServerSummary = { id: "edge", name: "Edge", ip_address: "192.0.2.1", status: "connected", connection_mode: "websocket", listen_port: 0, pull_port: 0,
  ipv6_enabled: true, traffic_limit: 0, xray_mode: "external", region_city: "Tokyo", provider_name: "Example", renewal_price: 10, renewal_currency: "USD",
  current_upload_speed: 1024, current_download_speed: 2048, created_at: "2026-08-31", updated_at: "2026-08-31" };
const other = { ...edge, id: "other", name: "Other", ip_address: "192.0.2.2", status: "offline" as const };
function cmd(overrides: Partial<AgentCommand> = {}): AgentCommand {
  return { id: "cmd", server_id: "edge", request_id: "request", method: "GET", path: "/api/child/system/info", query: "", timeout_ms: 30000,
    stream: false, status: "succeeded", attempts: 1, created_at: "2026-08-31", updated_at: "2026-08-31", ...overrides };
}
const agent: AgentRead = { id: "agent", server_id: "edge", hostname: "edge", agent_version: "0.3.0a0", connection_mode: "websocket", listen_port: 0, xray_mode: "external",
  warp_installed: false, registered_at: "2026-08-31", last_seen_at: "2026-08-31", capabilities: {
    rpc: true, stream: true, return_route_test: true, native_limiter: true, user_auto_speed_rules: true, subscription_access: true, node_cleanup: true,
    xray_config_workspace: true, agent_switch_xray_mode: true, agent_switch_listen_port: true, agent_probe_master_url: true, agent_update_master_url: true } };
async function flush() { await act(async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); }); }
async function mount() { const result = render(<DashboardView />); await flush(); return result; }
function getButton(name: string) {
  const labelled = screen.queryByLabelText(name, { selector: "button" });
  if (labelled) return labelled as HTMLButtonElement;
  const control = screen.getByText(name, { selector: "button > span:last-child" }).closest("button");
  if (!control) throw new Error(`Button is missing: ${name}`);
  return control;
}
function getDialog(title: string) {
  // rc-util intentionally reuses "test-id" in NODE_ENV=test. The visible title
  // identifies its own dialog without following another control's duplicate ID.
  const dialog = screen.getByText(title, { selector: ".ant-modal-title" }).closest('[role="dialog"]');
  if (!dialog) throw new Error(`Dialog is missing: ${title}`);
  return within(dialog as HTMLElement);
}
async function select(label: string, option: string) {
  fireEvent.mouseDown(screen.getByLabelText(label)); await flush();
  fireEvent.click(screen.getByText(option, { selector: ".ant-select-item-option-content" })); await flush();
}
function editNumber(input: HTMLElement, value: string, finish: "blur" | "Enter") {
  fireEvent.focus(input);
  fireEvent.keyDown(input, { key: "1" });
  fireEvent.change(input, { target: { value } });
  fireEvent.keyUp(input, { key: "1" });
  if (finish === "blur") fireEvent.blur(input);
  else fireEvent.keyDown(input, { key: "Enter" });
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  // Ant's full Dashboard has many controls. Visibility/layout belongs to the
  // browser gate; skip jsdom's expensive CSS cascade for semantic queries here.
  configure({ defaultHidden: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(listServers).mockResolvedValue([edge, other]); vi.mocked(listAgents).mockResolvedValue([]);
  vi.mocked(getLatestTelemetry).mockImplementation(async server_id => ({ server_id, latest: null, license_required: false }));
  vi.mocked(getLatestScanResult).mockImplementation(async server_id => ({ server_id, scan: null, license_required: false }));
  vi.mocked(listServerCommands).mockImplementation(async server_id => ({ server_id, commands: [], license_required: false }));
  vi.mocked(listCommandStreamFrames).mockResolvedValue({ server_id: "edge", command_id: "cmd", frames: [], license_required: false });
  vi.mocked(queueAgentOperation).mockResolvedValue({ command: cmd(), license_required: false });
  vi.mocked(createServerCommand).mockResolvedValue({ command: cmd(), license_required: false });
  vi.mocked(createServer).mockResolvedValue({ server: { ...edge, id: "new", name: "New edge" }, agent_token: "private-manual-agent-token", license_required: false });
  vi.mocked(updateServerProbeMetadata).mockResolvedValue({ server: edge, license_required: false });
});
afterEach(() => { cleanup(); configure({ defaultHidden: false }); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("React Dashboard workflows", () => {
  it("updates only the visible dashboard brand and keeps refresh and Agent descriptions intact", async () => {
    vi.mocked(getPublicBranding).mockResolvedValue({ site_title: "站点标题", brand_title: "品牌🧭".repeat(10), license_required: false });
    render(<BrandingProvider><DashboardView /></BrandingProvider>); await flush();
    expect(screen.getByRole("heading", { name: `${"品牌🧭".repeat(10)} 控制台` }).classList.contains("branding-block-text")).toBe(true);
    expect(screen.getByText("管理服务器、查看 Agent 遥测数据并下发操作，无需许可证。")).toBeTruthy();
    expect(getButton("刷新")).toBeTruthy(); expect(createServer).not.toHaveBeenCalled();
  }, 30000);
  it("loads server inventory and opens explicit bootstrap/edit/remove actions for the selected row", async () => {
    await mount(); expect(screen.getByText("192.0.2.1")).toBeTruthy(); expect(screen.getByTestId("traffic-panel").textContent).toBe("2");
    expect(getLatestTelemetry).toHaveBeenCalledWith("edge"); expect(getLatestScanResult).toHaveBeenCalledWith("other");
    fireEvent.click(getButton("在 Edge 上安装 Agent")); expect(screen.getByTestId("bootstrap-dialog-target").textContent).toBe("edge");
    fireEvent.click(getButton("编辑 Other")); expect(screen.getByTestId("management-dialog-target").textContent).toBe("other:edit");
    fireEvent.click(getButton("删除 Edge")); expect(screen.getByTestId("management-dialog-target").textContent).toBe("edge:remove");
    expect(queueAgentOperation).not.toHaveBeenCalled();
  }, 30000);
  it("creates the complete default request and keeps its manual token private and dismissible", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("名称"), { target: { value: " New edge " } });
    fireEvent.change(screen.getByLabelText("IPv4", { selector: "input[type=text]" }), { target: { value: "192.0.2.9" } });
    fireEvent.change(screen.getByLabelText("新服务器到期日期"), { target: { value: "2026-09-30" } });
    fireEvent.click(getButton("创建服务器")); await flush();
    expect(createServer).toHaveBeenCalledWith(expect.objectContaining({ name: "New edge", ip_address: "192.0.2.9", ip_address_v6: null,
      domain: null, domain_v6: null, expires_at: "2026-09-30T00:00:00Z", connection_mode: "auto", xray_mode: "external", listen_port: 23889, ipv6_enabled: true }));
    expect((screen.getByLabelText("Agent 令牌") as HTMLTextAreaElement).value).toBe("private-manual-agent-token");
    fireEvent.click(getButton("隐藏令牌")); expect(screen.queryByLabelText("Agent 令牌")).toBeNull();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  }, 30000);
  it("preserves all probe metadata fields and UTC expiry semantics", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("国家"), { target: { value: " JP " } });
    fireEvent.change(screen.getByLabelText("服务商地址"), { target: { value: "https://provider.example" } });
    fireEvent.change(screen.getByLabelText("到期日期"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("人民币价格"), { target: { value: "72" } });
    await select("周期", "月"); await select("电信互联", "付费");
    fireEvent.click(getButton("保存元数据")); await flush();
    expect(updateServerProbeMetadata).toHaveBeenCalledWith("edge", expect.objectContaining({ region_country: "JP", provider_url: "https://provider.example",
      expires_at: "2026-10-01T00:00:00Z", renewal_price_cny: 72, renewal_cycle: "month", telecom_paid_peer: true }));
    expect(screen.getByText("探针元数据已保存。")).toBeTruthy();
  }, 30000);
  it("fails closed for capability-dependent Agent settings and Xray file operations", async () => {
    await mount();
    for (const name of ["Xray 系统配置", "Xray 文件", "切换 Xray 模式", "应用监听端口", "探测", "更新"]) {
      const button = getButton(name) as HTMLButtonElement; expect(button.disabled).toBe(true); fireEvent.click(button);
    }
    expect(queueAgentOperation).not.toHaveBeenCalled();
    expect((getButton("Xray 配置") as HTMLButtonElement).disabled).toBe(false);
  }, 30000);
  it("queues advertised settings with explicit recovery semantics", async () => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("控制台地址"), { target: { value: " https://control.example " } });
    fireEvent.click(getButton("探测")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_probe_master_url", { master_url: "https://control.example" });
    fireEvent.click(getButton("更新")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_update_master_url", { master_url: "https://control.example", only_if_recovery: true });
    fireEvent.click(getButton("Xray 文件")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "xray_config_files_list", undefined);
  }, 30000);
  it("requires WARP terms and clears WARP+ credentials after success and target changes", async () => {
    await mount(); fireEvent.click(getButton("安装 WARP"));
    const dialog = getDialog("安装 WARP 免费版");
    expect((dialog.getByRole("button", { name: "安装" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: /我接受/ })); fireEvent.click(dialog.getByRole("button", { name: "安装" })); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "warp_install", { accept_terms: true });
    const input = screen.getByLabelText("WARP+ 凭据（选填）") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "private-warp-credential" } }); fireEvent.click(getButton("更新 WARP+")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "warp_license", { license: "private-warp-credential" }); expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "another-private-credential" } }); await select("目标服务器", "Other"); expect(input.value).toBe("");
  }, 30000);
  it("retains Xray release checksum/runtime-state controls and explicit removal confirmation", async () => {
    await mount(); fireEvent.click(getButton("安装 Xray"));
    fireEvent.change(screen.getByLabelText("Xray 版本"), { target: { value: "v26.9.1" } });
    let dialog = getDialog("安装 / 升级 Xray");
    expect((dialog.getByRole("button", { name: "安装" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("压缩包 SHA-256 校验和"), { target: { value: "d".repeat(64) } }); await select("运行状态", "已停止");
    fireEvent.click(dialog.getByRole("button", { name: "安装" })); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "xray_install", { version: "v26.9.1", sha256: "d".repeat(64), start: false });
    fireEvent.click(getButton("移除 Xray")); dialog = getDialog("移除 Xray");
    expect((dialog.getByRole("button", { name: "确认" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "确认更改运行时" })); fireEvent.click(dialog.getByRole("button", { name: "确认" })); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "xray_remove", {});
  }, 30000);
  it("resets log deletion consent whenever the target or file scope changes", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("日志文件名"), { target: { value: "agent.log" } });
    fireEvent.click(screen.getByLabelText("确认删除日志"));
    fireEvent.change(screen.getByLabelText("日志文件名"), { target: { value: "xray.log" } });
    expect((getButton("清空日志") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("确认删除日志")); await select("目标服务器", "Other");
    expect((screen.getByLabelText("确认删除日志") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByLabelText("全部文件")); fireEvent.click(screen.getByLabelText("确认删除日志"));
    fireEvent.click(getButton("清空日志")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("other", "log_files_delete", { all: true });
    expect((screen.getByLabelText("确认删除日志") as HTMLInputElement).checked).toBe(false);
  }, 30000);
  it("queues latency and return-route probes with bounded computed command timeouts", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("延迟探测目标"), { target: { value: "example.com,\nexample.net" } });
    fireEvent.click(getButton("下发延迟探测")); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "domain_latency", { domains: ["example.com", "example.net"], timeout_ms: 2000, allow_icmp: false, command_timeout_ms: 30000 });
    expect((screen.getByLabelText("延迟探测目标") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("电信主机"), { target: { value: "203.0.113.2" } });
    fireEvent.click(getButton("追踪回程路由")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "return_route_test", expect.objectContaining({ ip_version: 4, timeout_seconds: 25,
      command_timeout_ms: 30000, targets: [{ carrier: "telecom", host: "203.0.113.2", region: "", port: 80 }] }));
  }, 30000);
  it("validates custom JSON and preserves method/query/timeout/stream fields", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("JSON 请求体"), { target: { value: "{" } });
    fireEvent.click(getButton("下发命令")); await flush();
    expect(createServerCommand).not.toHaveBeenCalled(); expect(screen.getByText("命令请求体必须是有效的 JSON。")).toBeTruthy();
    await select("请求方法", "POST"); fireEvent.change(screen.getByLabelText("JSON 请求体"), { target: { value: '{"key":"value"}' } });
    fireEvent.change(screen.getByLabelText("查询参数"), { target: { value: "lines=10" } }); fireEvent.click(screen.getByLabelText("流式输出"));
    fireEvent.click(getButton("下发命令")); await flush();
    expect(createServerCommand).toHaveBeenCalledWith("edge", { method: "POST", path: "/api/child/system/info", query: "lines=10", body: { key: "value" }, timeout_ms: 30000, stream: true });
    expect((screen.getByLabelText("JSON 请求体") as HTMLTextAreaElement).value).toBe("");
  }, 30000);
  it("observes pending diagnostics and streams every two seconds, then stops after terminal state or unmount", async () => {
    vi.mocked(listServerCommands).mockImplementation(async server_id => ({ server_id,
      commands: server_id === "edge" ? [cmd({ status: "pending", stream: true, path: "/api/child/network/return-route-test" })] : [], license_required: false }));
    const { unmount } = await mount(); expect(listCommandStreamFrames).toHaveBeenCalledWith("edge", "cmd");
    expect(screen.getByTestId("dashboard-commands").textContent).toBe("cmd:pending");
    vi.mocked(listServerCommands).mockImplementation(async server_id => ({ server_id, commands: server_id === "edge" ? [cmd()] : [], license_required: false }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByTestId("dashboard-commands").textContent).toBe("cmd:succeeded");
    const calls = vi.mocked(listServerCommands).mock.calls.length; await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(listServerCommands).toHaveBeenCalledTimes(calls); unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(listServerCommands).toHaveBeenCalledTimes(calls);
  }, 30000);
  it("routes Agent lifecycle operations to the status-and-confirmation dialog", async () => {
    await mount(); fireEvent.click(getButton("升级 Agent"));
    expect(screen.getByTestId("lifecycle-dialog-target").textContent).toBe("edge:agent_upgrade"); expect(queueAgentOperation).not.toHaveBeenCalled();
  }, 30000);
  it("retains quick diagnostics, service control, logs, Nginx cleanup and release/status operations", async () => {
    await mount();
    for (const [label, kind] of [["系统信息", "system_info"], ["流量", "traffic"], ["速率", "speed"],
      ["服务", "services_status"], ["网卡", "system_nics"], ["扫描", "scan"], ["日志文件", "log_files_list"],
      ["Xray 发布版本", "xray_release"], ["安装 Nginx", "nginx_install"], ["移除 Nginx", "nginx_remove"],
      ["WARP 状态", "warp_status"], ["Nginx 配置", "nginx_config_read"], ["Nginx 文件", "nginx_config_files_list"]]) {
      fireEvent.click(getButton(label)); await flush();
      expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", kind, undefined);
    }
    fireEvent.click(getButton("重启 Xray")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "service_control", { service: "xray", action: "restart" });
    fireEvent.click(getButton("Agent 日志")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "logs", { service: "agent", lines: 200 });
    fireEvent.click(getButton("清理流转发")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "nginx_clear_stream_port", { port: 443 });
  }, 30000);
  it("never renders a late manual Agent token after the page unmounts", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof createServer>>) => void;
    vi.mocked(createServer).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { unmount } = await mount(); fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New edge" } });
    const button = getButton("创建服务器"); fireEvent.click(button); fireEvent.click(button); await flush();
    expect(createServer).toHaveBeenCalledOnce(); unmount();
    await act(async () => { resolve({ server: edge, agent_token: "late-private-token", license_required: false }); });
    expect(screen.queryByLabelText("Agent 令牌")).toBeNull(); expect(document.body.textContent).not.toContain("late-private-token");
  }, 30000);
  it("shows queue failures without discarding an unsubmitted custom body", async () => {
    vi.mocked(createServerCommand).mockRejectedValue(new Error("Command target is offline")); await mount();
    fireEvent.change(screen.getByLabelText("JSON 请求体"), { target: { value: '{"keep":"draft"}' } });
    fireEvent.click(getButton("下发命令")); await flush();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy(); expect(document.body.textContent).not.toContain("Command target is offline");
    expect((screen.getByLabelText("JSON 请求体") as HTMLTextAreaElement).value).toBe('{"keep":"draft"}');
  }, 30000);
  it("keeps the exact Queue command accessible name during loading and after a failed request", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(createServerCommand).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    await mount(); fireEvent.click(getButton("下发命令")); await flush();
    const form = within(screen.getByLabelText("JSON 请求体").closest("form")!);
    expect(form.getByRole("button", { name: "下发命令" })).toBeTruthy();
    await act(async () => { reject(new Error("Offline")); }); await flush();
    const button = form.getByRole("button", { name: "下发命令" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false); expect(button.getAttribute("aria-label")).toBe("下发命令");
  }, 30000);
  it.each([
    { label: "端口", button: "创建服务器", minimum: 0, maximum: 65535 },
    { label: "流量限额（字节）", button: "创建服务器", minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
    { label: "流转发端口", button: "清理流转发", minimum: 1, maximum: 65535 },
    { label: "监听端口", button: "应用监听端口", minimum: 0, maximum: 65535 },
    { label: "延迟探测超时", button: "下发延迟探测", minimum: 200, maximum: 10000 },
    { label: "路由探测超时（秒）", button: "追踪回程路由", minimum: 10, maximum: 45 },
    { label: "命令超时", button: "下发命令", minimum: 1000, maximum: 300000 },
  ])("rejects blank, negative, fractional and over-bound $label after real Ant blur and Enter", async ({ label, button, minimum, maximum }) => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New edge" } });
    fireEvent.change(screen.getByLabelText("延迟探测目标"), { target: { value: "example.com" } });
    fireEvent.change(screen.getByLabelText("电信主机"), { target: { value: "203.0.113.2" } });
    fireEvent.change(screen.getByLabelText("控制台地址"), { target: { value: "https://control.example" } });
    const input = screen.getByLabelText(label) as HTMLInputElement;
    expect(input.getAttribute("aria-valuemin")).toBe(String(minimum));
    expect(input.getAttribute("aria-valuemax")).toBe(String(maximum));
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["", "-1", `${minimum}.5`, String(maximum + 1)]) {
        editNumber(input, value, finish);
        if (value) expect(input.value).toBe(value);
        fireEvent.click(getButton(button)); await flush();
        expect(createServer).not.toHaveBeenCalled();
        expect(updateServerProbeMetadata).not.toHaveBeenCalled();
        expect(createServerCommand).not.toHaveBeenCalled();
        expect(queueAgentOperation).not.toHaveBeenCalled();
      }
    }
  }, 30000);
  it.each([
    { label: "新服务器续费价格", button: "创建服务器", key: "renewal_price" },
    { label: "续费价格", button: "保存元数据", key: "renewal_price" },
    { label: "人民币价格", button: "保存元数据", key: "renewal_price_cny" },
  ])("rejects invalid $label without silently clearing it, but permits explicit empty and decimal prices", async ({ label, button, key }) => {
    await mount(); fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New edge" } });
    const input = screen.getByLabelText(label) as HTMLInputElement;
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["-1", "-", "$1", "0x10", "1e", "1e999", "1e-999", " "]) {
        editNumber(input, value, finish); fireEvent.click(getButton(button)); await flush();
        expect(createServer).not.toHaveBeenCalled(); expect(updateServerProbeMetadata).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "2.5", "Enter"); fireEvent.click(getButton(button)); await flush();
    if (button === "创建服务器") {
      expect(createServer).toHaveBeenLastCalledWith(expect.objectContaining({ [key]: 2.5 }));
      fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New edge" } });
    } else expect(updateServerProbeMetadata).toHaveBeenLastCalledWith("edge", expect.objectContaining({ [key]: 2.5 }));
    editNumber(input, "", "blur"); fireEvent.click(getButton(button)); await flush();
    if (button === "创建服务器") expect(createServer).toHaveBeenLastCalledWith(expect.objectContaining({ [key]: null }));
    else expect(updateServerProbeMetadata).toHaveBeenLastCalledWith("edge", expect.objectContaining({ [key]: null }));
  }, 30000);
  it("does not reuse an old required quota for malformed or underflowed numeric drafts", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("名称"), { target: { value: "New edge" } });
    const input = screen.getByLabelText("流量限额（字节）");
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["-", "$1", "0x10", "1e", "1e999", "1e-999", " "]) {
        editNumber(input, value, finish); fireEvent.click(getButton("创建服务器")); await flush();
        expect(createServer).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "0", "Enter"); editNumber(screen.getByLabelText("端口"), "0", "blur");
    fireEvent.click(getButton("创建服务器")); await flush();
    expect(createServer).toHaveBeenCalledWith(expect.objectContaining({ traffic_limit: 0, listen_port: 0 }));
  }, 30000);
  it("rejects blank and invalid selected return-route ports instead of substituting port 80", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("电信主机"), { target: { value: "203.0.113.2" } });
    const input = screen.getByLabelText("电信端口");
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["", "-1", "80.5", "65536"]) {
        editNumber(input, value, finish); fireEvent.click(getButton("追踪回程路由")); await flush();
        expect(queueAgentOperation).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "443", "Enter"); fireEvent.click(getButton("追踪回程路由")); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "return_route_test", expect.objectContaining({
      targets: [{ carrier: "telecom", host: "203.0.113.2", region: "", port: 443 }],
    }));
  }, 30000);
  it("Enter on listen port applies only that explicitly validated setting, never the Master URL", async () => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("控制台地址"), { target: { value: "https://control.example" } });
    editNumber(screen.getByLabelText("监听端口"), "-1", "Enter"); await flush();
    expect(queueAgentOperation).not.toHaveBeenCalled();
    editNumber(screen.getByLabelText("监听端口"), "0", "Enter"); await flush();
    expect(queueAgentOperation).toHaveBeenCalledOnce();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "agent_switch_listen_port", { listen_port: 0 });
  }, 30000);
});
