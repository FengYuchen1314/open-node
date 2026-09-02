// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProbeAccessTokenCreateResponse, ProbeSettingsResponse, ProbeTask } from "../../domain/probe";
import { listServers } from "../../services/inventory";
import * as probe from "../../services/probe";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import ProbeAdministrationPanel from "./ProbeAdministrationPanel";

vi.mock("../../services/inventory", () => ({ listServers: vi.fn() }));
vi.mock("../../services/probe", () => ({ clearProbeAccessToken: vi.fn(), createProbeAccessToken: vi.fn(), createProbeTask: vi.fn(), dispatchDueProbeTasks: vi.fn(), getPublicProbeSettings: vi.fn(), listProbeTasks: vi.fn(), updateProbeTask: vi.fn(), updatePublicProbeSettings: vi.fn() }));
const settings: ProbeSettingsResponse = { settings: { enabled: true, title: "Saved probe", refresh_interval_sec: 5, has_access_token: true, require_access_token: true }, license_required: false };
const task: ProbeTask = { id: "task-one", server_id: "edge", kind: "system", enabled: true, interval_sec: 300, domains: [], domain_timeout_ms: 2000, allow_icmp: false, return_route_targets: [], return_route_timeout_seconds: 25, ip_version: 4, command_timeout_ms: 30000, next_run_at: "2026-08-30T00:00:00Z", created_at: "", updated_at: "" };
const callbacks = { onSettings: vi.fn(), onAccessToken: vi.fn(), onRefresh: vi.fn() };

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(probe.getPublicProbeSettings).mockResolvedValue(settings);
  vi.mocked(probe.updatePublicProbeSettings).mockResolvedValue(settings);
  vi.mocked(listServers).mockResolvedValue([{ id: "edge", name: "Edge" }] as Awaited<ReturnType<typeof listServers>>);
  vi.mocked(probe.listProbeTasks).mockResolvedValue({ tasks: [task], license_required: false });
  vi.mocked(probe.createProbeTask).mockResolvedValue({ task, license_required: false });
  vi.mocked(probe.dispatchDueProbeTasks).mockResolvedValue({ checked_at: "", dispatched: [], license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function mount() { return renderUi(<ProbeAdministrationPanel accessToken="" {...callbacks} />); }
function button(name: string | RegExp) { return screen.getByRole("button", { name }) as HTMLButtonElement; }
function draft(label: string, value: string) {
  const input = screen.getByLabelText(label);
  fireEvent.focus(input); fireEvent.change(input, { target: { value } });
  fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
  return input as HTMLInputElement;
}
async function selectKind(label: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "探针类型" }));
  fireEvent.click(screen.getByText(label, { selector: ".ui-option" }));
  await flush();
}

describe("React probe administration", () => {
  it("blocks settings writes until a successful load, including after a failed read", async () => {
    const pending = deferred<ProbeSettingsResponse>();
    vi.mocked(probe.getPublicProbeSettings).mockReturnValueOnce(pending.promise);
    mount();
    expect(button("生成").disabled).toBe(true); expect(button("保存设置").disabled).toBe(true);
    fireEvent.submit(button("保存设置").closest("form")!); fireEvent.click(button("生成")); await flush();
    expect(probe.updatePublicProbeSettings).not.toHaveBeenCalled(); expect(probe.createProbeAccessToken).not.toHaveBeenCalled();
    await act(async () => pending.reject(new Error("Settings unavailable")));
    expect(screen.getByText("暂时无法加载探针设置。")).toBeTruthy(); expect(button("生成").disabled).toBe(true);
    fireEvent.click(button("刷新探针设置")); await flush();
    expect(button("保存设置").disabled).toBe(false);
    expect((screen.getByLabelText("标题") as HTMLInputElement).value).toBe("Saved probe");
  });

  it("requires confirmation to rotate a loaded token and ignores its response after disposal", async () => {
    const pending = deferred<ProbeAccessTokenCreateResponse>();
    vi.mocked(probe.createProbeAccessToken).mockReturnValue(pending.promise);
    const view = mount(); await flush();
    fireEvent.click(button("生成")); await flush(); expect(probe.createProbeAccessToken).not.toHaveBeenCalled();
    const confirm = button(/^确\s*定$/); fireEvent.click(confirm); fireEvent.click(confirm); await flush();
    expect(probe.createProbeAccessToken).toHaveBeenCalledTimes(1);
    view.unmount(); await act(async () => pending.resolve({ ...settings, token: "late-worker-secret" }));
    expect(callbacks.onAccessToken).not.toHaveBeenCalled(); expect(screen.queryByDisplayValue("late-worker-secret")).toBeNull();
  });

  it("rejects invalid refresh drafts after blur and Enter instead of saving a default", async () => {
    mount(); await flush();
    const form = button("保存设置").closest("form")!;
    for (const value of ["-1", "0.4", "", "1e", "61"]) {
      draft("刷新间隔（秒）", value); fireEvent.submit(form); await flush();
      expect(probe.updatePublicProbeSettings).not.toHaveBeenCalled();
      expect(screen.getByText("刷新间隔必须为 1 至 60 秒的整数。")).toBeTruthy();
    }
    draft("刷新间隔（秒）", "10"); fireEvent.submit(form); await flush();
    expect(probe.updatePublicProbeSettings).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ refresh_interval_sec: 10, require_access_token: true }));
  });

  it("does not create or dispatch with unknown inventory/task state and permits an explicit retry", async () => {
    vi.mocked(probe.listProbeTasks).mockRejectedValueOnce(new Error("Task list unavailable"));
    mount(); await flush();
    const header = screen.getByText("定时探针").closest<HTMLElement>(".ui-card-title")!;
    expect(header.style.whiteSpace).toBe("normal");
    expect(header.contains(button("下发到期任务"))).toBe(true);
    expect(header.contains(button("刷新探针任务"))).toBe(true);
    expect(button("下发到期任务").disabled).toBe(true); expect(button("添加任务").disabled).toBe(true);
    fireEvent.click(button("下发到期任务")); fireEvent.submit(button("添加任务").closest("form")!); await flush();
    expect(probe.dispatchDueProbeTasks).not.toHaveBeenCalled(); expect(probe.createProbeTask).not.toHaveBeenCalled();
    fireEvent.click(button("刷新探针任务")); await flush();
    expect(button("下发到期任务").disabled).toBe(false);
    fireEvent.click(button("下发到期任务")); await flush(); expect(probe.dispatchDueProbeTasks).toHaveBeenCalledTimes(1);
  });

  it("validates scheduled intervals without coercion", async () => {
    mount(); await flush();
    const form = button("添加任务").closest("form")!;
    for (const value of ["-1", "0.4", "", "86401"]) {
      draft("执行间隔（秒）", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("执行间隔必须为 60 至 86400 秒的整数。")).toBeTruthy();
    }
    draft("执行间隔（秒）", "600"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ kind: "domain_latency", interval_sec: 600 }));
  });

  it("validates latency timeouts without coercion", async () => {
    mount(); await flush();
    const form = button("添加任务").closest("form")!;
    for (const value of ["-1", "200.4", "", "10001"]) {
      draft("超时时间（毫秒）", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("超时时间必须为 200 至 10000 毫秒的整数。")).toBeTruthy();
    }
    draft("超时时间（毫秒）", "2500"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ server_id: "edge", kind: "domain_latency", interval_sec: 300, domain_timeout_ms: 2500, domains: ["example.com"] }));
  });

  it("validates selected route ports without substituting defaults", async () => {
    mount(); await flush();
    await selectKind("回程路由");
    const form = button("添加任务").closest("form")!;
    fireEvent.change(screen.getByLabelText("电信主机"), { target: { value: "route.example" } });
    for (const value of ["79999", "0.4", ""]) {
      draft("电信端口", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("所有已选回程路由目标的端口都必须为 1 至 65535 的整数。")).toBeTruthy();
    }
  });

  it("validates route timeouts and excludes invalid hidden drafts when switching to system probes", async () => {
    mount(); await flush(); draft("超时时间（毫秒）", "-");
    await selectKind("回程路由");
    const form = button("添加任务").closest("form")!;
    fireEvent.change(screen.getByLabelText("电信主机"), { target: { value: "route.example" } });
    draft("回程探测超时（秒）", "46");
    fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).not.toHaveBeenCalled();
    expect(screen.getByText("回程探测超时时间必须为 10 至 45 秒的整数。")).toBeTruthy();
    await selectKind("系统"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith({ server_id: "edge", kind: "system", interval_sec: 300, domains: [], domain_timeout_ms: 2000, allow_icmp: false, return_route_targets: [], return_route_timeout_seconds: 25, ip_version: 4, command_timeout_ms: 30000 });
  });
});
