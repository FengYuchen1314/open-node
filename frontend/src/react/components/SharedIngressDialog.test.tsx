// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentCommand } from "../../domain/inventory";
import type { SharedIngressConfiguration, SharedIngressState } from "../../domain/shared-ingress";
import {
  applySharedIngress,
  disableSharedIngress,
  getSharedIngress,
} from "../../services/shared-ingress";
import { flush, installDom, renderUi } from "../test-utils";
import SharedIngressDialog from "./SharedIngressDialog";

vi.mock("../../services/shared-ingress", async original => ({
  ...await original<typeof import("../../services/shared-ingress")>(),
  applySharedIngress: vi.fn(), disableSharedIngress: vi.fn(), getSharedIngress: vi.fn(),
}));
vi.setConfig({ testTimeout: 30000 });

const serverId = "11111111-1111-4111-8111-111111111111";
const nodeId = "22222222-2222-4222-8222-222222222222";
const now = "2026-09-02T00:00:00Z";
const route = { node_id: nodeId, profile: "vless-reality-vision" as const, sni: "node.example.com", upstream_address: "127.0.0.1" as const, upstream_port: 62041 };
const configured: SharedIngressConfiguration = {
  listen_port: 443, listen_ipv6: true, routes: [route],
  website: { sni: "site.example.com", upstream_url: "https://origin.example/app", tls_address: "127.0.0.1", tls_port: 62044, certificate_name: "site.example.com", redirect_http: true },
};
const saved: SharedIngressState = { server_id: serverId, configuration: configured, revision: 3, created_at: now, updated_at: now, license_required: false };
function command(method: "PUT" | "DELETE", body: unknown): AgentCommand {
  return { id: "33333333-3333-4333-8333-333333333333", server_id: serverId, request_id: "request", method, path: "/api/child/nginx/shared-ingress", query: "", body, timeout_ms: 60000, stream: false, status: "pending", depends_on_command_id: null, attempts: 0, result_status: null, result_body: null, result_error: null, created_at: now, leased_at: null, completed_at: null, updated_at: now };
}

async function mount(state = saved) {
  vi.mocked(getSharedIngress).mockResolvedValue(state);
  const props = { open: true, serverId, onOpenChange: vi.fn() };
  const view = renderUi(<SharedIngressDialog {...props} />); await flush(); return { ...view, props };
}

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(applySharedIngress).mockImplementation(async (_server, payload) => ({
    state: { ...saved, configuration: payload.configuration, revision: saved.revision + 1 },
    command: command("PUT", payload.configuration), license_required: false,
  }));
  vi.mocked(disableSharedIngress).mockResolvedValue({
    state: { ...saved, configuration: null, revision: saved.revision + 1 },
    command: command("DELETE", null), license_required: false,
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("shared TCP 443 ingress dialog", () => {
  it("shows immutable automatic routes, enforces unique SNI and saves with CAS", async () => {
    await mount();
    expect(screen.getByText("受管分流会独占公网 TCP 443")).toBeTruthy();
    expect(screen.getByText("VLESS Reality Vision", { selector: "td" })).toBeTruthy();
    expect(screen.getByText(/node\.example\.com → 127\.0\.0\.1:62041/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("网站 SNI"), { target: { value: "node.example.com" } }); await flush();
    expect(screen.getByText("网站 SNI 与节点路由重复，请更换域名。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "保存并下发" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("网站 SNI"), { target: { value: "new.example.com" } });
    fireEvent.change(screen.getByLabelText("绝对 HTTP(S) 上游"), { target: { value: "http://127.0.0.1:8080/app" } });
    fireEvent.click(screen.getByRole("switch", { name: "自动 HTTP 转 HTTPS 308" })); await flush();
    fireEvent.click(screen.getByRole("button", { name: "保存并下发" })); await flush();
    expect(applySharedIngress).toHaveBeenCalledExactlyOnceWith(serverId, {
      expected_revision: 3, command_timeout_ms: 60000,
      configuration: {
        ...configured, routes: [route],
        website: { ...configured.website!, sni: "new.example.com", upstream_url: "http://127.0.0.1:8080/app", redirect_http: false },
      },
    });
    expect(screen.getByText("Agent 命令状态")).toBeTruthy();
    expect(screen.getByText("等待 Agent")).toBeTruthy();
  });

  it("defaults redirect to 308 for a website-only declaration and requires explicit disable confirmation", async () => {
    const empty: SharedIngressState = { server_id: serverId, configuration: null, revision: 0, created_at: null, updated_at: null, license_required: false };
    await mount(empty);
    fireEvent.click(screen.getByRole("switch", { name: "启用网站反向代理" })); await flush();
    expect((screen.getByRole("switch", { name: "自动 HTTP 转 HTTPS 308" }) as HTMLButtonElement).getAttribute("aria-checked")).toBe("true");
    fireEvent.change(screen.getByLabelText("网站 SNI"), { target: { value: "site.example.com" } });
    fireEvent.change(screen.getByLabelText("证书名称"), { target: { value: "site.example.com" } });
    fireEvent.change(screen.getByLabelText("绝对 HTTP(S) 上游"), { target: { value: "https://origin.example/app" } }); await flush();
    fireEvent.click(screen.getByRole("button", { name: "保存并下发" })); await flush();
    expect(applySharedIngress).toHaveBeenCalledWith(serverId, {
      expected_revision: 0, command_timeout_ms: 60000,
      configuration: { listen_port: 443, listen_ipv6: true, routes: [], website: {
        sni: "site.example.com", upstream_url: "https://origin.example/app", certificate_name: "site.example.com",
        redirect_http: true, tls_address: "127.0.0.1", tls_port: 62044,
      } },
    });

    fireEvent.click(screen.getByRole("button", { name: "确认禁用" })); await flush();
    const confirmation = within(screen.getByText("确认禁用受管 443 分流", { selector: ".ant-modal-title" }).closest('[role="dialog"]')!);
    expect((confirmation.getByRole("button", { name: "禁用并下发" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirmation.getByRole("checkbox", { name: "我确认禁用此服务器的全部受管 443 入口" }));
    fireEvent.click(confirmation.getByRole("button", { name: "禁用并下发" })); await flush();
    expect(disableSharedIngress).toHaveBeenCalledExactlyOnceWith(serverId, { expected_revision: 4, command_timeout_ms: 60000 });
    expect(screen.getByText("禁用声明已保存，Agent 正在释放受管 TCP 443。")).toBeTruthy();
  });
});
