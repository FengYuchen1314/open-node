/** Write-only credentials deliberately do not appear in any read contract. */
export interface ExternalSourceCreate {
  owner_username: string;
  name: string;
  url: string;
  user_agent?: string;
  enabled?: boolean;
}

export interface ExternalRevisionRequest { expected_revision: number }

export interface ExternalSourceUpdate extends ExternalRevisionRequest {
  name: string;
  enabled: boolean;
  /** null preserves the saved URL. */
  url?: string | null;
  /** null preserves the saved agent; an empty string restores the default. */
  user_agent?: string | null;
}

export interface ExternalSourceDelete extends ExternalRevisionRequest { confirm: boolean }
export interface ExternalNodeUpdate extends ExternalRevisionRequest { name: string; enabled: boolean }

export interface ExternalSourceRead {
  id: string;
  owner_username: string;
  name: string;
  enabled: boolean;
  revision: number;
  has_custom_user_agent: boolean;
  node_count: number;
  available_node_count: number;
  metadata: Record<string, number>;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExternalSourcesResponse { sources: ExternalSourceRead[]; license_required: false }

export interface ExternalNodeRead {
  id: string;
  source_id: string;
  upstream_name: string;
  name: string;
  protocol: string;
  enabled: boolean;
  present: boolean;
  available: boolean;
  reason: string | null;
}

export interface ExternalSourceDetail {
  source: ExternalSourceRead;
  nodes: ExternalNodeRead[];
  license_required: false;
}

export type ExternalNodeChange = "new" | "updated" | "unchanged" | "missing" | "unavailable";

export interface ExternalPreviewNode {
  node_id: string;
  upstream_name: string;
  name: string;
  protocol: string;
  change: ExternalNodeChange;
  existing: boolean;
  selectable: boolean;
  reason: string | null;
  changed_fields: string[];
}

export interface ExternalConfirmationRead {
  source_id: string;
  preview_id: string;
  revision: number;
  imported_count: number;
  updated_count: number;
  missing_count: number;
  applied_at: string;
}

export interface ExternalPreviewRead {
  id: string;
  source_id: string;
  source_revision: number;
  created_at: string;
  expires_at: string;
  metadata: Record<string, number>;
  nodes: ExternalPreviewNode[];
  receipt: ExternalConfirmationRead | null;
  license_required: false;
}

export interface ExternalPreviewConfirm extends ExternalRevisionRequest {
  selected_node_ids: string[];
  accept_changes: boolean;
}

export interface ExternalSourceDeleteResponse { deleted: true; license_required: false }
export interface ExternalPreviewCancelResponse { cancelled: true; license_required: false }
