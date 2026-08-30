import { describe, expect, it, vi } from "vitest";
import { createObservableState } from "./observable-state";

describe("memory-only observable state", () => {
  it("keeps snapshots stable until a field changes", () => {
    const store = createObservableState({ ready: false, session: null as object | null });
    const initial = store.getSnapshot();
    expect(store.getSnapshot()).toBe(initial);
    const listener = vi.fn();
    store.subscribe(listener);
    store.state.ready = false;
    expect(listener).not.toHaveBeenCalled();
    store.state.ready = true;
    expect(listener).toHaveBeenCalledOnce();
    expect(store.getSnapshot()).toEqual({ ready: true, session: null });
    expect(store.getSnapshot()).not.toBe(initial);
    expect(initial.ready).toBe(false);
  });

  it("notifies all active subscribers and respects unsubscribe", () => {
    const store = createObservableState({ value: 0 });
    const first = vi.fn();
    const second = vi.fn();
    const unsubscribe = store.subscribe(first);
    store.subscribe(second);
    store.state.value = 1;
    unsubscribe();
    store.state.value = 2;
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);
  });
});
