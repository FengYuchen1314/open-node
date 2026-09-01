export const applicationUpdateStatuses = [
  "unavailable", "idle", "checking", "current", "available",
  "updating", "succeeded", "failed", "recovery_required",
] as const;

export type ApplicationUpdateStatus = typeof applicationUpdateStatuses[number];

export interface ApplicationUpdateState {
  schema_version: 1;
  managed: boolean;
  status: ApplicationUpdateStatus;
  request_id: string | null;
  current_revision: string;
  latest_revision: string | null;
  has_update: boolean | null;
  checked_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  message: string;
  release_url: string | null;
  license_required: false;
}

export interface ApplicationUpdateAccepted {
  accepted: true;
  request_id: string;
  action: "check" | "apply";
  license_required: false;
}
