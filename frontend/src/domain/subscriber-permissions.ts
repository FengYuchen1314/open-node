export const subscriberFeatures = [
  "templates", "external_subscriptions", "private_routes", "renewals",
] as const;
export type SubscriberFeature = typeof subscriberFeatures[number];

export const subscriberFeatureLabels: Record<SubscriberFeature, string> = {
  templates: "订阅自定义",
  external_subscriptions: "外部订阅来源",
  private_routes: "个人路由节点",
  renewals: "续费申请",
};

export interface SubscriberPermissionsSettings {
  revision: number;
  pages: SubscriberFeature[];
  template_quota: number;
  external_source_quota: number;
  license_required: false;
}

export interface SubscriberPermissionsUpdate {
  expected_revision: number;
  pages: SubscriberFeature[];
  template_quota: number;
  external_source_quota: number;
  license_required: false;
}

export interface SubscriberQuotaUsage { used: number; maximum: number }
export interface SubscriberPermissionsAccount {
  pages: SubscriberFeature[];
  templates: SubscriberQuotaUsage;
  external_sources: SubscriberQuotaUsage;
  license_required: false;
}

export function validPermissionRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function validPermissionQuota(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 1000;
}

export function validSubscriberFeatures(value: unknown): value is SubscriberFeature[] {
  return Array.isArray(value)
    && value.every((item, index) => item === subscriberFeatures.filter(feature => value.includes(feature))[index])
    && value.length === new Set(value).size;
}
