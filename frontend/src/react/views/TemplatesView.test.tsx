// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TemplatesView from "./TemplatesView";

vi.mock("../components/TemplatesWorkspace", () => ({
  default: () => <div data-testid="template-library">Clash / Mihomo YAML 模板库</div>,
}));
vi.mock("./SubscriptionCustomizationsView", () => ({
  default: () => <div data-testid="subscription-customizations">订阅自定义工作区</div>,
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

function renderView(entry = "/templates") {
  return render(<MemoryRouter initialEntries={[entry]}><TemplatesView /><LocationProbe /></MemoryRouter>);
}

describe("TemplatesView", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("uses the template library as the canonical default and only renders that workspace", () => {
    renderView();
    expect(screen.getByRole("heading", { name: "模板管理" })).toBeTruthy();
    expect(screen.getByText(/Clash \/ Mihomo YAML 与 Surge 模板/)).toBeTruthy();
    expect(screen.getByTestId("template-library")).toBeTruthy();
    expect(screen.queryByTestId("subscription-customizations")).toBeNull();
  });

  it("opens subscription customizations from the query tab without mounting the library", () => {
    renderView("/templates?tab=customizations&source=sidebar");
    expect(screen.getByTestId("subscription-customizations")).toBeTruthy();
    expect(screen.queryByTestId("template-library")).toBeNull();
    expect(screen.getByTestId("location").textContent).toBe("/templates?tab=customizations&source=sidebar");
  });

  it("updates only the tab query while preserving unrelated query parameters", () => {
    renderView("/templates?owner=alice");
    fireEvent.click(screen.getByRole("tab", { name: "订阅自定义" }));
    expect(screen.getByTestId("subscription-customizations")).toBeTruthy();
    expect(screen.getByTestId("location").textContent).toContain("owner=alice");
    expect(screen.getByTestId("location").textContent).toContain("tab=customizations");
    fireEvent.click(screen.getByRole("tab", { name: "模板库" }));
    expect(screen.getByTestId("template-library")).toBeTruthy();
    expect(screen.getByTestId("location").textContent).toBe("/templates?owner=alice");
  });

  it("fails safely to the library for an unknown tab", () => {
    renderView("/templates?tab=unknown");
    expect(screen.getByTestId("template-library")).toBeTruthy();
    expect(screen.queryByTestId("subscription-customizations")).toBeNull();
  });
});
