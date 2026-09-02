export interface ServerEgressRoutingSelector {
  domains: string[];
  ips: string[];
  inbound_tags: string[];
  users: string[];
  protocols: string[];
  port?: string | null;
  network?: "tcp" | "udp" | "tcp,udp" | null;
}

export interface ServerEgressPreviewRequest {
  target_node_id: string;
  promote_to_default: boolean;
  routing?: ServerEgressRoutingSelector | null;
  pinned_peer_cert_sha256?: string | null;
}

export interface ServerEgressApplyRequest extends ServerEgressPreviewRequest {
  expected_preview_revision: string;
  command_timeout_ms?: number;
  dispatch?: true;
}

export interface ServerEgressRemovePreviewRequest {
  target_node_id: string;
}

export interface ServerEgressRemoveRequest extends ServerEgressRemovePreviewRequest {
  expected_preview_revision: string;
  command_timeout_ms?: number;
  dispatch?: true;
}

export interface ServerEgressCandidate {
  node_id: string;
  node_name: string;
  server_id: string;
  server_name: string;
  protocol: string;
  available: boolean;
  unavailable_reason?: string | null;
  configured: boolean;
  is_default: boolean;
  has_routing_rule: boolean;
  has_target_client?: boolean;
  needs_repair?: boolean;
  tls_probe?: ServerEgressTlsProbeDescriptor | null;
}

/** Credential-free target selected from the final generated Xray outbound. */
export interface ServerEgressTlsProbeDescriptor {
  protocol: string;
  address: string;
  port: number;
  server_name?: string | null;
  alpn: string[];
}

export interface ServerEgressCatalog {
  server_id: string;
  candidates: ServerEgressCandidate[];
  source_snapshot_id?: string | null;
  source_snapshot_revision?: string | null;
}

export interface ServerEgressPreview {
  source_server_id: string;
  source_server_name: string;
  target_node_id: string;
  target_node_name: string;
  target_server_id: string;
  target_server_name: string;
  protocol: string;
  action: "create" | "update" | "repair" | "remove";
  outbound_tag: string;
  routing_marktag: string;
  promote_to_default: boolean;
  will_be_default: boolean;
  routing_action: "keep" | "set" | "remove";
  routing?: ServerEgressRoutingSelector | null;
  source_snapshot_id: string;
  target_snapshot_id: string;
  preview_revision: string;
  tls_probe?: ServerEgressTlsProbeDescriptor | null;
  pinned_peer_cert_sha256?: string | null;
}

/** The API returns identifiers only; generated command bodies and credentials never reach this UI. */
export interface ServerEgressApplyResponse {
  preview: ServerEgressPreview;
  change_set_id: string;
  change_set_status: string;
  command_ids: string[];
  license_required: false;
}
