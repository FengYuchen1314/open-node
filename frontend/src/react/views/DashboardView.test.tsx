// @vitest-environment jsdom
import { act, cleanup, configure, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentCommand, AgentRead, ServerSummary } from "../../domain/inventory";
import { createServer, createServerCommand, getLatestScanResult, getLatestTelemetry, listAgents, listCommandStreamFrames,
  listServerCommands, listServers, queueAgentOperation, updateServerProbeMetadata } from "../../services/inventory";
import DashboardView from "./DashboardView";
vi.mock("../../services/inventory", () => ({ createServer: vi.fn(), createServerCommand: vi.fn(), getLatestScanResult: vi.fn(), getLatestTelemetry: vi.fn(),
  listAgents: vi.fn(), listCommandStreamFrames: vi.fn(), listServerCommands: vi.fn(), listServers: vi.fn(), queueAgentOperation: vi.fn(), updateServerProbeMetadata: vi.fn() }));
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
  it("loads server inventory and opens explicit bootstrap/edit/remove actions for the selected row", async () => {
    await mount(); expect(screen.getByText("192.0.2.1")).toBeTruthy(); expect(screen.getByTestId("traffic-panel").textContent).toBe("2");
    expect(getLatestTelemetry).toHaveBeenCalledWith("edge"); expect(getLatestScanResult).toHaveBeenCalledWith("other");
    fireEvent.click(getButton("Install Agent on Edge")); expect(screen.getByTestId("bootstrap-dialog-target").textContent).toBe("edge");
    fireEvent.click(getButton("Edit Other")); expect(screen.getByTestId("management-dialog-target").textContent).toBe("other:edit");
    fireEvent.click(getButton("Remove Edge")); expect(screen.getByTestId("management-dialog-target").textContent).toBe("edge:remove");
    expect(queueAgentOperation).not.toHaveBeenCalled();
  }, 30000);
  it("creates the complete default request and keeps its manual token private and dismissible", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Name"), { target: { value: " New edge " } });
    fireEvent.change(screen.getByLabelText("IPv4", { selector: "input[type=text]" }), { target: { value: "192.0.2.9" } });
    fireEvent.change(screen.getByLabelText("New server expires"), { target: { value: "2026-09-30" } });
    fireEvent.click(getButton("Create server")); await flush();
    expect(createServer).toHaveBeenCalledWith(expect.objectContaining({ name: "New edge", ip_address: "192.0.2.9", ip_address_v6: null,
      domain: null, domain_v6: null, expires_at: "2026-09-30T00:00:00Z", connection_mode: "auto", xray_mode: "external", listen_port: 23889, ipv6_enabled: true }));
    expect((screen.getByLabelText("Agent token") as HTMLTextAreaElement).value).toBe("private-manual-agent-token");
    fireEvent.click(getButton("Hide token")); expect(screen.queryByLabelText("Agent token")).toBeNull();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  }, 30000);
  it("preserves all probe metadata fields and UTC expiry semantics", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Country"), { target: { value: " JP " } });
    fireEvent.change(screen.getByLabelText("Provider URL"), { target: { value: "https://provider.example" } });
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("CNY price"), { target: { value: "72" } });
    await select("Cycle", "Month"); await select("Telecom peer", "Paid");
    fireEvent.click(getButton("Save metadata")); await flush();
    expect(updateServerProbeMetadata).toHaveBeenCalledWith("edge", expect.objectContaining({ region_country: "JP", provider_url: "https://provider.example",
      expires_at: "2026-10-01T00:00:00Z", renewal_price_cny: 72, renewal_cycle: "month", telecom_paid_peer: true }));
    expect(screen.getByText("Probe metadata saved.")).toBeTruthy();
  }, 30000);
  it("fails closed for capability-dependent Agent settings and Xray file operations", async () => {
    await mount();
    for (const name of ["Xray system", "Xray files", "Switch Xray mode", "Apply listen port", "Probe", "Update"]) {
      const button = getButton(name) as HTMLButtonElement; expect(button.disabled).toBe(true); fireEvent.click(button);
    }
    expect(queueAgentOperation).not.toHaveBeenCalled();
    expect((getButton("Xray config") as HTMLButtonElement).disabled).toBe(false);
  }, 30000);
  it("queues advertised settings with explicit recovery semantics", async () => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("Master URL"), { target: { value: " https://control.example " } });
    fireEvent.click(getButton("Probe")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_probe_master_url", { master_url: "https://control.example" });
    fireEvent.click(getButton("Update")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "agent_update_master_url", { master_url: "https://control.example", only_if_recovery: true });
    fireEvent.click(getButton("Xray files")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "xray_config_files_list", undefined);
  }, 30000);
  it("requires WARP terms and clears WARP+ credentials after success and target changes", async () => {
    await mount(); fireEvent.click(getButton("Install WARP"));
    const dialog = getDialog("Install free WARP");
    expect((dialog.getByRole("button", { name: "Install" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: /I accept the/ })); fireEvent.click(dialog.getByRole("button", { name: "Install" })); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "warp_install", { accept_terms: true });
    const input = screen.getByLabelText("WARP+ credential (optional)") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "private-warp-credential" } }); fireEvent.click(getButton("Update WARP+")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "warp_license", { license: "private-warp-credential" }); expect(input.value).toBe("");
    fireEvent.change(input, { target: { value: "another-private-credential" } }); await select("Target server", "Other"); expect(input.value).toBe("");
  }, 30000);
  it("retains Xray release checksum/runtime-state controls and explicit removal confirmation", async () => {
    await mount(); fireEvent.click(getButton("Install Xray"));
    fireEvent.change(screen.getByLabelText("Xray version"), { target: { value: "v26.9.1" } });
    let dialog = getDialog("Install / Upgrade Xray");
    expect((dialog.getByRole("button", { name: "Install" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Archive SHA-256"), { target: { value: "d".repeat(64) } }); await select("Runtime state", "Stopped");
    fireEvent.click(dialog.getByRole("button", { name: "Install" })); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "xray_install", { version: "v26.9.1", sha256: "d".repeat(64), start: false });
    fireEvent.click(getButton("Remove Xray")); dialog = getDialog("Remove Xray");
    expect((dialog.getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "Confirm runtime change" })); fireEvent.click(dialog.getByRole("button", { name: "Confirm" })); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "xray_remove", {});
  }, 30000);
  it("resets log deletion consent whenever the target or file scope changes", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Log file"), { target: { value: "agent.log" } });
    fireEvent.click(screen.getByLabelText("Confirm log deletion"));
    fireEvent.change(screen.getByLabelText("Log file"), { target: { value: "xray.log" } });
    expect((getButton("Purge logs") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Confirm log deletion")); await select("Target server", "Other");
    expect((screen.getByLabelText("Confirm log deletion") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByLabelText("All files")); fireEvent.click(screen.getByLabelText("Confirm log deletion"));
    fireEvent.click(getButton("Purge logs")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("other", "log_files_delete", { all: true });
    expect((screen.getByLabelText("Confirm log deletion") as HTMLInputElement).checked).toBe(false);
  }, 30000);
  it("queues latency and return-route probes with bounded computed command timeouts", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Latency targets"), { target: { value: "example.com,\nexample.net" } });
    fireEvent.click(getButton("Queue latency probe")); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "domain_latency", { domains: ["example.com", "example.net"], timeout_ms: 2000, allow_icmp: false, command_timeout_ms: 30000 });
    expect((screen.getByLabelText("Latency targets") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("Telecom host"), { target: { value: "203.0.113.2" } });
    fireEvent.click(getButton("Trace return route")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "return_route_test", expect.objectContaining({ ip_version: 4, timeout_seconds: 25,
      command_timeout_ms: 30000, targets: [{ carrier: "telecom", host: "203.0.113.2", region: "", port: 80 }] }));
  }, 30000);
  it("validates custom JSON and preserves method/query/timeout/stream fields", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("JSON body"), { target: { value: "{" } });
    fireEvent.click(getButton("Queue command")); await flush();
    expect(createServerCommand).not.toHaveBeenCalled(); expect(screen.getByText("Command body must be valid JSON.")).toBeTruthy();
    await select("Method", "POST"); fireEvent.change(screen.getByLabelText("JSON body"), { target: { value: '{"key":"value"}' } });
    fireEvent.change(screen.getByLabelText("Query"), { target: { value: "lines=10" } }); fireEvent.click(screen.getByLabelText("Stream"));
    fireEvent.click(getButton("Queue command")); await flush();
    expect(createServerCommand).toHaveBeenCalledWith("edge", { method: "POST", path: "/api/child/system/info", query: "lines=10", body: { key: "value" }, timeout_ms: 30000, stream: true });
    expect((screen.getByLabelText("JSON body") as HTMLTextAreaElement).value).toBe("");
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
    await mount(); fireEvent.click(getButton("Upgrade Agent"));
    expect(screen.getByTestId("lifecycle-dialog-target").textContent).toBe("edge:agent_upgrade"); expect(queueAgentOperation).not.toHaveBeenCalled();
  }, 30000);
  it("retains quick diagnostics, service control, logs, Nginx cleanup and release/status operations", async () => {
    await mount();
    for (const [label, kind] of [["System info", "system_info"], ["Traffic", "traffic"], ["Speed", "speed"],
      ["Services", "services_status"], ["NICs", "system_nics"], ["Scan", "scan"], ["Log files", "log_files_list"],
      ["Xray release", "xray_release"], ["Install Nginx", "nginx_install"], ["Remove Nginx", "nginx_remove"],
      ["WARP status", "warp_status"], ["Nginx config", "nginx_config_read"], ["Nginx files", "nginx_config_files_list"]]) {
      fireEvent.click(getButton(label)); await flush();
      expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", kind, undefined);
    }
    fireEvent.click(getButton("Restart Xray")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "service_control", { service: "xray", action: "restart" });
    fireEvent.click(getButton("Agent logs")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "logs", { service: "agent", lines: 200 });
    fireEvent.click(getButton("Clear stream")); await flush();
    expect(queueAgentOperation).toHaveBeenLastCalledWith("edge", "nginx_clear_stream_port", { port: 443 });
  }, 30000);
  it("never renders a late manual Agent token after the page unmounts", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof createServer>>) => void;
    vi.mocked(createServer).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { unmount } = await mount(); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New edge" } });
    const button = getButton("Create server"); fireEvent.click(button); fireEvent.click(button); await flush();
    expect(createServer).toHaveBeenCalledOnce(); unmount();
    await act(async () => { resolve({ server: edge, agent_token: "late-private-token", license_required: false }); });
    expect(screen.queryByLabelText("Agent token")).toBeNull(); expect(document.body.textContent).not.toContain("late-private-token");
  }, 30000);
  it("shows queue failures without discarding an unsubmitted custom body", async () => {
    vi.mocked(createServerCommand).mockRejectedValue(new Error("Command target is offline")); await mount();
    fireEvent.change(screen.getByLabelText("JSON body"), { target: { value: '{"keep":"draft"}' } });
    fireEvent.click(getButton("Queue command")); await flush();
    expect(screen.getByText("Command target is offline")).toBeTruthy();
    expect((screen.getByLabelText("JSON body") as HTMLTextAreaElement).value).toBe('{"keep":"draft"}');
  }, 30000);
  it("keeps the exact Queue command accessible name during loading and after a failed request", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(createServerCommand).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    await mount(); fireEvent.click(getButton("Queue command")); await flush();
    const form = within(screen.getByLabelText("JSON body").closest("form")!);
    expect(form.getByRole("button", { name: "Queue command" })).toBeTruthy();
    await act(async () => { reject(new Error("Offline")); }); await flush();
    const button = form.getByRole("button", { name: "Queue command" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false); expect(button.getAttribute("aria-label")).toBe("Queue command");
  }, 30000);
  it.each([
    { label: "Port", button: "Create server", minimum: 0, maximum: 65535 },
    { label: "Traffic limit (bytes)", button: "Create server", minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
    { label: "Stream port", button: "Clear stream", minimum: 1, maximum: 65535 },
    { label: "Listen port", button: "Apply listen port", minimum: 0, maximum: 65535 },
    { label: "Latency timeout", button: "Queue latency probe", minimum: 200, maximum: 10000 },
    { label: "Route timeout seconds", button: "Trace return route", minimum: 10, maximum: 45 },
    { label: "Command timeout", button: "Queue command", minimum: 1000, maximum: 300000 },
  ])("rejects blank, negative, fractional and over-bound $label after real Ant blur and Enter", async ({ label, button, minimum, maximum }) => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New edge" } });
    fireEvent.change(screen.getByLabelText("Latency targets"), { target: { value: "example.com" } });
    fireEvent.change(screen.getByLabelText("Telecom host"), { target: { value: "203.0.113.2" } });
    fireEvent.change(screen.getByLabelText("Master URL"), { target: { value: "https://control.example" } });
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
    { label: "Renewal", button: "Create server", key: "renewal_price" },
    { label: "Renewal price", button: "Save metadata", key: "renewal_price" },
    { label: "CNY price", button: "Save metadata", key: "renewal_price_cny" },
  ])("rejects invalid $label without silently clearing it, but permits explicit empty and decimal prices", async ({ label, button, key }) => {
    await mount(); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New edge" } });
    const input = screen.getByLabelText(label) as HTMLInputElement;
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["-1", "-", "$1", "0x10", "1e", "1e999", "1e-999", " "]) {
        editNumber(input, value, finish); fireEvent.click(getButton(button)); await flush();
        expect(createServer).not.toHaveBeenCalled(); expect(updateServerProbeMetadata).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "2.5", "Enter"); fireEvent.click(getButton(button)); await flush();
    if (button === "Create server") {
      expect(createServer).toHaveBeenLastCalledWith(expect.objectContaining({ [key]: 2.5 }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New edge" } });
    } else expect(updateServerProbeMetadata).toHaveBeenLastCalledWith("edge", expect.objectContaining({ [key]: 2.5 }));
    editNumber(input, "", "blur"); fireEvent.click(getButton(button)); await flush();
    if (button === "Create server") expect(createServer).toHaveBeenLastCalledWith(expect.objectContaining({ [key]: null }));
    else expect(updateServerProbeMetadata).toHaveBeenLastCalledWith("edge", expect.objectContaining({ [key]: null }));
  }, 30000);
  it("does not reuse an old required quota for malformed or underflowed numeric drafts", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New edge" } });
    const input = screen.getByLabelText("Traffic limit (bytes)");
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["-", "$1", "0x10", "1e", "1e999", "1e-999", " "]) {
        editNumber(input, value, finish); fireEvent.click(getButton("Create server")); await flush();
        expect(createServer).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "0", "Enter"); editNumber(screen.getByLabelText("Port"), "0", "blur");
    fireEvent.click(getButton("Create server")); await flush();
    expect(createServer).toHaveBeenCalledWith(expect.objectContaining({ traffic_limit: 0, listen_port: 0 }));
  }, 30000);
  it("rejects blank and invalid selected return-route ports instead of substituting port 80", async () => {
    await mount(); fireEvent.change(screen.getByLabelText("Telecom host"), { target: { value: "203.0.113.2" } });
    const input = screen.getByLabelText("Telecom port");
    for (const finish of ["blur", "Enter"] as const) {
      for (const value of ["", "-1", "80.5", "65536"]) {
        editNumber(input, value, finish); fireEvent.click(getButton("Trace return route")); await flush();
        expect(queueAgentOperation).not.toHaveBeenCalled();
      }
    }
    editNumber(input, "443", "Enter"); fireEvent.click(getButton("Trace return route")); await flush();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "return_route_test", expect.objectContaining({
      targets: [{ carrier: "telecom", host: "203.0.113.2", region: "", port: 443 }],
    }));
  }, 30000);
  it("Enter on listen port applies only that explicitly validated setting, never the Master URL", async () => {
    vi.mocked(listAgents).mockResolvedValue([agent]); await mount();
    fireEvent.change(screen.getByLabelText("Master URL"), { target: { value: "https://control.example" } });
    editNumber(screen.getByLabelText("Listen port"), "-1", "Enter"); await flush();
    expect(queueAgentOperation).not.toHaveBeenCalled();
    editNumber(screen.getByLabelText("Listen port"), "0", "Enter"); await flush();
    expect(queueAgentOperation).toHaveBeenCalledOnce();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "agent_switch_listen_port", { listen_port: 0 });
  }, 30000);
});
