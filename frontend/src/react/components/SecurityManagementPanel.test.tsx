// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSecurityBan,
  loadSecurityBans,
  loadSecurityEvents,
  loadSecuritySettings,
  removeSecurityBan,
  saveSecuritySettings,
} from "../../services/security";
import { flush, installDom, renderUi } from "../test-utils";
import SecurityManagementPanel from "./SecurityManagementPanel";

vi.mock("../../services/security", async original => ({
  ...await original<typeof import("../../services/security")>(),
  createSecurityBan: vi.fn(),
  loadSecurityBans: vi.fn(),
  loadSecurityEvents: vi.fn(),
  loadSecuritySettings: vi.fn(),
  removeSecurityBan: vi.fn(),
  saveSecuritySettings: vi.fn(),
}));
const settings = {
  revision: 4,
  brute_force_enabled: true,
  brute_force_max_failures: 5,
  brute_force_window_minutes: 1440,
  brute_force_block_minutes: 1440,
  skip_local_ip: true,
  license_required: false as const,
};
const ban = {
  ip: "1.1.1.1", reason: "brute_force" as const, banned_at: "2026-09-01T00:00:00Z",
  expires_at: "2026-09-02T00:00:00Z", permanent: false, fail_count: 5, actor: "",
};

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(loadSecuritySettings).mockResolvedValue({ ...settings });
  vi.mocked(loadSecurityBans).mockResolvedValue([ban]);
  vi.mocked(loadSecurityEvents).mockResolvedValue({ events: [], offset: 0, limit: 100, has_more: false, license_required: false });
  vi.mocked(saveSecuritySettings).mockImplementation(async value => ({ ...value, revision: value.revision + 1 }));
  vi.mocked(createSecurityBan).mockResolvedValue({ ...ban, reason: "manual", actor: "admin" });
  vi.mocked(removeSecurityBan).mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("security management panel", () => {
  it("loads current settings and bans without writing", async () => {
    renderUi(<SecurityManagementPanel />); await flush();
    expect(screen.getByText("1.1.1.1")).toBeTruthy();
    expect(screen.getByText("安全事件与 IP 封禁")).toBeTruthy();
    expect(createSecurityBan).not.toHaveBeenCalled();
    expect(removeSecurityBan).not.toHaveBeenCalled();
    expect(saveSecuritySettings).not.toHaveBeenCalled();
  });

  it("saves one revision-bound threshold update", async () => {
    renderUi(<SecurityManagementPanel />); await flush();
    const failures = screen.getAllByRole("spinbutton")[0];
    fireEvent.change(failures, { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "保存安全阈值" })); await flush();
    expect(saveSecuritySettings).toHaveBeenCalledExactlyOnceWith({
      ...settings, brute_force_max_failures: 6,
    });
    expect(screen.getByText("安全阈值已保存并立即生效。")).toBeTruthy();
  });

  it("creates a manual ban once and rereads all security state", async () => {
    renderUi(<SecurityManagementPanel />); await flush();
    fireEvent.change(screen.getByLabelText("要封禁的 IP"), { target: { value: "8.8.8.8" } });
    fireEvent.click(screen.getByText("永久封禁"));
    fireEvent.click(screen.getByRole("button", { name: "手动封禁" })); await flush();
    expect(createSecurityBan).toHaveBeenCalledExactlyOnceWith("8.8.8.8", true);
    expect(loadSecuritySettings).toHaveBeenCalledTimes(2);
    expect(loadSecurityBans).toHaveBeenCalledTimes(2);
    expect(loadSecurityEvents).toHaveBeenCalledTimes(2);
    expect(screen.getByText("IP 封禁已生效。")).toBeTruthy();
  });
});
