import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { defaultBranding, normalizeBrandingText, validBrandingRevision, type BrandingSettings, type PublicBranding } from "../../domain/branding";
import { getPublicBranding } from "../../services/branding";

interface BrandingContextValue {
  branding: Readonly<PublicBranding>;
  captureRead: () => number;
  acceptRead: (value: BrandingSettings, generation: number) => boolean;
  acceptSaved: (value: BrandingSettings) => boolean;
}

// Independently mounted views remain usable and never start an implicit request.
const BrandingContext = createContext<BrandingContextValue>({
  branding: defaultBranding, captureRead: () => 0, acceptRead: () => true, acceptSaved: () => true,
});

function displayValues(value: PublicBranding): PublicBranding | null {
  if (!value || value.license_required !== false) return null;
  const site = normalizeBrandingText(value.site_title, 80), brand = normalizeBrandingText(value.brand_title, 40);
  return site === null || brand === null || site !== value.site_title || brand !== value.brand_title ? null : { site_title: site, brand_title: brand, license_required: false };
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBranding] = useState<Readonly<PublicBranding>>(defaultBranding);
  const current = useRef({ mounted: true, generation: 0, revision: -1, branding: defaultBranding });
  const pending = useRef<Promise<PublicBranding> | null>(null);
  useEffect(() => {
    let active = true;
    current.current.mounted = true;
    const generation = current.current.generation;
    pending.current ??= getPublicBranding();
    void pending.current.then(value => {
      if (!active || current.current.generation !== generation) return;
      const next = displayValues(value);
      if (next) { current.current.branding = next; setBranding(next); }
    }).catch(() => { /* Defaults keep both login forms available; never display a response body. */ });
    return () => { active = false; current.current.mounted = false; };
  }, []);

  const captureRead = useCallback(() => current.current.generation, []);
  const acceptSaved = useCallback((value: BrandingSettings) => {
    const state = current.current, next = displayValues(value);
    if (!state.mounted || !next || !validBrandingRevision(value.revision) || value.revision < state.revision
      || (value.revision === state.revision && (next.site_title !== state.branding.site_title || next.brand_title !== state.branding.brand_title))) return false;
    state.generation += 1; state.revision = value.revision; state.branding = next; setBranding(next);
    return true;
  }, []);
  const acceptRead = useCallback((value: BrandingSettings, generation: number) => {
    if (generation !== current.current.generation) return false;
    return acceptSaved(value);
  }, [acceptSaved]);
  const context = useMemo(() => ({ branding, captureRead, acceptRead, acceptSaved }), [branding, captureRead, acceptRead, acceptSaved]);
  return <BrandingContext.Provider value={context}>{children}</BrandingContext.Provider>;
}

export function useBranding() { return useContext(BrandingContext); }
