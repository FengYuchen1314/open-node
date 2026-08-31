export const renewalStatuses = ["pending", "approved", "rejected", "cancelled"] as const;
export type RenewalStatus = typeof renewalStatuses[number];
export interface RenewalRequest {
  id: string;
  username: string;
  plan_id: string;
  plan_name: string;
  previous_end_date: string | null;
  renew_days: number;
  status: RenewalStatus;
  created_at: string;
  reviewed_at: string | null;
  reviewed_by: string | null;
  new_end_date: string | null;
}
export interface RenewalsPage { requests: RenewalRequest[]; total: number; limit: number; offset: number; license_required: false }
export interface AccountRenewals extends RenewalsPage {
  eligible: boolean;
  unavailable_code: string | null;
  plan_id: string | null;
  plan_name: string | null;
  renew_days: number | null;
  plan_expires_at: string | null;
}
export interface RenewalCreate { request_id: string; passphrase: string }
export type RenewalDecision = { decision: "approve"; confirm_reviewed: true; passphrase: string }
  | { decision: "reject"; confirm_reviewed: true };
export interface RenewalReviewResult { request: RenewalRequest; processed: boolean; command_count: number; warnings_count: number }
export const renewalStatusLabels: Record<RenewalStatus, string> = { pending: "待审核", approved: "已通过", rejected: "已拒绝", cancelled: "已撤回" };
export function validRenewalPassphrase(value: string) { return !!value.trim() && [...value.trim()].length <= 256 && !/[\u0000-\u001f\u007f]/.test(value.trim()); }
export const validRenewalId = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
