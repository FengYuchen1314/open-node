// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultBranding, type BrandingSettings, type PublicBranding } from "../../domain/branding";
import { getPublicBranding } from "../../services/branding";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import { BrandingProvider, useBranding } from "./useBranding";

vi.mock("../../services/branding", async original => ({ ...await original<typeof import("../../services/branding")>(), getPublicBranding: vi.fn() }));
vi.mock("../../services/probe-public", () => ({ getPublicProbePayload: vi.fn(), getPublicProbeTargets: vi.fn(), getPublicProbeSeries: vi.fn(), getPublicProbeStreamUrl: vi.fn() }));
const saved: BrandingSettings = { site_title: "新站点", brand_title: "新品牌", revision: 5, license_required: false };
function Consumer() {
  const { branding, acceptRead, acceptSaved, captureRead } = useBranding();
  const captured = useRef(captureRead());
  return <div><output aria-label="brand">{branding.brand_title}</output><output aria-label="site">{branding.site_title}</output>
    <button onClick={() => acceptSaved(saved)}>save</button>
    <button onClick={() => acceptRead({ ...saved, brand_title: "旧名称", revision: 4 }, captured.current)}>late read</button>
    <button onClick={() => acceptRead({ ...saved, brand_title: "更旧名称", revision: 3 }, captureRead())}>older revision</button>
    <button onClick={() => acceptSaved({ ...saved, brand_title: "同版本不一致" })}>same revision</button>
    <button onClick={() => acceptSaved({ ...saved, revision: 6, brand_title: "PRIVATE\u202e" })}>invalid save</button>
  </div>;
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); sessionStorage.clear();
  vi.mocked(getPublicBranding).mockResolvedValue({ ...defaultBranding });
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network request"); }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("isolated branding provider", () => {
  it("gives standalone views safe defaults without any implicit fetch", async () => {
    render(<Consumer />); await flush();
    expect(screen.getByLabelText("brand").textContent).toBe("Open Node");
    expect(getPublicBranding).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
  it("deduplicates the anonymous read during StrictMode replay", async () => {
    const pending = deferred<PublicBranding>(); vi.mocked(getPublicBranding).mockReturnValue(pending.promise);
    render(<StrictMode><BrandingProvider><Consumer /></BrandingProvider></StrictMode>); await flush();
    expect(getPublicBranding).toHaveBeenCalledOnce();
    await act(async () => pending.resolve({ site_title: "自定义标题", brand_title: "自定义品牌", license_required: false }));
    expect(screen.getByLabelText("site").textContent).toBe("自定义标题");
  });
  it("does not let a late public read overwrite a confirmed save", async () => {
    const pending = deferred<PublicBranding>(); vi.mocked(getPublicBranding).mockReturnValue(pending.promise);
    render(<BrandingProvider><Consumer /></BrandingProvider>); fireEvent.click(screen.getByText("save"));
    await act(async () => pending.resolve({ ...defaultBranding }));
    expect(screen.getByLabelText("brand").textContent).toBe(saved.brand_title);
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
  it("rejects stale admin reads, older versions, conflicting equal versions and malformed saves", async () => {
    render(<BrandingProvider><Consumer /></BrandingProvider>); await flush(); fireEvent.click(screen.getByText("save"));
    for (const name of ["late read", "older revision", "same revision", "invalid save"]) {
      fireEvent.click(screen.getByText(name));
      expect(screen.getByLabelText("brand").textContent).toBe(saved.brand_title);
    }
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it.each([new Error("PRIVATE provider body"), null])("keeps defaults after an unavailable or malformed public response: %j", async failure => {
    if (failure) vi.mocked(getPublicBranding).mockRejectedValue(failure);
    else vi.mocked(getPublicBranding).mockResolvedValue({ ...defaultBranding, brand_title: "PRIVATE\u202e" });
    render(<BrandingProvider><Consumer /></BrandingProvider>); await flush();
    expect(screen.getByLabelText("brand").textContent).toBe("Open Node");
    expect(document.body.textContent).not.toContain("PRIVATE");
  });
  it("does not reset a save to defaults when the old public read fails", async () => {
    const pending = deferred<PublicBranding>(); vi.mocked(getPublicBranding).mockReturnValue(pending.promise);
    render(<BrandingProvider><Consumer /></BrandingProvider>); fireEvent.click(screen.getByText("save"));
    await act(async () => pending.reject(new Error("PRIVATE")));
    expect(screen.getByLabelText("site").textContent).toBe(saved.site_title);
  });
  it("discards reads from an unmounted provider without contaminating its replacement", async () => {
    const first = deferred<PublicBranding>(); vi.mocked(getPublicBranding).mockReturnValueOnce(first.promise);
    const view = render(<BrandingProvider><Consumer /></BrandingProvider>); view.unmount();
    render(<BrandingProvider><Consumer /></BrandingProvider>); await flush();
    await act(async () => first.resolve({ ...defaultBranding, brand_title: "旧窗口品牌" }));
    expect(screen.getByLabelText("brand").textContent).toBe("Open Node"); expect(getPublicBranding).toHaveBeenCalledTimes(2);
  });
  it("leaves the separate public-only Probe surface and its title independent of branding requests", async () => {
    installDom(); vi.stubGlobal("WebSocket", undefined);
    const probe = await import("../../services/probe-public");
    vi.mocked(probe.getPublicProbePayload).mockResolvedValue({ enabled: true, title: "探针自己的标题", servers: [], refresh_interval_sec: 60, license_required: false });
    vi.mocked(probe.getPublicProbeTargets).mockResolvedValue({ success: true, targets: [], license_required: false });
    const { default: ProbeView } = await import("../views/ProbeView");
    document.title = "Public Probe";
    renderUi(<ProbeView publicOnly />); await flush();
    expect(screen.getByRole("heading", { name: "探针自己的标题" })).toBeTruthy();
    expect(document.title).toBe("Public Probe"); expect(getPublicBranding).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
});
