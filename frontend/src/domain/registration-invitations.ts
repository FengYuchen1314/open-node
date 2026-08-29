import type { AgentCommand } from "./inventory";
import type { ProductUser, SubscriptionPlan } from "./subscriptions";

export type RegistrationInvitationStatus = "active" | "used" | "revoked" | "expired";

export interface RegistrationInvitation {
  id: string;
  token_hint: string;
  plan_id: string;
  plan_name: string;
  status: RegistrationInvitationStatus;
  used_by: string | null;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface RegistrationInvitationsResponse {
  invitations: RegistrationInvitation[];
  license_required: false;
}

export interface RegistrationInvitationCreate {
  plan_id: string;
  expires_minutes: number;
}

export interface RegistrationInvitationCreateResponse {
  invitation: RegistrationInvitation;
  registration_url: string;
  license_required: false;
}

export interface RegistrationClaim {
  token: string;
  username: string;
  password: string;
  email?: string | null;
  display_name?: string | null;
}

export interface RegistrationClaimResponse {
  user: ProductUser;
  plan: SubscriptionPlan;
  commands: AgentCommand[];
  warnings: string[];
  license_required: false;
}
