// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "../../ui";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";

import { listServers } from "../../services/inventory";
import { flush, installDom } from "../test-utils";
import ServerWorkspaceView from "./ServerWorkspaceView";

vi.mock("../../services/inventory", async original => ({ ...await original<typeof import("../../services/inventory")>(), listServers: vi.fn() }));
vi.mock("./DashboardView", () => ({ default: () => <div>Dashboard child</div> }));
vi.mock("./ConfigView", () => ({ default: ({ allowNodeCatalogMutations }: { allowNodeCatalogMutations?: boolean }) => <div data-testid="config-child">Config child:{String(allowNodeCatalogMutations)}</div> }));
vi.mock("../components/ServerEgressPanel", () => ({ default: ({ advancedContent }: { advancedContent: React.ReactNode }) => <div>Egress child{advancedContent}</div> }));
vi.mock("./ServerSharingView", () => ({ default: () => <div>Sharing child</div> }));
vi.mock("./DDNSView", () => ({ default: () => <div>DDNS child</div> }));
vi.mock("../components/SharedIngressDialog", () => ({ default: ({ open, serverId }: { open: boolean; serverId: string }) => <div data-testid="shared-ingress-dialog">{open ? `open:${serverId}` : "closed"}</div> }));

function LocationProbe() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
function mount(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><ConfigProvider theme={{ token: { motion: false } }}><ServerWorkspaceView /><LocationProbe /></ConfigProvider></MemoryRouter>);
}

beforeEach(() => { vi.resetAllMocks(); installDom(); vi.mocked(listServers).mockResolvedValue([]); });
afterEach(async () => { cleanup(); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("server management aggregate workspace", () => {
  it("selects a deep-linked tab and keeps unrelated query values while navigating", async () => {
    mount("/servers?tab=egress&from=legacy"); await flush();
    expect(screen.getByText("Egress child", { exact: false })).toBeTruthy();
    expect(screen.getByTestId("config-child").textContent).toBe("Config child:false");
    expect(screen.getByRole("tab", { name: "服务器设置" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "动态 DNS" })); await flush();
    expect(screen.getByText("DDNS child")).toBeTruthy();
    expect(screen.getByTestId("location").textContent).toBe("/servers?tab=ddns&from=legacy");
  });

  it("falls back to access and maintenance for an unknown tab", async () => {
    mount("/servers?tab=unknown"); await flush();
    expect(screen.getByText("Dashboard child")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "接入与维护" }).getAttribute("aria-selected")).toBe("true");
  });

  it("selects a local server and opens the shared-ingress reverse-proxy dialog", async () => {
    vi.mocked(listServers).mockResolvedValue([
      { id: "local", name: "Local server", is_federated: false },
      { id: "shared", name: "Shared server", is_federated: true },
    ] as Awaited<ReturnType<typeof listServers>>);
    mount("/servers?tab=reverse-proxy"); await flush();
    expect(screen.getByTestId("shared-ingress-dialog").textContent).toBe("closed");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "反向代理服务器" }));
    fireEvent.click(screen.getByText("Local server", { selector: ".ui-option" }));
    fireEvent.click(screen.getByRole("button", { name: "打开反向代理配置" })); await flush();
    expect(screen.getByTestId("shared-ingress-dialog").textContent).toBe("open:local");
    expect(screen.queryByText("Shared server", { selector: ".ui-option" })).toBeNull();
  });
});
