// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { RestoreStatus } from "../../domain/backups";
import { authState } from "../../services/auth";
import { getRestoreStatus, reviewRestore } from "../../services/backups";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import RestoreReviewPanel from "./RestoreReviewPanel";

vi.mock("../../services/backups", async original => ({ ...await original<typeof import("../../services/backups")>(),
  getRestoreStatus: vi.fn(), reviewRestore: vi.fn(),
}));
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "restore-csrf" };
const initial: RestoreStatus = { blocked: true, restart_required: false, record: { version: 1,
  id: "01234567-89ab-4cde-8fab-0123456789ab", status: "review_required", created_at: "2026-09-01T00:00:00Z",
  archive_sha256: "a".repeat(64), invalidated_sessions: 2, cancelled_agent_commands: 3,
  cancelled_certificate_jobs: 1, quarantined_files: 4, reviewed_at: null } };
const reviewed: RestoreStatus = { ...initial, restart_required: true, record: { ...initial.record!, status: "reviewed", reviewed_at: "2026-09-01T00:01:00Z" } };
beforeEach(() => {
  vi.resetAllMocks(); installDom(); authState.ready = true; authState.session = { ...operator };
  vi.mocked(reviewRestore).mockResolvedValue(reviewed); vi.mocked(getRestoreStatus).mockResolvedValue(reviewed);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function mount() { return renderUi(<RestoreReviewPanel initial={initial} requiresTwoFactor operator={operator} />); }
function fill() {
  for (const box of screen.getAllByRole("checkbox")) fireEvent.click(box);
  fireEvent.change(screen.getByLabelText("恢复复核密码"), { target: { value: "PRIVATE-password" } });
  fireEvent.change(screen.getByLabelText("恢复复核验证码或恢复码"), { target: { value: "123456" } });
}
it("does not submit unchecked confirmation fields through keyboard form submission", async () => {
  mount();
  fireEvent.change(screen.getByLabelText("恢复复核密码"), { target: { value: "PRIVATE-password" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存复核结果" }).closest("form")!); await flush();
  expect(reviewRestore).not.toHaveBeenCalled();
});
it("requires all confirmations and fresh proof, clears credentials and asks for explicit restart", async () => {
  mount(); const button = screen.getByRole("button", { name: "保存复核结果" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true); fill(); expect(button.disabled).toBe(false);
  const pending = deferred<RestoreStatus>(); vi.mocked(reviewRestore).mockReturnValue(pending.promise);
  fireEvent.submit(button.closest("form")!); fireEvent.submit(button.closest("form")!); await flush();
  expect(reviewRestore).toHaveBeenCalledOnce();
  expect((screen.getByLabelText("恢复复核密码") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("恢复复核验证码或恢复码") as HTMLInputElement).value).toBe("");
  await act(async () => pending.resolve(reviewed)); await flush();
  expect(screen.getByText("复核已保存，请在服务器上重启控制面")).toBeTruthy();
  expect(screen.queryByLabelText("恢复复核密码")).toBeNull();
});
it("reconciles a lost receipt using GET without replaying proof", async () => {
  mount(); fill(); vi.mocked(reviewRestore).mockRejectedValue(new Error("PRIVATE server error"));
  fireEvent.submit(screen.getByRole("button", { name: "保存复核结果" }).closest("form")!); await flush();
  expect(document.body.textContent).not.toContain("PRIVATE");
  fireEvent.click(screen.getByRole("button", { name: "刷新恢复状态" })); await flush();
  expect(getRestoreStatus).toHaveBeenCalledOnce(); expect(reviewRestore).toHaveBeenCalledOnce();
  expect(screen.getByText("复核已保存，请在服务器上重启控制面")).toBeTruthy();
});
it("discards late results after unmount or session replacement", async () => {
  const pending = deferred<RestoreStatus>(); vi.mocked(reviewRestore).mockReturnValue(pending.promise);
  const view = mount(); fill(); fireEvent.submit(screen.getByRole("button", { name: "保存复核结果" }).closest("form")!);
  authState.session = { ...operator, csrf_token: "replacement" }; view.unmount();
  await act(async () => pending.resolve(reviewed)); await flush();
  expect(screen.queryByText("复核已保存，请在服务器上重启控制面")).toBeNull();
});
