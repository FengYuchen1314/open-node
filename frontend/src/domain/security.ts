export type SecurityEventKind = "probe" | "ban" | "unban" | "ban_manual" | "login_fail" | "login_locked";

export interface SecurityEvent {
  id: number;
  at: string;
  ip: string;
  kind: SecurityEventKind;
  path: string;
  username: string;
  detail: string;
  actor: string;
}

export interface SecurityEvents {
  events: SecurityEvent[];
  offset: number;
  limit: number;
  has_more: boolean;
  license_required: false;
}

export interface SecurityBan {
  ip: string;
  reason: "brute_force" | "manual";
  banned_at: string;
  expires_at: string | null;
  permanent: boolean;
  fail_count: number;
  actor: string;
}

export interface SecuritySettings {
  revision: number;
  brute_force_enabled: boolean;
  brute_force_max_failures: number;
  brute_force_window_minutes: number;
  brute_force_block_minutes: number;
  skip_local_ip: boolean;
  license_required: false;
}
