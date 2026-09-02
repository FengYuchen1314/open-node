// @vitest-environment jsdom

import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentCommand, AgentCommandCreateResponse, AgentOperationKind, AgentOperationPayload, ServerSummary } from "../../domain/inventory";
import { listServerCommands, listServers, queueAgentOperation } from "../../services/inventory";
import {
  applyServerEgress,
  getServerEgressCatalog,
  previewServerEgress,
  previewServerEgressRemoval,
  removeServerEgress,
} from "../../services/server-egress";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import ServerEgressPanel from "./ServerEgressPanel";

// WARP lifecycle drives several real Ant Design confirmation flows and durable polls.
vi.setConfig({ testTimeout: 60_000 });

vi.mock("../../services/inventory", () => ({
  listServerCommands: vi.fn(),
  listServers: vi.fn(),
  queueAgentOperation: vi.fn(),
}));
vi.mock("../../services/server-egress", () => ({
  applyServerEgress: vi.fn(),
  getServerEgressCatalog: vi.fn(),
  previewServerEgress: vi.fn(),
  previewServerEgressRemoval: vi.fn(),
  removeServerEgress: vi.fn(),
}));

const now = "2026-09-02T00:00:00Z";
const localServer = (id: string, name: string, isFederated = false) => ({ id, name, is_federated: isFederated }) as ServerSummary;
let commands: AgentCommand[];
let sequence: number;
let warpState: Record<string, unknown>;

function pathFor(kind: AgentOperationKind) {
  if (kind === "outbounds_list" || kind === "outbounds_manage") return "/api/child/outbounds";
  if (kind === "outbound_tls_pin_probe") return "/api/child/outbound-tls-pin/probe";
  if (kind === "routing_read" || kind === "routing_manage") return "/api/child/routing";
  if (kind.startsWith("warp_")) return `/api/child/warp/${kind.slice(5)}`;
  return `/api/child/${kind}`;
}

function resultFor(serverId: string, kind: AgentOperationKind) {
  if (kind === "outbound_tls_pin_probe") return { success: true, pinned_peer_cert_sha256: "ab".repeat(32) };
  if (kind === "outbounds_list") return { outbounds: serverId === "other" ? [{ tag: "other-default", protocol: "freedom" }] : [
    { tag: "direct", protocol: "freedom", settings: { password: "table-must-not-leak" } },
    { tag: "warp-v4", protocol: "wireguard", settings: { secretKey: "also-private" } },
    { tag: "warp-v6", protocol: "wireguard" },
    { tag: "managed-egress:edge:configured", protocol: "vless", settings: { password: "managed-private" } },
  ] };
  if (kind === "routing_read") return { routing: serverId === "other" ? { rules: [] } : { domainStrategy: "AsIs", rules: [{ marktag: "china-direct", domain: ["geosite:cn"], outboundTag: "direct" }] }, observatory: { subjectSelector: ["managed-"] }, burstObservatory: null };
  if (kind === "warp_status") return { ...warpState };
  return { success: true };
}

function makeCommand(serverId: string, kind: AgentOperationKind, payload?: AgentOperationPayload, patch: Partial<AgentCommand> = {}): AgentCommand {
  return {
    id: `${serverId}-${kind}-${++sequence}`,
    server_id: serverId,
    request_id: `request-${sequence}`,
    method: kind.endsWith("_list") || kind === "routing_read" || kind === "warp_status" ? "GET" : "POST",
    path: pathFor(kind),
    query: "",
    body: payload,
    timeout_ms: 30000,
    stream: false,
    status: "succeeded",
    attempts: 1,
    result_status: 200,
    result_body: resultFor(serverId, kind),
    created_at: now,
    updated_at: now,
    ...patch,
  };
}

async function settle() { await flush(); await flush(); }

beforeEach(() => {
  vi.resetAllMocks(); installDom(); commands = []; sequence = 0;
  warpState = { installed: true, registered: true, phase: "configured", account_type: "free", addr_v4: "172.16.0.2", addr_v6: "2606:4700::2", registered_at: now };
  vi.mocked(listServers).mockResolvedValue([localServer("edge", "Edge server"), localServer("shared", "Shared server", true), localServer("other", "Other server")]);
  vi.mocked(getServerEgressCatalog).mockResolvedValue({ server_id: "edge", source_snapshot_id: "source-snapshot", source_snapshot_revision: "b".repeat(64), candidates: [
    { node_id: "node-1", node_name: "Tokyo node", server_id: "target-server", server_name: "Tokyo server", protocol: "vless", available: true, configured: false, is_default: false, has_routing_rule: false, tls_probe: { protocol: "vless", address: "egress.example", port: 443, server_name: "tls.example", alpn: ["h2"] } },
    { node_id: "node-2", node_name: "Unavailable node", server_id: "edge", server_name: "Edge server", protocol: "trojan", available: false, unavailable_reason: "Source and target must be different servers", configured: false, is_default: false, has_routing_rule: false },
    { node_id: "node-3", node_name: "Configured node", server_id: "configured-server", server_name: "Configured server", protocol: "vless", available: true, configured: true, is_default: false, has_routing_rule: true },
  ] });
  const egressPreview: Awaited<ReturnType<typeof previewServerEgress>> = { source_server_id: "edge", source_server_name: "Edge server", target_node_id: "node-1", target_node_name: "Tokyo node", target_server_id: "target-server", target_server_name: "Tokyo server", protocol: "vless", action: "create", outbound_tag: "managed-egress:edge:node", routing_marktag: "managed-egress-rule:edge:node", promote_to_default: true, will_be_default: true, routing_action: "set", routing: { domains: ["geosite:cn"], ips: [], inbound_tags: [], users: [], protocols: [] }, source_snapshot_id: "source-snapshot", target_snapshot_id: "target-snapshot", preview_revision: "a".repeat(64), pinned_peer_cert_sha256: "ab".repeat(32) };
  const removalPreview: Awaited<ReturnType<typeof previewServerEgressRemoval>> = { ...egressPreview, target_node_id: "node-3", target_node_name: "Configured node", target_server_id: "configured-server", target_server_name: "Configured server", action: "remove", outbound_tag: "managed-egress:edge:configured", routing_marktag: "managed-egress-rule:edge:configured", promote_to_default: false, will_be_default: false, routing_action: "remove", routing: null, preview_revision: "c".repeat(64) };
  vi.mocked(previewServerEgress).mockResolvedValue(egressPreview);
  vi.mocked(applyServerEgress).mockResolvedValue({ preview: egressPreview, change_set_id: "change-1", change_set_status: "dispatched", command_ids: ["command-1"], license_required: false });
  vi.mocked(previewServerEgressRemoval).mockResolvedValue(removalPreview);
  vi.mocked(removeServerEgress).mockResolvedValue({ preview: removalPreview, change_set_id: "change-2", change_set_status: "dispatched", command_ids: ["command-2"], license_required: false });
  vi.mocked(queueAgentOperation).mockImplementation(async (serverId, kind, payload) => {
    if (kind === "warp_install") warpState = { installed: true, registered: true, phase: "configured", account_type: "free" };
    if (kind === "warp_remove") warpState = { installed: false, registered: false, phase: "absent", license_active: false };
    const command = makeCommand(serverId, kind, payload); commands.push(command);
    return { command, license_required: false } as AgentCommandCreateResponse;
  });
  vi.mocked(listServerCommands).mockImplementation(async serverId => ({ server_id: serverId, commands: commands.filter(command => command.server_id === serverId), license_required: false }));
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("ServerEgressPanel", () => {
  it("reads the official outbound/routing responses for one local server without rendering credentials", async () => {
    renderUi(<ServerEgressPanel advancedContent={<div data-testid="advanced-config">高级配置内容</div>} />); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_list", undefined);
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "routing_read", undefined);
    expect(screen.getAllByText("direct").length).toBeGreaterThan(0);
    expect(screen.getByText("china-direct")).toBeTruthy();
    expect(screen.getByText("出站已配置")).toBeTruthy();
    expect(document.body.textContent).not.toContain("table-must-not-leak");
    expect(document.body.textContent).not.toContain("also-private");
    expect(screen.queryByTestId("advanced-config")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "高级配置" }));
    expect(screen.getByTestId("advanced-config")).toBeTruthy();
    expect(screen.queryByText("china-direct")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "出站与路由" })); await settle();
    expect(screen.getByText("china-direct")).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "出站与路由服务器" }));
    expect(screen.queryByText("Shared server", { selector: ".ant-select-item-option-content" })).toBeNull();
  });

  it("sets an arbitrary outbound and refreshed WARP tags as the default using a complete order", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.click(screen.getByRole("button", { name: "将 warp-v4 设为默认" })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_manage", {
      action: "reorder", tags: ["warp-v4", "direct", "warp-v6", "managed-egress:edge:configured"],
    });
  });

  it("blocks raw editing and deletion for managed and WARP exits", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    const row = screen.getByText("managed-egress:edge:configured").closest("tr")!;
    expect(within(row).getByRole("button", { name: /编辑 JSON/ })).toHaveProperty("disabled", true);
    expect(within(row).getByRole("button", { name: /删除/ })).toHaveProperty("disabled", true);
    const warpRow = screen.getAllByText("warp-v4").find(item => item.closest("tr"))!.closest("tr")!;
    expect(within(warpRow).getByRole("button", { name: /编辑 JSON/ })).toHaveProperty("disabled", true);
    expect(within(warpRow).getByRole("button", { name: /删除/ })).toHaveProperty("disabled", true);
  });

  it("previews and applies a catalog candidate by revision without handling generated credentials", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    expect(screen.getByText("Tokyo node")).toBeTruthy();
    expect(screen.getByText("不可用：源服务器与出口节点服务器不能相同")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Source and target must be different servers");
    fireEvent.click(screen.getByRole("checkbox", { name: "将该节点提升为默认出站" }));
    fireEvent.click(screen.getByRole("radio", { name: "设置或替换规则" }));
    fireEvent.change(screen.getByLabelText("出口路由域名"), { target: { value: "geosite:cn\ngeosite:cn" } });
    fireEvent.click(screen.getByRole("button", { name: "生成安全预览" })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbound_tls_pin_probe", {
      protocol: "vless", address: "egress.example", port: 443, server_name: "tls.example", alpn: ["h2"], timeout_ms: 8000, command_timeout_ms: 20000,
    });
    expect(previewServerEgress).toHaveBeenCalledWith("edge", { target_node_id: "node-1", promote_to_default: true, routing: { domains: ["geosite:cn"], ips: [], inbound_tags: [], users: [], protocols: [] }, pinned_peer_cert_sha256: "ab".repeat(32) });
    expect(screen.getByText("managed-egress:edge:node")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "应用此预览" }));
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByRole("checkbox")); fireEvent.click(dialog.getByRole("button", { name: /应\s*用/ })); await settle();
    expect(applyServerEgress).toHaveBeenCalledWith("edge", { target_node_id: "node-1", promote_to_default: true, routing: { domains: ["geosite:cn"], ips: [], inbound_tags: [], users: [], protocols: [] }, pinned_peer_cert_sha256: "ab".repeat(32), expected_preview_revision: "a".repeat(64), dispatch: true });
    expect(screen.getByText("change-1")).toBeTruthy();
    expect(screen.getByText(/系统设置 → 变更记录/)).toBeTruthy();
    expect(vi.mocked(queueAgentOperation).mock.calls.filter(call => call[1] === "outbounds_list").length).toBeGreaterThan(1);
    expect(document.body.textContent).not.toContain("password");
  });

  it("keeps an existing managed route by omitting routing and only removes it explicitly", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "受管出口节点" }));
    fireEvent.click(screen.getByText("Configured node · Configured server · vless", { selector: ".ant-select-item-option-content" }));
    expect(screen.getByRole("radio", { name: "保持现有规则" })).toHaveProperty("checked", true);

    fireEvent.click(screen.getByRole("button", { name: "生成安全预览" })); await settle();
    expect(previewServerEgress).toHaveBeenLastCalledWith("edge", {
      target_node_id: "node-3",
      promote_to_default: false,
    });

    fireEvent.click(screen.getByRole("radio", { name: "删除现有规则" }));
    fireEvent.click(screen.getByRole("button", { name: "生成安全预览" })); await settle();
    expect(previewServerEgress).toHaveBeenLastCalledWith("edge", {
      target_node_id: "node-3",
      promote_to_default: false,
      routing: null,
    });
  });

  it("disconnects a configured managed exit through the dedicated revision-bound cleanup flow", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    const row = screen.getByText("Configured node").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /断\s*开/ })); await settle();
    expect(previewServerEgressRemoval).toHaveBeenCalledWith("edge", { target_node_id: "node-3" });
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("managed-egress:edge:configured")).toBeTruthy();
    fireEvent.click(dialog.getByRole("checkbox"));
    fireEvent.click(dialog.getByRole("button", { name: /确认断开/ })); await settle();
    expect(removeServerEgress).toHaveBeenCalledWith("edge", { target_node_id: "node-3", expected_preview_revision: "c".repeat(64), dispatch: true });
    expect(screen.getByText("change-2")).toBeTruthy();
  });

  it("adds, updates and confirms removal of outbound JSON with the official action payloads", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.click(screen.getByRole("button", { name: /添加出站/ }));
    let dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("出站 JSON"), { target: { value: '{"tag":"blocked","protocol":"blackhole","settings":{}}' } });
    fireEvent.click(dialog.getByRole("button", { name: /提\s*交/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_manage", { action: "add", outbound: { tag: "blocked", protocol: "blackhole", settings: {} } });

    const directRow = screen.getAllByText("direct").find(item => item.closest("tr"))!.closest("tr")!;
    fireEvent.click(within(directRow).getByRole("button", { name: /编辑 JSON/ }));
    dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("出站 JSON"), { target: { value: '{"tag":"direct-new","protocol":"freedom"}' } });
    fireEvent.click(dialog.getByRole("button", { name: /提\s*交/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_manage", { action: "update", tag: "direct", outbound: { tag: "direct-new", protocol: "freedom" } });

    fireEvent.click(within(screen.getAllByText("direct").find(item => item.closest("tr"))!.closest("tr")!).getByRole("button", { name: /删除/ }));
    dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: /删\s*除/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_manage", { action: "remove", tag: "direct" });
  });

  it("probes and injects the fork TLS certificate pin without sending outbound credentials", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.click(screen.getByRole("button", { name: /添加出站/ }));
    const dialog = within(screen.getByRole("dialog"));
    const outbound = {
      tag: "tls-proxy",
      protocol: "vless",
      settings: { vnext: [{ address: "proxy.example", port: 443, users: [{ id: "private-user-id" }] }] },
      streamSettings: { network: "tcp", security: "tls", tlsSettings: { serverName: "sni.example", alpn: ["h2"] } },
    };
    fireEvent.change(dialog.getByLabelText("出站 JSON"), { target: { value: JSON.stringify(outbound) } });
    fireEvent.click(dialog.getByRole("button", { name: "从目标服务器自动探测" })); await settle();

    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbound_tls_pin_probe", {
      protocol: "vless",
      address: "proxy.example",
      port: 443,
      server_name: "sni.example",
      alpn: ["h2"],
      timeout_ms: 8000,
      command_timeout_ms: 20000,
    });
    const probeCall = vi.mocked(queueAgentOperation).mock.calls.find(call => call[1] === "outbound_tls_pin_probe")!;
    expect(JSON.stringify(probeCall[2])).not.toContain("private-user-id");
    expect(dialog.getByLabelText("证书 SHA-256 Pin")).toHaveProperty("value", "ab".repeat(32));

    fireEvent.click(dialog.getByRole("button", { name: /提\s*交/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "outbounds_manage", {
      action: "add",
      outbound: {
        ...outbound,
        streamSettings: {
          ...outbound.streamSettings,
          tlsSettings: { ...outbound.streamSettings.tlsSettings, pinnedPeerCertSha256: "ab".repeat(32) },
        },
      },
    });
  });

  it("extracts the direct AnyTLS target without sending its password", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.click(screen.getByRole("button", { name: /添加出站/ }));
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("出站 JSON"), { target: { value: JSON.stringify({
      tag: "anytls-proxy",
      protocol: "anytls",
      settings: { address: "anytls.example", port: 8443, password: "private-password" },
      streamSettings: { network: "tcp", security: "tls", tlsSettings: { serverName: "sni.example" } },
    }) } });
    fireEvent.click(dialog.getByRole("button", { name: "从目标服务器自动探测" })); await settle();

    const probeCall = vi.mocked(queueAgentOperation).mock.calls.find(call => call[1] === "outbound_tls_pin_probe")!;
    expect(probeCall).toEqual(["edge", "outbound_tls_pin_probe", {
      protocol: "anytls",
      address: "anytls.example",
      port: 8443,
      server_name: "sni.example",
      alpn: [],
      timeout_ms: 8000,
      command_timeout_ms: 20000,
    }]);
    expect(JSON.stringify(probeCall[2])).not.toContain("private-password");
    expect(dialog.getByLabelText("证书 SHA-256 Pin")).toHaveProperty("value", "ab".repeat(32));
  });

  it("manages routing JSON with the official action payloads", async () => {
    renderUi(<ServerEgressPanel />); await settle();
    fireEvent.click(screen.getByRole("button", { name: /添加规则/ }));
    let dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("路由规则 JSON"), { target: { value: '{"type":"field","ip":["geoip:private"],"outboundTag":"direct"}' } });
    fireEvent.click(dialog.getByRole("button", { name: /添\s*加/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "routing_manage", { action: "add_rule", rule: { type: "field", ip: ["geoip:private"], outboundTag: "direct" }, index: 1 });

    fireEvent.click(screen.getByRole("button", { name: "删除第 1 条路由规则" }));
    dialog = within(screen.getByRole("dialog")); fireEvent.click(dialog.getByRole("button", { name: /删\s*除/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "routing_manage", { action: "remove_rule", index: 0 });

    const routingEditor = screen.getByLabelText("完整路由配置 JSON") as HTMLTextAreaElement;
    expect(JSON.parse(routingEditor.value)).toEqual({ routing: { domainStrategy: "AsIs", rules: [{ marktag: "china-direct", domain: ["geosite:cn"], outboundTag: "direct" }] }, observatory: { subjectSelector: ["managed-"] }, burstObservatory: null });
    fireEvent.change(routingEditor, { target: { value: '{"routing":{"domainStrategy":"IPIfNonMatch","rules":[]},"observatory":{"subjectSelector":["managed-"]},"burstObservatory":null}' } });
    fireEvent.click(screen.getByRole("button", { name: "保存完整路由配置" })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "routing_manage", { action: "set", routing: { domainStrategy: "IPIfNonMatch", rules: [] }, observatory: { subjectSelector: ["managed-"] }, burstObservatory: null });
    expect(queueAgentOperation).not.toHaveBeenCalledWith("edge", "routing_manage", expect.objectContaining({ burst_observatory: expect.anything() }));
  });

  it("manages the WARP lifecycle only after explicit confirmation", async () => {
    warpState = { installed: false, registered: false, phase: "absent", license_active: false };
    renderUi(<ServerEgressPanel />); await settle();
    const credential = screen.getByLabelText("WARP+ 凭据") as HTMLInputElement;
    fireEvent.change(credential, { target: { value: "private-warp-plus" } });
    fireEvent.click(screen.getByRole("button", { name: "更新 WARP+" })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "warp_license", { license: "private-warp-plus" });
    expect(credential.value).toBe("");
    expect(document.body.textContent).not.toContain("private-warp-plus");

    fireEvent.click(screen.getByRole("button", { name: "安装 WARP" }));
    let dialog = within(screen.getByRole("dialog"));
    expect(queueAgentOperation).not.toHaveBeenCalledWith("edge", "warp_install", expect.anything());
    fireEvent.click(dialog.getByRole("checkbox")); fireEvent.click(dialog.getByRole("button", { name: /安\s*装/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "warp_install", { accept_terms: true });
    fireEvent.click(screen.getByRole("button", { name: "移除 WARP" }));
    dialog = within(screen.getByRole("dialog")); fireEvent.click(dialog.getByRole("checkbox")); fireEvent.click(dialog.getByRole("button", { name: /移\s*除/ })); await settle();
    expect(queueAgentOperation).toHaveBeenCalledWith("edge", "warp_remove", { confirm: true });
  });

  it("discards late results after the operator switches servers", async () => {
    const firstList = deferred<Awaited<ReturnType<typeof listServerCommands>>>();
    let held = true;
    vi.mocked(listServerCommands).mockImplementation(async serverId => {
      if (serverId === "edge" && held) { held = false; return firstList.promise; }
      return { server_id: serverId, commands: commands.filter(command => command.server_id === serverId), license_required: false };
    });
    renderUi(<ServerEgressPanel />); await flush();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "出站与路由服务器" }));
    fireEvent.click(screen.getByText("Other server", { selector: ".ant-select-item-option-content" })); await settle();
    expect(screen.getByText("other-default")).toBeTruthy();
    await act(async () => firstList.resolve({ server_id: "edge", commands: commands.filter(command => command.server_id === "edge"), license_required: false })); await settle();
    expect(screen.getByText("other-default")).toBeTruthy();
    expect(screen.queryByText("china-direct")).toBeNull();
  });

  it("reports durable command failures and bounded polling timeouts", async () => {
    vi.mocked(listServerCommands).mockImplementation(async serverId => ({ server_id: serverId, commands: commands.filter(command => command.server_id === serverId).map(command => ({ ...command, status: "failed" as const, result_error: "xray unavailable" })), license_required: false }));
    renderUi(<ServerEgressPanel />); await settle();
    expect(screen.getByText("操作未完成，请检查当前状态后重试。")).toBeTruthy();
    cleanup(); commands = []; sequence = 0; vi.clearAllMocks(); vi.useFakeTimers();
    vi.mocked(listServers).mockResolvedValue([localServer("edge", "Edge server")]);
    vi.mocked(queueAgentOperation).mockImplementation(async (serverId, kind, payload) => ({ command: makeCommand(serverId, kind, payload, { status: "waiting" }), license_required: false }));
    vi.mocked(listServerCommands).mockResolvedValue({ server_id: "edge", commands: [], license_required: false });
    renderUi(<ServerEgressPanel />); await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); }); await flush();
    expect(screen.getByText(/Agent 命令等待超时/)).toBeTruthy();
  });
});
