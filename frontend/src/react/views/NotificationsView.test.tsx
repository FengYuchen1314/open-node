// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  notificationDefaults, type NotificationDeliveriesResponse, type NotificationDeliveryDetail, type NotificationDeliveryRead,
  type NotificationPreviewRead, type NotificationSettingsRead,
} from "../../domain/notifications";
import { authState } from "../../services/auth";
import {
  getNotificationDelivery, getNotificationRequest, getNotificationSettings, listNotificationDeliveries,
  NotificationRequestError, previewNotifications, retryNotificationDelivery, testNotification, updateNotificationSettings,
} from "../../services/notifications";
import { deferred, flush, installDom, renderUi as render } from "../test-utils";
import NotificationsView from "./NotificationsView";

vi.mock("../../services/notifications", async importOriginal => ({
  ...await importOriginal<typeof import("../../services/notifications")>(),
  getNotificationDelivery: vi.fn(), getNotificationRequest: vi.fn(), getNotificationSettings: vi.fn(), listNotificationDeliveries: vi.fn(),
  previewNotifications: vi.fn(), retryNotificationDelivery: vi.fn(), testNotification: vi.fn(), updateNotificationSettings: vi.fn(),
}));

const requestId = "10000000-0000-4000-8000-000000000001";
const deliveryId = "20000000-0000-4000-8000-000000000001";
const secondId = "20000000-0000-4000-8000-000000000002";
const attemptId = "30000000-0000-4000-8000-000000000001";
const secondAttemptId = "30000000-0000-4000-8000-000000000002";
const planId = "40000000-0000-4000-8000-000000000001";
const token = "12345:abcdefghijklmnopqrstuvwxyz0123456789_PRIVATE";
const now = "2026-08-31T01:00:00Z";
let saved: NotificationSettingsRead, rows: NotificationDeliveryRead[], preview: NotificationPreviewRead;
let details: Map<string, NotificationDeliveryDetail>;
function delivery(change: Partial<NotificationDeliveryRead> = {}): NotificationDeliveryRead {
  return { id: deliveryId, kind: "test", state: "unknown", config_revision: 4, destination_revision: 2, request_id: requestId,
    chat_id: "-4503599627370495", username: null, plan_id: null, plan_name: null, expires_at: null, last_attempt_id: attemptId, attempt_count: 1,
    created_at: now, updated_at: now, next_attempt_at: null, retry_available_at: now, manual_retry_allowed: true,
    code: "telegram_response_timeout", message_id: null, license_required: false, ...change };
}
function detail(row = delivery()): NotificationDeliveryDetail {
  return { delivery: row, attempts: row.last_attempt_id ? [{ id: row.last_attempt_id, delivery_id: row.id, state: row.state === "queued" || row.state === "cancelled" ? "unknown" : row.state,
    attempt_number: row.attempt_count, config_revision: row.config_revision, destination_revision: row.destination_revision, chat_id: row.chat_id,
    started_at: now, deadline_at: now, finished_at: now, code: row.code, message_id: row.message_id, retry_after: null, retryable: false, late_receipt_at: null }] : [], license_required: false };
}
function button(name: string) { return screen.getByRole("button", { name }) as HTMLButtonElement; }
function dialog(title: string) { return within(screen.getByText(title, { selector: ".ant-modal-title" }).closest('[role="dialog"]')!); }
async function click(name: string) { fireEvent.click(button(name)); await flush(); }
async function selectToken(option: string) {
  fireEvent.mouseDown(screen.getByLabelText("Bot Token 操作")); await flush();
  const node = screen.getAllByText(option).find(item => item.closest(".ant-select-item-option"));
  if (!node) throw new Error("Missing token action");
  fireEvent.click(node); await flush();
}
async function openTest() { await click("发送 Telegram 测试"); return dialog("确认发送测试消息"); }
async function submitTest() {
  const modal = await openTest(); fireEvent.click(modal.getByRole("checkbox", { name: "确认 Telegram 接收目标" }));
  fireEvent.click(modal.getByRole("button", { name: "确认提交通知发送请求" })); await flush();
}
async function advance(milliseconds: number) { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); await flush(); }
function noSending() { expect(testNotification).not.toHaveBeenCalled(); expect(retryNotificationDelivery).not.toHaveBeenCalled(); }

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(now); vi.resetAllMocks(); installDom();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(crypto, "randomUUID").mockReturnValue(requestId);
  authState.ready = true; authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-secret" };
  saved = { ...notificationDefaults, revision: 4, has_token: true, chat_id: "-4503599627370495", destination_revision: 2, storage_ready: true, storage_error: null, license_required: false };
  rows = []; details = new Map();
  preview = { revision: 4, as_of: now, timezone: "Asia/Shanghai", local_time: "09:00", enabled: false, chat_id: saved.chat_id,
    total: 0, candidates: [], sample_message: "示例用户\n示例套餐\n示例到期时间", is_sample: true, license_required: false };
  vi.mocked(getNotificationSettings).mockImplementation(async () => structuredClone(saved));
  vi.mocked(listNotificationDeliveries).mockImplementation(async () => ({ deliveries: structuredClone(rows), license_required: false }));
  vi.mocked(getNotificationDelivery).mockImplementation(async id => structuredClone(details.get(id) ?? detail(delivery({ id }))));
  vi.mocked(getNotificationRequest).mockRejectedValue(new NotificationRequestError(404, "notification_request_not_found"));
  vi.mocked(previewNotifications).mockImplementation(async () => structuredClone(preview));
  vi.mocked(testNotification).mockImplementation(async payload => detail(delivery({ request_id: payload.request_id, state: "queued", code: null, last_attempt_id: null, attempt_count: 0, retry_available_at: null, manual_retry_allowed: false })));
  vi.mocked(retryNotificationDelivery).mockImplementation(async (id, payload) => detail(delivery({ id, request_id: payload.request_id, state: "queued", code: null, manual_retry_allowed: false, retry_available_at: null })));
  vi.mocked(updateNotificationSettings).mockImplementation(async payload => ({ ...saved, revision: saved.revision + 1, enabled: payload.enabled, chat_id: payload.chat_id,
    advance_days: payload.advance_days, timezone: payload.timezone, has_token: payload.token_action === "clear" ? false : true }));
});
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); authState.session = null; authState.ready = false; });

describe("administrator notification workflows", () => {
  it("requires a ready administrator session before loading or displaying configuration", async () => {
    authState.ready = false; authState.session = null;
    render(<NotificationsView />); await flush();
    expect(screen.getByRole("status", { name: "正在读取管理员会话" })).toBeTruthy();
    expect(getNotificationSettings).not.toHaveBeenCalled(); expect(listNotificationDeliveries).not.toHaveBeenCalled();
    act(() => { authState.ready = true; }); await flush();
    expect(screen.getByText("请使用管理员账户登录后管理通知。")).toBeTruthy(); noSending();
  });

  it("only reads on entry, explains privacy and scheduler boundaries, and permits explicit tests while reminders are disabled", async () => {
    render(<NotificationsView />); await flush();
    expect(getNotificationSettings).toHaveBeenCalledOnce(); expect(listNotificationDeliveries).toHaveBeenCalledOnce();
    expect(previewNotifications).not.toHaveBeenCalled(); expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
    expect((screen.getByRole("switch", { name: "启用套餐临期提醒" })).getAttribute("aria-checked")).toBe("false");
    expect((screen.getByLabelText("通知每日扫描时间") as HTMLInputElement).value).toBe("09:00");
    expect((screen.getByLabelText("通知时区") as HTMLInputElement).value).toBe("Asia/Shanghai");
    expect((screen.getByLabelText("提前提醒天数") as HTMLInputElement).value).toBe("7");
    expect(button("发送 Telegram 测试").disabled).toBe(false);
    expect(document.body.textContent).toContain("用户名、套餐名称和到期时间");
    expect(document.body.textContent).toContain("符合条件的提醒可能很快发出");
    expect(document.body.textContent).toContain("每天本地 09:00 起按分钟扫描");
    expect(document.body.textContent).toContain("20 点日报和人工续费审批尚未接入");
    expect(document.body.textContent).toContain("通知不会延长套餐或确认付款");
  });

  it("requires saved token and chat configuration before a test, without echoing any secret or token suffix", async () => {
    saved.has_token = false; saved.chat_id = "";
    render(<NotificationsView />); await flush();
    expect(button("发送 Telegram 测试").disabled).toBe(true);
    expect(screen.queryByLabelText("新的 Telegram Bot Token")).toBeNull();
    expect(document.body.textContent).not.toContain(token); expect(document.body.textContent).not.toContain("PRIVATE"); noSending();
  });

  it("clears a replacement token as its single save begins and never restores or persists it after a failure", async () => {
    const wait = deferred<NotificationSettingsRead>(), store = vi.spyOn(Storage.prototype, "setItem");
    vi.mocked(updateNotificationSettings).mockReturnValueOnce(wait.promise);
    render(<NotificationsView />); await flush(); await selectToken("替换 Bot Token");
    fireEvent.change(screen.getByLabelText("新的 Telegram Bot Token"), { target: { value: token } });
    const save = button("保存通知配置"); fireEvent.click(save); fireEvent.click(save); await flush();
    expect(updateNotificationSettings).toHaveBeenCalledOnce();
    expect(updateNotificationSettings).toHaveBeenCalledWith({ ...notificationDefaults, chat_id: saved.chat_id, expected_revision: 4, token_action: "replace", token });
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe("");
    await act(async () => { wait.reject(new Error(`错误 https://api.telegram.org/bot${token}/sendMessage`)); }); await flush();
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain(token); expect(document.body.textContent).not.toContain("api.telegram.org");
    expect(button("保存通知配置").disabled).toBe(true); expect(store).not.toHaveBeenCalled(); noSending();
    expect(screen.getByText(/请先重新读取已保存配置/)).toBeTruthy();
  });

  it("discards token drafts on action change, backgrounding, explicit reload and unmount", async () => {
    const view = render(<NotificationsView />); await flush(); await selectToken("替换 Bot Token");
    const fill = () => fireEvent.change(screen.getByLabelText("新的 Telegram Bot Token"), { target: { value: token } });
    fill(); await selectToken("保留已保存的 Token"); await selectToken("替换 Bot Token");
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe("");
    fill(); vi.spyOn(document, "hidden", "get").mockReturnValue(true); fireEvent(document, new Event("visibilitychange")); await flush();
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe("");
    vi.spyOn(document, "hidden", "get").mockReturnValue(false); fill(); await click("重新读取已保存通知配置");
    expect(screen.queryByLabelText("新的 Telegram Bot Token")).toBeNull();
    await selectToken("替换 Bot Token"); fill(); view.unmount(); render(<NotificationsView />); await flush(); await selectToken("替换 Bot Token");
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe("");
    expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
  });

  it("requires explicit clearing confirmation and reminders off, then omits the token field", async () => {
    saved.enabled = true; render(<NotificationsView />); await flush(); await selectToken("清除已保存的 Token");
    expect(button("保存通知配置").disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "确认清除已保存的 Bot Token" }));
    expect(button("保存通知配置").disabled).toBe(true);
    fireEvent.click(screen.getByRole("switch", { name: "启用套餐临期提醒" })); await click("保存通知配置");
    expect(updateNotificationSettings).toHaveBeenCalledWith({ ...notificationDefaults, chat_id: saved.chat_id, expected_revision: 4, token_action: "clear" });
    expect(Object.hasOwn(vi.mocked(updateNotificationSettings).mock.calls[0]![0], "token")).toBe(false); noSending();
  });

  it("can disable previously enabled reminders with a missing key while preserving token ciphertext", async () => {
    saved.enabled = true; saved.storage_ready = false; saved.storage_error = "notification_storage_key_missing";
    render(<NotificationsView />); await flush();
    expect(document.body.textContent).toContain("恢复原通知密钥"); expect(button("发送 Telegram 测试").disabled).toBe(true);
    fireEvent.mouseDown(screen.getByLabelText("Bot Token 操作")); await flush();
    for (const text of ["替换 Bot Token", "清除已保存的 Token"]) {
      const node = screen.getAllByText(text).find(item => item.closest(".ant-select-item-option"))!;
      expect(node.closest(".ant-select-item-option")?.getAttribute("aria-disabled")).toBe("true");
    }
    fireEvent.keyDown(screen.getByLabelText("Bot Token 操作"), { key: "Escape", code: "Escape" });
    fireEvent.click(screen.getByRole("switch", { name: "启用套餐临期提醒" })); await click("保存通知配置");
    expect(updateNotificationSettings).toHaveBeenCalledWith({ ...notificationDefaults, chat_id: saved.chat_id, expected_revision: 4, token_action: "keep" });
    expect(Object.hasOwn(vi.mocked(updateNotificationSettings).mock.calls[0]![0], "token")).toBe(false);
    expect(button("发送 Telegram 测试").disabled).toBe(true); noSending();
  });

  it("never clamps invalid days or sends malformed chat and timezone drafts", async () => {
    render(<NotificationsView />); await flush();
    for (const value of ["0", "366", "1.5", ""]) {
      fireEvent.change(screen.getByLabelText("提前提醒天数"), { target: { value } }); await flush();
      expect(button("保存通知配置").disabled).toBe(true);
      if (value) expect((screen.getByLabelText("提前提醒天数") as HTMLInputElement).value).toBe(value);
    }
    fireEvent.change(screen.getByLabelText("提前提醒天数"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: "1e6" } });
    expect(button("保存通知配置").disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: saved.chat_id } });
    fireEvent.change(screen.getByLabelText("通知时区"), { target: { value: "Invalid/Zone" } });
    expect(button("保存通知配置").disabled).toBe(true); expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
  });

  it("previews saved configuration despite dirty drafts and clearly identifies a no-candidate sample", async () => {
    render(<NotificationsView />); await flush();
    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: "1234" } });
    await selectToken("替换 Bot Token"); fireEvent.change(screen.getByLabelText("新的 Telegram Bot Token"), { target: { value: token } });
    await click("预览已保存通知配置");
    expect(previewNotifications).toHaveBeenCalledWith({ expected_revision: 4 });
    const result = within(screen.getByTestId("notification-preview"));
    expect(result.getByText("没有符合条件的用户，以下仅为示例；不会发送。")).toBeTruthy();
    expect(result.getByText(`已保存目标 Chat ID：${saved.chat_id}`)).toBeTruthy();
    expect(result.queryByText("1234")).toBeNull(); expect(screen.getByTestId("notification-preview").querySelector("pre")?.textContent).toBe(preview.sample_message);
    expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
    expect(JSON.stringify(vi.mocked(previewNotifications).mock.calls)).not.toContain(token);
  });

  it("labels eligible candidates as a window, not a pending-send count or new delivery promise", async () => {
    preview.total = 2; preview.is_sample = false; preview.sample_message = "alice 的套餐即将到期";
    preview.candidates = [{ username: "alice", plan_id: planId, plan_name: "Starter", expires_at: now }];
    render(<NotificationsView />); await flush(); await click("预览已保存通知配置");
    expect(screen.getByText("当前到期窗口内有 2 个符合条件的用户；不是待发送数量。")).toBeTruthy();
    expect(screen.getByText("这里只展示前 1 个候选。")).toBeTruthy();
    expect(document.body.textContent).toContain("候选包含已有投递记录的事件");
    expect(document.body.textContent).toContain("实际发送还受投递记录和当前开关限制"); noSending();
  });

  it("renders existing control-containing user/plan names and XSS-like names only as plain text", async () => {
    const username = "alice\noperations\t\u0000\u0085", planName = '<img data-notification-xss src=x onerror="alert(1)">\nStarter\t\u0000';
    rows = [delivery({ kind: "package_expiry", username, plan_id: planId, plan_name: planName, expires_at: now })]; details.set(deliveryId, detail(rows[0]!));
    preview.total = 1; preview.is_sample = false; preview.candidates = [{ username, plan_id: planId, plan_name: planName, expires_at: now }];
    render(<NotificationsView />); await flush(); await click("预览已保存通知配置");
    expect(screen.getByTestId("notification-preview").textContent).toContain(username);
    expect(screen.getByTestId("notification-preview").textContent).toContain(planName);
    await click(`查看通知投递 ${deliveryId}`);
    expect(screen.getByTestId("notification-detail").textContent).toContain(planName);
    expect(document.querySelector("[data-notification-xss]")).toBeNull();
    expect(button("发送 Telegram 测试").disabled).toBe(false); noSending();
  });

  it("requires current saved target confirmation, supports cancellation, and ignores draft targets", async () => {
    render(<NotificationsView />); await flush();
    fireEvent.change(screen.getByLabelText("Telegram Chat ID"), { target: { value: "1234" } });
    let modal = await openTest();
    expect(modal.getByText(saved.chat_id)).toBeTruthy(); expect(modal.queryByText("1234")).toBeNull();
    expect(modal.getByText("未保存的修改不会用于本次发送。")).toBeTruthy();
    expect((modal.getByRole("button", { name: "确认提交通知发送请求" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(modal.getByRole("button", { name: "取消通知发送确认" })); await flush(); noSending();
    modal = await openTest(); fireEvent.click(modal.getByRole("checkbox", { name: "确认 Telegram 接收目标" }));
    fireEvent.click(modal.getByRole("button", { name: "确认提交通知发送请求" })); await flush();
    expect(testNotification).toHaveBeenCalledWith({ expected_revision: 4, request_id: requestId });
    expect(crypto.randomUUID).toHaveBeenCalledOnce(); expect(updateNotificationSettings).not.toHaveBeenCalled();
  });

  it("submits only once on double-click and does not claim delivery or acceptance from a queued receipt", async () => {
    const wait = deferred<NotificationDeliveryDetail>(); vi.mocked(testNotification).mockReturnValueOnce(wait.promise);
    render(<NotificationsView />); await flush(); const modal = await openTest();
    fireEvent.click(modal.getByRole("checkbox", { name: "确认 Telegram 接收目标" }));
    const submit = modal.getByRole("button", { name: "确认提交通知发送请求" }); fireEvent.click(submit); fireEvent.click(submit); await flush();
    expect(testNotification).toHaveBeenCalledOnce(); expect(crypto.randomUUID).toHaveBeenCalledOnce();
    await advance(5000); expect(testNotification).toHaveBeenCalledOnce(); expect(getNotificationRequest).not.toHaveBeenCalled();
    await act(async () => { wait.resolve(detail(delivery({ state: "queued", code: null, last_attempt_id: null, attempt_count: 0, manual_retry_allowed: false }))); }); await flush();
    const result = within(screen.getByTestId("notification-detail")); expect(result.getByText("排队中")).toBeTruthy();
    expect(result.queryByText("Telegram 已接受")).toBeNull(); expect(screen.queryByTestId("notification-pending")).toBeNull();
  });

  it("keeps a lost receipt and its UUID through 404 reconciliation and only replays after explicit confirmation", async () => {
    vi.mocked(testNotification).mockRejectedValueOnce(new NotificationRequestError(null));
    render(<NotificationsView />); await flush(); await submitTest();
    expect(screen.getByTestId("notification-pending")).toBeTruthy(); expect(button("发送 Telegram 测试").disabled).toBe(true);
    await click("查询原通知请求"); expect(getNotificationRequest).toHaveBeenCalledWith(requestId);
    expect(document.body.textContent).toContain("这不代表消息未发送");
    await advance(5000); expect(getNotificationRequest).toHaveBeenCalledTimes(2); expect(testNotification).toHaveBeenCalledOnce();
    await click("使用原通知请求 ID 再次提交"); const modal = dialog("再次提交原通知请求");
    expect(modal.getByText("复用原请求 ID 和原始参数；不会另建请求。")).toBeTruthy();
    fireEvent.click(modal.getByRole("checkbox", { name: "确认 Telegram 接收目标" }));
    fireEvent.click(modal.getByRole("button", { name: "确认提交通知发送请求" })); await flush();
    expect(testNotification).toHaveBeenCalledTimes(2);
    expect(vi.mocked(testNotification).mock.calls[0]![0]).toEqual(vi.mocked(testNotification).mock.calls[1]![0]);
    expect(crypto.randomUUID).toHaveBeenCalledOnce(); expect(screen.queryByTestId("notification-pending")).toBeNull();
  });

  it("resolves an original request by GET without sending and does not steal a different selected detail", async () => {
    rows = [delivery({ id: secondId })]; details.set(secondId, detail(rows[0]!));
    vi.mocked(testNotification).mockRejectedValueOnce(new NotificationRequestError(null));
    render(<NotificationsView />); await flush(); await submitTest(); await click(`查看通知投递 ${secondId}`);
    vi.mocked(getNotificationRequest).mockResolvedValueOnce(delivery({ state: "accepted", code: "telegram_accepted", message_id: 42, manual_retry_allowed: false }));
    await click("查询原通知请求");
    expect(screen.queryByTestId("notification-pending")).toBeNull(); expect(testNotification).toHaveBeenCalledOnce(); expect(retryNotificationDelivery).not.toHaveBeenCalled();
    expect(within(screen.getByTestId("notification-detail")).getByText(secondId)).toBeTruthy();
    expect(within(screen.getByTestId("notification-detail")).queryByText(deliveryId)).toBeNull();
    expect(screen.getByText("已找到原请求的投递记录；没有另建请求或重新发送。")).toBeTruthy();
  });

  it("does not replay an uncertain request against a changed settings revision or silently allocate a new UUID", async () => {
    vi.mocked(testNotification).mockRejectedValueOnce(new NotificationRequestError(null));
    render(<NotificationsView />); await flush(); await submitTest(); saved.revision = 5; saved.chat_id = "1234";
    await click("使用原通知请求 ID 再次提交");
    expect(screen.queryByText("再次提交原通知请求", { selector: ".ant-modal-title" })).toBeNull();
    expect(document.body.textContent).toContain("旧请求只能先查询对账");
    expect(button("使用原通知请求 ID 再次提交").disabled).toBe(true);
    expect(testNotification).toHaveBeenCalledOnce(); expect(crypto.randomUUID).toHaveBeenCalledOnce();
  });

  it("requires an explicit risk acknowledgement to stop tracking and never cancels or resends implicitly", async () => {
    vi.mocked(testNotification).mockRejectedValueOnce(new NotificationRequestError(409, "notification_revision_conflict"));
    render(<NotificationsView />); await flush(); await submitTest();
    expect(button("停止跟踪通知请求").disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "确认停止跟踪通知请求的风险" })); await click("停止跟踪通知请求");
    expect(screen.queryByTestId("notification-pending")).toBeNull();
    expect(document.body.textContent).toContain("原请求仍可能完成");
    expect(testNotification).toHaveBeenCalledOnce(); expect(retryNotificationDelivery).not.toHaveBeenCalled(); expect(crypto.randomUUID).toHaveBeenCalledOnce();
  });

  it("requires both server retry permission and elapsed deadline, and never retries queued, sending, accepted or cancelled work", async () => {
    const deadline = "2026-08-31T01:00:05Z";
    rows = [delivery({ retry_available_at: deadline }), delivery({ id: secondId, manual_retry_allowed: false }),
      ...(["queued", "sending", "accepted", "cancelled"] as const).map((state, index) => delivery({ id: `20000000-0000-4000-8000-00000000000${index + 3}`, state }))];
    render(<NotificationsView />); await flush();
    rows.forEach(row => expect(button(`人工重试通知 ${row.id}`).disabled).toBe(true));
    await advance(5000); expect(button(`人工重试通知 ${deliveryId}`).disabled).toBe(false);
    rows.slice(1).forEach(row => expect(button(`人工重试通知 ${row.id}`).disabled).toBe(true)); noSending();
  });

  it("binds manual retry to a fresh attempt and current saved target with two separate acknowledgements", async () => {
    rows = [delivery({ chat_id: "-1001" })]; const current = delivery({ chat_id: "-1001", last_attempt_id: secondAttemptId, attempt_count: 2 });
    details.set(deliveryId, detail(current)); render(<NotificationsView />); await flush(); await click(`人工重试通知 ${deliveryId}`);
    const modal = dialog("确认人工重试通知");
    expect(modal.getByText(saved.chat_id)).toBeTruthy(); expect(modal.getByText("-1001")).toBeTruthy();
    const submit = modal.getByRole("button", { name: "确认提交通知发送请求" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true); fireEvent.click(modal.getByRole("checkbox", { name: "确认 Telegram 接收目标" })); expect(submit.disabled).toBe(true);
    fireEvent.click(modal.getByRole("checkbox", { name: "确认通知可能重复发送" })); fireEvent.click(submit); await flush();
    expect(retryNotificationDelivery).toHaveBeenCalledWith(deliveryId, { expected_revision: 4, request_id: requestId, expected_attempt_id: secondAttemptId, confirm_duplicate_risk: true });
    expect(testNotification).not.toHaveBeenCalled();
  });

  it("rechecks retry eligibility immediately before confirmation and refuses an already accepted attempt", async () => {
    rows = [delivery()]; details.set(deliveryId, detail(delivery({ state: "accepted", code: "telegram_accepted", message_id: 42, manual_retry_allowed: false })));
    render(<NotificationsView />); await flush(); await click(`人工重试通知 ${deliveryId}`);
    expect(screen.queryByText("确认人工重试通知", { selector: ".ant-modal-title" })).toBeNull();
    expect(document.body.textContent).toContain("当前投递还不允许人工重试"); expect(button(`人工重试通知 ${deliveryId}`).disabled).toBe(true); noSending();
  });

  it("keeps Chinese state and cancellation explanations distinct from failure or read receipts", async () => {
    rows = (["queued", "sending", "accepted", "failed", "unknown", "cancelled"] as const).map((state, index) => delivery({
      id: `20000000-0000-4000-8000-00000000000${index + 1}`, state,
      code: state === "accepted" ? "telegram_accepted" : state === "cancelled" ? "notification_already_accepted" : state === "failed" ? "notification_claim_expired" : null,
      message_id: state === "accepted" ? 42 : null,
    }));
    render(<NotificationsView />); await flush();
    for (const [index, label] of ["排队中", "发送中", "Telegram 已接受", "失败", "结果未知", "已取消"].entries()) {
      const row = within(button(`查看通知投递 ${rows[index]!.id}`).closest("tr")!); expect(row.getByText(label)).toBeTruthy();
    }
    const cancelled = within(button(`查看通知投递 ${rows[5]!.id}`).closest("tr")!);
    expect(cancelled.getByText("已存在 Telegram 接受回执，尚未发送的重复队列已取消。")).toBeTruthy();
    expect(cancelled.queryByText("失败")).toBeNull();
    expect(document.body.textContent).toContain("Telegram 已接受消息，不代表收件人已读");
    expect(document.body.textContent).toContain("是否重试以当前投递状态为准"); noSending();
  });

  it("ignores an old detail completion after another delivery was selected or the detail was closed", async () => {
    const wait = deferred<NotificationDeliveryDetail>(); rows = [delivery(), delivery({ id: secondId })];
    vi.mocked(getNotificationDelivery).mockReturnValueOnce(wait.promise);
    render(<NotificationsView />); await flush(); await click(`查看通知投递 ${deliveryId}`); await click(`查看通知投递 ${secondId}`);
    await act(async () => { wait.resolve(detail(delivery())); }); await flush();
    const shown = within(screen.getByTestId("notification-detail")); expect(shown.getByText(secondId)).toBeTruthy(); expect(shown.queryByText(deliveryId)).toBeNull();
    const closed = deferred<NotificationDeliveryDetail>(); vi.mocked(getNotificationDelivery).mockReturnValueOnce(closed.promise);
    await click(`查看通知投递 ${deliveryId}`); await click("关闭通知投递详情");
    await act(async () => { closed.resolve(detail(delivery())); }); await flush(); expect(screen.queryByTestId("notification-detail")).toBeNull();
  });

  it("ignores an older list read that completes after a confirmed submission", async () => {
    const oldList = deferred<NotificationDeliveriesResponse>(); vi.mocked(listNotificationDeliveries).mockReturnValueOnce(oldList.promise);
    render(<NotificationsView />); await flush(); await submitTest();
    expect(button(`查看通知投递 ${deliveryId}`)).toBeTruthy();
    await act(async () => { oldList.resolve({ deliveries: [], license_required: false }); }); await flush();
    expect(button(`查看通知投递 ${deliveryId}`)).toBeTruthy(); expect(testNotification).toHaveBeenCalledOnce();
  });

  it("polls read-only while visible, clears sensitive input when hidden, and stops on unmount", async () => {
    const view = render(<NotificationsView />); await flush();
    await advance(5000); expect(listNotificationDeliveries).toHaveBeenCalledTimes(2); noSending();
    await selectToken("替换 Bot Token"); fireEvent.change(screen.getByLabelText("新的 Telegram Bot Token"), { target: { value: token } });
    vi.spyOn(document, "hidden", "get").mockReturnValue(true); fireEvent(document, new Event("visibilitychange")); await flush(); await advance(5000);
    expect((screen.getByLabelText("新的 Telegram Bot Token") as HTMLInputElement).value).toBe(""); expect(listNotificationDeliveries).toHaveBeenCalledTimes(2);
    view.unmount(); vi.spyOn(document, "hidden", "get").mockReturnValue(false); await advance(10000);
    expect(listNotificationDeliveries).toHaveBeenCalledTimes(2); expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
  });

  it("drops stale work and token drafts when the authenticated administrator changes", async () => {
    const wait = deferred<NotificationSettingsRead>();
    render(<NotificationsView />); await flush(); await selectToken("替换 Bot Token");
    fireEvent.change(screen.getByLabelText("新的 Telegram Bot Token"), { target: { value: token } });
    vi.mocked(getNotificationSettings).mockReturnValueOnce(wait.promise); await click("重新读取已保存通知配置");
    act(() => { authState.session = { configured: true, authenticated: true, username: "second-admin", csrf_token: "other-csrf" }; }); await flush();
    await act(async () => { wait.resolve({ ...saved, chat_id: "1234" }); }); await flush();
    expect((screen.getByLabelText("Telegram Chat ID") as HTMLInputElement).value).toBe(saved.chat_id);
    expect(screen.queryByLabelText("新的 Telegram Bot Token")).toBeNull(); expect(updateNotificationSettings).not.toHaveBeenCalled(); noSending();
  });

  it("recovers from StrictMode effect replay without a permanently busy settings form", async () => {
    render(<StrictMode><NotificationsView /></StrictMode>); await flush();
    expect((screen.getByLabelText("Telegram Chat ID") as HTMLInputElement).value).toBe(saved.chat_id);
    fireEvent.change(screen.getByLabelText("提前提醒天数"), { target: { value: "8" } }); await click("保存通知配置");
    expect(updateNotificationSettings).toHaveBeenCalledOnce(); noSending();
  });
});
