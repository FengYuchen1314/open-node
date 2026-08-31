import { afterEach, describe, expect, it, vi } from "vitest";
import {
  notificationCodes, notificationDefaults, validNotificationChatId, validNotificationTimezone, validNotificationToken,
  type NotificationDeliveryDetail, type NotificationDeliveryRead, type NotificationPreviewRead, type NotificationSettingsRead,
  type NotificationSettingsUpdate,
} from "../domain/notifications";
import { authState } from "./auth";
import {
  getNotificationDelivery, getNotificationRequest, getNotificationSettings, listNotificationDeliveries,
  notificationCodeMessage, notificationErrorMessage, NotificationRequestError, previewNotifications,
  retryNotificationDelivery, testNotification, updateNotificationSettings,
} from "./notifications";

const requestId = "10000000-0000-4000-8000-000000000001";
const retryId = "10000000-0000-4000-8000-000000000002";
const deliveryId = "20000000-0000-4000-8000-000000000001";
const attemptId = "30000000-0000-4000-8000-000000000001";
const planId = "40000000-0000-4000-8000-000000000001";
const token = "12345:abcdefghijklmnopqrstuvwxyz0123456789_PRIVATE";
const date = "2026-08-31T01:00:00Z";
const settings: NotificationSettingsRead = { ...notificationDefaults, revision: 4, has_token: true, chat_id: "-4503599627370495", destination_revision: 2, storage_ready: true, storage_error: null, license_required: false };
const preview: NotificationPreviewRead = { revision: 4, as_of: date, timezone: "Asia/Shanghai", local_time: "09:00", enabled: false, chat_id: settings.chat_id, total: 1,
  candidates: [{ username: "alice", plan_id: planId, plan_name: "Plan", expires_at: date }], sample_message: "用户 alice\n套餐 Plan\n到期时间", is_sample: false, license_required: false };
const delivery: NotificationDeliveryRead = { id: deliveryId, kind: "test", state: "unknown", config_revision: 4, destination_revision: 2, request_id: requestId,
  chat_id: settings.chat_id, username: null, plan_id: null, plan_name: null, expires_at: null, last_attempt_id: attemptId, attempt_count: 1,
  created_at: date, updated_at: date, next_attempt_at: null, retry_available_at: date, manual_retry_allowed: true, code: "telegram_response_timeout", message_id: null, license_required: false };
const detail: NotificationDeliveryDetail = { delivery, attempts: [{ id: attemptId, delivery_id: deliveryId, state: "unknown", attempt_number: 1, config_revision: 4, destination_revision: 2,
  chat_id: settings.chat_id, started_at: date, deadline_at: date, finished_at: date, code: "telegram_response_timeout", message_id: null, retry_after: null, retryable: false, late_receipt_at: null }], license_required: false };
const update: NotificationSettingsUpdate = { ...notificationDefaults, expected_revision: 4, chat_id: settings.chat_id, token_action: "replace", token };
const retry = { expected_revision: 4, request_id: retryId, expected_attempt_id: attemptId, confirm_duplicate_risk: true };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); authState.session = null; });

describe("notification contracts and request boundaries", () => {
  it("uses only the eight administrator endpoints, exact methods, and projected request bodies", async () => {
    const calls: { url: string; init: RequestInit }[] = [], values = [settings, settings, preview, detail, { deliveries: [delivery], license_required: false }, detail, delivery, detail];
    const fetcher: typeof fetch = async (input, init = {}) => { calls.push({ url: String(input), init }); return response(values.shift()); };
    expect(await getNotificationSettings(fetcher)).toEqual(settings);
    expect(await updateNotificationSettings({ ...update, extra_secret: "discard-me" } as NotificationSettingsUpdate, fetcher)).toEqual(settings);
    expect(await previewNotifications({ expected_revision: 4 }, fetcher)).toEqual(preview);
    expect(await testNotification({ expected_revision: 4, request_id: requestId }, fetcher)).toEqual(detail);
    expect(await listNotificationDeliveries(50, fetcher)).toEqual({ deliveries: [delivery], license_required: false });
    expect(await getNotificationDelivery(deliveryId, fetcher)).toEqual(detail);
    expect(await getNotificationRequest(retryId, fetcher)).toEqual(delivery);
    expect(await retryNotificationDelivery(deliveryId, retry, fetcher)).toEqual(detail);
    expect(calls.map(call => call.url)).toEqual([
      "/api/v1/notifications/settings", "/api/v1/notifications/settings", "/api/v1/notifications/preview", "/api/v1/notifications/test",
      "/api/v1/notifications/deliveries?limit=50", `/api/v1/notifications/deliveries/${deliveryId}`, `/api/v1/notifications/requests/${retryId}`, `/api/v1/notifications/deliveries/${deliveryId}/retry`,
    ]);
    expect(calls.map(call => call.init.method ?? "GET")).toEqual(["GET", "PUT", "POST", "POST", "GET", "GET", "GET", "POST"]);
    expect(calls.map(call => call.init.body === undefined ? null : JSON.parse(String(call.init.body)))).toEqual([
      null, update, { expected_revision: 4 }, { expected_revision: 4, request_id: requestId }, null, null, null, retry,
    ]);
    for (const call of calls) {
      expect(call.init.cache).toBe("no-store"); expect(call.init.redirect).toBe("error"); expect(call.init.referrerPolicy).toBe("no-referrer");
      expect(new Headers(call.init.headers).get("Accept")).toBe("application/json");
      expect(call.url).not.toContain(token); expect(call.url).not.toContain(settings.chat_id);
    }
  });

  it("keeps bot secrets write-only and never serializes a supplied token for keep or clear", async () => {
    const bodies: Record<string, unknown>[] = [], fetcher: typeof fetch = async (_input, init) => { bodies.push(JSON.parse(String(init?.body))); return response(settings); };
    await updateNotificationSettings({ ...update, token_action: "keep" }, fetcher);
    await updateNotificationSettings({ ...update, token_action: "clear" }, fetcher);
    expect(bodies.every(body => !Object.hasOwn(body, "token"))).toBe(true);
    expect(JSON.stringify(bodies)).not.toContain(token);
    const injected = { ...settings, token, bot_token: token, token_suffix: "PRIVATE", endpoint: `https://api.telegram.org/bot${token}/sendMessage` };
    expect(await getNotificationSettings(async () => response(injected))).toEqual(settings);
  });

  it("adds administrator cookies and write CSRF without a CSRF field in bodies", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-secret" };
    const values = [settings, settings, preview, detail, { deliveries: [], license_required: false }, detail, delivery, detail], calls: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init: RequestInit) => { calls.push(init); return response(values.shift()); }));
    await getNotificationSettings(); await updateNotificationSettings(update); await previewNotifications({ expected_revision: 4 });
    await testNotification({ expected_revision: 4, request_id: requestId }); await listNotificationDeliveries();
    await getNotificationDelivery(deliveryId); await getNotificationRequest(requestId); await retryNotificationDelivery(deliveryId, retry);
    calls.forEach(init => {
      expect(init.credentials).toBe("include"); expect(init.cache).toBe("no-store");
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(init.body === undefined ? null : "csrf-secret");
      expect(String(init.body)).not.toContain("csrf-secret");
    });
  });

  it("clears an expired administrator session and ignores all raw error fields", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-secret" };
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: token, message: token, body: token }, 401)));
    await expect(getNotificationSettings()).rejects.toThrow("重新登录");
    expect(authState.session.authenticated).toBe(false);
  });

  it("projects previews, delivery records and attempts without retaining unexpected secrets", async () => {
    const extra = { token, token_suffix: "PRIVATE", raw_response: token, detail: token, request_url: `https://api.telegram.org/bot${token}/sendMessage` };
    const savedPreview = await previewNotifications({ expected_revision: 4 }, async () => response({ ...preview, ...extra, candidates: preview.candidates.map(row => ({ ...row, ...extra })) }));
    const savedDetail = await getNotificationDelivery(deliveryId, async () => response({ ...detail, ...extra, delivery: { ...delivery, ...extra }, attempts: detail.attempts.map(row => ({ ...row, ...extra })) }));
    expect(savedPreview).toEqual(preview); expect(savedDetail).toEqual(detail);
    expect(JSON.stringify([savedPreview, savedDetail])).not.toContain(token);
  });

  it("accepts bounded existing display names with control characters without weakening identifier, time or error readers", async () => {
    const username = "alice\noperations\t\u0000\u0085", planName = '<img data-notification-xss src=x onerror="alert(1)">\nStarter\t\u0000';
    const namedPreview = { ...preview, candidates: [{ ...preview.candidates[0], username, plan_name: planName }] };
    const namedDelivery = { ...delivery, kind: "package_expiry", username, plan_id: planId, plan_name: planName, expires_at: date };
    expect((await previewNotifications({ expected_revision: 4 }, async () => response(namedPreview))).candidates[0]).toEqual(namedPreview.candidates[0]);
    expect(await getNotificationRequest(requestId, async () => response(namedDelivery))).toEqual(namedDelivery);
    for (const change of [{ chat_id: settings.chat_id + "\n" }, { updated_at: date + "\t" }, { id: deliveryId + "\n" }, { username: "x".repeat(4097) }, { plan_name: {} }]) {
      await expect(getNotificationRequest(requestId, async () => response({ ...namedDelivery, ...change }))).rejects.toBeInstanceOf(NotificationRequestError);
    }
    const unknown = await getNotificationRequest(requestId, async () => response({ ...namedDelivery, code: "telegram_accepted\n" }));
    expect(unknown.code).toBe("notification_unknown_error");
    const failure = await getNotificationSettings(async () => response({ detail: username + planName }, 500)).catch(error => error);
    expect(notificationErrorMessage(failure)).not.toContain(username); expect(notificationErrorMessage(failure)).not.toContain(planName);
  });

  it("maps an unrecognized result code to a fixed marker, never an arbitrary provider sentence", async () => {
    const result = await getNotificationRequest(requestId, async () => response({ ...delivery, code: `错误 ${token}` }));
    expect(result.code).toBe("notification_unknown_error"); expect(JSON.stringify(result)).not.toContain(token);
    expect(notificationCodeMessage(result.code)).toMatch(/查询当前投递状态/);
  });

  it.each(notificationCodes)("only displays the known code %s, never a modified or appended code", code => {
    const message = notificationCodeMessage(code);
    expect(message).toMatch(/[\u3400-\u9fff]/u);
    for (const unsafe of [code + " ", code + "\n", code + token, `${token}:${code}`, { code }, [code], "__proto__"]) {
      expect(notificationCodeMessage(unsafe)).toBe(notificationCodeMessage(null));
      expect(notificationCodeMessage(unsafe)).not.toContain(token);
    }
  });

  it.each([401, 403, 404, 409, 413, 415, 422, 429, 500, 502, 503])("keeps HTTP %s errors bounded and never trusts detail, body or Error.message", async status => {
    const failure = await testNotification({ expected_revision: 4, request_id: requestId }, async () => response({ detail: token, message: token, code: token }, status)).catch(error => error as unknown);
    expect(failure).toBeInstanceOf(NotificationRequestError);
    expect((failure as NotificationRequestError).outcomeUnknown).toBe(status >= 500);
    expect(notificationErrorMessage(failure)).not.toContain(token);
    (failure as Error).message = `错误 ${token}`;
    expect(notificationErrorMessage(failure)).not.toContain(token);
  });

  it("recognizes fixed top-level API codes but does not turn an HTTP failure into Telegram acceptance", async () => {
    await expect(getNotificationSettings(async () => response({ code: "notification_storage_key_missing", detail: token }, 503))).rejects.toThrow("恢复原通知密钥");
    const noReceipt = await getNotificationRequest(requestId, async () => response({ code: "notification_request_not_found", detail: token }, 404)).catch(error => error);
    expect(notificationErrorMessage(noReceipt)).toContain("不代表消息未发送");
    const failed = await testNotification({ expected_revision: 4, request_id: requestId }, async () => response({ code: "telegram_accepted" }, 500)).catch(error => error);
    expect(notificationErrorMessage(failed)).toContain("无法确认"); expect(notificationErrorMessage(failed)).not.toContain("已接受");
    expect(notificationErrorMessage(new Error(`错误 ${token}`))).not.toContain(token);
  });

  it("treats network loss, HTML, malformed JSON and oversized successful bodies as unknown", async () => {
    const fetchers: typeof fetch[] = [async () => { throw new Error(token); }, async () => new Response(`<html>${token}</html>`), async () => new Response("{broken"), async () => new Response("x".repeat(262145))];
    for (const fetcher of fetchers) {
      const failure = await testNotification({ expected_revision: 4, request_id: requestId }, fetcher).catch(error => error as NotificationRequestError);
      expect(failure).toBeInstanceOf(NotificationRequestError); expect((failure as NotificationRequestError).outcomeUnknown).toBe(true);
      expect(notificationErrorMessage(failure)).not.toContain(token);
    }
  });

  it("bounds a lost response with an abort and never resends the request", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    const fetcher = vi.fn<typeof fetch>((_input, init) => new Promise((_resolve, reject) => {
      signal = init?.signal;
      signal?.addEventListener("abort", () => reject(new Error(token)), { once: true });
    }));
    const result = testNotification({ expected_revision: 4, request_id: requestId }, fetcher).catch(error => error as NotificationRequestError);
    await vi.advanceTimersByTimeAsync(15000);
    const failure = await result;
    expect(failure).toBeInstanceOf(NotificationRequestError);
    expect(notificationErrorMessage(failure)).not.toContain(token);
    expect(signal?.aborted).toBe(true); expect(fetcher).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("rejects wrong identities, unknown states and malformed success contracts", async () => {
    const malformed = [
      { ...detail, delivery: { ...delivery, id: retryId } },
      { ...detail, delivery: { ...delivery, state: "succeeded" } },
      { ...detail, delivery: { ...delivery, chat_id: Number(settings.chat_id) } },
      { ...detail, delivery: { ...delivery, updated_at: "not-a-date" } },
      { ...detail, attempts: [{ ...detail.attempts[0], delivery_id: retryId }] },
      { ...detail, license_required: true },
    ];
    for (const body of malformed) await expect(getNotificationDelivery(deliveryId, async () => response(body))).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(previewNotifications({ expected_revision: 5 }, async () => response(preview))).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(testNotification({ expected_revision: 4, request_id: retryId }, async () => response(detail))).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(getNotificationSettings(async () => response({ ...settings, revision: Number.MAX_SAFE_INTEGER + 1 }))).rejects.toBeInstanceOf(NotificationRequestError);
  });

  it("preserves chat IDs as strings up to the Telegram 52-bit boundary", async () => {
    for (const chatId of ["1", "-1", "4503599627370495", "-4503599627370495"]) {
      expect(validNotificationChatId(chatId)).toBe(true);
      const body = { ...settings, chat_id: chatId };
      expect((await getNotificationSettings(async () => response(body))).chat_id).toBe(chatId);
    }
    for (const chatId of ["0", "-0", "01", "1.5", "1e6", "4503599627370496", "-4503599627370496", " 123", "123\n"]) expect(validNotificationChatId(chatId)).toBe(false);
    expect(validNotificationChatId(123 as unknown as string)).toBe(false);
  });

  it("validates input without clamping, coercion, unintended token writes or network work", async () => {
    const fetcher = vi.fn<typeof fetch>();
    for (const change of [{ advance_days: 0 }, { advance_days: 366 }, { advance_days: 1.5 }, { advance_days: Number.NaN }, { chat_id: "1e5" }, { timezone: "../secret" }, { local_time: "20:00" }, { token: token + "\n" }, { token_action: "clear", enabled: true }]) {
      await expect(updateNotificationSettings({ ...update, ...change } as NotificationSettingsUpdate, fetcher)).rejects.toBeInstanceOf(NotificationRequestError);
    }
    await expect(listNotificationDeliveries(51, fetcher)).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(getNotificationRequest("../../secret", fetcher)).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(getNotificationRequest(requestId + "\n", fetcher)).rejects.toBeInstanceOf(NotificationRequestError);
    await expect(retryNotificationDelivery(deliveryId, { ...retry, confirm_duplicate_risk: false }, fetcher)).rejects.toBeInstanceOf(NotificationRequestError);
    expect(fetcher).not.toHaveBeenCalled();
    expect(validNotificationToken("1:abcdefghijklmnopqrst")).toBe(true);
    expect(validNotificationTimezone("Asia/Shanghai")).toBe(true); expect(validNotificationTimezone("No/Such_Zone")).toBe(false);
  });
});
