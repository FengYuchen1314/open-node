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
function button(name: string) { return screen.getByRole("button", { name }) as HTMLButtonElement; }
function draft(label: string, value: string) {
  const input = screen.getByLabelText(label);
  fireEvent.focus(input); fireEvent.change(input, { target: { value } });
  fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" });
  return input as HTMLInputElement;
}
async function selectKind(label: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Probe type" }));
  fireEvent.click(screen.getByText(label, { selector: ".ant-select-item-option-content" }));
  await flush();
}

describe("React probe administration", () => {
  it("blocks settings writes until a successful load, including after a failed read", async () => {
    const pending = deferred<ProbeSettingsResponse>();
    vi.mocked(probe.getPublicProbeSettings).mockReturnValueOnce(pending.promise);
    mount();
    expect(button("Generate").disabled).toBe(true); expect(button("Save settings").disabled).toBe(true);
    fireEvent.submit(button("Save settings").closest("form")!); fireEvent.click(button("Generate")); await flush();
    expect(probe.updatePublicProbeSettings).not.toHaveBeenCalled(); expect(probe.createProbeAccessToken).not.toHaveBeenCalled();
    await act(async () => pending.reject(new Error("Settings unavailable")));
    expect(screen.getByText("Settings unavailable")).toBeTruthy(); expect(button("Generate").disabled).toBe(true);
    fireEvent.click(button("Refresh probe settings")); await flush();
    expect(button("Save settings").disabled).toBe(false);
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Saved probe");
  });

  it("requires confirmation to rotate a loaded token and ignores its response after disposal", async () => {
    const pending = deferred<ProbeAccessTokenCreateResponse>();
    vi.mocked(probe.createProbeAccessToken).mockReturnValue(pending.promise);
    const view = mount(); await flush();
    fireEvent.click(button("Generate")); await flush(); expect(probe.createProbeAccessToken).not.toHaveBeenCalled();
    const confirm = button("OK"); fireEvent.click(confirm); fireEvent.click(confirm); await flush();
    expect(probe.createProbeAccessToken).toHaveBeenCalledTimes(1);
    view.unmount(); await act(async () => pending.resolve({ ...settings, token: "late-worker-secret" }));
    expect(callbacks.onAccessToken).not.toHaveBeenCalled(); expect(screen.queryByDisplayValue("late-worker-secret")).toBeNull();
  });

  it("rejects invalid refresh drafts after blur and Enter instead of saving a default", async () => {
    mount(); await flush();
    const form = button("Save settings").closest("form")!;
    for (const value of ["-1", "0.4", "", "1e", "61"]) {
      draft("Refresh seconds", value); fireEvent.submit(form); await flush();
      expect(probe.updatePublicProbeSettings).not.toHaveBeenCalled();
      expect(screen.getByText("Refresh seconds must be a whole number from 1 to 60.")).toBeTruthy();
    }
    draft("Refresh seconds", "10"); fireEvent.submit(form); await flush();
    expect(probe.updatePublicProbeSettings).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ refresh_interval_sec: 10, require_access_token: true }));
  });

  it("does not create or dispatch with unknown inventory/task state and permits an explicit retry", async () => {
    vi.mocked(probe.listProbeTasks).mockRejectedValueOnce(new Error("Task list unavailable"));
    mount(); await flush();
    const header = screen.getByText("Scheduled probes").closest<HTMLElement>(".ant-card-head-title")!;
    expect(header.style.whiteSpace).toBe("normal");
    expect(header.contains(button("Dispatch due"))).toBe(true);
    expect(header.contains(button("Refresh probe tasks"))).toBe(true);
    expect(button("Dispatch due").disabled).toBe(true); expect(button("Add task").disabled).toBe(true);
    fireEvent.click(button("Dispatch due")); fireEvent.submit(button("Add task").closest("form")!); await flush();
    expect(probe.dispatchDueProbeTasks).not.toHaveBeenCalled(); expect(probe.createProbeTask).not.toHaveBeenCalled();
    fireEvent.click(button("Refresh probe tasks")); await flush();
    expect(button("Dispatch due").disabled).toBe(false);
    fireEvent.click(button("Dispatch due")); await flush(); expect(probe.dispatchDueProbeTasks).toHaveBeenCalledTimes(1);
  });

  it("validates scheduled intervals without coercion", async () => {
    mount(); await flush();
    const form = button("Add task").closest("form")!;
    for (const value of ["-1", "0.4", "", "86401"]) {
      draft("Interval seconds", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("Interval seconds must be a whole number from 60 to 86400.")).toBeTruthy();
    }
    draft("Interval seconds", "600"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ kind: "domain_latency", interval_sec: 600 }));
  });

  it("validates latency timeouts without coercion", async () => {
    mount(); await flush();
    const form = button("Add task").closest("form")!;
    for (const value of ["-1", "200.4", "", "10001"]) {
      draft("Timeout ms", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("Timeout ms must be a whole number from 200 to 10000.")).toBeTruthy();
    }
    draft("Timeout ms", "2500"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ server_id: "edge", kind: "domain_latency", interval_sec: 300, domain_timeout_ms: 2500, domains: ["example.com"] }));
  });

  it("validates selected route ports without substituting defaults", async () => {
    mount(); await flush();
    await selectKind("Return route");
    const form = button("Add task").closest("form")!;
    fireEvent.change(screen.getByLabelText("Telecom host"), { target: { value: "route.example" } });
    for (const value of ["79999", "0.4", ""]) {
      draft("Telecom port", value); fireEvent.submit(form); await flush();
      expect(probe.createProbeTask).not.toHaveBeenCalled();
      expect(screen.getByText("Every selected return-route port must be a whole number from 1 to 65535.")).toBeTruthy();
    }
  });

  it("validates route timeouts and excludes invalid hidden drafts when switching to system probes", async () => {
    mount(); await flush(); draft("Timeout ms", "-");
    await selectKind("Return route");
    const form = button("Add task").closest("form")!;
    fireEvent.change(screen.getByLabelText("Telecom host"), { target: { value: "route.example" } });
    draft("Route timeout seconds", "46");
    fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).not.toHaveBeenCalled();
    expect(screen.getByText("Route timeout seconds must be a whole number from 10 to 45.")).toBeTruthy();
    await selectKind("System"); fireEvent.submit(form); await flush();
    expect(probe.createProbeTask).toHaveBeenCalledExactlyOnceWith({ server_id: "edge", kind: "system", interval_sec: 300, domains: [], domain_timeout_ms: 2000, allow_icmp: false, return_route_targets: [], return_route_timeout_seconds: 25, ip_version: 4, command_timeout_ms: 30000 });
  });
});
