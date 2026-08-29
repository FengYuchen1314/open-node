export type SubscriptionTemplateFormat = "clash" | "surge";

export interface SubscriptionTemplate {
  id: string;
  name: string;
  format: SubscriptionTemplateFormat;
  owner_username: string | null;
  is_public: boolean;
  editable: boolean;
  revision: string;
  content: string | null;
  size_bytes: number;
  plan_names: string[];
  default_scopes: string[];
  created_at: string;
  updated_at: string;
}

export interface SubscriptionTemplateSettings {
  clash_template_id: string | null;
  surge_template_id: string | null;
  enabled: boolean;
  revision: string;
}

export interface SubscriptionTemplateList {
  templates: SubscriptionTemplate[];
  settings: SubscriptionTemplateSettings;
  can_manage: boolean;
  license_required: false;
}

export interface SubscriptionTemplateWrite {
  name: string;
  format: SubscriptionTemplateFormat;
  content: string;
  owner_username: string | null;
  is_public: boolean;
}

export interface SubscriptionTemplatePreview {
  content: string;
  warnings: string[];
  included_nodes: number;
  excluded_nodes: number;
}
