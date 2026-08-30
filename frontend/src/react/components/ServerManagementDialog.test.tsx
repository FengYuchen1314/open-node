// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerSummary } from "../../domain/inventory";
import { getServerRemoval, getServerSettings, removeServer, updateServerSettings, type RemovalPreview } from "../../services/server-management";
import ServerManagementDialog from "./ServerManagementDialog";
vi.mock("../../services/server-management", () => ({ getServerRemoval: vi.fn(), getServerSettings: vi.fn(), removeServer: vi.fn(), updateServerSettings: vi.fn() }));
const server: ServerSummary = { id: "edge", name: "Edge", domain: "edge.example", domain_v6: "v6.example", ip_address: "192.0.2.1", ip_address_v6: "2001:db8::1",
  status: "connected", connection_mode: "websocket", listen_port: 0, pull_port: 0, ipv6_enabled: true, traffic_limit: 0, xray_mode: "external",
  current_upload_speed: 0, current_download_speed: 0, created_at: "2026-08-31", updated_at: "2026-08-31" };
const settings = { server, revision: "revision-1", updated_node_ids: [], license_required: false as const };
const preview: RemovalPreview = { server_id: "edge", server_name: "Edge", revision: "revision-1", nodes: [{ id: "node", name: "Node one" }],
  plans: [{ id: "plan", name: "Plan one" }], change_sets: [{ id: "change", name: "Old change" }], certificates: [{ id: "cert", name: "Certificate one" }],
  command_count: 7, unfinished_command_count: 1, telemetry_count: 5, user_count: 2, blockers: [] };
async function flush() { await act(async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); }); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const getStyle = window.getComputedStyle; vi.spyOn(window, "getComputedStyle").mockImplementation(element => getStyle(element));
  vi.mocked(getServerSettings).mockResolvedValue(settings); vi.mocked(getServerRemoval).mockResolvedValue(preview);
  vi.mocked(updateServerSettings).mockResolvedValue(settings);
  vi.mocked(removeServer).mockResolvedValue({ server_id: "edge", removed_node_count: 1, updated_plan_count: 1 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount(mode: "edit" | "remove" = "edit") {
  const props = { open: true, serverId: "edge", mode, onOpenChange: vi.fn(), onUpdated: vi.fn() };
  return { ...render(<ServerManagementDialog {...props} />), props };
}
describe("React server management", () => {
  it.each(["edit", "remove"] as const)("keeps the %s dialog centered with a viewport-bounded scrollable body", async mode => {
    mount(mode); await flush();
    const dialog = screen.getByRole("dialog");
    expect(dialog.closest(".ant-modal-wrap")?.classList.contains("ant-modal-centered")).toBe(true);
    expect((dialog.querySelector(".ant-modal-body") as HTMLElement).style.maxHeight).toBe("calc(100dvh - 200px)");
  });
  it("edits all addresses with the loaded revision and explicit node synchronization choice", async () => {
    const { props } = mount(); await flush();
    expect((screen.getByLabelText("IPv6 address") as HTMLInputElement).value).toBe("2001:db8::1");
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "Renamed" } });
    fireEvent.change(screen.getByLabelText("Domain"), { target: { value: "new.example" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Update matching node addresses" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" })); await flush();
    expect(updateServerSettings).toHaveBeenCalledWith("edge", { name: "Renamed", domain: "new.example", domain_v6: "v6.example",
      ip_address: "192.0.2.1", ip_address_v6: "2001:db8::1", ipv6_enabled: true }, "revision-1", false);
    expect(props.onUpdated).toHaveBeenCalledOnce(); expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });
  it("requires exact server name plus remote-runtime acknowledgement to remove", async () => {
    mount("remove"); await flush();
    expect(screen.getByText("Node one")).toBeTruthy(); expect(screen.getByText("Certificate one")).toBeTruthy();
    expect(screen.getByText(/stop automatic renewal/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Remove" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirm server name"), { target: { value: "Edge " } });
    fireEvent.click(screen.getByRole("checkbox", { name: "I accept that remote services may keep running" }));
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirm server name"), { target: { value: "Edge" } });
    fireEvent.click(button); await flush(); expect(removeServer).toHaveBeenCalledWith("edge", preview, "Edge");
  });
  it("never enables removal when the backend preview contains blockers", async () => {
    vi.mocked(getServerRemoval).mockResolvedValue({ ...preview, blockers: ["Pending deployment must finish"] });
    mount("remove"); await flush(); expect(screen.getByText("Pending deployment must finish")).toBeTruthy();
    expect((screen.getByLabelText("Confirm server name") as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove" })); expect(removeServer).not.toHaveBeenCalled();
  });
  it("invalidates a failed removal preview and requires a fresh preview/confirmation", async () => {
    vi.mocked(removeServer).mockRejectedValue(new Error("Revision changed")); mount("remove"); await flush();
    fireEvent.change(screen.getByLabelText("Confirm server name"), { target: { value: "Edge" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "I accept that remote services may keep running" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" })); await flush();
    expect(screen.getByText("Revision changed")).toBeTruthy(); expect(screen.queryByLabelText("Confirm server name")).toBeNull();
    expect((screen.getByRole("button", { name: "Remove" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reload server details" })); await flush();
    expect((screen.getByLabelText("Confirm server name") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("checkbox", { name: "I accept that remote services may keep running" }) as HTMLInputElement).checked).toBe(false);
  });
  it("ignores an old server load after a target change", async () => {
    let resolve!: (value: typeof settings) => void;
    vi.mocked(getServerSettings).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { props, rerender } = mount();
    vi.mocked(getServerSettings).mockResolvedValue({ ...settings, server: { ...server, id: "other", name: "Other" } });
    rerender(<ServerManagementDialog {...props} serverId="other" />); await flush();
    await act(async () => { resolve(settings); });
    expect((screen.getByLabelText("Server name") as HTMLInputElement).value).toBe("Other");
  });
  it("prevents duplicate save requests and ignores success after closing", async () => {
    let resolve!: (value: typeof settings) => void;
    vi.mocked(updateServerSettings).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { props, rerender } = mount(); await flush();
    const button = screen.getByRole("button", { name: "Save" }); fireEvent.click(button); fireEvent.click(button); await flush();
    expect(updateServerSettings).toHaveBeenCalledOnce();
    rerender(<ServerManagementDialog {...props} open={false} />); await act(async () => { resolve(settings); });
    expect(props.onUpdated).not.toHaveBeenCalled(); expect(props.onOpenChange).not.toHaveBeenCalled();
  });
});
