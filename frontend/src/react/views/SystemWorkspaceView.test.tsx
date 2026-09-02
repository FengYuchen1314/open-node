// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import { flush, installDom } from "../test-utils";
import SystemWorkspaceView from "./SystemWorkspaceView";

vi.mock("./SystemSettingsView", () => ({ default: () => <div>General child</div> }));
vi.mock("./AccessView", () => ({ default: () => <div>Access child</div> }));
vi.mock("./NotificationsView", () => ({ default: () => <div>Notifications child</div> }));
vi.mock("./BackupsView", () => ({ default: () => <div>Backups child</div> }));
vi.mock("./ChangesView", () => ({ default: () => <div>Changes child</div> }));
vi.mock("./AdminRenewalsView", () => ({ default: () => <div>Renewals child</div> }));
vi.mock("./ProbeView", () => ({ default: () => <div>Probe child</div> }));
vi.mock("./SubscriptionsView", () => ({ default: ({ workspace }: { workspace: string }) => <div>{workspace === "migration" ? "Migration child" : "Subscriptions child"}</div> }));

function LocationProbe() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
function mount(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><ConfigProvider theme={{ token: { motion: false } }}><SystemWorkspaceView /><LocationProbe /></ConfigProvider></MemoryRouter>);
}

beforeEach(installDom);
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("system settings aggregate workspace", () => {
  it.each([
    ["access", "Access child"], ["notifications", "Notifications child"], ["backups", "Backups child"],
    ["changes", "Changes child"], ["renewals", "Renewals child"], ["probe", "Probe child"], ["migration", "Migration child"],
  ])("renders the %s legacy deep-link destination", async (tab, child) => {
    mount(`/system-settings?tab=${tab}`); await flush();
    expect(screen.getByText(child)).toBeTruthy();
  });

  it("uses general settings by default and writes tab selection to the URL", async () => {
    mount("/system-settings?source=menu"); await flush();
    expect(screen.getByText("General child")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "备份与恢复" })); await flush();
    expect(screen.getByText("Backups child")).toBeTruthy();
    expect(screen.getByTestId("location").textContent).toBe("/system-settings?source=menu&tab=backups");
  });
});
