import { Image, Select } from "../../ui";
import { useState } from "react";
import { useAppearance } from "../hooks/useAppearance";

export function SiteLogo({ compact = false }: { compact?: boolean }) {
  const { appearance } = useAppearance(); const [failed, setFailed] = useState("");
  return appearance.logo_url && failed !== appearance.logo_url
    ? <Image preview={false} src={appearance.logo_url} alt="站点 Logo" className={compact ? "site-logo compact" : "site-logo"} crossOrigin="anonymous" referrerPolicy="no-referrer" onError={() => setFailed(appearance.logo_url)} /> : null;
}
export function LoginWallpaper() {
  const { appearance } = useAppearance(); const [failed, setFailed] = useState("");
  return appearance.wallpaper_url && failed !== appearance.wallpaper_url
    ? <img src={appearance.wallpaper_url} alt="" className="auth-wallpaper" crossOrigin="anonymous" referrerPolicy="no-referrer" aria-hidden onError={() => setFailed(appearance.wallpaper_url)} /> : null;
}
export function ThemeSelector() {
  const { preference, setPreference } = useAppearance();
  return <Select aria-label="页面主题" size="small" value={preference} onChange={setPreference} options={[
    { value: "site", label: "站点默认" }, { value: "light", label: "浅色" },
    { value: "dark", label: "深色" }, { value: "system", label: "跟随系统" },
  ]} />;
}
