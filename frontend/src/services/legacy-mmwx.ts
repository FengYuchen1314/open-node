import type {
  LegacyMMWXIdentityBundle,
  LegacyMMWXImportPreview,
  LegacyMMWXImportResponse,
} from "../domain/legacy-mmwx";
import { authenticatedFetch } from "./auth";

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
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((entry: { loc?: unknown[]; msg?: string }) =>
          `${entry.loc?.slice(1).join(".") ?? ""}: ${entry.msg ?? "Invalid value"}`).join("; ")
        : "";
    throw new Error(message || `MMWX migration request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function previewLegacyMMWXIdentities(
  bundle: LegacyMMWXIdentityBundle,
  replaceExisting: boolean,
  fetcher?: typeof fetch,
) {
  return request<LegacyMMWXImportPreview>(
    "/preview",
    { bundle, replace_existing: replaceExisting },
    fetcher ?? authenticatedFetch,
  );
}

export function importLegacyMMWXIdentities(
  bundle: LegacyMMWXIdentityBundle,
  replaceExisting: boolean,
  preview: LegacyMMWXImportPreview,
  confirmedUserCount: number,
  fetcher?: typeof fetch,
) {
  return request<LegacyMMWXImportResponse>(
    "/import",
    {
      bundle,
      replace_existing: replaceExisting,
      expected_revision: preview.revision,
      confirm_user_count: confirmedUserCount,
    },
    fetcher ?? authenticatedFetch,
  );
}
