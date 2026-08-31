// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "../../routes";
import { subscriberState } from "../../services/subscriber-auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AccountExternalSourcesView from "./AccountExternalSourcesView";

const prefix = "/api/v1/account/external-subscriptions";
const source = { id: "source-1", owner_username: "alice", name: "我的来源", enabled: true, revision: 1,
  has_custom_user_agent: false, node_count: 0, available_node_count: 0, metadata: {},
  last_synced_at: null, created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z" };
const preview = { id: "preview-1", source_id: source.id, source_revision: 1, metadata: {},
  created_at: source.created_at, expires_at: "2099-08-31T00:00:00Z", receipt: null, license_required: false,
  nodes: [{ node_id: "node-1", upstream_name: "URI 节点", name: "URI 节点", protocol: "vless", change: "new", existing: false, selectable: true, reason: null, changed_fields: [] }] };
const secret = "https://provider.example/private?token=NO-ECHO";
const respond = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
let listed = true;
const session = (username = "alice") => ({ authenticated: true, username, csrf_token: `csrf-${username}`, requires_2fa: false, challenge: null });
beforeEach(() => {
  vi.useFakeTimers(); installDom(); listed = true;
  subscriberState.ready = true; subscriberState.session = session();
  fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
    const url = String(input), method = init.method ?? "GET";
    if (url === prefix && method === "GET") return respond({ sources: listed ? [source] : [], license_required: false });
    if (url === prefix && method === "POST") return respond(source);
    if (url === `${prefix}/${source.id}`) return respond({ source, nodes: [], license_required: false });
    if (url === `${prefix}/${source.id}/previews`) return respond(preview);
    if (url === `${prefix}/${source.id}/previews/${preview.id}/confirm`) return respond({ source_id: source.id, preview_id: preview.id, revision: 2, imported_count: 1, updated_count: 0, missing_count: 0, applied_at: source.created_at });
    throw new Error("Unexpected private request");
  });
  vi.stubGlobal("fetch", fetcher); localStorage.clear(); sessionStorage.clear();
});
afterEach(() => { cleanup(); subscriberState.session = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });
async function click(name: string) { fireEvent.click(screen.getByRole("button", { name })); await flush(); }
function dialog(title: string) { return within(screen.getByText(title, { selector: ".ant-modal-title" }).closest(".ant-modal") as HTMLElement); }
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }

describe("account external source workspace", () => {
  it("requires subscriber login and registers only the explicit subscriber route", async () => {
    subscriberState.session = null; renderUi(<AccountExternalSourcesView />); await flush();
    expect(screen.getByText("请先登录用户中心，再管理自己的外部订阅。")).toBeTruthy(); expect(fetcher).not.toHaveBeenCalled();
    expect(routes.find(route => route.path === "/account/external-subscriptions")?.meta?.subscriber).toBe(true);
    expect(screen.getByRole("link", { name: "返回用户中心" }).getAttribute("href")).toBe("/account");
  });

  it("loads only owned status, hides owner controls and does not automatically fetch upstream", async () => {
    renderUi(<AccountExternalSourcesView />); await flush();
    expect(screen.queryByLabelText("按所属用户筛选外部订阅来源")).toBeNull();
    expect(screen.getByText(/仅管理当前账户自己的来源/)).toBeTruthy();
    expect(screen.getByText(/URI 列表及 Base64 编码内容/)).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0]![0]).toBe(prefix);
    await click("添加外部订阅来源");
    expect(screen.queryByLabelText("外部订阅所属用户")).toBeNull();
    expect(screen.getByText(/所属用户（由登录会话确定）/)).toBeTruthy();
  });

  it("clears the secret at submission and creates once without sending an owner", async () => {
    const pending = deferred<Response>(); listed = false;
    const regular = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((url, init) => init?.method === "POST" ? pending.promise : regular(url, init));
    renderUi(<AccountExternalSourcesView />); await flush(); await click("添加外部订阅来源");
    fill("外部订阅来源名称", "我的来源"); fill("外部订阅链接", secret);
    const form = screen.getByLabelText("外部订阅来源名称").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect((screen.getByLabelText("外部订阅链接") as HTMLInputElement).value).toBe("");
    const posts = fetcher.mock.calls.filter(([, init]) => init?.method === "POST"); expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0]![1]?.body))).toEqual({ name: "我的来源", url: secret, user_agent: "", enabled: true });
    expect(document.body.textContent).not.toContain(secret); expect(localStorage.length + sessionStorage.length).toBe(0);
    await act(async () => pending.resolve(respond(source))); await flush();
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/previews"))).toBe(false);
  });

  it("requires explicit fetch, node selection and change acknowledgement before confirmation", async () => {
    renderUi(<AccountExternalSourcesView />); await flush(); await click("查看外部订阅来源 我的来源");
    await click("预览外部订阅来源");
    expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    await click("抓取外部订阅预览");
    const modal = dialog("外部订阅来源预览");
    expect((modal.getByRole("button", { name: "确认外部订阅预览" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(modal.getByRole("checkbox", { name: "导入外部节点 URI 节点" }));
    fireEvent.click(modal.getByRole("checkbox", { name: "接受外部订阅预览变更" })); await flush();
    await click("确认外部订阅预览");
    const calls = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/confirm")); expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0]![1]?.body))).toEqual({ expected_revision: 1, selected_node_ids: ["node-1"], accept_changes: true });
    expect(screen.getByText("外部订阅预览已确认")).toBeTruthy();
  });

  it("destroys secrets and discards late old-account replies when the session changes", async () => {
    const pending = deferred<Response>(); listed = false;
    const regular = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((url, init) => init?.method === "POST" ? pending.promise : regular(url, init));
    renderUi(<AccountExternalSourcesView />); await flush(); await click("添加外部订阅来源");
    fill("外部订阅来源名称", "我的来源"); fill("外部订阅链接", secret); await click("保存外部订阅来源");
    await act(async () => { subscriberState.session = session("bob"); }); await flush();
    await act(async () => pending.resolve(respond(source))); await flush();
    expect(screen.queryByLabelText("外部订阅链接")).toBeNull(); expect(screen.queryByText("我的来源")).toBeNull();
    expect(screen.getByText(/仅管理当前账户自己的来源：bob/)).toBeTruthy(); expect(document.body.textContent).not.toContain(secret);
  });
});
