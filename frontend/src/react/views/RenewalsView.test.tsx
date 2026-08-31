// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountRenewals, RenewalRequest } from "../../domain/renewals";
import { authState } from "../../services/auth";
import { getAccountRenewal, getAccountRenewals, listRenewals, newRenewalRequestId, RenewalRequestError, reviewRenewal, submitRenewal } from "../../services/renewals";
import { subscriberState } from "../../services/subscriber-auth";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AdminRenewalsView from "./AdminRenewalsView";
import RenewalRequestView from "./RenewalRequestView";

vi.mock("../../services/renewals", async original => ({ ...await original<typeof import("../../services/renewals")>(), getAccountRenewal: vi.fn(), getAccountRenewals: vi.fn(), listRenewals: vi.fn(), newRenewalRequestId: vi.fn(), reviewRenewal: vi.fn(), submitRenewal: vi.fn() }));
const id = "01234567-89ab-4cde-8fab-0123456789ab";
const row: RenewalRequest = { id, username: "alice", plan_id: id, plan_name: "月付套餐", previous_end_date: "2026-09-30T00:00:00Z", renew_days: 30, status: "pending", created_at: "2026-08-31T00:00:00Z", reviewed_at: null, reviewed_by: null, new_end_date: null };
const overview: AccountRenewals = { requests: [], total: 0, limit: 20, offset: 0, license_required: false, eligible: true, unavailable_code: null, plan_id: id, plan_name: "月付套餐", renew_days: 30, plan_expires_at: row.previous_end_date };
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
function button(name: string) { return screen.getByRole("button", { name }) as HTMLButtonElement; }
async function click(name: string) { fireEvent.click(button(name)); await flush(); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); installDom();
  authState.ready = true; authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "ADMIN-CSRF" };
  subscriberState.ready = true; subscriberState.session = { authenticated: true, username: "alice", csrf_token: "USER-CSRF", requires_2fa: false, challenge: null };
  vi.mocked(getAccountRenewals).mockResolvedValue({ ...overview }); vi.mocked(getAccountRenewal).mockResolvedValue(row);
  vi.mocked(newRenewalRequestId).mockReturnValue(id); vi.mocked(submitRenewal).mockResolvedValue(row);
  vi.mocked(listRenewals).mockResolvedValue({ requests: [row], total: 1, limit: 20, offset: 0, license_required: false });
  vi.mocked(reviewRenewal).mockResolvedValue({ request: { ...row, status: "approved", new_end_date: "2026-10-30T00:00:00Z" }, processed: true, command_count: 1, warnings_count: 0 });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network"); })); localStorage.clear(); sessionStorage.clear();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });

describe("Chinese renewal workspaces", () => {
  it("keeps subscriber and administrator access separate", async () => {
    subscriberState.session = null; authState.session = { configured: true, authenticated: false, username: null, csrf_token: null };
    renderUi(<><RenewalRequestView /><AdminRenewalsView /></>); await flush();
    expect(screen.getByText("请登录用户账户后申请续费。")).toBeTruthy(); expect(screen.getByText("请登录管理员账户后审核续费。")).toBeTruthy();
    expect(getAccountRenewals).not.toHaveBeenCalled(); expect(listRenewals).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
  it("loads under StrictMode and explains manual review without submitting", async () => {
    renderUi(<StrictMode><RenewalRequestView /></StrictMode>); await flush();
    expect(screen.getByRole("heading", { name: "申请续费" })).toBeTruthy(); expect(button("刷新续费记录").disabled).toBe(false);
    expect(screen.getByText(/提交申请不会自动扣款/)).toBeTruthy(); expect(submitRenewal).not.toHaveBeenCalled();
  });
  it("submits only once and immediately clears the reference from the form", async () => {
    const pending = deferred<RenewalRequest>(); vi.mocked(submitRenewal).mockReturnValue(pending.promise);
    renderUi(<RenewalRequestView />); await flush(); fill("续费口令", "PRIVATE-REFERENCE");
    const form = screen.getByLabelText("续费口令").closest("form")!;
    fireEvent.submit(form); fireEvent.submit(form); await flush();
    expect(submitRenewal).toHaveBeenCalledExactlyOnceWith({ request_id: id, passphrase: "PRIVATE-REFERENCE" });
    expect((screen.getByLabelText("续费口令") as HTMLInputElement).value).toBe("");
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
    await act(async () => pending.resolve(row));
    expect(screen.getByText(/续费申请已提交，等待管理员审核/)).toBeTruthy(); expect(button("提交续费申请").disabled).toBe(true);
  });
  it("reconciles a lost receipt by GET of the original ID without replaying POST", async () => {
    vi.mocked(submitRenewal).mockRejectedValue(new RenewalRequestError(null));
    renderUi(<RenewalRequestView />); await flush(); fill("续费口令", "PRIVATE"); await click("提交续费申请");
    expect(screen.getByText("申请结果尚未确认")).toBeTruthy(); expect(button("提交续费申请").disabled).toBe(true);
    vi.mocked(getAccountRenewals).mockResolvedValue({ ...overview, requests: [row], total: 1, eligible: false, unavailable_code: "renewal_pending" });
    await click("查询原申请"); expect(getAccountRenewal).toHaveBeenCalledExactlyOnceWith(id); expect(submitRenewal).toHaveBeenCalledOnce();
    expect(screen.getByText(/已找到原申请/)).toBeTruthy(); expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it("requires matching reference and explicit review confirmation before approval", async () => {
    const pending = deferred<Awaited<ReturnType<typeof reviewRenewal>>>(); vi.mocked(reviewRenewal).mockReturnValue(pending.promise);
    renderUi(<AdminRenewalsView />); await flush(); await click("审核通过");
    expect(button("确认通过并延期").disabled).toBe(true); fill("用户提供的续费口令", "PRIVATE");
    expect(button("确认通过并延期").disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "已人工核对续费信息，同意延长此用户的套餐" })); await flush();
    const approvalButton = button("确认通过并延期");
    fireEvent.click(approvalButton); fireEvent.click(approvalButton); await flush();
    expect(reviewRenewal).toHaveBeenCalledExactlyOnceWith(id, { decision: "approve", confirm_reviewed: true, passphrase: "PRIVATE" });
    expect((screen.getByLabelText("用户提供的续费口令") as HTMLInputElement).value).toBe("");
    await act(async () => pending.resolve({ request: { ...row, status: "approved", new_end_date: "2026-10-30T00:00:00Z" }, processed: true, command_count: 1, warnings_count: 0 }));
    expect(screen.getByText(/续费审核已通过，套餐到期时间更新为/)).toBeTruthy(); expect(screen.queryByRole("button", { name: "审核通过" })).toBeNull();
  });
  it("drops a pending user's response after the session changes", async () => {
    const pending = deferred<AccountRenewals>(); vi.mocked(getAccountRenewals).mockReturnValue(pending.promise);
    renderUi(<RenewalRequestView />); await flush();
    act(() => { subscriberState.session = null; }); await flush();
    await act(async () => pending.resolve({ ...overview, plan_name: "PRIVATE-OLD-ACCOUNT" }));
    expect(screen.getByText("请登录用户账户后申请续费。")).toBeTruthy(); expect(document.body.textContent).not.toContain("PRIVATE-OLD-ACCOUNT");
  });
});
