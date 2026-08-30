// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { useAsyncScope } from "./useAsyncScope";

describe("React asynchronous view scopes", () => {
  it("invalidates older operations and leaves passive captures in the current generation", () => {
    const view = renderHook(useAsyncScope);
    const first = view.result.current.begin();
    const captured = view.result.current.capture();
    expect(captured).toBe(first);
    expect(view.result.current.isCurrent(first)).toBe(true);
    view.rerender();
    expect(view.result.current.isCurrent(first)).toBe(true);
    const next = view.result.current.begin();
    expect(view.result.current.isCurrent(first)).toBe(false);
    expect(view.result.current.isCurrent(next)).toBe(true);
    view.result.current.invalidate();
    expect(view.result.current.isCurrent(next)).toBe(false);
    view.unmount();
  });
  it("survives StrictMode effect replay but refuses every callback after disposal", () => {
    const view = renderHook(useAsyncScope, { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> });
    const scope = view.result.current;
    const current = scope.begin(); expect(scope.isCurrent(current)).toBe(true);
    view.unmount(); expect(scope.isCurrent(current)).toBe(false);
    expect(scope.isCurrent(scope.begin())).toBe(false);
  });
});
