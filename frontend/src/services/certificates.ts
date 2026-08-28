import { authenticatedFetch } from "./auth";

export type CertificateChallenge = "dns" | "standalone" | "webroot";

export interface DNSProvider {
  id: string;
  name: string;
  provider: string;
  credential_fields: string[];
}

export interface ManagedCertificate {
  id: string;
  name: string;
  domains: string[];
  email: string | null;
  provider_id: string | null;
  validation_server_id?: string | null;
  directory_url: string | null;
  challenge_type: CertificateChallenge;
  webroot_id: string | null;
  status: string;
  auto_renew: boolean;
  active_job_id: string | null;
  version_id: string | null;
  expires_at: number | null;
  last_error: string | null;
}

export interface CertificateCapabilities {
  available: boolean;
  account_management: boolean;
  revocation: boolean;
  remote_http_available?: boolean;
  validation_nodes?: Array<{ id: string; name: string; version: 1; standalone: boolean; webroots: string[]; cleanup_error: string | null }>;
  directories: string[];
  challenge_types: CertificateChallenge[];
  webroots: string[];
  providers: Array<{ id: string; fields: string[]; required: string[] }>;
}

export interface CertificateVersion {
  id: string;
  created_at: number;
  details: { serial: string; issuer: string; expires_at: number };
  revocation: { status: "pending" | "unknown" | "revoked"; reason: number; confirmed_at: number | null; directory_url: string } | null;
}

export interface CertificateDetail {
  certificate: ManagedCertificate;
  account: { email: string; state: string; uri: string | null; eab_configured: boolean; pending_email: string | null; retry_job_id: string | null } | null;
  versions: CertificateVersion[];
  jobs: Array<{ id: string; kind: string; status: string; message: string | null; created_at: number; cleanup_pending?: boolean }>;
  targets: Array<{ id: string; server_id: string; domain: string; cert_name: string; status: string; error: string | null; auto_deploy: boolean }>;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function certificateRequest<T>(path = "", method = "GET", body?: unknown): Promise<T> {
  const response = await authenticatedFetch(`${apiBaseUrl}/api/v1/certificates${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === "string" ? data.detail : "Invalid certificate request";
    throw new Error(message);
  }
  return data as T;
}
