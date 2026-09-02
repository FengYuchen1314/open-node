// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { certificateRequest, type CertificateCapabilities, type CertificateDetail, type DNSProvider, type ManagedCertificate } from "../../services/certificates";
import { listServers } from "../../services/inventory";
import type { ServerSummary } from "../../domain/inventory";
import CertificatesView from "./CertificatesView";
import { installDom, renderUi as render } from "../test-utils";

vi.setConfig({ testTimeout: 30000 });

vi.mock("../../services/inventory", () => ({ listServers: vi.fn() }));
vi.mock("../../services/certificates", () => ({ certificateRequest: vi.fn() }));
const directory = "https://acme.example/directory";
const server: ServerSummary = { id: "edge", name: "Edge", status: "connected", connection_mode: "http", listen_port: 0, pull_port: 0, ipv6_enabled: false, traffic_limit: 0, xray_mode: "external", current_upload_speed: 0, current_download_speed: 0, created_at: "2026-08-31", updated_at: "2026-08-31" };
let caps: CertificateCapabilities, providers: DNSProvider[], row: ManagedCertificate, detail: CertificateDetail;
async function flush() { await act(async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); }); }
function modal(title: string) {
  // Select/Modal share rc-util's hard-coded test ID, unlike production React IDs.
  return within(screen.getByText(title, { selector: ".ant-modal-title" }).closest('[role="dialog"]')!);
}
async function selectOption(label: string, option: string) {
  fireEvent.mouseDown(screen.getByLabelText(label)); await flush();
  const node = screen.getAllByText(option).find((item) => item.closest(".ant-select-item-option"));
  if (!node) throw new Error(`Missing option ${option} for ${label}`);
  fireEvent.click(node); await flush();
}
async function inspect() { fireEvent.click(screen.getByRole("button", { name: "证书详情" })); await flush(); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  installDom();
  vi.mocked(listServers).mockResolvedValue([server]);
  caps = { available: true, account_management: true, revocation: true, directories: [directory], challenge_types: ["dns", "standalone", "webroot"], webroots: ["local-public"], providers: [{ id: "cloudflare", fields: ["CF_API_TOKEN", "CF_API_ENDPOINT"], required: ["CF_API_TOKEN"] }, { id: "other", fields: ["OTHER_TOKEN"], required: ["OTHER_TOKEN"] }] };
  providers = [{ id: "provider", name: "Cloud DNS", provider: "cloudflare", credential_fields: ["CF_API_TOKEN"] }];
  row = { id: "cert", name: "Edge certificate", domains: ["edge.example"], email: "admin@example.com", provider_id: "provider", directory_url: directory, challenge_type: "dns", webroot_id: null, status: "issued", auto_renew: true, active_job_id: null, version_id: "v1", expires_at: 1800000000, last_error: null };
  detail = { certificate: row, account: { email: "admin@example.com", state: "unconfirmed", uri: null, eab_configured: true, pending_email: "pending@example.com", retry_job_id: "account-job" }, versions: [{ id: "v1", created_at: 1788000000, details: { serial: "serial-v1", issuer: "Example CA", expires_at: 1800000000 }, revocation: null }, { id: "v0", created_at: 1787000000, details: { serial: "serial-v0", issuer: "Example CA", expires_at: 1799000000 }, revocation: null }], jobs: [{ id: "job", kind: "issue", status: "failed", message: "Waiting for cleanup", created_at: 1788000000, cleanup_pending: true }], targets: [{ id: "target", server_id: "edge", domain: "edge.example", cert_name: "edge.example", status: "deployed", error: null, auto_deploy: true }] };
  vi.mocked(certificateRequest).mockImplementation(async (path = "", method = "GET") => {
    const data = method !== "GET" ? {} : path === "" ? { certificates: [row] } : path === "/providers" ? { providers } : path === "/capabilities" ? caps : path === "/cert" ? detail : path.startsWith("/cert/material") ? { cert_pem: "public-cert-material", key_pem: "private-key-material" } : {};
    return structuredClone(data) as never;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("React certificate workflows", () => {
  it("generates a server self-signed certificate without ACME only after trust confirmation", async () => {
    caps.self_signed = true; caps.available = false; caps.challenge_types = []; providers = [];
    const original = vi.mocked(certificateRequest).getMockImplementation()!;
    let complete!: () => void;
    const pending = new Promise<void>((resolve) => { complete = resolve; });
    vi.mocked(certificateRequest).mockImplementation(async (path = "", method = "GET", body) => {
      if (path === "/self-signed" && method === "POST") { await pending; return {} as never; }
      return await original(path, method, body) as never;
    });
    render(<CertificatesView />); await flush();
    expect((screen.getByRole("button", { name: "新建证书" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "生成自签名证书" }));
    const dialog = modal("生成自签名证书");
    const save = dialog.getByRole("button", { name: "生成并保存" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(dialog.getByText(/不是受信 CA 签发/)).toBeTruthy();
    expect(dialog.queryByLabelText("私钥 PEM")).toBeNull();
    expect((dialog.getByRole("spinbutton", { name: "有效天数" }) as HTMLInputElement).value).toBe("365");
    fireEvent.change(dialog.getByLabelText("证书名称"), { target: { value: " Private TLS " } });
    fireEvent.change(dialog.getByLabelText("DNS 域名或 IP（SAN）"), { target: { value: "example.com, 192.0.2.20\n2001:db8::20" } });
    expect(save.disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "我了解自签名证书不受浏览器默认信任" }));
    fireEvent.click(save); fireEvent.click(save); await flush();
    expect(vi.mocked(certificateRequest).mock.calls.filter(([path, method]) => path === "/self-signed" && method === "POST")).toHaveLength(1);
    expect(certificateRequest).toHaveBeenCalledWith("/self-signed", "POST", { name: "Private TLS", domains: ["example.com", "192.0.2.20", "2001:db8::20"], valid_days: 365, purpose: "server_auth", confirm_self_signed: true });
    expect(save.disabled).toBe(true);
    complete(); await flush();
    expect(screen.getByText(/自签名证书已生成并保存。未自动部署/)).toBeTruthy();
    expect(vi.mocked(certificateRequest).mock.calls.filter(([, method]) => method === "POST")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "生成自签名证书" }));
    expect((modal("生成自签名证书").getByRole("checkbox", { name: "我了解自签名证书不受浏览器默认信任" }) as HTMLInputElement).checked).toBe(false);
  });
  it("keeps self-signed trust warnings, IP filenames and explicit deployment without CA actions", async () => {
    row.directory_url = null; row.auto_renew = false; row.domains = ["2001:db8::20"];
    detail.account = null; detail.jobs = []; detail.versions = [detail.versions[0]];
    detail.versions[0].details.self_signed = true;
    render(<CertificatesView />); await flush(); await inspect();
    expect(screen.getByText(/不支持 ACME 自动续签或 CA 吊销/)).toBeTruthy();
    expect(screen.getByText("自签名", { selector: ".ant-tag" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "吊销版本" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "立即续签" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "下载证书" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByLabelText("主机名") as HTMLInputElement).value).toBe("2001:db8::20");
    expect((screen.getByLabelText("证书文件名") as HTMLInputElement).value).toBe("2001_db8__20");
    expect(vi.mocked(certificateRequest).mock.calls.every(([, method]) => !method || method === "GET")).toBe(true);
    // Existing explicit deployment remains available; generation itself never invokes it.
    fireEvent.click(screen.getByRole("button", { name: "部署证书" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets/target/deploy", "POST");
  });
  it("fails closed without the capability and never resends a failed generation after polling or unmount", async () => {
    const view = render(<CertificatesView />); await flush();
    expect((screen.getByRole("button", { name: "生成自签名证书" }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount(); caps.self_signed = true;
    const original = vi.mocked(certificateRequest).getMockImplementation()!;
    let fail!: (reason: Error) => void;
    const pending = new Promise<never>((_, reject) => { fail = reject; });
    vi.mocked(certificateRequest).mockImplementation(async (path = "", method = "GET", body) => {
      if (path === "/self-signed" && method === "POST") return await pending;
      return await original(path, method, body) as never;
    });
    const mounted = render(<CertificatesView />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成自签名证书" }));
    fireEvent.change(screen.getByLabelText("证书名称"), { target: { value: "Local" } });
    fireEvent.change(screen.getByLabelText("DNS 域名或 IP（SAN）"), { target: { value: "localhost" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我了解自签名证书不受浏览器默认信任" }));
    fireEvent.click(screen.getByRole("button", { name: "生成并保存" })); await flush();
    fail(new Error("provider failure with private-key-material")); await flush();
    expect(document.body.textContent).not.toContain("private-key-material");
    expect(screen.queryByText(/自签名证书已生成并保存/)).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    mounted.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(vi.mocked(certificateRequest).mock.calls.filter(([path, method]) => path === "/self-signed" && method === "POST")).toHaveLength(1);
  });

  it("creates DNS certificates with explicit CA terms and optional EAB", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "新建证书" }));
    expect((screen.getByRole("button", { name: "创建证书" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("证书名称"), { target: { value: "Wildcard" } });
    fireEvent.change(screen.getByLabelText("DNS 域名"), { target: { value: "*.example.com, example.com" } });
    fireEvent.change(screen.getByLabelText("账户邮箱"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByText("外部账户绑定"));
    fireEvent.change(screen.getByLabelText("EAB 密钥 ID"), { target: { value: "key-id" } });
    fireEvent.change(screen.getByLabelText("EAB HMAC 密钥"), { target: { value: "hmac-secret" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我接受此 CA 的服务条款" }));
    fireEvent.click(screen.getByRole("button", { name: "创建证书" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("", "POST", { name: "Wildcard", domains: ["*.example.com", "example.com"], email: "owner@example.com", challenge_type: "dns", validation_server_id: null, provider_id: "provider", webroot_id: null, directory_url: directory, accept_terms: true, auto_renew: true, eab_kid: "key-id", eab_hmac_key: "hmac-secret" });
    expect(screen.queryByLabelText("EAB HMAC 密钥")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "新建证书" })); fireEvent.click(screen.getByText("外部账户绑定"));
    expect((screen.getByLabelText("EAB HMAC 密钥") as HTMLInputElement).value).toBe("");
  });
  it.each(["standalone", "webroot"] as const)("uses eligible remote %s validation with exact host/webroot and blocks wildcard names", async (challenge) => {
    providers = []; caps.available = false; caps.challenge_types = []; caps.remote_http_available = true;
    caps.validation_nodes = [{ id: "remote", name: "Remote validation", version: 1, standalone: challenge === "standalone", webroots: challenge === "webroot" ? ["remote-public"] : [], cleanup_error: null }, { id: "unsafe", name: "Cleanup failed node", version: 1, standalone: true, webroots: ["unsafe"], cleanup_error: "cleanup pending" }];
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "新建证书" }));
    fireEvent.change(screen.getByLabelText("证书名称"), { target: { value: "Remote cert" } });
    fireEvent.change(screen.getByLabelText("账户邮箱"), { target: { value: "owner@example.com" } });
    fireEvent.change(screen.getByLabelText("DNS 域名"), { target: { value: "*.example.com" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我接受此 CA 的服务条款" }));
    expect(screen.getByText("通配符域名需要使用 DNS-01")).toBeTruthy();
    expect((screen.getByRole("button", { name: "创建证书" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("DNS 域名"), { target: { value: "remote.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "创建证书" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("", "POST", expect.objectContaining({ challenge_type: challenge, validation_server_id: "remote", provider_id: null, webroot_id: challenge === "webroot" ? "remote-public" : null }));
  });
  it("rotates DNS credentials without echoing existing values", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "DNS 服务商" }));
    fireEvent.click(screen.getByRole("button", { name: "更换 DNS 凭据" }));
    expect((screen.getByLabelText("CF_API_TOKEN") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("combobox", { name: "DNS 服务商类型" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "new-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存服务商" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/providers/provider", "PUT", { name: "Cloud DNS", provider: "cloudflare", credentials: { CF_API_TOKEN: "new-secret" } });
  });
  it("clears new DNS credentials on provider changes and close", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "DNS 服务商" }));
    fireEvent.click(screen.getByRole("button", { name: "添加 DNS 服务商" }));
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "must-clear" } });
    await selectOption("DNS 服务商类型", "other");
    await selectOption("DNS 服务商类型", "cloudflare");
    expect((screen.getByLabelText("CF_API_TOKEN") as HTMLInputElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "must-clear-again" } });
    fireEvent.click(screen.getByRole("button", { name: "取 消" }));
    expect(screen.queryByLabelText("CF_API_TOKEN")).toBeNull();
  });
  it("clears imported PEM on cancel and submits private material only through the import endpoint", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "导入 PEM" }));
    fireEvent.change(screen.getByLabelText("证书名称"), { target: { value: "Imported" } });
    fireEvent.change(screen.getByLabelText("证书 PEM"), { target: { value: "public-pem" } });
    fireEvent.change(screen.getByLabelText("私钥 PEM"), { target: { value: "private-pem" } });
    fireEvent.click(screen.getByRole("button", { name: "取 消" }));
    fireEvent.click(screen.getByRole("button", { name: "导入 PEM" }));
    expect((screen.getByLabelText("私钥 PEM") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("证书 PEM"), { target: { value: "public-pem" } });
    fireEvent.change(screen.getByLabelText("私钥 PEM"), { target: { value: "private-pem" } });
    fireEvent.click(screen.getByRole("button", { name: "导入证书" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/import", "POST", { name: "Imported", cert_pem: "public-pem", key_pem: "private-pem" });
    expect(screen.queryByLabelText("私钥 PEM")).toBeNull();
  });
  it("preserves account job retries and reports pending node cleanup", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    expect(screen.getByText("节点验证文件待清理")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试账户更新" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/account/jobs/account-job/retry", "POST");
  });
  it("drops replacement secrets when EAB switches to keep", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "编辑 ACME 账户" }));
    expect((screen.getByLabelText("账户邮箱") as HTMLInputElement).value).toBe("pending@example.com");
    await selectOption("外部账户绑定", "替换凭据");
    fireEvent.change(screen.getByLabelText("EAB 密钥 ID"), { target: { value: "replace-id" } });
    fireEvent.change(screen.getByLabelText("EAB HMAC 密钥"), { target: { value: "replace-secret" } });
    await selectOption("外部账户绑定", "保留现有凭据");
    fireEvent.click(screen.getByRole("button", { name: "更新账户" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/account", "POST", { email: "pending@example.com", eab_action: "keep" });
  });
  it("binds irreversible revocation to the selected version and keeps revoked deployment disabled", async () => {
    detail.versions = [detail.versions[0]]; detail.versions[0].revocation = { status: "unknown", reason: 1, confirmed_at: null, directory_url: directory };
    render(<CertificatesView />); await flush(); await inspect();
    expect((screen.getByRole("button", { name: "部署证书" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/吊销结果尚未确认/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试吊销" }));
    expect(screen.getAllByText("serial-v1").length).toBeGreaterThan(0);
    const dialog = modal("吊销证书版本");
    expect((dialog.getByRole("button", { name: "吊销版本" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "我确认吊销此版本" }));
    fireEvent.click(dialog.getByRole("button", { name: "吊销版本" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/versions/v1/revoke", "POST", { confirm: true, reason: 1, directory_url: directory });
  });
  it("shows the exact already-revoked receipt with success in the same job row", async () => {
    row.status = "revoked";
    detail.versions[0].revocation = { status: "revoked", reason: 1, confirmed_at: 1788000000, directory_url: directory };
    detail.jobs = [{ id: "repeat-revoke", kind: "revoke", status: "succeeded", message: "CA reports this certificate is already revoked", created_at: 1788000000 }];
    render(<CertificatesView />); await flush(); await inspect();
    const job = within(screen.getByText("CA 已确认此证书已被吊销。").closest("tr")!);
    expect(job.getByText("已成功")).toBeTruthy();
    expect(job.queryByText("操作未完成，请检查当前状态后重试。")).toBeNull();
    expect(job.queryByText("CA reports this certificate is already revoked")).toBeNull();
    expect((screen.getByRole("button", { name: "部署证书" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it.each([
    ["renew", "skipped", "The certificate is not due for renewal", "证书尚未到续期时间。", "已跳过"],
    ["revoke", "queued", "Resuming reconciliation with the CA", "正在恢复与 CA 的结果核对。", "排队中"],
    ["revoke", "queued", "Operation paused; reconciliation resumes after restart", "操作已暂停，重启后将继续核对结果。", "排队中"],
  ])("keeps the %s/%s worker outcome distinct from failure or success", async (kind, status, message, translated, statusText) => {
    row.status = status === "queued" ? "queued" : "issued";
    row.active_job_id = status === "queued" ? "status-job" : null;
    detail.jobs = [{ id: "status-job", kind, status, message, created_at: 1788000000 }];
    render(<CertificatesView />); await flush(); await inspect();
    const job = within(screen.getByText(translated).closest("tr")!);
    expect(job.getByText(statusText)).toBeTruthy();
    expect(job.queryByText("操作未完成，请检查当前状态后重试。")).toBeNull();
    expect(job.queryByText("已成功")).toBeNull();
    expect(job.queryByText("失败")).toBeNull();
  });
  it.each([
    ["Provider failure: already revoked https://provider.example/?token=secret", "操作未完成，请检查当前状态后重试。"],
    ["CA reports this certificate is already revoked: https://provider.example/?token=secret", "操作未完成，请检查当前状态后重试。"],
    ["CA result is not confirmed; retry to reconcile (serverInternal)", "CA 结果尚未确认，请重试以核实结果（serverInternal）。"],
  ])("keeps unknown revocation and failure safeguards for: %s", async (message, translated) => {
    detail.versions[0].revocation = { status: "unknown", reason: 1, confirmed_at: null, directory_url: directory };
    detail.jobs = [{ id: "unknown-revoke", kind: "revoke", status: "failed", message, created_at: 1788000000, cleanup_pending: true }];
    render(<CertificatesView />); await flush(); await inspect();
    const job = within(screen.getByText(translated).closest("tr")!);
    expect(job.getByText("失败")).toBeTruthy();
    expect(job.getByText("节点验证文件待清理")).toBeTruthy();
    expect(job.queryByText("CA 已确认此证书已被吊销。")).toBeNull();
    expect(screen.getByText(/吊销结果尚未确认/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "部署证书" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).not.toMatch(/already revoked|provider\.example|token=secret/u);
  });
  it("retains deployment target controls and guards target removal with confirmation", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    const targets = within(screen.getByText("部署目标", { selector: ".ant-card-head-title" }).closest(".ant-card")!);
    const targetForm = within(targets.getByLabelText("目标服务器").closest("form")!);
    const autoDeploy = targetForm.getByRole("checkbox", { name: "自动部署" });
    const addTarget = targetForm.getByRole("button", { name: "添加目标" });
    const deploy = targets.getByRole("button", { name: "部署证书" });
    const remove = targets.getByRole("button", { name: "移除目标" });
    await selectOption("目标服务器", "Edge"); await selectOption("重载服务", "两者");
    fireEvent.click(autoDeploy);
    fireEvent.click(addTarget); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets", "POST", { server_id: "edge", domain: "edge.example", cert_name: "edge.example", reload: "both", auto_deploy: false });
    fireEvent.click(deploy); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets/target/deploy", "POST");
    fireEvent.click(remove);
    expect(certificateRequest).not.toHaveBeenCalledWith("/cert/targets/target", "DELETE");
    fireEvent.click(modal("移除部署目标？").getByRole("button", { name: "确认" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets/target", "DELETE");
  });
  it("requires explicit private-key download confirmation and revokes its blob URL", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:certificate-test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<CertificatesView />); await flush(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "下载私钥" }));
    expect(certificateRequest).not.toHaveBeenCalledWith("/cert/material?include_private_key=true");
    fireEvent.click(modal("下载私钥？").getByRole("button", { name: "确认" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/material?include_private_key=true");
    expect(click).toHaveBeenCalledOnce(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:certificate-test");
    expect(document.body.textContent).not.toContain("private-key-material");
  });
  it("polls at 5s only while visible and no dialog is open, then stops on unmount", async () => {
    const view = render(<CertificatesView />); await flush();
    const catalogCalls = () => vi.mocked(certificateRequest).mock.calls.filter(([path]) => path === undefined).length;
    expect(catalogCalls()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); await flush(); expect(catalogCalls()).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "导入 PEM" })); await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(catalogCalls()).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "取 消" })); vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(catalogCalls()).toBe(2);
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(catalogCalls()).toBe(2);
  });
});
