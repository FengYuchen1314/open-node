export interface LicenseStatus {
  edition: "free";
  license_required: false;
  paid_entitlements_enabled: false;
  external_license_server: null;
  feature_gates: string[];
  message: string;
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchLicenseStatus(fetcher = fetch): Promise<LicenseStatus> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/license/status`);
  if (!response.ok) {
    throw new Error(`License status request failed with ${response.status}`);
  }
  return response.json() as Promise<LicenseStatus>;
}
