import { beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "./auth";
import { ApplicationUpdateRequestError, applyApplicationUpdate, checkApplicationUpdate, getApplicationUpdate } from "./application-updates";

const current = "a".repeat(40), latest = "b".repeat(40);
const state = { schema_version: 1, managed: true, status: "available", request_id: "11111111-1111-4111-8111-111111111111",
  current_revision: current, latest_revision: latest, has_update: true, checked_at: "2026-09-01T01:02:03Z", started_at: null, completed_at: null,
  message: "发现可用更新", release_url: `https://github.com/FengYuchen1314/open-node/commit/${latest}`, license_required: false };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => { authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf" }; });

describe("application update service", () => {
  it("validates state and sends only fixed check/apply contracts", async () => {
    const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
    const fetcher = vi.fn(async (input, init) => {
      calls.push([input, init]);
      if (String(input).endsWith("/check")) return response({ accepted: true, request_id: state.request_id, action: "check", license_required: false }, 202);
      if (String(input).endsWith("/apply")) return response({ accepted: true, request_id: state.request_id, action: "apply", license_required: false }, 202);
      return response(state);
    });
    expect(await getApplicationUpdate(fetcher)).toEqual(state);
    await checkApplicationUpdate(fetcher); await applyApplicationUpdate(latest, fetcher);
    expect(calls.map(item => String(item[0]))).toEqual(["/api/v1/application-update", "/api/v1/application-update/check", "/api/v1/application-update/apply"]);
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({ target_revision: latest, confirmed: true });
    expect(calls[1][1]?.body).toBeUndefined();
  });
  it("rejects forged links, extra fields and invalid targets", async () => {
    await expect(getApplicationUpdate(async () => response({ ...state, release_url: "https://evil.invalid/x" }))).rejects.toBeInstanceOf(ApplicationUpdateRequestError);
    await expect(getApplicationUpdate(async () => response({ ...state, command: "PRIVATE" }))).rejects.toBeInstanceOf(ApplicationUpdateRequestError);
    expect(() => applyApplicationUpdate("main", vi.fn())).toThrow(ApplicationUpdateRequestError);
  });
  it("uses fixed error text and never echoes a server body", async () => {
    await expect(checkApplicationUpdate(async () => response({ code: "PRIVATE", detail: "PRIVATE" }, 500))).rejects.toThrow("未能确认更新操作结果");
  });
});
