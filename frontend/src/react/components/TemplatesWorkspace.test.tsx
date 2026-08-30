// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Upload } from "antd";
import TemplatesWorkspace from "./TemplatesWorkspace";
import { createSubscriptionTemplate, getSubscriptionTemplate, getSubscriptionTemplateSettings, getSubscriptionTemplateStarter, listSubscriptionTemplates, previewSubscriptionTemplate, removeSubscriptionTemplate, updateSubscriptionTemplate, updateSubscriptionTemplateSettings } from "../../services/subscription-templates";
import { listProductUsers } from "../../services/subscriptions";
import type { SubscriptionTemplate, SubscriptionTemplateSettings } from "../../domain/subscription-templates";
const session = vi.hoisted(() => ({ username: "alice" }));
vi.mock("../hooks/useSession", () => ({ useSubscriberSession: () => ({ ready: true, error: "", session }) }));
vi.mock("../../services/subscription-templates", () => ({ createSubscriptionTemplate: vi.fn(), getSubscriptionTemplate: vi.fn(), getSubscriptionTemplateSettings: vi.fn(), getSubscriptionTemplateStarter: vi.fn(), listSubscriptionTemplates: vi.fn(), previewSubscriptionTemplate: vi.fn(), removeSubscriptionTemplate: vi.fn(), subscriptionTemplateDownloadUrl: (id: string) => `/api/file/${id}`, updateSubscriptionTemplate: vi.fn(), updateSubscriptionTemplateSettings: vi.fn() }));
vi.mock("../../services/subscriptions", () => ({ listProductUsers: vi.fn() }));
const template: SubscriptionTemplate = { id: "t1", name: "main.yaml", format: "clash", owner_username: null, is_public: true, editable: true, revision: "template-r1", content: "proxies: []", size_bytes: 11, plan_names: [], default_scopes: [], created_at: "", updated_at: "" };
const settings: SubscriptionTemplateSettings = { clash_template_id: null, surge_template_id: null, enabled: true, revision: "settings-r1" };
async function flush() { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); }
beforeEach(() => {
  vi.resetAllMocks(); session.username = "alice";
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [template], settings, can_manage: true, license_required: false });
  vi.mocked(getSubscriptionTemplateSettings).mockResolvedValue(settings);
  vi.mocked(getSubscriptionTemplate).mockResolvedValue(template);
  vi.mocked(listProductUsers).mockResolvedValue({ users: [], license_required: false });
  vi.mocked(getSubscriptionTemplateStarter).mockResolvedValue({ format: "clash", content: "proxies: []" });
  vi.mocked(previewSubscriptionTemplate).mockResolvedValue({ content: "rendered-secret-client", included_nodes: 1, excluded_nodes: 1, warnings: ["One node excluded"] });
  vi.mocked(updateSubscriptionTemplate).mockResolvedValue({ ...template, revision: "template-r2" });
  vi.mocked(removeSubscriptionTemplate).mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function selectTemplate() { await flush(); fireEvent.click(screen.getByRole("button", { name: "main.yaml" })); await flush(); }

describe("React template workspace", { timeout: 20_000 }, () => {
  it("imports files as local drafts without retaining them in the hidden upload list", async () => {
    const { unmount } = render(<TemplatesWorkspace />); await flush();
    for (const [name, content] of [["first.yaml", "proxies: [first-private-draft]"], ["second.yaml", "proxies: [second-private-draft]"]]) {
      const file = new File([content], name, { type: "application/yaml" }); Object.defineProperty(file, "text", { value: async () => content });
      fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [file] } }); await flush();
      expect(Object.getOwnPropertyDescriptor(file, Upload.LIST_IGNORE)?.value).toBe(true);
      expect((screen.getByLabelText("Filename") as HTMLInputElement).value).toBe(name);
      expect((screen.getByLabelText("Template source") as HTMLTextAreaElement).value).toBe(content);
    }
    expect(createSubscriptionTemplate).not.toHaveBeenCalled(); expect(updateSubscriptionTemplate).not.toHaveBeenCalled();
    unmount(); expect(document.body.textContent).not.toContain("private-draft");
  });
  it("uses existing revisions for structured edits and retains download access", async () => {
    render(<TemplatesWorkspace />); await selectTemplate();
    expect(screen.getByRole("link", { name: "Download template" }).getAttribute("href")).toBe("/api/file/t1");
    fireEvent.change(screen.getByLabelText("Template source"), { target: { value: "proxies: [updated]" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" })); await flush();
    expect(updateSubscriptionTemplate).toHaveBeenCalledWith("t1", { name: "main.yaml", format: "clash", content: "proxies: [updated]", owner_username: null, is_public: true }, "template-r1", false);
  });
  it("keeps a forbidden library read-only without showing privileged owner fields to subscribers", async () => {
    vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [{ ...template, editable: false }], settings: { ...settings, enabled: false }, can_manage: false, license_required: false });
    vi.mocked(getSubscriptionTemplateSettings).mockResolvedValue({ ...settings, enabled: false });
    render(<TemplatesWorkspace subscriber />); await selectTemplate();
    expect((screen.getByRole("button", { name: "New template" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Save defaults" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Template source") as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.queryByLabelText("Owner")).toBeNull(); expect(screen.queryByLabelText("Public")).toBeNull(); expect(listProductUsers).not.toHaveBeenCalled();
  });
  it("duplicates public templates into private drafts and forces subscriber ownership", async () => {
    vi.mocked(createSubscriptionTemplate).mockResolvedValue({ ...template, id: "new", name: "main-copy.yaml", owner_username: "alice", is_public: false });
    render(<TemplatesWorkspace subscriber />); await selectTemplate(); fireEvent.click(screen.getByRole("button", { name: "Duplicate template" }));
    expect((screen.getByLabelText("Filename") as HTMLInputElement).value).toBe("main-copy.yaml");
    fireEvent.click(screen.getByRole("button", { name: "Save" })); await flush();
    expect(createSubscriptionTemplate).toHaveBeenCalledWith({ name: "main-copy.yaml", format: "clash", content: "proxies: []", owner_username: null, is_public: false }, true);
    expect(updateSubscriptionTemplate).not.toHaveBeenCalled();
  });
  it("renders compatibility warnings and invalidates preview when source changes", async () => {
    render(<TemplatesWorkspace subscriber />); await selectTemplate(); fireEvent.click(screen.getByRole("button", { name: "Preview" })); await flush();
    expect(previewSubscriptionTemplate).toHaveBeenCalledWith("clash", "proxies: []", null, true);
    expect((screen.getByLabelText("Rendered preview") as HTMLTextAreaElement).value).toBe("rendered-secret-client"); expect(screen.getByText("1 excluded")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Source" })); fireEvent.change(screen.getByLabelText("Template source"), { target: { value: "proxies: [new]" } });
    fireEvent.click(screen.getByRole("tab", { name: "Preview" })); expect((screen.getByLabelText("Rendered preview") as HTMLTextAreaElement).value).toBe("");
  });
  it("requires the exact filename before removal and passes the same revision", async () => {
    render(<TemplatesWorkspace />); await selectTemplate(); fireEvent.click(screen.getByRole("button", { name: "Remove template" }));
    expect((screen.getByRole("button", { name: "Remove" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirm filename"), { target: { value: "main.yaml" } }); fireEvent.click(screen.getByRole("button", { name: "Remove" })); await flush();
    expect(removeSubscriptionTemplate).toHaveBeenCalledWith("t1", "template-r1", "main.yaml", false); expect(screen.queryByLabelText("Template source")).toBeNull();
  });
  it("does not let a late preview survive a subscriber-session change", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof previewSubscriptionTemplate>>) => void;
    vi.mocked(previewSubscriptionTemplate).mockReturnValue(new Promise(done => { resolve = done; }));
    const { rerender } = render(<TemplatesWorkspace subscriber />); await selectTemplate(); fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    session.username = "bob"; rerender(<TemplatesWorkspace subscriber />); await flush();
    await act(async () => resolve({ content: "late-alice-secret", included_nodes: 1, excluded_nodes: 0, warnings: [] }));
    expect(screen.queryByLabelText("Rendered preview")).toBeNull(); expect(document.body.textContent).not.toContain("late-alice-secret");
  });
  it("saves defaults with their revision and selected subscriber scope", async () => {
    vi.mocked(listProductUsers).mockResolvedValue({ users: [{ username: "bob", display_name: "Bob" }] as Awaited<ReturnType<typeof listProductUsers>>["users"], license_required: false });
    vi.mocked(getSubscriptionTemplateSettings).mockImplementation(async username => ({ ...settings, revision: username ? "bob-settings" : "system-settings" }));
    vi.mocked(updateSubscriptionTemplateSettings).mockResolvedValue({ ...settings, revision: "next" });
    render(<TemplatesWorkspace />); await flush(); fireEvent.mouseDown(screen.getByRole("combobox", { name: "Subscriber" })); fireEvent.click(screen.getByText("Bob (bob)", { selector: ".ant-select-item-option-content" })); await flush();
    fireEvent.click(screen.getByRole("switch", { name: "Allow personal templates" })); fireEvent.click(screen.getByRole("button", { name: "Save defaults" })); await flush();
    expect(updateSubscriptionTemplateSettings).toHaveBeenCalledWith({ ...settings, enabled: false, revision: "bob-settings" }, "bob", false);
  });
  it("rejects oversized template files before reading or creating a draft", async () => {
    const { container } = render(<TemplatesWorkspace />); await flush();
    const file = new File([""], "large.yaml", { type: "text/yaml" }); Object.defineProperty(file, "size", { value: 2 * 1024 * 1024 + 1 });
    fireEvent.change(container.querySelector("input[type=file]")!, { target: { files: [file] } }); await flush();
    expect(screen.getByText("Template files are limited to 2 MiB")).toBeTruthy(); expect(screen.queryByLabelText("Template source")).toBeNull();
  });
});
