// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor";
import PlanNodeAliases from "./PlanNodeAliases";
import LimitOverrideField from "./LimitOverrideField";
import UserLimitEditor from "./UserLimitEditor";
import { newAutoSpeedRule, type AutoSpeedRule } from "../../domain/auto-speed";
import type { UserLimitOverrides, UserLimitsRead } from "../../domain/user-limits";
import type { ManagedNode } from "../../domain/subscriptions";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function flush() { await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); }); }

describe("React subscription field editors", { timeout: 20_000 }, () => {
  it("adds structured automatic rules and validates burst windows", async () => {
    const valid = vi.fn();
    function Harness() { const [value, setValue] = useState<AutoSpeedRule[]>([]); return <AutoSpeedRuleEditor value={value} onChange={setValue} onValid={valid} />; }
    render(<Harness />); fireEvent.click(screen.getByRole("button", { name: "Add automatic rule" }));
    expect(screen.getByLabelText("Trigger Mbps")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Burst" }));
    fireEvent.change(screen.getByLabelText("Hold seconds"), { target: { value: "600" } }); await flush();
    expect(valid).toHaveBeenLastCalledWith(false); expect(screen.getByText("Invalid automatic limit rule")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Window seconds"), { target: { value: "900" } }); await flush();
    expect(valid).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove automatic rule 1" })); expect(screen.getByText("No automatic rules")).toBeTruthy();
  });
  it("reorders rules without changing fields and respects disabled state", () => {
    const first = newAutoSpeedRule(), second = { ...newAutoSpeedRule(), threshold_mbps: 200 }, changed = vi.fn();
    const props = { value: [first, second], onChange: changed };
    const { rerender } = render(<AutoSpeedRuleEditor {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Move rule 2 up" })); expect(changed).toHaveBeenCalledWith([second, first]);
    changed.mockClear(); rerender(<AutoSpeedRuleEditor {...props} disabled />);
    fireEvent.click(screen.getByRole("button", { name: "Add automatic rule" })); expect(changed).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Move rule 2 up" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("keeps invalid automatic-rule values invalid after blur and Enter", async () => {
    const valid = vi.fn();
    function Harness() { const [value, setValue] = useState<AutoSpeedRule[]>([newAutoSpeedRule()]); return <AutoSpeedRuleEditor value={value} onChange={setValue} onValid={valid} />; }
    render(<Harness />); const input = screen.getByLabelText("Hold seconds");
    for (const value of ["0", "-1", "0.4", "86401", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "30" } }); await flush(); expect(valid).toHaveBeenLastCalledWith(true);
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" }); await flush();
      expect(valid).toHaveBeenLastCalledWith(false); expect(screen.getByText("Invalid automatic limit rule")).toBeTruthy();
    }
  });
  it("retains disabled aliases but prunes nodes that leave the plan", async () => {
    const changed = vi.fn(), valid = vi.fn();
    const { rerender } = render(<PlanNodeAliases nodes={[{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }]} value={{ a: "Fast", b: "Backup" }} onChange={changed} enabled={false} onEnabledChange={vi.fn()} onValid={valid} />);
    expect((screen.getByLabelText("Alpha: subscription name") as HTMLInputElement).disabled).toBe(true); expect(changed).not.toHaveBeenCalled();
    rerender(<PlanNodeAliases nodes={[{ id: "a", name: "Alpha" }]} value={{ a: "Fast", b: "Backup" }} onChange={changed} enabled onEnabledChange={vi.fn()} onValid={valid} />); await flush();
    expect(changed).toHaveBeenCalledWith({ a: "Fast" });
  });
  it("distinguishes inherited, unlimited and custom limits", async () => {
    const changed = vi.fn();
    function Harness() { const [value, setValue] = useState<number | null>(null); return <LimitOverrideField label="Speed" unit="Mbps" maximum={1000} minimum={0.01} value={value} onChange={number => { changed(number); setValue(number); }} suggested={25} />; }
    render(<Harness />);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Speed mode" })); fireEvent.click(screen.getByText("Unlimited", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(0);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Speed mode" })); fireEvent.click(screen.getByText("Custom", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(25); expect((screen.getByLabelText("Speed (Mbps)") as HTMLInputElement).value).toBe("25");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Speed mode" })); fireEvent.click(screen.getByText("Inherit", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(null);
  });
  it("removes both per-node override maps without changing account limits", () => {
    const value: UserLimitOverrides = { traffic_limit_gb: 40, speed_limit_mbps: null, device_limit: 0, node_speed_limits: { a: 5 }, node_device_limits: { a: 3 } };
    const current: UserLimitsRead = { traffic_limit_bytes: 1024, speed_limit_mbps: 10, device_limit: 2, speed_source: "plan", device_source: "plan", nodes: [], warnings: ["Offline limits await Agent confirmation"] };
    const changed = vi.fn();
    render(<UserLimitEditor value={value} onChange={changed} nodes={[{ id: "a", name: "Alpha" }] as ManagedNode[]} current={current} />);
    expect(screen.getByRole("region", { name: "Account limits" }).style.paddingInline).toBe("8px");
    fireEvent.click(screen.getByRole("button", { name: "Remove override Alpha" }));
    expect(changed).toHaveBeenCalledWith({ ...value, node_speed_limits: {}, node_device_limits: {} });
    expect(screen.getByText("Offline limits await Agent confirmation")).toBeTruthy();
  });
  it("does not convert invalid custom limits to inherited or unlimited values", async () => {
    const changed = vi.fn();
    function Harness() { const [value, setValue] = useState<number | null>(25); return <LimitOverrideField label="Connections" maximum={100} minimum={1} integer value={value} onChange={next => { changed(next); setValue(next); }} />; }
    render(<Harness />); const input = screen.getByLabelText("Connections");
    for (const value of ["-1", "0.4", "101", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "25" } }); changed.mockClear();
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" }); await flush();
      expect(screen.getByText("Enter a valid positive limit")).toBeTruthy();
      expect(changed.mock.calls.length).toBeGreaterThan(0); expect(changed.mock.calls.every(([next]) => next !== 0 && next !== null && next !== 25)).toBe(true);
      expect(screen.getByLabelText("Connections")).toBeTruthy();
    }
  });
});
