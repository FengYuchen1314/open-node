import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RenewalRequest } from "../domain/renewals";
import { authState } from "./auth";
import { cancelRenewal, getAccountRenewal, getAccountRenewals, listRenewals, renewalErrorMessage, RenewalRequestError, reviewRenewal, submitRenewal } from "./renewals";
import { subscriberState } from "./subscriber-auth";

const id = "01234567-89ab-4cde-8fab-0123456789ab";
const row: RenewalRequest = { id, username: "alice", plan_id: id, plan_name: "月付套餐", previous_end_date: "2026-09-30T00:00:00Z", renew_days: 30, status: "pending", created_at: "2026-08-31T00:00:00Z", reviewed_at: null, reviewed_by: null, new_end_date: null };
const page = { requests: [row], total: 1, limit: 20, offset: 0, license_required: false };
const account = { ...page, eligible: false, unavailable_code: "renewal_pending", plan_id: id, plan_name: "月付套餐", renew_days: 30, plan_expires_at: row.previous_end_date };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "ADMIN-CSRF" };
  subscriberState.session = { authenticated: true, username: "alice", csrf_token: "USER-CSRF", requires_2fa: false, challenge: null };
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("renewal HTTP requests", () => {
  it("uses subscriber cookies/CSRF and excludes user identity and secrets from URLs", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ...row, passphrase: "PRIVATE-EXTRA" }, 201));
    expect(await submitRenewal({ request_id: id, passphrase: "PRIVATE-REFERENCE" }, fetcher)).toEqual(row);
    const [path, init] = fetcher.mock.calls[0]!;
    expect(path).toBe("/api/v1/account/renewals"); expect(init?.credentials).toBe("include"); expect(init?.cache).toBe("no-store");
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("USER-CSRF");
    expect(JSON.parse(String(init?.body))).toEqual({ request_id: id, passphrase: "PRIVATE-REFERENCE" });
    expect(String(path)).not.toContain("PRIVATE");
    expect(await getAccountRenewals(0, vi.fn<typeof fetch>().mockResolvedValue(response(account)))).toEqual(account);
  });
  it("uses administrator authentication for an explicit, confirmed review", async () => {
    const approved = { ...row, status: "approved", new_end_date: "2026-10-30T00:00:00Z" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ request: approved, processed: true, commands: [{ body: "PRIVATE-CREDENTIAL" }], warnings: [], license_required: false }));
    vi.stubGlobal("fetch", fetcher);
    const result = await reviewRenewal(id, { decision: "approve", confirm_reviewed: true, passphrase: "PRIVATE" });
    expect(result.command_count).toBe(1); expect(JSON.stringify(result)).not.toContain("PRIVATE");
    expect(new Headers(fetcher.mock.calls[0]![1]?.headers).get("X-CSRF-Token")).toBe("ADMIN-CSRF");
    expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual({ decision: "approve", confirm_reviewed: true, passphrase: "PRIVATE" });
  });
  it("rejects malformed receipts, different IDs and unsafe routes without exposing inputs", async () => {
    for (const value of [{ ...row, id: "11234567-89ab-4cde-8fab-0123456789ab" }, { ...row, status: "PRIVATE" }, { ...row, renew_days: "30" }, { ...row, created_at: "not-a-time" }]) {
      await expect(getAccountRenewal(id, vi.fn<typeof fetch>().mockResolvedValue(response(value)))).rejects.toBeInstanceOf(RenewalRequestError);
    }
    const fetcher = vi.fn<typeof fetch>();
    expect(() => cancelRenewal("../../PRIVATE", fetcher)).toThrow(RenewalRequestError); expect(fetcher).not.toHaveBeenCalled();
    expect(() => submitRenewal({ request_id: id, passphrase: "a\nb" }, fetcher)).toThrow();
    const duplicate = { ...page, requests: [row, row] };
    await expect(listRenewals("all", 0, vi.fn<typeof fetch>().mockResolvedValue(response(duplicate)))).rejects.toBeInstanceOf(RenewalRequestError);
  });
  it("keeps error messages fixed and never retries an uncertain request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("PRIVATE disconnected"));
    const error = await submitRenewal({ request_id: id, passphrase: "PRIVATE" }, fetcher).catch(value => value);
    expect(error.outcomeUnknown).toBe(true); expect(fetcher).toHaveBeenCalledOnce();
    error.message = "PRIVATE mutation"; expect(renewalErrorMessage(error)).not.toContain("PRIVATE");
    const denied = await getAccountRenewal(id, vi.fn<typeof fetch>().mockResolvedValue(response({ code: "renewal_not_found", detail: "PRIVATE" }, 404))).catch(value => value);
    expect(renewalErrorMessage(denied)).toContain("未找到续费申请"); expect(renewalErrorMessage(denied)).not.toContain("PRIVATE");
  });
});
