import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { defaultAppearance, validImageUrl, validRevision, type AppearanceSettings, type PublicAppearance, type ThemePreference } from "../../domain/appearance";
import { getPublicAppearance } from "../../services/appearance";

const KEY = "open-node-theme-preference";
interface Value {
  appearance: Readonly<PublicAppearance>; preference: ThemePreference; dark: boolean;
  setPreference: (value: ThemePreference) => void; captureRead: () => number;
  acceptRead: (value: AppearanceSettings, generation: number) => boolean;
  acceptSaved: (value: AppearanceSettings) => boolean;
}
const Context = createContext<Value>({ appearance: defaultAppearance, preference: "site", dark: false,
  setPreference: () => {}, captureRead: () => 0, acceptRead: () => true, acceptSaved: () => true });
function safe(value: PublicAppearance): PublicAppearance | null {
  return value && value.license_required === false && ["light", "dark", "system"].includes(value.default_theme)
    && validImageUrl(value.logo_url, "logo") && validImageUrl(value.wallpaper_url, "wallpaper") ? value : null;
}
function stored(): ThemePreference {
  try { const value = localStorage.getItem(KEY); return value && ["site", "light", "dark", "system"].includes(value) ? value as ThemePreference : "site"; }
  catch { return "site"; }
}
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Readonly<PublicAppearance>>(defaultAppearance);
  const [preference, savePreference] = useState<ThemePreference>(stored);
  const [systemDark, setSystemDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  const state = useRef({ mounted: true, generation: 0, revision: -1, appearance: defaultAppearance });
  const pending = useRef<Promise<PublicAppearance> | null>(null);
  useEffect(() => {
    const query = matchMedia("(prefers-color-scheme: dark)");
    const change = () => setSystemDark(query.matches); change(); query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    let active = true; state.current.mounted = true; const generation = state.current.generation;
    pending.current ??= getPublicAppearance();
    void pending.current.then(value => { const valueSafe = safe(value); if (active && generation === state.current.generation && valueSafe) { state.current.appearance = valueSafe; setAppearance(valueSafe); } }).catch(() => {});
    return () => { active = false; state.current.mounted = false; };
  }, []);
  const setPreference = useCallback((value: ThemePreference) => {
    if (!["site", "light", "dark", "system"].includes(value)) return;
    savePreference(value); try { localStorage.setItem(KEY, value); } catch { /* Theme remains usable in memory. */ }
  }, []);
  const acceptSaved = useCallback((value: AppearanceSettings) => {
    const next = safe(value), current = state.current;
    if (!next || !current.mounted || !validRevision(value.revision) || value.revision < current.revision
      || (value.revision === current.revision && JSON.stringify(next) !== JSON.stringify(current.appearance))) return false;
    current.generation += 1; current.revision = value.revision; current.appearance = next; setAppearance(next); return true;
  }, []);
  const captureRead = useCallback(() => state.current.generation, []);
  const acceptRead = useCallback((value: AppearanceSettings, generation: number) => generation === state.current.generation && acceptSaved(value), [acceptSaved]);
  const selected = preference === "site" ? appearance.default_theme : preference;
  const dark = selected === "dark" || (selected === "system" && systemDark);
  return <Context.Provider value={useMemo(() => ({ appearance, preference, dark, setPreference, captureRead, acceptRead, acceptSaved }), [appearance, preference, dark, setPreference, captureRead, acceptRead, acceptSaved])}>{children}</Context.Provider>;
}
export function useAppearance() { return useContext(Context); }
