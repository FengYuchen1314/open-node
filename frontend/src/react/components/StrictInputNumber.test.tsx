// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import StrictInputNumber from "./StrictInputNumber";

afterEach(cleanup);

function mount(allowEmpty = false) {
  const changed = vi.fn();
  function Harness() {
    const [value, setValue] = useState<number | null>(25);
    return <><StrictInputNumber aria-label="Amount" aria-valuemin={1} aria-valuemax={100}
      value={value} allowEmpty={allowEmpty} onChange={next => { changed(next); setValue(next); }} />
      <button onClick={() => setValue(25)}>Reset</button></>;
  }
  render(<Harness />);
  return { changed, input: screen.getByRole("spinbutton", { name: "Amount" }) as HTMLInputElement };
}

function draft(input: HTMLInputElement, value: string) {
  fireEvent.focus(input);
  fireEvent.keyDown(input, { key: value.at(-1) ?? "Backspace" });
  fireEvent.change(input, { target: { value } });
  fireEvent.keyUp(input, { key: value.at(-1) ?? "Backspace" });
}

function leaveAndEnter(input: HTMLInputElement) {
  fireEvent.blur(input);
  fireEvent.focus(input);
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.keyUp(input, { key: "Enter" });
}

describe("Strict numeric input", () => {
  it("preserves the negative sign during sequential typing without reusing the old value", () => {
    const { input, changed } = mount();
    draft(input, ""); draft(input, "-");
    expect(input.value).toBe("-"); expect(changed).toHaveBeenLastCalledWith(Number.NaN);
    draft(input, input.value + "1"); expect(input.value).toBe("-1");
    leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(-1); expect(input.value).toBe("-1");
    expect(changed.mock.calls.every(([value]) => value !== 0 && value !== 25 && value !== null)).toBe(true);
  });

  it.each([[-1, "-1"], [0.4, "0.4"], [101, "101"]])("leaves %s for business validation after blur and Enter", (value, text) => {
    const { input, changed } = mount(); draft(input, text); leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(value); expect(input.value).toBe(text);
    expect(input.getAttribute("aria-valuemin")).toBe("1"); expect(input.getAttribute("aria-valuemax")).toBe("100");
  });

  it.each(["abc", "$0", "0x10", "Infinity", "1e999", "1e-999", "-", ".", "1e", " "])("never restores a valid value for malformed draft %j", text => {
    const { input, changed } = mount(); draft(input, text);
    expect(input.value).toBe(text); expect(changed).toHaveBeenLastCalledWith(Number.NaN);
    leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(Number.NaN);
    expect(changed.mock.calls.every(([value]) => Number.isNaN(value))).toBe(true);
  });

  it("keeps required empty input invalid but permits an explicit zero", () => {
    const { input, changed } = mount(); draft(input, ""); leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(Number.NaN); expect(input.value).toBe("");
    draft(input, "0"); leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(0); expect(input.value).toBe("0");
  });

  it("allows null only for an explicitly empty optional input", () => {
    const { input, changed } = mount(true); draft(input, ""); leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(null);
    changed.mockClear(); draft(input, "-"); leaveAndEnter(input);
    expect(changed).toHaveBeenLastCalledWith(Number.NaN);
    expect(changed.mock.calls.every(([value]) => Number.isNaN(value))).toBe(true);
    draft(input, "25"); draft(input, ""); expect(changed).toHaveBeenLastCalledWith(null);
  });

  it("supports progressive decimals and exponents without erasing unfinished input", () => {
    const { input, changed } = mount();
    draft(input, ""); draft(input, "."); expect(input.value).toBe(".");
    draft(input, input.value + "4"); leaveAndEnter(input); expect(changed).toHaveBeenLastCalledWith(0.4);
    draft(input, "1"); draft(input, "1e"); expect(input.value).toBe("1e");
    expect(changed).toHaveBeenLastCalledWith(Number.NaN);
    draft(input, input.value + "2"); leaveAndEnter(input); expect(changed).toHaveBeenLastCalledWith(100);
  });

  it("accepts an explicit controlled reset after an invalid draft", () => {
    const { input } = mount(); draft(input, "-");
    fireEvent.click(screen.getByRole("button", { name: "Reset" })); expect(input.value).toBe("25");
  });
});
