// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "antd";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { completeInitialSetup, getInitialSetupStatus, InitialSetupError, type InitialSetupStatus } from "../../services/initial-setup";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import InitialSetupPanel from "./InitialSetupPanel";

vi.mock("../../services/initial-setup", async original => ({ ...await original<typeof import("../../services/initial-setup")>(), getInitialSetupStatus: vi.fn(), completeInitialSetup: vi.fn() }));
const ready: InitialSetupStatus = { configured: false, available: true, expires_at: "2026-09-01T12:00:00Z", token_required: true };
const complete: InitialSetupStatus = { configured: true, available: false, expires_at: null, token_required: true };
beforeEach(() => { vi.resetAllMocks(); installDom(); vi.mocked(getInitialSetupStatus).mockResolvedValue(ready); vi.mocked(completeInitialSetup).mockResolvedValue(); });
afterEach(async () => { cleanup(); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function fill() {
  for (const [label, value] of [["初始化凭证", "a".repeat(43)], ["管理员密码", "  private-password  "], ["确认密码", "  private-password  "], ["浏览器标题", "  中文站点  "]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("checkbox"));
}
describe("Chinese browser first-run setup", () => {
  it("creates the administrator once, clears secrets immediately and requires normal login", async () => {
    const pending = deferred<void>(); vi.mocked(completeInitialSetup).mockReturnValue(pending.promise);
    renderUi(<InitialSetupPanel />); await flush(); fill();
    const form = screen.getByLabelText("管理员密码").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(completeInitialSetup).toHaveBeenCalledExactlyOnceWith({ setup_token: "a".repeat(43), username: "admin", password: "  private-password  ", site_title: "中文站点", brand_title: "Open Node", confirm_new_install: true });
    expect((screen.getByLabelText("初始化凭证") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("管理员密码") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("确认密码") as HTMLInputElement).value).toBe("");
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain("private-password");
    await act(async () => pending.resolve());
    expect(screen.getByRole("button", { name: "前往登录" })).toBeTruthy();
    expect(screen.queryByLabelText("管理员密码")).toBeNull();
  });
  it("requires acknowledgment and matching passwords", async () => {
    renderUi(<InitialSetupPanel />); await flush();
    expect((screen.getByRole("button", { name: "完成初始化" }) as HTMLButtonElement).disabled).toBe(true);
    fill(); fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "wrong-password" } });
    fireEvent.submit(screen.getByLabelText("管理员密码").closest("form")!); await flush();
    expect(completeInitialSetup).not.toHaveBeenCalled();
    expect(screen.getByText("两次输入的密码不一致。")).toBeTruthy();
  });
  it("reconciles a lost completion response with GET, without repeating the write", async () => {
    vi.mocked(completeInitialSetup).mockRejectedValue(new Error("PRIVATE"));
    vi.mocked(getInitialSetupStatus).mockResolvedValueOnce(ready).mockResolvedValueOnce(complete);
    renderUi(<InitialSetupPanel />); await flush(); fill();
    fireEvent.click(screen.getByRole("button", { name: "完成初始化" })); await flush();
    expect(completeInitialSetup).toHaveBeenCalledOnce(); expect(getInitialSetupStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "前往登录" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it("keeps the form closed if outcome reconciliation fails", async () => {
    vi.mocked(completeInitialSetup).mockRejectedValue(new InitialSetupError(null));
    vi.mocked(getInitialSetupStatus).mockResolvedValueOnce(ready).mockRejectedValueOnce(new Error("PRIVATE"));
    renderUi(<InitialSetupPanel />); await flush(); fill();
    fireEvent.click(screen.getByRole("button", { name: "完成初始化" })); await flush();
    expect(screen.queryByLabelText("管理员密码")).toBeNull();
    expect(screen.getByRole("button", { name: "重新读取状态" })).toBeTruthy();
    expect(completeInitialSetup).toHaveBeenCalledOnce();
  });
  it("shows local issuance instructions when a ticket is absent or expired", async () => {
    vi.mocked(getInitialSetupStatus).mockResolvedValue({ ...ready, available: false, expires_at: null });
    renderUi(<InitialSetupPanel />); await flush();
    expect(screen.getByText("open-node-admin prepare-setup")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完成初始化" })).toBeNull();
  });
  it("ignores stale status reads across StrictMode remount and after unmount", async () => {
    const old = deferred<InitialSetupStatus>();
    vi.mocked(getInitialSetupStatus).mockReturnValueOnce(old.promise).mockResolvedValueOnce(ready);
    const view = render(<StrictMode><ConfigProvider theme={{ token: { motion: false } }}><InitialSetupPanel /></ConfigProvider></StrictMode>); await flush();
    expect(getInitialSetupStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("初始化凭证")).toBeTruthy();
    await act(async () => old.resolve(complete));
    expect(screen.queryByRole("button", { name: "前往登录" })).toBeNull();
    fill(); const result = deferred<void>(); vi.mocked(completeInitialSetup).mockReturnValue(result.promise);
    fireEvent.click(screen.getByRole("button", { name: "完成初始化" })); await flush(); view.unmount();
    await act(async () => result.resolve());
    expect(screen.queryByRole("button", { name: "前往登录" })).toBeNull();
  });
});
