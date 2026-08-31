import { describe, expect, it, vi } from "vitest";
import type { ManagedNode } from "../domain/subscriptions";
import { getNodeManagement, getNodeRemoval, nodeSettings, parseNodeObject, removeNode, retryNodeRemoval, saveNode } from "./node-management";

const node: ManagedNode = {
  id: "node-id", name: "Node", server_id: "server-id", protocol: "vless", node_type: "routed",
  enabled: true, tags: ["one"], config: { nested: { port: 443 } }, client_template: { level: 0 },
  parent_id: "parent", target_node_id: "target", created_at: "", updated_at: "",
};
describe("node management", () => {
  it("copies editable fields without exposing runtime identity as mutable settings", () => {
    const values = nodeSettings(node);
    expect(values).not.toHaveProperty("server_id");
    expect(values).not.toHaveProperty("protocol");
    expect(values.parent_id).toBe("parent");
    values.tags.push("two");
    (values.config.nested as { port: number }).port = 80;
    expect(node.tags).toEqual(["one"]);
    expect(node.config.nested).toEqual({ port: 443 });
  });
  it("validates JSON objects", () => {
    expect(parseNodeObject('{"port":443}', "Config")).toEqual({ port: 443 });
    for (const value of ["null", "[]", "false", '"text"', "{"]) expect(() => parseNodeObject(value, "Config")).toThrow("Config");
  });
  it("sends revision and explicit cleanup acknowledgments", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await getNodeManagement("id /", fetcher);
    await saveNode("id", nodeSettings(node), "revision", fetcher);
    await removeNode("id", "revision", "Node", true, fetcher);
    await getNodeRemoval("job", fetcher);
    await retryNodeRemoval("job", fetcher);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/nodes/id%20%2F/settings");
    const calls = fetcher.mock.calls as unknown as [string, RequestInit | undefined][];
    expect(JSON.parse(calls[1]![1]!.body as string)).toMatchObject({ expected_revision: "revision", acknowledge_runtime_restart: true });
    expect(JSON.parse(calls[2]![1]!.body as string)).toMatchObject({ confirm_name: "Node", acknowledge_unmanaged_resources: true });
    expect(calls[4]).toEqual(["/api/v1/node-removals/job/retry", { method: "POST" }]);
  });
  it("surfaces conflict and validation details", async () => {
    const conflict = async () => new Response(JSON.stringify({ detail: "Reload nodes" }), { status: 409 });
    await expect(getNodeManagement("id", conflict)).rejects.toThrow("请重新加载节点。");
    const invalid = async () => new Response(JSON.stringify({ detail: [{ loc: ["body", "tags"], msg: "Too long" }] }), { status: 422 });
    await expect(getNodeManagement("id", invalid)).rejects.toThrow("tags: 长度超出限制。");
  });
});
