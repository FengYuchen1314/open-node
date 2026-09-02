import { describe, expect, it, vi } from "vitest";

import type { AgentCommand } from "../domain/inventory";
import type { SharedIngressConfiguration, SharedIngressState } from "../domain/shared-ingress";
import { applySharedIngress, disableSharedIngress, getSharedIngress } from "./shared-ingress";

const serverId = "11111111-1111-4111-8111-111111111111";
const nodeId = "22222222-2222-4222-8222-222222222222";
const configuration: SharedIngressConfiguration = {
  listen_port: 443, listen_ipv6: true,
  routes: [{ node_id: nodeId, profile: "vless-reality-vision", sni: "node.example.com", upstream_address: "127.0.0.1", upstream_port: 62041 }],
  website: { sni: "site.example.com", upstream_url: "https://origin.example/app", tls_address: "127.0.0.1", tls_port: 62044, certificate_name: "site.example.com", redirect_http: true },
};
const now = "2026-09-02T00:00:00Z";
const state: SharedIngressState = { server_id: serverId, configuration, revision: 1, created_at: now, updated_at: now, license_required: false };
function command(method: "PUT" | "DELETE", body: unknown): AgentCommand {
  return { id: "33333333-3333-4333-8333-333333333333", server_id: serverId, request_id: "request-1", method, path: "/api/child/nginx/shared-ingress", query: "", body, timeout_ms: 60000, stream: false, status: "pending", depends_on_command_id: null, attempts: 0, result_status: null, result_body: null, result_error: null, created_at: now, leased_at: null, completed_at: null, updated_at: now };
}
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

describe("shared ingress service", () => {
  it("uses exact GET, CAS PUT and guarded DELETE contracts", async () => {
    const disabled = { ...state, configuration: null, revision: 2 };
    const queue = [state, { state, command: command("PUT", configuration), license_required: false }, { state: disabled, command: command("DELETE", null), license_required: false }];
    const fetcher = vi.fn((_url: RequestInfo | URL, _init?: RequestInit) => response(queue.shift()));
    expect((await getSharedIngress(serverId, fetcher)).configuration).toEqual(configuration);
    await applySharedIngress(serverId, { configuration, expected_revision: 1, command_timeout_ms: 60000 }, fetcher);
    await disableSharedIngress(serverId, { expected_revision: 1, command_timeout_ms: 60000 }, fetcher);
    expect(fetcher.mock.calls.map(call => [String(call[0]), call[1]?.method ?? "GET"])).toEqual([
      [`/api/v1/servers/${serverId}/shared-ingress`, "GET"],
      [`/api/v1/servers/${serverId}/shared-ingress`, "PUT"],
      [`/api/v1/servers/${serverId}/shared-ingress`, "DELETE"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ configuration, expected_revision: 1, command_timeout_ms: 60000 });
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ expected_revision: 1, command_timeout_ms: 60000 });
  });

  it("fails closed on unknown profiles, cross-server receipts and mismatched Agent commands", async () => {
    await expect(getSharedIngress(serverId, () => response({ ...state, configuration: { ...configuration, routes: [{ ...configuration.routes[0], profile: "vmess" }] } })))
      .rejects.toThrow("未能确认 443 分流操作结果");
    await expect(getSharedIngress(serverId, () => response({ ...state, server_id: nodeId })))
      .rejects.toThrow("未能确认 443 分流操作结果");
    await expect(applySharedIngress(serverId, { configuration, expected_revision: 1, command_timeout_ms: 60000 },
      () => response({ state, command: command("DELETE", null), license_required: false })))
      .rejects.toThrow("未能确认 443 分流操作结果");
  });

  it("maps bounded conflicts without echoing private backend details", async () => {
    await expect(applySharedIngress(serverId, { configuration, expected_revision: 1, command_timeout_ms: 60000 },
      () => response({ detail: "shared ingress revision changed: expected 1, current 2" }, 409)))
      .rejects.toThrow("配置已发生变化");
    await expect(getSharedIngress(serverId, () => response({ detail: "PRIVATE HOST ERROR" }, 500)))
      .rejects.toThrow("未能确认 443 分流操作结果");
  });
});
