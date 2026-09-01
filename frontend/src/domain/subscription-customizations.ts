export type CustomRuleType = "dns" | "rules" | "rule-providers";
export type CustomRuleMode = "replace" | "prepend" | "append";

export interface CustomRule {
  id: string;
  owner_username: string;
  name: string;
  type: CustomRuleType;
  mode: CustomRuleMode;
  content: string;
  enabled: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface CustomRuleWrite {
  owner_username: string;
  name: string;
  type: CustomRuleType;
  mode: CustomRuleMode;
  content: string;
  enabled: boolean;
}

export interface ProxyProvider {
  id: string;
  owner_username: string;
  external_source_id: string;
  name: string;
  type: "http";
  interval: number;
  proxy: string;
  size_limit: number;
  health_check_enabled: boolean;
  health_check_url: string;
  health_check_interval: number;
  health_check_timeout: number;
  health_check_lazy: boolean;
  health_check_expected_status: number;
  filter: string;
  exclude_filter: string;
  exclude_type: string;
  override: Record<string, unknown>;
  process_mode: "client";
  enabled: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export type ProxyProviderWrite = Omit<
  ProxyProvider,
  "id" | "revision" | "created_at" | "updated_at"
>;

export interface CustomRulesResponse { rules: CustomRule[]; license_required: false }
export interface ProxyProvidersResponse { providers: ProxyProvider[]; license_required: false }
