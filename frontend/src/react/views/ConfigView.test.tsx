// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand, AgentCommandCreateResponse, AgentOperationKind, AgentRead, ServerSummary, XrayConfigSnapshot, XrayRuntimeInbound } from "../../domain/inventory";
import * as inventory from "../../services/inventory";
import * as subscriptions from "../../services/subscriptions";
import ConfigView from "./ConfigView";
import { installDom, renderUi as render } from "../test-utils";

// These suites render real Ant Design tables/forms; allow bounded DOM work on the VPS.
vi.setConfig({ testTimeout: 30000 });

vi.mock("../../services/inventory", () => ({ acceptXrayConfigPendingRecovery: vi.fn(), applyXrayConfigRecovery: vi.fn(), createXrayRuntimeTunnelChain: vi.fn(), deleteXrayRuntimeTunnel: vi.fn(), deployXrayRuntimeTunnel: vi.fn(), getXrayRuntimeInventory: vi.fn(), getXrayRuntimeTunnelInventory: vi.fn(), listCommandStreamFrames: vi.fn(), listAgents: vi.fn(), listServerCommands: vi.fn(), listServers: vi.fn(), listXrayConfigSnapshots: vi.fn(), queueAgentOperation: vi.fn(), restoreXrayConfigSnapshot: vi.fn() }));
vi.mock("../../services/subscriptions", () => ({ cleanupExtraXrayRuntimeCredentials: vi.fn(), createManagedNodeFromRuntimeInbound: vi.fn(), getXrayRuntimeCredentialReconciliation: vi.fn(), getXrayRuntimeNodeReconciliation: vi.fn(), importManagedNodesFromRuntimeInbounds: vi.fn(), listXrayRuntimeNodeDrafts: vi.fn(), repairMissingXrayRuntimeCredentials: vi.fn(), syncManagedNodeFromRuntime: vi.fn() }));
vi.mock("../components/CommandInspector", () => ({ default: ({ commands, streamFramesByCommand }: { commands: AgentCommand[]; streamFramesByCommand: Record<string, unknown[]> }) => <div data-testid="command-inspector">{commands.map((command) => `${command.id}:${command.status}:${streamFramesByCommand[command.id]?.length ?? 0}`).join(",")}</div> }));
vi.mock("../components/LimiterPanel", () => ({ default: ({ serverId }: { serverId: string }) => <div data-testid="limiter-server">{serverId}</div> }));
const sha = "a".repeat(64), otherSha = "b".repeat(64);
const server: ServerSummary = { id: "edge", name: "Edge", domain: "edge.example", pull_address: "proxy.example", status: "connected", connection_mode: "http", listen_port: 0, pull_port: 0, ipv6_enabled: false, traffic_limit: 0, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0, created_at: "2026-08-31", updated_at: "2026-08-31" };
const agent: AgentRead = { id: "agent", server_id: "edge", hostname: "edge", agent_version: "0.3.0a0", connection_mode: "http", listen_port: 0, xray_mode: "external", capabilities: { rpc: true, stream: true, return_route_test: true, native_limiter: true, user_auto_speed_rules: true, subscription_access: true, node_cleanup: true, xray_config_workspace: true, agent_switch_xray_mode: true, agent_switch_listen_port: true, agent_probe_master_url: true, agent_update_master_url: true }, warp_installed: false, registered_at: "2026-08-31", last_seen_at: "2026-08-31" };
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
function card(title: string) { return within(screen.getByText(title, { selector: ".ant-card-head-title" }).closest(".ant-card")!); }
async function selectOption(label: string, option: string) {
  fireEvent.mouseDown(screen.getByLabelText(label)); await flush();
  const node = screen.getAllByText(option).find((item) => item.closest(".ant-select-item-option"));
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
  it("loads in StrictMode and keeps all seven workflow tabs", async () => {
    render(<StrictMode><ConfigView /></StrictMode>); await flush();
    for (const name of ["Xray", "System", "Runtime", "Limits", "Nginx", "Sites", "Files"]) expect(screen.getByRole("tab", { name })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Target server" }).closest(".ant-select")?.textContent).toContain("Edge");
    expect(screen.queryByTestId("limiter-server")).toBeNull(); await tab("Limits"); expect(screen.getByTestId("limiter-server").textContent).toBe("edge");
  });
  it("queues raw Xray test/write without changing payload semantics", async () => {
    render(<ConfigView />); await flush(); const panel = within(screen.getByRole("tabpanel", { name: "Xray" }));
    fireEvent.change(panel.getByLabelText("Xray config"), { target: { value: '{"inbounds":[]}' } });
    fireEvent.click(panel.getByRole("button", { name: "Test" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_test_config", { config: '{"inbounds":[]}' });
    fireEvent.click(panel.getByRole("switch", { name: "Force" })); fireEvent.click(panel.getByRole("button", { name: "Write" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_write", { config: '{"inbounds":[]}', path: null, force: true });
  });
  it("unlocks supported system fields only from a successful read and preserves DNS/policy objects", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("System");
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.click(panel.getByRole("button", { name: "Use latest" }));
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(false);
    expect((panel.getByRole("switch", { name: "Xray gRPC API" }) as HTMLButtonElement).disabled).toBe(true);
    expect((panel.getByLabelText("Xray gRPC API port") as HTMLInputElement).disabled).toBe(false);
    fireEvent.change(panel.getByLabelText("DNS JSON"), { target: { value: "[]" } }); expect((panel.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(panel.getByLabelText("DNS JSON"), { target: { value: '{"servers":["9.9.9.9"],"queryStrategy":"UseIPv4"}' } });
    fireEvent.click(panel.getByRole("button", { name: "Write" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_system_config_write", { log_level: "info", metrics_enabled: true, metrics_listen: "127.0.0.1:11111", stats_enabled: true, grpc_enabled: true, grpc_port: 46736, dns: { servers: ["9.9.9.9"], queryStrategy: "UseIPv4" }, policy: { levels: { "0": { handshake: 4 } }, system: { statsInboundUplink: true } }, expected_sha256: sha });
    expect((panel.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Read the current Xray system configuration again before another write.")).toBeTruthy();
  });
  it("does not fall back to an older successful system GET while a newer read is pending", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("System");
    fireEvent.click(panel.getByRole("button", { name: "Use latest" })); fireEvent.click(panel.getByRole("button", { name: "Read" })); await flush();
    fireEvent.click(panel.getByRole("button", { name: "Use latest" }));
    expect((panel.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("No completed Xray system config result.")).toBeTruthy();
  });
  it("honors fixed gRPC endpoints and server capability gates", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult({ grpc_port_writable: false, fixed_stats_address: "127.0.0.1:46736" }))];
    vi.mocked(inventory.listAgents).mockResolvedValue([agent]); render(<ConfigView />); await flush(); const panel = await tab("System");
    fireEvent.click(panel.getByRole("button", { name: "Use latest" }));
    expect((panel.getByLabelText("Xray gRPC API port") as HTMLInputElement).disabled).toBe(true);
    expect((panel.getByRole("switch", { name: "Metrics" }) as HTMLButtonElement).disabled).toBe(false);
    await selectOption("Target server", "Other");
    expect((panel.getByRole("button", { name: "Read" }) as HTMLButtonElement).disabled).toBe(true);
    expect((panel.getByLabelText("DNS JSON") as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.getByText(/Install and connect an upgraded Open Node Agent/)).toBeTruthy();
  });
  it.each(["config.jsonc", "CONFIG.JSONC"])("never unlocks %s, even when an Agent claims writable", async (filename) => {
    history.edge = [command("/api/child/xray/config-files", { content: "// comment\n{}", path: `/etc/xray/${filename}`, sha256: sha, writable: true })];
    render(<ConfigView />); await flush(); await tab("Files"); const file = within(screen.getByText("Xray file").closest(".ant-card")!);
    fireEvent.click(file.getByRole("button", { name: "Use latest" }));
    expect((file.getByLabelText("File") as HTMLInputElement).value).toBe(filename);
    expect((file.getByLabelText("Content") as HTMLTextAreaElement).disabled).toBe(true);
    expect((file.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("binds a writable file to its exact name/revision and requires another read after writes", async () => {
    history.edge = [command("/api/child/xray/config-files", { content: "{}", path: "/etc/xray/xray.json", sha256: sha, writable: true })];
    render(<ConfigView />); await flush(); await tab("Files"); const file = within(screen.getByText("Xray file").closest(".ant-card")!);
    fireEvent.click(file.getByRole("button", { name: "Use latest" }));
    fireEvent.change(file.getByLabelText("Content"), { target: { value: '{"log":{}}' } }); fireEvent.click(file.getByRole("button", { name: "Write" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_file_write", { file: "xray.json", content: '{"log":{}}', expected_sha256: sha });
    expect((file.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(file.getByRole("button", { name: "Use latest" }));
    fireEvent.change(file.getByLabelText("File"), { target: { value: "different.json" } });
    expect((file.getByLabelText("Content") as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.change(file.getByLabelText("File"), { target: { value: "xray.json" } });
    expect((file.getByLabelText("Content") as HTMLTextAreaElement).disabled).toBe(true);
  });
  it("uses file lists only to select the primary file, not to unlock editing", async () => {
    history.edge = [command("/api/child/xray/config-files", { files: { main: [{ name: "primary.json" }] } })];
    render(<ConfigView />); await flush(); await tab("Files"); const file = within(screen.getByText("Xray file").closest(".ant-card")!);
    fireEvent.click(file.getByRole("button", { name: "Use latest" }));
    expect((file.getByLabelText("File") as HTMLInputElement).value).toBe("primary.json"); expect((file.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(file.getByRole("button", { name: "Read" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_config_file_read", { file: "primary.json" });
  });
  it("loads snapshots, blocks direct pending restore and applies current with the recovery contract", async () => {
    render(<ConfigView />); await flush();
    const pending = screen.getByText("Pending").closest("tr")!; expect((within(pending).getByRole("button", { name: "Restore" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(pending).getByRole("button", { name: "Load" })); await flush();
    expect(inventory.listXrayConfigSnapshots).toHaveBeenCalledWith("edge", { limit: 8, withConfig: true });
    expect((screen.getByLabelText("Xray config") as HTMLTextAreaElement).value).toBe('{"snapshot":true}');
    fireEvent.click(screen.getByRole("button", { name: "Apply current" })); await flush();
    expect(inventory.applyXrayConfigRecovery).toHaveBeenCalledWith("edge", { restart_xray: true, merge_agent_only: true, command_timeout_ms: 60000 });
    expect(screen.getByText(/Merged 1 agent-only entries/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Accept" })); await flush();
    expect(inventory.acceptXrayConfigPendingRecovery).toHaveBeenCalledWith("edge"); expect(screen.queryByText("Pending")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore" })); await flush(); expect(inventory.restoreXrayConfigSnapshot).toHaveBeenCalledWith("edge", "current");
  });
  it("preserves native tunnel deployment fields and rejects colliding listener ports", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Deploy tunnel");
    expect((panel.getByLabelText("Domain") as HTMLInputElement).value).toBe("edge.example");
    fireEvent.click(panel.getByText("Listeners"));
    fireEvent.change(panel.getByLabelText("Nginx port"), { target: { value: "443" } });
    expect((panel.getByRole("button", { name: "Deploy tunnel" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(panel.getByLabelText("Nginx port"), { target: { value: "8001" } });
    fireEvent.click(panel.getByRole("button", { name: "Deploy tunnel" })); await flush();
    expect(inventory.deployXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { domain: "edge.example", proxy_domain: "proxy.example", site_type: "static", site_value: null, listen_address: "0.0.0.0", listen_port: 443, nginx_port: 8001, forward_port: 46174, api_port: 46736, metrics_port: 38889, cert_name: "edge.example", clear_stream_port: true, restart_xray: true, force: false, queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("never clamps an invalid listener port into a deployable port on blur or Enter", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Deploy tunnel");
    fireEvent.click(panel.getByText("Listeners")); const input = panel.getByLabelText("Public port");
    fireEvent.change(input, { target: { value: "-1" } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.deployXrayRuntimeTunnel).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("-1");
    expect((panel.getByRole("button", { name: "Deploy tunnel" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("never turns a negative or blank chain entry port into automatic port zero", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Tunnel chain"); const input = panel.getByLabelText("Entry port");
    for (const value of ["-1", ""]) {
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input);
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
      expect(inventory.createXrayRuntimeTunnelChain).not.toHaveBeenCalled();
      expect((input as HTMLInputElement).value).toBe(value);
      expect((panel.getByRole("button", { name: "Create chain" }) as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.change(input, { target: { value: "0" } }); fireEvent.blur(input);
    fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).toHaveBeenCalledWith(expect.objectContaining({ entry_port: 0 }));
  });
  it("does not restore an old chain port while an incomplete numeric draft is being edited", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Tunnel chain"); const input = panel.getByLabelText("Entry port");
    fireEvent.focus(input); fireEvent.keyDown(input, { key: "-" }); fireEvent.change(input, { target: { value: "-" } }); fireEvent.keyUp(input, { key: "-" });
    expect((input as HTMLInputElement).value).toBe("-");
    expect((panel.getByRole("button", { name: "Create chain" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.blur(input); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).not.toHaveBeenCalled(); expect(["-", ""]).toContain((input as HTMLInputElement).value);
    expect((panel.getByRole("button", { name: "Create chain" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("never clamps an out-of-range gRPC API port before a revision-bound write", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("System");
    fireEvent.click(panel.getByRole("button", { name: "Use latest" })); const input = panel.getByLabelText("Xray gRPC API port");
    fireEvent.change(input, { target: { value: "65536" } }); fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" }); fireEvent.submit(input.closest("form")!); await flush();
    expect(inventory.queueAgentOperation).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("65536");
    expect((panel.getByRole("button", { name: "Write" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("retains ordered chain hops and scan-after-apply", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Tunnel chain");
    fireEvent.click(panel.getByRole("button", { name: "Move hop 1 down" }));
    fireEvent.click(panel.getByRole("button", { name: "Create chain" })); await flush();
    expect(inventory.createXrayRuntimeTunnelChain).toHaveBeenCalledWith({ label: "relay", server_ids: ["other", "edge"], entry_port: 19000, target_address: "127.0.0.1", target_port: 443, queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("keeps routed and chain deletion selectors distinct and scans after apply", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Runtime tunnels");
    expect(screen.getByText(/\[2001:db8::1\]:443/)).toBeTruthy();
    fireEvent.click(panel.getByRole("button", { name: "Delete" })); await flush();
    expect(inventory.deleteXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { kind: "routed", tag: "outbound-tunnel", rule_index: 2, queue_agent_commands: true, queue_scan_after_apply: true });
    fireEvent.click(panel.getByRole("button", { name: "Delete chain" })); await flush();
    expect(inventory.deleteXrayRuntimeTunnel).toHaveBeenCalledWith("edge", { kind: "chain", label: "relay", queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("retains bulk import and individual runtime-node creation", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime");
    fireEvent.click(card("Runtime inventory").getByRole("button", { name: "Import missing" })); await flush(); expect(subscriptions.importManagedNodesFromRuntimeInbounds).toHaveBeenCalledWith("edge");
    fireEvent.click(card("Runtime inbounds").getByRole("button", { name: "Create node" })); await flush(); expect(subscriptions.createManagedNodeFromRuntimeInbound).toHaveBeenCalledWith("edge", { source_index: 0 });
  });
  it("allows stale-node sync while keeping missing-runtime nodes locked", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Managed node reconciliation");
    const sync = panel.getAllByRole("button", { name: "Sync" }); expect((sync[1] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(sync[0]); await flush(); expect(subscriptions.syncManagedNodeFromRuntime).toHaveBeenCalledWith("edge", "node", { source_index: 0 });
  });
  it("retains both missing-client repair and extra-client cleanup workflows", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Runtime inventory");
    expect(card("Credential reconciliation").getByText("Missing: missing@example.com")).toBeTruthy(); expect(card("Credential reconciliation").getByText("Extra: extra@example.com")).toBeTruthy();
    fireEvent.click(panel.getByRole("button", { name: "Repair clients" })); await flush(); expect(subscriptions.repairMissingXrayRuntimeCredentials).toHaveBeenCalledWith("edge", { queue_agent_commands: true, queue_scan_after_apply: true });
    fireEvent.click(panel.getByRole("button", { name: "Cleanup extras" })); await flush(); expect(subscriptions.cleanupExtraXrayRuntimeCredentials).toHaveBeenCalledWith("edge", { queue_agent_commands: true, queue_scan_after_apply: true });
  });
  it("preserves advanced runtime operations and validates JSON payload shape", async () => {
    render(<ConfigView />); await flush(); await tab("Runtime"); const panel = card("Runtime operations");
    fireEvent.click(panel.getByRole("button", { name: "Routing" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "routing_read", undefined);
    fireEvent.change(panel.getByLabelText("Payload"), { target: { value: "[]" } }); fireEvent.click(panel.getByRole("button", { name: "Queue" })); await flush(); expect(screen.getByText("Payload must be a JSON object.")).toBeTruthy();
  });
  it("preserves site operations and literal Nginx file paths", async () => {
    render(<ConfigView />); await flush(); const panel = await tab("Sites"); fireEvent.click(panel.getByRole("button", { name: "Servers" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_servers_list", undefined);
    fireEvent.click(panel.getByRole("button", { name: "Queue" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_setup_ssl", { domain: "example.com" });
    await tab("Files"); const nginx = within(screen.getByText("Nginx file").closest(".ant-card")!);
    fireEvent.change(nginx.getByLabelText("Write path"), { target: { value: " servers/custom.conf " } }); fireEvent.click(nginx.getByRole("button", { name: "Write" })); await flush(); expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "nginx_config_file_write", { path: "servers/custom.conf", content: "server {\n    listen 80;\n}\n" });
  });
  it("requires a supported takeover preview plus confirmation and sends its source checksum", async () => {
    vi.mocked(inventory.queueAgentOperation).mockImplementation(async (id, kind, payload) => {
      const confirmed = Boolean((payload as { confirm?: boolean })?.confirm);
      const result = confirmed ? { success: true } : { preview: true, config_path: "/etc/xray/config.json", source_sha256: sha, source_files: ["/etc/xray/a.json", "/etc/xray/b.json"], running: true };
      const queued = command("/takeover", result, { id: "takeover", server_id: id }); history[id] = [queued]; return { command: queued, license_required: false };
    });
    render(<ConfigView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Takeover external" })); await flush();
    const modal = within(screen.getByText("Xray takeover", { selector: ".ant-modal-title" }).closest('[role="dialog"]')!);
    expect(modal.getByText("2 source files")).toBeTruthy(); expect((modal.getByRole("button", { name: "Take over" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(modal.getByRole("checkbox", { name: "Replace source fragments and restart Xray if running" }));
    fireEvent.click(modal.getByRole("button", { name: "Take over" })); await flush();
    expect(inventory.queueAgentOperation).toHaveBeenCalledWith("edge", "xray_takeover_external", { confirm: true, expected_sha256: sha });
    expect(screen.getByText("Xray takeover completed.")).toBeTruthy();
  });
  it("discards late takeover responses after closing the preview", async () => {
    let resolve!: (value: AgentCommandCreateResponse) => void;
    vi.mocked(inventory.queueAgentOperation).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    render(<ConfigView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Takeover external" })); await flush();
    fireEvent.click(screen.getByLabelText("Close takeover"));
    const calls = vi.mocked(inventory.listServerCommands).mock.calls.length;
    await act(async () => { resolve({ command: command("/takeover", null), license_required: false }); }); await flush();
    expect(inventory.listServerCommands).toHaveBeenCalledTimes(calls);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.queryByRole("dialog", { hidden: false })).toBeNull();
  });
  it("shows command failures/conflicts without silently retrying writes", async () => {
    history.edge = [command("/api/child/xray/system-config", systemResult())]; render(<ConfigView />); await flush(); const panel = await tab("System");
    fireEvent.click(panel.getByRole("button", { name: "Use latest" })); vi.mocked(inventory.queueAgentOperation).mockRejectedValue(new Error("Xray revision conflict"));
    fireEvent.click(panel.getByRole("button", { name: "Write" })); await flush();
    expect(screen.getByText("Xray revision conflict")).toBeTruthy(); expect(inventory.queueAgentOperation).toHaveBeenCalledOnce();
  });
});
