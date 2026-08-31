import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExternalPreviewRead, ExternalSourceDetail, ExternalSourceRead, ExternalSourceUpdate } from "../domain/external-subscriptions";
import { authState } from "./auth";
import {
  cancelExternalPreview, confirmExternalPreview, createExternalPreview, createExternalSource, deleteExternalSource,
  ExternalSubscriptionsError, externalSubscriptionsErrorMessage, getExternalPreview, getExternalSource,
  listExternalSources, updateExternalNode, updateExternalSource,
} from "./external-subscriptions";

const source: ExternalSourceRead = {
  id: "source-1", owner_username: "alice", name: "Remote provider", enabled: true, revision: 4,
  has_custom_user_agent: true, node_count: 1, available_node_count: 1, metadata: { upload: 10, download: 20, total: 1000 },
  last_synced_at: null, created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:00:00Z",
};
const detail: ExternalSourceDetail = {
  source, license_required: false, nodes: [{ id: "node-1", source_id: source.id, upstream_name: "Tokyo", name: "Tokyo", protocol: "vless", enabled: true, present: true, available: true, reason: null }],
};
const receipt = { source_id: source.id, preview_id: "preview-1", revision: 5, imported_count: 1, updated_count: 0, missing_count: 0, applied_at: "2026-08-31T00:10:00Z" };
const preview: ExternalPreviewRead = {
  id: "preview-1", source_id: source.id, source_revision: 4, created_at: "2026-08-31T00:00:00Z", expires_at: "2099-08-31T00:15:00Z", metadata: {},
  nodes: [{ node_id: "node-new", upstream_name: "New node", name: "New node", protocol: "ss", existing: false, change: "new", selectable: true, reason: null, changed_fields: [] }],
  receipt: null, license_required: false,
};
const create = { owner_username: "alice", name: "Remote provider", url: "https://provider.example/subscription?token=private-source", user_agent: "private-user-agent", enabled: true };
const update: ExternalSourceUpdate = { expected_revision: 4, name: "New name", enabled: false, url: null, user_agent: null };
const confirmation = { expected_revision: 4, selected_node_ids: ["node-new"], accept_changes: true };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); authState.session = null; });

describe("external subscription API contracts", () => {
  it("uses the ten frozen routes with exact methods and request bodies", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const results = [{ sources: [source], license_required: false }, source, detail, source, { deleted: true, license_required: false }, detail, preview, preview, receipt, { cancelled: true, license_required: false }];
    const fetcher: typeof fetch = async (input, init = {}) => { calls.push({ url: String(input), init }); return response(results.shift()); };
    expect(await listExternalSources(fetcher)).toEqual({ sources: [source], license_required: false });
    expect(await createExternalSource(create, fetcher)).toEqual(source);
    expect(await getExternalSource(source.id, fetcher)).toEqual(detail);
    expect(await updateExternalSource(source.id, update, fetcher)).toEqual(source);
    expect(await deleteExternalSource(source.id, { expected_revision: 4, confirm: true }, fetcher)).toEqual({ deleted: true, license_required: false });
    expect(await updateExternalNode(source.id, "node-1", { expected_revision: 4, name: "Local name", enabled: false }, fetcher)).toEqual(detail);
    expect(await createExternalPreview(source.id, { expected_revision: 4 }, fetcher)).toEqual(preview);
    expect(await getExternalPreview(source.id, preview.id, fetcher)).toEqual(preview);
    expect(await confirmExternalPreview(source.id, preview.id, confirmation, fetcher)).toEqual(receipt);
    expect(await cancelExternalPreview(source.id, preview.id, fetcher)).toEqual({ cancelled: true, license_required: false });
    expect(calls.map(call => call.url)).toEqual([
      "/api/v1/external-subscriptions", "/api/v1/external-subscriptions", "/api/v1/external-subscriptions/source-1",
      "/api/v1/external-subscriptions/source-1", "/api/v1/external-subscriptions/source-1/delete",
      "/api/v1/external-subscriptions/source-1/nodes/node-1", "/api/v1/external-subscriptions/source-1/previews",
      "/api/v1/external-subscriptions/source-1/previews/preview-1", "/api/v1/external-subscriptions/source-1/previews/preview-1/confirm",
      "/api/v1/external-subscriptions/source-1/previews/preview-1",
    ]);
    expect(calls.map(call => call.init.method ?? "GET")).toEqual(["GET", "POST", "GET", "PUT", "POST", "PUT", "POST", "GET", "POST", "DELETE"]);
    expect(calls.map(call => call.init.body === undefined ? null : JSON.parse(String(call.init.body)))).toEqual([
      null, create, null, update, { expected_revision: 4, confirm: true }, { expected_revision: 4, name: "Local name", enabled: false },
      { expected_revision: 4 }, null, confirmation, null,
    ]);
    for (const { url, init } of calls) {
      expect(init.cache).toBe("no-store"); expect(init.redirect).toBe("error"); expect(init.referrerPolicy).toBe("no-referrer");
      expect(new Headers(init.headers).get("Accept")).toBe("application/json");
      if (init.body !== undefined) expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
      expect(url).not.toContain(create.url); expect(url).not.toContain(create.user_agent); expect(url).not.toContain("?");
    }
  });

  it("uses CSRF for every write, cookies for administrator requests, and no CSRF header for reads", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "csrf-only-in-header" };
    const calls: RequestInit[] = [], results = [{ sources: [], license_required: false }, detail, preview, source, source, detail, preview, receipt, { cancelled: true, license_required: false }, { deleted: true, license_required: false }];
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init: RequestInit) => { calls.push(init); return response(results.shift()); }));
    await listExternalSources(); await getExternalSource(source.id); await getExternalPreview(source.id, preview.id);
    await createExternalSource(create); await updateExternalSource(source.id, update);
    await updateExternalNode(source.id, "node-1", { expected_revision: 4, name: "Name", enabled: true });
    await createExternalPreview(source.id, { expected_revision: 4 }); await confirmExternalPreview(source.id, preview.id, confirmation);
    await cancelExternalPreview(source.id, preview.id); await deleteExternalSource(source.id, { expected_revision: 4, confirm: true });
    expect(calls).toHaveLength(10);
    calls.forEach((init, index) => {
      expect(init.credentials).toBe("include"); expect(init.cache).toBe("no-store");
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(index < 3 ? null : "csrf-only-in-header");
      expect(String(init.body)).not.toContain("csrf-only-in-header");
    });
  });

  it("clears an expired administrator session without leaking the response body", async () => {
    authState.session = { configured: true, authenticated: true, username: "admin", csrf_token: "old-csrf" };
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: create.url }, 401)));
    await expect(listExternalSources()).rejects.toThrow("请重新登录");
    expect(authState.session.authenticated).toBe(false); expect(authState.session.csrf_token).toBeNull();
  });

  it("encodes every path identifier without ever making a direct upstream request", async () => {
    const sourceId = "source/with?query#fragment", previewId = "preview/with?query", nodeId = "node/with?query";
    const paths: string[] = [], fetcher: typeof fetch = async input => {
      const path = String(input); paths.push(path);
      if (path.endsWith("/confirm")) return response({ ...receipt, source_id: sourceId, preview_id: previewId });
      if (path.includes("/nodes/")) return response({ ...detail, source: { ...source, id: sourceId }, nodes: [] });
      return response({ ...preview, id: previewId, source_id: sourceId });
    };
    await updateExternalNode(sourceId, nodeId, { expected_revision: 4, name: "Name", enabled: true }, fetcher);
    await getExternalPreview(sourceId, previewId, fetcher);
    await confirmExternalPreview(sourceId, previewId, confirmation, fetcher);
    expect(paths).toEqual([
      "/api/v1/external-subscriptions/source%2Fwith%3Fquery%23fragment/nodes/node%2Fwith%3Fquery",
      "/api/v1/external-subscriptions/source%2Fwith%3Fquery%23fragment/previews/preview%2Fwith%3Fquery",
      "/api/v1/external-subscriptions/source%2Fwith%3Fquery%23fragment/previews/preview%2Fwith%3Fquery/confirm",
    ]);
  });

  it("keeps owner immutable and distinguishes preserved secrets from an explicit default-agent reset", async () => {
    const bodies: Record<string, unknown>[] = [], fetcher: typeof fetch = async (_input, init) => { bodies.push(JSON.parse(String(init?.body))); return response(source); };
    await updateExternalSource(source.id, { expected_revision: 4, name: "Name", enabled: true, owner_username: "mallory" } as ExternalSourceUpdate, fetcher);
    await updateExternalSource(source.id, { expected_revision: 4, name: "Name", enabled: true, url: "https://new.example/private", user_agent: "" }, fetcher);
    expect(bodies).toEqual([
      { expected_revision: 4, name: "Name", enabled: true, url: null, user_agent: null },
      { expected_revision: 4, name: "Name", enabled: true, url: "https://new.example/private", user_agent: "" },
    ]);
    await createExternalSource({ owner_username: "alice", name: "Name", url: create.url }, fetcher);
    expect(bodies[2]).toEqual({ owner_username: "alice", name: "Name", url: create.url, user_agent: "", enabled: true });
  });

  it("projects public source, node, preview and receipt fields without retaining unexpected credentials", async () => {
    const fetcher: typeof fetch = async input => String(input).includes("/previews/")
      ? response({ ...preview, url: create.url, user_agent: create.user_agent, receipt: { ...receipt, secret: "hidden-receipt" }, nodes: preview.nodes.map(node => ({ ...node, proxy: { password: "hidden-proxy" } })) })
      : response({ ...detail, source: { ...source, url: create.url, user_agent: create.user_agent }, nodes: detail.nodes.map(node => ({ ...node, config: { password: "hidden-proxy" } })) });
    const read = await getExternalSource(source.id, fetcher), readPreview = await getExternalPreview(source.id, preview.id, fetcher);
    expect(read).toEqual(detail); expect(readPreview).toEqual({ ...preview, receipt });
    for (const secret of [create.url, create.user_agent, "hidden-proxy", "hidden-receipt"]) expect(JSON.stringify([read, readPreview])).not.toContain(secret);
  });

  it("whitelists metadata keys and omits negative, fractional or unsafe fields without rejecting the source", async () => {
    const injected = { upload: 0, download: -1, total: 2 ** 63 - 1, expire: 1.5, token_hint: 12345, arbitrary_count: 42 };
    const fetcher: typeof fetch = async () => response({ sources: [{ ...source, metadata: injected }, { ...source, id: "safe-source", metadata: { upload: 1, download: 2, total: Number.MAX_SAFE_INTEGER, expire: 1_800_000_000 } }], license_required: false });
    const result = await listExternalSources(fetcher);
    expect(result.sources).toHaveLength(2);
    expect(result.sources[0]?.metadata).toEqual({ upload: 0 });
    expect(result.sources[1]?.metadata).toEqual({ upload: 1, download: 2, total: Number.MAX_SAFE_INTEGER, expire: 1_800_000_000 });
    expect(JSON.stringify(result)).not.toContain("token_hint"); expect(JSON.stringify(result)).not.toContain("arbitrary_count");
  });

  it("rejects mismatched response identities instead of showing another owner's data or receipt", async () => {
    const wrongSource: typeof fetch = async () => response({ ...detail, source: { ...source, id: "another-source" } });
    const wrongNode: typeof fetch = async () => response({ ...detail, nodes: [{ ...detail.nodes[0], source_id: "another-source" }] });
    const wrongPreview: typeof fetch = async () => response({ ...preview, source_id: "another-source" });
    const wrongReceipt: typeof fetch = async () => response({ ...receipt, preview_id: "another-preview" });
    const wrongOwner: typeof fetch = async () => response({ ...source, owner_username: "another-owner" });
    await expect(getExternalSource(source.id, wrongSource)).rejects.toThrow(ExternalSubscriptionsError);
    await expect(getExternalSource(source.id, wrongNode)).rejects.toThrow(ExternalSubscriptionsError);
    await expect(getExternalPreview(source.id, preview.id, wrongPreview)).rejects.toThrow(ExternalSubscriptionsError);
    await expect(confirmExternalPreview(source.id, preview.id, confirmation, wrongReceipt)).rejects.toThrow(ExternalSubscriptionsError);
    await expect(createExternalSource(create, wrongOwner)).rejects.toThrow(ExternalSubscriptionsError);
  });

  it.each([401, 403, 404, 409, 410, 413, 415, 422, 429, 500, 502, 503])("bounds HTTP %s errors and never echoes URL, UA or validation input", async status => {
    const fetcher: typeof fetch = async () => response({ detail: `${create.url} ${create.user_agent}`, input: create }, status);
    const failure = await createExternalSource(create, fetcher).catch(error => error as unknown);
    expect(failure).toBeInstanceOf(ExternalSubscriptionsError);
    expect((failure as ExternalSubscriptionsError).status).toBe(status);
    expect((failure as ExternalSubscriptionsError).outcomeUnknown).toBe(status >= 500);
    expect(externalSubscriptionsErrorMessage(failure)).not.toContain(create.url);
    expect(externalSubscriptionsErrorMessage(failure)).not.toContain(create.user_agent);
    expect(externalSubscriptionsErrorMessage(failure)).toMatch(/[\u3400-\u9fff]/u);
  });

  it.each([
    ["Cancel an existing preview before fetching again", "已有 3 个未确认的预览"],
    ["External subscription preview expired; fetch again", "请关闭预览，并手动获取新预览"],
    ["External source changed after this preview", "来源在预览后发生了变化"],
    ["Preview was confirmed with a different selection", "此预览已按另一组选择确认"],
    ["Preview is already confirmed; its receipt is retained", "此预览已确认，仍可查看确认回执"],
    ["Subscriber removal is in progress", "正在删除此来源所属的用户"],
    ["Subscriber external source limit reached", "外部订阅来源数量已达上限"],
    ["External source saved-node limit reached", "已保存的节点数量已达上限"],
  ])("translates safe detail only after exact original-message validation: %s", (original, translated) => {
    const accepted = new ExternalSubscriptionsError(409, original);
    expect(accepted.message).toContain(translated);
    const fallback = new ExternalSubscriptionsError(409).message;
    for (const detail of [original + " " + create.url, original + "\n", accepted.message, { detail: original }, [{ msg: original, input: create.url }]]) {
      expect(new ExternalSubscriptionsError(409, detail).message).toBe(fallback);
    }
  });

  it("retains only exact, known safe conflict guidance", async () => {
    const fetcher: typeof fetch = async () => response({ detail: "Preview was confirmed with a different selection" }, 409);
    await expect(confirmExternalPreview(source.id, preview.id, confirmation, fetcher)).rejects.toThrow("请查看确认回执");
    const limit: typeof fetch = async () => response({ detail: "Cancel an existing preview before fetching again" }, 409);
    await expect(createExternalPreview(source.id, { expected_revision: 4 }, limit)).rejects.toThrow("已有 3 个未确认的预览");
  });

  it("handles validation arrays, proxy HTML, invalid JSON and unexpected successful shapes safely", async () => {
    const validation: typeof fetch = async () => response({ detail: [{ input: create.url, msg: create.user_agent }] }, 422);
    const html: typeof fetch = async () => new Response(`<html>${create.url}</html>`, { status: 502 });
    const malformed: typeof fetch = async () => new Response(create.url);
    const missingFields: typeof fetch = async () => response({ ...receipt, applied_at: undefined });
    const nonFree: typeof fetch = async () => response({ sources: [], license_required: true });
    for (const fetcher of [validation, html, malformed, missingFields]) {
      const failure = await confirmExternalPreview(source.id, preview.id, confirmation, fetcher).catch(error => error as unknown);
      expect(failure).toBeInstanceOf(ExternalSubscriptionsError);
      expect(externalSubscriptionsErrorMessage(failure)).not.toContain(create.url);
    }
    await expect(listExternalSources(nonFree)).rejects.toThrow(ExternalSubscriptionsError);
  });

  it("marks network/parse ambiguity without retaining causes or retrying a confirmation or fetch", async () => {
    const failure = new Error(`Network lost while requesting ${create.url}`);
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(failure);
    const result = await confirmExternalPreview(source.id, preview.id, confirmation, fetcher).catch(error => error as ExternalSubscriptionsError);
    expect(result).toBeInstanceOf(ExternalSubscriptionsError);
    expect((result as ExternalSubscriptionsError).outcomeUnknown).toBe(true);
    expect(result).not.toHaveProperty("cause"); expect(externalSubscriptionsErrorMessage(result)).not.toContain(create.url);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(externalSubscriptionsErrorMessage(failure)).not.toContain(create.url);
  });

  it("leaves selection and revision identical on explicit retries and uses GET for receipt recovery", async () => {
    const calls: RequestInit[] = [], fetcher: typeof fetch = async (_input, init = {}) => { calls.push(init); return response(init.method === "POST" ? receipt : { ...preview, receipt }); };
    await confirmExternalPreview(source.id, preview.id, confirmation, fetcher);
    await confirmExternalPreview(source.id, preview.id, confirmation, fetcher);
    const restored = await getExternalPreview(source.id, preview.id, fetcher);
    expect(calls[0]?.body).toBe(calls[1]?.body); expect(calls[2]?.method ?? "GET").toBe("GET");
    expect(restored.receipt).toEqual(receipt);
  });
});
