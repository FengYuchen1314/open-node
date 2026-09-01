export type SiteTheme = "light" | "dark" | "system";
export type ThemePreference = "site" | SiteTheme;
export interface PublicAppearance {
  default_theme: SiteTheme;
  logo_url: string;
  wallpaper_url: string;
  license_required: false;
}
export interface AppearanceSettings extends PublicAppearance { revision: number }
export interface AppearanceUpdate extends PublicAppearance { expected_revision: number }
export const defaultAppearance: Readonly<PublicAppearance> = Object.freeze({
  default_theme: "light", logo_url: "", wallpaper_url: "", license_required: false,
});
export function validRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
export function validImageUrl(value: unknown, slot: "logo" | "wallpaper"): value is string {
  if (typeof value !== "string" || value.length > 2000 || /[^\x21-\x7e]/.test(value)) return false;
  if (!value) return true;
  if (new RegExp(`^/api/v1/appearance/assets/${slot}/[a-f0-9]{64}$`).test(value)) return true;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    return url.protocol === "https:" && !url.username && !url.password && !url.hash
      && !value.includes("\\") && (!url.port || url.port === "443")
      && /^[a-z0-9.-]+$/i.test(url.hostname)
      && host !== "localhost" && !host.endsWith(".local");
  } catch { return false; }
}
