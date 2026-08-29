export interface LegacyMMWXIdentity {
  username: string;
  password_hash: string;
  email: string | null;
  display_name: string | null;
  source_role: "user" | "admin";
  is_active: boolean;
  totp_enabled: boolean;
  totp_secret: string | null;
  recovery_code_hashes: string[];
  token: string | null;
  generated_short_code: string | null;
  custom_short_code: string | null;
  created_at: string | null;
}

export interface LegacyMMWXIdentityBundle {
  version: 1;
  source_revision?: string | null;
  users: LegacyMMWXIdentity[];
}

export interface LegacyMMWXImportPreview {
  revision: string;
  ready: boolean;
  total_users: number;
  new_users: number;
  existing_users: number;
  imported_accounts: number;
  replaced_accounts: number;
  skipped_accounts: number;
  imported_tokens: number;
  replaced_tokens: number;
  skipped_tokens: number;
  imported_totp: number;
  blockers: string[];
  warnings: string[];
  license_required: false;
}

export interface LegacyMMWXImportResponse {
  preview: LegacyMMWXImportPreview;
  applied: true;
}
