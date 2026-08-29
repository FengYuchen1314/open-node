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
  source_package_id?: number | null;
  package_started_at?: string | null;
  package_expires_at?: string | null;
  is_reset?: boolean;
  reset_day?: number;
  created_at: string | null;
}

export interface LegacyMMWXPackage {
  source_id: number;
  name: string;
  short_code: string | null;
}

export interface LegacyMMWXSubscriptionProfile {
  source_id: number;
  owner_username: string;
  name: string;
  description: string;
  source_type: "create" | "import" | "upload" | "package";
  filename: string;
  template_filename: string;
  file_short_code: string;
  custom_short_code: string | null;
  selected_tags: string[];
  selected_node_ids: number[];
  selected_custom_rule_ids: number[];
  selected_override_script_ids: number[];
  raw_output: boolean;
  sort_order: number;
  expires_at: string | null;
  assigned_usernames: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface LegacyMMWXIdentityBundle {
  version: 1;
  source_revision?: string | null;
  users: LegacyMMWXIdentity[];
  packages?: LegacyMMWXPackage[];
  subscription_profiles?: LegacyMMWXSubscriptionProfile[];
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
  mapped_packages: number;
  assigned_plans: number;
  imported_profiles: number;
  replaced_profiles: number;
  skipped_profiles: number;
  imported_profile_assignments: number;
  blockers: string[];
  warnings: string[];
  license_required: false;
}

export interface LegacyMMWXImportResponse {
  preview: LegacyMMWXImportPreview;
  applied: true;
}
