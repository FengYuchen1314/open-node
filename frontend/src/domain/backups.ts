export const backupStatuses = ["queued", "running", "ready", "failed", "expired", "cancelled"] as const;
export type BackupStatus = typeof backupStatuses[number];

export const backupErrorCodes = [
  "backup_not_found", "backup_busy", "backup_not_ready", "backup_request_conflict",
  "backup_worker_unavailable", "backup_authorization_expired", "backup_creation_failed",
  "backup_expired", "backup_invalid_request", "backup_rate_limited",
] as const;
export type BackupErrorCode = typeof backupErrorCodes[number];
export type BackupDisplayCode = BackupErrorCode | "backup_unknown_error";

export interface BackupJob {
  id: string;
  status: BackupStatus;
  created_at: string;
  expires_at: string;
  size: number | null;
  sha256: string | null;
  error_code: BackupDisplayCode | null;
  restoration_ready: false;
}

export interface BackupsOverview {
  available: boolean;
  unavailable_code: BackupDisplayCode | null;
  jobs: BackupJob[];
  max_completed: 2;
  ttl_seconds: 900;
  requires_two_factor: boolean;
  restoration_supported: false;
}

export interface BackupCreateRequest {
  request_id: string;
  recipient: string;
  password: string;
  code: string;
}

export const validBackupId = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);

/** Only the native public-key shape; the official age tool verifies its checksum. */
export const validBackupRecipient = (value: unknown): value is string => typeof value === "string"
  && /^age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58}$/.test(value);

export const backupInProgress = (job: BackupJob) => job.status === "queued" || job.status === "running";
