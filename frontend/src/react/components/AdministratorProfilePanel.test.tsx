// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadAdministratorProfile, saveAdministratorProfile } from "../../services/auth";
import { flush, installDom, renderUi } from "../test-utils";
import AdministratorProfilePanel from "./AdministratorProfilePanel";

vi.mock("../../services/auth", async original => ({
  ...await original<typeof import("../../services/auth")>(),
  loadAdministratorProfile: vi.fn(), saveAdministratorProfile: vi.fn(),
}));

const profile = { username: "admin", email: "old@example.test", nickname: "管理员", avatar_url: "", revision: 2 };
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf" };

beforeEach(() => {
  vi.resetAllMocks(); installDom();
  vi.mocked(loadAdministratorProfile).mockResolvedValue(profile);
  vi.mocked(saveAdministratorProfile).mockResolvedValue({ ...profile, email: "new@example.test", revision: 3 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("administrator profile panel", () => {
  it("loads and saves a revision-bound Chinese profile", async () => {
    renderUi(<AdministratorProfilePanel operator={operator} />); await flush();
    expect((screen.getByLabelText("邮箱") as HTMLInputElement).value).toBe("old@example.test");
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "new@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "保存管理员资料" })); await flush();
    expect(saveAdministratorProfile).toHaveBeenCalledExactlyOnceWith({
      email: "new@example.test", nickname: "管理员", avatar_url: "", revision: 2,
    });
    expect(screen.getByText("管理员资料已保存。")).toBeTruthy();
  });

  it("re-reads an uncertain save without replaying it", async () => {
    vi.mocked(saveAdministratorProfile).mockRejectedValue(new Error("PRIVATE"));
    vi.mocked(loadAdministratorProfile).mockResolvedValueOnce(profile).mockResolvedValueOnce({ ...profile, nickname: "服务器值", revision: 3 });
    renderUi(<AdministratorProfilePanel operator={operator} />); await flush();
    fireEvent.change(screen.getByLabelText("昵称"), { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存管理员资料" })); await flush();
    expect(saveAdministratorProfile).toHaveBeenCalledOnce();
    expect(loadAdministratorProfile).toHaveBeenCalledTimes(2);
    expect((screen.getByLabelText("昵称") as HTMLInputElement).value).toBe("服务器值");
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
});
