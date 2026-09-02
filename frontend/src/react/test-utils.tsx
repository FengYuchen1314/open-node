import { act, configure, render } from "@testing-library/react";
import { ConfigProvider } from "../ui";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

/** DOM behavior only; responsive visibility is checked by the real VPS browser gates. */
export function installDom() {
  configure({ defaultHidden: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
}

export function renderUi(children: ReactNode) {
  return render(children, { wrapper: ({ children }) => <MemoryRouter><ConfigProvider theme={{ token: { motion: false } }}>{children}</ConfigProvider></MemoryRouter> });
}

export async function flush() {
  await act(async () => { for (let index = 0; index < 12; index += 1) await Promise.resolve(); });
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
