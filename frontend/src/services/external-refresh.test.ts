import { afterEach, expect, it, vi } from "vitest";
import { accountExternalSubscriptions, getExternalSource, updateExternalRefresh } from "./external-subscriptions";
import { subscriberState } from "./subscriber-auth";

const refresh = { enabled: true, interval_minutes: 15, scope: "all", paused: false, running: false,
  next_run_at: "2026-09-01T01:00:00Z", last_attempt_at: null, last_finished_at: null, last_success_at: null,
  code: "never", consecutive_failures: 0, imported_count: 0, updated_count: 0, missing_count: 0, new_available_count: 0 };
const source = { id: "source-1", owner_username: "alice", name: "Mine", enabled: true, revision: 3,
  has_custom_user_agent: false, node_count: 0, available_node_count: 0, metadata: {}, last_synced_at: null,
  created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", refresh };
const payload = { expected_revision: 2, enabled: true, interval_minutes: 15, scope: "all" as const, accept_changes: true };
const respond = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
afterEach(() => { subscriberState.session = null; vi.restoreAllMocks(); });

it("projects only safe schedule fields and writes the frozen administrator contract", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(respond({ ...source, url: "SECRET", refresh: { ...refresh, exception: "SECRET" } }));
  const value = await updateExternalRefresh(source.id, { ...payload, injected: "SECRET" } as typeof payload, fetcher);
  expect(JSON.stringify(value)).not.toContain("SECRET");
  expect(fetcher.mock.calls[0]![0]).toBe("/api/v1/external-subscriptions/source-1/refresh-schedule");
  expect(JSON.parse(String(fetcher.mock.calls[0]![1]?.body))).toEqual(payload);
  expect(value.refresh).toEqual(refresh);
});

it.each([{ code: "SECRET" }, { interval_minutes: 0 }, { scope: "arbitrary" }, { running: 1 }, { next_run_at: "broken" }, { imported_count: -1 }])("rejects malformed successful status %j", async invalid => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(respond({ source: { ...source, refresh: { ...refresh, ...invalid } }, nodes: [], license_required: false }));
  await expect(getExternalSource(source.id, fetcher)).rejects.toMatchObject({ status: null });
});

it("uses subscriber identity, csrf and response ownership for schedule writes", async () => {
  subscriberState.session = { authenticated: true, username: "alice", csrf_token: "own-csrf", requires_2fa: false, challenge: null };
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(respond(source));
  const api = accountExternalSubscriptions("alice", fetcher);
  await api.updateExternalRefresh(source.id, payload);
  const [url, init] = fetcher.mock.calls[0]!;
  expect(url).toBe("/api/v1/account/external-subscriptions/source-1/refresh-schedule");
  expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("own-csrf");
  expect(init?.credentials).toBe("include");
  fetcher.mockResolvedValue(respond({ ...source, owner_username: "bob" }));
  await expect(api.updateExternalRefresh(source.id, payload)).rejects.toMatchObject({ status: null });
});
