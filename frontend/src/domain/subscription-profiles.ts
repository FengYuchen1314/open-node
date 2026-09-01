export interface SubscriberSubscriptionProfile {
  id: string;
  name: string;
  description: string;
  subscription_url: string;
  short_code: string;
  enabled: boolean;
  expires_at: string | null;
  warnings: string[];
}

export interface SubscriberSubscriptionProfilesResponse {
  profiles: SubscriberSubscriptionProfile[];
  license_required: false;
}

export interface SubscriptionProfile {
  id: string;
  owner_username: string;
  assigned_usernames: string[];
  revision: string;
  name: string;
  description: string;
  node_ids: string[];
  clash_template_id: string | null;
  surge_template_id: string | null;
  custom_rules_enabled: boolean;
  selected_custom_rule_ids: string[];
  proxy_providers_enabled: boolean;
  selected_proxy_provider_ids: string[];
  override_scripts_enabled: boolean;
  selected_override_script_ids: string[];
  enabled: boolean;
  sort_order: number;
  source_type: string;
  source_filename: string;
  source_template_filename: string;
  legacy_source_id: number | null;
  legacy_file_short_code: string | null;
  legacy_custom_short_code: string | null;
  migration_warnings: string[];
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionProfilesResponse {
  profiles: SubscriptionProfile[];
  license_required: false;
}

export interface SubscriptionProfileUpdate {
  name: string;
  description: string;
  node_ids: string[];
  clash_template_id: string | null;
  surge_template_id: string | null;
  custom_rules_enabled: boolean;
  selected_custom_rule_ids: string[];
  proxy_providers_enabled: boolean;
  selected_proxy_provider_ids: string[];
  override_scripts_enabled: boolean;
  selected_override_script_ids: string[];
  assigned_usernames: string[];
  enabled: boolean;
  expected_revision: string;
}
