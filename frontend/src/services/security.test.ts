import { describe, expect, it, vi } from "vitest";

import type { SecuritySettings } from "../domain/security";
import {
  createSecurityBan,
  loadSecurityBans,
  loadSecurityEvents,
  loadSecuritySettings,
  removeSecurityBan,
  saveSecuritySettings,
} from "./security";

const at = "2026-09-01T00:00:00Z";
const settings: SecuritySettings = {
  revision: 2,
  brute_force_enabled: true,
  brute_force_max_failures: 5,
  brute_force_window_minutes: 1440,
  brute_force_block_minutes: 1440,
  skip_local_ip: true,
  license_required: false,
};
const ban = {
  ip: "1.1.1.1",
  reason: "brute_force" as const,
  banned_at: at,
  expires_at: "2026-09-02T00:00:00Z",
  permanent: false,
  fail_count: 5,
  actor: "",
};
const event = {
  id: 1,
  at,
  ip: "1.1.1.1",
  kind: "ban" as const,
  path: "/api/v1/subscribe/{key}",
  username: "",
  detail: "fail=5",
  actor: "",
};
function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(
    status === 204 ? null : JSON.stringify(value),
    { status, headers: { "Content-Type": "application/json" } },
  ));
}

describe("security management service", () => {
  it("strictly reads settings, bans and paged events", async () => {
    const fetcher = vi.fn()
      .mockImplementationOnce(() => response(settings))
      .mockImplementationOnce(() => response({ bans: [ban], license_required: false }))
      .mockImplementationOnce(() => response({ events: [event], offset: 100, limit: 100, has_more: true, license_required: false }));
    expect(await loadSecuritySettings(fetcher)).toEqual(settings);
    expect(await loadSecurityBans(fetcher)).toEqual([ban]);
    expect(await loadSecurityEvents({ kind: "ban", ip: " 1.1.1.1 ", offset: 100 }, fetcher)).toEqual({
      events: [event], offset: 100, limit: 100, has_more: true, license_required: false,
    });
    expect(String(fetcher.mock.calls[2][0])).toBe("/api/v1/security/events?kind=ban&ip=1.1.1.1&limit=100&offset=100");
  });

  it("writes only the bounded settings and ban contracts", async () => {
    const manual = { ...ban, reason: "manual" as const, permanent: true, expires_at: null, fail_count: 0, actor: "admin" };
    const fetcher = vi.fn()
      .mockImplementationOnce(() => response({ ...settings, revision: 3 }))
      .mockImplementationOnce(() => response(manual, 201))
      .mockImplementationOnce(() => response(null, 204));
    expect((await saveSecuritySettings(settings, fetcher)).revision).toBe(3);
    expect(await createSecurityBan("2001:4860:4860::8888", true, fetcher)).toEqual(manual);
    await removeSecurityBan("2001:4860:4860::8888", fetcher);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      expected_revision: 2,
      brute_force_enabled: true,
      brute_force_max_failures: 5,
      brute_force_window_minutes: 1440,
      brute_force_block_minutes: 1440,
      skip_local_ip: true,
    });
    expect(fetcher.mock.calls.map(call => [String(call[0]), call[1]?.method])).toEqual([
      ["/api/v1/security/settings", "PUT"],
      ["/api/v1/security/bans", "POST"],
      ["/api/v1/security/bans/2001%3A4860%3A4860%3A%3A8888", "DELETE"],
    ]);
  });

  it("rejects extra success fields and never echoes unknown server details", async () => {
    await expect(loadSecuritySettings(vi.fn(() => response({ ...settings, secret: "PRIVATE" })))).rejects.toThrow("安全管理响应无效");
    const fetcher = vi.fn(() => response({ code: "unknown", detail: "PRIVATE-UPSTREAM-DETAIL" }, 503));
    try {
      await loadSecurityBans(fetcher);
      throw new Error("expected rejection");
    } catch (error) {
      expect(String(error)).toContain("安全管理响应无效");
      expect(String(error)).not.toContain("PRIVATE-UPSTREAM-DETAIL");
    }
  });
});
