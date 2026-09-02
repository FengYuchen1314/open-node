// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand, AgentCommandCreateResponse, AgentOperationKind, AgentRead, ServerSummary, XrayConfigSnapshot, XrayRuntimeInbound } from "../../domain/inventory";
import * as inventory from "../../services/inventory";
import * as subscriptions from "../../services/subscriptions";
import ConfigView from "./ConfigView";
import { installDom, renderUi as render } from "../test-utils";

// These suites render real interface tables/forms; allow bounded DOM work on the VPS.
vi.setConfig({ testTimeout: 30000 });

vi.mock("../../services/inventory", () => ({ acceptXrayConfigPendingRecovery: vi.fn(), applyXrayConfigRecovery: vi.fn(), createXrayRuntimeTunnelChain: vi.fn(), deleteXrayRuntimeTunnel: vi.fn(), deployXrayRuntimeTunnel: vi.fn(), getXrayRuntimeInventory: vi.fn(), getXrayRuntimeTunnelInventory: vi.fn(), listCommandStreamFrames: vi.fn(), listAgents: vi.fn(), listServerCommands: vi.fn(), listServers: vi.fn(), listXrayConfigSnapshots: vi.fn(), queueAgentOperation: vi.fn(), restoreXrayConfigSnapshot: vi.fn() }));
vi.mock("../../services/subscriptions", () => ({ cleanupExtraXrayRuntimeCredentials: vi.fn(), createManagedNodeFromRuntimeInbound: vi.fn(), getXrayRuntimeCredentialReconciliation: vi.fn(), getXrayRuntimeNodeReconciliation: vi.fn(), importManagedNodesFromRuntimeInbounds: vi.fn(), listXrayRuntimeNodeDrafts: vi.fn(), repairMissingXrayRuntimeCredentials: vi.fn(), syncManagedNodeFromRuntime: vi.fn() }));
vi.mock("../components/CommandInspector", () => ({ default: ({ commands, streamFramesByCommand }: { commands: AgentCommand[]; streamFramesByCommand: Record<string, unknown[]> }) => <div data-testid="command-inspector">{commands.map((command) => `${command.id}:${command.status}:${streamFramesByCommand[command.id]?.length ?? 0}`).join(",")}</div> }));
vi.mock("../components/LimiterPanel", () => ({ default: ({ serverId }: { serverId: string }) => <div data-testid="limiter-server">{serverId}</div> }));
const sha = "a".repeat(64), otherSha = "b".repeat(64);
const server: ServerSummary = { id: "edge", name: "Edge", domain: "edge.example", pull_address: "proxy.example", status: "connected", connection_mode: "http", listen_port: 0, pull_port: 0, ipv6_enabled: false, traffic_limit: 0, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0, created_at: "2026-08-31", updated_at: "2026-08-31" };
const agent: AgentRead = { id: "agent", server_id: "edge", hostname: "edge", agent_version: "0.3.0a0", connection_mode: "http", listen_port: 0, xray_mode: "external", capabilities: { rpc: true, stream: true, return_route_test: true, native_limiter: true, user_auto_speed_rules: true, subscription_access: true, node_cleanup: true, xray_config_workspace: true, agent_switch_xray_mode: true, agent_switch_listen_port: true, agent_probe_master_url: true, agent_update_master_url: true, managed_protocols: true }, warp_installed: false, registered_at: "2026-08-31", last_seen_at: "2026-08-31" };
const inbound: XrayRuntimeInbound = { source_index: 0, tag: "vless-443", display_name: "VLESS inbound", protocol: "vless", port: 443, listen: "0.0.0.0", network: "tcp", security: "tls", client_container: "clients", client_count: 1, user_emails: ["alice@example.com"], sniffing_enabled: true, sniffing_dest_override: ["http", "tls"], sniffing_exclude_domains: ["excluded.example"], traffic: { uplink: 1024, downlink: 2048 }, user_traffic: { uplink: 512, downlink: 256 }, remarks: ["unsupported_draft"] };
let history: Record<string, AgentCommand[]>;
const now = "2026-08-31T03:00:00Z";
function command(path: string, body: unknown, patch: Partial<AgentCommand> = {}): AgentCommand { return { id: "read", server_id: "edge", request_id: "request", method: "GET", path, query: "", timeout_ms: 30000, stream: false, status: "succeeded", attempts: 1, result_status: 200, result_body: body, created_at: now, updated_at: now, ...patch }; }
function systemResult(patch: Record<string, unknown> = {}) {
  return { sha256: sha, config: { log_level: "info", dns: { servers: ["1.1.1.1"], queryStrategy: "UseIPv4" }, policy: { levels: { "0": { handshake: 4 } }, system: { statsInboundUplink: true } }, metrics_enabled: true, metrics_listen: "127.0.0.1:11111", stats_enabled: true, grpc_enabled: true, grpc_port: 46736, writable: true, api_mode: "routed", grpc_disable_supported: false, grpc_port_writable: true, ...patch } };
}
const snapshot = (status: XrayConfigSnapshot["status"]): XrayConfigSnapshot => ({ id: status, server_id: "edge", config_hash: status === "current" ? sha : otherSha, source: "agent_report", status, size_bytes: 100, created_at: now });
async function flush() { await act(async () => { for (let i = 0; i < 24; i += 1) await Promise.resolve(); }); }
async function tab(name: string) { fireEvent.click(screen.getByRole("tab", { name })); await flush(); return within(screen.getByRole("tabpanel", { name })); }
function card(title: string) { return within(screen.getByText(title, { selector: ".ui-card-title" }).closest(".ui-card")!); }
async function selectOption(label: string, option: string) {
  fireEvent.mouseDown(screen.getByLabelText(label)); await flush();
  const node = screen.getAllByText(option).find((item) => item.closest(".ui-option"));
  if (!node) throw new Error(`Missing option ${option}`);
  fireEvent.click(node); await flush();
}
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); history = { edge: [], other: [] };
  installDom();
  vi.mocked(inventory.listServers).mockResolvedValue([server, { ...server, id: "other", name: "Other", domain: "other.example" }]);
  vi.mocked(inventory.listAgents).mockResolvedValue([agent, { ...agent, id: "agent-other", server_id: "other" }]);
  vi.mocked(inventory.listServerCommands).mockImplementation(async (serverId) => ({ server_id: serverId, commands: history[serverId] ?? [], license_required: false }));
  vi.mocked(inventory.listCommandStreamFrames).mockResolvedValue({ server_id: "edge", command_id: "stream", frames: [], license_required: false });
  vi.mocked(inventory.listXrayConfigSnapshots).mockImplementation(async (serverId, options) => ({ server_id: serverId, snapshots: [snapshot("current"), snapshot("pending_recovery")].map((item) => ({ ...item, server_id: serverId, ...(options?.withConfig ? { config: '{"snapshot":true}' } : {}) })), license_required: false }));
  vi.mocked(inventory.queueAgentOperation).mockImplementation(async (serverId, kind) => {
    const paths: Partial<Record<AgentOperationKind, string>> = { xray_system_config_read: "/api/child/xray/system-config", xray_config_file_read: "/api/child/xray/config-files", xray_config_files_list: "/api/child/xray/config-files" };
    const queued = command(paths[kind] ?? `/queued/${kind}`, null, { id: `queued-${kind}`, server_id: serverId, status: "pending", result_status: null, method: kind.endsWith("read") || kind.endsWith("list") ? "GET" : "POST", created_at: "2026-08-31T04:00:00Z" });
    history[serverId] = [queued, ...(history[serverId] ?? [])]; return { command: queued, license_required: false };
  });
  vi.mocked(inventory.getXrayRuntimeInventory).mockImplementation(async (serverId) => ({ server_id: serverId, has_scan: true, xray_running: true, xray_version: "v26.3.27", xray_capabilities: {}, api_port: 46736, config_modified: false, config_added_sections: [], inbound_count: 1, client_count: 1, protocol_counts: { vless: 1 }, traffic: inbound.traffic, user_traffic: inbound.user_traffic, inbounds: [inbound], updated_at: now, license_required: false }));
  vi.mocked(inventory.getXrayRuntimeTunnelInventory).mockResolvedValue({ server_id: "edge", has_config: true, tunnel_count: 1, chain_count: 1, tunnels: [{ kind: "routed", tag: "outbound-tunnel", target_address: "2001:db8::1", target_port: 443, inbound_tag: "vless-443", match_domains: ["routed.example"], match_ips: ["192.0.2.0/24"], rule_index: 2 }], chains: [{ label: "relay", entry_port: 19000, final_target: "final.example:443", hops: [{ tag: "hop", listen_port: 19000, target_address: "final.example", target_port: 443 }] }], warnings: [], license_required: false });
  vi.mocked(subscriptions.listXrayRuntimeNodeDrafts).mockResolvedValue({ server_id: "edge", has_scan: true, drafts: [{ source_index: 0, source_display_name: "VLESS inbound", draft: { name: "Draft node", server_id: "edge", protocol: "vless", config: { host: "edge.example", port: 443 }, node_type: "physical" }, create_available: true, warnings: [] }], license_required: false });
  vi.mocked(subscriptions.getXrayRuntimeNodeReconciliation).mockResolvedValue({ server_id: "edge", has_scan: true, runtime_count: 1, managed_node_count: 1, managed_runtime_count: 0, unmanaged_runtime_count: 1, unavailable_runtime_count: 0, in_sync_count: 0, stale_count: 1, missing_runtime_count: 1, catalog_only_count: 0, runtime_entries: [{ source_index: 0, source_display_name: "VLESS inbound", protocol: "vless", status: "unmanaged", warnings: [] }], managed_entries: [{ node_id: "node", node_name: "Stale node", protocol: "vless", node_type: "physical", enabled: true, status: "stale", runtime_source_index: 0, runtime_display_name: "VLESS inbound", drifts: [{ field: "host", managed_value: "old.example", runtime_value: "edge.example" }] }, { node_id: "missing", node_name: "Missing node", protocol: "vless", node_type: "physical", enabled: true, status: "missing_runtime", drifts: [] }], license_required: false });
  vi.mocked(subscriptions.getXrayRuntimeCredentialReconciliation).mockResolvedValue({ server_id: "edge", has_scan: true, node_count: 1, expected_credential_count: 2, matched_runtime_client_count: 1, in_sync_count: 0, missing_runtime_count: 0, out_of_sync_count: 1, missing_runtime_client_count: 1, extra_runtime_client_count: 1, entries: [{ node_id: "node", node_name: "Stale node", protocol: "vless", inbound_tag: "vless-443", enabled: true, expected_emails: ["alice@example.com", "missing@example.com"], runtime_emails: ["alice@example.com", "extra@example.com"], missing_runtime_emails: ["missing@example.com"], extra_runtime_emails: ["extra@example.com"], status: "drift" }], license_required: false });
  vi.mocked(inventory.acceptXrayConfigPendingRecovery).mockResolvedValue({ server_id: "edge", current: { ...snapshot("current"), config_hash: otherSha }, snapshots: [{ ...snapshot("current"), config_hash: otherSha }], license_required: false });
  vi.mocked(inventory.applyXrayConfigRecovery).mockResolvedValue({ server_id: "edge", snapshot: snapshot("current"), commands: [], command_count: 2, merged_agent_only_count: 1, warnings: ["Merged agent inbound"], license_required: false });
  vi.mocked(inventory.restoreXrayConfigSnapshot).mockResolvedValue({ command: command("/restore", null), license_required: false });
  vi.mocked(inventory.deployXrayRuntimeTunnel).mockResolvedValue({ server_id: "edge", server_name: "Edge", domain: "edge.example", cert_name: "edge.example", nginx_config: "", domain_config: "", xray_config: "", command_previews: [], commands: [], command_count: 0, warnings: [], license_required: false });
  vi.mocked(inventory.createXrayRuntimeTunnelChain).mockResolvedValue({ label: "relay", entry_server_id: "edge", entry_host: "edge.example", entry_port: 19000, final_target: "127.0.0.1:443", hops: [], command_previews: [], commands: [], scan_commands: [], command_count: 0, warnings: [], license_required: false });
  vi.mocked(inventory.deleteXrayRuntimeTunnel).mockResolvedValue({ server_id: "edge", has_config: true, target_kind: "routed", command_previews: [], commands: [], command_count: 0, warnings: [], license_required: false });
  vi.mocked(subscriptions.importManagedNodesFromRuntimeInbounds).mockResolvedValue({ server_id: "edge", has_scan: true, created_nodes: [], existing_nodes: [], skipped: [], created_count: 1, existing_count: 0, skipped_count: 0, license_required: false });
  const node = { id: "node", name: "Managed node", server_id: "edge", protocol: "vless", config: { host: "edge.example", port: 443 }, node_type: "physical" as const, enabled: true, tags: [], client_template: {}, created_at: now, updated_at: now };
  vi.mocked(subscriptions.createManagedNodeFromRuntimeInbound).mockResolvedValue({ node, license_required: false });
  vi.mocked(subscriptions.syncManagedNodeFromRuntime).mockResolvedValue({ server_id: "edge", node, source_index: 0, source_display_name: "VLESS inbound", updated_fields: ["host"], drifts_before: [], drifts_after: [], license_required: false });
  vi.mocked(subscriptions.repairMissingXrayRuntimeCredentials).mockResolvedValue({ server_id: "edge", has_scan: true, entries: [], provisioning_batches: [], commands: [], planned_client_count: 1, batch_count: 1, warnings: [], license_required: false });
  vi.mocked(subscriptions.cleanupExtraXrayRuntimeCredentials).mockResolvedValue({ server_id: "edge", has_scan: true, entries: [], command_previews: [], commands: [], planned_client_count: 1, command_count: 0, warnings: [], license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("React configuration workspace", () => {
  it("keeps a shared server inside its official node and credential scope", async () => {
    vi.mocked(inventory.listServers).mockResolvedValue([{
      ...server,
      is_federated: true,
      federation_owner_url: "https://owner.example",
      federation_prefix: "shared-",
      federation_allow_manage_xray: false,
    }]);
    vi.mocked(inventory.listAgents).mockResolvedValue([]);
    render(<ConfigView />); await flush();
    expect(screen.getByText("分享服务器由拥有方控制")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "运行时" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Xray" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Nginx" })).toBeNull();
    expect(screen.queryByText("部署隧道", { selector: ".ui-card-title" })).toBeNull();
    expect(screen.queryByText("运行时操作", { selector: ".ui-card-title" })).toBeNull();
    expect(screen.getByRole("button", { name: "导入缺失节点" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "补齐客户端" })).toBeTruthy();
  });

  it("loads in StrictMode and keeps all seven workflow tabs", async () => {
    render(<StrictMode><ConfigView /></StrictMode>); await flush();
    for (const name of ["Xray", "系统", "运行时", "限制", "Nginx", "网站", "文件"]) expect(screen.getByRole("tab", { name })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "目标服务器" }).closest(".ui-select")?.textContent).toContain("Edge");
    expect(screen.queryByTestId("limiter-server")).toBeNull(); await tab("限制"); expect(screen.getByTestId("limiter-server").textContent).toBe("edge");
  });
  it("queues raw Xray test/write without changing payload semantics", async () => {
    render(<ConfigView />); await flush(); const panel = within(screen.getByRole("tabpanel", { name: "Xray" }));
    fireEvent.change(panel.getByLabelText("Xray 配置"), { target: { value: '{"inbounds":[]}' } });
    fireEvent.click(panel.getByRole("button", { name: "测试" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_test_config", { config: '{"inbounds":[]}' });
    fireEvent.click(panel.getByRole("switch", { name: "强制" })); fireEvent.click(panel.getByRole("button", { name: "写入" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_write", { config: '{"inbounds":[]}', path: null, force: true });
  });
  it("unlocks supported system fields only from a successful read and preserves DNS/policy objects", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("系统");
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" }));
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(false);
    expect((panel.getByRole("switch", { name: "Xray gRPC API" }) as HTMLButtonElement).disabled).toBe(true);
    expect((panel.getByLabelText("Xray gRPC API 端口") as HTMLInputElement).disabled).toBe(false);
    fireEvent.change(panel.getByLabelText("DNS JSON"), { target: { value: "[]" } }); expect((panel.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(panel.getByLabelText("DNS JSON"), { target: { value: '{"servers":["9.9.9.9"],"queryStrategy":"UseIPv4"}' } });
    fireEvent.click(panel.getByRole("button", { name: "写入" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_system_config_write", { log_level: "info", metrics_enabled: true, metrics_listen: "127.0.0.1:11111", stats_enabled: true, grpc_enabled: true, grpc_port: 46736, dns: { servers: ["9.9.9.9"], queryStrategy: "UseIPv4" }, policy: { levels: { "0": { handshake: 4 } }, system: { statsInboundUplink: true } }, expected_sha256: sha });
    expect((panel.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("再次写入前，请重新读取当前 Xray 系统配置。")).toBeTruthy();
  });
  it("does not fall back to an older successful system GET while a newer read is pending", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("系统");
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" })); fireEvent.click(panel.getByRole("button", { name: "读取" })); await flush();
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" }));
    expect((panel.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("暂无已完成的 Xray 系统配置结果。")).toBeTruthy();
  });
  it("honors fixed gRPC endpoints and server capability gates", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult({ grpc_port_writable: false, fixed_stats_address: "127.0.0.1:46736" }))];
    vi.mocked(inventory.listAgents).mockResolvedValue([agent]); render(<ConfigView />); await flush(); const panel = await tab("系统");
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" }));
    expect((panel.getByLabelText("Xray gRPC API 端口") as HTMLInputElement).disabled).toBe(true);
    expect((panel.getByRole("switch", { name: "指标" }) as HTMLButtonElement).disabled).toBe(false);
    await selectOption("目标服务器", "Other");
    expect((panel.getByRole("button", { name: "读取" }) as HTMLButtonElement).disabled).toBe(true);
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.getByText(/请先安装新版 Open Node Agent 并使其连接控制台/)).toBeTruthy();
  });
  it.each(["config.jsonc", "CONFIG.JSONC"])("never unlocks %s, even when an Agent claims writable", async (filename) => {
    history.edge = [command("/api/child/xray/config-files", { content: "// comment\n{}", path: `/etc/xray/${filename}`, sha256: sha, writable: true })];
    render(<ConfigView />); await flush(); await tab("文件"); const file = within(screen.getByText("Xray 文件").closest(".ui-card")!);
    fireEvent.click(file.getByRole("button", { name: "使用最新结果" }));
    expect((file.getByLabelText("文件") as HTMLInputElement).value).toBe(filename);
    expect((file.getByLabelText("内容") as HTMLTextAreaElement).disabled).toBe(true);
    expect((file.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("binds a writable file to its exact name/revision and requires another read after writes", async () => {
    history.edge = [command("/api/child/xray/config-files", { content: "{}", path: "/etc/xray/xray.json", sha256: sha, writable: true })];
    render(<ConfigView />); await flush(); await tab("文件"); const file = within(screen.getByText("Xray 文件").closest(".ui-card")!);
    fireEvent.click(file.getByRole("button", { name: "使用最新结果" }));
    fireEvent.change(file.getByLabelText("内容"), { target: { value: '{"log":{}}' } }); fireEvent.click(file.getByRole("button", { name: "写入" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_file_write", { file: "xray.json", content: '{"log":{}}', expected_sha256: sha });
    expect((file.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(file.getByRole("button", { name: "使用最新结果" }));
    fireEvent.change(file.getByLabelText("文件"), { target: { value: "different.json" } });
    expect((file.getByLabelText("内容") as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.change(file.getByLabelText("文件"), { target: { value: "xray.json" } });
    expect((file.getByLabelText("内容") as HTMLTextAreaElement).disabled).toBe(true);
  });
  it("uses file lists only to select the primary file, not to unlock editing", async () => {
    history.edge = [command("/api/child/xray/config-files", { files: { main: [{ name: "primary.json" }] } })];
    render(<ConfigView />); await flush(); await tab("文件"); const file = within(screen.getByText("Xray 文件").closest(".ui-card")!);
    fireEvent.click(file.getByRole("button", { name: "使用最新结果" }));
    expect((file.getByLabelText("文件") as HTMLInputElement).value).toBe("primary.json"); expect((file.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(file.getByRole("button", { name: "读取" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_file_read", { file: "primary.json" });
  });
  it("loads snapshots, blocks direct pending restore and applies current with the recovery contract", async () => {
    render(<ConfigView />); await flush();
    const pending = screen.getByText("待恢复", { selector: ".ui-tag" }).closest("tr")!; expect((within(pending).getByRole("button", { name: "恢复" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(pending).getByRole("button", { name: "载入" })); await flush();
    expect(inventory.listXrayConfigSnapshots).toHaveBeenCalledWith("edge", { limit: 8, withConfig: true });
    expect((screen.getByLabelText("Xray 配置") as HTMLTextAreaElement).value).toBe('{"snapshot":true}');
    fireEvent.click(screen.getByRole("button", { name: "应用当前配置" })); await flush();
    expect(inventory.applyXrayConfigRecovery).toHaveBeenCalledWith("edge", { restart_xray: true, merge_agent_only: true, command_timeout_ms: 60000 });
    expect(screen.getByText(/已合并 1 个仅存在于 Agent 的条目/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "接受" })); await flush();
    expect(inventory.acceptXrayConfigPendingRecovery).toHaveBeenCalledWith("edge"); expect(screen.queryByText("待恢复")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "恢复" })); await flush(); expect(inventory.restoreXrayConfigSnapshot).toHaveBeenCalledWith("edge", "current");
  });
  it("preserves native tunnel deployment fields and rejects colliding listener ports", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("部署隧道");
    expect((panel.getByLabelText("域名") as HTMLInputElement).value).toBe("edge.example");
    fireEvent.click(panel.getByText("监听配置"));
    fireEvent.change(panel.getByLabelText("Nginx 端口"), { target: { value: "443" } });
    expect((panel.getByRole("button", { name: "部署隧道" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(panel.getByLabelText("Nginx 端口"), { target: { value: "8001" } });
    fireEvent.click(panel.getByRole("button", { name: "部署隧道" })); await flush();
    expect(inventory.deployXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { domain: "edge.example", proxy_domain: "proxy.example", site_type: "static", site_value: null, listen_address: "0.0.0.0", listen_port: 443, nginx_port: 8001, forward_port: 46174, api_port: 46736, metrics_port: 38889, cert_name: "edge.example", clear_stream_port: true, restart_xray: true, force: false, queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("never clamps an invalid listener port into a deployable port on blur or Enter", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("部署隧道");
    fireEvent.click(panel.getByText("监听配置")); const input = panel.getByLabelText("公网端口");
    fireEvent.change(input, { target: { value: "-1" } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.deployXrayRuntimeTunnel).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("-1");
    expect((panel.getByRole("button", { name: "部署隧道" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("never turns a negative or blank chain entry port into automatic port zero", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("链式隧道"); const input = panel.getByLabelText("入口端口");
    for (const value of ["-1", ""]) {
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input);
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
      expect(inventory.createXrayRuntimeTunnelChain).not.toHaveBeenCalled();
      expect((input as HTMLInputElement).value).toBe(value);
      expect((panel.getByRole("button", { name: "创建链式隧道" }) as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.change(input, { target: { value: "0" } }); fireEvent.blur(input);
    fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).toHaveBeenCalledWith(expect.objectContaining({ entry_port: 0 }));
  });
  it("does not restore an old chain port while an incomplete numeric draft is being edited", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("链式隧道"); const input = panel.getByLabelText("入口端口");
    fireEvent.focus(input); fireEvent.keyDown(input, { key: "-" }); fireEvent.change(input, { target: { value: "-" } }); fireEvent.keyUp(input, { key: "-" });
    expect((input as HTMLInputElement).value).toBe("-");
    expect((panel.getByRole("button", { name: "创建链式隧道" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.blur(input); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).not.toHaveBeenCalled(); expect(["-", ""]).toContain((input as HTMLInputElement).value);
    expect((panel.getByRole("button", { name: "创建链式隧道" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("never clamps an out-of-range gRPC API port before a revision-bound write", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("系统");
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" })); const input = panel.getByLabelText("Xray gRPC API 端口");
    fireEvent.change(input, { target: { value: "65536" } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.queueAgentOperation).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("65536");
    expect((panel.getByRole("button", { name: "写入" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("retains ordered chain hops and scan-after-apply", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("链式隧道");
    fireEvent.click(panel.getByRole("button", { name: "下移第 1 跳" }));
    fireEvent.click(panel.getByRole("button", { name: "创建链式隧道" })); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).toHaveBeenCalledWith({ label: "relay", server_ids: ["other", "edge"], entry_port: 19000, target_address: "127.0.0.1", target_port: 443, queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("keeps routed and chain deletion selectors distinct and scans after apply", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("运行时隧道");
    expect(screen.getByText(/\[2001:db8::1\]:443/)).toBeTruthy();
    fireEvent.click(panel.getByRole("button", { name: "删除" })); await flush();
    expect(inventory.deleteXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { kind: "routed", tag: "outbound-tunnel", rule_index: 2, queue_agent_commands: true, queue_scan_after_apply: true });
    fireEvent.click(panel.getByRole("button", { name: "删除链式隧道" })); await flush();
    expect(inventory.deleteXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { kind: "chain", label: "relay", queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("retains bulk import and individual runtime-node creation", async () => {
    render(<ConfigView />); await flush(); await tab("运行时");
    fireEvent.click(card("运行时清单").getByRole("button", { name: "导入缺失节点" })); await flush(); expect(subscriptions.importManagedNodesFromRuntimeInbounds).toHaveBeenCalledWith("edge");
    fireEvent.click(card("运行时入站").getByRole("button", { name: "创建节点" })); await flush(); expect(subscriptions.createManagedNodeFromRuntimeInbound).toHaveBeenCalledWith("edge", { source_index: 0 });
  });
  it("keeps node-catalog mutations out of the server-settings embedding", async () => {
    render(<ConfigView allowNodeCatalogMutations={false} />); await flush(); await tab("运行时");
    expect(card("运行时清单").queryByRole("button", { name: "导入缺失节点" })).toBeNull();
    expect(card("运行时入站").queryByRole("button", { name: "创建节点" })).toBeNull();
    expect(card("受管理节点核对").queryByRole("button", { name: "同步" })).toBeNull();
    expect(screen.getAllByText("扫描备注").length).toBeGreaterThan(0);
  });
  it("allows stale-node sync while keeping missing-runtime nodes locked", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("受管理节点核对");
    const sync = panel.getAllByRole("button", { name: "同步" }); expect((sync[1] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(sync[0]); await flush(); expect(subscriptions.syncManagedNodeFromRuntime).toHaveBeenCalledWith("edge", "node", { source_index: 0 });
  });
  it("retains both missing-client repair and extra-client cleanup workflows", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("运行时清单");
    expect(card("凭据核对").getByText("缺失：missing@example.com")).toBeTruthy(); expect(card("凭据核对").getByText("多余：extra@example.com")).toBeTruthy();
    fireEvent.click(panel.getByRole("button", { name: "补齐客户端" })); await flush(); expect(subscriptions.repairMissingXrayRuntimeCredentials).toHaveBeenCalledWith("edge", { queue_agent_commands: true, queue_scan_after_apply: true });
    fireEvent.click(panel.getByRole("button", { name: "清理多余客户端" })); await flush(); expect(subscriptions.cleanupExtraXrayRuntimeCredentials).toHaveBeenCalledWith("edge", { queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("preserves advanced runtime operations and validates JSON payload shape", async () => {
    render(<ConfigView />); await flush(); await tab("运行时"); const panel = card("运行时操作");
    fireEvent.click(panel.getByRole("button", { name: "路由" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "routing_read", undefined);
    fireEvent.change(panel.getByLabelText("请求内容"), { target: { value: "[]" } }); fireEvent.click(panel.getByRole("button", { name: "排队执行" })); await flush(); expect(screen.getByText("请求内容 必须是 JSON 对象。")).toBeTruthy();
  });
  it("preserves site operations and literal Nginx file paths", async () => {
    render(<ConfigView />); await flush(); const panel = await tab("网站"); fireEvent.click(panel.getByRole("button", { name: "服务器" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_servers_list", undefined);
    fireEvent.click(panel.getByRole("button", { name: "排队执行" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_setup_ssl", { domain: "example.com" });
    await tab("文件"); const nginx = within(screen.getByText("Nginx 文件").closest(".ui-card")!);
    fireEvent.change(nginx.getByLabelText("写入路径"), { target: { value: " servers/custom.conf " } }); fireEvent.click(nginx.getByRole("button", { name: "写入" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_config_file_write", { path: "servers/custom.conf", content: "server {\n    listen 80;\n}\n" });
  });
  it("requires a supported takeover preview plus confirmation and sends its source checksum", async () => {
    vi.mocked(inventory.queueAgentOperation).mockImplementation(async (id, kind, payload) => {
      const confirmed = Boolean((payload as { confirm?: boolean })?.confirm);
      const result = confirmed ? { success: true } : { preview: true, config_path: "/etc/xray/config.json", source_sha256: sha, source_files: ["/etc/xray/a.json", "/etc/xray/b.json"], running: true };
      const queued = command("/takeover", result, { id: "takeover", server_id: id }); history[id] = [queued]; return { command: queued, license_required: false };
    });
    render(<ConfigView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "接管外部 Xray" })); await flush();
    const modal = within(screen.getByText("Xray 接管", { selector: ".ui-dialog-title" }).closest('[role="dialog"]')!);
    expect(modal.getByText("2 个源文件")).toBeTruthy(); expect((modal.getByRole("button", { name: "接管" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(modal.getByRole("checkbox", { name: "替换源配置片段，并在 Xray 正在运行时重启" }));
    fireEvent.click(modal.getByRole("button", { name: "接管" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_takeover_external", { confirm: true, expected_sha256: sha });
    expect(screen.getByText("Xray 接管已完成。")).toBeTruthy();
  });
  it("discards late takeover responses after closing the preview", async () => {
    let resolve!: (value: AgentCommandCreateResponse) => void;
    vi.mocked(inventory.queueAgentOperation).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    render(<ConfigView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "接管外部 Xray" })); await flush();
    fireEvent.click(screen.getByLabelText("关闭接管窗口"));
    const calls = vi.mocked(inventory.listServerCommands).mock.calls.length;
    await act(async () => { resolve({ command: command("/takeover", null), license_required: false }); }); await flush();
    expect(inventory.listServerCommands).toHaveBeenCalledTimes(calls);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.queryByRole("dialog", { hidden: false })).toBeNull();
  });
  it("shows command failures/conflicts without silently retrying writes", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("系统");
    fireEvent.click(panel.getByRole("button", { name: "使用最新结果" })); vi.mocked(inventory.queueAgentOperation).mockRejectedValue(new Error("Xray revision conflict"));
    fireEvent.click(panel.getByRole("button", { name: "写入" })); await flush();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy(); expect(document.body.textContent).not.toContain("Xray revision conflict"); expect(inventory.queueAgentOperation).toHaveBeenCalledOnce();
  });
});
