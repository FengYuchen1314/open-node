export type TemporarySubscriptionStatus = "active" | "expired" | "exhausted";

export interface TemporarySubscription {
  id: string;
  username: string;
  label: string;
  node_ids: string[];
  max_access: number;
  access_count: number;
  expires_at: string;
  status: TemporarySubscriptionStatus;
  subscription_url: string;
  created_at: string;
  updated_at: string;
}

export interface TemporarySubscriptionCreate {
  username: string;
  label: string;
  node_ids: string[];
  max_access: number;
  expires_in_seconds: number;
}

export interface TemporarySubscriptionsResponse {
  subscriptions: TemporarySubscription[];
  license_required: false;
}
