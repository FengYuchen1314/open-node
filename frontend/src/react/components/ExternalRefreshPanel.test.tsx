// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ExternalRefreshRead, ExternalSourceRead } from "../../domain/external-subscriptions";
import { ExternalSubscriptionsError } from "../../services/external-subscriptions";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import ExternalRefreshPanel from "./ExternalRefreshPanel";

const refresh: ExternalRefreshRead = {
  enabled: false, interval_minutes: 60, scope: "saved_only", paused: false, running: false,
  next_run_at: null, last_attempt_at: null, last_finished_at: null, last_success_at: null,
  code: "never", consecutive_failures: 0, imported_count: 0, updated_count: 0,
  missing_count: 0, new_available_count: 0,
};
const source: ExternalSourceRead = {
  id: "source-1", owner_username: "alice", name: "我的来源", enabled: true, revision: 2,
  has_custom_user_agent: false, node_count: 0, available_node_count: 0, metadata: {},
  last_synced_at: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", refresh,
};
const api = { updateExternalRefresh: vi.fn(), getExternalSource: vi.fn() };
const onSaved = vi.fn(), onRead = vi.fn();
beforeEach(() => { vi.resetAllMocks(); installDom(); api.updateExternalRefresh.mockResolvedValue({ ...source, revision: 3, refresh: { ...refresh, enabled: true } }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function render(value = source, disabled = false) { return renderUi(<ExternalRefreshPanel source={value} disabled={disabled} api={api} onSaved={onSaved} onRead={onRead} />); }
async function edit() { fireEvent.click(screen.getByRole("button", { name: "配置外部订阅定时刷新" })); await flush(); }
const save = () => screen.getByRole("button", { name: "保存外部订阅定时刷新" }) as HTMLButtonElement;

it("does not fetch on opening and requires explicit consent even for keyboard submit", async () => {
  render(); await edit();
  fireEvent.click(screen.getByRole("switch", { name: "开启外部订阅定时刷新" })); await flush();
  expect(save().disabled).toBe(true);
  fireEvent.submit(save().closest("form")!); await flush();
  expect(api.updateExternalRefresh).not.toHaveBeenCalled(); expect(api.getExternalSource).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "确认外部订阅自动变更" })); await flush();
  fireEvent.click(save()); await flush();
  expect(api.updateExternalRefresh).toHaveBeenCalledExactlyOnceWith(source.id, {
    expected_revision: 2, enabled: true, interval_minutes: 60, scope: "saved_only", accept_changes: true,
  });
  expect(onSaved).toHaveBeenCalledTimes(1);
});

it("all-node scope warns and resets prior consent", async () => {
  render({ ...source, refresh: { ...refresh, enabled: true } }); await edit();
  fireEvent.click(screen.getByRole("checkbox", { name: "确认外部订阅自动变更" }));
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "外部订阅自动同步范围" })); await flush();
  fireEvent.click(screen.getAllByText("更新已保存节点，并自动加入新节点", { selector: ".ant-select-item-option-content" }).at(-1)!); await flush();
  expect(screen.getByText(/新发现的可用节点会自动加入/)).toBeTruthy();
  expect(save().disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "确认外部订阅自动变更" }));
  fireEvent.click(save()); await flush();
  expect(api.updateExternalRefresh.mock.calls[0]![1].scope).toBe("all");
});

it("unknown outcomes require a read and fresh consent without automatic resubmission", async () => {
  api.updateExternalRefresh.mockRejectedValue(new ExternalSubscriptionsError(null, "PRIVATE"));
  api.getExternalSource.mockResolvedValue({ source: { ...source, revision: 8, refresh: { ...refresh, enabled: true } }, nodes: [], license_required: false });
  render({ ...source, refresh: { ...refresh, enabled: true } }); await edit();
  fireEvent.click(screen.getByRole("checkbox", { name: "确认外部订阅自动变更" })); fireEvent.click(save()); await flush();
  expect(save().disabled).toBe(true); expect(document.body.textContent).not.toContain("PRIVATE");
  fireEvent.click(screen.getByRole("button", { name: "重新读取定时刷新设置" })); await flush();
  expect(api.updateExternalRefresh).toHaveBeenCalledTimes(1); expect(onRead).toHaveBeenCalledTimes(1);
  expect(save().disabled).toBe(true);
  api.updateExternalRefresh.mockResolvedValue({ ...source, revision: 9 });
  fireEvent.click(screen.getByRole("checkbox", { name: "确认外部订阅自动变更" })); fireEvent.click(save()); await flush();
  expect(api.updateExternalRefresh.mock.calls[1]![1].expected_revision).toBe(8);
});

it("disabling requires no auto-change consent and emits only one request", async () => {
  const pending = deferred<ExternalSourceRead>(); api.updateExternalRefresh.mockReturnValue(pending.promise);
  render({ ...source, refresh: { ...refresh, enabled: true } }); await edit();
  fireEvent.click(screen.getByRole("switch", { name: "开启外部订阅定时刷新" }));
  fireEvent.click(save()); fireEvent.click(save()); await flush();
  expect(api.updateExternalRefresh).toHaveBeenCalledTimes(1);
  expect(api.updateExternalRefresh.mock.calls[0]![1]).toMatchObject({ enabled: false, accept_changes: false });
  cleanup(); pending.resolve(source); await flush(); expect(onSaved).not.toHaveBeenCalled();
});

it("shows paused failure state and discovery count without exposing exception text", () => {
  render({ ...source, refresh: { ...refresh, enabled: true, paused: true, code: "fetch_failed", consecutive_failures: 3, new_available_count: 2 } }, true);
  expect(screen.getByText("抓取失败，保留上次节点")).toBeTruthy();
  expect(screen.getByText(/连续失败 3 次/)).toBeTruthy();
  expect(screen.getByText(/待手动导入 2/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "配置外部订阅定时刷新" }) as HTMLButtonElement).disabled).toBe(true);
});
