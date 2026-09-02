import type { CamouflagePoolCatalog, CamouflageRegion } from "../domain/camouflage";
import { authenticatedFetch } from "./auth";
import { requestError } from "./request-error";

const base = import.meta.env.VITE_API_BASE_URL ?? "";

export async function listCamouflagePools(
  region?: CamouflageRegion,
  fetcher = authenticatedFetch,
): Promise<CamouflagePoolCatalog> {
  const query = region ? `?region=${encodeURIComponent(region)}` : "";
  const response = await fetcher(`${base}/api/v1/camouflage-pools${query}`);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw requestError(data?.detail, `获取伪装池失败（${response.status}）`);
  }
  return response.json() as Promise<CamouflagePoolCatalog>;
}
