// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { certificateRequest } from "../../services/certificates";
import DNSProvidersPanel from "./DNSProvidersPanel";

vi.mock("../../services/certificates", async importOriginal => ({
  ...await importOriginal<typeof import("../../services/certificates")>(),
  certificateRequest: vi.fn(),
}));

const provider = { id: "11111111-1111-4111-8111-111111111111", name: "主 DNS", provider: "cloudflare", credential_fields: ["CF_API_TOKEN"] };
const capabilities = { available: false, account_management: false, revocation: false, directories: [], challenge_types: [], webroots: [], providers: [{ id: "cloudflare", fields: ["CF_API_TOKEN"], required: ["CF_API_TOKEN"] }] };

async function flush() { await act(async () => { for (let index = 0; index < 10; index += 1) await Promise.resolve(); }); }

beforeEach(() => {
  vi.resetAllMocks();
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.mocked(certificateRequest).mockImplementation(async path => path === "/providers" ? { providers: [provider] } : capabilities);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("DNS provider settings without certificate management", () => {
  it("adds a provider with required credentials and refreshes the DDNS catalog", async () => {
    const updated = vi.fn();
    render(<DNSProvidersPanel onUpdated={updated} />); await flush();
    expect(screen.getByText("主 DNS")).toBeTruthy();
    expect(screen.getByText(/这里不提供证书签发或证书管理/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "添加 DNS 服务商" }));
    fireEvent.change(screen.getByLabelText("服务商名称"), { target: { value: "边缘 DNS" } });
    fireEvent.change(screen.getByLabelText("CF_API_TOKEN"), { target: { value: "secret-token" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 DNS 服务商" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith("/providers", "POST", { name: "边缘 DNS", provider: "cloudflare", credentials: { CF_API_TOKEN: "secret-token" } });
    expect(updated).toHaveBeenCalledOnce();
    expect(document.body.textContent).not.toContain("secret-token");
  });

  it("requires confirmation before deleting a provider", async () => {
    render(<DNSProvidersPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "删除 DNS 服务商 主 DNS" }));
    expect(certificateRequest).not.toHaveBeenCalledWith(`/providers/${provider.id}`, "DELETE");
    fireEvent.click(screen.getByRole("button", { name: "确认删除 DNS 服务商" })); await flush();
    expect(certificateRequest).toHaveBeenCalledWith(`/providers/${provider.id}`, "DELETE");
  });
});
