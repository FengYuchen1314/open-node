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
    throw new Error(`许可证状态请求失败（${response.status}）`);
  }
  return response.json() as Promise<LicenseStatus>;
}

export async function fetchAppMeta(fetcher = authenticatedFetch): Promise<AppMeta> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/meta`);
  if (!response.ok) throw new Error(`应用信息请求失败（${response.status}）`);
  return response.json() as Promise<AppMeta>;
}
