import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authState } from "./auth";
import { accountExternalSubscriptions, ExternalSubscriptionsError } from "./external-subscriptions";
import { subscriberState } from "./subscriber-auth";

const source = { id: "source-1", owner_username: "alice", name: "Mine", enabled: true, revision: 1,
  has_custom_user_agent: false, node_count: 0, available_node_count: 0, metadata: {},
  last_synced_at: null, created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z" };
const detail = { source, nodes: [], license_required: false };
const preview = { id: "preview-1", source_id: source.id, source_revision: 1, nodes: [], metadata: {},
  created_at: source.created_at, expires_at: "2099-08-31T00:00:00Z", receipt: null, license_required: false };
const receipt = { source_id: source.id, preview_id: preview.id, revision: 2, imported_count: 0, updated_count: 0, missing_count: 0, applied_at: source.created_at };
const respond = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const session = (username = "alice") => ({ authenticated: true, username, csrf_token: `subscriber-${username}`, requires_2fa: false, challenge: null });
beforeEach(() => { subscriberState.ready = true; subscriberState.session = session(); authState.session = { authenticated: true, configured: true, username: "admin", csrf_token: "ADMIN-SECRET" }; });
afterEach(() => { subscriberState.session = null; authState.session = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("account external source transport", () => {
  it("uses all ten account routes with subscriber CSRF and no serialized caller-supplied owner", async () => {
    const replies = [{ sources: [source], license_required: false }, source, detail, source, { deleted: true, license_required: false }, detail, preview, preview, receipt, { cancelled: true, license_required: false }];
    const fetcher = vi.fn<typeof fetch>(async () => respond(replies.shift()));
    const api = accountExternalSubscriptions("alice", fetcher);
    await api.listExternalSources();
    await api.createExternalSource({ owner_username: "bob", name: "Mine", url: "https://provider.example/PRIVATE" });
    await api.getExternalSource(source.id);
    await api.updateExternalSource(source.id, { expected_revision: 1, name: "Mine", enabled: true });
    await api.deleteExternalSource(source.id, { expected_revision: 1, confirm: true });
    await api.updateExternalNode(source.id, "node-1", { expected_revision: 1, name: "Node", enabled: false });
    await api.createExternalPreview(source.id, { expected_revision: 1 });
    await api.getExternalPreview(source.id, preview.id);
    await api.confirmExternalPreview(source.id, preview.id, { expected_revision: 1, selected_node_ids: [], accept_changes: true });
    await api.cancelExternalPreview(source.id, preview.id);
    expect(fetcher).toHaveBeenCalledTimes(10);
    expect(JSON.parse(String(fetcher.mock.calls[1]![1]?.body))).toEqual({ name: "Mine", url: "https://provider.example/PRIVATE", user_agent: "", enabled: true });
    for (const [url, init] of fetcher.mock.calls) {
      expect(String(url)).toMatch(/^\/api\/v1\/account\/external-subscriptions(?:\/|$)/);
      expect(String(url)).not.toContain("PRIVATE"); expect(String(init?.body)).not.toContain("owner_username");
      expect(init?.credentials).toBe("include"); expect(init?.redirect).toBe("error"); expect(init?.cache).toBe("no-store");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-CSRF-Token")).toBe(init?.method && init.method !== "GET" ? "subscriber-alice" : null);
      expect(JSON.stringify([...headers])).not.toContain("ADMIN-SECRET");
    }
    expect(JSON.parse(String(fetcher.mock.calls[3]![1]?.body))).toEqual({ expected_revision: 1, name: "Mine", enabled: true, url: null, user_agent: null });
  });

  it("rejects cross-owner read replies and safe-maps private error bodies", async () => {
    const other = { ...source, owner_username: "bob", url: "PRIVATE" };
    const api = accountExternalSubscriptions("alice", vi.fn<typeof fetch>().mockResolvedValue(respond({ sources: [other], license_required: false })));
    await expect(api.listExternalSources()).rejects.toMatchObject({ status: null });
    const failed = accountExternalSubscriptions("alice", vi.fn<typeof fetch>().mockResolvedValue(respond({ detail: "PRIVATE SOURCE" }, 403)));
    const error = await failed.getExternalSource(source.id).catch(value => value);
    expect(error).toBeInstanceOf(ExternalSubscriptionsError); expect(error.message).not.toContain("PRIVATE"); expect(error.message).toContain("请求验证");
  });

  it("does not accept late replies or clear a newer subscriber session, and never clears admin auth", async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>(() => new Promise(done => { resolve = done; }));
    const old = accountExternalSubscriptions("alice", fetcher).listExternalSources();
    subscriberState.session = session("bob"); resolve(respond({ detail: "PRIVATE" }, 401));
    await expect(old).rejects.toMatchObject({ status: null });
    expect(subscriberState.session?.username).toBe("bob"); expect(authState.session?.username).toBe("admin");
    const current = accountExternalSubscriptions("bob", vi.fn<typeof fetch>().mockResolvedValue(respond({}, 401)));
    await expect(current.listExternalSources()).rejects.toMatchObject({ status: 401 });
    expect(subscriberState.session).toBeNull(); expect(authState.session?.username).toBe("admin");
  });
});
