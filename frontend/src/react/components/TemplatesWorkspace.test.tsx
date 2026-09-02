// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TemplatesWorkspace from "./TemplatesWorkspace";
import { createSubscriptionTemplate, getSubscriptionTemplate, getSubscriptionTemplateStarter, listSubscriptionTemplates, previewSubscriptionTemplate, removeSubscriptionTemplate, updateSubscriptionTemplate, updateSubscriptionTemplateSettings } from "../../services/subscription-templates";
import type { SubscriptionTemplate, SubscriptionTemplateSettings } from "../../domain/subscription-templates";

vi.mock("../../services/subscription-templates", () => ({
  createSubscriptionTemplate: vi.fn(), getSubscriptionTemplate: vi.fn(), getSubscriptionTemplateStarter: vi.fn(),
  listSubscriptionTemplates: vi.fn(), previewSubscriptionTemplate: vi.fn(), removeSubscriptionTemplate: vi.fn(),
  subscriptionTemplateDownloadUrl: (id: string) => `/api/file/${id}`, updateSubscriptionTemplate: vi.fn(), updateSubscriptionTemplateSettings: vi.fn(),
}));
const template: SubscriptionTemplate = { id: "t1", name: "main.yaml", format: "clash", owner_username: null, is_public: true, editable: true, revision: "template-r1", content: "proxies: []", size_bytes: 11, plan_names: [], default_scopes: ["system"], created_at: "", updated_at: "" };
const settings: SubscriptionTemplateSettings = { clash_template_id: "t1", enabled: true, revision: "settings-r1" };
async function flush() { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); }

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listSubscriptionTemplates).mockResolvedValue({ templates: [template], settings, can_manage: true, license_required: false });
  vi.mocked(getSubscriptionTemplate).mockResolvedValue(template);
  vi.mocked(getSubscriptionTemplateStarter).mockResolvedValue({ format: "clash", content: "proxies: []" });
  vi.mocked(previewSubscriptionTemplate).mockResolvedValue({ content: "rendered", included_nodes: 1, excluded_nodes: 0, warnings: [] });
  vi.mocked(updateSubscriptionTemplate).mockResolvedValue({ ...template, revision: "template-r2" });
  vi.mocked(updateSubscriptionTemplateSettings).mockResolvedValue(settings);
  vi.mocked(removeSubscriptionTemplate).mockResolvedValue(undefined);
});
afterEach(() => cleanup());
async function selectTemplate() { await flush(); fireEvent.click(screen.getByRole("button", { name: "main.yaml" })); await flush(); }

describe("global template workspace", () => {
  it("shows the seeded global default and has no personal-template controls", async () => {
    render(<TemplatesWorkspace />); await flush();
    expect((screen.getByRole("combobox", { name: "全局默认模板" }) as HTMLSelectElement).value).toBe("t1");
    expect(screen.getByText(/全部流量都经过代理/)).toBeTruthy();
    expect(screen.queryByLabelText("所属用户")).toBeNull();
  });

  it("updates an existing template as a global public Clash template", async () => {
    render(<TemplatesWorkspace />); await selectTemplate();
    expect(screen.getByRole("link", { name: "下载模板" }).getAttribute("href")).toBe("/api/file/t1");
    fireEvent.change(screen.getByLabelText("模板源码"), { target: { value: "proxies: [updated]" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await flush();
    expect(updateSubscriptionTemplate).toHaveBeenCalledWith("t1", { name: "main.yaml", format: "clash", content: "proxies: [updated]", owner_username: null, is_public: true }, "template-r1");
  });

  it("uploads only YAML as an unsaved local draft", async () => {
    render(<TemplatesWorkspace />); await flush();
    const file = new File(["proxies: [draft]"], "draft.yaml", { type: "application/yaml" });
    Object.defineProperty(file, "text", { value: async () => "proxies: [draft]" });
    fireEvent.change(document.querySelector("input[type=file]")!, { target: { files: [file] } }); await flush();
    expect((screen.getByLabelText("文件名") as HTMLInputElement).value).toBe("draft.yaml");
    expect(createSubscriptionTemplate).not.toHaveBeenCalled();
  });

  it("previews and requires the exact filename for deletion", async () => {
    render(<TemplatesWorkspace />); await selectTemplate();
    fireEvent.click(screen.getByRole("button", { name: "预览" })); await flush();
    expect(previewSubscriptionTemplate).toHaveBeenCalledWith("clash", "proxies: []", null);
    expect((screen.getByLabelText("渲染预览") as HTMLTextAreaElement).value).toBe("rendered");
    fireEvent.click(screen.getByRole("button", { name: "移除模板" }));
    expect((screen.getByRole("button", { name: "删除" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("确认文件名"), { target: { value: "main.yaml" } });
    fireEvent.click(screen.getByRole("button", { name: "删除" })); await flush();
    expect(removeSubscriptionTemplate).toHaveBeenCalledWith("t1", "template-r1", "main.yaml");
  });

  it("does not call the administrator API in subscriber mode", () => {
    render(<TemplatesWorkspace subscriber />);
    expect(screen.getByText("订阅模板由管理员全局维护")).toBeTruthy();
    expect(listSubscriptionTemplates).not.toHaveBeenCalled();
  });
});
