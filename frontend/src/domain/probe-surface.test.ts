import { describe, expect, it, vi } from "vitest";

import {
  allowsProbeAdministratorAccess,
  browserProbeAccessToken,
  startProbeSurface,
} from "./probe-surface";

describe("probe surface policy", () => {
  it("starts only read-only public data flows for a public bundle", () => {
    const startup = {
      refreshPublicProbe: vi.fn(),
      refreshPublicTargets: vi.fn(),
      refreshAdministratorTasks: vi.fn(),
      openPublicStream: vi.fn(),
    };

    startProbeSurface(true, startup);

    expect(allowsProbeAdministratorAccess(true)).toBe(false);
    expect(browserProbeAccessToken(true, "browser-secret")).toBeUndefined();
    expect(startup.refreshPublicProbe).toHaveBeenCalledOnce();
    expect(startup.refreshPublicTargets).toHaveBeenCalledOnce();
    expect(startup.refreshAdministratorTasks).not.toHaveBeenCalled();
    expect(startup.openPublicStream).toHaveBeenCalledOnce();
  });

  it("retains administrator task loading in the control-plane surface", () => {
    const startup = {
      refreshPublicProbe: vi.fn(),
      refreshPublicTargets: vi.fn(),
      refreshAdministratorTasks: vi.fn(),
      openPublicStream: vi.fn(),
    };

    startProbeSurface(false, startup);

    expect(allowsProbeAdministratorAccess(false)).toBe(true);
    expect(browserProbeAccessToken(false, " browser-secret ")).toBe("browser-secret");
    expect(startup.refreshAdministratorTasks).toHaveBeenCalledOnce();
  });
});
