// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExternalConfirmationRead, ExternalPreviewCancelResponse, ExternalPreviewRead, ExternalSourceDetail, ExternalSourceRead, ExternalSourcesResponse } from "../../domain/external-subscriptions";
import type { ProductUser } from "../../domain/subscriptions";
import {
  cancelExternalPreview, confirmExternalPreview, createExternalPreview, createExternalSource, deleteExternalSource,
  ExternalSubscriptionsError, getExternalPreview, getExternalSource, listExternalSources, updateExternalNode, updateExternalSource,
} from "../../services/external-subscriptions";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import ExternalSubscriptionsPanel from "./ExternalSubscriptionsPanel";

vi.mock("../../services/external-subscriptions", async importOriginal => {
  const actual = await importOriginal<typeof import("../../services/external-subscriptions")>();
  return { ...actual, listExternalSources: vi.fn(), createExternalSource: vi.fn(), getExternalSource: vi.fn(), updateExternalSource: vi.fn(), deleteExternalSource: vi.fn(), updateExternalNode: vi.fn(), createExternalPreview: vi.fn(), getExternalPreview: vi.fn(), confirmExternalPreview: vi.fn(), cancelExternalPreview: vi.fn() };
});
const users: ProductUser[] = ["alice", "bob"].map(username => ({ username, display_name: username === "alice" ? "Alice" : "Bob", role: "user", is_active: true, is_reset: false, reset_day: 1, created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z" }));
const source: ExternalSourceRead = {
  id: "source-1", owner_username: "alice", name: "Provider A", enabled: true, revision: 4, has_custom_user_agent: true,
  node_count: 2, available_node_count: 1, metadata: { upload: 10, download: 20, total: 1000 },
  last_synced_at: "2026-08-31T00:00:00Z", created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z",
};
const secondSource: ExternalSourceRead = { ...source, id: "source-2", owner_username: "bob", name: "Provider B", revision: 8, has_custom_user_agent: false };
const detail: ExternalSourceDetail = { source, license_required: false, nodes: [
  { id: "existing-1", source_id: source.id, upstream_name: "Upstream Tokyo", name: "Local Tokyo", protocol: "vless", enabled: true, present: true, available: true, reason: null },
  { id: "missing-1", source_id: source.id, upstream_name: "Missing node", name: "Missing node", protocol: "ss", enabled: true, present: false, available: false, reason: "上游中缺少此节点" },
] };
const secondDetail: ExternalSourceDetail = { ...detail, source: secondSource, nodes: [] };
const preview: ExternalPreviewRead = {
  id: "preview-1", source_id: source.id, source_revision: 4, created_at: "2026-08-31T00:00:00Z", expires_at: "2099-08-31T00:15:00Z", metadata: { upload: 11, download: 22, total: 1000 }, receipt: null, license_required: false,
  nodes: [
    { node_id: "new-1", upstream_name: "New Tokyo", name: "New Tokyo", protocol: "ss", existing: false, change: "new", selectable: true, reason: null, changed_fields: [] },
    { node_id: "new-blocked", upstream_name: "Unsupported new", name: "Unsupported new", protocol: "unsupported", existing: false, change: "new", selectable: false, reason: "不支持此协议", changed_fields: [] },
    { node_id: "existing-1", upstream_name: "Upstream Tokyo", name: "Local Tokyo", protocol: "vless", existing: true, change: "updated", selectable: false, reason: null, changed_fields: ["password", "server"] },
    { node_id: "missing-1", upstream_name: "Missing node", name: "Missing node", protocol: "ss", existing: true, change: "missing", selectable: false, reason: "此次抓取的来源中不存在此节点", changed_fields: [] },
    { node_id: "unavailable-1", upstream_name: "Unavailable node", name: "Unavailable node", protocol: "unsupported", existing: true, change: "unavailable", selectable: false, reason: "不支持此配置", changed_fields: [] },
    { node_id: "unchanged-1", upstream_name: "Unchanged node", name: "Unchanged node", protocol: "trojan", existing: true, change: "unchanged", selectable: false, reason: null, changed_fields: [] },
  ],
};
const receipt: ExternalConfirmationRead = { source_id: source.id, preview_id: preview.id, revision: 5, imported_count: 1, updated_count: 1, missing_count: 1, applied_at: "2026-08-31T00:10:00Z" };
const secretUrl = "https://provider.example/subscription?token=source-private-token";
const secretAgent = "private-agent-header";

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(listExternalSources).mockResolvedValue({ sources: [source, secondSource], license_required: false });
  vi.mocked(getExternalSource).mockImplementation(async id => id === source.id ? detail : secondDetail);
  vi.mocked(createExternalSource).mockResolvedValue(source); vi.mocked(updateExternalSource).mockResolvedValue({ ...source, revision: 5 });
  vi.mocked(deleteExternalSource).mockResolvedValue({ deleted: true, license_required: false });
  vi.mocked(updateExternalNode).mockResolvedValue({ ...detail, source: { ...source, revision: 5 } });
  vi.mocked(createExternalPreview).mockResolvedValue(preview); vi.mocked(getExternalPreview).mockResolvedValue(preview);
  vi.mocked(confirmExternalPreview).mockResolvedValue(receipt); vi.mocked(cancelExternalPreview).mockResolvedValue({ cancelled: true, license_required: false });
});
afterEach(async () => {
  cleanup();
  // Ant's short loading-state grace period belongs to the real DOM lifetime.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); });
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

function modal(title: string) {
  const heading = screen.getByText(title, { selector: ".ant-modal-title" });
  return within(heading.closest(".ant-modal") as HTMLElement);
}
function input(scope: ReturnType<typeof within>, label: string) { return scope.getByLabelText(label) as HTMLInputElement; }
async function select(label: string, option: string, scope: ReturnType<typeof within> = within(document.body)) {
  fireEvent.mouseDown(scope.getByRole("combobox", { name: label })); await flush();
  const options = screen.getAllByText(option, { selector: ".ant-select-item-option-content" });
  fireEvent.click(options[options.length - 1]!); await flush();
}
async function openSource(name = source.name) {
  await flush(); fireEvent.click(screen.getByRole("button", { name: `查看外部订阅来源 ${name}` })); await flush();
  return within(screen.getByTestId("external-source-detail"));
}
async function openPreview(fetch = true) {
  const panel = await openSource(); fireEvent.click(panel.getByRole("button", { name: "预览外部订阅来源" })); await flush();
  const dialog = modal("外部订阅来源预览");
  if (fetch) { fireEvent.click(dialog.getByRole("button", { name: "抓取外部订阅预览" })); await flush(); }
  return dialog;
}
function acknowledge(dialog: ReturnType<typeof within>, choose = true) {
  if (choose) fireEvent.click(dialog.getByRole("checkbox", { name: "导入外部节点 New Tokyo" }));
  fireEvent.click(dialog.getByRole("checkbox", { name: "接受外部订阅预览变更" }));
}
async function openCreate() {
  await flush(); fireEvent.click(screen.getByRole("button", { name: "添加外部订阅来源" })); await flush();
  return modal("添加外部订阅来源");
}
async function fillCreate(dialog: ReturnType<typeof within>) {
  await select("外部订阅所属用户", "Alice (alice)", dialog);
  fireEvent.change(input(dialog, "外部订阅来源名称"), { target: { value: "New provider" } });
  fireEvent.change(input(dialog, "外部订阅链接"), { target: { value: secretUrl } });
}

describe("external sources: explicit administration and secret handling", () => {
  it("loads only safe status and explains scope, local billing and unsupported follow-up phases", async () => {
    renderUi(<ExternalSubscriptionsPanel users={users} />); const panel = await openSource();
    expect(listExternalSources).toHaveBeenCalledExactlyOnceWith(); expect(getExternalSource).toHaveBeenCalledExactlyOnceWith(source.id);
    expect(panel.getByText("自定义（不显示）")).toBeTruthy();
    expect(panel.getByText("上游信息（不参与本地计费）")).toBeTruthy();
    expect(screen.getByText(/临时链接和命名订阅配置不会自动包含/)).toBeTruthy();
    expect(screen.getByText(/URI 列表及 Base64 编码内容/)).toBeTruthy();
    expect(screen.getByText(/也可在来源详情中开启定时刷新/)).toBeTruthy();
    expect(panel.getByText(/无法撤回客户端已下载的上游凭据/)).toBeTruthy();
    fireEvent.click(panel.getByRole("button", { name: "预览外部订阅来源" })); await flush();
    expect(modal("外部订阅来源预览").getByRole("button", { name: "抓取外部订阅预览" })).toBeTruthy();
    expect(createExternalPreview).not.toHaveBeenCalled(); expect(confirmExternalPreview).not.toHaveBeenCalled();
    expect(createExternalSource).not.toHaveBeenCalled(); expect(updateExternalSource).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(secretUrl); expect(document.body.textContent).not.toContain(secretAgent);
  });

  it("ignores a superseded list read and safely retries a failed status read", async () => {
    const old = deferred<ExternalSourcesResponse>(); vi.mocked(listExternalSources).mockRejectedValueOnce(new Error(secretUrl)).mockReturnValueOnce(old.promise);
    renderUi(<ExternalSubscriptionsPanel users={users} />); await flush();
    const refresh = screen.getByRole("button", { name: "刷新外部订阅来源" });
    expect(document.body.textContent).not.toContain(secretUrl); expect(screen.getByText(/无法完成外部订阅请求/)).toBeTruthy();
    fireEvent.click(refresh); await flush();
    const editor = await openCreate(); await fillCreate(editor); fireEvent.click(editor.getByRole("button", { name: "保存外部订阅来源" })); await flush();
    await act(async () => old.resolve({ sources: [secondSource], license_required: false }));
    expect(screen.getByRole("button", { name: "查看外部订阅来源 Provider A" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看外部订阅来源 Provider B" })).toBeNull();
    expect(createExternalPreview).not.toHaveBeenCalled();
  });

  it("does not let a late source detail replace a newly selected owner", async () => {
    const pending = deferred<ExternalSourceDetail>(); vi.mocked(getExternalSource).mockReturnValueOnce(pending.promise).mockResolvedValue(secondDetail);
    renderUi(<ExternalSubscriptionsPanel users={users} />); await openSource(); await openSource(secondSource.name);
    await act(async () => pending.resolve(detail));
    const panel = within(screen.getByTestId("external-source-detail"));
    expect(panel.getByText("bob")).toBeTruthy(); expect(panel.queryByText("alice")).toBeNull();
    expect(panel.queryByText("Local Tokyo")).toBeNull(); expect(panel.getByText("默认（clash-meta/2.4.0）")).toBeTruthy();
  });

  it("requires an explicit existing owner and clears source credentials when that owner changes", async () => {
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openCreate();
    const save = dialog.getByRole("button", { name: "保存外部订阅来源" }) as HTMLButtonElement;
    fireEvent.change(input(dialog, "外部订阅来源名称"), { target: { value: "Private provider" } });
    fireEvent.change(input(dialog, "外部订阅链接"), { target: { value: secretUrl } });
    expect(save.disabled).toBe(true); await select("外部订阅所属用户", "Alice (alice)", dialog);
    expect(input(dialog, "外部订阅链接").value).toBe("");
    await select("外部订阅 User-Agent 设置", "设置自定义 User-Agent", dialog);
    fireEvent.change(input(dialog, "外部订阅链接"), { target: { value: secretUrl } }); fireEvent.change(input(dialog, "外部订阅自定义 User-Agent"), { target: { value: secretAgent } });
    expect(save.disabled).toBe(false); await select("外部订阅所属用户", "Bob (bob)", dialog);
    expect(input(dialog, "外部订阅链接").value).toBe(""); expect(input(dialog, "外部订阅自定义 User-Agent").value).toBe("");
    expect(save.disabled).toBe(true); expect(createExternalSource).not.toHaveBeenCalled();
  });

  it("creates once with explicit credentials, clears inputs while pending, and never fetches on save", async () => {
    const pending = deferred<ExternalSourceRead>(); vi.mocked(createExternalSource).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />);
    const dialog = await openCreate(); await fillCreate(dialog); await select("外部订阅 User-Agent 设置", "设置自定义 User-Agent", dialog);
    fireEvent.change(input(dialog, "外部订阅自定义 User-Agent"), { target: { value: secretAgent } });
    const save = dialog.getByRole("button", { name: "保存外部订阅来源" }); fireEvent.click(save); fireEvent.click(save); await flush();
    expect(createExternalSource).toHaveBeenCalledExactlyOnceWith({ owner_username: "alice", name: "New provider", url: secretUrl, user_agent: secretAgent, enabled: true });
    expect(input(dialog, "外部订阅链接").value).toBe(""); expect(input(dialog, "外部订阅自定义 User-Agent").value).toBe("");
    await act(async () => pending.resolve(source)); await flush();
    expect(screen.queryByText("添加外部订阅来源", { selector: ".ant-modal-title" })).toBeNull();
    expect(updated).toHaveBeenCalledTimes(1); expect(createExternalPreview).not.toHaveBeenCalled(); expect(document.body.innerHTML).not.toContain(secretAgent);
  });

  it("discards pending creation on close and does not populate a reopened form from its late reply", async () => {
    const pending = deferred<ExternalSourceRead>(); vi.mocked(createExternalSource).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />);
    const dialog = await openCreate(); await fillCreate(dialog); fireEvent.click(dialog.getByRole("button", { name: "保存外部订阅来源" })); await flush();
    fireEvent.click(dialog.getByRole("button", { name: "取消编辑外部订阅来源" }));
    const reopened = await openCreate(); fireEvent.change(input(reopened, "外部订阅来源名称"), { target: { value: "Later draft" } });
    await act(async () => pending.resolve(source));
    expect(input(reopened, "外部订阅来源名称").value).toBe("Later draft"); expect(input(reopened, "外部订阅链接").value).toBe("");
    expect(updated).not.toHaveBeenCalled(); expect(getExternalSource).not.toHaveBeenCalled();
  });

  it("preserves write-only secrets and the fixed owner when editing ordinary fields", async () => {
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const panel = await openSource();
    fireEvent.click(panel.getByRole("button", { name: "编辑外部订阅来源" })); const dialog = modal("编辑外部订阅来源");
    expect(dialog.queryByRole("combobox", { name: "外部订阅所属用户" })).toBeNull(); expect(dialog.getByText("alice")).toBeTruthy();
    expect(input(dialog, "外部订阅链接").value).toBe("");
    fireEvent.change(input(dialog, "外部订阅来源名称"), { target: { value: " Renamed provider " } });
    fireEvent.click(dialog.getByRole("switch", { name: "启用外部订阅来源" }));
    fireEvent.click(dialog.getByRole("button", { name: "保存外部订阅来源" })); await flush();
    expect(updateExternalSource).toHaveBeenCalledExactlyOnceWith(source.id, { expected_revision: 4, name: "Renamed provider", enabled: false, url: null, user_agent: null });
    expect(updated).toHaveBeenCalledTimes(1); expect(createExternalPreview).not.toHaveBeenCalled();
  });

  it("replaces a URL and explicitly resets the user agent to the documented default", async () => {
    renderUi(<ExternalSubscriptionsPanel users={users} />); const panel = await openSource(); fireEvent.click(panel.getByRole("button", { name: "编辑外部订阅来源" })); const dialog = modal("编辑外部订阅来源");
    fireEvent.change(input(dialog, "外部订阅链接"), { target: { value: secretUrl } });
    await select("外部订阅 User-Agent 设置", "设置自定义 User-Agent", dialog);
    fireEvent.change(input(dialog, "外部订阅自定义 User-Agent"), { target: { value: secretAgent } });
    await select("外部订阅 User-Agent 设置", "使用默认值（clash-meta/2.4.0）", dialog);
    expect(dialog.queryByLabelText("外部订阅自定义 User-Agent")).toBeNull();
    expect(dialog.getByText(/更换链接后，须先预览并确认/)).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "保存外部订阅来源" })); await flush();
    expect(updateExternalSource).toHaveBeenCalledExactlyOnceWith(source.id, { expected_revision: 4, name: source.name, enabled: true, url: secretUrl, user_agent: "" });
    expect(createExternalPreview).not.toHaveBeenCalled();
  });

  it("rejects invalid names and custom user agents without silently substituting a default", async () => {
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openCreate(); await fillCreate(dialog);
    await select("外部订阅 User-Agent 设置", "设置自定义 User-Agent", dialog);
    const save = dialog.getByRole("button", { name: "保存外部订阅来源" }) as HTMLButtonElement;
    for (const value of ["", "non-ascii-é", "a".repeat(257)]) { fireEvent.change(input(dialog, "外部订阅自定义 User-Agent"), { target: { value } }); expect(save.disabled).toBe(true); }
    fireEvent.change(input(dialog, "外部订阅自定义 User-Agent"), { target: { value: secretAgent } });
    for (const value of [" ", "x".repeat(161)]) { fireEvent.change(input(dialog, "外部订阅来源名称"), { target: { value } }); expect(save.disabled).toBe(true); }
    expect(createExternalSource).not.toHaveBeenCalled();
  });

  it("requires explicit revision refresh after a source conflict, retaining choices but clearing replacements", async () => {
    vi.mocked(updateExternalSource).mockRejectedValueOnce(new ExternalSubscriptionsError(409, secretUrl)).mockResolvedValue({ ...source, revision: 10 });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const panel = await openSource(); fireEvent.click(panel.getByRole("button", { name: "编辑外部订阅来源" })); const dialog = modal("编辑外部订阅来源");
    fireEvent.change(input(dialog, "外部订阅来源名称"), { target: { value: "My intended name" } }); fireEvent.change(input(dialog, "外部订阅链接"), { target: { value: secretUrl } });
    fireEvent.click(dialog.getByRole("switch", { name: "启用外部订阅来源" }));
    const save = dialog.getByRole("button", { name: "保存外部订阅来源" }) as HTMLButtonElement; fireEvent.click(save); await flush();
    expect(save.disabled).toBe(true); expect(input(dialog, "外部订阅链接").value).toBe(""); expect(input(dialog, "外部订阅来源名称").value).toBe("My intended name");
    expect(document.body.textContent).not.toContain(secretUrl); expect(updateExternalSource).toHaveBeenCalledTimes(1);
    vi.mocked(getExternalSource).mockResolvedValue({ ...detail, source: { ...source, revision: 9, name: "Remote rename" } });
    fireEvent.click(dialog.getByRole("button", { name: "刷新来源版本" })); await flush();
    expect(dialog.getByText(/最新保存的来源：Remote rename/)).toBeTruthy(); expect(save.disabled).toBe(false);
    expect(updateExternalSource).toHaveBeenCalledTimes(1); fireEvent.click(save); await flush();
    expect(updateExternalSource).toHaveBeenLastCalledWith(source.id, { expected_revision: 9, name: "My intended name", enabled: false, url: null, user_agent: null });
  });

  it("renames/disables a node against the source revision without changing its upstream identity", async () => {
    const pending = deferred<ExternalSourceDetail>(); vi.mocked(updateExternalNode).mockReturnValue(pending.promise);
    renderUi(<ExternalSubscriptionsPanel users={users} />); const panel = await openSource(); fireEvent.click(panel.getByRole("button", { name: "编辑外部节点 Local Tokyo" })); const dialog = modal("编辑外部节点");
    expect(dialog.getByText(/上游标识：Upstream Tokyo/)).toBeTruthy();
    fireEvent.change(input(dialog, "外部节点名称"), { target: { value: "Office Tokyo" } }); fireEvent.click(dialog.getByRole("switch", { name: "启用外部节点" }));
    const save = dialog.getByRole("button", { name: "保存外部节点" }); fireEvent.click(save); fireEvent.click(save); await flush();
    expect(updateExternalNode).toHaveBeenCalledExactlyOnceWith(source.id, "existing-1", { expected_revision: 4, name: "Office Tokyo", enabled: false });
    await act(async () => pending.resolve({ ...detail, source: { ...source, revision: 5 } }));
    expect(screen.queryByText("编辑外部节点", { selector: ".ant-modal-title" })).toBeNull(); expect(createExternalPreview).not.toHaveBeenCalled();
  });

  it("keeps node choices on conflict and blocks a retry if a refreshed node has disappeared", async () => {
    vi.mocked(updateExternalNode).mockRejectedValue(new ExternalSubscriptionsError(409));
    renderUi(<ExternalSubscriptionsPanel users={users} />); const panel = await openSource(); fireEvent.click(panel.getByRole("button", { name: "编辑外部节点 Local Tokyo" })); const dialog = modal("编辑外部节点");
    fireEvent.change(input(dialog, "外部节点名称"), { target: { value: "My node choice" } }); fireEvent.click(dialog.getByRole("button", { name: "保存外部节点" })); await flush();
    vi.mocked(getExternalSource).mockResolvedValue({ ...detail, source: { ...source, revision: 6 }, nodes: [] });
    fireEvent.click(dialog.getByRole("button", { name: "刷新外部节点版本" })); await flush();
    expect(input(dialog, "外部节点名称").value).toBe("My node choice"); expect(dialog.getByText(/此外部节点已不存在/)).toBeTruthy();
    expect((dialog.getByRole("button", { name: "保存外部节点" }) as HTMLButtonElement).disabled).toBe(true); expect(updateExternalNode).toHaveBeenCalledTimes(1);
  });

  it("requires deletion acknowledgment, and re-acknowledgment after an explicit revision refresh", async () => {
    vi.mocked(deleteExternalSource).mockRejectedValueOnce(new ExternalSubscriptionsError(409)).mockResolvedValue({ deleted: true, license_required: false });
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const panel = await openSource();
    fireEvent.click(panel.getByRole("button", { name: "删除外部订阅来源" })); let dialog = modal("删除外部订阅来源？");
    expect((dialog.getByRole("button", { name: "确认删除外部订阅来源" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("button", { name: "保留外部订阅来源" })); expect(deleteExternalSource).not.toHaveBeenCalled();
    fireEvent.click(panel.getByRole("button", { name: "删除外部订阅来源" })); dialog = modal("删除外部订阅来源？");
    fireEvent.click(dialog.getByRole("checkbox", { name: "确认外部订阅来源删除影响" })); fireEvent.click(dialog.getByRole("button", { name: "确认删除外部订阅来源" })); await flush();
    vi.mocked(getExternalSource).mockResolvedValue({ ...detail, source: { ...source, revision: 6, node_count: 3 } });
    fireEvent.click(dialog.getByRole("button", { name: "刷新删除操作版本" })); await flush();
    expect((dialog.getByRole("checkbox", { name: "确认外部订阅来源删除影响" }) as HTMLInputElement).checked).toBe(false);
    expect(dialog.getByText(/其 3 个已保存节点/)).toBeTruthy(); expect(deleteExternalSource).toHaveBeenCalledTimes(1);
    fireEvent.click(dialog.getByRole("checkbox", { name: "确认外部订阅来源删除影响" })); fireEvent.click(dialog.getByRole("button", { name: "确认删除外部订阅来源" })); await flush();
    expect(deleteExternalSource).toHaveBeenLastCalledWith(source.id, { expected_revision: 6, confirm: true }); expect(updated).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("external-source-detail")).toBeNull(); expect(createExternalPreview).not.toHaveBeenCalled();
  });
});

describe("external previews: explicit choice, atomic confirmation and receipts", () => {
  it("shows all change classes, allows only selectable new nodes, and requires acceptance before confirmation", async () => {
    const pending = deferred<ExternalPreviewRead>(); vi.mocked(createExternalPreview).mockReturnValue(pending.promise);
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(false);
    const fetchButton = dialog.getByRole("button", { name: "抓取外部订阅预览" }); fireEvent.click(fetchButton); fireEvent.click(fetchButton); await flush();
    expect(createExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, { expected_revision: 4 }); expect(confirmExternalPreview).not.toHaveBeenCalled();
    await act(async () => pending.resolve(preview));
    const confirm = dialog.getByRole("button", { name: "确认外部订阅预览" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true); expect(dialog.getByText("变更字段：password, server")).toBeTruthy();
    for (const name of ["Unsupported new", "Local Tokyo", "Missing node", "Unavailable node", "Unchanged node"]) expect((dialog.getByRole("checkbox", { name: `导入外部节点 ${name}` }) as HTMLInputElement).disabled).toBe(true);
    expect(dialog.getByText("不支持此协议")).toBeTruthy(); expect(dialog.getByText("此次抓取的来源中不存在此节点")).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "选择全部外部新节点" })); expect(dialog.getByText("已选择 1 个新节点")).toBeTruthy(); expect(confirm.disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "接受外部订阅预览变更" })); fireEvent.click(confirm); await flush();
    expect(confirmExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id, { expected_revision: 4, selected_node_ids: ["new-1"], accept_changes: true });
    expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(dialog.getByText(/已导入 1 个，更新 1 个，缺失 1 个/)).toBeTruthy();
    expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull(); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it("can explicitly apply existing-node updates/missing states without importing any new nodes", async () => {
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog, false);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    expect(confirmExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id, { expected_revision: 4, selected_node_ids: [], accept_changes: true });
  });

  it("checks the 1000-node selection limit without silently truncating the chosen nodes", async () => {
    vi.mocked(createExternalPreview).mockResolvedValue({ ...preview, nodes: Array.from({ length: 1001 }, (_, index) => ({ ...preview.nodes[0]!, node_id: `new-${index}`, name: `New ${index}`, upstream_name: `New ${index}` })) });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview();
    fireEvent.click(dialog.getByRole("button", { name: "选择全部外部新节点" })); fireEvent.click(dialog.getByRole("checkbox", { name: "接受外部订阅预览变更" }));
    expect(dialog.getByText("已选择 1001 个新节点")).toBeTruthy(); expect(dialog.getByText(/每次确认最多选择 1,000 个新节点/)).toBeTruthy();
    expect((dialog.getByRole("button", { name: "确认外部订阅预览" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("button", { name: "清空外部节点选择" }));
    expect(dialog.getByText("已选择 0 个新节点")).toBeTruthy(); expect(confirmExternalPreview).not.toHaveBeenCalled();
  });

  it("locks an ambiguous confirmation to the same preview/revision/selection for an explicit retry", async () => {
    vi.mocked(confirmExternalPreview).mockRejectedValueOnce(new Error(`${secretUrl} ${secretAgent}`)).mockResolvedValue(receipt);
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    expect(dialog.getByText("确认结果尚不明确")).toBeTruthy(); expect(document.body.textContent).not.toContain(secretUrl);
    expect((dialog.getByRole("checkbox", { name: "导入外部节点 New Tokyo" }) as HTMLInputElement).disabled).toBe(true);
    expect((dialog.getByRole("checkbox", { name: "接受外部订阅预览变更" }) as HTMLInputElement).disabled).toBe(true);
    expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull(); expect(dialog.queryByRole("button", { name: "抓取外部订阅预览" })).toBeNull();
    const payload = vi.mocked(confirmExternalPreview).mock.calls[0]?.[2];
    fireEvent.click(dialog.getByRole("button", { name: "重试同一外部订阅确认" })); await flush();
    expect(confirmExternalPreview).toHaveBeenCalledTimes(2); expect(confirmExternalPreview).toHaveBeenLastCalledWith(source.id, preview.id, payload);
    expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it("checks the existing receipt after ambiguity, preserving the original selection while no receipt exists", async () => {
    vi.mocked(confirmExternalPreview).mockRejectedValue(new ExternalSubscriptionsError(503));
    vi.mocked(getExternalPreview).mockResolvedValueOnce(preview).mockResolvedValue({ ...preview, receipt });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    const check = dialog.getByRole("button", { name: "查询外部订阅确认结果" }); fireEvent.click(check); await flush();
    expect(dialog.getByText(/暂未取得回执/)).toBeTruthy();
    expect((dialog.getByRole("checkbox", { name: "导入外部节点 New Tokyo" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(check); await flush();
    expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(getExternalPreview).toHaveBeenNthCalledWith(1, source.id, preview.id); expect(getExternalPreview).toHaveBeenNthCalledWith(2, source.id, preview.id);
    expect(confirmExternalPreview).toHaveBeenCalledTimes(1); expect(createExternalPreview).toHaveBeenCalledTimes(1); expect(cancelExternalPreview).not.toHaveBeenCalled();
  });

  it("retains a conflicted preview selection but never upgrades its revision or silently retries", async () => {
    vi.mocked(confirmExternalPreview).mockRejectedValue(new ExternalSubscriptionsError(409, "External source changed after this preview"));
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    vi.mocked(getExternalSource).mockResolvedValue({ ...detail, source: { ...source, revision: 9 } });
    fireEvent.click(dialog.getByRole("button", { name: "刷新预览来源状态" })); await flush();
    expect(dialog.getByText(/当前来源版本：9/)).toBeTruthy();
    expect((dialog.getByRole("checkbox", { name: "导入外部节点 New Tokyo" }) as HTMLInputElement).checked).toBe(true);
    expect((dialog.getByRole("button", { name: "重试同一外部订阅确认" }) as HTMLButtonElement).disabled).toBe(true);
    expect(confirmExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id, { expected_revision: 4, selected_node_ids: ["new-1"], accept_changes: true }); expect(createExternalPreview).toHaveBeenCalledTimes(1);
    vi.mocked(getExternalPreview).mockResolvedValue({ ...preview, receipt }); fireEvent.click(dialog.getByRole("button", { name: "查询外部订阅确认结果" })); await flush();
    expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(cancelExternalPreview).not.toHaveBeenCalled();
  });

  it("allows an explicit corrected choice after a definite validation rejection without refetching", async () => {
    vi.mocked(confirmExternalPreview).mockRejectedValueOnce(new ExternalSubscriptionsError(422)).mockResolvedValue(receipt);
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    expect(dialog.queryByText("确认结果尚不明确")).toBeNull();
    fireEvent.click(dialog.getByRole("button", { name: "清空外部节点选择" }));
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    expect(confirmExternalPreview).toHaveBeenLastCalledWith(source.id, preview.id, { expected_revision: 4, selected_node_ids: [], accept_changes: true }); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it("does not confirm an expired unsubmitted preview or automatically obtain a replacement", async () => {
    vi.mocked(createExternalPreview).mockResolvedValue({ ...preview, expires_at: "2000-01-01T00:00:00Z" });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush();
    expect(dialog.getByText(/此预览已过期/)).toBeTruthy(); expect(confirmExternalPreview).not.toHaveBeenCalled(); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("recovers a known preview using only GET (confirmed=%s)", async confirmed => {
    vi.mocked(getExternalPreview).mockResolvedValue({ ...preview, receipt: confirmed ? receipt : null });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview(false);
    fireEvent.change(input(dialog, "恢复外部订阅预览 ID"), { target: { value: preview.id } });
    fireEvent.click(dialog.getByRole("button", { name: "恢复外部订阅预览" })); await flush();
    expect(getExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id);
    expect(createExternalPreview).not.toHaveBeenCalled(); expect(confirmExternalPreview).not.toHaveBeenCalled();
    if (confirmed) { expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull(); }
    else { expect(dialog.getByText(/此预览尚未确认/)).toBeTruthy(); expect((dialog.getByRole("button", { name: "确认外部订阅预览" }) as HTMLButtonElement).disabled).toBe(true); }
  });

  it("cancels only an unconfirmed preview, blocks duplicate requests, and does not change the source", async () => {
    const pending = deferred<ExternalPreviewCancelResponse>(); vi.mocked(cancelExternalPreview).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const dialog = await openPreview();
    const cancel = dialog.getByRole("button", { name: "取消外部订阅预览" }); fireEvent.click(cancel); fireEvent.click(cancel); await flush();
    expect(cancelExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id);
    await act(async () => pending.resolve({ cancelled: true, license_required: false }));
    expect(screen.queryByText("外部订阅来源预览", { selector: ".ant-modal-title" })).toBeNull();
    expect(updated).not.toHaveBeenCalled(); expect(updateExternalSource).not.toHaveBeenCalled(); expect(confirmExternalPreview).not.toHaveBeenCalled();
  });

  it("recovers an already-confirmed receipt after a cancellation race without issuing another delete", async () => {
    vi.mocked(cancelExternalPreview).mockRejectedValue(new ExternalSubscriptionsError(409, "Preview is already confirmed; its receipt is retained"));
    vi.mocked(getExternalPreview).mockResolvedValue({ ...preview, receipt });
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview();
    fireEvent.click(dialog.getByRole("button", { name: "取消外部订阅预览" })); await flush();
    expect(dialog.getByText(/仍可查看确认回执/)).toBeTruthy();
    expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull();
    fireEvent.click(dialog.getByRole("button", { name: "查询外部订阅确认结果" })); await flush();
    expect(dialog.getByText("外部订阅预览已确认")).toBeTruthy(); expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull();
    expect(cancelExternalPreview).toHaveBeenCalledTimes(1); expect(confirmExternalPreview).not.toHaveBeenCalled(); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it("keeps failed cancellation visible and does not misreport its outcome", async () => {
    vi.mocked(cancelExternalPreview).mockRejectedValue(new Error(secretUrl));
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview();
    fireEvent.click(dialog.getByRole("button", { name: "取消外部订阅预览" })); await flush();
    expect(dialog.getByText(preview.id)).toBeTruthy(); expect(document.body.textContent).not.toContain(secretUrl);
    expect(dialog.getByText(/无法完成外部订阅请求/)).toBeTruthy();
    expect(dialog.getByText("请先查询预览状态，再执行其他操作")).toBeTruthy();
    expect(dialog.queryByRole("button", { name: "取消外部订阅预览" })).toBeNull();
    expect((dialog.getByRole("button", { name: "确认外部订阅预览" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(dialog.getByRole("button", { name: "查询外部订阅确认结果" })); await flush();
    expect(dialog.getByRole("button", { name: "取消外部订阅预览" })).toBeTruthy(); expect(confirmExternalPreview).not.toHaveBeenCalled();
  });

  it("offers recovery instead of automatically retrying a full pending-preview limit", async () => {
    vi.mocked(createExternalPreview).mockRejectedValue(new ExternalSubscriptionsError(409, "Cancel an existing preview before fetching again"));
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview();
    expect(dialog.getByText(/已有 3 个未确认的预览/)).toBeTruthy();
    expect((dialog.getByRole("button", { name: "抓取外部订阅预览" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input(dialog, "恢复外部订阅预览 ID"), { target: { value: preview.id } }); fireEvent.click(dialog.getByRole("button", { name: "恢复外部订阅预览" })); await flush();
    expect(getExternalPreview).toHaveBeenCalledExactlyOnceWith(source.id, preview.id); expect(createExternalPreview).toHaveBeenCalledTimes(1);
    expect((dialog.getByRole("checkbox", { name: "导入外部节点 New Tokyo" }) as HTMLInputElement).disabled).toBe(false);
  });
});

describe("external source lifecycle: late replies and browser-memory cleanup", () => {
  async function reopenPreview() {
    fireEvent.click(modal("外部订阅来源预览").getByRole("button", { name: "关闭外部订阅预览" })); await flush();
    fireEvent.click(within(screen.getByTestId("external-source-detail")).getByRole("button", { name: "预览外部订阅来源" })); await flush();
    return modal("外部订阅来源预览");
  }

  it("ignores a late fetch after preview close/reopen and never auto-confirms it", async () => {
    const pending = deferred<ExternalPreviewRead>(); vi.mocked(createExternalPreview).mockReturnValue(pending.promise);
    renderUi(<ExternalSubscriptionsPanel users={users} />); await openPreview(); const reopened = await reopenPreview();
    await act(async () => pending.resolve(preview));
    expect(reopened.queryByText(preview.id)).toBeNull(); expect(reopened.getByRole("button", { name: "抓取外部订阅预览" })).toBeTruthy(); expect(confirmExternalPreview).not.toHaveBeenCalled();
  });

  it("ignores a late confirmation and its notification after closing the preview", async () => {
    const pending = deferred<ExternalConfirmationRead>(); vi.mocked(confirmExternalPreview).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const dialog = await openPreview(); acknowledge(dialog);
    fireEvent.click(dialog.getByRole("button", { name: "确认外部订阅预览" })); await flush(); const reopened = await reopenPreview();
    await act(async () => pending.resolve(receipt));
    expect(reopened.queryByText("外部订阅预览已确认")).toBeNull(); expect(updated).not.toHaveBeenCalled(); expect(createExternalPreview).toHaveBeenCalledTimes(1);
  });

  it("ignores a late receipt after closing and clearing the selected preview", async () => {
    const pending = deferred<ExternalPreviewRead>(); vi.mocked(getExternalPreview).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const dialog = await openPreview();
    fireEvent.click(dialog.getByRole("button", { name: "查询外部订阅确认结果" })); await flush(); const reopened = await reopenPreview();
    await act(async () => pending.resolve({ ...preview, receipt }));
    expect(reopened.queryByText(preview.id)).toBeNull(); expect(updated).not.toHaveBeenCalled();
  });

  it("ignores a late cancellation so that it cannot close a newly opened preview", async () => {
    const pending = deferred<ExternalPreviewCancelResponse>(); vi.mocked(cancelExternalPreview).mockReturnValue(pending.promise);
    renderUi(<ExternalSubscriptionsPanel users={users} />); const dialog = await openPreview();
    fireEvent.click(dialog.getByRole("button", { name: "取消外部订阅预览" })); await flush(); await reopenPreview();
    await act(async () => pending.resolve({ cancelled: true, license_required: false }));
    expect(modal("外部订阅来源预览").getByRole("button", { name: "抓取外部订阅预览" })).toBeTruthy();
  });

  it("ignores late source edits after closing the editor and switching to another source", async () => {
    const pending = deferred<ExternalSourceRead>(); vi.mocked(updateExternalSource).mockReturnValue(pending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const panel = await openSource();
    fireEvent.click(panel.getByRole("button", { name: "编辑外部订阅来源" })); const dialog = modal("编辑外部订阅来源");
    fireEvent.click(dialog.getByRole("button", { name: "保存外部订阅来源" })); await flush(); fireEvent.click(dialog.getByRole("button", { name: "取消编辑外部订阅来源" }));
    await openSource(secondSource.name); await act(async () => pending.resolve({ ...source, name: "Late source reply" }));
    expect(within(screen.getByTestId("external-source-detail")).getByText("bob")).toBeTruthy(); expect(screen.queryByText("Late source reply")).toBeNull(); expect(updated).not.toHaveBeenCalled();
  });

  it("ignores a late node edit and deletion after their dialogs close", async () => {
    const nodePending = deferred<ExternalSourceDetail>(), deletePending = deferred<{ deleted: true; license_required: false }>();
    vi.mocked(updateExternalNode).mockReturnValue(nodePending.promise); vi.mocked(deleteExternalSource).mockReturnValue(deletePending.promise);
    const updated = vi.fn(); renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />); const panel = await openSource();
    fireEvent.click(panel.getByRole("button", { name: "编辑外部节点 Local Tokyo" })); const nodeDialog = modal("编辑外部节点");
    fireEvent.click(nodeDialog.getByRole("button", { name: "保存外部节点" })); await flush(); fireEvent.click(nodeDialog.getByRole("button", { name: "取消编辑外部节点" }));
    fireEvent.click(panel.getByRole("button", { name: "删除外部订阅来源" })); const deleteDialog = modal("删除外部订阅来源？");
    fireEvent.click(deleteDialog.getByRole("checkbox", { name: "确认外部订阅来源删除影响" })); fireEvent.click(deleteDialog.getByRole("button", { name: "确认删除外部订阅来源" })); await flush();
    fireEvent.click(deleteDialog.getByRole("button", { name: "保留外部订阅来源" }));
    await act(async () => { nodePending.resolve({ ...detail, source: { ...source, revision: 7 } }); deletePending.resolve({ deleted: true, license_required: false }); });
    expect(screen.getByTestId("external-source-detail")).toBeTruthy(); expect(updated).not.toHaveBeenCalled();
  });

  it("drops secrets and asynchronous writes when an owner starts removal", async () => {
    const pending = deferred<ExternalSourceRead>(); vi.mocked(createExternalSource).mockReturnValue(pending.promise);
    const updated = vi.fn(), view = renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />);
    const dialog = await openCreate(); await fillCreate(dialog); fireEvent.click(dialog.getByRole("button", { name: "保存外部订阅来源" })); await flush();
    view.rerender(<ExternalSubscriptionsPanel users={users.map(user => user.username === "alice" ? { ...user, removal_id: "removing" } : user)} onUpdated={updated} />); await flush();
    await act(async () => pending.resolve(source));
    expect(dialog.getByText(/所选用户不存在或正在移除/)).toBeTruthy(); expect(input(dialog, "外部订阅链接").value).toBe("");
    expect((dialog.getByRole("button", { name: "保存外部订阅来源" }) as HTMLButtonElement).disabled).toBe(true); expect(updated).not.toHaveBeenCalled();
  });

  it("clears secrets on inactive tabs without persisting them or restoring them on reopen", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem"), push = vi.spyOn(history, "pushState"), replace = vi.spyOn(history, "replaceState");
    const originalUrl = window.location.href;
    const view = renderUi(<ExternalSubscriptionsPanel users={users} />); const editor = await openCreate(); await fillCreate(editor);
    await select("外部订阅 User-Agent 设置", "设置自定义 User-Agent", editor); fireEvent.change(input(editor, "外部订阅自定义 User-Agent"), { target: { value: secretAgent } });
    view.rerender(<ExternalSubscriptionsPanel active={false} users={users} />); await flush();
    expect(screen.queryByTestId("external-subscriptions-panel")).toBeNull(); expect(document.body.innerHTML).not.toContain(secretAgent); expect(document.body.innerHTML).not.toContain(secretUrl);
    view.rerender(<ExternalSubscriptionsPanel active users={users} />); await flush();
    const reopened = await openCreate(); expect(input(reopened, "外部订阅链接").value).toBe(""); expect(input(reopened, "外部订阅来源名称").value).toBe("");
    expect(reopened.queryByLabelText("外部订阅自定义 User-Agent")).toBeNull();
    expect(storage).not.toHaveBeenCalled(); expect(push).not.toHaveBeenCalled(); expect(replace).not.toHaveBeenCalled(); expect(window.location.href).toBe(originalUrl);
    expect(createExternalSource).not.toHaveBeenCalled(); expect(createExternalPreview).not.toHaveBeenCalled();
  });

  it("clears previews and ignores an unmounted receipt without persisting selections or IDs", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem"), push = vi.spyOn(history, "pushState"), replace = vi.spyOn(history, "replaceState");
    const originalUrl = window.location.href, updated = vi.fn(), pending = deferred<ExternalPreviewRead>();
    const view = renderUi(<ExternalSubscriptionsPanel users={users} onUpdated={updated} />);
    const dialog = await openPreview(); acknowledge(dialog); vi.mocked(getExternalPreview).mockReturnValue(pending.promise);
    fireEvent.click(dialog.getByRole("button", { name: "查询外部订阅确认结果" })); await flush(); view.unmount();
    await act(async () => pending.resolve({ ...preview, receipt }));
    expect(screen.queryByText(preview.id)).toBeNull(); expect(updated).not.toHaveBeenCalled();
    expect(storage).not.toHaveBeenCalled(); expect(push).not.toHaveBeenCalled(); expect(replace).not.toHaveBeenCalled(); expect(window.location.href).toBe(originalUrl);
    expect(confirmExternalPreview).not.toHaveBeenCalled();
  });
});
