// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BackupJob, BackupsOverview } from "../../domain/backups";
import { routes } from "../../routes";
import { authState } from "../../services/auth";
import { BackupRequestError, createBackup, deleteBackup, getBackupJob, getBackups, newBackupRequestId, prepareRestoreArchive, uploadRestoreArchive } from "../../services/backups";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import BackupsView from "./BackupsView";

vi.mock("../../services/backups", async original => ({ ...await original<typeof import("../../services/backups")>(),
  createBackup: vi.fn(), deleteBackup: vi.fn(), getBackupJob: vi.fn(), getBackups: vi.fn(), newBackupRequestId: vi.fn(),
  uploadRestoreArchive: vi.fn(), prepareRestoreArchive: vi.fn(),
}));
const id = "01234567-89ab-4cde-8fab-0123456789ab", recipient = `age1${"q".repeat(58)}`;
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" };
const ready: BackupJob = { id, status: "ready", created_at: "2026-08-31T00:00:00Z", expires_at: "2026-08-31T00:15:00Z",
  size: 4096, sha256: "a".repeat(64), error_code: null, restoration_ready: false };
const queued: BackupJob = { ...ready, status: "queued", size: null, sha256: null };
const initial: BackupsOverview = { available: true, unavailable_code: null, jobs: [], max_completed: 2, ttl_seconds: 900,
  requires_two_factor: true, restoration_supported: false };
function button(name: string) { return screen.getByRole("button", { name }) as HTMLButtonElement; }
function fill(name: string, value: string) { fireEvent.change(screen.getByLabelText(name), { target: { value } }); }
async function click(name: string) { fireEvent.click(button(name)); await flush(); }
async function mount() { const result = renderUi(<BackupsView />); await flush(); return result; }
async function confirmForm() {
  fill("age 接收者公钥", recipient); await click("创建加密备份");
  fill("当前管理员密码", "PRIVATE-PASSWORD"); fill("验证器验证码或恢复码", "123456");
  fireEvent.click(screen.getByRole("checkbox", { name: "确认已自行保管恢复私钥" })); await flush();
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-08-31T00:01:00Z")); vi.resetAllMocks(); installDom();
  authState.ready = true; authState.error = ""; authState.session = { ...operator };
  vi.mocked(getBackups).mockResolvedValue({ ...initial, jobs: [] }); vi.mocked(newBackupRequestId).mockReturnValue(id);
  vi.mocked(createBackup).mockResolvedValue(queued); vi.mocked(getBackupJob).mockResolvedValue(ready);
  vi.mocked(deleteBackup).mockResolvedValue(undefined);
  vi.mocked(uploadRestoreArchive).mockResolvedValue({ id, size: 22, sha256: "a".repeat(64), expires_at: ready.expires_at, license_required: false });
  vi.mocked(prepareRestoreArchive).mockResolvedValue({ id, restart_required: true, automatic_restart: true, license_required: false });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network request"); }));
  localStorage.clear(); sessionStorage.clear();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });

describe("administrator backup workspace", () => {
  it("requires administrator authentication and registers an administrator-only route", async () => {
    authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
    await mount(); expect(screen.getByText("请登录管理员账户后管理备份。")).toBeTruthy();
    expect(getBackups).not.toHaveBeenCalled(); expect(createBackup).not.toHaveBeenCalled();
    expect(routes.find(route => route.path === "/backups")?.meta?.subscriber).toBeUndefined();
    expect(routes.some(route => route.path === "/backups")).toBe(true);
  });
  it("survives StrictMode replay and explains the public-key-only and not-yet-restored boundaries", async () => {
    renderUi(<StrictMode><BackupsView /></StrictMode>); await flush();
    expect(button("刷新备份状态").disabled).toBe(false); expect(screen.getByRole("heading", { name: "备份与恢复" })).toBeTruthy();
    expect(screen.getByText(/创建备份时这里只接收 age 公钥/)).toBeTruthy();
    expect(screen.getByText(/当前部署不支持浏览器恢复/)).toBeTruthy();
    expect(screen.getByText(/加密包保留 15 分钟，最多保留两份/)).toBeTruthy();
    expect(document.querySelector('input[type="file"]')).toBeNull(); expect(createBackup).not.toHaveBeenCalled();
  });
  it("prepares a selected browser restore only after credentials and both confirmations", async () => {
    vi.mocked(getBackups).mockResolvedValue({ ...initial, restoration_supported: true });
    await mount(); await click("上传备份并恢复");
    const file = new File([new Uint8Array(22)], "backup.zip.age", { type: "application/octet-stream" });
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } }); await flush();
    fill("age 恢复私钥", "AGE-SECRET-KEY-PRIVATE"); fill("当前管理员密码", "PRIVATE-PASSWORD");
    fill("验证器验证码或恢复码", "123456");
    fireEvent.click(screen.getByRole("checkbox", { name: /确认重启后/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /确认备份来源可信/ })); await flush();
    await click("验证并准备恢复");
    expect(uploadRestoreArchive).toHaveBeenCalledExactlyOnceWith(file);
    expect(prepareRestoreArchive).toHaveBeenCalledWith(id, {
      format: "age", identity: "AGE-SECRET-KEY-PRIVATE", subscriber_totp_key: "",
      password: "PRIVATE-PASSWORD", code: "123456", confirm_replace_instance: true,
      confirm_trusted_backup: true,
    });
    expect(screen.getByText(/服务正在重启/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("AGE-SECRET-KEY-PRIVATE");
  });
  it("offers a native same-origin download only for a ready, unexpired job", async () => {
    vi.mocked(getBackups).mockResolvedValue({ ...initial, jobs: [ready, { ...queued, id: "11234567-89ab-4cde-8fab-0123456789ab" },
      { ...ready, id: "21234567-89ab-4cde-8fab-0123456789ab", expires_at: "2026-08-31T00:00:30Z" }] });
    await mount();
    const links = screen.getAllByRole("link", { name: /下载加密备份/ }); expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute("href")).toBe(`${window.location.origin}/api/v1/backups/${id}/download`);
    expect(links[0]!.hasAttribute("download")).toBe(true); expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByText("等待执行")).toBeTruthy(); expect(screen.getByText("已超过保留期限")).toBeTruthy();
  });
  it("clears password/code at submission and admits only one explicit create while the receipt is pending", async () => {
    const pending = deferred<BackupJob>(); vi.mocked(createBackup).mockReturnValue(pending.promise);
    await mount(); await confirmForm();
    const form = screen.getByLabelText("当前管理员密码").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(createBackup).toHaveBeenCalledExactlyOnceWith({ request_id: id, recipient, password: "PRIVATE-PASSWORD", code: "123456" });
    expect((screen.getByLabelText("当前管理员密码") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("验证器验证码或恢复码") as HTMLInputElement).value).toBe("");
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
    await act(async () => pending.resolve(queued)); await flush();
    expect(screen.getByText("等待执行")).toBeTruthy(); expect(screen.queryByRole("link", { name: /下载加密备份/ })).toBeNull();
  });
  it("keeps a lost receipt's original UUID through 404 and reconciles using GET without another POST", async () => {
    vi.mocked(createBackup).mockRejectedValue(new Error("PRIVATE lost receipt"));
    vi.mocked(getBackupJob).mockRejectedValueOnce(new BackupRequestError(404, "backup_not_found")).mockResolvedValueOnce(ready);
    await mount(); await confirmForm(); await click("确认创建备份");
    expect(screen.getByTestId("backup-pending").textContent).toContain(id);
    expect(button("创建加密备份").disabled).toBe(true); expect(document.body.textContent).not.toContain("PRIVATE");
    await click("查询原备份请求"); expect(screen.getByTestId("backup-pending")).toBeTruthy();
    await click("查询原备份请求"); expect(screen.queryByTestId("backup-pending")).toBeNull();
    expect(getBackupJob).toHaveBeenNthCalledWith(1, id); expect(getBackupJob).toHaveBeenNthCalledWith(2, id);
    expect(createBackup).toHaveBeenCalledOnce(); expect(screen.getByRole("link", { name: `下载加密备份 ${id}` })).toBeTruthy();
  });
  it("discards a late creation receipt after a session change", async () => {
    const pending = deferred<BackupJob>(); vi.mocked(createBackup).mockReturnValue(pending.promise);
    await mount(); await confirmForm(); await click("确认创建备份");
    await act(async () => { authState.session = { ...operator, csrf_token: "DIFFERENT-CSRF" }; }); await flush();
    await act(async () => pending.resolve(ready)); await flush();
    expect(screen.queryByTestId(`backup-job-${id}`)).toBeNull(); expect(screen.queryByTestId("backup-pending")).toBeNull();
    expect((screen.getByLabelText("age 接收者公钥") as HTMLInputElement).value).toBe("");
  });
  it("requires explicit deletion confirmation and does not claim local downloaded files are removed", async () => {
    vi.mocked(getBackups).mockResolvedValue({ ...initial, jobs: [ready] });
    await mount(); await click(`删除备份 ${id}`); expect(deleteBackup).not.toHaveBeenCalled();
    expect(screen.getByText(/已经下载到本地的文件不会被删除/)).toBeTruthy();
    await click("确认删除备份"); expect(deleteBackup).toHaveBeenCalledExactlyOnceWith(id);
    expect(screen.queryByTestId(`backup-job-${id}`)).toBeNull(); expect(screen.getByText("删除请求已确认。")).toBeTruthy();
  });
  it("polls only GET and stops polling after unmount", async () => {
    vi.mocked(getBackups).mockResolvedValue({ ...initial, jobs: [queued] });
    const view = await mount(); expect(getBackups).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); await flush();
    expect(getBackups).toHaveBeenCalledTimes(2); expect(createBackup).not.toHaveBeenCalled(); expect(deleteBackup).not.toHaveBeenCalled();
    view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(getBackups).toHaveBeenCalledTimes(2);
  });
  it("keeps creation disabled when the server reports backup unavailable", async () => {
    vi.mocked(getBackups).mockResolvedValue({ ...initial, available: false, unavailable_code: "backup_worker_unavailable" });
    await mount(); fill("age 接收者公钥", recipient);
    expect(button("创建加密备份").disabled).toBe(true); expect(screen.getByText(/备份服务当前不可用/)).toBeTruthy();
  });
});
