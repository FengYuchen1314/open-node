// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, fireEvent, render as renderAnt, screen } from "@testing-library/react";
import zhCN from "antd/locale/zh_CN";
import { ConfigProvider } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AutoSpeedRuleEditor from "./AutoSpeedRuleEditor";
import PlanNodeAliases from "./PlanNodeAliases";
import LimitOverrideField from "./LimitOverrideField";
import UserLimitEditor from "./UserLimitEditor";
import { newAutoSpeedRule, type AutoSpeedRule } from "../../domain/auto-speed";
import type { UserLimitOverrides, UserLimitsRead } from "../../domain/user-limits";
import type { ManagedNode } from "../../domain/subscriptions";

const render = (ui: Parameters<typeof renderAnt>[0]) => renderAnt(ui, { wrapper: ({ children }) => <ConfigProvider locale={zhCN}>{children}</ConfigProvider> });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
});
afterEach(async () => {
  try {
    cleanup();
    // Ant Design form feedback can still have delayed state updates after unmount.
    // Execute them while jsdom exists instead of discarding pending callbacks.
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    expect(vi.getTimerCount(), "UI timers must finish before jsdom teardown").toBe(0);
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});
async function flush() { await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); }); }

describe("React subscription field editors", { timeout: 20_000 }, () => {
  it("adds structured automatic rules and validates burst windows", async () => {
    const valid = vi.fn();
    function Harness() { const [value, setValue] = useState<AutoSpeedRule[]>([]); return <AutoSpeedRuleEditor value={value} onChange={setValue} onValid={valid} />; }
    render(<Harness />); fireEvent.click(screen.getByRole("button", { name: "添加自动限速规则" }));
    expect(screen.getByLabelText("触发速度（Mbps）")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "突发限速" }));
    fireEvent.change(screen.getByLabelText("持续时间（秒）"), { target: { value: "600" } }); await flush();
    expect(valid).toHaveBeenLastCalledWith(false); expect(screen.getByText("自动限速规则无效")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("统计窗口（秒）"), { target: { value: "900" } }); await flush();
    expect(valid).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "移除自动限速规则 1" })); expect(screen.getByText("暂无自动限速规则")).toBeTruthy();
  });
  it("reorders rules without changing fields and respects disabled state", () => {
    const first = newAutoSpeedRule(), second = { ...newAutoSpeedRule(), threshold_mbps: 200 }, changed = vi.fn();
    const props = { value: [first, second], onChange: changed };
    const { rerender } = render(<AutoSpeedRuleEditor {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "上移规则 2" })); expect(changed).toHaveBeenCalledWith([second, first]);
    changed.mockClear(); rerender(<AutoSpeedRuleEditor {...props} disabled />);
    fireEvent.click(screen.getByRole("button", { name: "添加自动限速规则" })); expect(changed).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "上移规则 2" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("keeps invalid automatic-rule values invalid after blur and Enter", async () => {
    const valid = vi.fn();
    function Harness() { const [value, setValue] = useState<AutoSpeedRule[]>([newAutoSpeedRule()]); return <AutoSpeedRuleEditor value={value} onChange={setValue} onValid={valid} />; }
    render(<Harness />); const input = screen.getByLabelText("持续时间（秒）");
    for (const value of ["0", "-1", "0.4", "86401", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "30" } }); await flush(); expect(valid).toHaveBeenLastCalledWith(true);
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" }); await flush();
      expect(valid).toHaveBeenLastCalledWith(false); expect(screen.getByText("自动限速规则无效")).toBeTruthy();
    }
  });
  it("retains disabled aliases but prunes nodes that leave the plan", async () => {
    const changed = vi.fn(), valid = vi.fn();
    const { rerender } = render(<PlanNodeAliases nodes={[{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }]} value={{ a: "Fast", b: "Backup" }} onChange={changed} enabled={false} onEnabledChange={vi.fn()} onValid={valid} />);
    expect((screen.getByLabelText("Alpha：订阅名称") as HTMLInputElement).disabled).toBe(true); expect(changed).not.toHaveBeenCalled();
    rerender(<PlanNodeAliases nodes={[{ id: "a", name: "Alpha" }]} value={{ a: "Fast", b: "Backup" }} onChange={changed} enabled onEnabledChange={vi.fn()} onValid={valid} />); await flush();
    expect(changed).toHaveBeenCalledWith({ a: "Fast" });
  });
  it("distinguishes inherited, unlimited and custom limits", async () => {
    const changed = vi.fn();
    function Harness() { const [value, setValue] = useState<number | null>(null); return <LimitOverrideField label="速度" unit="Mbps" maximum={1000} minimum={0.01} value={value} onChange={number => { changed(number); setValue(number); }} suggested={25} />; }
    render(<Harness />);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "速度模式" })); fireEvent.click(screen.getByText("不限", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(0);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "速度模式" })); fireEvent.click(screen.getByText("自定义", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(25); expect((screen.getByLabelText("速度（Mbps）") as HTMLInputElement).value).toBe("25");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "速度模式" })); fireEvent.click(screen.getByText("继承", { selector: ".ant-select-item-option-content" })); await flush();
    expect(changed).toHaveBeenLastCalledWith(null);
  });
  it("removes both per-node override maps without changing account limits", () => {
    const value: UserLimitOverrides = { traffic_limit_gb: 40, speed_limit_mbps: null, device_limit: 0, node_speed_limits: { a: 5 }, node_device_limits: { a: 3 } };
    const current: UserLimitsRead = { traffic_limit_bytes: 1024, speed_limit_mbps: 10, device_limit: 2, speed_source: "plan", device_source: "plan", nodes: [], warnings: ["离线节点的限制需等待 Agent 确认"] };
    const changed = vi.fn();
    render(<UserLimitEditor value={value} onChange={changed} nodes={[{ id: "a", name: "Alpha" }] as ManagedNode[]} current={current} />);
    expect(screen.getByRole("region", { name: "账户限制" }).style.paddingInline).toBe("8px");
    fireEvent.click(screen.getByRole("button", { name: "移除 Alpha 的单独限制" }));
    expect(changed).toHaveBeenCalledWith({ ...value, node_speed_limits: {}, node_device_limits: {} });
    expect(screen.getByText("离线节点的限制需等待 Agent 确认")).toBeTruthy();
  });
  it("does not convert invalid custom limits to inherited or unlimited values", async () => {
    const changed = vi.fn();
    function Harness() { const [value, setValue] = useState<number | null>(25); return <LimitOverrideField label="连接数" maximum={100} minimum={1} integer value={value} onChange={next => { changed(next); setValue(next); }} />; }
    render(<Harness />); const input = screen.getByLabelText("连接数");
    for (const value of ["-1", "0.4", "101", "", "-", "1e-999"]) {
      fireEvent.change(input, { target: { value: "25" } }); changed.mockClear();
      fireEvent.change(input, { target: { value } }); fireEvent.blur(input); fireEvent.keyDown(input, { key: "Enter" }); await flush();
      expect(screen.getByText("请输入有效的正数限制值")).toBeTruthy();
      expect(changed.mock.calls.length).toBeGreaterThan(0); expect(changed.mock.calls.every(([next]) => next !== 0 && next !== null && next !== 25)).toBe(true);
      expect(screen.getByLabelText("连接数")).toBeTruthy();
    }
  });
});
