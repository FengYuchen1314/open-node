import { authenticatedFetch } from "./auth";
export interface LicenseStatus {
  edition: "free";
  license_required: false;
  paid_entitlements_enabled: false;
  external_license_server: null;
  feature_gates: string[];
  message: string;
}

export interface AppMeta {
  name: string;
  version: string;
  api_prefix: string;
  license_required: boolean;
  short_links_enabled: boolean;
  stack: Record<string, string>;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchLicenseStatus(fetcher = authenticatedFetch): Promise<LicenseStatus> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/license/status`);
  if (!response.ok) {
    throw new Error(`License status request failed with ${response.status}`);
  }
  return response.json() as Promise<LicenseStatus>;
}

export async function fetchAppMeta(fetcher = authenticatedFetch): Promise<AppMeta> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/meta`);
  if (!response.ok) throw new Error(`Application metadata request failed with ${response.status}`);
  return response.json() as Promise<AppMeta>;
}
