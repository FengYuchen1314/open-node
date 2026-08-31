import type {
  LegacyMMWXIdentityBundle,
  LegacyMMWXImportPreview,
  LegacyMMWXImportResponse,
} from "../domain/legacy-mmwx";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
const basePath = `${apiBaseUrl}/api/v1/migrations/mmwx/identities`;

async function request<T>(path: string, payload: unknown, fetcher = authenticatedFetch) {
  const response = await fetcher(basePath + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw requestError(detail, `MMWX 迁移请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function previewLegacyMMWXIdentities(
  bundle: LegacyMMWXIdentityBundle,
  replaceExisting: boolean,
  fetcher?: typeof fetch,
  packageMappings: Record<number, string> = {},
) {
  return request<LegacyMMWXImportPreview>(
    "/preview",
    { bundle, replace_existing: replaceExisting, package_mappings: packageMappings },
    fetcher ?? authenticatedFetch,
  );
}

export function importLegacyMMWXIdentities(
  bundle: LegacyMMWXIdentityBundle,
  replaceExisting: boolean,
  preview: LegacyMMWXImportPreview,
  confirmedUserCount: number,
  fetcher?: typeof fetch,
  packageMappings: Record<number, string> = {},
) {
  return request<LegacyMMWXImportResponse>(
    "/import",
    {
      bundle,
      replace_existing: replaceExisting,
      expected_revision: preview.revision,
      confirm_user_count: confirmedUserCount,
      package_mappings: packageMappings,
    },
    fetcher ?? authenticatedFetch,
  );
}
