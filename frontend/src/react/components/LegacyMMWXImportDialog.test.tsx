// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Upload } from "antd";
import LegacyMMWXImportDialog from "./LegacyMMWXImportDialog";
import { importLegacyMMWXIdentities, previewLegacyMMWXIdentities } from "../../services/legacy-mmwx";
import type { LegacyMMWXIdentityBundle, LegacyMMWXImportPreview } from "../../domain/legacy-mmwx";
import type { SubscriptionPlan } from "../../domain/subscriptions";

vi.mock("../../services/legacy-mmwx", () => ({ importLegacyMMWXIdentities: vi.fn(), previewLegacyMMWXIdentities: vi.fn() }));
const bundle: LegacyMMWXIdentityBundle = { version: 1, users: [], packages: [{ source_id: 17, name: "Legacy package", short_code: null }] };
const preview: LegacyMMWXImportPreview = { revision: "preview-r1", ready: true, total_users: 1, new_users: 1, existing_users: 0, imported_accounts: 1, replaced_accounts: 0, skipped_accounts: 0, imported_tokens: 1, replaced_tokens: 0, skipped_tokens: 0, imported_totp: 1, mapped_packages: 1, assigned_plans: 1, imported_profiles: 0, replaced_profiles: 0, skipped_profiles: 0, imported_profile_assignments: 0, blockers: [], warnings: ["Test migration warning"], license_required: false };
async function flush() { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); }
function file(contents: string) { const result = new File([contents], "identity.json", { type: "application/json" }); Object.defineProperty(result, "text", { value: async () => contents }); return result; }
beforeEach(() => {
  vi.resetAllMocks(); vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(previewLegacyMMWXIdentities).mockResolvedValue(preview); vi.mocked(importLegacyMMWXIdentities).mockResolvedValue({ preview, applied: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount() { const props = { open: true, plans: [{ id: "p", name: "Basic" }] as SubscriptionPlan[], onOpenChange: vi.fn(), onImported: vi.fn() }; return { ...render(<LegacyMMWXImportDialog {...props} />), props }; }
function selectFile(value: File) { fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [value] } }); }

describe("React MMWX identity import", { timeout: 20_000 }, () => {
  it("requires a fresh preview and exact user-count confirmation for mapped packages", async () => {
    const { props } = mount(); const identityFile = file(JSON.stringify(bundle)); selectFile(identityFile); await flush();
    expect(Object.getOwnPropertyDescriptor(identityFile, Upload.LIST_IGNORE)?.value).toBe(true);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Legacy package" })); fireEvent.click(screen.getByText("Basic", { selector: ".ant-select-item-option-content" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview" })); await flush();
    expect(previewLegacyMMWXIdentities).toHaveBeenCalledWith(bundle, false, undefined, { 17: "p" });
    expect((screen.getByRole("button", { name: "Import" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirm user count (1)"), { target: { value: "1" } }); fireEvent.click(screen.getByRole("button", { name: "Import" })); await flush();
    expect(importLegacyMMWXIdentities).toHaveBeenCalledWith(bundle, false, preview, 1, undefined, { 17: "p" }); expect(props.onImported).toHaveBeenCalledOnce(); expect(screen.getByText("Imported 1 identities")).toBeTruthy(); expect(screen.getByText("No file selected")).toBeTruthy();
  });
  it("invalidates a previous preview when replacement or package mapping changes", async () => {
    mount(); selectFile(file(JSON.stringify(bundle))); await flush(); fireEvent.click(screen.getByRole("button", { name: "Preview" })); await flush();
    fireEvent.change(screen.getByLabelText("Confirm user count (1)"), { target: { value: "1" } });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Legacy package" })); fireEvent.click(screen.getByText("Basic", { selector: ".ant-select-item-option-content" }));
    expect(screen.queryByLabelText("Confirm user count (1)")).toBeNull(); expect((screen.getByRole("button", { name: "Import" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Preview" })); await flush(); fireEvent.click(screen.getByRole("switch", { name: "Replace existing logins and links" }));
    expect(screen.queryByLabelText("Confirm user count (1)")).toBeNull(); expect(screen.getByText("Existing subscriber sessions will be revoked.")).toBeTruthy();
  });
  it("never rounds or clamps a mismatched confirmation into an importable user count", async () => {
    mount(); selectFile(file(JSON.stringify(bundle))); await flush(); fireEvent.click(screen.getByRole("button", { name: "Preview" })); await flush();
    const input = screen.getByLabelText("Confirm user count (1)"), apply = screen.getByRole("button", { name: "Import" }) as HTMLButtonElement;
    for (const value of ["2", "0", "0.6", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "1" } }); fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
      expect(apply.disabled).toBe(true);
      fireEvent.click(apply); await flush(); expect(importLegacyMMWXIdentities).not.toHaveBeenCalled();
    }
  });
  it("rejects oversized and invalid JSON without echoing file secrets", async () => {
    mount(); const oversized = file("private-secret"); Object.defineProperty(oversized, "size", { value: 16 * 1024 * 1024 + 1 }); selectFile(oversized); await flush();
    expect(screen.getByText("Identity file exceeds 16 MB")).toBeTruthy();
    selectFile(file('{"password_hash":"private-secret" invalid')); await flush();
    expect(screen.getByText("Invalid MMWX identity JSON")).toBeTruthy(); expect(document.body.textContent).not.toContain("private-secret"); expect(previewLegacyMMWXIdentities).not.toHaveBeenCalled();
  });
  it("clears the identity bundle on close and rejects late preview completion", async () => {
    let resolve!: (value: LegacyMMWXImportPreview) => void; vi.mocked(previewLegacyMMWXIdentities).mockReturnValue(new Promise(done => { resolve = done; }));
    const { props, rerender } = mount(); selectFile(file(JSON.stringify(bundle))); await flush(); fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    rerender(<LegacyMMWXImportDialog {...props} open={false} />); await act(async () => resolve(preview)); rerender(<LegacyMMWXImportDialog {...props} />);
    expect(screen.getByText("No file selected")).toBeTruthy(); expect(screen.queryByText("Test migration warning")).toBeNull(); expect((screen.getByRole("button", { name: "Preview" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
