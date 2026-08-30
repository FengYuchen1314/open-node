// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearSubscriberSession, revokeSubscriberDevice, subscriberChangePassword, subscriberDevices, subscriberIpPolicy, subscriberSecurity, subscriberToken, type SubscriberDevice } from "../../services/subscriber-auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import SubscriberSecurityPanel from "./SubscriberSecurityPanel";

vi.mock("../../services/subscriber-auth", async importOriginal => ({ ...await importOriginal<typeof import("../../services/subscriber-auth")>(), clearSubscriberSession: vi.fn(), revokeSubscriberDevice: vi.fn(), subscriberChangePassword: vi.fn(), subscriberDevices: vi.fn(), subscriberIpPolicy: vi.fn(), subscriberSecurity: vi.fn(), subscriberToken: vi.fn() }));
const device: SubscriberDevice = { id: "current-device", current: true, created_at: "2026-08-31T00:00:00Z", last_seen_at: "2026-08-31T01:00:00Z", expires_at: "2026-09-01T00:00:00Z", peer: "192.0.2.1", user_agent: "Test browser" };
beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(subscriberSecurity).mockResolvedValue({ totp_enabled: false, totp_available: true, recovery_codes_remaining: 0 });
  vi.mocked(subscriberDevices).mockResolvedValue([device, { ...device, id: "other-device", current: false }]);
  vi.mocked(subscriberIpPolicy).mockResolvedValue({ username: "alice", enabled: false, networks: [], updated_at: "2026-08-31T00:00:00Z", license_required: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const modal = () => within(screen.getByRole("dialog"));
const fill = (label: string, value: string) => fireEvent.change(modal().getByLabelText(label), { target: { value } });
describe("React subscriber security", () => {
  it("validates confirmation and clears all three password fields on server rejection", async () => {
    vi.mocked(subscriberChangePassword).mockRejectedValue(new Error("Password update refused"));
    renderUi(<SubscriberSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    fill("Current password", "private-old-password"); fill("New password", "private-new-password"); fill("Confirm password", "mismatching-password");
    expect((modal().getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
    fill("Confirm password", "private-new-password");
    fireEvent.click(modal().getByRole("button", { name: "Confirm" })); await flush();
    expect(subscriberChangePassword).toHaveBeenCalledExactlyOnceWith({ password: "private-old-password", code: "" }, "private-new-password");
    expect(modal().getByText("Password update refused")).toBeTruthy();
    for (const name of ["Current password", "New password", "Confirm password"]) expect((modal().getByLabelText(name) as HTMLInputElement).value).toBe("");
    expect(clearSubscriberSession).not.toHaveBeenCalled();
  });
  it("requires a fresh password and enrolled factor to rotate subscription links", async () => {
    vi.mocked(subscriberSecurity).mockResolvedValue({ totp_enabled: true, totp_available: true, recovery_codes_remaining: 10 });
    const onChanged = vi.fn(); renderUi(<SubscriberSecurityPanel onChanged={onChanged} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "Reset links" }));
    expect(subscriberToken).not.toHaveBeenCalled();
    fill("Current password", "private-password");
    expect((modal().getByRole("button", { name: "Confirm" }) as HTMLButtonElement).disabled).toBe(true);
    fill("Authenticator or recovery code", "one-time-proof");
    fireEvent.click(modal().getByRole("button", { name: "Confirm" })); await flush();
    expect(subscriberToken).toHaveBeenCalledExactlyOnceWith({ password: "private-password", code: "one-time-proof" });
    expect(onChanged).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("Current password")).toBeNull();
    expect(screen.getByText("Subscription links reset")).toBeTruthy();
  });
  it("distinguishes revoking other sessions from signing out the current device", async () => {
    renderUi(<SubscriberSecurityPanel />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "Revoke others" })); await flush();
    expect(revokeSubscriberDevice).toHaveBeenLastCalledWith(undefined);
    expect(clearSubscriberSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Sign out this device" })); await flush();
    expect(revokeSubscriberDevice).toHaveBeenLastCalledWith(device.id);
    expect(clearSubscriberSession).toHaveBeenCalledOnce();
  });
  it("discards an in-flight link reset notification after the account panel is disposed", async () => {
    const pending = deferred<Awaited<ReturnType<typeof subscriberToken>>>(); vi.mocked(subscriberToken).mockReturnValue(pending.promise);
    const onChanged = vi.fn(); const view = renderUi(<SubscriberSecurityPanel onChanged={onChanged} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "Reset links" })); fill("Current password", "private-password");
    fireEvent.click(modal().getByRole("button", { name: "Confirm" })); await flush();
    view.unmount(); await act(async () => pending.reject(new Error("Late rejection")));
    expect(onChanged).not.toHaveBeenCalled(); expect(screen.queryByText("Late rejection")).toBeNull();
    expect(document.body.textContent).not.toContain("private-password");
  });
});
