// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppearanceSettings } from "../../domain/appearance";
import { authState } from "../../services/auth";
import { AppearanceRequestError, getAppearanceSettings, getPublicAppearance, updateAppearance, uploadAppearanceImage } from "../../services/appearance";
import { AppearanceProvider, useAppearance } from "../hooks/useAppearance";
import { deferred, flush, installDom, renderUi } from "../test-utils";
import AppearancePanel from "./AppearancePanel";

vi.mock("../../services/appearance", async original => ({ ...await original<typeof import("../../services/appearance")>(),
  getPublicAppearance: vi.fn(), getAppearanceSettings: vi.fn(), updateAppearance: vi.fn(), uploadAppearanceImage: vi.fn(),
}));
const operator = { configured: true, authenticated: true, username: "admin", csrf_token: "PRIVATE-CSRF" };
const initial: AppearanceSettings = { default_theme: "light", logo_url: "", wallpaper_url: "", license_required: false, revision: 4 };
let saved: AppearanceSettings;
function Shell() { const { appearance } = useAppearance(); return <><output aria-label="当前外观">{JSON.stringify(appearance)}</output><AppearancePanel operator={operator} /></>; }
beforeEach(() => {
  vi.resetAllMocks(); installDom(); localStorage.clear(); saved = { ...initial };
  authState.ready = true; authState.error = ""; authState.session = { ...operator };
  vi.mocked(getPublicAppearance).mockResolvedValue({ ...initial });
  vi.mocked(getAppearanceSettings).mockImplementation(async () => ({ ...saved }));
  vi.mocked(updateAppearance).mockImplementation(async value => {
    const { expected_revision, ...appearance } = value;
    return saved = { ...appearance, revision: expected_revision + 1 };
  });
  vi.mocked(uploadAppearanceImage).mockImplementation(async (slot, revision) => saved = { ...saved, revision: revision + 1, [`${slot}_url`]: `/api/v1/appearance/assets/${slot}/${"a".repeat(64)}` });
});
afterEach(async () => { cleanup(); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount() { const view = renderUi(<AppearanceProvider><Shell /></AppearanceProvider>); await flush(); return view; }
describe("appearance administration", () => {
  it("reads without writing and explains the public image boundary", async () => {
    await mount(); expect(screen.getByLabelText("Logo 地址")).toBeTruthy();
    expect(screen.getByText(/图片和外部地址会公开给访客/)).toBeTruthy();
    expect(updateAppearance).not.toHaveBeenCalled(); expect(uploadAppearanceImage).not.toHaveBeenCalled();
  });
  it("saves normalized public URLs and the standard interface theme", async () => {
    await mount(); fireEvent.click(screen.getByText("深色"));
    fireEvent.change(screen.getByLabelText("Logo 地址"), { target: { value: "  https://cdn.example.test/logo.png  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存外观设置" })); await flush();
    expect(updateAppearance).toHaveBeenCalledExactlyOnceWith({ expected_revision: 4, default_theme: "dark", logo_url: "https://cdn.example.test/logo.png", wallpaper_url: "", license_required: false });
    expect(screen.getByText("外观设置已保存。")).toBeTruthy();
    expect(screen.getByLabelText("当前外观").textContent).toContain('"default_theme":"dark"');
  });
  it("uploads selected bytes once, clears the selection and accepts only the confirmed URL", async () => {
    const pending = deferred<AppearanceSettings>(); vi.mocked(uploadAppearanceImage).mockReturnValue(pending.promise);
    await mount(); const file = new File(["image"], "PRIVATE-NAME.png", { type: "image/png" });
    const input = document.querySelector('input[type="file"]')!; fireEvent.change(input, { target: { files: [file] } }); await flush();
    const uploadButton = screen.getByRole("button", { name: "上传并启用 Logo" });
    fireEvent.click(uploadButton); fireEvent.click(uploadButton); await flush();
    expect(uploadAppearanceImage).toHaveBeenCalledExactlyOnceWith("logo", 4, file);
    expect(document.body.textContent).not.toContain("PRIVATE-NAME");
    await act(async () => pending.resolve({ ...initial, revision: 5, logo_url: `/api/v1/appearance/assets/logo/${"a".repeat(64)}` }));
    expect(screen.getByText("图片已上传并启用。")).toBeTruthy();
  });
  it("rejects excessive selected files before sending", async () => {
    await mount(); const file = new File([new Uint8Array(2 * 1024 * 1024 + 1)], "large.png");
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } }); await flush();
    expect(screen.getByText("图片不能超过 2 MiB。")).toBeTruthy(); expect(uploadAppearanceImage).not.toHaveBeenCalled();
  });
  it("reconciles an unknown outcome with one GET and never retries the write", async () => {
    vi.mocked(updateAppearance).mockRejectedValue(new AppearanceRequestError(null));
    vi.mocked(getAppearanceSettings).mockResolvedValueOnce(initial).mockResolvedValueOnce({ ...initial, default_theme: "dark", revision: 5 });
    await mount(); fireEvent.click(screen.getByText("深色")); fireEvent.click(screen.getByRole("button", { name: "保存外观设置" })); await flush();
    expect(updateAppearance).toHaveBeenCalledOnce(); expect(getAppearanceSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/已重新读取，请核对/)).toBeTruthy();
  });
  it("closes saving after a conflict if rereading also fails", async () => {
    vi.mocked(updateAppearance).mockRejectedValue(new AppearanceRequestError(409, "appearance_revision_conflict"));
    vi.mocked(getAppearanceSettings).mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error("PRIVATE"));
    await mount(); fireEvent.click(screen.getByText("深色")); fireEvent.click(screen.getByRole("button", { name: "保存外观设置" })); await flush();
    expect((screen.getByRole("button", { name: "保存外观设置" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/重新读取仍未成功/)).toBeTruthy(); expect(document.body.textContent).not.toContain("PRIVATE");
  });
});
