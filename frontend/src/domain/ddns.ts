export interface DDNSProvider {
  id: string;
  name: string;
  provider: string;
  supported: boolean;
}

export interface DDNSServer {
  server_id: string;
  server_name: string;
  server_status: string;
  is_federated: boolean;
  enabled: boolean;
  provider_id: string | null;
  provider_name: string | null;
  provider_type: string | null;
  pull_address: string | null;
  pull_address_v6: string | null;
  ip_address: string | null;
  ip_address_v6: string | null;
  ipv6_enabled: boolean;
  last_synced_at: string | null;
  last_error: string | null;
  pending: boolean;
  revision: number;
  license_required: false;
}

export interface DDNSWorkspace {
  servers: DDNSServer[];
  providers: DDNSProvider[];
  license_required: false;
}

export interface DDNSConfigInput {
  enabled: boolean;
  provider_id: string | null;
  pull_address: string | null;
  pull_address_v6: string | null;
  expected_revision: number;
}
