export const backupStatuses = ["queued", "running", "ready", "failed", "expired", "cancelled"] as const;
export type BackupStatus = typeof backupStatuses[number];

export const backupErrorCodes = [
  "backup_not_found", "backup_busy", "backup_not_ready", "backup_request_conflict",
  "backup_worker_unavailable", "backup_authorization_expired", "backup_creation_failed",
  "backup_expired", "backup_invalid_request", "backup_rate_limited",
  "restore_upload_invalid", "restore_upload_not_found", "restore_upload_busy",
  "restore_upload_unavailable", "restore_prepare_failed",
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
  restoration_supported: boolean;
  offline_restoration_supported?: true;
  recovery?: RestoreStatus;
}

export interface RestoreStatus {
  blocked: boolean;
  restart_required: boolean;
  record: {
    version: 1;
    id: string;
    status: "review_required" | "reviewed";
    created_at: string;
    archive_sha256: string;
    invalidated_sessions: number;
    cancelled_agent_commands: number;
    cancelled_certificate_jobs: number;
    quarantined_files: number;
    reviewed_at: string | null;
  } | null;
}
export interface RestoreReviewRequest {
  id: string;
  password: string;
  code: string;
  confirm_original_stopped: true;
  confirm_configuration: true;
  confirm_trusted_backup: true;
}

export interface BackupCreateRequest {
  request_id: string;
  recipient: string;
  password: string;
  code: string;
}

export type RestoreArchiveFormat = "age" | "plain";
export interface RestoreUploadReceipt {
  id: string;
  size: number;
  sha256: string;
  expires_at: string;
  license_required: false;
}
export interface RestorePrepareInput {
  format: RestoreArchiveFormat;
  identity: string;
  subscriber_totp_key: string;
  confirm_replace_instance: true;
  confirm_trusted_backup: true;
}
export interface AdministratorRestorePrepareInput extends RestorePrepareInput {
  password: string;
  code: string;
}
export interface RestorePreparedReceipt {
  id: string;
  restart_required: true;
  automatic_restart: boolean;
  license_required: false;
}

export const validBackupId = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);

/** Only the native public-key shape; the official age tool verifies its checksum. */
export const validBackupRecipient = (value: unknown): value is string => typeof value === "string"
  && /^age1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{58}$/.test(value);

export const backupInProgress = (job: BackupJob) => job.status === "queued" || job.status === "running";
