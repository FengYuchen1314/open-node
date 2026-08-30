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
async function inspect() { fireEvent.click(screen.getByRole("button", { name: "Certificate details" })); await flush(); }
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
  it("creates DNS certificates with explicit CA terms and optional EAB", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "New certificate" }));
    expect((screen.getByRole("button", { name: "Create certificate" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Certificate name"), { target: { value: "Wildcard" } });
    fireEvent.change(screen.getByLabelText("DNS names"), { target: { value: "*.example.com, example.com" } });
    fireEvent.change(screen.getByLabelText("Account email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByText("External account binding"));
    fireEvent.change(screen.getByLabelText("EAB key ID"), { target: { value: "key-id" } });
    fireEvent.change(screen.getByLabelText("EAB HMAC key"), { target: { value: "hmac-secret" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "I accept this CA's terms of service" }));
    fireEvent.click(screen.getByRole("button", { name: "Create certificate" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("", "POST", { name: "Wildcard", domains: ["*.example.com", "example.com"], email: "owner@example.com", challenge_type: "dns", validation_server_id: null, provider_id: "provider", webroot_id: null, directory_url: directory, accept_terms: true, auto_renew: true, eab_kid: "key-id", eab_hmac_key: "hmac-secret" });
    expect(screen.queryByLabelText("EAB HMAC key")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New certificate" })); fireEvent.click(screen.getByText("External account binding"));
    expect((screen.getByLabelText("EAB HMAC key") as HTMLInputElement).value).toBe("");
  });
  it.each(["standalone", "webroot"] as const)("uses eligible remote %s validation with exact host/webroot and blocks wildcard names", async (challenge) => {
    providers = []; caps.available = false; caps.challenge_types = []; caps.remote_http_available = true;
    caps.validation_nodes = [{ id: "remote", name: "Remote validation", version: 1, standalone: challenge === "standalone", webroots: challenge === "webroot" ? ["remote-public"] : [], cleanup_error: null }, { id: "unsafe", name: "Cleanup failed node", version: 1, standalone: true, webroots: ["unsafe"], cleanup_error: "cleanup pending" }];
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "New certificate" }));
    fireEvent.change(screen.getByLabelText("Certificate name"), { target: { value: "Remote cert" } });
    fireEvent.change(screen.getByLabelText("Account email"), { target: { value: "owner@example.com" } });
    fireEvent.change(screen.getByLabelText("DNS names"), { target: { value: "*.example.com" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "I accept this CA's terms of service" }));
    expect(screen.getByText("Wildcard names require DNS-01")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Create certificate" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("DNS names"), { target: { value: "remote.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Create certificate" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("", "POST", expect.objectContaining({ challenge_type: challenge, validation_server_id: "remote", provider_id: null, webroot_id: challenge === "webroot" ? "remote-public" : null }));
  });
  it("rotates DNS credentials without echoing existing values and clears them on provider changes/close", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("tab", { name: "DNS providers" }));
    fireEvent.click(screen.getByRole("button", { name: "Rotate DNS credentials" }));
    expect((screen.getByLabelText("CF_API_TOKEN") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("combobox", { name: "DNS provider type" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "new-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/providers/provider", "PUT", { name: "Cloud DNS", provider: "cloudflare", credentials: { CF_API_TOKEN: "new-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add DNS provider" }));
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "must-clear" } });
    await selectOption("DNS provider type", "other");
    await selectOption("DNS provider type", "cloudflare");
    expect((screen.getByLabelText("CF_API_TOKEN") as HTMLInputElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "must-clear-again" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("CF_API_TOKEN")).toBeNull();
  });
  it("clears imported PEM on cancel and submits private material only through the import endpoint", async () => {
    render(<CertificatesView />); await flush(); fireEvent.click(screen.getByRole("button", { name: "Import PEM" }));
    fireEvent.change(screen.getByLabelText("Certificate name"), { target: { value: "Imported" } });
    fireEvent.change(screen.getByLabelText("Certificate PEM"), { target: { value: "public-pem" } });
    fireEvent.change(screen.getByLabelText("Private key PEM"), { target: { value: "private-pem" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Import PEM" }));
    expect((screen.getByLabelText("Private key PEM") as HTMLTextAreaElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("Certificate PEM"), { target: { value: "public-pem" } });
    fireEvent.change(screen.getByLabelText("Private key PEM"), { target: { value: "private-pem" } });
    fireEvent.click(screen.getByRole("button", { name: "Import certificate" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/import", "POST", { name: "Imported", cert_pem: "public-pem", key_pem: "private-pem" });
    expect(screen.queryByLabelText("Private key PEM")).toBeNull();
  });
  it("preserves account job retries and reports pending node cleanup", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    expect(screen.getByText("Node challenge cleanup pending")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry account update" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/account/jobs/account-job/retry", "POST");
  });
  it("drops replacement secrets when EAB switches to keep", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Edit ACME account" }));
    expect((screen.getByLabelText("Account email") as HTMLInputElement).value).toBe("pending@example.com");
    await selectOption("External account binding", "Replace credentials");
    fireEvent.change(screen.getByLabelText("EAB key ID"), { target: { value: "replace-id" } });
    fireEvent.change(screen.getByLabelText("EAB HMAC key"), { target: { value: "replace-secret" } });
    await selectOption("External account binding", "Keep existing");
    fireEvent.click(screen.getByRole("button", { name: "Update account" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/account", "POST", { email: "pending@example.com", eab_action: "keep" });
  });
  it("binds irreversible revocation to the selected version and keeps revoked deployment disabled", async () => {
    detail.versions = [detail.versions[0]]; detail.versions[0].revocation = { status: "unknown", reason: 1, confirmed_at: null, directory_url: directory };
    render(<CertificatesView />); await flush(); await inspect();
    expect((screen.getByRole("button", { name: "Deploy certificate" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Revocation is not yet confirmed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry revocation" }));
    expect(screen.getAllByText("serial-v1").length).toBeGreaterThan(0);
    const dialog = modal("Revoke certificate version");
    expect((dialog.getByRole("button", { name: "Revoke version" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "I confirm revocation of this version" }));
    fireEvent.click(dialog.getByRole("button", { name: "Revoke version" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/versions/v1/revoke", "POST", { confirm: true, reason: 1, directory_url: directory });
  });
  it("retains deployment target controls and guards target removal with confirmation", async () => {
    render(<CertificatesView />); await flush(); await inspect();
    const targets = within(screen.getByText("Deployment targets", { selector: ".ant-card-head-title" }).closest(".ant-card")!);
    const targetForm = within(targets.getByLabelText("Target server").closest("form")!);
    const autoDeploy = targetForm.getByRole("checkbox", { name: "Auto-deploy" });
    const addTarget = targetForm.getByRole("button", { name: "Add target" });
    const deploy = targets.getByRole("button", { name: "Deploy certificate" });
    const remove = targets.getByRole("button", { name: "Remove target" });
    await selectOption("Target server", "Edge"); await selectOption("Reload", "both");
    fireEvent.click(autoDeploy);
    fireEvent.click(addTarget); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets", "POST", { server_id: "edge", domain: "edge.example", cert_name: "edge.example", reload: "both", auto_deploy: false });
    fireEvent.click(deploy); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets/target/deploy", "POST");
    fireEvent.click(remove);
    expect(certificateRequest).not.toHaveBeenCalledWith("/cert/targets/target", "DELETE");
    fireEvent.click(modal("Remove deployment target?").getByRole("button", { name: "Confirm" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/targets/target", "DELETE");
  });
  it("requires explicit private-key download confirmation and revokes its blob URL", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:certificate-test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<CertificatesView />); await flush(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Download private key" }));
    expect(certificateRequest).not.toHaveBeenCalledWith("/cert/material?include_private_key=true");
    fireEvent.click(modal("Download the private key?").getByRole("button", { name: "Confirm" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/cert/material?include_private_key=true");
    expect(click).toHaveBeenCalledOnce(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:certificate-test");
    expect(document.body.textContent).not.toContain("private-key-material");
  });
  it("polls at 5s only while visible and no dialog is open, then stops on unmount", async () => {
    const view = render(<CertificatesView />); await flush();
    const catalogCalls = () => vi.mocked(certificateRequest).mock.calls.filter(([path]) => path === undefined).length;
    expect(catalogCalls()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); await flush(); expect(catalogCalls()).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Import PEM" })); await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(catalogCalls()).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" })); vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(catalogCalls()).toBe(2);
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(10000); }); expect(catalogCalls()).toBe(2);
  });
});
