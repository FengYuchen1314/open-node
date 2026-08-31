export interface PublicBranding {
  site_title: string;
  brand_title: string;
  license_required: false;
}

export interface BrandingSettings extends PublicBranding {
  revision: number;
}

export interface BrandingUpdate {
  expected_revision: number;
  site_title: string;
  brand_title: string;
}

export const defaultBranding: Readonly<PublicBranding> = Object.freeze({
  site_title: "Open Node", brand_title: "Open Node", license_required: false,
});

export const brandingErrorCodes = [
  "branding_invalid_request", "branding_revision_conflict", "branding_storage_unavailable",
] as const;
export type BrandingErrorCode = typeof brandingErrorCodes[number];

/** Match the backend's Unicode code-point contract, without normalizing names. */
export function normalizeBrandingText(value: unknown, maximum: 80 | 40): string | null {
  if (typeof value !== "string" || /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(value)
    || /\p{Cf}/u.test(value.replace(/[\u200c\u200d]/gu, ""))) return null;
  // Validate the original string first: trim must not silently swallow controls.
  const text = value.trim();
  if (!text || Array.from(text).length > maximum || !/[\p{L}\p{N}\p{P}\p{S}]/u.test(text)) return null;
  return text;
}

export function validBrandingRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
