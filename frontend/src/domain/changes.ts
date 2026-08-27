import type { AgentCommand, AgentCommandCreateRequest } from "./inventory";

export type AgentChangeSetStatus = "planned" | "dispatched" | "rollback_queued";

export interface AgentChangeSetStepCreateRequest {
  server_id: string;
  label?: string;
  forward: AgentCommandCreateRequest;
  rollback?: AgentCommandCreateRequest | null;
}

export interface AgentChangeSetCreateRequest {
  name: string;
  description?: string;
  rollback_on_failure?: boolean;
  dispatch?: boolean;
  steps: AgentChangeSetStepCreateRequest[];
}

export interface AgentChangeSetRollbackRequest {
  reason?: string;
}

export interface AgentChangeSetStep {
  id: string;
  change_set_id: string;
  sequence: number;
  server_id: string;
  label: string;
  forward: AgentCommandCreateRequest;
  rollback?: AgentCommandCreateRequest | null;
  forward_command?: AgentCommand | null;
  rollback_command?: AgentCommand | null;
  created_at: string;
  updated_at: string;
}

export interface AgentChangeSet {
  id: string;
  name: string;
  description: string;
  status: AgentChangeSetStatus;
  rollback_on_failure: boolean;
  rollback_reason: string;
  steps: AgentChangeSetStep[];
  created_at: string;
  updated_at: string;
}

export interface AgentChangeSetsResponse {
  change_sets: AgentChangeSet[];
  license_required: false;
}

export interface AgentChangeSetResponse {
  change_set: AgentChangeSet;
  commands: AgentCommand[];
  warnings: string[];
  license_required: false;
}
