// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getPublicAppearance } from "../../services/appearance";
import { deferred, flush, installDom } from "../test-utils";
import { AppearanceProvider, useAppearance } from "./useAppearance";

vi.mock("../../services/appearance", async original => ({ ...await original<typeof import("../../services/appearance")>(), getPublicAppearance: vi.fn() }));
const publicValue = { default_theme: "dark" as const, logo_url: "https://cdn.example.test/logo.png", wallpaper_url: "", license_required: false as const };
function Consumer() { const value = useAppearance(); return <><output aria-label="appearance">{JSON.stringify({ ...value.appearance, dark: value.dark, preference: value.preference })}</output><button onClick={() => value.setPreference("light")}>light</button><button onClick={() => value.acceptSaved({ ...publicValue, default_theme: "system", revision: 4 })}>save</button></>; }
beforeEach(() => { vi.resetAllMocks(); installDom(); localStorage.clear(); vi.mocked(getPublicAppearance).mockResolvedValue(publicValue); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("appearance context", () => {
  it("deduplicates the public read in StrictMode and applies the site default", async () => {
    render(<StrictMode><AppearanceProvider><Consumer /></AppearanceProvider></StrictMode>); await flush();
    expect(getPublicAppearance).toHaveBeenCalledOnce(); expect(screen.getByLabelText("appearance").textContent).toContain('"dark":true');
  });
  it("stores only the local theme choice and never stores public image settings", async () => {
    render(<AppearanceProvider><Consumer /></AppearanceProvider>); await flush(); fireEvent.click(screen.getByText("light"));
    expect(screen.getByLabelText("appearance").textContent).toContain('"dark":false');
    expect(localStorage.getItem("open-node-theme-preference")).toBe("light");
    expect(JSON.stringify(localStorage)).not.toContain("cdn.example.test");
  });
  it("fences a late public response after a confirmed administrator save", async () => {
    const pending = deferred<typeof publicValue>(); vi.mocked(getPublicAppearance).mockReturnValue(pending.promise);
    render(<AppearanceProvider><Consumer /></AppearanceProvider>); fireEvent.click(screen.getByText("save"));
    await act(async () => pending.resolve(publicValue));
    expect(screen.getByLabelText("appearance").textContent).toContain('"default_theme":"system"');
  });
});
